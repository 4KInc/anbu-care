"""Interim bill payment: the envelope, the payee lock, and the refusals.

This is the highest-consequence code in the project. Everywhere else the worst
case is a wrong sentence; here it is money arriving somewhere it should not.

So almost every test below is about a REFUSAL. The happy path is one test. The
rest are the ways an autonomous payer must decline, because a system that pays
when it should not is worse than one that never pays at all.

The single most important test is `test_a_bill_can_never_set_the_destination`.
`ExtractedBill.vendor` is a string a model read off a photograph, and it is the
only payee-shaped field in the repo. The design exists mostly to make it
structurally impossible for that string to become a destination.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from anbu_care import service
from anbu_care.payments import (
    MandateRejected,
    PaymentRefused,
    approve_escalated,
    confirm,
    consider_bill,
    grant,
    live_for_case,
    money_view,
    revoke,
    upi_intent,
)
from anbu_care.tools import onboarding_tools, triage_tools

HOSPITAL_VPA = "sacredheart@hdfcbank"
ATTACKER_VPA = "definitely.not.the.hospital@okaxis"


@pytest.fixture
def case():
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    cid = triage_tools.run_triage(
        parent_id=pid, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]
    return pid, cid


def _mandate(case_id, parent_id, *, per_bill=100_000, total=400_000, hours=48):
    return grant(parent_id=parent_id, case_id=case_id, payee_vpa=HOSPITAL_VPA,
                 payee_label="Sacred Heart Hospital", per_bill_cap_inr=per_bill,
                 total_cap_inr=total, hours=hours, granted_by="Karthik")


# =========================================================================
# THE DESTINATION NEVER COMES FROM THE BILL
# =========================================================================


def test_a_bill_can_never_set_the_destination(case):
    """The one that matters most.

    A bill may propose an AMOUNT. It can never propose a DESTINATION. The
    enforcer assigns the payee from the mandate rather than comparing the
    bill's to it, so even a bill that names an attacker's address cannot
    redirect money — and naming one at all is treated as evidence the bill is
    wrong, which stops the payment entirely.
    """
    parent_id, case_id = case
    _mandate(case_id, parent_id)

    result = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id="IP/1", amount_inr=40_000,
                           extracted_payee=ATTACKER_VPA)

    assert result["outcome"] == "escalated"
    assert result["paid"] is False
    assert "payee_mismatch" in result["reason"]
    assert service.list_payments(case_id) == []


def test_the_enforcer_assigns_the_payee_rather_than_comparing_it():
    """Read the code, not the behaviour. A comparison that is later loosened
    to a fuzzy match would still pass a behavioural test; an assignment cannot
    be loosened into taking the bill's value."""
    source = (pathlib.Path(__file__).resolve().parents[1]
              / "anbu_care" / "payments" / "enforcer.py").read_text()
    assert "payee = mandate.payee_vpa" in source
    body = source[source.index("def decide("):]
    assert "extracted_payee ==" not in body, "the destination is being compared"


def test_a_payment_only_ever_targets_the_mandate_payee(case):
    """Even on the happy path, and even when the bill named someone else, the
    UPI intent that comes out is addressed to the mandate."""
    parent_id, case_id = case
    _mandate(case_id, parent_id)
    result = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id="IP/1", amount_inr=40_000)

    assert "sacredheart%40hdfcbank" in result["upi_intent"]
    assert "okaxis" not in result["upi_intent"]


# =========================================================================
# THE LLM CANNOT MOVE MONEY
# =========================================================================


def test_settlement_is_unreachable_from_the_agent_and_tool_layers():
    """By import graph, not by convention. A convention is not a guarantee."""
    root = pathlib.Path(__file__).resolve().parents[1] / "anbu_care"
    offenders = []
    for path in list((root / "agents").rglob("*.py")) + list((root / "tools").rglob("*.py")):
        text = path.read_text()
        if "payments.settlement" in text or "from anbu_care.payments import settlement" in text:
            offenders.append(path.name)
    assert not offenders, f"settlement is importable from: {offenders}"


def test_settlement_is_not_exported_from_the_package():
    """`from anbu_care.payments import settlement` must not be the easy path."""
    import anbu_care.payments as payments

    assert "settlement" not in payments.__all__
    assert not hasattr(payments, "initiate")


def test_only_the_enforcer_path_calls_settlement():
    """Exactly one call site, and it sits after every check has passed."""
    root = pathlib.Path(__file__).resolve().parents[1] / "anbu_care"
    callers = [p.name for p in root.rglob("*.py")
               if "settlement.initiate(" in p.read_text()]
    assert callers == ["run.py"], f"settlement.initiate called from {callers}"


# =========================================================================
# THE ENVELOPE
# =========================================================================


def test_an_in_envelope_bill_initiates_with_no_human_tap(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id)

    result = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id="IP/1", amount_inr=40_000)

    assert result["outcome"] == "initiated"
    assert result["autonomous"] is True
    assert "payee_from_mandate" in result["guards_passed"]
    assert len(service.list_payments(case_id)) == 1


def test_an_over_cap_bill_refuses_and_escalates(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=50_000)

    result = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id="IP/1", amount_inr=60_000)

    assert result["failed_check"] == "per_bill_cap"
    assert service.list_payments(case_id) == []


