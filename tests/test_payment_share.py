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


# --- the message the family actually reads -----------------------------------

def _rendered_bill_message(monkeypatch, case_id, parent_id, *, cashless):
    """Run the real reporting path and return the one body it produced."""
    import json

    from anbu_care import server
    from anbu_care.bills import extract as bill_vision
    from anbu_care.comms import storage as gcs
    from anbu_care.comms.storage import StoredArtifact
    from anbu_care.docvision import read as docvision_read
    from anbu_care.payments.mandate import grant
    from anbu_care.tools import whatsapp_tools

    lines = [("Cardiac ward bed charges", "ward", 9_500),
             ("Nursing charges", "nursing", 1_200),
             ("Holter monitoring - 24 hour", "investigation", 4_200),
             ("Ward pharmacy - cardiac drugs", "pharmacy", 12_400)]
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(bill_vision, "_call_model", lambda image, mime: json.dumps({
        "vendor": "Sacred Heart Hospital", "payee_vpa": None,
        "bill_date": "2026-08-22", "stated_total_inr": 27_300,
        "balance_due_inr": 27_300, "is_interim": True, "unreadable": False,
        "unreadable_reason": None,
        "line_items": [{"label": l, "item": k, "amount_inr": a} for l, k, a in lines]}))
    monkeypatch.setattr(docvision_read, "read",
                        lambda image, mime_type="image/jpeg": docvision_read.Reading(
                            ok=True, kind="bill", engine="stub", detail="stub"))
    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="":
                        StoredArtifact(stored=True, url="https://signed/x",
                                       object_name=f"a/{filename}", detail="",
                                       expires_in_seconds=900))
    onboarding_tools.record_family_contact(
        parent_id, name="Arun", relationship="son", whatsapp_e164="+14155550142",
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=["billing_updates", "status_updates", "outbound_notify"])
    grant(parent_id=parent_id, case_id=case_id, payee_vpa="sacredheart@okhdfcbank",
          payee_label="Sacred Heart Hospital", per_bill_cap_inr=50_000,
          total_cap_inr=400_000, hours=48, granted_by="Arun")
    if cashless:
        _preauth(case_id, parent_id, "authorized")

    sent = []
    monkeypatch.setattr(whatsapp_tools, "_deliver",
                        lambda to_e164, body, template_name, media_url=None,
                        audience="", recipient_name="": sent.append(body) or {
                            "delivered": True, "detail": "stub", "mode": "stub",
                            "reference": "stub", "template": template_name})
    server._read_bill_and_report(case_id, parent_id, b"\xff\xd8\xff" + b"x" * 9000,
                                 "image/jpeg")
    assert sent, "no message was produced for the bill"
    return sent[-1]


def test_the_cashless_split_is_stated_once_not_twice(case, monkeypatch):
    """The regression this caught on the way in.

    `_owed_now` names the split and `_settlement_lines` named it again, in the
    future tense - so one message carried both figures twice and the estimate
    caveat three times. Worse than noise: "once the insurer settles" tells a
    family to wait for money that is being settled with the hospital now.
    """
    pid, cid = case
    body = _rendered_bill_message(monkeypatch, cid, pid, cashless=True)

    import re

    assert "settled by your insurer with the hospital directly" in body
    assert "Once the insurer settles" not in body, \
        "the reimbursement wording survived into a cashless message"

    # Both figures are read back OUT of the sentence that states them, so this
    # asserts uniqueness rather than a number that moves whenever the coverage
    # rules or the fixture's line items do.
    said = re.search(r"Around INR ([\d,]+) of it is settled .*? so INR "
                     r"([\d,]+) is the part that is yours", body)
    assert said, body
    covered, yours = said.group(1), said.group(2)
    assert covered != yours
    assert body.count(covered) == 1, f"{covered} is stated twice:\n{body}"
    assert body.count(yours) == 1, f"{yours} is stated twice:\n{body}"
    assert body.count("is the part that is yours") == 1, body
    assert body.count("not the insurer's decision") == 1, body


def test_a_reimbursement_message_is_unchanged(case, monkeypatch):
    pid, cid = case
    body = _rendered_bill_message(monkeypatch, cid, pid, cashless=False)

    assert "The hospital wants INR 27,300 of it now." in body
    assert "Once the insurer settles" in body
    assert "settled by your insurer with the hospital directly" not in body


def test_the_dashboard_can_read_the_split_back_off_the_chain(case, monkeypatch):
    """The card showing what it did NOT pay reads the receipt, not a recompute.

    It matches on payment_id and on `basis`, so if either the field name or the
    basis string moves, the card silently stops showing the more interesting
    number and nothing else fails. This is that guard.
    """
    import pathlib

    from anbu_care import service
    from anbu_care.payments import consider_bill

    pid, cid = case
    _preauth(cid, pid, "authorized")
    bill = _bill(cid, pid)
    share = payment_share.decide(case_id=cid, bill=bill,
                                 estimate=_Estimate(17_567, 9_733))

    from anbu_care.payments.mandate import grant
    grant(parent_id=pid, case_id=cid, payee_vpa="hospital@okhdfcbank",
          payee_label="Sacred Heart Hospital", per_bill_cap_inr=50_000,
          total_cap_inr=400_000, hours=48, granted_by="Arun")
    consider_bill(case_id=cid, parent_id=pid, bill_id=bill.bill_id,
                  amount_inr=share.amount_inr, share=share,
                  extracted_payee="Sacred Heart Hospital",
                  extracted_vendor="Sacred Heart Hospital")

    receipt = next(r for r in service.load_receipts(cid)
                   if r.kind in {"payment.auto_initiated", "payment.approved"})
    carried = receipt.payload["share"]
    assert carried["basis"] == payment_share.RESIDUAL
    assert carried["insurer_share_inr"] == 17_567
    assert carried["balance_due_inr"] == 27_300
    assert receipt.payload["payment_id"]

    # And the dashboard looks for exactly these names.
    ui = pathlib.Path("anbu_care/webui/index.html").read_text()
    assert 'pl.payment_id === x.payment_id' in ui
    assert 'sh.basis === "cashless_residual"' in ui
    for field in ("insurer_share_inr", "balance_due_inr", "estimate_is_provisional"):
        assert field in ui, f"the card stopped reading {field}"
