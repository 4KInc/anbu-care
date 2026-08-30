"""How much of a photographed bill is actually the family's to pay.

Until this existed the answer was always "all of it". Both callers handed the
enforcer `bill.balance_due_inr` - the balance printed on the paper - and nine
guards then checked the destination, the caps and the anomalies without one of
them asking the only question that decides the amount: is the insurer paying
the hospital directly?

Under a CASHLESS admission it is. The insurer settles with the hospital and the
family owes the residual: the disallowed lines, the co-pay, the non-medical
items. Paying the printed balance there does not merely overpay, it pays the
insurer's share out of the family's money and then leaves them to argue for it
back. On the demo's own day-four bill that is INR 27,300 paid where INR 9,733
was owed.

Under REIMBURSEMENT the family really does pay first and claim later, so the
printed balance is right, and nothing here changes it.

THE ESTIMATE IS AN ESTIMATE, and this is the honest cost of the feature. The
residual comes from `CoverageEstimate`, which is Anbu Care's own arithmetic over
the policy, not the insurer's decision. Where a room line exceeded its sub-limit
the estimate does not model the proportionate deduction Indian insurers apply to
associated charges, so the true residual can be LARGER than the figure used
here, and paying it would underpay the hospital. That is a real exposure and it
is not hidden: `estimate_is_provisional` rides on the receipt and the family is
told the number is an estimate in the same message that names it.

WHAT STOPS THE WORST VERSION. If the hospital has already posted the insurer's
share - the bill shows a credit, so its balance is below its own total - then
the paper is already the residual and deducting the estimate on top would
subtract the same cover twice. That case pays the printed balance and says so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from anbu_care import service

log = logging.getLogger(__name__)

# The two outcomes that mean the insurer is paying the hospital directly.
IN_FORCE = frozenset({"authorized", "authorized_with_limits"})

FULL = "full_balance"
RESIDUAL = "cashless_residual"
ALREADY_NET = "hospital_posted_the_credit"


@dataclass(frozen=True)
class Share:
    """The amount to hand the enforcer, and why it is that amount."""

    amount_inr: int
    basis: str
    covered_inr: int = 0
    balance_due_inr: int = 0
    preauth_id: str = ""
    estimate_is_provisional: bool = False
    note: str = ""

    @property
    def is_residual(self) -> bool:
        return self.basis == RESIDUAL

    def receipt_payload(self) -> dict:
        return {
            "basis": self.basis,
            "amount_inr": self.amount_inr,
            "balance_due_inr": self.balance_due_inr,
            "insurer_share_inr": self.covered_inr,
            "preauth_id": self.preauth_id,
            "estimate_is_provisional": self.estimate_is_provisional,
            "note": self.note,
        }


def cashless_in_force(case_id: str) -> str:
    """The id of an authorised cashless pre-auth on this admission, or "".

    Never raises. A pre-auth lookup that failed must not stop a bill being
    paid, and falling back to the printed balance is the behaviour that existed
    before this module did.
    """
    try:
        for req in service.list_preauths(case_id):
            if req.outcome in IN_FORCE:
                return req.preauth_id
    except Exception as e:  # noqa: BLE001 - fall back to the printed balance
        log.warning("could not read pre-auth state for %s: %s", case_id, e)
    return ""


def decide(*, case_id: str, bill, estimate=None) -> Share:
    """What to pay on this bill.

    `estimate` is the case's CoverageEstimate where one has been computed. With
    no estimate there is no residual to compute and the printed balance stands,
    which is the same answer this code gave before the split existed.
    """
    balance = int(getattr(bill, "balance_due_inr", 0) or 0)

    preauth_id = cashless_in_force(case_id)
    if not preauth_id:
        return Share(
            amount_inr=balance, basis=FULL, balance_due_inr=balance,
            note=("No cashless authorisation is in force on this admission, so "
                  "the family pays the hospital and claims afterwards. The "
                  "balance printed on the bill is what is owed."))

    covered = int(getattr(estimate, "estimated_covered_inr", 0) or 0)
    residual = int(getattr(estimate, "estimated_you_pay_inr", 0) or 0)
    if estimate is None or covered <= 0:
        return Share(
            amount_inr=balance, basis=FULL, balance_due_inr=balance,
            preauth_id=preauth_id,
            note=("Cashless is authorised, but nothing on this bill is "
                  "estimated to be covered, so the whole balance is the "
                  "family's either way."))

    # The hospital already netted the insurer off. Deducting again would take
    # the same cover out twice and underpay by the whole covered amount.
    total = int(getattr(bill, "payable_total_inr", 0) or 0)
    if total and balance < total:
        return Share(
            amount_inr=balance, basis=ALREADY_NET, covered_inr=total - balance,
            balance_due_inr=balance, preauth_id=preauth_id,
            note=("The bill's balance is already below its own total, so the "
                  "hospital has posted the insurer's share. The printed "
                  "balance is the family's share and is paid as it stands."))

    # Never above the printed balance, and never below zero. A residual that
    # came out larger than the paper is an estimate disagreeing with a hospital,
    # and the paper wins.
    amount = max(0, min(residual, balance))
    return Share(
        amount_inr=amount, basis=RESIDUAL, covered_inr=covered,
        balance_due_inr=balance, preauth_id=preauth_id,
        # The estimate's own warning flag, carried straight through. It is set
        # when a room line exceeded its sub-limit, where the real residual is
        # larger than this figure - which is exactly the direction that
        # underpays a hospital. The family is told in the same message.
        estimate_is_provisional=bool(getattr(estimate, "may_understate", False)),
        note=("Cashless is authorised on this admission, so the insurer settles "
              "its share with the hospital directly and only the rest is the "
              "family's. This is Anbu Care's estimate of that rest, not the "
              "insurer's decision."))
