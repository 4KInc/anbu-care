"""Binding a treating clinician's WhatsApp number to one case.

The question this answers is "how does Anbu Care know that is the doctor", and
the two obvious answers are both wrong.

You cannot register the doctor in advance, because a treating clinician is
whoever is on shift when your mother is admitted. And you cannot read it off
the message, because "Dr Kumar here, she needs an MRI" is a sentence anybody
can say — if that grants ordering rights on a woman's medical record, then it
grants them to everyone.

The answer is the one the handoff link already uses: a CAPABILITY. The family
mints a link and hands it to the treating team; possession of it is the grant.
This extends that grant to a channel. The doctor opens the link at the bedside,
taps once, and sends a WhatsApp message carrying a one-time code that only that
link could have produced. The number is bound by the credential, not assumed
from a claim.

Everything the link is, the binding is:

  ONE CASE      it authorises orders on that case and nothing else.
  EXPIRING      it dies with the grant that made it, not on its own schedule.
  REVOCABLE     the case's handoff_epoch is carried, so revoking the family's
                links revokes this in the same act. There is no second thing
                to remember to turn off.
  RECEIPTED     binding is on the chain, so the family can see that a number
                was connected and when.

What it deliberately does NOT do is identify a human. Same honesty as the link
itself: this proves somebody who held the grant controls that handset. It does
not prove they are a doctor, and nothing here says it does — an order carries
"as recorded by", never "verified as".
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from anbu_care import service
from anbu_care.handoff.access import HandoffDenied, _secret
from anbu_care.provenance.store import get_store

logger = logging.getLogger(__name__)

# How long a binding code is worth sending. Long enough to tap through to
# WhatsApp and hit send, short enough that a screenshot of the page is useless
# by the time anyone finds it.
CODE_TTL_SECONDS = 15 * 60

# Prefixed so a binding code cannot be replayed as a handoff token or an alert
# link, and neither of those can be sent in as a binding code.
_DOMAIN = "anbu.channel.v1"

_PREFIX = "CLINCHAN#"


@dataclass
class ClinicianChannel:
    """A handset that may place orders on one case, for as long as the grant lasts."""

    channel_id: str
    case_id: str
    parent_id: str
    e164: str
    handoff_epoch: int
    expires_at: int
    bound_at: str
    label: str = ""
    revoked_at: str = ""

    @property
    def pk(self) -> str:
        return f"CASE#{self.case_id}"

    @property
    def sk(self) -> str:
        # Digits only, matching the number index, so whatsapp:+1669… and
        # +1669… are one handset here too.
        return f"{_PREFIX}{service.number_key(self.e164)}"


def _sign(case_id: str, epoch: int, expires: int, secret: bytes) -> str:
    """Hex, not urlsafe base64.

    The code is hyphen-delimited and base64url emits "-", so a signature could
    split itself into extra fields and every code carrying one was rejected as
    malformed. Hex has no character that collides with the delimiter.
    """
    payload = f"{_DOMAIN}|{case_id}|{epoch}|{expires}".encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def mint_code(case_id: str, now: int | None = None) -> str:
    """A one-time code only a holder of the link could have produced.

    It carries the case, because the handset that sends it back is a stranger
    to this system and the message is all there is to identify. The case id is
    not a secret — it is in every link the family already holds — and the
    signature is what makes the code unforgeable.
    """
    case = service.load_case(case_id)
    if case is None:
        raise HandoffDenied("no such case")
    secret = _secret()
    if not secret:
        raise HandoffDenied("no signing secret is configured, so no code can be issued")

    expires = int(now or time.time()) + CODE_TTL_SECONDS
    signature = _sign(case_id, case.handoff_epoch, expires, secret)[:16]
    return f"ANBU-{case_id}-{case.handoff_epoch}-{expires}-{signature}"


def looks_like_code(text: str) -> bool:
    """Cheap check before spending anything on an unregistered number's message."""
    return "ANBU-" in (text or "").upper()


def extract_code(text: str) -> str:
    """The code out of whatever the sender actually typed around it."""
    match = re.search(r"ANBU-\S+", (text or ""), re.IGNORECASE)
    return match.group(0).strip(".,!?") if match else ""


