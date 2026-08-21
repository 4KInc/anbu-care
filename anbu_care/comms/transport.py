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


# A 2xx from the create call is not success on its own: Twilio can return one
# of these terminal states in the same body.
TERMINAL_FAILURE = {"failed", "undelivered", "canceled"}

# Meta's current Graph version for the /messages endpoint. Taken from the
# curl sample in the app's own API Setup panel, which is ahead of the
# get-started docs page (v23.0).
GRAPH_VERSION = "v25.0"


@dataclass(frozen=True)
class DeliveryResult:
    """What actually happened.

    `delivered` means the provider ACCEPTED the message for delivery — not that
    it reached the handset. Twilio returns `queued`/`accepted` on the create
    call; the handset-confirmed `delivered` status arrives later over a status
    callback we do not run. So `delivered=True` is the strongest claim we can
    honestly make, and every label spells out which one it is.
    """

    delivered: bool
    channel: str
    detail: str
    label: str = SANDBOX_LABEL
    provider_id: str | None = None
    http_status: int | None = None
    provider_status: str | None = None
    media_url_sent: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "channel": self.channel,
            "detail": self.detail,
            "label": self.label,
            "provider_id": self.provider_id,
            "http_status": self.http_status,
            "provider_status": self.provider_status,
            "media_url_sent": self.media_url_sent,
        }


def _env(name: str) -> str | None:
    """Secrets come from the environment. Never from a literal in this file."""
    value = os.getenv(name)
    return value.strip() if value else None


def _twilio_auth() -> tuple[str, str] | None:
    """Prefer an API key: it can be revoked without rotating the account token.

    Either way the URL carries the Account SID — only the HTTP Basic username
    changes. Returns None when nothing usable is configured.
    """
    account = _env("TWILIO_ACCOUNT_SID")
    if not account:
        return None
    key_sid, key_secret = _env("TWILIO_API_KEY_SID"), _env("TWILIO_API_KEY_SECRET")
    if key_sid and key_secret:
        return key_sid, key_secret
    token = _env("TWILIO_AUTH_TOKEN")
    return (account, token) if token else None


