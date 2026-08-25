"""How an attempt reaches a centre, in two halves that must stay two halves.

A channel PREPARES and then COMMITS, and nothing may collapse them. The first
version had one `attempt()` that navigated, filled and submitted, with the
enforcer ruling afterwards on what it saw - which meant `no_payment` and
`cancellable` were reporting on a form that had already gone. A guard that runs
after the act is a log line, not a guard.

So:

  prepare   navigate, find the form, work out what goes where, read the page,
            and capture how a person would cancel. Submits NOTHING.
  decide    the enforcer rules, in the caller, on what prepare actually saw.
  commit    only then, and only if it was allowed.

Preparation is deliberately STATELESS. A remote driver could hold the browser
open between the two calls and be much faster, and it would break the first time
Cloud Run replaced an instance mid-decision. Commit re-navigates and re-fills
from the same inputs, which costs a second run and cannot be broken by instance
churn - and it re-reads the page on the way, so a page that changed between the
two is filled from what is there now rather than from a memory of it.

The three honest outcomes are unchanged:

  REQUESTED   a form was submitted and the centre has not answered. This is what
              an unauthenticated callback form can truthfully produce, and it is
              not an appointment.
  CONFIRMED   the centre returned a slot or a reference.
  UNAVAILABLE this channel cannot serve this centre. Not a failure of the centre
              and not an error - the lane moves to the next one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

REQUESTED = "requested"
CONFIRMED = "confirmed"
UNAVAILABLE = "unavailable"
READY = "ready"


@dataclass(frozen=True)
class Preparation:
    """Everything the enforcer needs, gathered without committing to anything."""

    outcome: str
    detail: str = ""
    cancel_url: str = ""
    cancel_phone: str = ""
    # What the driver actually read. The enforcer looks here for payment
    # signals, so it must be the page as it stood with the form filled in.
    page_text: str = ""
    # Opaque, and the driver's own business. Never a browser session: it is the
    # inputs commit needs to do the same navigation again.
    handle: dict = field(default_factory=dict)
    # Whether this flow will text somebody a code. Known BEFORE committing, so
    # the person holding the phone can be warned before it arrives rather than
    # receiving six digits from a lab nobody told her to expect.
    expects_otp: bool = False
    expects_otp_because: str = ""

    @property
    def ready(self) -> bool:
        return self.outcome == READY


@dataclass(frozen=True)
class AttemptResult:
    """What committing got out of one centre."""

    outcome: str
    detail: str = ""
    cancel_url: str = ""
    cancel_phone: str = ""
    slot_text: str = ""
    provider_ref: str = ""
    # Where the screenshot of what was submitted is kept, so a family can see
    # the page rather than take this system's word for it.
    evidence: str = ""

    @property
    def landed(self) -> bool:
        return self.outcome in {REQUESTED, CONFIRMED}


class Channel(Protocol):
    """A way of asking a centre to hold a slot."""

    name: str

    def can_serve(self, centre: dict) -> bool: ...

    def prepare(self, *, centre: dict, payload: dict) -> Preparation: ...

    def commit(self, *, centre: dict, payload: dict, prepared: Preparation,
               session_id: str = "", otp_wait_seconds: int = 0) -> AttemptResult: ...


class NoChannel:
    """The driver used when none is configured. Reaches nothing, and says so.

    It exists so the lane runs end to end with no booking stack at all: the
    guards run, a centre is chosen, the fall-through happens, and the family is
    told a person needs to ring. That is a true description of the system's
    behaviour rather than a placeholder pretending to be one.
    """

    name = "none"

    def can_serve(self, centre: dict) -> bool:
        return False

    def prepare(self, *, centre: dict, payload: dict) -> Preparation:
        return Preparation(
            outcome=UNAVAILABLE,
            detail=("no booking channel is configured on this deployment, so "
                    "nothing was sent to this centre"))

    def commit(self, *, centre: dict, payload: dict, prepared: Preparation,
               session_id: str = "", otp_wait_seconds: int = 0) -> AttemptResult:
        return AttemptResult(outcome=UNAVAILABLE, detail=prepared.detail)


def available() -> list[Channel]:
    """The channels this deployment can use, in the order to try them.

    Named explicitly rather than discovered, because a channel that can act on
    her behalf should never be something that switched itself on. Ordered least
    intrusive first: a form submission bothers nobody, a phone call occupies a
    person.
    """
    enabled = [c.strip().lower()
               for c in os.getenv("ANBU_BOOKING_CHANNELS", "").split(",")
               if c.strip()]
    channels: list[Channel] = []
    for name in enabled:
        if name == "web":
            from anbu_care.booking.web import WebChannel

            if WebChannel.configured():
                channels.append(WebChannel())
    return channels or [NoChannel()]