def test_the_total_cap_stops_the_bill_that_would_cross_it(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=100_000, total=100_000)
    base = datetime.now(UTC)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="A",
                  amount_inr=60_000, now=base)

    result = consider_bill(case_id=case_id, parent_id=parent_id, bill_id="B",
                           amount_inr=60_000, now=base + timedelta(hours=12))
    assert result["failed_check"] == "total_cap"
    assert sum(p.amount_inr for p in service.list_payments(case_id)) == 60_000


def test_a_bill_outside_the_window_refuses(case):
    parent_id, case_id = case
    mandate = _mandate(case_id, parent_id, hours=1)

    later = mandate.window_closes_at + timedelta(hours=1)
    result = consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                           amount_inr=10_000, now=later)
    assert result["failed_check"] == "within_window"


def test_a_mandate_for_another_admission_does_not_pay_this_one(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id)
    other = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["fall"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]

    result = consider_bill(case_id=other, parent_id=parent_id,
                           bill_id="IP/9", amount_inr=10_000)
    assert result["failed_check"] == "mandate_present"


def test_with_no_mandate_every_bill_escalates(case):
    """The no-mandate mode. This is the whole of the never-autonomous design:
    the same enforcer, with nothing granted, refuses everything."""
    parent_id, case_id = case

    result = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id="IP/1", amount_inr=1_000)
    assert result["failed_check"] == "mandate_present"
    assert result["needs_human"] is True
    assert service.list_payments(case_id) == []


# =========================================================================
# IDEMPOTENCY, REVOCATION, ANOMALY
# =========================================================================


def test_the_same_bill_twice_pays_once(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id)

    first = consider_bill(case_id=case_id, parent_id=parent_id,
                          bill_id="IP/1", amount_inr=40_000)
    second = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id="IP/1", amount_inr=40_000)

    assert first["outcome"] == "initiated"
    assert second["failed_check"] == "not_duplicate"
    assert len(service.list_payments(case_id)) == 1

    kinds = [r.kind for r in service.get_chain(case_id).receipts]
    assert kinds.count("payment.auto_initiated") == 1


def test_revocation_hard_stops_autonomy(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="A", amount_inr=20_000)

    revoke(case_id, revoked_by="Karthik")

    result = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id="B", amount_inr=20_000)
    assert result["failed_check"] == "mandate_present"
    assert live_for_case(case_id) is None
    assert len(service.list_payments(case_id)) == 1


def test_an_anomalous_but_in_cap_bill_still_escalates(case):
    """Routine bills flow without waking him. Unusual ones stop, even when
    every cap allows them."""
    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=100_000, total=400_000)

    base = datetime.now(UTC)
    for i, amount in enumerate((8_000, 9_000)):
        consider_bill(case_id=case_id, parent_id=parent_id, bill_id=f"B{i}",
                      amount_inr=amount, now=base + timedelta(hours=12 * i))

    # In cap, but more than 3x the running mean of the earlier bills.
    result = consider_bill(case_id=case_id, parent_id=parent_id, bill_id="B9",
                           amount_inr=80_000, now=base + timedelta(hours=30))

    assert result["outcome"] == "escalated"
    assert result["failed_check"] == "no_anomaly"
    assert "amount_spike" in result["reason"]


def test_a_second_bill_within_hours_escalates(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id)
    base = datetime.now(UTC)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="A",
                  amount_inr=20_000, now=base)

    result = consider_bill(case_id=case_id, parent_id=parent_id, bill_id="B",
                           amount_inr=21_000, now=base + timedelta(hours=1))
    assert "burst" in result["reason"]


# =========================================================================
# CONFIRMED IS NEVER ASSUMED
# =========================================================================


def test_an_initiated_payment_is_not_a_paid_one(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id)
    result = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id="IP/1", amount_inr=40_000)

    assert result["paid"] is False
    view = money_view(case_id)
    assert view["paid_inr"] == 0
    assert view["initiated_unconfirmed_inr"] == 40_000

    kinds = [r.kind for r in service.get_chain(case_id).receipts]
    assert "payment.auto_initiated" in kinds
    assert "payment.confirmed" not in kinds


def test_confirmation_is_a_separate_act(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id)
    result = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id="IP/1", amount_inr=40_000)

    confirm(case_id=case_id, payment_id=result["payment_id"])

    view = money_view(case_id)
    assert view["paid_inr"] == 40_000
    assert view["initiated_unconfirmed_inr"] == 0


# =========================================================================
# NO BANKING CREDENTIALS, ANYWHERE
# =========================================================================


CREDENTIAL_WORDS = ("pin", "cvv", "card_number", "cardnumber", "password",
                    "bank_login", "account_number", "upi_pin", "otp", "secret")


def test_no_payment_schema_has_a_field_a_credential_could_live_in():
    from anbu_care.schemas import PaymentMandate, PaymentRecord

    for model in (PaymentMandate, PaymentRecord):
        for name in model.model_fields:
            lowered = name.lower()
            assert not any(word in lowered for word in CREDENTIAL_WORDS), \
                f"{model.__name__}.{name} looks like a credential field"


