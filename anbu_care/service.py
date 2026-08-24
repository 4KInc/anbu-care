"""Case service — the layer agent tools call.

Agents own the conversation; this owns the state transitions. Keeping them
apart is what lets an agent be replaced without the receipt chain changing
shape, and lets every tool be tested with no model in the loop.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from anbu_care.config import settings
from anbu_care.provenance.chain import Receipt, ReceiptChain, VerificationResult
from anbu_care.provenance.store import (
    CASE_SUBJECT,
    Store,
    get_store,
    load_receipts,
    save_receipt,
)
from anbu_care.schemas import (
    Adjudication,
    Case,
    ClaimPacket,
    ClaimStage,
    ClaimSubmission,
    ClinicianNote,
    DiagnosticOrder,
    ParentProfile,
    ParsedDocument,
    PaymentMandate,
    PaymentRecord,
)

# IRDAI 2024 Master Circular: cashless pre-auth decision within 1 hour;
# reimbursement settled on a 30-day clock.
SLA_CASHLESS_PREAUTH = timedelta(hours=1)
SLA_REIMBURSEMENT = timedelta(days=30)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------
# Profiles & documents
# --------------------------------------------------------------------------


def save_profile(profile: ParentProfile, store: Store | None = None) -> None:
    store = store or get_store()
    profile.updated_at = _now()
    store.put(f"PARENT#{profile.parent_id}", "PROFILE", profile.model_dump(mode="json"))


def load_profile(parent_id: str, store: Store | None = None) -> ParentProfile | None:
    store = store or get_store()
    row = store.get(f"PARENT#{parent_id}", "PROFILE")
    return ParentProfile.model_validate(_clean(row)) if row else None


def save_document(doc: ParsedDocument, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"PARENT#{doc.parent_id}", f"DOC#{doc.document_id}", doc.model_dump(mode="json"))


def list_documents(parent_id: str, store: Store | None = None) -> list[ParsedDocument]:
    store = store or get_store()
    rows = store.query_prefix(f"PARENT#{parent_id}", "DOC#")
    return [ParsedDocument.model_validate(_clean(r)) for r in rows]


# --------------------------------------------------------------------------
# Payments
#
# Mandates and payments live under the CASE partition, because both are scoped
# to one admission. A mandate that outlived its case would be a standing
# authority nobody remembered granting.
# --------------------------------------------------------------------------


def save_mandate(mandate: PaymentMandate, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"CASE#{mandate.case_id}", f"MANDATE#{mandate.mandate_id}",
              mandate.model_dump(mode="json"))


def list_mandates(case_id: str, store: Store | None = None) -> list[PaymentMandate]:
    store = store or get_store()
    rows = store.query_prefix(f"CASE#{case_id}", "MANDATE#")
    return [PaymentMandate.model_validate(_clean(r)) for r in rows]


def save_payment(payment: PaymentRecord, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"CASE#{payment.case_id}", f"PAYMENT#{payment.payment_id}",
              payment.model_dump(mode="json"))


def list_payments(case_id: str, store: Store | None = None) -> list[PaymentRecord]:
    store = store or get_store()
    rows = store.query_prefix(f"CASE#{case_id}", "PAYMENT#")
    return sorted((PaymentRecord.model_validate(_clean(r)) for r in rows),
                  key=lambda p: p.initiated_at)


def save_clinician_note(note: ClinicianNote, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"CASE#{note.case_id}", f"CLINNOTE#{note.note_id}",
              note.model_dump(mode="json"))


def list_clinician_notes(case_id: str,
                         store: Store | None = None) -> list[ClinicianNote]:
    """Oldest first. Credentialed: these are a clinician's words about her."""
    store = store or get_store()
    rows = store.query_prefix(f"CASE#{case_id}", "CLINNOTE#")
    return sorted((ClinicianNote.model_validate(_clean(r)) for r in rows),
                  key=lambda n: n.recorded_at)


def save_diagnostic_order(order: DiagnosticOrder, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"CASE#{order.case_id}", f"DXORDER#{order.order_id}",
              order.model_dump(mode="json"))


def list_diagnostic_orders(case_id: str,
                           store: Store | None = None) -> list[DiagnosticOrder]:
    """Oldest first. A case can accumulate several orders over a stay."""
    store = store or get_store()
    rows = store.query_prefix(f"CASE#{case_id}", "DXORDER#")
    return sorted((DiagnosticOrder.model_validate(_clean(r)) for r in rows),
                  key=lambda o: o.recorded_at)


