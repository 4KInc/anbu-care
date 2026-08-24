"""WhatsApp communications agent tools.

Every send passes through the compliance gate first. The gate is code, not
instruction: an agent that is merely told not to send a lab value is not a
control, and the block must hold even if the model is confused or coaxed.

Production template approval takes roughly 10–15 business days and will not
clear inside the hackathon window, so sends go to the WhatsApp Business API
sandbox and are recorded either way.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
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
    attach_claim_summary: bool = False,
    # A link to draw as a QR and attach. For the handoff link, where the person
    # holding the message is not the person who needs it: the neighbour is
    # standing next to the doctor, and a picture he can scan off her screen is
    # how it gets to him.
    attach_qr_of: str = "",
    purpose_override: str = "",
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
        purpose_override: Demand a different consent purpose than the message
            class implies. A care-circle notice is logistics, but requires
            outbound_notify rather than admission_alerts, because being told
            once as a listed contact is not the same agreement as receiving the
            family's case feed. This changes ONLY which consent is required. It
            does not touch the content gate, and it is not a bypass: a message
            carrying clinical detail is blocked with an override set exactly as
            it is without one.
        attach_claim_summary: Request that the case's claim summary PDF ride
            along. This is a request, not an instruction: the document is built
            from adjudicator output by code, its text is classified like any
            other message, and it is dropped if it cannot be built, classified
            clean, and stored. You cannot choose which document is attached and
            you cannot attach a clinical one.

    Returns:
        Whether the message was sent, and if not, exactly why it was blocked.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}

    contact = next((c for c in profile.family_contacts if c.whatsapp_e164 == to_e164), None)
    if contact is None:
        return {"status": "error", "error": f"{to_e164} is not a registered family contact"}

    try:
        declared = MessageClass(message_class)
    except ValueError:
        return {"status": "error", "error": f"unknown message_class '{message_class}'"}

    return _send(
        case_id=case_id, parent_id=parent_id, to_e164=to_e164,
        recipient_name=contact.name,
        consents=contact.consents,
        purpose=purpose_override or PURPOSE_BY_CLASS.get(declared),
        language=getattr(contact, "language", "en"),
        template_name=template_name, template_params=template_params,
        declared=declared, attach_claim_summary=attach_claim_summary,
        attach_qr_of=attach_qr_of,
    )


def send_parent_message(
    parent_id: str,
    template_name: str,
    template_params: dict[str, Any],
    message_class: str,
    purpose: str,
    case_id: str = "",
) -> dict[str, Any]:
    """Send a templated message to the PARENT herself.

    The first outbound direction in this system that points at her. Everything
    else addresses a family member; she was only ever answered, never
    contacted.

    It is not a second route, and that distinction is the whole reason this
    function is four lines long. The content gate, the template set, the
    transport and the receipt are the ones every other message uses — a
    parallel send path "because it's only a check-in" is exactly how a
    diagnosis eventually escapes. What differs is one thing: WHOSE agreement is
    read. A family contact's consents live on their contact record and cover
    their own traffic. Hers live on her profile, in `contact_consents`, because
    she is the data principal and nobody else can agree on her behalf.

    Args:
        parent_id: Whose record, and who is being written to.
        template_name: One of the pre-approved templates.
        template_params: Values for that template's parameters.
        message_class: logistics, status, or billing. Never clinical.
        purpose: The parent-held consent purpose required. Named explicitly
            rather than derived from the class, because the class-to-purpose
            map is a table of FAMILY purposes and reaching into it here would
            let a family member's agreement authorise a message to her.
        case_id: The case this belongs to, so it lands on that chain and
            renders on the trace.

    Returns:
        Whether it was sent, and if not, exactly why not.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}

    to_e164 = (profile.whatsapp_e164 or "").strip()
    if not to_e164:
        # No number, no message, and no pretending. A recovery check-in that
        # silently goes nowhere reads on the trace as care that happened.
        return {"status": "error",
                "error": f"{profile.name or parent_id} has no WhatsApp number on file, "
                         "so nothing can be sent to her."}

    try:
        declared = MessageClass(message_class)
    except ValueError:
        return {"status": "error", "error": f"unknown message_class '{message_class}'"}

    first = profile.name.split()[0] if profile.name else "your parent"
    return _send(
        case_id=case_id, parent_id=parent_id, to_e164=to_e164,
        recipient_name=first,
        # Read live off the profile, every send. A withdrawal takes effect on
        # the very next message rather than whenever something was last cached.
        consents=profile.contact_consents,
        purpose=purpose,
        language=getattr(profile, "language", "en"),
        template_name=template_name, template_params=template_params,
        declared=declared, attach_claim_summary=False,
    )