def test_no_credential_word_appears_in_the_payment_package():
    """grep, as the brief asked. Comments explaining that we hold none are
    fine; a field, parameter or key is not."""
    root = pathlib.Path(__file__).resolve().parents[1] / "anbu_care" / "payments"
    for path in root.rglob("*.py"):
        text = path.read_text()
        for word in ("cvv", "card_number", "upi_pin", "bank_login"):
            for line in text.splitlines():
                stripped = line.strip()
                if word in line.lower() and not stripped.startswith(("#", '"', "'")):
                    raise AssertionError(f"{path.name}: {line.strip()[:70]}")


def test_no_receipt_carries_a_destination(case):
    """The chain gets a reference, never an address. A receipt that carried the
    VPA would put a payable destination into a record designed to be shared."""
    parent_id, case_id = case
    _mandate(case_id, parent_id)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                  amount_inr=40_000)

    for receipt in service.get_chain(case_id).receipts:
        blob = str(receipt.payload).lower()
        assert HOSPITAL_VPA not in blob, f"{receipt.kind} carries the raw VPA"
        for word in CREDENTIAL_WORDS:
            assert f'"{word}"' not in blob


def test_verify_leaks_nothing_about_the_money(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                  amount_inr=40_000)

    from anbu_care.tools import provenance_tools

    blob = str(provenance_tools.verify_case_chain(case_id)).lower()
    assert "40000" not in blob and "40,000" not in blob
    assert HOSPITAL_VPA not in blob
    assert "hdfcbank" not in blob


# =========================================================================
# THE MANDATE ITSELF
# =========================================================================


def test_a_mandate_needs_a_plausible_upi_address(case):
    parent_id, case_id = case
    with pytest.raises(MandateRejected) as rejected:
        grant(parent_id=parent_id, case_id=case_id, payee_vpa="Sacred Heart",
              payee_label="Sacred Heart", per_bill_cap_inr=1000,
              total_cap_inr=2000, hours=24)
    assert "billing desk" in str(rejected.value)


def test_a_per_bill_cap_cannot_exceed_the_total(case):
    parent_id, case_id = case
    with pytest.raises(MandateRejected) as rejected:
        grant(parent_id=parent_id, case_id=case_id, payee_vpa=HOSPITAL_VPA,
              payee_label="X", per_bill_cap_inr=500_000, total_cap_inr=100_000,
              hours=24)
    assert "exhaust" in str(rejected.value)


def test_only_one_live_mandate_per_case(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id)
    with pytest.raises(MandateRejected) as rejected:
        _mandate(case_id, parent_id)
    assert "Revoke it" in str(rejected.value)


def test_approving_an_amount_does_not_create_a_destination(case):
    """The human gate authorises money, never a place to send it."""
    parent_id, case_id = case

    with pytest.raises(PaymentRefused) as refused:
        approve_escalated(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                          amount_inr=10_000, approved_by="Karthik")
    assert "does not create one" in str(refused.value)


def test_a_human_can_approve_what_the_enforcer_refused(case):
    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=10_000)

    refused = consider_bill(case_id=case_id, parent_id=parent_id,
                            bill_id="IP/1", amount_inr=50_000)
    assert refused["outcome"] == "escalated"

    approved = approve_escalated(case_id=case_id, parent_id=parent_id,
                                 bill_id="IP/1", amount_inr=50_000,
                                 approved_by="Karthik")
    assert approved["outcome"] == "initiated"
    assert approved["autonomous"] is False
    assert "sacredheart%40hdfcbank" in approved["upi_intent"]


def test_the_upi_intent_is_a_real_one():
    intent = upi_intent(payee_vpa=HOSPITAL_VPA, payee_label="Sacred Heart",
                        amount_inr=48_200, note="IP/2026/04471")
    assert intent.startswith("upi://pay?")
    assert "pa=sacredheart%40hdfcbank" in intent
    assert "am=48200" in intent and "cu=INR" in intent


# =========================================================================
# END TO END, THROUGH THE REAL BILL LANE
# =========================================================================


IMAGE = b"\xff\xd8\xff" + b"x" * 8000


def _bill_reads(monkeypatch, *, balance_due, vendor="Sacred Heart Hospital",
                total=90_000):
    """Pin what the model returns at the one seam, so the guards under test are
    the real enforcer rather than a mock of it."""
    import json

    from anbu_care.bills import extract as bill_vision
    from anbu_care.comms import storage as gcs
    from anbu_care.comms.storage import StoredArtifact
    from anbu_care.docvision import read as docvision_read

    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(bill_vision, "_call_model", lambda image, mime_type: json.dumps({
        "vendor": vendor, "bill_date": "2026-08-20",
        "stated_total_inr": total, "balance_due_inr": balance_due,
        "is_interim": True, "unreadable": False, "unreadable_reason": None,
        "line_items": [{"label": "ICU bed charges", "item": "icu",
                        "amount_inr": total, "source_hint": "2 days"}]}))
    monkeypatch.setattr(docvision_read, "read",
                        lambda image, mime_type="image/jpeg": docvision_read.Reading(
                            ok=True, kind="bill", engine="stub", detail="stubbed"))
    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="":
                        StoredArtifact(stored=True, url="https://signed/x",
                                       object_name=f"a/{filename}", detail="",
                                       expires_in_seconds=900))