def load_diagnostic_order(case_id: str, order_id: str,
                          store: Store | None = None) -> DiagnosticOrder | None:
    store = store or get_store()
    row = (store or get_store()).get(f"CASE#{case_id}", f"DXORDER#{order_id}")
    return DiagnosticOrder.model_validate(_clean(row)) if row else None


def find_payments_by_settlement_ref(reference: str,
                                   store: Store | None = None) -> list[PaymentRecord]:
    """Every payment carrying this provider reference.

    A webhook names an order, not a case, so this is the one payment lookup
    that cannot start from a partition key. It scans, which is honest about
    what it costs: at demo scale that is nothing, and at real scale it would
    want an index rather than a cleverer scan.
    """
    if not reference:
        return []
    store = store or get_store()
    rows = store.query_sk_prefix_across("PAYMENT#")
    payments = [PaymentRecord.model_validate(_clean(r)) for r in rows]
    return [p for p in payments if p.settlement_ref == reference]


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------


def open_case(parent_id: str, store: Store | None = None) -> Case:
    store = store or get_store()
    case = Case(case_id=new_id("case"), parent_id=parent_id)
    store.put(f"CASE#{case.case_id}", "META", case.model_dump(mode="json"))
    index_case(case, store=store)
    append_receipt(
        case.case_id,
        kind="case.opened",
        actor="intake_agent",
        payload={"parent_id": parent_id, "opened_at": case.opened_at.isoformat()},
        store=store,
    )
    return case


def index_case(case: Case, store: Store | None = None) -> None:
    """Write the parent -> case reverse index row. Idempotent.

    Called on open AND on every update, so a case written before this index
    existed repairs itself the first time anything touches it. Cases opened
    earlier and never touched since need the backfill in
    `scripts/backfill_case_index.py`.
    """
    store = store or get_store()
    store.put(f"PARENT#{case.parent_id}", f"CASE#{case.case_id}",
              {"case_id": case.case_id, "parent_id": case.parent_id,
               "opened_at": case.opened_at.isoformat()})


def latest_case_for_parent(parent_id: str, store: Store | None = None) -> Case | None:
    """The most recently opened case for a parent, or None.

    None means no case, not "pick something plausible". A bill filed against a
    guessed case silently moves someone else's money, which is worse than a
    bill that was not filed at all.
    """
    store = store or get_store()
    rows = store.query_prefix(f"PARENT#{parent_id}", "CASE#")
    if not rows:
        return None
    newest = max(rows, key=lambda r: r.get("opened_at", ""))
    return load_case(newest["case_id"], store=store)


def load_case(case_id: str, store: Store | None = None) -> Case | None:
    store = store or get_store()
    row = store.get(f"CASE#{case_id}", "META")
    return Case.model_validate(_clean(row)) if row else None


def update_case(case: Case, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"CASE#{case.case_id}", "META", case.model_dump(mode="json"))
    # Self-healing: a case written before the reverse index existed gets one
    # the first time anything touches it, so the gap closes on its own rather
    # than only by a backfill somebody has to remember to run.
    index_case(case, store=store)


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def number_key(number: str) -> str:
    """Index on digits only, so whatsapp:+1669… and +1669… are one number.

    Public because the clinician channel binds handsets too and must agree with
    this exactly. Two normalisers would eventually disagree about whether a
    number was bound, which is the kind of bug that only shows up in the one
    case it matters.
    """
    return "".join(ch for ch in number if ch.isdigit())


# Kept for the callers already using it.
_number_key = number_key


def register_whatsapp_number(number: str, parent_id: str, contact_name: str | None) -> None:
    """Point a WhatsApp number at the parent whose record it may write to.

    The store has no scan across partitions, so an inbound message cannot be
    matched by walking every profile. This index makes the lookup a single get.

    Deliberately records only ownership, never consent: consent is checked
    against the live profile when a message arrives, so revoking it takes
    effect immediately rather than whenever this row was last written.
    """
    key = _number_key(number)
    if not key:
        return
    get_store().put(
        f"WANUMBER#{key}", "OWNER",
        {"parent_id": parent_id, "contact_name": contact_name},
    )


def lookup_whatsapp_number(number: str) -> dict[str, Any] | None:
    key = _number_key(number)
    if not key:
        return None
    return get_store().get(f"WANUMBER#{key}", "OWNER")


