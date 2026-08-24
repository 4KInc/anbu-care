"""Granting and revoking the authority to pay without asking again.

A mandate carries its own caps and window, is granted by a human in a
credentialed session, and can be killed by one instantly.

It comes in two shapes. One is scoped to a single admission. The other is
STANDING: granted on the parent, ahead of any admission, and adopted by each
case as it opens.

The standing shape exists because the per-admission one had the son granting
authority at the exact moment he cannot. A case opens at 3am in Thoothukudi
while he is asleep in Nashville; until he wakes up and grants something, a bill
cannot be paid and the system that was supposed to stand in for him is waiting
on him. Deciding how much may be spent is his job. Being conscious when an
ambulance arrives is not.

What makes that safe is one rule, and it is the whole of the design: **the
total cap is a ceiling across every case the grant covers, not a fresh
allowance for each one.** A family that authorised INR 400,000 authorised that
much money. Copying the cap onto each new admission would turn one signature
into three, which is how a standing authority becomes a story about a system
that spent more than anybody agreed to.

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


def _check(payee_vpa: str, per_bill_cap_inr: int, total_cap_inr: int,
           hours: int) -> None:
    """The same refusals whatever the grant is scoped to.

    Shared rather than duplicated because a standing grant is the wider act:
    a rule that held for one admission and quietly did not hold for all of
    them would be the wrong way round.
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
    _check(vpa, per_bill_cap_inr, total_cap_inr, hours)

    if service.load_case(case_id) is None:
        raise MandateRejected(f"no case {case_id}")

    # One live mandate per case. A second would make "the destination"
    # ambiguous. Deliberately NOT live_for_case, which adopts a standing grant
    # as a side effect of being asked - granting explicitly would then adopt
    # one and immediately refuse itself.
    existing = _live_on_case(case_id)
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


def _live_on_case(case_id: str) -> PaymentMandate | None:
    """The live mandate written against this case, if any. No adoption."""
    return next((m for m in service.list_mandates(case_id) if m.is_live), None)


def live_standing_for(parent_id: str) -> PaymentMandate | None:
    """The one live standing grant on this parent, read fresh. Never cached."""
    return next((m for m in service.list_standing_mandates(parent_id) if m.is_live),
                None)


def grant_standing(*, parent_id: str, payee_vpa: str, payee_label: str,
                   per_bill_cap_inr: int, total_cap_inr: int, hours: int,
                   granted_by: str = "", method_ref: str = "") -> PaymentMandate:
    """Authorise ahead of an admission, so nobody has to be awake for one.

    Validated exactly as a per-case grant is, because it is the same act with a
    wider reach - and the receipt goes on the PARENT chain, which is where an
    authority that outlives any single episode belongs.
    """
    _check(payee_vpa, per_bill_cap_inr, total_cap_inr, hours)
    if service.load_profile(parent_id) is None:
        raise MandateRejected(f"no parent {parent_id}")

    existing = live_standing_for(parent_id)
    if existing is not None:
        raise MandateRejected(
            f"standing authority {existing.mandate_id} is already live for this "
            f"parent. Revoke it before granting another.")

    now = datetime.now(UTC)
    mandate = PaymentMandate(
        mandate_id=service.new_id("mandate"),
        parent_id=parent_id, case_id="",
        payee_vpa=payee_vpa.strip(), payee_label=payee_label.strip() or payee_vpa.strip(),
        per_bill_cap_inr=per_bill_cap_inr, total_cap_inr=total_cap_inr,
        window_opens_at=now, window_closes_at=now + timedelta(hours=hours),
        granted_by=granted_by, method_ref=method_ref,
    )
    service.save_standing_mandate(mandate)

    service.append_receipt(
        parent_id, kind="mandate.standing_granted", actor="family",
        payload={
            "mandate_id": mandate.mandate_id,
            "payee_ref": payee_ref(mandate.payee_vpa),
            "payee_label": mandate.payee_label,
            "per_bill_cap_inr": per_bill_cap_inr,
            "total_cap_inr": total_cap_inr,
            "window_closes_at": mandate.window_closes_at.isoformat(),
            "granted_by": granted_by,
            "note": ("A family member authorised automatic payment of interim "
                     "bills to ONE destination, ahead of any admission, so a "
                     "case opening while they sleep is not one that cannot pay. "
                     "The total cap is a ceiling across every admission this "
                     "covers, not a fresh allowance for each. The destination is "
                     "not on this receipt and is never taken from a bill. No "
                     "banking credential is held."),
        })
    return mandate


def revoke_standing(parent_id: str, revoked_by: str = "") -> PaymentMandate | None:
    """Stop it covering anything further, including cases already carrying it.

    Copies already adopted are not hunted down and rewritten. The enforcer
    checks the grant behind a copy on every decision, so killing the grant
    stops the copies in the same act - the same discipline that makes per-case
    revocation a hard stop rather than a flag consulted politely.
    """
    mandate = live_standing_for(parent_id)
    if mandate is None:
        return None
    mandate.revoked_at = datetime.now(UTC)
    service.save_standing_mandate(mandate)
    service.append_receipt(
        parent_id, kind="mandate.standing_revoked", actor="family",
        payload={"mandate_id": mandate.mandate_id, "revoked_by": revoked_by,
                 "note": ("Standing authority withdrawn. Admissions already "
                          "carrying it stop paying automatically too, and every "
                          "further bill escalates for explicit approval.")})
    return mandate


def live_for_case(case_id: str) -> PaymentMandate | None:
    """The live authority on this case, read fresh. Never cached.

    A case-level mandate wins outright - an explicit grant for this admission
    is a narrower, later human act than a standing one, and it should.

    Otherwise a live standing grant is ADOPTED: written onto the case with a
    receipt, so the chain shows what paid for this admission and shows that
    nobody granted it here. Adoption is the honest alternative to the enforcer
    quietly reaching up to the parent on every decision, which would leave a
    case whose bills were paid under an authority its own record never mentions.
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

    # Declined here already. Revoking an adopted copy must stick, and without
    # this the next question re-adopts the grant it was just told to stop.
    if any(m.standing_id == standing.mandate_id
           for m in service.list_mandates(case_id)):
        return None

    return _adopt(standing, case_id)


def _adopt(standing: PaymentMandate, case_id: str) -> PaymentMandate:
    """Write the standing grant onto this case, as its own record."""
    adopted = standing.model_copy(update={
        "mandate_id": service.new_id("mandate"),
        "case_id": case_id,
        "standing_id": standing.mandate_id,
    })
    service.save_mandate(adopted)
    service.append_receipt(
        case_id, kind="mandate.standing_applied", actor="system",
        payload={
            "mandate_id": adopted.mandate_id,
            "standing_id": standing.mandate_id,
            "payee_ref": payee_ref(adopted.payee_vpa),
            "payee_label": adopted.payee_label,
            "per_bill_cap_inr": adopted.per_bill_cap_inr,
            "total_cap_inr": adopted.total_cap_inr,
            "window_closes_at": adopted.window_closes_at.isoformat(),
            "granted_by": adopted.granted_by,
            "note": ("NOBODY AUTHORISED ANYTHING FOR THIS ADMISSION. A standing "
                     "authority the family granted earlier covers it, and this "
                     "records that it was applied here and by what. The total "
                     "cap is shared with every other admission the same grant "
                     "covers, so this is not a fresh allowance."),
        })
    return adopted