def _twilio(to_e164: str, body: str, media_url: str | None = None) -> DeliveryResult:
    sid = _env("TWILIO_ACCOUNT_SID")
    auth = _twilio_auth()
    sender = _env("TWILIO_WHATSAPP_FROM") or "whatsapp:+14155238886"

    if not auth:
        return DeliveryResult(
            delivered=False, channel="twilio",
            detail="No Twilio credentials are set (need TWILIO_ACCOUNT_SID plus either "
                   "TWILIO_API_KEY_SID/SECRET or TWILIO_AUTH_TOKEN); nothing was sent.",
        )

    import requests

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=auth,
            data={
                "From": sender if sender.startswith("whatsapp:") else f"whatsapp:{sender}",
                "To": f"whatsapp:{to_e164}",
                "Body": body,
                **({"MediaUrl": media_url} if media_url else {}),
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
    status = payload.get("status")
    if status in TERMINAL_FAILURE:
        return DeliveryResult(
            delivered=False, channel="twilio", provider_id=payload.get("sid"),
            http_status=response.status_code, provider_status=status,
            detail=(f"Twilio accepted the request but the message is {status}; "
                    f"it did not reach {to_e164}. "
                    f"{payload.get('error_message') or ''}".strip()),
        )
    return DeliveryResult(
        delivered=True, channel="twilio",
        provider_id=payload.get("sid"),
        http_status=response.status_code,
        provider_status=status,
        media_url_sent=bool(media_url),
        detail=(f"accepted by Twilio for delivery to {to_e164} (status: {status}). "
                "Handset confirmation would arrive over a status callback, which "
                "this demo does not run — so this is acceptance, not receipt."),
    )


def _meta(to_e164: str, body: str, media_url: str | None = None) -> DeliveryResult:
    token = _env("WHATSAPP_ACCESS_TOKEN")
    number_id = _env("WHATSAPP_PHONE_NUMBER_ID")
    if not (token and number_id):
        return DeliveryResult(
            delivered=False, channel="meta",
            detail="WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID are not set; nothing was sent.",
        )
    payload: dict = (
        {"messaging_product": "whatsapp", "to": to_e164, "type": "document",
         "document": {"link": media_url, "caption": body}}
        if media_url else
        {"messaging_product": "whatsapp", "to": to_e164,
         "type": "text", "text": {"body": body}}
    )
    return _meta_post(token, number_id, to_e164, payload, media=bool(media_url))


def _meta_post(token: str, number_id: str, to_e164: str, payload: dict,
               media: bool = False) -> DeliveryResult:
    import requests

    version = _env("WHATSAPP_API_VERSION") or GRAPH_VERSION
    try:
        response = requests.post(
            f"https://graph.facebook.com/{version}/{number_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return DeliveryResult(
            delivered=False, channel="meta",
            detail=f"transport error, nothing was sent: {type(exc).__name__}: {exc}"[:200],
        )

    if not response.ok:
        try:
            reason = (response.json().get("error") or {}).get("message", response.text)
        except Exception:  # noqa: BLE001
            reason = response.text
        return DeliveryResult(
            delivered=False, channel="meta", http_status=response.status_code,
            detail=f"Meta rejected the message, nothing was delivered: {str(reason)[:200]}",
        )

    data = response.json()
    messages = data.get("messages") or [{}]
    status = messages[0].get("message_status")
    return DeliveryResult(
        delivered=True, channel="meta",
        provider_id=messages[0].get("id"),
        http_status=response.status_code,
        provider_status=status,
        media_url_sent=media,
        detail=(f"accepted by Meta for delivery to {to_e164}"
                + (f" (status: {status})" if status else "")
                + ". Handset confirmation would arrive over a webhook, which this "
                  "demo does not run — so this is acceptance, not receipt."),
    )


def open_session(to_e164: str, template: str = "hello_world",
                 language: str = "en_US") -> DeliveryResult:
    """Send a pre-approved template to open the 24-hour freeform window.

    Operational handshake, NOT a family update: it carries no case content and
    deliberately does not go through the content gate, because there is nothing
    to classify. The gated path stays the only way real content leaves.
    """
    token = _env("WHATSAPP_ACCESS_TOKEN")
    number_id = _env("WHATSAPP_PHONE_NUMBER_ID")
    if not (token and number_id):
        return DeliveryResult(
            delivered=False, channel="meta",
            detail="WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID are not set; nothing was sent.",
        )
    return _meta_post(
        token, number_id, to_e164,
        {"messaging_product": "whatsapp", "to": to_e164, "type": "template",
         "template": {"name": template, "language": {"code": language}}},
    )


def _off(to_e164: str, body: str, media_url: str | None = None) -> DeliveryResult:
    return DeliveryResult(
        delivered=False, channel="off",
        detail=("No transport is configured, so no message left the platform. The gate "
                "decision above is real; the delivery is not."),
    )


TRANSPORTS = {"twilio": _twilio, "meta": _meta, "off": _off}


def send(to_e164: str, body: str, mode: str | None = None,
         media_url: str | None = None) -> DeliveryResult:
    """Carry an already-permitted message. Never called for a blocked one."""
    # `mode is not None`, not `mode or ...`: an explicit "" must mean off, not
    # silently inherit whatever the ambient environment happens to say.
    raw = mode if mode is not None else os.getenv("ANBU_WHATSAPP_MODE", "off")
    chosen = (raw or "off").strip().lower()
    # "sandbox" was the old name for the no-op path; keep it meaning no-op
    # rather than silently starting to send.
    if chosen in {"sandbox", "", "none", "false"}:
        chosen = "off"
    return TRANSPORTS.get(chosen, _off)(to_e164, body, media_url)
