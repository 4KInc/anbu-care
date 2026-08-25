"""Holding an appointment for a test a clinician ordered.

The lane that moves this system from surfacing options to acting on them, and
the first one where being wrong reaches a third party who never agreed to any
of it. See `docs/proposals/booking.md` for the reasoning; the short version is
that it is the payment lane's shape - a bounded authority a human granted,
ordered deterministic guards, a destination the counterparty cannot set,
receipts, and one act to revoke - pointed at a different verb.

It carries NO authority to spend.
"""

from anbu_care.booking.mandate import (
    BookingMandateRejected,
    grant_standing,
    live_for_case,
    live_standing_for,
    revoke,
    revoke_standing,
)
from anbu_care.booking.run import BookingRefused, arrange, cancel, choose

__all__ = ["BookingMandateRejected", "BookingRefused", "arrange", "cancel",
           "choose", "grant_standing", "live_for_case", "live_standing_for",
           "revoke", "revoke_standing"]
