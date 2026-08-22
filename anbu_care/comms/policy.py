"""WhatsApp message policy.

Meta's healthcare policy and India's DPDP Act draw a hard line: logistics,
status, and billing may go over WhatsApp; clinical detail may not. This is
enforced in code, before send, because a prompt that is asked not to leak a lab
value is not a control.

The design consequence is deliberate: because clinical data cannot go over
WhatsApp, the WhatsApp beats are logistics and billing. The ECG reading and the
diagnosis live inside the secure dashboard.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from anbu_care.schemas import MessageClass

# Free-form replies are only permitted within 24h of family-initiated contact;
# outside it, only pre-approved templates may be sent.
FREE_FORM_WINDOW = timedelta(hours=24)

ALLOWED_CLASSES = {MessageClass.LOGISTICS, MessageClass.STATUS, MessageClass.BILLING}

# Patterns that mark a message as clinical. Matching any of these blocks the
# send outright, whatever the caller claimed the message class was.
CLINICAL_PATTERNS: list[tuple[str, str]] = [
    (r"\b(diagnos(is|ed|tic finding)|impression)\b", "states a diagnosis"),
    (r"\b(ecg|ekg|echo|troponin|creatinine|hba1c|ldl|hdl|cholesterol|wbc|rbc|platelet|haemoglobin|hemoglobin)\b",
     "names a lab or diagnostic result"),
    (r"\b\d+(\.\d+)?\s*(mg/dl|mmol/l|ng/ml|g/dl|mmhg|bpm|iu/l)\b", "carries a clinical measurement"),
    (r"\b(prescrib\w*|dosage|\d+\s*mg\b|\d+\s*ml\b|twice daily|bd|tds|od)\b", "carries prescription specifics"),
    (r"\b(myocardial infarction|stroke|infarct|stenosis|tumou?r|carcinoma|sepsis|arrhythmia)\b",
     "names a clinical condition"),
    (r"\b(biopsy|histopath\w*|culture report|scan (report|findings))\b", "references a diagnostic report"),
]

# Where a family member goes for anything the gate will not send. Every
# template points here, because "we did not tell you the lab value" is only
# reasonable if the same message says where the lab value actually is.
DASHBOARD_URL = os.getenv("ANBU_DASHBOARD_URL", "https://anbu-care-37j4eofpwq-el.a.run.app/app")

# The pre-approved template set. Production template approval takes ~10-15
# business days and will not clear inside the hackathon window, so these are
# registered against the WhatsApp Business API sandbox.
#
# These are read by a worried son or daughter on a phone, usually at a bad
# hour, so they are written the way a person would write them. No em dashes,
# no semicolons, no stacked clauses, and a link rather than an instruction to
# go and find something.
TEMPLATES: dict[str, dict[str, object]] = {
    "admission_alert": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: {parent_name} is being taken to {hospital_name} in {hospital_area}. "
                "The reason given was {reason_short}. "
                "You can see everything we know here: {dashboard_url}",
        "params": ["parent_name", "hospital_name", "hospital_area", "reason_short"],
    },
    "status_update": {
        "message_class": MessageClass.STATUS,
        "body": "Anbu Care: {parent_name} is {status}. "
                "That is from {hospital_name} at {timestamp}. "
                "The full record is here: {dashboard_url}",
        "params": ["parent_name", "status", "hospital_name", "timestamp"],
    },
    "claim_stage": {
        "message_class": MessageClass.BILLING,
        "body": "Anbu Care: the claim for {parent_name} has moved to {stage}. "
                "The amount claimed is INR {amount}. "
                "You can follow it here: {dashboard_url}",
        "params": ["parent_name", "stage", "amount"],
    },
    "billing_summary": {
        "message_class": MessageClass.BILLING,
        "body": "Anbu Care: the bill for {parent_name} comes to INR {total} across {line_count} items. "
                "The itemised breakdown is here: {dashboard_url}",
        "params": ["parent_name", "total", "line_count"],
    },
    "bill_recorded": {
        # BILLING, and the numbers here are the family's own bill read back to
        # them — what was charged, not what a clinician found. The estimate is
        # named an estimate in the copy, because a figure in a WhatsApp message
        # is the version people remember.
        "message_class": MessageClass.BILLING,
        "body": "Anbu Care: that bill is on {parent_name}'s record. "
                "{line_count} line items, INR {total_billed} billed.\n"
                "Estimated split against her policy: about INR {estimated_covered} "
                "covered, about INR {estimated_you_pay} to pay.\n\n"
                "That is an estimate from the policy terms, not the insurer's "
                "decision. The itemised breakdown, and the photo it was read "
                "from, are here: {dashboard_url}",
        "params": ["parent_name", "line_count", "total_billed",
                   "estimated_covered", "estimated_you_pay"],
    },
    "bill_unreadable": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: that bill could not be read. {reason}\n"
                "The photo is kept. Send a clearer one, or add the amounts by "
                "hand here: {dashboard_url}",
        "params": ["reason"],
    },
    "clinician_handoff_link": {
        # LOGISTICS, and it must stay that way: this message carries a link and
        # an instruction, never a finding. The allergies live behind the link,
        # which is the whole point — the gate would block them travelling over
        # WhatsApp, and a link is not the thing it points at.
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: if you are with {parent_name} at the hospital, or you "
                "can reach whoever is, show the treating team this link.\n"
                "{handoff_url}\n\n"
                "It opens a read-only summary of her allergies, conditions, "
                "medication and recent results. No login is needed. It stops "
                "working in {expires_minutes} minutes and you can stop it sooner "
                "from the dashboard.\n"
                "Every time it is opened, that is recorded on her case.",
        "params": ["parent_name", "handoff_url", "expires_minutes"],
    },
    "urgent_family_alert": {
        "message_class": MessageClass.STATUS,
        # Written for one reader: the son or daughter who wakes at 2am to this.
        # Everything they will ask in the first thirty seconds, in the order
        # they will ask it — what happened, where is she, why there, is it
        # covered, what do I do — and then a way to see the rest.
        "body": "Anbu Care, urgent. {parent_name} sent this at {timestamp}:\n"
                "\"{said}\"\n"
                "{words_note}"
                "{understood_as}"
                "\nShe is being directed to {hospital_name}, {distance_km} km away. "
                "{why_hospital}\n"
                "{cashless_status}.\n\n"
                "Call her now. If you cannot reach her, call 108, the ambulance "
                "line in India. Anbu Care has not called an ambulance and cannot.\n"
                "Everything known so far: {dashboard_url}",
        "params": ["parent_name", "timestamp", "said", "words_note", "understood_as",
                   "hospital_name",
                   "distance_km", "why_hospital", "cashless_status"],
    },
    "urgent_family_alert_withheld": {
        "message_class": MessageClass.STATUS,
        # The same alert with the quote removed, for when what she wrote
        # contains medical detail the gate will not carry. Everything else is
        # identical, because the routing, the cost and the instruction to call
        # were never the problem. Being more clinically precise must not make a
        # mother harder to help.
        "body": "Anbu Care, urgent. {parent_name} sent a message at {timestamp}.\n"
                "What she wrote contains medical detail, so it is not repeated here. "
                "You can read it in the dashboard.\n"
                "{understood_as}"
                "\nShe is being directed to {hospital_name}, {distance_km} km away. "
                "{why_hospital}\n"
                "{cashless_status}.\n\n"
                "Call her now. If you cannot reach her, call 108, the ambulance "
                "line in India. Anbu Care has not called an ambulance and cannot.\n"
                "Her exact words and everything else: {dashboard_url}",
        "params": ["parent_name", "timestamp", "understood_as", "hospital_name",
                   "distance_km", "why_hospital", "cashless_status"],
    },
    "voice_note_unclear": {
        "message_class": MessageClass.STATUS,
        # A case is opened, but no triage has run and no hospital was chosen,
        # so this template names neither. Claiming a routing decision that was
        # never made would be a worse failure than the one it is reporting.
        "body": "Anbu Care, urgent. {parent_name} sent a voice note at {timestamp}.\n"
                "Anbu Care could not make out what she said. No symptoms have been "
                "identified and nothing has been assessed.\n\n"
                "Please listen to the recording and call her now. If you cannot reach "
                "her, call 108, the ambulance line in India. Anbu Care has not called "
                "an ambulance and cannot.\n"
                "Listen to it here: {dashboard_url}",
        "params": ["parent_name", "timestamp"],
    },
    "care_circle_unclear": {
        "message_class": MessageClass.LOGISTICS,
        # The neighbour is asked to go round. No hospital is named because none
        # was chosen, and no recording is shared because they are a notified
        # party, not someone with access to her record.
        "body": "Anbu Care: {parent_name} sent a voice message at {timestamp} that "
                "could not be understood. Please check on her or call her now. "
                "You are receiving this as a listed contact. No medical details are "
                "shared here.",
        "params": ["parent_name", "timestamp"],
    },
    "care_circle_notice": {
        "message_class": MessageClass.LOGISTICS,
        # Where, when, and whether the bill is covered. There is no slot for a
        # reason, a finding or a condition, so the commonest way clinical
        # detail reaches a template — someone filling a free-text field — does
        # not exist here.
        "body": "Anbu Care: {parent_name} is being directed to {hospital_name}, {timestamp}. "
                "{cashless_status}. "
                "You are receiving this as a listed contact. No reply is needed, "
                "and no medical details are shared here.",
        "params": ["parent_name", "hospital_name", "timestamp", "cashless_status"],
    },
    "doctor_assigned": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: Dr. {doctor_name} from {department} is now looking after {parent_name} "
                "at {hospital_name}. "
                "You can see the case here: {dashboard_url}",
        "params": ["doctor_name", "department", "parent_name", "hospital_name"],
    },
}


@dataclass
class GateResult:
    allowed: bool
    message_class: MessageClass
    reason: str
    requires_template: bool
    detected_clinical: list[str]


def classify_message(body: str, declared: MessageClass | None = None) -> tuple[MessageClass, list[str]]:
    """Detect clinical content regardless of what the caller declared."""
    lowered = body.lower()
    hits = [why for pattern, why in CLINICAL_PATTERNS if re.search(pattern, lowered)]
    if hits:
        return MessageClass.CLINICAL, hits
    return (declared or MessageClass.STATUS), []


def gate_message(
    body: str,
    declared: MessageClass | None = None,
    *,
    last_inbound_at: datetime | None = None,
    template_name: str | None = None,
    now: datetime | None = None,
) -> GateResult:
    """Decide whether this message may leave the platform over WhatsApp."""
    actual, hits = classify_message(body, declared)

    if actual is MessageClass.CLINICAL:
        return GateResult(
            allowed=False,
            message_class=actual,
            reason=(
                "Blocked: clinical detail may not be sent over WhatsApp under Meta's "
                "healthcare policy and DPDP. It "
                + ", ".join(hits)
                + ". Deliver this through the secure dashboard instead."
            ),
            requires_template=False,
            detected_clinical=hits,
        )

    if actual not in ALLOWED_CLASSES:
        return GateResult(
            allowed=False,
            message_class=actual,
            reason=f"Blocked: message class {actual.value} is not permitted over WhatsApp.",
            requires_template=False,
            detected_clinical=[],
        )

    current = now or datetime.now(UTC)
    in_window = last_inbound_at is not None and (current - last_inbound_at) <= FREE_FORM_WINDOW

    if in_window:
        return GateResult(
            allowed=True,
            message_class=actual,
            reason="Allowed: inside the 24-hour customer-service window opened by family-initiated contact.",
            requires_template=False,
            detected_clinical=[],
        )

    if template_name and template_name in TEMPLATES:
        return GateResult(
            allowed=True,
            message_class=actual,
            reason=f"Allowed: outside the 24-hour window, sent as pre-approved template '{template_name}'.",
            requires_template=True,
            detected_clinical=[],
        )

    return GateResult(
        allowed=False,
        message_class=actual,
        reason=(
            "Blocked: outside the 24-hour window and no pre-approved template named. "
            "Free-form messages are only permitted within 24h of family-initiated contact."
        ),
        requires_template=True,
        detected_clinical=[],
    )


def render_template(template_name: str, params: dict[str, str],
                    case_id: str | None = None, parent_id: str | None = None) -> str:
    template = TEMPLATES.get(template_name)
    if template is None:
        raise KeyError(f"unknown template '{template_name}'")
    required = set(template["params"])  # type: ignore[arg-type]
    missing = required - params.keys()
    if missing:
        raise ValueError(f"template '{template_name}' missing params: {sorted(missing)}")
    # The link is injected here, not passed in: a caller must not be able to
    # point a family member at an address of its choosing. It may name which
    # case to open, so the link lands on the episode the message is about
    # rather than on an empty dashboard.
    url = DASHBOARD_URL
    if case_id:
        url = f"{url}?case={case_id}"
        # A signed, expiring credential for this case only, so the person who
        # was woken at 2am can open the link instead of hunting for a token.
        # Absent when no signing secret is configured, in which case the link
        # still works but asks them to sign in.
        if parent_id:
            from anbu_care.webauth import make_link_token

            token = make_link_token(parent_id, case_id)
            if token:
                url = f"{url}&t={token}"
    return str(template["body"]).format(**{**params, "dashboard_url": url})


def consent_ok(consents: dict[str, datetime], purpose: str) -> bool:
    """DPDP requires purpose-specific, timestamped opt-in. A blanket checkbox
    is not sufficient, so consent is checked per purpose, not per contact."""
    return purpose in consents