def test_a_photographed_interim_bill_auto_clears(case, monkeypatch):
    """The whole feature, from a photograph. No human tap anywhere."""
    from anbu_care.bills import ingest_bill_image

    parent_id, case_id = case
    _mandate(case_id, parent_id)
    _bill_reads(monkeypatch, balance_due=45_000)

    bill = ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    assert bill.balance_due_inr == 45_000
    assert bill.is_interim is True

    from anbu_care.payments import consider_bill

    outcome = consider_bill(case_id=case_id, parent_id=parent_id,
                            bill_id=bill.bill_id, amount_inr=bill.balance_due_inr,
                            extracted_payee=bill.vendor)
    assert outcome["outcome"] == "initiated"
    # The vendor string off the photograph named a hospital, not a VPA, and it
    # still did not become a destination.
    assert "sacredheart%40hdfcbank" in outcome["upi_intent"]


def test_a_bill_with_nothing_outstanding_is_not_paid(case, monkeypatch):
    """A payment is for the balance due, never the total. A bill with an
    advance already against it would otherwise be paid twice over."""
    from anbu_care.bills import ingest_bill_image

    parent_id, case_id = case
    _mandate(case_id, parent_id)
    _bill_reads(monkeypatch, balance_due=0, total=90_000)

    bill = ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    assert bill.balance_due_inr == 0

    from anbu_care.server import _consider_payment

    assert _consider_payment(case_id, parent_id, bill) == "", \
        "a bill with no balance due should not reach the enforcer"
    assert service.list_payments(case_id) == []


def test_the_trace_shows_what_the_enforcer_checked(case):
    from anbu_care.trace import compose_trace

    parent_id, case_id = case
    _mandate(case_id, parent_id)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                  amount_inr=40_000)

    steps = compose_trace(case_id).steps
    granted = next(s for s in steps if s.kind == "mandate.granted")
    paid = next(s for s in steps if s.kind == "payment.auto_initiated")

    assert "authorised automatic payment" in granted.what
    assert "checks passed" in paid.detail
    assert "not yet settled" in paid.detail


def test_the_trace_says_which_check_refused_a_payment(case):
    from anbu_care.trace import compose_trace

    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=10_000)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                  amount_inr=99_000)

    step = next(s for s in compose_trace(case_id).steps
                if s.kind == "payment.escalated")
    assert "per bill cap failed" in step.detail


def test_a_hospital_name_on_a_bill_is_not_a_destination_claim(case):
    """Found by the end-to-end test, and it would have made the feature dead.

    Every real bill prints a hospital NAME in the vendor field. Comparing a
    name to a VPA never matches, so treating any mismatch as an anomaly
    escalated every legitimate bill. Only a string shaped like a payment
    address counts as a bill claiming a destination.
    """
    parent_id, case_id = case
    _mandate(case_id, parent_id)

    ordinary = consider_bill(case_id=case_id, parent_id=parent_id, bill_id="A",
                             amount_inr=30_000,
                             extracted_payee="Sacred Heart Hospital")
    assert ordinary["outcome"] == "initiated"


def test_a_bill_naming_a_payable_address_still_stops_everything(case):
    """The other half. A name is noise; an address is an attack."""
    parent_id, case_id = case
    _mandate(case_id, parent_id)

    for claimed in (ATTACKER_VPA, "upi://pay?pa=attacker@okaxis"):
        result = consider_bill(case_id=case_id, parent_id=parent_id,
                               bill_id=f"B-{claimed[:6]}", amount_inr=30_000,
                               extracted_payee=claimed)
        assert result["outcome"] == "escalated", claimed
        assert "payee_mismatch" in result["reason"]
    assert service.list_payments(case_id) == []


# =========================================================================
# ESCALATIONS, RECONCILED AGAINST THE CHAIN
# =========================================================================


def test_a_refused_bill_shows_as_open(case):
    from anbu_care.payments import escalations

    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=10_000)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                  amount_inr=50_000)

    open_ones = escalations(case_id)
    assert len(open_ones) == 1
    assert open_ones[0]["bill_id"] == "IP/1"
    assert open_ones[0]["failing_check"] == "per_bill_cap"
    assert open_ones[0]["amount_inr"] == 50_000
    assert open_ones[0]["open"] is True


def test_a_bill_escalated_then_approved_shows_resolved(case):
    """The guard that matters. A bill somebody already dealt with must stop
    asking to be dealt with, and that has to be reconciled from the chain
    rather than from a flag somebody has to remember to clear."""
    from anbu_care.payments import escalations

    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=10_000)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                  amount_inr=50_000)
    assert escalations(case_id)[0]["open"] is True

    approve_escalated(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                      amount_inr=50_000, approved_by="Karthik")

    resolved = escalations(case_id)
    assert len(resolved) == 1, "the refusal stays on the record"
    assert resolved[0]["open"] is False, "but it no longer needs anybody"


def test_two_bills_are_reconciled_independently(case):
    from anbu_care.payments import escalations

    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=10_000)
    for bill_id in ("IP/1", "IP/2"):
        consider_bill(case_id=case_id, parent_id=parent_id, bill_id=bill_id,
                      amount_inr=50_000)
    approve_escalated(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                      amount_inr=50_000, approved_by="Karthik")

    by_bill = {e["bill_id"]: e["open"] for e in escalations(case_id)}
    assert by_bill == {"IP/1": False, "IP/2": True}


