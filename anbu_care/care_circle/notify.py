"""Telling the people around a parent where they were taken.

Outbound only. A neighbour, a sibling, a doctor the family listed — they are
notified parties, not integrated providers. Nothing here opens a channel back,
nothing waits for a reply, and no part of the system implies a clinician is
"in the loop". Saying otherwise would be claiming an integration that does not
exist.

Two decisions carry the guarantees.

The care circle is not a stored roster. It is the set of contacts holding
outbound_notify consent, computed at send time. A roster could disagree with
what people actually agreed to; a derived set cannot.

And the fan-out sends through the ordinary gated path, once per contact. There
is no second outbound route "because it's only logistics" — that route is how a
diagnosis eventually escapes. Every notice is classified, consent-checked and
receipted exactly like every other message.
"""

from __future__ import annotations

from datetime import datetime

from anbu_care import service
from anbu_care.comms import consent, localtime
from anbu_care.comms.policy import consent_ok
from anbu_care.schemas import FamilyContact, NotificationResult

TEMPLATE = "care_circle_notice"


def care_circle(parent_id: str) -> list[FamilyContact]:
    """Contacts who have agreed to be notified. Read live, every time."""
    profile = service.load_profile(parent_id)
    if profile is None:
        return []
    return [c for c in profile.family_contacts
            if consent_ok(c.consents, consent.OUTBOUND_NOTIFY)]


def notify(
    case_id: str,
    parent_id: str,
    hospital_name: str,
    timestamp: str,
    cashless_status: str,
    now: datetime | None = None,
    skip_numbers: set[str] | None = None,
    skip_names: set[str] | None = None,
) -> list[NotificationResult]:
    """Notify each consented contact, and report each one separately.

    Never returns an aggregate. Three contacts where one number is unreachable
    is two deliveries and one failure, and the receipt chain says so — because
    "the care circle was notified" would be false for the person who was not.
    """
    from anbu_care.tools import whatsapp_tools

    profile = service.load_profile(parent_id)
    if profile is None:
        return []

    first_name = profile.name.split()[0] if profile.name else "your parent"
    results: list[NotificationResult] = []

    already = skip_numbers or set()
    already_named = {n.strip().lower() for n in (skip_names or set())}

    for contact in profile.family_contacts:
        # Someone who has already had the full alert does not need the short
        # one as well. A person on both lists is one person, and telling them
        # twice about the same thing wastes the seconds that matter.
        #
        # Matched on the NAME where the caller gives one, because a person is
        # not a handset. Skipping by number silenced a neighbour who shared the
        # family's phone: she was filtered out as if she were the son, and the
        # care circle was never told anything.
        if contact.name.strip().lower() in already_named:
            continue
        if already and contact.whatsapp_e164 in already:
            continue
        # Checked here so a contact without consent appears in the results as
        # explicitly not consented, rather than silently missing from them.
        if not consent_ok(contact.consents, consent.OUTBOUND_NOTIFY):
            results.append(NotificationResult(
                contact_name=contact.name, to_e164=contact.whatsapp_e164,
                role=contact.role, consented=False, allowed=False, delivered=False,
                reason=(f"{contact.name} has not consented to "
                        f"'{consent.OUTBOUND_NOTIFY}' ({consent.describe(consent.OUTBOUND_NOTIFY)}). "
                        "Nothing was sent."),
            ))
            continue

        # Each contact reads the time on their own clock.
        when = (localtime.for_reader(now, contact.timezone,
                                     getattr(profile, "timezone", "Asia/Kolkata"),
                                     profile.city)
                if now is not None else timestamp)
        sent = whatsapp_tools.send_family_update(
            case_id=case_id,
            parent_id=parent_id,
            to_e164=contact.whatsapp_e164,
            template_name=TEMPLATE,
            template_params={
                "parent_name": first_name,
                "hospital_name": hospital_name,
                "timestamp": when,
                "cashless_status": cashless_status,
            },
            message_class="logistics",
            purpose_override=consent.OUTBOUND_NOTIFY,
        )
        results.append(NotificationResult(
            contact_name=contact.name, to_e164=contact.whatsapp_e164, role=contact.role,
            consented=True,
            allowed=bool(sent.get("allowed")),
            delivered=bool(sent.get("delivered")),
            reason=str(sent.get("reason", "")),
            receipt_id=sent.get("receipt_id"),
        ))

    return results
