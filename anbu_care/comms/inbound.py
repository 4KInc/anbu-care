"""Who is allowed to write into a parent's record from the outside.

The wellbeing webhook is an unauthenticated write path. It has to be — Twilio
cannot carry our bearer token — so the signature check is not a hardening
measure sitting in front of some other control. It IS the control. If it is
wrong, anyone who learns the URL can post sentences into a stranger's health
record.

Two separate questions, deliberately not conflated:

1. Did this request come from Twilio? Answered by the signature, over the exact
   bytes Twilio signed.
2. Is the sender someone this parent registered? Answered by looking `From` up
   against stored contacts and their consents. The signature says nothing about
   this — Twilio will faithfully relay a message from any number at all.

The second is why `source` is a provenance label and not an identity claim.
"self-reported" means "arrived from the number registered to the parent". It
does not prove who was holding the phone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any

from anbu_care import service
from anbu_care.comms import consent
from anbu_care.wellbeing.store import SELF_REPORTED

# The consent purpose a contact must hold before anything they send is stored.
#
# This was "status_updates" until it was noticed that "status_updates" is an
# OUTBOUND purpose: consenting to receive updates about a parent was silently
# making someone eligible to file reports about them. Old consents are NOT
# accepted as a fallback — dual-accepting the old flag would restore exactly
# the conflation being removed. Contacts must be re-registered.
WELLBEING_PURPOSE = consent.INBOUND_WELLBEING


def public_url(request: Any) -> str:
    """The URL Twilio actually signed, not the one this process received.

    Twilio signs the public HTTPS address it posted to. Behind Cloud Run the
    request arrives from a proxy, so request.url reports the internal scheme
    and the signature never matches — the check fails closed, which is the safe
    direction, but it fails on every legitimate message too.

    The forwarded headers carry what the caller saw. Only the scheme and host
    are taken from them: the path comes from the request itself, so a spoofed
    header cannot redirect verification at a different endpoint.
    """
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        return str(request.url)
    # Twilio signs the first proto when a chain of proxies appends several.
    scheme = scheme.split(",")[0].strip()
    host = host.split(",")[0].strip()
    url = f"{scheme}://{host}{request.url.path}"
    return f"{url}?{request.url.query}" if request.url.query else url


class SignatureRejected(Exception):
    """The request did not come from Twilio, or did not arrive intact."""


def verify_twilio_signature(url: str, form: list[tuple[str, str]], header: str | None) -> None:
    """Validate X-Twilio-Signature, or raise.

    Twilio signs the full URL concatenated with each POST parameter's name and
    value, sorted by name. The signature must be checked against the parameters
    exactly as they arrived: rebuilding them from a reparsed or re-serialised
    copy is the classic way this check silently starts passing everything.

    Missing, malformed and well-formed-but-wrong are all the same answer. A
    caller learns only that it was refused.
    """
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not token:
        raise SignatureRejected("TWILIO_AUTH_TOKEN is not set; cannot verify, so nothing is trusted.")
    if not header:
        raise SignatureRejected("no X-Twilio-Signature on the request.")

    payload = url + "".join(f"{k}{v}" for k, v in sorted(form, key=lambda kv: kv[0]))
    expected = base64.b64encode(
        hmac.new(token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    ).decode("utf-8")

    # compare_digest on the base64 text: a malformed signature fails here the
    # same way a wrong one does, without a decode step that could raise and
    # turn a rejection into a 500.
    if not hmac.compare_digest(expected, header):
        raise SignatureRejected("signature did not match the request body.")


@dataclass(frozen=True)
class Sender:
    parent_id: str
    source: str


def resolve_sender(from_number: str) -> Sender | None:
    """Map an inbound number to a parent and a source label, or refuse.

    Returns None when the number belongs to nobody, or to somebody who has not
    consented. That is a refusal to store, not an error: under DPDP a person
    who has not given purpose-specific consent has not agreed to have their
    messages kept, and the honest response is to keep nothing.

    Consent is read from the live profile rather than from the index, so
    withdrawing it takes effect on the next message.
    """
    owner = service.lookup_whatsapp_number(from_number)
    if not owner:
        return None

    profile = service.load_profile(owner.get("parent_id", ""))
    if profile is None:
        return None

    contact_name = owner.get("contact_name")
    if contact_name is None:
        return Sender(parent_id=profile.parent_id, source=SELF_REPORTED)

    contact = next((c for c in profile.family_contacts if c.name == contact_name), None)
    if contact is None or WELLBEING_PURPOSE not in contact.consents:
        # Registered once, but not consented now. Still nothing stored.
        return None
    return Sender(parent_id=profile.parent_id, source=f"caregiver:{contact.name}")