def test_an_auto_paid_bill_resolves_an_earlier_refusal(case):
    """Refused for being outside the window, then paid inside it. Resolving
    is about a payment existing after the refusal, not about who made it."""
    from anbu_care.payments import escalations

    parent_id, case_id = case
    mandate = _mandate(case_id, parent_id, hours=2)
    after = mandate.window_closes_at + timedelta(hours=1)

    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                  amount_inr=20_000, now=after)
    assert escalations(case_id)[0]["open"] is True

    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                  amount_inr=20_000, now=mandate.window_opens_at + timedelta(minutes=5))
    assert escalations(case_id)[0]["open"] is False


# =========================================================================
# THE BROWSER RENDERS THE DECISION. IT NEVER MAKES ONE.
# =========================================================================


def _client() -> str:
    return (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()


def test_the_client_computes_no_cap_and_no_pay_decision():
    """Same rule the dashboard already lives under for severity and sub-limits.

    If the browser decided whether a bill was inside the envelope, the
    guarantee would have moved into unversioned client code that nothing
    audits. It renders `failing_check` and `guards_passed` as the enforcer
    recorded them; it never derives either.
    """
    page = _client()
    forbidden = [
        "SPIKE_FACTOR", "BURST_WINDOW", "NEAR_CAP", "LATE_WINDOW",
        "per_bill_cap_inr >", "> m.per_bill_cap_inr",
        "amount_inr > ", "total_cap_inr >",
        "payee_vpa ==", ".payee_vpa",     # never reads a destination back
    ]
    for token in forbidden:
        assert token not in page, f"the client appears to decide: {token!r}"


def test_no_destination_VALUE_can_appear_in_the_page():
    """Checked live against the deployed DOM, and pinned here.

    The KEY `payee_vpa` does appear in the client source, because the grant
    form has to POST one — the son types it. What must never appear is a
    destination VALUE: the API returns a label and a hash, so there is nothing
    for the page to render even if someone tried.

    So the precise claim is narrower than "the string never appears", and
    stating it precisely is the point: a write-only field in a form is not the
    same risk as a destination rendered back into the document.
    """
    page = _client()
    # Read-back paths, which would put a real address on screen.
    assert "m.payee_vpa" not in page
    assert "x.payee_vpa" not in page
    # The address IS shown back once, in the confirmation dialog, because the
    # son must see what he is about to pin. That is a native confirm() and not
    # a render path: it never enters the document.
    confirm_fn = page[page.index("async function confirmMandate()"):
                      page.index("async function revokeMandate()")]
    assert "${vpa}" in confirm_fn, "the son is no longer shown what he is pinning"
    assert page.count("${vpa}") == 1, "the address appears outside the dialog"

    # And no render path interpolates it into HTML.
    for renderer in ("vMandate", "paymentCard", "refusalCard", "mandateForm"):
        body = page[page.index(f"function {renderer}("):]
        body = body[:body.index("\n}")]
        assert "vpa" not in body.replace('id="mvpa"', "").replace("mvpa", ""), \
            f"{renderer} touches an address"


def test_no_payment_response_carries_a_destination(case):
    """The strongest form of "no raw VPA in the DOM": the API never sends one,
    so it cannot reach the page even by mistake."""
    from anbu_care.payments import escalations, money_view

    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=10_000)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                  amount_inr=50_000)
    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/2",
                  amount_inr=5_000)

    blob = str({"view": money_view(case_id),
                "escalations": escalations(case_id),
                "payments": [p.model_dump(mode="json")
                             for p in service.list_payments(case_id)]}).lower()
    assert HOSPITAL_VPA not in blob
    assert "hdfcbank" not in blob
    assert "payee_vpa" not in blob


def test_the_payment_form_holds_no_credential_field():
    """The son types a UPI ADDRESS at grant time — a destination, not a
    credential. There is nowhere in the form a PIN could be entered, and no
    field named like one."""
    page = _client()
    form = page[page.index("function mandateForm()"):page.index("function showMandateForm")]

    assert 'id="mvpa"' in form          # the address, which is the point
    for word in ("pin", "cvv", "password", "card", "otp", "secret"):
        assert f'id="{word}' not in form.lower()
        assert f'type="password"' not in form.lower()


def test_the_refusal_is_the_visual_lead():
    """Structural, not aesthetic: the refusals render BEFORE the mandate panel
    and before the payment list, so a bill needing a person is the first thing
    on the tab rather than something found by scrolling."""
    page = _client()
    view = page[page.index("function vPayments()"):page.index("function refusalCard")]

    refusals = view.index("open.map(refusalCard)")
    mandate = view.index("vMandate(m, p)")
    money = view.index("vMoney(p)")
    assert refusals < mandate < money, "the refusal is no longer the lead"


def test_the_settlement_label_is_on_the_payment_view():
    page = _client()
    view = page[page.index("function vPayments()"):page.index("function refusalCard")]
    assert "Settlement is simulated" in view
    assert "Autonomy is bounded" in view