def _send(
    *,
    case_id: str,
    parent_id: str,
    to_e164: str,
    recipient_name: str,
    consents: dict,
    purpose: str | None,
    language: str,
    template_name: str,
    template_params: dict[str, Any],
    declared: MessageClass,
    attach_claim_summary: bool,
    attach_qr_of: str = "",
) -> dict[str, Any]:
    """Consent, gate, render, deliver, record. The only way content leaves.

    The ordering is load-bearing and is the reason translation lives HERE
    rather than inside a template or a transport:

        consent -> GATE (on the English record) -> render -> deliver

    `CLINICAL_PATTERNS` is a list of English regexes. It would not recognise a
    lab value written in Tamil script, so a design that translated first and
    gated second would have a hole in it the width of the alphabet. Gating the
    source and rendering afterwards means the gate has already ruled on the
    exact words being rendered, and the rendering is constrained to say the
    same thing.
    """
    try:
        body = render_template(template_name, template_params,
                               case_id=case_id, parent_id=parent_id)
    except (KeyError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}

    if purpose and not consent_ok(consents, purpose):
        return _record(
            case_id, to_e164, declared, template_name, body,
            allowed=False,
            reason=(
                f"Blocked: {recipient_name} has not given purpose-specific consent for "
                f"'{purpose}'. DPDP requires per-purpose, timestamped opt-in; a blanket "
                "checkbox is not sufficient."
            ),
        )

    gate = gate_message(body, declared, template_name=template_name)
    if not gate.allowed:
        return _record(case_id, to_e164, gate.message_class, template_name, body,
                       allowed=False, reason=gate.reason)

    rendering = _render_for_reader(body, language, template_name)

    attachment = _attach(case_id) if attach_claim_summary else None
    if attachment is None and attach_qr_of:
        attachment = _attach_qr(attach_qr_of)
    media_url = attachment.pop("url", None) if attachment else None

    delivery = _deliver(to_e164, rendering.text, template_name, media_url=media_url)
    return _record(case_id, to_e164, gate.message_class, template_name, rendering.text,
                   allowed=True, reason=gate.reason, delivery=delivery,
                   attachment=attachment, rendering=rendering)


# What each template is a rendering OF, named the way it should appear in the
# provenance note. A template with no entry names its own kind rather than
# guessing — every one of these points at something that was actually recorded.
SOURCE_REF = {
    "bill_recorded": "bill",
    "clinician_note_left": "record",
    "diagnostic_options_ready": "record",
    "diagnostic_options_none": "record",
    "bill_already_recorded": "bill",
    "bill_already_recorded_retake": "bill",
    "bill_unreadable": "bill",
    "billing_summary": "bill",
    "document_recorded": "document",
    "document_recorded_withheld": "document",
    "document_already_recorded": "document",
    "document_unreadable": "document",
    "admission_alert": "admission record",
    "status_update": "status update",
    "claim_stage": "claim record",
    "doctor_assigned": "case record",
    "urgent_family_alert": "check-in",
    "urgent_family_alert_withheld": "check-in",
    "voice_note_unclear": "voice note",
    "care_circle_unclear": "voice note",
    "care_circle_notice": "admission record",
    "clinician_handoff_link": "case record",
    "recovery_check_in": "check-in question",
    "recovery_escalation_family": "recovery check-in",
    "recovery_escalation_family_withheld": "recovery check-in",
}


# Templates that render INSIDE the Twilio webhook, where the line is held open
# and Twilio hangs up at roughly fifteen seconds. Every one of them is an alert
# telling somebody to call now, so they get the short translation ceiling: a
# late alert is worse than an English one, and a webhook that times out makes
# Twilio retry and send the whole thing twice.
#
# Everything not on this list runs after a response or from a tick, with nobody
# waiting, and can afford to wait for the Tamil.
URGENT_TEMPLATES = {
    "urgent_family_alert", "urgent_family_alert_withheld",
    "recovery_escalation_family", "recovery_escalation_family_withheld",
    "voice_note_unclear", "care_circle_unclear", "care_circle_notice",
}


def _render_for_reader(body: str, language: str, template_name: str):
    """Put the gated message into the reader's language, or leave it alone.

    Never raises into a send. `NoSourceRecord` cannot fire here — the body came
    out of a pre-approved template, which is itself the record — but if it ever
    did, the English record is what goes out.
    """
    from anbu_care.comms import translate

    source_ref = SOURCE_REF.get(template_name, "record")
    timeout = (translate.URGENT_TIMEOUT_SECONDS if template_name in URGENT_TEMPLATES
               else translate.UNHURRIED_TIMEOUT_SECONDS)
    try:
        return translate.render(body, language=language, source_ref=source_ref,
                                timeout_seconds=timeout)
    except translate.NoSourceRecord:
        return translate._passthrough(body, source_ref,
                                      detail="no recorded source named; English was sent")


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


