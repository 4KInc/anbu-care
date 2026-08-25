"""How an attempt actually reaches a centre.

Phase 0 ships the registry and no driver. That is deliberate rather than
unfinished: the deciding, the guards, the falling through and the receipts are
where both the value and the risk of this lane live, and they are testable with
no network at all. A driver arriving later changes what an attempt DOES and
nothing about what it is allowed to do.

Every driver answers the same question - "did this centre take the request, and
how do we undo it" - and the honest answers are three, not two:

  REQUESTED   a form was submitted and the centre has not answered yet. This is
              what an unauthenticated callback form can truthfully produce, and
              it is not an appointment. Same discipline as an initiated payment
              that is not a settled one.
  CONFIRMED   the centre returned a slot or a reference.
  UNAVAILABLE this channel cannot serve this centre. Not a failure of the
              centre and not an error - the lane moves to the next one.

A driver may also REFUSE, which is different again: it got far enough to learn
something that means this booking must not happen here, most often that the slot
must be paid for. That routes back through the enforcer rather than being
handled locally, because deciding is not a driver's job.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

REQUESTED = "requested"
CONFIRMED = "confirmed"
UNAVAILABLE = "unavailable"
REFUSED = "refused"


@dataclass(frozen=True)
class AttemptResult:
    """What one channel got out of one centre."""

    outcome: str
    detail: str = ""
    # Captured before committing, and the reason guard 10 can do its job.
    cancel_url: str = ""
    cancel_phone: str = ""
    slot_text: str = ""
    provider_ref: str = ""
    # Whatever the channel read, so the enforcer can look for payment signals in
    # words the driver never interpreted.
    page_text: str = ""
    fields_sent: dict = field(default_factory=dict)

    @property
    def landed(self) -> bool:
        return self.outcome in {REQUESTED, CONFIRMED}


class Channel(Protocol):
    """A way of asking a centre to hold a slot."""

    name: str

    def can_serve(self, centre: dict) -> bool: ...

    def attempt(self, *, centre: dict, payload: dict) -> AttemptResult: ...


class NoChannel:
    """The Phase 0 driver. Reaches nothing, and says so precisely.

    It exists so the lane runs end to end today: the guards run, a centre is
    chosen, the attempt is recorded, the fall-through happens, and the family is
    told a person needs to ring. That is a true description of the system's
    behaviour rather than a placeholder pretending to be one, and every receipt
    it writes will still be accurate once a real driver lands beside it.
    """

    name = "none"

    def can_serve(self, centre: dict) -> bool:
        return False

    def attempt(self, *, centre: dict, payload: dict) -> AttemptResult:
        return AttemptResult(
            outcome=UNAVAILABLE,
            detail=("no booking channel is configured on this deployment, so "
                    "nothing was sent to this centre"))


def available() -> list[Channel]:
    """The channels this deployment can use, in the order to try them.

    Ordered cheapest-and-most-certain first, which is also least-intrusive
    first: a form submission bothers nobody, a phone call occupies a person.
    """
    enabled = {c.strip().lower()
               for c in os.getenv("ANBU_BOOKING_CHANNELS", "").split(",")
               if c.strip()}
    if not enabled:
        return [NoChannel()]

    channels: list[Channel] = []
    # Drivers register here as they land. Named explicitly rather than
    # discovered, because a channel that can act on her behalf should never be
    # something that switched itself on.
    return channels or [NoChannel()]
