"""Granting and revoking the authority to hold an appointment on her behalf.

The same shape as the payment mandate, pointed at a different verb, and standing
for the same reason: a test gets ordered at 3am while the son is asleep eleven
time zones away, and an authority that needs him awake is an authority that
fails at exactly the moment it exists for.

A standing grant lives on the parent and each case ADOPTS it as it opens,
writing a receipt that says in as many words that nobody authorised anything for
this admission. A case whose appointments were made under an authority its own
record never mentions would be unauditable, and unauditable is the one thing
this lane cannot be - it is the first place the system acts against a third
party in the physical world.

Revocation is a hard stop, not a flag consulted politely. The enforcer loads
both the copy and the grant behind it on every decision, so there is no cached
authority to go stale and no in-flight grace period.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from anbu_care import service
from anbu_care.schemas import BookingMandate

logger = logging.getLogger(__name__)

PREFERENCES = frozenset({"nearest", "highest_score"})


class BookingMandateRejected(Exception):
    """Not granted, and the reason is safe to show whoever asked."""


def _check(max_distance_km: float, prefer: str, max_attempts: int,
           hours: int) -> None:
    if max_distance_km <= 0:
        raise BookingMandateRejected("the distance limit must be positive")
    if prefer not in PREFERENCES:
        raise BookingMandateRejected(
            f"{prefer!r} is not a preference this system can honour. It knows "
            f"distance and its own ranking; it has no price for a test, and "
            f"ordering by one it cannot see would be worse than offering fewer.")
    if max_attempts < 1:
        raise BookingMandateRejected("it must be allowed at least one attempt")
    if hours <= 0:
        raise BookingMandateRejected("the window must be a positive number of hours")


def grant_standing(*, parent_id: str, max_distance_km: float = 15.0,
                   home_collection_only: bool = False,
                   prefer: str = "highest_score", max_attempts: int = 3,
                   requires_cancellable: bool = True, hours: int = 720,
                   granted_by: str = "") -> BookingMandate:
    """Authorise ahead of any admission, so nobody has to be awake for one."""
    _check(max_distance_km, prefer, max_attempts, hours)
    if service.load_profile(parent_id) is None:
        raise BookingMandateRejected(f"no parent {parent_id}")

    existing = live_standing_for(parent_id)
    if existing is not None:
        raise BookingMandateRejected(
            f"booking authority {existing.mandate_id} is already live for this "
            f"parent. Revoke it before granting another.")

    now = datetime.now(UTC)
    mandate = BookingMandate(
        mandate_id=service.new_id("bmandate"), parent_id=parent_id,
        window_opens_at=now, window_closes_at=now + timedelta(hours=hours),
        max_distance_km=max_distance_km,
        home_collection_only=home_collection_only, prefer=prefer,
        max_attempts=max_attempts, requires_cancellable=requires_cancellable,
        granted_by=granted_by,
    )
    service.save_standing_booking_mandate(mandate)

    service.append_receipt(
        parent_id, kind="booking.standing_granted", actor="family",
        payload={
            "mandate_id": mandate.mandate_id,
            "max_distance_km": max_distance_km,
            "home_collection_only": home_collection_only,
            "prefer": prefer, "max_attempts": max_attempts,
            "requires_cancellable": requires_cancellable,
            "window_closes_at": mandate.window_closes_at.isoformat(),
            "granted_by": granted_by,
            "note": ("A family member authorised Anbu Care to hold an "
                     "appointment for a test a clinician orders, at a centre "
                     "this system surfaced, within these limits. It carries NO "
                     "authority to spend: a slot that must be paid for stops "
                     "and asks. Every attempt is recorded, including the ones "
                     "that failed."),
        })
    return mandate


def revoke_standing(parent_id: str, revoked_by: str = "") -> BookingMandate | None:
    """Stop it covering anything further, including cases already carrying it."""
    mandate = live_standing_for(parent_id)
    if mandate is None:
        return None
    mandate.revoked_at = datetime.now(UTC)
    service.save_standing_booking_mandate(mandate)
    service.append_receipt(
        parent_id, kind="booking.standing_revoked", actor="family",
        payload={"mandate_id": mandate.mandate_id, "revoked_by": revoked_by,
                 "note": ("Booking authority withdrawn. Admissions already "
                          "carrying it stop too, and a test a clinician orders "
                          "is surfaced for a person to arrange.")})
    return mandate


def live_standing_for(parent_id: str) -> BookingMandate | None:
    """The one live standing grant on this parent, read fresh. Never cached."""
    return next((m for m in service.list_standing_booking_mandates(parent_id)
                 if m.is_live), None)


def _live_on_case(case_id: str) -> BookingMandate | None:
    return next((m for m in service.list_booking_mandates(case_id) if m.is_live),
                None)


def live_for_case(case_id: str) -> BookingMandate | None:
    """The live booking authority on this case, read fresh.

    A case-level grant wins outright, then a standing one is adopted. Identical
    reasoning to the payment lane: an explicit grant for this admission is a
    narrower, later human act, and declining it here must stick rather than
    being re-adopted by the next question.
    """
    on_case = _live_on_case(case_id)
    if on_case is not None:
        return on_case

    case = service.load_case(case_id)
    if case is None:
        return None
    standing = live_standing_for(case.parent_id)
    if standing is None:
        return None
    if any(m.standing_id == standing.mandate_id
           for m in service.list_booking_mandates(case_id)):
        return None

    return _adopt(standing, case_id)


def _adopt(standing: BookingMandate, case_id: str) -> BookingMandate:
    adopted = standing.model_copy(update={
        "mandate_id": service.new_id("bmandate"),
        "case_id": case_id,
        "standing_id": standing.mandate_id,
    })
    service.save_booking_mandate(adopted)
    service.append_receipt(
        case_id, kind="booking.standing_applied", actor="system",
        payload={
            "mandate_id": adopted.mandate_id,
            "standing_id": standing.mandate_id,
            "max_distance_km": adopted.max_distance_km,
            "prefer": adopted.prefer,
            "max_attempts": adopted.max_attempts,
            "note": ("NOBODY AUTHORISED ANYTHING FOR THIS ADMISSION. A standing "
                     "booking authority the family granted earlier covers it, "
                     "and this records that it was applied here and by what. It "
                     "still carries no authority to spend."),
        })
    return adopted


def revoke(case_id: str, revoked_by: str = "") -> BookingMandate | None:
    """Kill the authority on this admission. Adopted or explicit."""
    mandate = _live_on_case(case_id)
    if mandate is None:
        return None
    mandate.revoked_at = datetime.now(UTC)
    service.save_booking_mandate(mandate)
    service.append_receipt(
        case_id, kind="booking.revoked", actor="family",
        payload={"mandate_id": mandate.mandate_id, "revoked_by": revoked_by,
                 "note": ("Booking stopped for this admission. Options are "
                          "still surfaced; nothing is arranged.")})
    return mandate