def _attach_qr(payload: str) -> dict[str, Any]:
    """A QR of this link, stored and signed so the provider can fetch it.

    Never raises into a send. A QR that could not be built or stored degrades
    to "no attachment" and the message still goes with the link in it — the
    link is the thing that works, and the picture is what makes it easy.
    """
    from anbu_care.comms import artifacts, storage

    try:
        png = artifacts.qr_png(payload)
    except Exception as exc:  # noqa: BLE001 - no QR is an outcome, not a failure
        return {"attached": False, "reason": f"the QR could not be drawn: {type(exc).__name__}"}

    digest = hashlib.sha256(png).hexdigest()
    stored = storage.store(f"qr/{digest[:16]}.png", png, content_type="image/png")
    if not stored.stored:
        return {"attached": False, "reason": stored.detail}

    return {
        "attached": True,
        "url": stored.url,
        "kind": "handoff_qr",
        "filename": f"qr/{digest[:16]}.png",
        # The hash of the IMAGE, not of the link it encodes. A receipt that
        # carried the URL would put a live credential on a chain anyone can
        # read.
        "sha256": digest,
        "bytes": len(png),
    }


def _attach(case_id: str) -> dict[str, Any]:
    """Build, classify and store the claim summary. Never raise into the send.

    A failure here must degrade to "no attachment", not to a failed message and
    not to a claim that something was attached. The refusal reason is kept and
    written to the chain, because "the gate refused the document" is evidence
    worth having.
    """
    from anbu_care.comms import artifacts, storage

    adjudication = service.latest_adjudication(case_id)
    if adjudication is None:
        return {"attached": False, "reason": "no adjudication on this case yet; nothing to attach."}

    try:
        artifact = artifacts.build("claim_summary", adjudication)
    except artifacts.ArtifactRefused as exc:
        return {"attached": False, "reason": str(exc)}

    stored = storage.store(artifact.filename, artifact.pdf)
    if not stored.stored:
        return {"attached": False, "reason": stored.detail, **artifact.as_receipt_payload()}

    return {
        "attached": True,
        "url": stored.url,
        "expires_in_seconds": stored.expires_in_seconds,
        **artifact.as_receipt_payload(),
    }


def _deliver(to_e164: str, body: str, template_name: str,
             media_url: str | None = None) -> dict[str, Any]:
    """Carry a message the gate has already permitted.

    This is only ever reached after `gate_message` returned allowed. A blocked
    message returns earlier and never touches a transport — that ordering is the
    point, and a test asserts the transport is not called for a blocked send.

    The result reports what actually happened. With no transport configured it
    says so rather than claiming a delivery, for the same reason the ingest path
    reports a stored count rather than the agent's word for it.
    """
    from anbu_care.comms.transport import send

    result = send(to_e164, body, media_url=media_url).as_dict()
    result["template"] = template_name
    return result


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
    attachment: dict[str, Any] | None = None,
    rendering: Any = None,
) -> dict[str, Any]:
    """Write every send attempt to the chain, including the blocked ones.

    A blocked send is evidence the boundary held, so it belongs in the audit
    trail just as much as a delivered one.

    Permission and delivery are separate facts and are recorded separately. The
    gate allowing a message does not mean a message arrived: with no transport
    configured, or a transport that failed, nothing was sent and `sent_at` stays
    empty. Stamping a send time on a message that never left would be the same
    lie as an agent reporting an ingest it never made.
    """
    was_delivered = bool(delivery and delivery.get("delivered"))
    message = OutboundMessage(
        message_id=service.new_id("msg"),
        case_id=case_id,
        to_e164=to_e164,
        message_class=message_class,
        template_name=template_name,
        body=body,
        allowed=allowed,
        block_reason=None if allowed else reason,
        sent_at=datetime.now(UTC) if was_delivered else None,
    )
    receipt = service.append_receipt(
        case_id,
        # Three outcomes, named honestly: blocked by the gate, permitted and
        # delivered, or permitted but not delivered.
        kind=("comms.blocked" if not allowed
              else "comms.sent" if was_delivered
              else "comms.not_delivered"),
        actor="whatsapp_agent",
        # The attachment goes in the chain too, refusals included: "the gate
        # would not release the document" is evidence worth keeping. The signed
        # URL is deliberately absent — it expires, and a receipt that records a
        # dead link reads as proof of something it cannot support. The sha256
        # is what proves which bytes were sent.
        # The rendering goes on too. `source_sha256` is the hash of the English
        # the gate actually ruled on, so the chain can prove the Tamil was
        # derived from a recorded message rather than composed — the same
        # move the wellbeing receipt makes, where the hash proves the words
        # without carrying them.
        payload={**message.model_dump(mode="json"), "gate_reason": reason,
                 "delivery": delivery, "delivered": was_delivered,
                 "attachment": attachment,
                 **(rendering.as_receipt_payload() if rendering is not None else {})},
    )
    return {
        "status": ("blocked" if not allowed else "ok" if was_delivered else "not_delivered"),
        "allowed": allowed,
        "delivered": was_delivered,
        "reason": reason,
        "message": message.model_dump(mode="json"),
        "delivery": delivery,
        "attachment": attachment,
        "receipt_id": receipt.receipt_id,
        **({"rendering": rendering.as_receipt_payload()} if rendering is not None else {}),
    }
