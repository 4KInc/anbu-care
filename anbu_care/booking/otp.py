"""Getting a one-time code from the person who is actually holding the phone.

Nearly every real slot-booking flow in India sends an OTP. That is an identity
control and a bot control, and **defeating it is out of scope, permanently.**

It is also not the obstacle it looks like. The code goes to a phone a human is
holding, and there is a human in the room: the neighbour who is with her. So
Anbu Care asks her for it. That is a person supplying their OWN one-time code to
a system they are helping - the thing the control exists to require - and it is
the care circle rather than the son, because he is asleep eleven time zones away
and this is exactly the hop the whole design says to hand to whoever is present.

Two properties make this safe to have in the inbound path at all:

  NARROW    a message of digits is only ever read as a code while a request is
            actually outstanding for that parent, inside a few minutes, from
            somebody already in the care circle. Outside that window "123456" is
            a wellbeing message like any other, and is recorded as one.
  ONE SHOT  a request is closed the moment it is used or expires. A code cannot
            be replayed into a second booking, and a stale reply cannot resume a
            session that has moved on.

The session id is minted HERE and handed to the driver, rather than the driver
minting one and reporting it back. The inbound webhook needs to know which
browser session a reply belongs to before the reply arrives, and a system that
has to wait for a stranger's answer to learn where to send it has a race it
cannot win.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import asdict, dataclass

from anbu_care import service
from anbu_care.provenance.store import get_store

logger = logging.getLogger(__name__)

# Long enough for somebody to notice a message, read a text, and type six
# digits. Short enough that the code is dead before the window is.
TTL_SECONDS = 6 * 60

_PREFIX = "OTPREQ#"

# What a one-time code looks like on the subcontinent: four to eight digits and
# nothing else in the message. Deliberately strict - "she took 6 tablets" must
# never be read as a verification code.
_CODE = re.compile(r"^\s*(\d{4,8})\s*$")


@dataclass
class OtpRequest:
    """One outstanding ask for a code, and where the answer has to go."""

    request_id: str
    parent_id: str
    case_id: str
    order_id: str
    session_id: str
    centre_name: str
    place_id: str
    asked_at: float
    expires_at: float
    asked_of: str = ""
    used_at: float = 0.0

    @property
    def pk(self) -> str:
        return f"PARENT#{self.parent_id}"

    @property
    def sk(self) -> str:
        return f"{_PREFIX}{self.request_id}"

    def live(self, now: float | None = None) -> bool:
        return not self.used_at and self.expires_at > (now or time.time())


def looks_like_code(text: str) -> str:
    """The digits, if this message is nothing but digits. Otherwise empty."""
    match = _CODE.match(text or "")
    return match.group(1) if match else ""


def open_request(*, parent_id: str, case_id: str, order_id: str,
                 centre_name: str, place_id: str, asked_of: str = "",
                 now: float | None = None) -> OtpRequest:
    """Start waiting for a code, and mint the session the answer belongs to."""
    moment = now or time.time()
    request = OtpRequest(
        request_id=service.new_id("otpreq"), parent_id=parent_id,
        case_id=case_id, order_id=order_id,
        # Unguessable, because it names a live browser session that is one step
        # from submitting somebody's details.
        session_id=secrets.token_urlsafe(24),
        centre_name=centre_name, place_id=place_id,
        asked_at=moment, expires_at=moment + TTL_SECONDS, asked_of=asked_of,
    )
    get_store().put(request.pk, request.sk, asdict(request))
    service.append_receipt(
        case_id, kind="booking.otp_requested", actor="booking",
        payload={
            "request_id": request.request_id, "order_id": order_id,
            "place_id": place_id, "asked_of": asked_of,
            "expires_in_seconds": TTL_SECONDS,
            "note": ("The centre sent a one-time code to a phone. Anbu Care "
                     "asked the person who is with her for it rather than "
                     "trying to get around the check, which is what the check "
                     "is for. The code itself is never recorded."),
        })
    return request


def live_for(parent_id: str, now: float | None = None) -> OtpRequest | None:
    """The outstanding request for this parent, if there is one."""
    rows = get_store().query_prefix(f"PARENT#{parent_id}", _PREFIX)
    moment = now or time.time()
    best = None
    for row in rows:
        fields = {k: v for k, v in row.items() if k not in {"pk", "sk"}}
        try:
            request = OtpRequest(**fields)
        except TypeError:
            continue
        if request.live(moment) and (best is None or request.asked_at > best.asked_at):
            best = request
    return best


def close(request: OtpRequest, *, outcome: str, now: float | None = None) -> None:
    """One shot. Used or abandoned, it stops being answerable either way."""
    request.used_at = now or time.time()
    get_store().put(request.pk, request.sk, asdict(request))
    service.append_receipt(
        request.case_id, kind="booking.otp_closed", actor="booking",
        payload={"request_id": request.request_id, "outcome": outcome,
                 "note": ("The code request is closed and cannot be answered "
                          "again. The code was never recorded.")})


def sweep(parent_id: str, now: float | None = None) -> int:
    """Close anything that expired without an answer. Returns how many."""
    rows = get_store().query_prefix(f"PARENT#{parent_id}", _PREFIX)
    moment = now or time.time()
    closed = 0
    for row in rows:
        fields = {k: v for k, v in row.items() if k not in {"pk", "sk"}}
        try:
            request = OtpRequest(**fields)
        except TypeError:
            continue
        if not request.used_at and request.expires_at <= moment:
            close(request, outcome="expired", now=moment)
            closed += 1
    return closed
