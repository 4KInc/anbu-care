"""Render the chain as a decision sequence.

The agency in this system is real — the adjudicator returns QUERY, evidence is
gathered, the packet is re-assembled and re-checked, and the loop only stops
when the gate clears. What was missing was not the deciding. It was any way for
a person to *watch* it.

This module is that view, and it is deliberately the dullest code in the
repository, because the interesting property is what it refuses to do:

**One step per receipt. Never more, never fewer.**

No summarising two receipts into one tidy beat. No inferring an unrecorded step
because the story reads better with it. No reordering into the sequence a
narrator would prefer. If something is not on the chain it does not appear
here, and the test that matters asserts the trace length equals the receipt
count exactly.

That constraint is the whole point. A trace that could invent a step would be a
story about an agent rather than a record of one, and this project's entire
argument is that the difference is checkable. Every step carries its `seq` and
`hash`, and the public `/verify` endpoint sits beside the view, so "watch it
decide" and "check that it did" are the same screen.

Note the trace does NOT re-derive attempt counts from the adjudicator's
`attempt` field, which stays 1 because each resubmission is a fresh submission.
Rounds are counted by how many `claim.adjudicated` receipts exist, which is what
actually happened.
"""

from __future__ import annotations

from anbu_care import service
from anbu_care.provenance.chain import Receipt
from anbu_care.schemas import DecisionTrace, TraceStep

# What each receipt kind meant, in the language of a decision rather than a
# database write. These are LABELS for receipts that exist — none of them can
# cause a step to appear.
_WHAT = {
    "case.opened": "A case was opened",
    "triage.decision": "Triage assessed severity and ranked hospitals",
    "evidence.assessed": "Evidence was re-checked against the completeness gate",
    "evidence.enriched": "The packet was enriched with additional evidence",
    "claim.packet_assembled": "A claim packet was assembled",
    "claim.submitted": "The claim was submitted",
    "claim.adjudicated": "The counterparty answered",
    "claim.stage_changed": "The claim moved stage",
    "wellbeing.recorded": "A check-in was recorded",
    "wellbeing.escalated": "The check-in was escalated",
    "wellbeing.unclear": "A voice note could not be made out",
    "comms.sent": "A message was delivered",
    "comms.blocked": "A message was blocked before sending",
    "comms.not_delivered": "A message was permitted but not delivered",
    "voice.placed": "A call was placed",
    "voice.not_placed": "A call was not placed",
    "clinician.note": "A clinician left a note",
    "emergency.access": "The emergency summary was opened",
}


def _detail(receipt: Receipt) -> str:
    """One line of what actually came back, read from the payload.

    Every branch reads a stored field. Where a receipt kind has no special
    handling the step still appears — with a generic line — because dropping it
    would break the one-step-per-receipt rule that makes this trustworthy.
    """
    p = receipt.payload

    match receipt.kind:
        case "triage.decision":
            severity = p.get("severity", "unknown")
            ranked = p.get("ranked") or []
            chosen = next(
                (h.get("name") for h in ranked
                 if h.get("hospital_id") == p.get("recommended_hospital_id")), None)
            tail = f", routed to {chosen}" if chosen else ""
            return f"severity {severity}{tail}"

        case "claim.adjudicated":
            outcome = p.get("outcome", "unknown")
            missing = p.get("missing_documents") or []
            if missing:
                return f"{outcome} — asked for {', '.join(m.replace('_', ' ') for m in missing)}"
            disallowed = p.get("total_disallowed_inr")
            if isinstance(disallowed, int) and disallowed > 0:
                return f"{outcome} — INR {disallowed:,} disallowed"
            return str(outcome)

        case "evidence.assessed":
            gate = p.get("gate", "unknown")
            confidence = p.get("confidence")
            missing = p.get("missing_fields") or []
            bits = [f"gate {gate}"]
            if isinstance(confidence, (int, float)):
                bits.append(f"confidence {confidence:.2f}")
            if missing:
                bits.append(f"still missing {', '.join(missing)}")
            return ", ".join(bits)

        case "claim.packet_assembled":
            total = p.get("total_claimed_inr")
            docs = len(p.get("attached_document_ids") or [])
            amount = f"INR {total:,}" if isinstance(total, int) else "an amount"
            return f"{amount} claimed, {docs} document(s) attached"

        case "claim.submitted":
            return f"{p.get('sla_kind', 'claim')}, SLA to {p.get('sla_deadline') or 'unset'}"

        case "clinician.note":
            return str(p.get("captured") or "note recorded")

        case "emergency.access":
            return str(p.get("scope") or "summary read")

        case "comms.sent" | "comms.blocked" | "comms.not_delivered":
            return str(p.get("reason") or p.get("template_name") or p.get("message_class") or "")

        case _:
            return str(p.get("note") or p.get("summary") or "")


def _fork(receipts: list[Receipt]) -> dict | None:
    """Describe the QUERY → gather → re-check arc, if the chain contains it.

    Returns None when it does not. This narrates receipts that exist; it never
    causes one to be rendered, and every index it reports is a real `seq`.
    """
    adjudications = [r for r in receipts if r.kind == "claim.adjudicated"]
    queries = [r for r in adjudications if r.payload.get("outcome") == "QUERY"]
    if not queries:
        return None

    first_query = queries[0]
    asked_for = first_query.payload.get("missing_documents") or []

    # What happened after the query, in the order it happened.
    after = [r for r in receipts if r.seq > first_query.seq]
    gathered = [r.seq for r in after
                if r.kind in {"evidence.assessed", "evidence.enriched",
                              "claim.packet_assembled"}]
    resolved = next((r for r in after if r.kind == "claim.adjudicated"
                     and r.payload.get("outcome") != "QUERY"), None)

    return {
        "queried_at_seq": first_query.seq,
        "asked_for": [m.replace("_", " ") for m in asked_for],
        "gathered_at_seqs": gathered,
        "resolved_at_seq": resolved.seq if resolved else None,
        "resolved_outcome": resolved.payload.get("outcome") if resolved else None,
        # Rounds counted from the receipts, NOT from the adjudicator's own
        # `attempt` field, which stays 1 because each resubmission is a new
        # submission rather than a retry of the old one.
        "rounds": len(adjudications),
        "still_open": resolved is None,
    }


def compose_trace(case_id: str) -> DecisionTrace:
    """The chain, as a sequence a person can follow. One step per receipt."""
    chain = service.get_chain(case_id)
    receipts = list(chain.receipts)

    steps = [
        TraceStep(
            seq=r.seq,
            at=r.created_at,
            actor=r.actor,
            kind=r.kind,
            what=_WHAT.get(r.kind, r.kind),
            detail=_detail(r),
            receipt_hash=r.hash,
        )
        for r in receipts
    ]

    verification = chain.verify()
    return DecisionTrace(
        case_id=case_id,
        steps=steps,
        receipt_count=len(receipts),
        chain_head_hash=chain.head_hash,
        chain_verified=verification.ok,
        query_fork=_fork(receipts),
    )
