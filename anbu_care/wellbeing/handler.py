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

    # Two audiences, deliberately told different things. The family decision
    # maker is woken at 2am and needs everything the system knows. A neighbour
    # or a listed doctor needs to be asked to go round, and is not entitled to
    # the rest.
    family = _tell_the_family(case_id, parent_id, entry)
    circle = _tell_the_care_circle(case_id, parent_id, verdict)
    alerted = family[0] + circle[0]
    not_alerted = family[1] + circle[1]

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


def _routing_lines(case_id: str) -> tuple[str, str, str]:
    """Hospital, distance and WHY that hospital, straight off the triage receipt.

    The "why" is the part usually thrown away, and at 2am it is the difference
    between "she is going somewhere" and "she is going somewhere further away
    on purpose, so the bill is covered". None of it is clinical: it is
    distance, empanelment and cost.
    """
    triage = next(
        (r for r in reversed(service.get_chain(case_id).receipts)
         if r.kind == "triage.decision"), None,
    )
    if triage is None:
        return "a hospital, being arranged", "unknown", ""

    recommended = triage.payload.get("recommended_hospital_id")
    for entry in triage.payload.get("ranked") or []:
        if entry.get("hospital_id") == recommended:
            name = str(entry.get("name") or "a hospital")
            distance = f"{entry.get('distance_km', 0):.1f}"
            why = _why_only(str(triage.payload.get("explanation") or ""))
            return name, distance, why
    return "a hospital, being arranged", "unknown", ""


def _why_only(explanation: str) -> str:
    """Keep the reasoning, drop the restatement and the internal scoring.

    The triage explanation opens with the severity and a "Recommending X (2.2
    km, score 0.971)" line. The alert already names the hospital and the
    distance, and a relevance score means nothing to someone woken at 2am, so
    both sentences go. What is left is the part worth reading: it is further
    away on purpose, and here is why.
    """
    sentences = [s.strip() for s in explanation.split(". ") if s.strip()]
    kept = [s for s in sentences
            if not s.startswith("Severity ") and not s.startswith("Recommending ")]
    text = ". ".join(kept)
    if text and not text.endswith("."):
        text += "."
    return text


def _tell_the_family(
    case_id: str, parent_id: str, entry: WellbeingEntry
) -> tuple[list[str], list[str]]:
    """The full picture, to contacts who hold admission-alert consent.

    Her own words are relayed. They are what she chose to send over WhatsApp
    herself, and a child who cannot tell "I cannot breathe" from "I turned my
    ankle" cannot judge how hard to panic. It still goes through the content
    gate: a message that turns out to carry a lab value is blocked like any
    other.
    """
    from anbu_care.comms import consent
    from anbu_care.comms.policy import consent_ok
    from anbu_care.tools import whatsapp_tools

    profile = service.load_profile(parent_id)
    if profile is None:
        return [], []

    first = profile.name.split()[0] if profile.name else "your parent"
    hospital, distance, why = _routing_lines(case_id)
    policy = getattr(profile, "policy", None)
    cashless = ("Cashless should apply at this hospital" if policy and policy.cashless_eligible
                else "Cashless is not confirmed for this admission")

    alerted: list[str] = []
    failed: list[str] = []
    for contact in profile.family_contacts:
        if not consent_ok(contact.consents, consent.ADMISSION_ALERTS):
            continue
        sent = whatsapp_tools.send_family_update(
            case_id=case_id, parent_id=parent_id, to_e164=contact.whatsapp_e164,
            template_name="urgent_family_alert",
            template_params={
                "parent_name": first,
                "timestamp": entry.received_at.strftime("%H:%M UTC"),
                "said": entry.text,
                "hospital_name": hospital,
                "distance_km": distance,
                "why_hospital": why,
                "cashless_status": cashless,
            },
            message_class="status",
            # Selection and gate must demand the SAME purpose. Without this the
            # class implies status_updates while the loop selects on
            # admission_alerts, and a contact who consented to admission alerts
            # is silently skipped by the gate after being chosen to receive one.
            purpose_override=consent.ADMISSION_ALERTS,
        )
        (alerted if sent.get("delivered") else failed).append(contact.name)
    return alerted, failed


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

    hospital, _, _ = _routing_lines(case_id)
    try:
        results = care_notify.notify(
            case_id=case_id,
            parent_id=parent_id,
            hospital_name=hospital,
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
