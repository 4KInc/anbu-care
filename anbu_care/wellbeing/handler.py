"""What happens after a check-in is stored.

Ordinary check-ins end here with an acknowledgement. A check-in that the
deterministic severity table flags as HIGH gets a person involved: a case is
opened, triage runs on the recognised symptom terms exactly as it would for any
other intake, the consented care circle is told, and the sender is told to call
an ambulance.

The order matters and is not arbitrary. Notification happens BEFORE the reply
is composed, because the reply says whether anyone was actually alerted and
that sentence has to be true. It is the one message in this system where a
false claim could get somebody hurt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from anbu_care import service
from anbu_care.provenance.store import PARENT_SUBJECT
from anbu_care.schemas import WellbeingEntry
from anbu_care.wellbeing import escalation as esc

logger = logging.getLogger(__name__)


@dataclass
class Handled:
    reply: str
    escalated: bool = False
    case_id: str | None = None
    alerted: list[str] = field(default_factory=list)
    not_alerted: list[str] = field(default_factory=list)


def handle(entry: WellbeingEntry, parent_id: str) -> Handled:
    """Escalate if the table says so. Otherwise acknowledge and stop."""
    profile = service.load_profile(parent_id)
    conditions = list(profile.chronic_conditions) if profile else []

    verdict = esc.assess(entry.text, conditions)

    if not verdict.escalate:
        return Handled(reply=esc.reply_text(verdict, []))

    case_id = _open_and_triage(entry, parent_id, verdict)
    alerted, not_alerted = _tell_the_care_circle(case_id, parent_id, verdict)

    return Handled(
        reply=esc.reply_text(verdict, alerted),
        escalated=True,
        case_id=case_id,
        alerted=alerted,
        not_alerted=not_alerted,
    )


def _open_and_triage(entry: WellbeingEntry, parent_id: str, verdict: esc.Escalation) -> str:
    """Open an episode and route it, recording why on both chains.

    The escalation receipt goes on the parent chain and names the phrases that
    matched, not a conclusion. "These words matched the red-flag table" is
    auditable. "This person is having a cardiac event" would be a diagnosis
    nobody made.
    """
    case = service.open_case(parent_id)

    service.append_receipt(
        parent_id,
        kind="wellbeing.escalated",
        actor="wellbeing_intake",
        payload={
            "entry_id": entry.entry_id,
            "case_id": case.case_id,
            "severity": verdict.severity.value,
            "matched_rules": verdict.matched,
            "model_terms": verdict.model_terms,
            "model_used": verdict.model_used,
            "model_note": verdict.model_note,
            "note": (
                "A deterministic red-flag table matched these phrases and a case was "
                "opened so a human is involved. This is a routing decision, not a "
                "clinical assessment, and no diagnosis has been made."
            ),
        },
        subject=PARENT_SUBJECT,
    )

    from anbu_care.tools import triage_tools

    # Same entry point, same table, same receipt as any other intake. The
    # symptoms are the model's normalised terms; the raw words go along as
    # free_text so the table sees exactly what was said.
    triage_tools.run_triage(
        parent_id=parent_id,
        symptoms=verdict.symptoms,
        free_text=entry.text,
        reported_by=entry.source,
        lat=0.0,
        lon=0.0,
        case_id=case.case_id,
    )
    return case.case_id


def _tell_the_care_circle(
    case_id: str, parent_id: str, verdict: esc.Escalation
) -> tuple[list[str], list[str]]:
    """Notify consented contacts. Returns who was actually reached.

    Logistics only, through the existing gate: the care circle is told to call,
    never what the message said. The symptom words stay in the credentialed
    record.
    """
    from anbu_care.care_circle import notify as care_notify

    profile = service.load_profile(parent_id)
    name = profile.name.split()[0] if profile and profile.name else "your parent"

    try:
        results = care_notify.notify(
            case_id=case_id,
            parent_id=parent_id,
            hospital_name="a hospital, being arranged",
            timestamp="just now",
            cashless_status=(
                f"An urgent message was received from {name}. Please call them now"
            ),
        )
    except Exception:
        logger.exception("care-circle notification failed")
        return [], []

    alerted = [r.contact_name for r in results if r.delivered]
    not_alerted = [r.contact_name for r in results if not r.delivered]
    return alerted, not_alerted
