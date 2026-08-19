"""Evidence / STEP_UP agent tools.

Confidence-gated pre-submission enrichment. This runs on the packet Anbu Care
submits — it is NOT inside the insurer's adjudication loop and does not
pre-empt a denial. Its only job is to raise first-pass approval odds before the
packet goes out.
"""

from __future__ import annotations

from typing import Any

from anbu_care import service
from anbu_care.schemas import ClaimPacket, EvidenceAssessment, EvidenceGate

# Below this, the packet is enriched before submission rather than sent thin.
STEP_UP_THRESHOLD = 0.75
# Below this, submitting would waste the first-pass attempt entirely.
BLOCK_THRESHOLD = 0.40

# Each requirement contributes to packet confidence. Weights sum to 1.0.
REQUIREMENTS: list[tuple[str, float, str]] = [
    ("policy_number", 0.15, "policy number on the packet"),
    ("admission_summary", 0.20, "admission/discharge summary text"),
    ("itemized_bills_inr", 0.25, "itemised bill lines"),
    ("diagnostics", 0.15, "diagnostic reports referenced"),
    ("attached_document_ids", 0.15, "source documents attached"),
    ("matched_policy_clauses", 0.10, "policy clauses matched to the claimed items"),
]


def _score_packet(packet: ClaimPacket) -> tuple[float, list[str], list[str]]:
    confidence = 0.0
    missing: list[str] = []
    present: list[str] = []
    for field, weight, label in REQUIREMENTS:
        value = getattr(packet, field, None)
        filled = bool(value)
        if filled:
            confidence += weight
            present.append(label)
        else:
            missing.append(label)
    return round(confidence, 3), missing, present


def assess_claim_packet(case_id: str, packet_id: str) -> dict[str, Any]:
    """Score a claim packet's completeness and decide whether to step up.

    Args:
        case_id: The case the packet belongs to.
        packet_id: The packet to assess.

    Returns:
        A confidence score, a gate decision (PASS / STEP_UP / BLOCK), what is
        missing, and the concrete enrichment actions to take before submitting.
    """
    packet = service.load_packet(case_id, packet_id)
    if packet is None:
        return {"status": "error", "error": f"no packet {packet_id} on case {case_id}"}

    confidence, missing, present = _score_packet(packet)

    if confidence >= STEP_UP_THRESHOLD:
        gate = EvidenceGate.PASS
        rationale = (
            f"Packet confidence {confidence:.2f} clears the {STEP_UP_THRESHOLD:.2f} bar. "
            "Submitting as-is."
        )
    elif confidence >= BLOCK_THRESHOLD:
        gate = EvidenceGate.STEP_UP
        rationale = (
            f"Packet confidence {confidence:.2f} is below the {STEP_UP_THRESHOLD:.2f} bar. "
            "Enriching before submission to raise first-pass approval odds — this is "
            "pre-submission enrichment, not an appeal against a denial."
        )
    else:
        gate = EvidenceGate.BLOCK
        rationale = (
            f"Packet confidence {confidence:.2f} is below the {BLOCK_THRESHOLD:.2f} floor. "
            "Submitting now would burn the first-pass attempt on an incomplete packet. "
            "Gather the missing evidence first."
        )

    actions = [f"attach or generate: {item}" for item in missing]
    assessment = EvidenceAssessment(
        packet_id=packet_id,
        confidence=confidence,
        gate=gate,
        missing_fields=missing,
        enrichment_actions=actions,
        rationale=rationale,
    )

    receipt = service.append_receipt(
        case_id,
        kind="evidence.assessed",
        actor="evidence_agent",
        payload=assessment.model_dump(mode="json"),
    )
    return {
        "status": "ok",
        "assessment": assessment.model_dump(mode="json"),
        "present": present,
        "receipt_id": receipt.receipt_id,
    }


def enrich_claim_packet(
    case_id: str,
    packet_id: str,
    matched_policy_clauses: list[str],
    additional_document_ids: list[str],
    diagnostics: list[str],
) -> dict[str, Any]:
    """Add the evidence a STEP_UP assessment asked for, then re-score.

    Args:
        case_id: The case the packet belongs to.
        packet_id: The packet to enrich.
        matched_policy_clauses: Policy clauses you matched to the claimed items.
        additional_document_ids: Prior records to attach, from the parent's KB.
        diagnostics: Diagnostic reports to reference.

    Returns:
        The enriched packet and its new confidence score.
    """
    packet = service.load_packet(case_id, packet_id)
    if packet is None:
        return {"status": "error", "error": f"no packet {packet_id} on case {case_id}"}

    before, _, _ = _score_packet(packet)

    for clause in matched_policy_clauses:
        if clause not in packet.matched_policy_clauses:
            packet.matched_policy_clauses.append(clause)
    for doc_id in additional_document_ids:
        if doc_id not in packet.attached_document_ids:
            packet.attached_document_ids.append(doc_id)
    for diagnostic in diagnostics:
        if diagnostic not in packet.diagnostics:
            packet.diagnostics.append(diagnostic)

    service.save_packet(packet)
    after, missing, _ = _score_packet(packet)

    receipt = service.append_receipt(
        case_id,
        kind="evidence.enriched",
        actor="evidence_agent",
        payload={
            "packet_id": packet_id,
            "confidence_before": before,
            "confidence_after": after,
            "added_clauses": matched_policy_clauses,
            "added_documents": additional_document_ids,
            "added_diagnostics": diagnostics,
            "still_missing": missing,
        },
    )
    return {
        "status": "ok",
        "confidence_before": before,
        "confidence_after": after,
        "still_missing": missing,
        "packet": packet.model_dump(mode="json"),
        "receipt_id": receipt.receipt_id,
    }