def bind(code: str, e164: str, label: str = "",
         now: int | None = None) -> ClinicianChannel:
    """Connect this handset to the case its code was minted for.

    Expired, forged, revoked and malformed all raise the same way. A sender
    with a bad code learns that it did not work and nothing else: not whether
    the case exists, not whose it is, not whether they were close.
    """
    secret = _secret()
    parts = (code or "").strip().split("-")
    # ANBU - case - <id> - epoch - expires - sig, because the case id itself
    # contains a hyphen.
    if not secret or len(parts) != 6 or parts[0].upper() != "ANBU":
        raise HandoffDenied("that code is not valid")

    case_id = f"{parts[1]}-{parts[2]}"
    try:
        epoch, expires = int(parts[3]), int(parts[4])
    except ValueError:
        raise HandoffDenied("that code is not valid") from None

    moment = int(now or time.time())
    if expires < moment:
        raise HandoffDenied("that code has expired. Open the link again for a new one.")

    case = service.load_case(case_id)
    if case is None:
        raise HandoffDenied("that code is not valid")
    if epoch != case.handoff_epoch:
        # The family revoked their links after this code was minted.
        raise HandoffDenied("that code is no longer valid")
    if not hmac.compare_digest(_sign(case_id, epoch, expires, secret)[:16], parts[5]):
        raise HandoffDenied("that code is not valid")

    channel = ClinicianChannel(
        channel_id=service.new_id("clinchan"), case_id=case_id,
        parent_id=case.parent_id, e164=e164, handoff_epoch=epoch,
        # The binding outlives the code, not the grant: the code is a handshake,
        # the grant is the authority, and both die when the family revokes.
        expires_at=moment + _binding_seconds(),
        bound_at=datetime.now(UTC).isoformat(), label=label,
    )
    get_store().put(channel.pk, channel.sk, asdict(channel))

    service.append_receipt(
        case_id, kind="clinician.channel_bound", actor="handoff_link",
        payload={
            "channel_id": channel.channel_id,
            # The number is NOT on the chain. /verify is public and a doctor's
            # mobile is theirs; the hash proves which handset without
            # publishing it.
            "handset_ref": hashlib.sha256(e164.encode()).hexdigest()[:16],
            "expires_at": channel.expires_at,
            "note": ("A handset was connected by somebody holding a write-scoped "
                     "link for this case. Anbu Care cannot verify who they are "
                     "and does not claim to; orders from it are recorded as "
                     "stated, never as verified."),
        })
    logger.info("clinician channel bound for %s", case_id)
    return channel


def _binding_seconds() -> int:
    """How long a bound handset may place orders.

    Longer than the link, because a doctor who scanned a QR at 2am should not
    have to scan it again for the morning round. Still finite, because a
    credential nobody remembers issuing is the thing this file exists to avoid.
    """
    return 12 * 60 * 60


def for_number(e164: str, now: int | None = None) -> ClinicianChannel | None:
    """The live binding for this handset, or None.

    Checked against the case's CURRENT handoff_epoch on every message, so the
    family revoking their links stops orders from this number on the next one
    rather than whenever somebody remembers.
    """
    moment = int(now or time.time())
    rows = get_store().query_sk_prefix_across(f"{_PREFIX}{service.number_key(e164)}")
    for row in rows:
        fields = {k: v for k, v in row.items() if k not in {"pk", "sk"}}
        try:
            channel = ClinicianChannel(**fields)
        except TypeError:
            continue
        if channel.revoked_at or channel.expires_at < moment:
            continue
        case = service.load_case(channel.case_id)
        if case is None or case.handoff_epoch != channel.handoff_epoch:
            continue
        return channel
    return None


def revoke_for_case(case_id: str) -> int:
    """Kill every binding on this case. Returns how many."""
    store = get_store()
    rows = store.query_prefix(f"CASE#{case_id}", _PREFIX)
    for row in rows:
        store.delete(f"CASE#{case_id}", row["sk"])
    return len(rows)
