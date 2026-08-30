"""How much of a photographed bill the family is actually asked to pay.

Until this existed the answer was always "the balance printed on the paper",
and on a cashless admission that pays the insurer's share out of the family's
money. These are the cases that decide the figure, and the ones where getting
it wrong is worse than doing nothing.
"""

from __future__ import annotations

import pytest

from anbu_care import service
from anbu_care.payments import share as payment_share
from anbu_care.schemas import BillLineItem, ExtractedBill, PreAuthRequest
from anbu_care.tools import onboarding_tools, triage_tools


@pytest.fixture
def case():
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=[], allergies=[])["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-NRI-4471902",
        sum_insured_inr=500_000, network_hospitals=["Sacred Heart Hospital"],
        cashless_eligible=True)
    cid = triage_tools.run_triage(
        parent_id=pid, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]
    return pid, cid


def _bill(case_id, parent_id, *, total=27_300, balance=27_300):
    return ExtractedBill(
        bill_id=service.new_id("bill"), case_id=case_id, parent_id=parent_id,
        vendor="Sacred Heart Hospital",
        line_items=[BillLineItem(label="Cardiac ward bed charges",
                                 item="ward", amount_inr=total)],
        stated_total_inr=total, balance_due_inr=balance)


def _preauth(case_id, parent_id, outcome):
    req = PreAuthRequest(
        preauth_id=service.new_id("pa"), case_id=case_id, parent_id=parent_id,
        insurer="Star Health", policy_number="SH-NRI-4471902",
        sum_insured_inr=500_000, cashless_eligible=True, outcome=outcome)
    service.save_preauth(req)
    return req


class _Estimate:
    """Just the two fields `decide` reads, plus the warning flag."""

    def __init__(self, covered, you_pay, may_understate=False):
        self.estimated_covered_inr = covered
        self.estimated_you_pay_inr = you_pay
        self.may_understate = may_understate


# --- reimbursement: nothing changes -----------------------------------------

def test_with_no_cashless_authorisation_the_printed_balance_stands(case):
    pid, cid = case
    got = payment_share.decide(case_id=cid, bill=_bill(cid, pid),
                               estimate=_Estimate(17_567, 9_733))
    assert got.basis == payment_share.FULL
    assert got.amount_inr == 27_300
    assert "claims afterwards" in got.note


@pytest.mark.parametrize("outcome", ["requested", "queried", "denied",
                                     "clock_breached"])
def test_a_pre_auth_that_is_not_authorised_does_not_reduce_anything(case, outcome):
    # Undecided or refused cover is not cover. Netting an insurer share off a
    # bill the insurer has not agreed to pay would leave the hospital short.
    pid, cid = case
    _preauth(cid, pid, outcome)
    got = payment_share.decide(case_id=cid, bill=_bill(cid, pid),
                               estimate=_Estimate(17_567, 9_733))
    assert got.basis == payment_share.FULL
    assert got.amount_inr == 27_300


# --- cashless: only the family's share --------------------------------------

@pytest.mark.parametrize("outcome", ["authorized", "authorized_with_limits"])
def test_under_cashless_only_the_uncovered_part_is_paid(case, outcome):
    pid, cid = case
    _preauth(cid, pid, outcome)
    got = payment_share.decide(case_id=cid, bill=_bill(cid, pid),
                               estimate=_Estimate(17_567, 9_733))
    assert got.basis == payment_share.RESIDUAL
    assert got.amount_inr == 9_733
    assert got.covered_inr == 17_567
    assert "not the insurer's decision" in got.note


def test_the_receipt_says_which_amount_this_is_and_why(case):
    # A figure smaller than the bill, with nothing on the chain saying the
    # insurer covers the rest, reads as an underpayment.
    pid, cid = case
    req = _preauth(cid, pid, "authorized")
    got = payment_share.decide(case_id=cid, bill=_bill(cid, pid),
                               estimate=_Estimate(17_567, 9_733))
    payload = got.receipt_payload()
    assert payload["basis"] == payment_share.RESIDUAL
    assert payload["amount_inr"] == 9_733
    assert payload["balance_due_inr"] == 27_300
    assert payload["insurer_share_inr"] == 17_567
    assert payload["preauth_id"] == req.preauth_id


def test_an_estimate_that_may_understate_is_flagged_not_hidden(case):
    # Where a room went over its sub-limit the real residual is larger, so
    # paying this figure underpays the hospital. It is carried, not suppressed.
    pid, cid = case
    _preauth(cid, pid, "authorized")
    got = payment_share.decide(
        case_id=cid, bill=_bill(cid, pid),
        estimate=_Estimate(17_567, 9_733, may_understate=True))
    assert got.estimate_is_provisional is True
    assert got.receipt_payload()["estimate_is_provisional"] is True


# --- the ways this could go wrong -------------------------------------------

def test_a_bill_the_hospital_already_netted_is_not_reduced_twice(case):
    # The bill totals 27,300 and asks for 9,733: the TPA credit is already
    # posted. Deducting the estimate again would pay 9,733 minus the cover.
    pid, cid = case
    _preauth(cid, pid, "authorized")
    got = payment_share.decide(
        case_id=cid, bill=_bill(cid, pid, total=27_300, balance=9_733),
        estimate=_Estimate(17_567, 9_733))
    assert got.basis == payment_share.ALREADY_NET
    assert got.amount_inr == 9_733
    assert "posted the insurer's share" in got.note


def test_a_residual_larger_than_the_bill_never_overpays(case):
    # An estimate disagreeing with a hospital does not get to raise the amount.
    pid, cid = case
    _preauth(cid, pid, "authorized")
    got = payment_share.decide(case_id=cid, bill=_bill(cid, pid, balance=5_000),
                               estimate=_Estimate(1_000, 99_000))
    assert got.amount_inr == 5_000


def test_a_negative_residual_becomes_nothing_rather_than_a_refund(case):
    pid, cid = case
    _preauth(cid, pid, "authorized")
    got = payment_share.decide(case_id=cid, bill=_bill(cid, pid),
                               estimate=_Estimate(30_000, -2_700))
    assert got.amount_inr == 0


def test_with_nothing_estimated_as_covered_the_whole_balance_is_theirs(case):
    pid, cid = case
    _preauth(cid, pid, "authorized")
    got = payment_share.decide(case_id=cid, bill=_bill(cid, pid),
                               estimate=_Estimate(0, 27_300))
    assert got.basis == payment_share.FULL
    assert got.amount_inr == 27_300


def test_with_no_estimate_at_all_the_printed_balance_stands(case):
    # No coverage math means no residual to compute, and the answer is the one
    # this code gave before the split existed.
    pid, cid = case
    _preauth(cid, pid, "authorized")
    got = payment_share.decide(case_id=cid, bill=_bill(cid, pid), estimate=None)
    assert got.basis == payment_share.FULL
    assert got.amount_inr == 27_300


def test_a_pre_auth_lookup_that_fails_falls_back_to_the_full_balance(case,
                                                                    monkeypatch):
    # Never raises into a bill. A broken lookup must not stop a hospital being
    # paid, and the fallback is the behaviour that existed before this module.
    pid, cid = case
    monkeypatch.setattr(payment_share.service, "list_preauths",
                        lambda _cid: (_ for _ in ()).throw(RuntimeError("down")))
    got = payment_share.decide(case_id=cid, bill=_bill(cid, pid),
                               estimate=_Estimate(17_567, 9_733))
    assert got.basis == payment_share.FULL
    assert got.amount_inr == 27_300
