"""Intake signals.

Anbu Care does not watch anyone. It has no sensors, no passive monitoring, and
no way to notice that something has happened. An episode begins when a signal
**arrives from outside** — a hospital intake desk posting to a webhook, a family
member submitting a form, a neighbour tapping a button.

That distinction is load-bearing and is preserved in the wording everywhere:
a signal is *received*, never *detected*. Anything implying the system noticed
something on its own is a bug, not a feature.

For the hackathon build every channel is a labelled stub — no ER system posts to
us, and no WhatsApp number is verified — so signals carry
"SIMULATED INTAKE SIGNAL" and say which channel they came in on.
"""

from __future__ import annotations

from typing import Any

from anbu_care import service
from anbu_care.config import settings

SIGNAL_LABEL = "SIMULATED INTAKE SIGNAL — received from an external channel, not detected by Anbu Care"

# The channels an episode can start on. All stubs in this build.
INTAKE_CHANNELS: dict[str, str] = {
    "er_desk_webhook": "hospital emergency-desk intake posting to our webhook",
    "family_form": "a family member submitting the intake form",
    "neighbour_button": "a neighbour or caregiver raising an alert",
    "whatsapp_inbound": "an inbound WhatsApp message from a registered contact",
    "phone_relay": "a call relayed by a caregiver and transcribed",
}


def receive_intake_signal(
    parent_id: str,
    channel: str,
    raw_text: str,
    reported_by: str,
) -> dict[str, Any]:
    """Record an intake signal that arrived from outside, and open a case.

    This is the only way an episode starts. Anbu Care does not monitor and
    cannot notice anything on its own — something outside it has to tell it.

    Args:
        parent_id: Whose episode this is.
        channel: Which channel the signal came in on. One of:
            er_desk_webhook, family_form, neighbour_button, whatsapp_inbound,
            phone_relay.
        raw_text: The signal exactly as it arrived, unedited.
        reported_by: Who or what raised it, e.g. "neighbour", "ER desk".

    Returns:
        The opened case and the recorded signal. Triage has not run yet — that
        is the next step, and it is a separate decision.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}

    if channel not in INTAKE_CHANNELS:
        return {
            "status": "error",
            "error": f"unknown intake channel '{channel}'",
            "known_channels": sorted(INTAKE_CHANNELS),
        }

    case = service.open_case(parent_id)
    receipt = service.append_receipt(
        case.case_id,
        kind="intake.signal_received",
        actor="intake_agent",
        payload={
            "parent_id": parent_id,
            "channel": channel,
            "channel_description": INTAKE_CHANNELS[channel],
            "raw_text": raw_text,
            "reported_by": reported_by,
            "label": SIGNAL_LABEL,
            "simulated": True,
        },
    )

    case.stage = "signal_received"
    service.update_case(case)

    service.publish_event(
        settings().topic_intake,
        {"case_id": case.case_id, "parent_id": parent_id, "channel": channel,
         "event": "intake.signal_received"},
    )

    return {
        "status": "ok",
        "case_id": case.case_id,
        "parent_id": parent_id,
        "channel": channel,
        "channel_description": INTAKE_CHANNELS[channel],
        "raw_text": raw_text,
        "reported_by": reported_by,
        "label": SIGNAL_LABEL,
        "receipt_id": receipt.receipt_id,
        "next_step": (
            "Hand this to the triage agent. The signal is what arrived; it is not "
            "an assessment, and nothing has been decided yet."
        ),
    }


def list_intake_channels() -> dict[str, Any]:
    """The channels an episode can start on, and what each represents.

    Returns:
        Every supported channel. All are labelled stubs in this build.
    """
    return {
        "status": "ok",
        "channels": INTAKE_CHANNELS,
        "label": SIGNAL_LABEL,
        "note": (
            "Anbu Care reacts to signals that arrive. It does not watch, sense, "
            "or detect. Every episode begins with one of these."
        ),
    }
