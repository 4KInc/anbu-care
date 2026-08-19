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

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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

# The pre-approved template set. Production template approval takes ~10–15
# business days and will not clear inside the hackathon window, so these are
# registered against the WhatsApp Business API sandbox.
TEMPLATES: dict[str, dict[str, object]] = {
    "admission_alert": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: {parent_name} is being taken to {hospital_name}, {hospital_area}. "
                "Reason: {reason_short}. Full details in your Anbu Care dashboard.",
        "params": ["parent_name", "hospital_name", "hospital_area", "reason_short"],
    },
    "status_update": {
        "message_class": MessageClass.STATUS,
        "body": "Anbu Care: {parent_name} — {status} at {hospital_name}, {timestamp}.",
        "params": ["parent_name", "status", "hospital_name", "timestamp"],
    },
    "claim_stage": {
        "message_class": MessageClass.BILLING,
        "body": "Anbu Care: claim for {parent_name} is now {stage}. "
                "Claimed amount INR {amount}. Track it in your dashboard.",
        "params": ["parent_name", "stage", "amount"],
    },
    "billing_summary": {
        "message_class": MessageClass.BILLING,
        "body": "Anbu Care: bill summary for {parent_name} — INR {total} across {line_count} items. "
                "Itemised breakdown in your dashboard.",
        "params": ["parent_name", "total", "line_count"],
    },
    "doctor_assigned": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: Dr. {doctor_name} ({department}) is attending to {parent_name} at {hospital_name}.",
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

    current = now or datetime.now(timezone.utc)
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


def render_template(template_name: str, params: dict[str, str]) -> str:
    template = TEMPLATES.get(template_name)
    if template is None:
        raise KeyError(f"unknown template '{template_name}'")
    required = set(template["params"])  # type: ignore[arg-type]
    missing = required - params.keys()
    if missing:
        raise ValueError(f"template '{template_name}' missing params: {sorted(missing)}")
    return str(template["body"]).format(**params)


def consent_ok(consents: dict[str, datetime], purpose: str) -> bool:
    """DPDP requires purpose-specific, timestamped opt-in. A blanket checkbox
    is not sufficient, so consent is checked per purpose, not per contact."""
    return purpose in consents