def test_a_bill_already_on_file_can_be_put_to_the_enforcer(case, monkeypatch):
    """The ordering hole: a mandate granted AFTER a bill arrived would never
    reconsider it, and the bill would sit unpaid while cashless lapsed."""
    from fastapi.testclient import TestClient

    from anbu_care.bills import ingest_bill_image
    from anbu_care.server import app

    parent_id, case_id = case
    _bill_reads(monkeypatch, balance_due=30_000)
    bill = ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    assert service.list_payments(case_id) == []      # nothing considered it yet

    _mandate(case_id, parent_id)                     # granted afterwards

    client = TestClient(app)
    headers = {"Authorization": "Bearer anbu-demo-family-token"}
    result = client.post(f"/api/cases/{case_id}/bills/{bill.bill_id}/consider",
                         headers=headers).json()

    assert result["outcome"] == "initiated"
    assert len(service.list_payments(case_id)) == 1


def test_considering_a_bill_carries_no_amount_or_payee(case):
    """The trigger cannot influence the decision. The amount comes from the
    stored bill and the destination from the mandate; nothing crosses the
    boundary that could change either."""
    import inspect

    from anbu_care import server

    source = inspect.getsource(server.consider_bill_for_payment)
    assert "body" not in inspect.signature(server.consider_bill_for_payment).parameters
    assert "bill.balance_due_inr" in source
    assert "payee" not in source.split("def consider_bill_for_payment")[1].split("return")[0] \
        or "extracted_payee=bill.vendor" in source


def test_a_bill_with_no_balance_is_not_put_to_the_enforcer(case, monkeypatch):
    from fastapi.testclient import TestClient

    from anbu_care.bills import ingest_bill_image
    from anbu_care.server import app

    parent_id, case_id = case
    _mandate(case_id, parent_id)
    _bill_reads(monkeypatch, balance_due=0)
    bill = ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    result = TestClient(app).post(
        f"/api/cases/{case_id}/bills/{bill.bill_id}/consider",
        headers={"Authorization": "Bearer anbu-demo-family-token"}).json()
    assert result["outcome"] == "nothing_due"
    assert service.list_payments(case_id) == []


# =========================================================================
# WHAT THE FIRST REAL BILL TAUGHT
# =========================================================================


def test_a_bill_from_another_hospital_escalates(case):
    """Found live. A Sundaram Arulrhaj bill was auto-paid to Sacred Heart.

    The destination was never at risk — it is locked to the mandate either way.
    The harm is quieter: the family still owes the hospital that sent the bill,
    and the authority meant for this admission has been spent on it.
    """
    parent_id, case_id = case
    _mandate(case_id, parent_id)

    result = consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                           amount_inr=3_890,
                           extracted_vendor="Sundaram Arulrhaj Hospitals")

    assert result["outcome"] == "escalated"
    assert "vendor_mismatch" in result["reason"]
    assert service.list_payments(case_id) == []


def test_the_same_hospital_written_differently_still_pays(case):
    """The check must not fire on a legal suffix. "Sacred Heart Hospital" and
    "Sacred Heart Hospitals Pvt Ltd" are the same place, and escalating that
    would make the feature useless in the other direction."""
    parent_id, case_id = case
    _mandate(case_id, parent_id)

    result = consider_bill(case_id=case_id, parent_id=parent_id, bill_id="IP/1",
                           amount_inr=3_890,
                           extracted_vendor="Sacred Heart Hospitals Pvt Ltd")
    assert result["outcome"] == "initiated"


def test_an_unreadable_vendor_does_not_block_payment(case):
    """A bill whose vendor could not be read is not evidence of anything. The
    destination is locked regardless, so a missing name must not be treated as
    a mismatch."""
    parent_id, case_id = case
    _mandate(case_id, parent_id)

    for vendor in (None, "", "   "):
        service.list_payments(case_id)
        result = consider_bill(case_id=case_id, parent_id=parent_id,
                               bill_id=f"IP/{vendor!r}", amount_inr=3_000,
                               extracted_vendor=vendor,
                               now=datetime.now(UTC) + timedelta(hours=12))
        assert result["outcome"] == "initiated", vendor
        break


def test_no_message_calls_a_bill_something_the_bill_did_not(case):
    """A template hardcoded "interim bill" and the first real bill through it
    said INPATIENT FINAL BILL on its face. The merged copy sidesteps the whole
    question: it says what is outstanding, which is true of any bill."""
    from anbu_care.comms.policy import TEMPLATES

    for name, spec in TEMPLATES.items():
        body = str(spec["body"])
        assert "interim bill of" not in body, name


def test_one_photograph_produces_one_message():
    """Two messages a second apart, both starting "Anbu Care:", both linking the
    same tab, both about one photograph — and five different figures about one
    piece of paper spread across them. The bill and what happened about paying
    it are the same event to the person who sent it."""
    import inspect

    from anbu_care import server

    source = inspect.getsource(server._read_bill_and_report)
    # Exactly one billing message on the bill path.
    assert source.count('"billing", consent.BILLING_UPDATES') == 1
    assert "payment_line = _consider_payment" in source

    # And the helper returns copy rather than sending its own message.
    helper = inspect.getsource(server._consider_payment)
    assert "tell(" not in helper, "_consider_payment still sends its own message"
    # Said once, against the bill it is about, rather than twice in one message.
    owed = inspect.getsource(server._owed_now)
    assert "settled with them rather than kept by the" in owed
    assert helper.count("insurer") <= 1, "the settlement point is repeated"


