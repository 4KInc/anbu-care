"""Sending the morning question, and labelling what comes back.

Two halves, and the asymmetry between them is the point.

SENDING is a fixed template with two blanks in it — her first name, and which
day this is. There is no free-text slot, no summary field, no place for a model
to put a sentence. The commonest way clinical content reaches a message in this
system is somebody filling a free-text parameter, and this template has none.

RECEIVING does nothing at all. Her reply goes down the inbound path that
already existed, untouched: same signature check, same transcription, same
store, same deterministic table, same escalation. This module contributes one
thing to it — a label saying which part of the story the reply belongs to —
and that label is computed from two stored facts (a window is open, a prompt
went out recently) rather than from anything she said.

That asymmetry is what keeps the honesty wall standing. The system is allowed
to ask. It is not allowed to interpret the answer. So the asking is specified
down to the character here, and the answering is handed straight to machinery
that was already proven not to interpret.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from anbu_care import service
from anbu_care.comms import consent as consent_purposes
from anbu_care.provenance.store import PARENT_SUBJECT
from anbu_care.recovery import window as win

logger = logging.getLogger(__name__)

TEMPLATE = "recovery_check_in"


def send_due(parent_id: str, now: datetime | None = None) -> dict | None:
    """Send today's check-in if one is owed. Otherwise do nothing and say so.

    Returns None when nothing was due — which is the ordinary case, twenty-three
    hours out of twenty-four. A missed day is never made up: if no tick ran this
    morning, this morning has no prompt, and the trace shows the gap rather than
    a burst of catch-up messages at midnight.
    """
    due = win.due_now(parent_id, now=now)
    if due is None:
        return None

    profile = service.load_profile(parent_id)
    if profile is None:
        return None
    first = profile.name.split()[0] if profile.name else "there"

    from anbu_care.tools import whatsapp_tools

    prompt_id = service.new_id("rp")
    sent = whatsapp_tools.send_parent_message(
        parent_id=parent_id,
        template_name=TEMPLATE,
        template_params={"parent_name": first, "day": str(due.day)},
        message_class="logistics",
        # Named explicitly. Nothing a family member agreed to can authorise a
        # message to her, so this purpose is passed rather than derived.
        purpose=consent_purposes.RECOVERY_CHECKINS,
        case_id=due.window.case_id,
    )

    win.claim_slot(parent_id, due, prompt_id, sent)

    delivered = bool(sent.get("delivered"))
    service.append_receipt(
        due.window.case_id or parent_id,
        kind="recovery.prompt_sent" if delivered else "recovery.prompt_not_delivered",
        actor="recovery",
        payload={
            "prompt_id": prompt_id,
            "window_id": due.window.window_id,
            "day": due.day,
            "of_days": due.window.days,
            "on": due.on.isoformat(),
            "to_language": getattr(profile, "language", "en"),
            "delivered": delivered,
            "gate_reason": sent.get("reason"),
            "rendering": sent.get("rendering"),
            "comms_receipt_id": sent.get("receipt_id"),
            "note": (
                "A recovery check-in question was sent. It asks how she is and "
                "whether she took today's medicines. It names no medicine, gives "
                "no advice, and carries no assessment."
                if delivered else
                "A recovery check-in question was composed and permitted, but no "
                "transport accepted it, so nothing reached her. Nothing is being "
                "claimed about a message that did not arrive."
            ),
        },
        **({} if due.window.case_id else {"subject": PARENT_SUBJECT}),
    )

    return {
        "prompt_id": prompt_id, "parent_id": parent_id,
        "window_id": due.window.window_id, "day": due.day,
        "delivered": delivered, "sent": sent,
    }


def phase_for(parent_id: str, now: datetime | None = None) -> tuple[str, str | None]:
    """Which phase an arriving check-in belongs to, and which prompt it answers.

    Derived from stored state and nothing else:

        an open recovery window exists  AND  a prompt went out within 24h
            -> ("recovery", prompt_id)
        otherwise
            -> ("acute", None)

    Her words are not read here, and could not be — this function never sees
    them. That matters, because a label derived from content would be an
    interpretation of content, and the moment the system starts sorting her
    sentences into categories it has begun assessing her.

    The label changes presentation. It does not change treatment: the same
    table sees the same text either way, and a recovery reply that trips a red
    flag gets the identical escalation an acute one would.
    """
    if win.open_window_for(parent_id) is None:
        return "acute", None
    prompt = win.recent_prompt(parent_id, now=now)
    if prompt is None:
        return "acute", None
    return "recovery", str(prompt.get("prompt_id") or "") or None


def day_of(parent_id: str, now: datetime | None = None) -> int | None:
    """Which day of the window we are on, for a message that wants to say so."""
    window = win.open_window_for(parent_id)
    if window is None:
        return None
    moment = (now or datetime.now(UTC)).astimezone(win._zone(window.timezone))
    day = window.day_number(moment.date())
    return day if day >= 1 else None


def handle_stop(parent_id: str, body: str) -> str | None:
    """She said STOP. End the check-ins and tell her they have ended.

    Returns the reply, or None if this was not an opt-out.

    Deliberately before anything is stored as a check-in: "STOP" is an
    instruction about the service, not a report about how she is, and filing it
    as a wellbeing entry would put a word she used to leave into a record of
    how she was feeling.
    """
    if not win.is_stop_word(body):
        return None
    closed = win.stop(parent_id, "stopped by request",
                      detail="She replied STOP. Check-ins ended on that message.")
    if not closed:
        return None
    return ("Understood. We have stopped the daily check-in messages. "
            "Your record is unchanged and you can still message here any time. "
            "If something is wrong, call 108.")
