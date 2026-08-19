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
from anbu_care.provenance.store import Store, get_store, load_receipts, save_receipt
from anbu_care.schemas import (
    Adjudication,
    Case,
    ClaimPacket,
    ClaimStage,
    ClaimSubmission,
    ParentProfile,
    ParsedDocument,
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
# Cases
# --------------------------------------------------------------------------


def open_case(parent_id: str, store: Store | None = None) -> Case:
    store = store or get_store()
    case = Case(case_id=new_id("case"), parent_id=parent_id)
    store.put(f"CASE#{case.case_id}", "META", case.model_dump(mode="json"))
    append_receipt(
        case.case_id,
        kind="case.opened",
        actor="intake_agent",
        payload={"parent_id": parent_id, "opened_at": case.opened_at.isoformat()},
        store=store,
    )
    return case


def load_case(case_id: str, store: Store | None = None) -> Case | None:
    store = store or get_store()
    row = store.get(f"CASE#{case_id}", "META")
    return Case.model_validate(_clean(row)) if row else None


def update_case(case: Case, store: Store | None = None) -> None:
    store = store or get_store()
    store.put(f"CASE#{case.case_id}", "META", case.model_dump(mode="json"))


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def append_receipt(
    case_id: str,
    *,
    kind: str,
    actor: str,
    payload: dict[str, Any],
    store: Store | None = None,
) -> Receipt:
    """Append one link to a case's chain and persist it.

    Reads the existing chain first so seq and prev_hash come from what is
    actually stored, not from an in-process guess.
    """
    store = store or get_store()
    chain = ReceiptChain(case_id, load_receipts(case_id, store))
    receipt = chain.append(kind=kind, actor=actor, payload=payload)
    save_receipt(receipt, store)
    return receipt


def get_chain(case_id: str, store: Store | None = None) -> ReceiptChain:
    return ReceiptChain(case_id, load_receipts(case_id, store or get_store()))


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