def test_the_message_explains_what_a_paid_interim_amount_means():
    """"About INR 0 to pay" and "we just paid INR 3,890" are both true and look
    like the system arguing with itself. One is what the insurer is expected to
    settle; the other is what the hospital wanted today."""
    import inspect

    from anbu_care import server

    helper = inspect.getsource(server._consider_payment)
    # Said once, against the bill it is about, rather than twice in one message.
    owed = inspect.getsource(server._owed_now)
    assert "settled with them rather than kept by the" in owed
    assert helper.count("insurer") <= 1, "the settlement point is repeated"

    # And the immediate demand is named separately from the eventual estimate,
    # with the advance accounting for the gap between them.
    owed = inspect.getsource(server._owed_now)
    assert "wants {inr(amount_inr)} of it now" in owed
    assert "already paid against it" in owed
    assert "settled with them rather than kept by the" in owed


# =========================================================================
# WHAT HAPPENS WHEN THE PERSON IS NOT SIGNED IN
# =========================================================================


def test_approving_without_a_session_does_not_navigate_away():
    """Reported live: tapping Approve from a signed link silently moved the
    reader to the Record tab. The thing they were doing vanished and nothing
    said why, which reads as a broken button rather than as a boundary."""
    page = _client()
    approve = page[page.index("async function approvePayment("):]
    approve = approve[:approve.index("\n}")]

    assert 'S.view="record"' not in approve, "approving still teleports"
    assert "askSignIn(" in approve, "it no longer asks in place"


def test_every_sign_in_entry_point_offers_google():
    """Reported live: "Sign in to share" ran signIn() directly, so it used the
    demo credential even on a deployment with Google configured. A button that
    quietly picks the weaker of two credentials is worse than one that asks."""
    page = _client()

    # The one shared panel, and it carries the Google slot.
    panel = page[page.index("function signInPanel("):page.index("function askSignIn(")]
    assert 'id="gbtn"' in panel
    assert "Use the demo credential" in panel

    # The real invariant: the demo credential is never offered ALONE. Every
    # place it appears, a Google button sits next to it, so nobody is quietly
    # given the weaker of the two.
    import re

    for match in re.finditer(r'onclick="signIn\(\)"', page):
        window = page[max(0, match.start() - 400):match.start()]
        assert 'id="gbtn"' in window, (
            "a demo sign-in button is offered without a Google one beside it: "
            + page[max(0, match.start() - 120):match.start() + 60])


def test_an_approval_says_what_happened():
    """The refusal card disappears once resolved. A card vanishing with nothing
    in its place is indistinguishable from a card that failed to do anything."""
    page = _client()
    approve = page[page.index("async function approvePayment("):]
    approve = approve[:approve.index("\n}")]
    assert "S.payNote" in approve
    assert "not settled yet" in approve

    view = page[page.index("function vPayments()"):page.index("function refusalCard")]
    assert "S.payNote" in view, "the note is never rendered"


def test_the_two_money_figures_are_not_confusable(case, monkeypatch):
    """Reported as confusing: "about INR 2,28,690 to pay" and then "INR
    2,70,720 of that is outstanding now" — a larger number described as part of
    a smaller one.

    They are different quantities pointing in different directions of time. One
    is what the hospital wants before the insurer has settled; the other is
    what the family is left with after. Neither is part of the other, and the
    gap between the bill and the immediate demand is the advance.
    """
    from anbu_care.server import _owed_now

    class _Bill:
        payable_total_inr = 370_720

    line = _owed_now(_Bill(), 270_720)
    assert "wants INR 2,70,720 of it now" in line
    assert "less the INR 1,00,000 already paid" in line

    # "of that" is gone: nothing describes one of these as part of the other.
    from anbu_care.comms.policy import TEMPLATES

    body = str(TEMPLATES["bill_recorded"]["body"])
    assert "of that is outstanding" not in body
    # The immediate demand leads; the settlement picture reads as what follows.
    assert body.index("{payment_line}") < body.index("{settlement_lines}")


def test_a_bill_with_no_advance_does_not_invent_one(case):
    from anbu_care.server import _owed_now

    class _Bill:
        payable_total_inr = 8_890

    line = _owed_now(_Bill(), 8_890)
    assert "wants INR 8,890 of it now" in line
    assert "already paid" not in line


def test_the_settlement_block_says_what_scope_it_is(case, monkeypatch):
    """It got this wrong in the most confusing way available.

    The estimate is CASE-WIDE, covering every bill on the stay. The copy
    labelled it "of the INR 38,450 bill" using the one that had just arrived,
    so both figures came out larger than the bill they were said to be part of.
    That is not a wording problem; it is a sentence that cannot be true.
    """
    from anbu_care.bills import ingest_bill_image
    from anbu_care.bills.coverage import estimate_for_case
    from anbu_care.bills import list_bills
    from anbu_care.server import _settlement_lines

    parent_id, case_id = case
    _bill_reads(monkeypatch, balance_due=38_450, total=38_450)
    bill = ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    estimate = estimate_for_case(case_id, list_bills(case_id))

    lines = _settlement_lines(bill, estimate)
    payable = estimate.total_billed_inr - estimate.total_discount_inr

    # One bill: no "across N bills" claim, and the total is the real one.
    assert "on this bill" in lines
    assert "across the" not in lines
    # Every figure quoted is the estimate's own, never this bill's total
    # standing in for the stay.
    assert str(estimate.estimated_covered_inr)[:2] in lines.replace(",", "")


