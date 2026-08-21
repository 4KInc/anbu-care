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
from datetime import UTC, datetime

from anbu_care import service
from anbu_care.comms import consent, localtime
from anbu_care.provenance.store import PARENT_SUBJECT
from anbu_care.schemas import MessageClass, WellbeingEntry
from anbu_care.wellbeing import escalation as esc

logger = logging.getLogger(__name__)


@dataclass
class Handled:
    reply: str
    escalated: bool = False
    case_id: str | None = None
    alerted: list[str] = field(default_factory=list)
    not_alerted: list[str] = field(default_factory=list)
    called: list[str] = field(default_factory=list)


def handle_unclear_voice(entry: WellbeingEntry, parent_id: str) -> Handled:
    """A voice note nobody could make out.

    A case IS opened. She recorded something urgent enough to send, and the
    failure to understand it does not make the event less real — it makes it
    less legible, which is a reason to involve people rather than fewer.

    But no triage runs and no severity is set, because nothing was recognised.
    The case exists with no triage decision, which the dashboard already reads
    as "triage has not run on this case". Inventing a severity to justify the
    case would be exactly the inference this whole path refuses to make.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return Handled(reply="Thanks, that's noted.")

    first = profile.name.split()[0] if profile.name else "your parent"
    case = service.open_case(parent_id)

    service.append_receipt(
        parent_id,
        kind="wellbeing.unclear",
        actor="wellbeing_intake",
        payload={
            "entry_id": entry.entry_id, "case_id": case.case_id,
            "source_kind": entry.source_kind,
            "note": ("A voice note arrived and could not be transcribed. A case was "
                     "opened so a human listens to it. No symptom was identified, no "
                     "severity was assessed, and no diagnosis exists."),
        },
        subject=PARENT_SUBJECT,
    )

    when = localtime.for_reader(
        entry.received_at, "UTC", getattr(profile, "timezone", "Asia/Kolkata"), profile.city,
    )
    alerted, failed, reached = _tell_unclear(
        case.case_id, parent_id, profile, first, entry, "voice_note_unclear",
        consent.ADMISSION_ALERTS, MessageClass.STATUS,
    )
    circle_alerted, circle_failed, _ = _tell_unclear(
        case.case_id, parent_id, profile, first, entry, "care_circle_unclear",
        consent.OUTBOUND_NOTIFY, MessageClass.LOGISTICS, skip=reached,
    )

    everyone = _unique(alerted + circle_alerted)
    reply = ("We could not make out your voice note. "
             + ("We have asked " + " and ".join(everyone) + " to call you now."
                if everyone else
                "We could not reach anyone, so please call someone yourself.")
             + f" If this is an emergency, call {esc.EMERGENCY_NUMBER} now.")
    return Handled(
        reply=reply, escalated=True, case_id=case.case_id,
        alerted=everyone,
        not_alerted=[n for n in _unique(failed + circle_failed) if n not in everyone],
    )


def _tell_unclear(case_id, parent_id, profile, first, entry, template, purpose, klass,
                  skip: set[str] | None = None):
    """Send one of the unclear templates to everyone holding `purpose`."""
    from anbu_care.comms.policy import consent_ok
    from anbu_care.tools import whatsapp_tools

    already = skip or set()
    alerted: list[str] = []
    failed: list[str] = []
    reached: set[str] = set()
    for contact in profile.family_contacts:
        if contact.whatsapp_e164 in already or not consent_ok(contact.consents, purpose):
            continue
        reached.add(contact.whatsapp_e164)
        sent = whatsapp_tools.send_family_update(
            case_id=case_id, parent_id=parent_id, to_e164=contact.whatsapp_e164,
            template_name=template,
            template_params={
                "parent_name": first,
                "timestamp": localtime.for_reader(
                    entry.received_at, contact.timezone,
                    getattr(profile, "timezone", "Asia/Kolkata"), profile.city,
                ),
            },
            message_class=klass.value,
            purpose_override=purpose,
        )
        (alerted if sent.get("delivered") else failed).append(contact.name)
    return alerted, failed, reached


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
    family_alerted, family_failed, family_numbers = _tell_the_family(
        case_id, parent_id, entry, verdict,
    )
    circle_alerted, circle_failed = _tell_the_care_circle(
        case_id, parent_id, verdict, skip_numbers=family_numbers,
    )
    # One person is one name, however many lists they appear on.
    alerted = _unique(family_alerted + circle_alerted)
    not_alerted = [n for n in _unique(family_failed + circle_failed) if n not in alerted]

    # A message arriving while someone sleeps is a message that did not happen.
    # The call goes out WITH it, not after a timer: Cloud Run cannot hold one
    # reliably, and for crushing chest pain a two minute wait is the wrong
    # behaviour regardless.
    called = _ring_them(case_id, parent_id)

    return Handled(
        reply=esc.reply_text(verdict, alerted, called=called),
        escalated=True,
        case_id=case_id,
        alerted=alerted,
        not_alerted=not_alerted,
        called=called,
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


def _ring_them(case_id: str, parent_id: str) -> list[str]:
    """Ring the family first, then the neighbour. Report who was actually dialled.

    The ladder is ordered by who can do most: the family decision maker holds
    the dashboard and the insurer relationship, the neighbour can be at the
    door in four minutes. Both are called, because at 2am the question is not
    who is best placed but who picks up.

    Spoken content goes through the same content gate as everything else. A
    second, ungated route "because it is only a phone call" is how a diagnosis
    eventually escapes.
    """
    from anbu_care.comms import consent, voice
    from anbu_care.comms.policy import classify_message, consent_ok
    from anbu_care.schemas import MessageClass

    profile = service.load_profile(parent_id)
    if profile is None:
        return []

    first = profile.name.split()[0] if profile.name else "your parent"
    spoken = (
        f"This is Anbu Care. {first} has sent an urgent message and may need help now. "
        "Please call her. Anbu Care has not called an ambulance and cannot."
    )

    actual, hits = classify_message(spoken, MessageClass.LOGISTICS)
    if actual is MessageClass.CLINICAL:
        logger.warning("spoken alert blocked as clinical (%s); no call placed", hits)
        return []

    called: list[str] = []
    for contact in profile.family_contacts:
        may_call = (consent_ok(contact.consents, consent.ADMISSION_ALERTS)
                    or consent_ok(contact.consents, consent.OUTBOUND_NOTIFY))
        if not may_call:
            continue

        result = voice.place_call(contact.whatsapp_e164, spoken)
        service.append_receipt(
            case_id,
            kind="voice.placed" if result.placed else "voice.not_placed",
            actor="wellbeing_intake",
            payload={"contact_name": contact.name, **result.as_dict(),
                     "spoken": spoken,
                     "note": ("A placed call is not an answered call. Whether anyone "
                              "picked up is not known here.")},
        )
        if result.placed:
            called.append(contact.name)
    return called


def _unique(names: list[str]) -> list[str]:
    """Order-preserving dedupe. "alerted X and X" reads as a broken system at
    the moment it most needs to be believed."""
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


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


def _understood_as(verdict: esc.Escalation) -> str:
    """The system showing its working, in one line.

    She may have written in Tamil, or in Tamil spelled with English letters,
    and her son may be half awake and unable to read either. The alert says it
    is urgent; without this it never says WHY, which makes the escalation
    illegible exactly when it needs to be obvious.

    These are recognised terms, not findings. The same phrases are already on
    the chain as matched_rules; this only puts them where the person who has to
    act can see them.
    """
    terms = [t for t in dict.fromkeys(verdict.model_terms) if t]
    if not terms:
        return ""
    return "Understood as: " + ", ".join(terms) + ".\n"


def _tell_the_family(
    case_id: str, parent_id: str, entry: WellbeingEntry, verdict: esc.Escalation
) -> tuple[list[str], list[str], set[str]]:
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
        return [], [], set()

    first = profile.name.split()[0] if profile.name else "your parent"
    hospital, distance, why = _routing_lines(case_id)
    policy = getattr(profile, "policy", None)
    cashless = ("Cashless should apply at this hospital" if policy and policy.cashless_eligible
                else "Cashless is not confirmed for this admission")

    alerted: list[str] = []
    failed: list[str] = []
    reached: set[str] = set()
    for contact in profile.family_contacts:
        if not consent_ok(contact.consents, consent.ADMISSION_ALERTS):
            continue
        reached.add(contact.whatsapp_e164)
        params = {
                "parent_name": first,
                # Rendered per recipient: the son abroad and a neighbour two
                # streets away read the same instant differently, and neither
                # should be doing timezone arithmetic at 2am.
                "timestamp": localtime.for_reader(
                    entry.received_at, contact.timezone,
                    getattr(profile, "timezone", "Asia/Kolkata"), profile.city,
                ),
                "said": entry.text,
                "hospital_name": hospital,
                "distance_km": distance,
                "why_hospital": why,
                "cashless_status": cashless,
                "understood_as": _understood_as(verdict),
                # A transcript is what a model heard, not what she said.
                # Quoting it as her words would put words in her mouth, and her
                # son would act on them.
                "words_note": (
                    "That is what Anbu Care heard in her voice note. It may be "
                    "imperfect, so listen to the recording in the dashboard.\n"
                    if entry.source_kind == "voice" else
                    "Those are her own words, not a medical assessment.\n"
                ),
        }
        sent = whatsapp_tools.send_family_update(
            case_id=case_id, parent_id=parent_id, to_e164=contact.whatsapp_e164,
            template_name="urgent_family_alert",
            template_params={**params, "said": entry.text},
            message_class="status",
            # Selection and gate must demand the SAME purpose. Without this the
            # class implies status_updates while the loop selects on
            # admission_alerts, and a contact who consented to admission alerts
            # is silently skipped by the gate after being chosen to receive one.
            purpose_override=consent.ADMISSION_ALERTS,
        )
        # Quoting her is what makes the alert useful, and it is also the only
        # thing in it the gate can refuse. If her own words carry a lab value
        # the message is blocked, and without a second attempt the one person
        # who can actually act is the only one not told. Being more clinically
        # precise must not make a mother harder to help.
        if not sent.get("allowed"):
            logger.info("family alert withheld her words for %s; retrying without the quote",
                        contact.name)
            sent = whatsapp_tools.send_family_update(
                case_id=case_id, parent_id=parent_id, to_e164=contact.whatsapp_e164,
                template_name="urgent_family_alert_withheld",
                template_params=params,
                message_class="status",
                purpose_override=consent.ADMISSION_ALERTS,
            )

        (alerted if sent.get("delivered") else failed).append(contact.name)
    return alerted, failed, reached


def _tell_the_care_circle(
    case_id: str, parent_id: str, verdict: esc.Escalation,
    skip_numbers: set[str] | None = None,
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
            now=datetime.now(UTC),
            skip_numbers=skip_numbers,
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
