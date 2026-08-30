"""The claim files itself when the discharge summary arrives.

This was the last lane in the system that still waited to be asked. Everything
around it had learned to act - an escalation files a cashless pre-authorisation
on its own, a scheduler notices when the insurer's hour runs out, an arriving
lab report closes the test it belongs to - and the reimbursement claim sat
there until somebody called a tool. On a recording that reads as staged,
because it is: the one moment the family would most want the system to move by
itself was the one moment it had to be pushed.

Nothing new had to be invented for it. The packet assembler, the adjudicator
and the SLA clock all existed and all worked. What was missing was the trigger,
and the discharge summary is the obvious one: it is the document that says the
admission is over, which is precisely the moment a reimbursement claim becomes
possible. The family photographs it for their own record and the claim starts.

WHAT IT WILL NOT DO. It will not file a claim twice - the case carries the
packet id and a second discharge summary finds it. It will not file without a
policy, because a claim with no insurer is a form addressed to nobody. It will
not file with no bills on the case, because a reimbursement claim with no
amounts asks an insurer for nothing and starts a thirty-day clock against it.
Each refusal is receipted with the reason rather than passing in silence, so a
case that did not file says why on its own chain.

DATES ARE NOT INVENTED. Where the reader could not make out an admission or
discharge date the field is passed through empty, and the adjudicator raises a
query rather than applying per-day sub-limits to a guess. That is the existing
behaviour and this does not route around it.

THE COUNTERPARTY IS SIMULATED and the submission says so, as it always has.
What is not simulated is the form: `claim_form.py` renders the Part A a person
could actually sign and send, filled from the record, and that document is the
part of this lane that was missing rather than pretended.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from anbu_care import service

log = logging.getLogger(__name__)

SLA_KIND = "reimbursement"          # the 30-day clock; real wall time
FORM_PREFIX = "claim-forms"


@dataclass(frozen=True)
class Filing:
    """What happened when the discharge summary landed."""

    outcome: str
    detail: str = ""
    packet_id: str = ""
    submission_id: str = ""
    sla_deadline: str = ""
    total_claimed_inr: int = 0
    form_object: str = ""
    guards_passed: list[str] = field(default_factory=list)

    @property
    def filed(self) -> bool:
        return self.outcome == "filed"


def _refuse(case_id: str, outcome: str, detail: str, passed: list[str]) -> Filing:
    """Record why no claim was filed. Silence here reads as nothing to claim."""
    try:
        service.append_receipt(
            case_id, kind="claim.not_filed", actor="claim_lane",
            payload={"reason": outcome, "detail": detail,
                     "guards_passed": passed,
                     "note": ("A discharge summary arrived and no reimbursement "
                              "claim was filed. This says why, so a case with no "
                              "claim on it is a decision on the record rather "
                              "than a gap.")})
    except Exception as e:  # noqa: BLE001 - the document stays on the record
        log.warning("could not receipt claim.not_filed on %s: %s", case_id, e)
    return Filing(outcome=outcome, detail=detail, guards_passed=passed)


def file_on_discharge(*, case_id: str, parent_id: str, payload: dict,
                      document_id: str = "",
                      now: datetime | None = None) -> Filing:
    """Assemble, submit and render the claim. Never raises into an ingest."""
    from anbu_care.bills import list_bills
    from anbu_care.tools import insurer_tools

    now = now or datetime.now(UTC)
    passed: list[str] = []

    case = service.load_case(case_id) if case_id else None
    if case is None:
        return Filing(outcome="no_case",
                      detail="the discharge summary is on her record but not on "
                             "an admission, so there is no claim to file")
    passed.append("case_known")

    # ONE CLAIM PER ADMISSION. A retaken photograph of the same paper, or a
    # second summary from the same stay, must not open a second claim against
    # one policy for one admission.
    if getattr(case, "packet_id", ""):
        return Filing(outcome="already_filed", packet_id=case.packet_id,
                      guards_passed=passed,
                      detail=f"this admission already has claim packet "
                             f"{case.packet_id}")
    passed.append("not_already_filed")

    profile = service.load_profile(parent_id)
    policy = getattr(profile, "policy", None) if profile else None
    if policy is None or not getattr(policy, "policy_number", ""):
        return _refuse(case_id, "no_policy",
                       "no insurance policy is on file for her, so there is no "
                       "insurer to claim from", passed)
    passed.append("policy_on_file")

    bills = list_bills(case_id)
    if not bills:
        return _refuse(case_id, "no_bills",
                       "no bill has been photographed for this admission, so a "
                       "claim would ask for nothing and start a thirty-day clock "
                       "against an empty form", passed)
    passed.append("bills_on_file")

    itemised: dict[str, int] = {}
    for bill in bills:
        for item in bill.line_items:
            key = (item.item or item.label or "other").strip().lower()
            itemised[key] = itemised.get(key, 0) + int(item.amount_inr or 0)

    documents = service.list_documents(parent_id)
    diagnostics = sorted({
        obs.name for doc in documents for obs in (doc.observations or []) if obs.name
    })

    assembled = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=parent_id,
        # The words on the paper, not a synthesis of them.
        admission_summary=str(payload.get("diagnosis") or "").strip(),
        itemized_bills_inr=itemised,
        diagnostics=diagnostics,
        attached_document_ids=[d.document_id for d in documents],
        # Passed through empty where the reader could not read one. The
        # adjudicator queries rather than guessing at per-day sub-limits.
        admitted_on=str(payload.get("admitted_on") or ""),
        discharged_on=str(payload.get("discharged_on") or ""),
    )
    if assembled.get("status") != "ok":
        return _refuse(case_id, "assembly_failed",
                       str(assembled.get("error") or "the packet could not be "
                           "assembled"), passed)
    passed.append("packet_assembled")
    packet_id = assembled["packet"]["packet_id"]

    submitted = insurer_tools.submit_claim(case_id, packet_id, SLA_KIND)
    if submitted.get("status") != "ok":
        return _refuse(case_id, "submission_failed",
                       str(submitted.get("error") or "the packet could not be "
                           "submitted"), passed)
    passed.append("submitted")

    packet = service.load_packet(case_id, packet_id)
    form_object = _store_form(case_id=case_id, packet=packet, profile=profile,
                              payload=payload, bills=bills, now=now)

    submission = submitted.get("submission") or {}
    service.append_receipt(
        case_id, kind="claim.filed_on_discharge", actor="claim_lane",
        payload={
            "packet_id": packet_id,
            "submission_id": submission.get("submission_id", ""),
            "from_document": document_id,
            "total_claimed_inr": getattr(packet, "total_claimed_inr", 0),
            "sla_kind": SLA_KIND,
            "sla_deadline": submission.get("sla_deadline", ""),
            "claim_form": form_object,
            "guards_passed": passed,
            "note": (
                "The discharge summary arrived and the reimbursement claim was "
                "assembled and submitted without anybody asking for it. The "
                "counterparty is a simulated adjudicator; the thirty-day clock "
                "is real wall time. A Part A claim form was filled from the "
                "record and stored - it is unsigned, and Anbu Care has not sent "
                "it to any insurer."
            ),
        })

    return Filing(
        outcome="filed", packet_id=packet_id,
        submission_id=submission.get("submission_id", ""),
        sla_deadline=str(submission.get("sla_deadline", "")),
        total_claimed_inr=int(getattr(packet, "total_claimed_inr", 0) or 0),
        form_object=form_object, guards_passed=passed,
        detail="the claim was assembled, submitted and a claim form filled")


def _store_form(*, case_id: str, packet, profile, payload: dict, bills: list,
                now: datetime) -> str:
    """Render and store the Part A. A failure here does not undo the claim."""
    from anbu_care.comms import storage
    from anbu_care.tpa import claim_form

    if packet is None:
        return ""
    try:
        pdf = claim_form.render(profile=profile, packet=packet,
                                discharge=payload, bills=bills, now=now)
        stored = storage.store(f"{FORM_PREFIX}/{case_id}/{packet.packet_id}.pdf",
                               pdf, content_type="application/pdf")
    except Exception:  # the claim is filed either way
        log.exception("could not render the claim form for %s", case_id)
        return ""
    return stored.object_name if stored.stored else ""