def test_the_scope_is_named_when_there_is_more_than_one_bill(case, monkeypatch):
    from anbu_care.bills import ingest_bill_image, list_bills
    from anbu_care.bills.coverage import estimate_for_case
    from anbu_care.server import _settlement_lines

    parent_id, case_id = case
    for amount in (38_450, 20_000):
        _bill_reads(monkeypatch, balance_due=amount, total=amount)
        bill = ingest_bill_image(case_id, parent_id,
                                 IMAGE + bytes([amount % 251]), "image/jpeg")
    estimate = estimate_for_case(case_id, list_bills(case_id))

    lines = _settlement_lines(bill, estimate)
    assert "across the 2 bills on this stay" in lines


def test_every_rupee_a_person_reads_is_grouped_the_indian_way():
    """The dashboard rendered 2,70,720 and the messages rendered 270,720. One
    figure, two shapes, for a family reading both."""
    from anbu_care.money import group, inr

    assert group(100_000) == "1,00,000"
    assert group(1_23_45_678) == "1,23,45,678"
    assert inr(270_720) == "INR 2,70,720"

    # No message-building code formats rupees with Python's own separator.
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "anbu_care"
    for name in ("server.py", "payments/run.py", "payments/enforcer.py",
                 "payments/mandate.py"):
        source = (root / name).read_text()
        for line in source.splitlines():
            if ":,}" in line and "INR" in line:
                raise AssertionError(f"{name}: {line.strip()[:70]}")


def test_the_interim_fixture_is_designed_to_clear(case):
    """A demo bill that escalates for a reason nobody intended wastes a take.

    This one exists to show the agent acting alone, so it has to pass every
    check — including the two that are easy to trip by accident: near-cap
    (90% of the per-bill cap) and vendor identity against the mandate.
    """
    from scripts.make_bill_images import INTERIM_DAY_TWO
    from anbu_care.payments.enforcer import NEAR_CAP_FRACTION

    balance = next(a for label, a, _ in INTERIM_DAY_TWO["totals"]
                   if label == "BALANCE DUE")
    per_bill_cap = 50_000

    assert balance < per_bill_cap
    assert balance < NEAR_CAP_FRACTION * per_bill_cap, "would trip near_cap"
    assert INTERIM_DAY_TWO["hospital"] == "Sacred Heart Hospital"
    assert "INTERIM" in INTERIM_DAY_TWO["bill_title"]

    # No advance against it, so the total and the balance agree and the message
    # has no gap to explain.
    total = next(a for label, a, _ in INTERIM_DAY_TWO["totals"] if label == "TOTAL")
    assert total == balance

    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=per_bill_cap)
    result = consider_bill(case_id=case_id, parent_id=parent_id,
                           bill_id=INTERIM_DAY_TWO["bill_no"], amount_inr=balance,
                           extracted_vendor=INTERIM_DAY_TWO["hospital"])
    assert result["outcome"] == "initiated"
    assert result["autonomous"] is True


def test_a_bill_prints_rupees_the_indian_way():
    """The bill is a photograph of an Indian document. 38,450 is right;
    38450 or a Western-grouped lakh would not be."""
    from scripts.make_bill_images import _group

    assert _group(38_450) == "38,450"
    assert _group(370_720) == "3,70,720"


def test_a_human_approval_does_not_make_the_next_payment_suspicious(case):
    """Reported live. The cardiac bill was refused for the cap and approved by
    hand; six minutes later an ordinary in-envelope bill escalated for "burst".

    The signal exists to catch the agent being drained while nobody is
    watching. A payment somebody approved means somebody was watching minutes
    ago, so counting it inverted the meaning of the check.
    """
    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=50_000)
    base = datetime.now(UTC)

    approve_escalated(case_id=case_id, parent_id=parent_id, bill_id="BIG",
                      amount_inr=270_720, approved_by="Heartlin")

    result = consider_bill(case_id=case_id, parent_id=parent_id, bill_id="SMALL",
                           amount_inr=38_450, now=base + timedelta(minutes=6),
                           extracted_vendor="Sacred Heart Hospital")
    assert result["outcome"] == "initiated", result.get("reason")


def test_two_automatic_payments_in_a_row_still_escalate(case):
    """The other half. Nobody was watching either time, which is the case the
    signal is actually for."""
    parent_id, case_id = case
    _mandate(case_id, parent_id, per_bill=50_000)
    base = datetime.now(UTC)

    consider_bill(case_id=case_id, parent_id=parent_id, bill_id="A",
                  amount_inr=20_000, now=base,
                  extracted_vendor="Sacred Heart Hospital")
    result = consider_bill(case_id=case_id, parent_id=parent_id, bill_id="B",
                           amount_inr=21_000, now=base + timedelta(minutes=6),
                           extracted_vendor="Sacred Heart Hospital")

    assert result["outcome"] == "escalated"
    assert "burst" in result["reason"]
    assert "automatic payment" in result["reason"]
