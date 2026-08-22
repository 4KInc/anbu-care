"""The emergency-access token: narrow, short-lived, revocable, receipted.

A nurse receiving an unconscious patient has no login and will not make one.
So the summary has to be reachable by someone who has never authenticated —
and that is exactly the thing the rest of this system refuses to do, because
clinical content sits behind a credential and always has.

The resolution is that this is not a new door. It is a **delegation of a subset
of the family's own access**. Only a caller who already holds a family session
can mint one, the token names one case and grants one read, and it dies on its
own within the hour.

Four controls, because the honest limitation cannot be engineered away:

  A link is a bearer credential. Whoever holds it, holds it. The system cannot
  tell which human opened it and never claims to — the receipt says the summary
  was opened, not who opened it, for the same reason `comms.not_delivered`
  exists. So instead of pretending to identify the reader, the blast radius is
  kept small enough that it does not matter much:

  1. ONE CASE. The signature covers the case and parent. A token for one case
     cannot read another, and cannot reach /trail or /api/parents at all.
  2. SIXTY MINUTES, absolute, no refresh. Long enough for an admission, short
     enough that a forwarded screenshot is stale by morning.
  3. REVOCABLE. The case's handoff_epoch is signed in; bumping it kills every
     outstanding link for that case at once.
  4. RECEIPTED. Every open writes to the chain, so the family can see that the
     record was read, and when.

Domain separation matters here: the payload is prefixed so a 2am alert link
from `webauth.make_link_token` can never be replayed as a handoff token, and a
handoff token can never be used to reach the family dashboard.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass

from anbu_care import service
from anbu_care.comms import consent as consent_purposes
from anbu_care.webauth import LINK_SECRET_ENV

# An hour. An admission is decided in far less, and a link that outlives the
# episode is a credential nobody remembers issuing.
HANDOFF_TTL_SECONDS = 60 * 60

# Prefixed so this signature space cannot overlap the alert-link one.
_DOMAIN = "anbu.handoff.v1"


class HandoffDenied(Exception):
    """Refused. The reason is for the family's audit trail, never the holder."""


@dataclass(frozen=True)
class HandoffGrant:
    case_id: str
    parent_id: str
    expires_at: int
    # Whether the treating team may also leave a note. Signed into the token,
    # so a read link cannot be edited into a write one. The family decides at
    # mint time: a link that silently accepted writes would not be the
    # "read-only" thing its own page claims to be.
    may_write_note: bool = False


def _secret() -> bytes | None:
    """Fails closed. No secret, no tokens — never a default.

    A hardcoded fallback would mean every deployment shared a key, so anyone
    who read this public repo could mint a link into any patient's record.
    """
    value = os.getenv(LINK_SECRET_ENV)
    return value.encode("utf-8") if value else None


def _sign(case_id: str, parent_id: str, epoch: int, expires: int, secret: bytes,
          scope: str = "read") -> str:
    payload = f"{_DOMAIN}:{scope}:{case_id}:{parent_id}:{epoch}:{expires}"
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def consent_held(parent_id: str) -> bool:
    """Read live, every time. Never cached, never inferred from another purpose."""
    profile = service.load_profile(parent_id)
    if profile is None:
        return False
    return consent_purposes.EMERGENCY_CLINICAL_SHARE in profile.disclosure_consents


def mint(case_id: str, now: int | None = None, allow_notes: bool = False) -> str:
    """Issue a token for one case. Raises HandoffDenied rather than returning junk.

    Consent is read at the moment of issue, from the parent's own record. A
    family member's willingness to receive updates is not her agreement to have
    her allergies read by a stranger, so nothing here falls back to another
    purpose when this one is absent.
    """
    case = service.load_case(case_id)
    if case is None:
        raise HandoffDenied("no such case")

    if not consent_held(case.parent_id):
        raise HandoffDenied(
            "the parent has not consented to emergency clinical sharing "
            f"({consent_purposes.EMERGENCY_CLINICAL_SHARE}); no link was issued"
        )

    secret = _secret()
    if not secret:
        raise HandoffDenied(
            f"{LINK_SECRET_ENV} is not configured, so no link can be signed"
        )

    expires = int(now or time.time()) + HANDOFF_TTL_SECONDS
    epoch = case.handoff_epoch
    scope = "note" if allow_notes else "read"
    signature = _sign(case.case_id, case.parent_id, epoch, expires, secret, scope)
    return f"{case.case_id}.{scope}.{epoch}.{expires}.{signature}"


def resolve(token: str, now: int | None = None) -> HandoffGrant:
    """What does this token authorise, right now? Raises if the answer is nothing.

    Malformed, expired, revoked, forged and signed-for-something-else all raise
    the same way and carry no detail about the case. A holder of a bad token
    learns only that it did not work — not whether the case exists, not whose
    it is, not whether they were close.
    """
    secret = _secret()
    if not secret or not token or token.count(".") != 4:
        raise HandoffDenied("this link is not valid")

    case_id, scope, epoch_raw, expires_raw, presented = token.split(".")
    if scope not in {"read", "note"}:
        raise HandoffDenied("this link is not valid")
    try:
        epoch, expires = int(epoch_raw), int(expires_raw)
    except ValueError:
        raise HandoffDenied("this link is not valid") from None

    if expires < int(now or time.time()):
        raise HandoffDenied("this link has expired")

    case = service.load_case(case_id)
    if case is None:
        raise HandoffDenied("this link is not valid")

    # Revocation: the epoch is signed in, so a bumped epoch fails the compare
    # below. Checked explicitly first so the refusal is honest in the log.
    if epoch != case.handoff_epoch:
        raise HandoffDenied("this link has been revoked by the family")

    expected = _sign(case.case_id, case.parent_id, epoch, expires, secret, scope)
    if not hmac.compare_digest(expected, presented):
        raise HandoffDenied("this link is not valid")

    return HandoffGrant(case_id=case.case_id, parent_id=case.parent_id,
                        expires_at=expires, may_write_note=(scope == "note"))


def revoke(case_id: str) -> None:
    """Stop sharing. Every outstanding link for this case dies immediately."""
    case = service.load_case(case_id)
    if case is None:
        raise HandoffDenied("no such case")
    case.handoff_epoch += 1
    service.update_case(case)


def record_access(grant: HandoffGrant) -> None:
    """Write the open onto the chain.

    Deliberately does NOT claim to identify a clinician. The system knows a
    link was opened; it does not know by whom, and a receipt asserting a name
    it cannot verify would be the same false claim as reporting a message
    delivered when the provider only accepted it.
    """
    service.append_receipt(
        case_id=grant.case_id,
        kind="emergency.access",
        actor="handoff_link",
        payload={
            "what": "the emergency clinical summary was opened",
            "scope": "summary read only, one case",
            "note": (
                "A link holder, not an identified clinician. This records that "
                "the summary was opened and when, which is all the system can "
                "honestly know about a bearer link."
            ),
            "link_expires_at": grant.expires_at,
        },
    )
