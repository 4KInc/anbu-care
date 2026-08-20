"""WhatsApp delivery transports.

This module only ever *carries* a message. It never decides whether one may be
sent — `comms/policy.py` does that, and `whatsapp_tools.send_family_update`
calls the gate first and returns before reaching a transport if the gate said
no. Nothing here inspects content, and nothing here can override a block.

Two real transports and an explicit off switch:

- ``twilio``  — the Twilio WhatsApp sandbox. Real messages reach a phone that has
  opted in by sending "join <code>" to the shared sandbox number. No Meta
  business verification and no template approval, which is what makes it usable
  inside a hackathon window. Reach is limited to numbers that joined *this*
  sandbox — it is not general production reach and must never be described as
  such.
- ``meta``    — the Meta Cloud API, for a verified sender with approved templates.
- ``off``     — no transport. Records the attempt and says plainly that nothing
  left the platform.

The honesty rule that matters: ``delivered`` is True only when a transport
actually accepted the message. An off switch, a missing credential, a timeout or
a rejected request all report ``delivered: False`` with a reason. Claiming a send
that did not happen is the same failure as claiming an ingest that did not
happen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

SANDBOX_LABEL = (
    "WhatsApp sandbox — real delivery to opted-in test numbers. Production reach "
    "requires Meta business verification and template approval (~10–15 business days)."
)


@dataclass(frozen=True)
class DeliveryResult:
    """What actually happened. `delivered` is never optimistic."""

    delivered: bool
    channel: str
    detail: str
    label: str = SANDBOX_LABEL
    provider_id: str | None = None
    http_status: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "channel": self.channel,
            "detail": self.detail,
            "label": self.label,
            "provider_id": self.provider_id,
            "http_status": self.http_status,
        }


def _env(name: str) -> str | None:
    """Secrets come from the environment. Never from a literal in this file."""
    value = os.getenv(name)
    return value.strip() if value else None


def _twilio(to_e164: str, body: str) -> DeliveryResult:
    sid = _env("TWILIO_ACCOUNT_SID")
    token = _env("TWILIO_AUTH_TOKEN")
    sender = _env("TWILIO_WHATSAPP_FROM") or "whatsapp:+14155238886"

    if not (sid and token):
        return DeliveryResult(
            delivered=False, channel="twilio",
            detail="TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not set; nothing was sent.",
        )

    import requests

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={
                "From": sender if sender.startswith("whatsapp:") else f"whatsapp:{sender}",
                "To": f"whatsapp:{to_e164}",
                "Body": body,
            },
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "not delivered"
        return DeliveryResult(
            delivered=False, channel="twilio",
            detail=f"transport error, nothing was sent: {type(exc).__name__}: {exc}"[:200],
        )

    if not response.ok:
        # Twilio returns a useful message; surface it rather than a bare code.
        try:
            reason = response.json().get("message", response.text)[:200]
        except Exception:  # noqa: BLE001
            reason = response.text[:200]
        return DeliveryResult(
            delivered=False, channel="twilio", http_status=response.status_code,
            detail=f"Twilio rejected the message, nothing was delivered: {reason}",
        )

    payload = response.json()
    return DeliveryResult(
        delivered=True, channel="twilio",
        provider_id=payload.get("sid"),
        http_status=response.status_code,
        detail=f"accepted by Twilio for delivery to {to_e164} (status {payload.get('status')})",
    )


def _meta(to_e164: str, body: str) -> DeliveryResult:
    token = _env("WHATSAPP_ACCESS_TOKEN")
    number_id = _env("WHATSAPP_PHONE_NUMBER_ID")
    if not (token and number_id):
        return DeliveryResult(
            delivered=False, channel="meta",
            detail="WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID are not set; nothing was sent.",
        )

    import requests

    try:
        response = requests.post(
            f"https://graph.facebook.com/v21.0/{number_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"messaging_product": "whatsapp", "to": to_e164,
                  "type": "text", "text": {"body": body}},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return DeliveryResult(
            delivered=False, channel="meta",
            detail=f"transport error, nothing was sent: {type(exc).__name__}: {exc}"[:200],
        )

    return DeliveryResult(
        delivered=bool(response.ok), channel="meta", http_status=response.status_code,
        detail=("accepted by Meta for delivery" if response.ok
                else f"Meta rejected the message, nothing was delivered: {response.text[:200]}"),
    )


def _off(to_e164: str, body: str) -> DeliveryResult:
    return DeliveryResult(
        delivered=False, channel="off",
        detail=("No transport is configured, so no message left the platform. The gate "
                "decision above is real; the delivery is not."),
    )


TRANSPORTS = {"twilio": _twilio, "meta": _meta, "off": _off}


def send(to_e164: str, body: str, mode: str | None = None) -> DeliveryResult:
    """Carry an already-permitted message. Never called for a blocked one."""
    chosen = (mode or os.getenv("ANBU_WHATSAPP_MODE", "off")).strip().lower()
    # "sandbox" was the old name for the no-op path; keep it meaning no-op
    # rather than silently starting to send.
    if chosen in {"sandbox", "", "none", "false"}:
        chosen = "off"
    return TRANSPORTS.get(chosen, _off)(to_e164, body)
