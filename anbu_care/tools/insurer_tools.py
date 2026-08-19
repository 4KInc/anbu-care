"""Insurer / TPA liaison agent tools.

There is no production insurer or TPA API to integrate against in the hackathon
window, so submission goes to a simulated endpoint and every response carries
that label. Packet assembly and SLA tracking are real: the packet is really
assembled from stored evidence, and the 1-hour cashless / 30-day reimbursement
clocks run against real wall time. Only the counterparty's answer is mocked.
"""

from __future__ import annotations

import hashlib
from typing import Any

from anbu_care import service
from anbu_care.config import settings
from anbu_care.schemas import (
    AdjudicationOutcome,
    ClaimPacket,
    ClaimStage,
    ClaimSubmission,
    DocumentKind,
)
from anbu_care.tpa import adjudicate

SIMULATION_NOTICE = "SIMULATED TPA — no production insurer API is integrated in this build."


def assemble_claim_packet(
    case_id: str,
    parent_id: str,
    admission_summary: str,
    itemized_bills_inr: dict[str, int],
    diagnostics: list[str],
    attached_document_ids: list[str],
    admitted_on: str,
    discharged_on: str,
) -> dict[str, Any]:
    """Compile bills, diagnostics, and the admission summary into one packet.

    Policy details are pulled from the parent's stored record rather than
    re-asked, which is the whole point of capturing insurance at onboarding.

    Args:
        case_id: The case being claimed for.
        parent_id: Whose policy this is.
        admission_summary: Admission and discharge summary text.
        itemized_bills_inr: Line item name to rupee amount, e.g. {"room": 24000}.
        diagnostics: Diagnostic reports performed, by name.
        attached_document_ids: Document ids from the parent's knowledge base.
        admitted_on: Admission date as ISO "YYYY-MM-DD". Pass "" if unknown —
            per-day sub-limits cannot be applied without it and the adjudicator
            will raise a query rather than guess.
        discharged_on: Discharge date as ISO "YYYY-MM-DD", or "" if unknown.

    Returns:
        The assembled packet, including sub-limit and sum-insured checks.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}

    total = sum(itemized_bills_inr.values())
    packet = ClaimPacket(
        packet_id=service.new_id("packet"),
        case_id=case_id,
        parent_id=parent_id,
        policy_number=profile.policy.policy_number if profile.policy else None,
        admission_summary=admission_summary,
        itemized_bills_inr=itemized_bills_inr,
        diagnostics=diagnostics,
        attached_document_ids=attached_document_ids,
        admitted_on=admitted_on or None,
        discharged_on=discharged_on or None,
        total_claimed_inr=total,
    )
    service.save_packet(packet)

    coverage = _check_coverage(packet, profile)
    receipt = service.append_receipt(
        case_id,
        kind="claim.packet_assembled",
        actor="insurer_liaison_agent",
        payload={
            "packet_id": packet.packet_id,
            "total_claimed_inr": total,
            "line_items": itemized_bills_inr,
            "attached_document_ids": attached_document_ids,
            "coverage_check": coverage,
        },
    )

    case = service.load_case(case_id)
    if case is not None:
        case.packet_id = packet.packet_id
        case.stage = "packet_assembled"
        service.update_case(case)

    return {
        "status": "ok",
        "packet": packet.model_dump(mode="json"),
        "coverage_check": coverage,
        "receipt_id": receipt.receipt_id,
    }


def _check_coverage(packet: ClaimPacket, profile) -> dict[str, Any]:
    """Cross-reference the claim against stored sum-insured and sub-limits."""
    policy = profile.policy
    if policy is None:
        return {"checked": False, "reason": "no policy on record for this parent"}

    warnings: list[str] = []
    if packet.total_claimed_inr > policy.sum_insured_inr:
        warnings.append(
            f"claimed INR {packet.total_claimed_inr} exceeds sum insured "
            f"INR {policy.sum_insured_inr} — the excess will not be payable"
        )
    for item, amount in packet.itemized_bills_inr.items():
        limit = policy.sub_limits_inr.get(item)
        if limit is not None and amount > limit:
            warnings.append(f"'{item}' at INR {amount} exceeds its sub-limit of INR {limit}")

    return {
        "checked": True,
        "insurer": policy.insurer,
        "sum_insured_inr": policy.sum_insured_inr,
        "cashless_eligible": policy.cashless_eligible,
        "total_claimed_inr": packet.total_claimed_inr,
        "warnings": warnings,
    }


def _attached_kinds(packet: ClaimPacket) -> tuple[set[DocumentKind], list[str]]:
    """Which document kinds are really attached, read from stored documents.

    Ground truth, not the packet's word for it: an id on the packet that has no
    stored document behind it does not count as attached. This is the same
    check that stops an agent claiming an ingest it never made.
    """
    stored = {d.document_id: d for d in service.list_documents(packet.parent_id)}
    kinds: set[DocumentKind] = set()
    dangling: list[str] = []
    for doc_id in packet.attached_document_ids:
        doc = stored.get(doc_id)
        if doc is None:
            dangling.append(doc_id)
        else:
            kinds.add(doc.kind)
    return kinds, dangling


def _run_adjudication(
    case_id: str,
    submission: ClaimSubmission,
    packet: ClaimPacket,
) -> dict[str, Any]:
    """Adjudicate, persist, receipt, and move the submission to match."""
    profile = service.load_profile(packet.parent_id)
    kinds, dangling = _attached_kinds(packet)

    submission.adjudication_attempts += 1
    result = adjudicate(
        packet,
        profile.policy if profile else None,
        kinds,
        attempt=submission.adjudication_attempts,
    )
    result.submission_id = submission.submission_id
    service.save_adjudication(result)

    if result.outcome is AdjudicationOutcome.QUERY:
        submission.stage = ClaimStage.QUERIED
        submission.query_raised_at = service._now()
        submission.query_response_deadline = service.sla_deadline(submission.sla_kind)
    elif result.outcome is AdjudicationOutcome.PARTIAL:
        submission.stage = ClaimStage.PARTIALLY_APPROVED
    elif result.outcome is AdjudicationOutcome.PASS:
        submission.stage = ClaimStage.APPROVED
    else:
        submission.stage = ClaimStage.DENIED
    service.save_submission(submission)

    receipt = service.append_receipt(
        case_id,
        kind="claim.adjudicated",
        actor="simulated_tpa",
        payload={
            "submission_id": submission.submission_id,
            "packet_id": packet.packet_id,
            "attempt": result.attempt,
            "outcome": result.outcome.value,
            "reasons": result.reasons,
            "lines": [line.model_dump(mode="json") for line in result.lines],
            "total_claimed_inr": result.total_claimed_inr,
            "total_allowed_inr": result.total_allowed_inr,
            "total_disallowed_inr": result.total_disallowed_inr,
            "missing_documents": result.missing_documents,
            "simulated": True,
            "adjudicator": result.adjudicator,
        },
    )

    service.publish_event(
        settings().topic_claim_status,
        {"case_id": case_id, "submission_id": submission.submission_id,
         "outcome": result.outcome.value},
    )

    payload = {
        "status": "ok",
        "adjudication": result.model_dump(mode="json"),
        "stage": submission.stage.value,
        "sla": service.sla_status(submission),
        "receipt_id": receipt.receipt_id,
        "notice": SIMULATION_NOTICE,
        "adjudicator": result.adjudicator,
    }
    if submission.query_response_deadline:
        payload["query_response_deadline"] = submission.query_response_deadline.isoformat()
    if dangling:
        payload["ignored_document_ids"] = dangling
        payload["ignored_note"] = (
            "these ids are on the packet but have no stored document behind them, "
            "so they were not counted as attached"
        )
    return payload


def submit_claim(case_id: str, packet_id: str, sla_kind: str) -> dict[str, Any]:
    """Submit the packet to the TPA and start the SLA clock.

    The counterparty is simulated. The SLA tracking is not — the deadline is a
    real timestamp and is checked against real wall time.

    Args:
        case_id: The case being claimed for.
        packet_id: The packet to submit.
        sla_kind: Either "cashless_preauth" (1-hour decision under the IRDAI
            2024 Master Circular) or "reimbursement" (30-day clock).

    Returns:
        The submission record, its SLA deadline, and the simulated
        acknowledgement — labelled as simulated.
    """
    packet = service.load_packet(case_id, packet_id)
    if packet is None:
        return {"status": "error", "error": f"no packet {packet_id} on case {case_id}"}

    if sla_kind not in {"cashless_preauth", "reimbursement"}:
        return {"status": "error", "error": "sla_kind must be 'cashless_preauth' or 'reimbursement'"}

    submission = ClaimSubmission(
        submission_id=service.new_id("sub"),
        packet_id=packet_id,
        case_id=case_id,
        stage=ClaimStage.SUBMITTED,
        sla_kind=sla_kind,
        sla_deadline=service.sla_deadline(sla_kind),
        simulated=settings().tpa_mode == "simulated",
        counterparty_note=SIMULATION_NOTICE,
    )
    service.save_submission(submission)

    ack = _simulated_ack(packet)
    adjudication = _run_adjudication(case_id, submission, packet)
    receipt = service.append_receipt(
        case_id,
        kind="claim.submitted",
        actor="insurer_liaison_agent",
        payload={
            "submission_id": submission.submission_id,
            "packet_id": packet_id,
            "sla_kind": sla_kind,
            "sla_deadline": submission.sla_deadline.isoformat() if submission.sla_deadline else None,
            "simulated": submission.simulated,
            "counterparty_ack": ack,
        },
    )

    case = service.load_case(case_id)
    if case is not None:
        case.submission_id = submission.submission_id
        case.stage = "claim_submitted"
        service.update_case(case)

    service.publish_event(
        settings().topic_claim_status,
        {"case_id": case_id, "submission_id": submission.submission_id, "stage": "submitted"},
    )

    return {
        "status": "ok",
        "submission": submission.model_dump(mode="json"),
        "counterparty_ack": ack,
        "adjudication": adjudication["adjudication"],
        "outcome": adjudication["adjudication"]["outcome"],
        "stage": adjudication["stage"],
        "sla": adjudication["sla"],
        "query_response_deadline": adjudication.get("query_response_deadline"),
        "ignored_document_ids": adjudication.get("ignored_document_ids", []),
        "receipt_id": receipt.receipt_id,
        "notice": SIMULATION_NOTICE,
        "adjudicator": adjudication["adjudicator"],
    }


def _simulated_ack(packet: ClaimPacket) -> dict[str, Any]:
    """A deterministic stand-in for a TPA acknowledgement.

    Derived from the packet id so the same packet always produces the same
    reference — a random one would make demo runs unreproducible.
    """
    digest = hashlib.sha256(packet.packet_id.encode()).hexdigest()[:8].upper()
    return {
        "simulated": True,
        "tpa_reference": f"SIM-TPA-{digest}",
        "received": True,
        "note": SIMULATION_NOTICE,
    }


def advance_claim_stage(case_id: str, submission_id: str, stage: str) -> dict[str, Any]:
    """Move a submission to its next stage and record it on the chain.

    Args:
        case_id: The case.
        submission_id: The submission to advance.
        stage: One of: under_review, approved, denied, paid.

    Returns:
        The updated submission and current SLA status.
    """
    submission = service.load_submission(case_id, submission_id)
    if submission is None:
        return {"status": "error", "error": f"no submission {submission_id} on case {case_id}"}
    try:
        new_stage = ClaimStage(stage)
    except ValueError:
        return {"status": "error", "error": f"unknown stage '{stage}'"}

    previous = submission.stage
    submission.stage = new_stage
    service.save_submission(submission)

    sla = service.sla_status(submission)
    receipt = service.append_receipt(
        case_id,
        kind="claim.stage_changed",
        actor="insurer_liaison_agent",
        payload={
            "submission_id": submission_id,
            "from": previous.value if isinstance(previous, ClaimStage) else str(previous),
            "to": new_stage.value,
            "sla": sla,
            "simulated": submission.simulated,
        },
    )
    service.publish_event(
        settings().topic_claim_status,
        {"case_id": case_id, "submission_id": submission_id, "stage": new_stage.value},
    )
    return {
        "status": "ok",
        "submission": submission.model_dump(mode="json"),
        "sla": sla,
        "receipt_id": receipt.receipt_id,
        "notice": SIMULATION_NOTICE,
    }


def check_claim_sla(case_id: str, submission_id: str) -> dict[str, Any]:
    """Check how much of the regulatory SLA window is left.

    Args:
        case_id: The case.
        submission_id: The submission to check.

    Returns:
        Deadline, seconds remaining, and whether the window has been breached.
    """
    submission = service.load_submission(case_id, submission_id)
    if submission is None:
        return {"status": "error", "error": f"no submission {submission_id} on case {case_id}"}
    return {"status": "ok", "sla": service.sla_status(submission), "notice": SIMULATION_NOTICE}


def respond_to_query(
    case_id: str,
    submission_id: str,
    attach_document_ids: list[str],
) -> dict[str, Any]:
    """Answer a raised query by attaching documents, then get re-adjudicated.

    Only documents that actually exist in the parent's record can be attached.
    If the document the query asked for is not on file, this reports that the
    query cannot be resolved — it never invents one, and it never reports
    progress that did not happen.

    Args:
        case_id: The case the claim belongs to.
        submission_id: The submission that was queried.
        attach_document_ids: Document ids from the parent's record to attach.

    Returns:
        The re-adjudication, or an explanation of why the query is unresolvable.
    """
    submission = service.load_submission(case_id, submission_id)
    if submission is None:
        return {"status": "error", "error": f"no submission {submission_id} on case {case_id}"}
    if submission.stage is not ClaimStage.QUERIED:
        return {
            "status": "error",
            "error": f"submission {submission_id} is at stage "
                     f"'{submission.stage.value}', not 'queried' — nothing to respond to",
        }

    packet = service.load_packet(case_id, submission.packet_id)
    if packet is None:
        return {"status": "error", "error": f"no packet {submission.packet_id} on case {case_id}"}

    stored = {d.document_id: d for d in service.list_documents(packet.parent_id)}
    unknown = [doc_id for doc_id in attach_document_ids if doc_id not in stored]
    if unknown:
        return {
            "status": "error",
            "error": "those documents are not on this parent's record",
            "unknown_document_ids": unknown,
            "available": [
                {"document_id": d.document_id, "kind": d.kind.value, "summary": d.summary[:80]}
                for d in stored.values()
            ],
            "hint": "attach an existing document, or say the query cannot be resolved. "
                    "Do not describe a document that is not on file as though it were.",
        }

    for doc_id in attach_document_ids:
        if doc_id not in packet.attached_document_ids:
            packet.attached_document_ids.append(doc_id)
    service.save_packet(packet)

    result = _run_adjudication(case_id, submission, packet)

    adjudication = result["adjudication"]
    if adjudication["outcome"] == AdjudicationOutcome.QUERY.value:
        result["unresolved"] = True
        result["unresolved_note"] = (
            "the query is still open: "
            + "; ".join(adjudication["reasons"])
            + ". Nothing on this parent's record satisfies it — say so plainly "
              "rather than describing the claim as progressing."
        )
    return result


def list_adjudications(case_id: str) -> dict[str, Any]:
    """Every adjudication on this case, in order, with its cited reasons.

    Args:
        case_id: The case to look up.

    Returns:
        The full adjudication history — all of it simulated.
    """
    history = service.list_adjudications(case_id)
    return {
        "status": "ok",
        "count": len(history),
        "adjudications": [a.model_dump(mode="json") for a in history],
        "notice": SIMULATION_NOTICE,
    }
