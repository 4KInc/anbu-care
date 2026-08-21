"""Ringing a phone, because a message at 2am may go unread until morning.

A WhatsApp notification arriving while someone sleeps is a notification that
did not happen. This places an actual call, which is the only thing in the
system that reaches through a silent phone.

What it does NOT do is call 108. Twilio's emergency calling is supported in a
fixed list of countries that does not include India, and outbound calls between
Twilio numbers and emergency services are not permitted outside their US and
Canada E911 product. The path does not exist.

That constraint happens to point at the better design anyway. An ambulance
dispatcher opens with "is she conscious, is she breathing, what is the exact
address" and a recording cannot answer any of it while occupying a line someone
else needs. A neighbour four hundred metres away can be at the door before an
ambulance reaches the street. Waking a human is not the consolation prize for
failing to reach an emergency service; for a fall, or for someone who cannot
get to the door, it is the faster intervention.

The honesty rules are the transport's rules. A placed call is reported as
placed, never as answered — Twilio returns "queued" and whether anyone picked
up is not known until later. Claiming somebody answered when the phone rang
into an empty room is the one lie here that could stop a family member making
the call themselves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from xml.sax.saxutils import escape

# The number a call comes FROM. Voice needs a purchased Twilio number: the
# WhatsApp sandbox does not carry calls.
CALLER_ID_ENV = "TWILIO_VOICE_FROM"


@dataclass(frozen=True)
class CallResult:
    """What happened when the call was placed. Never what happened on it."""

    placed: bool
    to_e164: str
    detail: str
    provider_id: str | None = None
    provider_status: str | None = None
    http_status: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "placed": self.placed,
            "to_e164": self.to_e164,
            "detail": self.detail,
            "provider_id": self.provider_id,
            "provider_status": self.provider_status,
            "http_status": self.http_status,
        }


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value else None


def twiml_for(spoken: str) -> str:
    """Say it twice, slowly. Someone woken at 2am misses the first sentence."""
    safe = escape(spoken)
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Pause length="1"/><Say voice="alice">{safe}</Say>'
        f'<Pause length="1"/><Say voice="alice">{safe}</Say>'
        "</Response>"
    )


def place_call(to_e164: str, spoken: str, mode: str | None = None) -> CallResult:
    """Ring a number and speak a line, or say plainly that it did not.

    Off by default. A voice call costs money and reaches a real person's phone
    at an hour they did not choose, so an unconfigured deployment must not be
    able to make one by accident.
    """
    chosen = (mode if mode is not None else os.getenv("ANBU_VOICE_MODE", "off")).strip().lower()
    if chosen in {"", "off", "none", "false"}:
        return CallResult(
            placed=False, to_e164=to_e164,
            detail=("No voice transport is configured, so no call was placed. "
                    "The message above is real; the call is not."),
        )

    account = _env("TWILIO_ACCOUNT_SID")
    key_sid, key_secret = _env("TWILIO_API_KEY_SID"), _env("TWILIO_API_KEY_SECRET")
    auth = (key_sid, key_secret) if key_sid and key_secret else (account, _env("TWILIO_AUTH_TOKEN"))
    caller_id = _env(CALLER_ID_ENV)

    if not account or not auth[1]:
        return CallResult(placed=False, to_e164=to_e164,
                          detail="Twilio credentials are not set; no call was placed.")
    if not caller_id:
        return CallResult(
            placed=False, to_e164=to_e164,
            detail=(f"{CALLER_ID_ENV} is not set. Voice needs a purchased Twilio number; "
                    "the WhatsApp sandbox cannot place calls. No call was placed."),
        )

    import requests

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account}/Calls.json",
            auth=auth,
            data={"From": caller_id, "To": to_e164, "Twiml": twiml_for(spoken)},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 - any failure means no call
        return CallResult(placed=False, to_e164=to_e164,
                          detail=f"call failed, nothing was placed: {type(exc).__name__}: {exc}"[:200])

    if not response.ok:
        try:
            reason = response.json().get("message", response.text)[:200]
        except Exception:  # noqa: BLE001
            reason = response.text[:200]
        return CallResult(placed=False, to_e164=to_e164, http_status=response.status_code,
                          detail=f"Twilio refused the call, nothing was placed: {reason}")

    payload = response.json()
    return CallResult(
        placed=True, to_e164=to_e164,
        provider_id=payload.get("sid"),
        provider_status=payload.get("status"),
        http_status=response.status_code,
        detail=(f"call placed to {to_e164} (status: {payload.get('status')}). "
                "Whether anyone answered is not known yet."),
    )
