"""WhatsApp communications agent tools.

Every send passes through the compliance gate first. The gate is code, not
instruction: an agent that is merely told not to send a lab value is not a
control, and the block must hold even if the model is confused or coaxed.

Production template approval takes roughly 10–15 business days and will not
clear inside the hackathon window, so sends go to the WhatsApp Business API
sandbox and are recorded either way.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from anbu_care import service
from anbu_care.comms.policy import TEMPLATES, consent_ok, gate_message, render_template
from anbu_care.config import settings
from anbu_care.schemas import MessageClass, OutboundMessage

PURPOSE_BY_CLASS = {
    MessageClass.LOGISTICS: "admission_alerts",
    MessageClass.STATUS: "status_updates",
    MessageClass.BILLING: "billing_updates",
}


def list_message_templates() -> dict[str, Any]:
    """List the pre-approved WhatsApp templates and what each may carry.

    Returns:
        Template names, their message class, body, and required parameters.
    """
    return {
        "status": "ok",
        "mode": settings().whatsapp_mode,
        "templates": {
            name: {
                "message_class": str(spec["message_class"].value),  # type: ignore[union-attr]
                "body": spec["body"],
                "params": spec["params"],
            }
            for name, spec in TEMPLATES.items()
        },
        "policy": (
            "Logistics, status, and billing may be sent. Clinical detail — diagnoses, "
            "lab values, prescription specifics — may not, and is blocked before send. "
            "Deliver clinical detail through the secure dashboard."
        ),
    }


def send_family_update(
    case_id: str,
    parent_id: str,
    to_e164: str,
    template_name: str,
    template_params: dict[str, str],
    message_class: str,
) -> dict[str, Any]:
    """Send a templated WhatsApp update to a family member.

    Args:
        case_id: The case this update belongs to.
        parent_id: The parent the update concerns.
        to_e164: Recipient's WhatsApp number in E.164 form.
        template_name: One of the pre-approved templates from list_message_templates.
        template_params: Values for that template's parameters.
        message_class: One of: logistics, status, billing. Never "clinical" —
            clinical content is blocked before send regardless of what is claimed.

    Returns:
        Whether the message was sent, and if not, exactly why it was blocked.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}

    try:
        declared = MessageClass(message_class)
    except ValueError:
        return {"status": "error", "error": f"unknown message_class '{message_class}'"}

    try:
        body = render_template(template_name, template_params)
    except (KeyError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}

    contact = next((c for c in profile.family_contacts if c.whatsapp_e164 == to_e164), None)
    if contact is None:
        return {"status": "error", "error": f"{to_e164} is not a registered family contact"}

    purpose = PURPOSE_BY_CLASS.get(declared)
    if purpose and not consent_ok(contact.consents, purpose):
        blocked = _record(
            case_id, to_e164, declared, template_name, body,
            allowed=False,
            reason=(
                f"Blocked: {contact.name} has not given purpose-specific consent for "
                f"'{purpose}'. DPDP requires per-purpose, timestamped opt-in; a blanket "
                "checkbox is not sufficient."
            ),
        )
        return blocked

    gate = gate_message(body, declared, template_name=template_name)
    if not gate.allowed:
        return _record(case_id, to_e164, gate.message_class, template_name, body,
                       allowed=False, reason=gate.reason)

    delivery = _deliver(to_e164, body, template_name)
    return _record(case_id, to_e164, gate.message_class, template_name, body,
                   allowed=True, reason=gate.reason, delivery=delivery)


def check_message_allowed(body: str, message_class: str) -> dict[str, Any]:
    """Check whether a message may go over WhatsApp, without sending it.

    Use this before drafting a message so you do not compose something that
    cannot be sent.

    Args:
        body: The message text you are considering.
        message_class: What you believe it is: logistics, status, or billing.

    Returns:
        Whether it would be allowed, and the reason.
    """
    try:
        declared = MessageClass(message_class)
    except ValueError:
        declared = None
    gate = gate_message(body, declared)
    return {
        "status": "ok",
        "allowed": gate.allowed,
        "detected_class": gate.message_class.value,
        "reason": gate.reason,
        "detected_clinical_signals": gate.detected_clinical,
    }


def _deliver(to_e164: str, body: str, template_name: str) -> dict[str, Any]:
    """Hand the message to WhatsApp.

    Sandbox mode records the send without calling Meta, which is what the
    hackathon window allows. Live mode is wired but unused until a verified
    number and approved templates exist.
    """
    cfg = settings()
    if cfg.whatsapp_mode != "live" or not (cfg.whatsapp_access_token and cfg.whatsapp_phone_number_id):
        return {
            "delivered": True,
            "channel": "sandbox",
            "note": "WhatsApp Business API sandbox — no message left the platform.",
        }

    import requests

    response = requests.post(
        f"https://graph.facebook.com/v21.0/{cfg.whatsapp_phone_number_id}/messages",
        headers={"Authorization": f"Bearer {cfg.whatsapp_access_token}"},
        json={
            "messaging_product": "whatsapp",
            "to": to_e164,
            "type": "text",
            "text": {"body": body},
        },
        timeout=20,
    )
    return {
        "delivered": response.ok,
        "channel": "live",
        "template": template_name,
        "http_status": response.status_code,
    }


def _record(
    case_id: str,
    to_e164: str,
    message_class: MessageClass,
    template_name: str,
    body: str,
    *,
    allowed: bool,
    reason: str,
    delivery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write every send attempt to the chain, including the blocked ones.

    A blocked send is evidence the boundary held, so it belongs in the audit
    trail just as much as a delivered one.
    """
    message = OutboundMessage(
        message_id=service.new_id("msg"),
        case_id=case_id,
        to_e164=to_e164,
        message_class=message_class,
        template_name=template_name,
        body=body,
        allowed=allowed,
        block_reason=None if allowed else reason,
        sent_at=datetime.now(timezone.utc) if allowed else None,
    )
    receipt = service.append_receipt(
        case_id,
        kind="comms.sent" if allowed else "comms.blocked",
        actor="whatsapp_agent",
        payload={**message.model_dump(mode="json"), "gate_reason": reason, "delivery": delivery},
    )
    return {
        "status": "ok" if allowed else "blocked",
        "allowed": allowed,
        "reason": reason,
        "message": message.model_dump(mode="json"),
        "delivery": delivery,
        "receipt_id": receipt.receipt_id,
    }
