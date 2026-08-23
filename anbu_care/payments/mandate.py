"""Granting and revoking the authority to pay without asking again.

A mandate is scoped to one admission and carries its own caps and window. It
is granted by a human in a credentialed session and it can be killed by one
instantly.

Revocation is a hard stop rather than a flag consulted politely: the enforcer
loads the mandate fresh on every decision, so there is no cached copy to go
stale and no in-flight grace period. A payment that has not passed the enforcer
at the moment revocation lands does not pass it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from anbu_care import service
from anbu_care.money import group
from anbu_care.payments.enforcer import payee_ref
from anbu_care.schemas import PaymentMandate


class MandateRejected(Exception):
    """Not granted, and the reason is safe to show whoever asked."""


def grant(*, parent_id: str, case_id: str, payee_vpa: str, payee_label: str,
          per_bill_cap_inr: int, total_cap_inr: int, hours: int,
          granted_by: str = "", method_ref: str = "") -> PaymentMandate:
    """Record what a human just authorised.

    The VPA arrives from a human who typed it, having read it off the
    hospital's own billing desk. It is never derived from a bill, an
    extraction, or anything this system inferred. Everything downstream treats
    it as the single destination, so this is the moment that matters.
    """
    vpa = (payee_vpa or "").strip()
    if "@" not in vpa or len(vpa) < 5:
        raise MandateRejected(
            f"{payee_vpa!r} is not a UPI address. It should look like "
            f"name@bank, and it should be read off the hospital's billing "
            f"desk rather than off a bill photograph.")
    if per_bill_cap_inr <= 0 or total_cap_inr <= 0:
        raise MandateRejected("both caps must be positive")
    if per_bill_cap_inr > total_cap_inr:
        raise MandateRejected(
            f"the per-bill cap (INR {group(per_bill_cap_inr)}) is above the total "
            f"cap (INR {group(total_cap_inr)}), which would let one bill exhaust "
            f"the whole authority")
    if hours <= 0:
        raise MandateRejected("the window must be a positive number of hours")

    if service.load_case(case_id) is None:
        raise MandateRejected(f"no case {case_id}")

    # One live mandate per case. A second would make "the destination" ambiguous.
    existing = live_for_case(case_id)
    if existing is not None:
        raise MandateRejected(
            f"mandate {existing.mandate_id} is already live on this case. "
            f"Revoke it before granting another.")

    now = datetime.now(UTC)
    mandate = PaymentMandate(
        mandate_id=service.new_id("mandate"),
        parent_id=parent_id, case_id=case_id,
        payee_vpa=vpa, payee_label=payee_label.strip() or vpa,
        per_bill_cap_inr=per_bill_cap_inr, total_cap_inr=total_cap_inr,
        window_opens_at=now, window_closes_at=now + timedelta(hours=hours),
        granted_by=granted_by, method_ref=method_ref,
    )
    service.save_mandate(mandate)

    service.append_receipt(
        case_id, kind="mandate.granted", actor="family",
        payload={
            "mandate_id": mandate.mandate_id,
            "payee_ref": payee_ref(vpa),
            "payee_label": mandate.payee_label,
            "per_bill_cap_inr": per_bill_cap_inr,
            "total_cap_inr": total_cap_inr,
            "window_closes_at": mandate.window_closes_at.isoformat(),
            "granted_by": granted_by,
            "note": ("A family member authorised automatic payment of interim "
                     "bills to ONE destination, up to these caps, inside this "
                     "window. The destination is not on this receipt and is "
                     "never taken from a bill. No banking credential is held."),
        })
    return mandate


def revoke(case_id: str, revoked_by: str = "") -> PaymentMandate | None:
    """Kill it now. Everything after this refuses."""
    mandate = live_for_case(case_id)
    if mandate is None:
        return None
    mandate.revoked_at = datetime.now(UTC)
    service.save_mandate(mandate)
    service.append_receipt(
        case_id, kind="mandate.revoked", actor="family",
        payload={"mandate_id": mandate.mandate_id, "revoked_by": revoked_by,
                 "note": ("Automatic payment stopped. Every further bill "
                          "escalates for explicit approval.")})
    return mandate


def live_for_case(case_id: str) -> PaymentMandate | None:
    """The one live mandate on this case, read fresh. Never cached."""
    return next((m for m in service.list_mandates(case_id) if m.is_live), None)