def append_receipt(
    case_id: str,
    *,
    kind: str,
    actor: str,
    payload: dict[str, Any],
    store: Store | None = None,
    subject: str = CASE_SUBJECT,
) -> Receipt:
    """Append one link to a case's chain and persist it.

    Reads the existing chain first so seq and prev_hash come from what is
    actually stored, not from an in-process guess.
    """
    store = store or get_store()
    chain = ReceiptChain(case_id, load_receipts(case_id, store, subject))
    receipt = chain.append(kind=kind, actor=actor, payload=payload)
    save_receipt(receipt, store, subject)
    return receipt


def get_chain(
    case_id: str, store: Store | None = None, subject: str = CASE_SUBJECT
) -> ReceiptChain:
    return ReceiptChain(case_id, load_receipts(case_id, store or get_store(), subject))


def verify_case(case_id: str, store: Store | None = None) -> VerificationResult:
    return get_chain(case_id, store).verify()


# --------------------------------------------------------------------------
# Claim packets & submission
# --------------------------------------------------------------------------


def save_packet(packet: ClaimPacket, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"CASE#{packet.case_id}", f"PACKET#{packet.packet_id}", packet.model_dump(mode="json"))


def load_packet(case_id: str, packet_id: str, store: Store | None = None) -> ClaimPacket | None:
    store = store or get_store()
    row = store.get(f"CASE#{case_id}", f"PACKET#{packet_id}")
    return ClaimPacket.model_validate(_clean(row)) if row else None


def save_submission(sub: ClaimSubmission, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"CASE#{sub.case_id}", f"SUBMISSION#{sub.submission_id}", sub.model_dump(mode="json"))


def load_submission(case_id: str, submission_id: str, store: Store | None = None) -> ClaimSubmission | None:
    store = store or get_store()
    row = store.get(f"CASE#{case_id}", f"SUBMISSION#{submission_id}")
    return ClaimSubmission.model_validate(_clean(row)) if row else None


def save_adjudication(adj: Adjudication, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"CASE#{adj.case_id}", f"ADJUDICATION#{adj.adjudication_id}", adj.model_dump(mode="json"))


def list_adjudications(case_id: str, store: Store | None = None) -> list[Adjudication]:
    store = store or get_store()
    rows = store.query_prefix(f"CASE#{case_id}", "ADJUDICATION#")
    return [Adjudication.model_validate(_clean(r)) for r in rows]


def latest_adjudication(case_id: str, store: Store | None = None) -> Adjudication | None:
    """The most recent assessment for a case, by attempt then time.

    A case can be adjudicated more than once — a QUERY answered with the
    missing document produces a second attempt — and anything derived from an
    adjudication must reflect the latest one, not the first.
    """
    rows = list_adjudications(case_id, store)
    if not rows:
        return None
    return max(rows, key=lambda a: (a.attempt, a.adjudicated_at))


def sla_deadline(kind: str, start: datetime | None = None) -> datetime:
    start = start or _now()
    window = SLA_CASHLESS_PREAUTH if kind == "cashless_preauth" else SLA_REIMBURSEMENT
    return start + window


def sla_status(sub: ClaimSubmission) -> dict[str, Any]:
    """SLA tracking is real even though the counterparty is simulated."""
    if sub.sla_deadline is None:
        return {"tracked": False}
    remaining = sub.sla_deadline - _now()
    return {
        "tracked": True,
        "kind": sub.sla_kind,
        "deadline": sub.sla_deadline.isoformat(),
        "seconds_remaining": int(remaining.total_seconds()),
        "breached": remaining.total_seconds() < 0,
        "stage": sub.stage.value if isinstance(sub.stage, ClaimStage) else str(sub.stage),
    }


# --------------------------------------------------------------------------
# Pub/Sub — async multi-day case tracking
# --------------------------------------------------------------------------


def publish_event(topic: str, payload: dict[str, Any]) -> str | None:
    """Publish a case event. No-ops unless ANBU_PUBSUB_ENABLED is set, so local
    runs and tests never need a live topic."""
    cfg = settings()
    if not cfg.pubsub_enabled:
        return None
    import json

    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    path = publisher.topic_path(cfg.project_id, topic)
    future = publisher.publish(path, json.dumps(payload, default=str).encode("utf-8"))
    return future.result(timeout=30)


def _clean(row: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (row or {}).items() if k not in {"pk", "sk"}}
