"""Turning a bill into a payment, or into a refusal somebody has to look at.

This is the only module that calls settlement, and it calls it in exactly one
place: after `enforcer.decide` has returned `pay=True`. Everything else here is
bookkeeping and receipts.

The shape is the same one the rest of the system uses. A model read the bill.
Code decided. The receipt records which guards passed, so the decision can be
argued with afterwards by someone who was asleep when it was made.
"""

from __future__ import annotations

from datetime import UTC, datetime

from anbu_care import service
from anbu_care.payments import mandate as mandates
from anbu_care.payments import settlement
from anbu_care.payments.enforcer import Decision, decide, payee_ref, upi_intent
from anbu_care.schemas import PaymentRecord


class PaymentRefused(Exception):
    """Not paid. Carries why, because "it did not pay" is not an answer."""

    def __init__(self, message: str, *, failed_check: str = "",
                 payment_id: str = "") -> None:
        super().__init__(message)
        self.failed_check = failed_check
        self.payment_id = payment_id


def consider_bill(*, case_id: str, parent_id: str, bill_id: str,
                  amount_inr: int, extracted_payee: str | None = None,
                  extracted_vendor: str | None = None,
                  now: datetime | None = None) -> dict:
    """Decide what happens to one payable bill, and do it.

    Returns a dict describing the outcome either way. Never raises for a
    refusal — a refusal is an outcome the family is told about, not an error.
    """
    now = now or datetime.now(UTC)
    mandate = mandates.live_for_case(case_id)
    history = service.list_payments(case_id)

    verdict: Decision = decide(
        bill_id=bill_id, case_id=case_id, amount_inr=amount_inr,
        mandate=mandate, history=history, now=now,
        extracted_payee=extracted_payee, extracted_vendor=extracted_vendor)

    if not verdict.pay:
        return _escalate(case_id=case_id, parent_id=parent_id, bill_id=bill_id,
                         amount_inr=amount_inr, verdict=verdict,
                         mandate=mandate)

    return _initiate(case_id=case_id, parent_id=parent_id, bill_id=bill_id,
                     verdict=verdict, mandate=mandate, autonomous=True)


def _escalate(*, case_id: str, parent_id: str, bill_id: str, amount_inr: int,
              verdict: Decision, mandate) -> dict:
    """Refused. Record which check stopped it, and hand it to a human."""
    service.append_receipt(
        case_id, kind="payment.escalated", actor="payment_enforcer",
        payload={
            "bill_id": bill_id,
            "amount_inr": amount_inr,
            "failed_check": verdict.failed_check,
            "reason": verdict.reason[:400],
            "guards_passed": verdict.guards_passed,
            "mandate_id": mandate.mandate_id if mandate else None,
            "note": ("No money moved. The enforcer refused to pay this "
                     "automatically and it now needs a person to approve it."),
        })
    return {
        "outcome": "escalated",
        "paid": False,
        "bill_id": bill_id,
        "amount_inr": amount_inr,
        "failed_check": verdict.failed_check,
        "reason": verdict.reason,
        "guards_passed": verdict.guards_passed,
        "needs_human": True,
    }


def _initiate(*, case_id: str, parent_id: str, bill_id: str,
              verdict: Decision, mandate, autonomous: bool) -> dict:
    """Every check passed. This is the one place settlement is called."""
    payment_id = service.new_id("pay")
    result = settlement.initiate(payment_id=payment_id,
                                 amount_inr=verdict.amount_inr,
                                 payee_ref=verdict.payee_ref,
                                 payee_label=mandate.payee_label,
                                 bill_id=bill_id)
    if not result.initiated:
        # The rail refused. Nothing was authorised away and nothing is
        # recorded as a payment: this is an escalation like any other refusal.
        return _escalate(
            case_id=case_id, parent_id=parent_id, bill_id=bill_id,
            amount_inr=verdict.amount_inr, mandate=mandate,
            verdict=Decision(pay=False, guards_passed=verdict.guards_passed,
                             failed_check="provider",
                             reason=f"the payment provider did not accept it: "
                                    f"{result.detail}",
                             amount_inr=verdict.amount_inr))

    record = PaymentRecord(
        payment_id=payment_id, case_id=case_id, parent_id=parent_id,
        bill_id=bill_id, amount_inr=verdict.amount_inr,
        payee_ref=verdict.payee_ref, payee_label=mandate.payee_label,
        mandate_id=mandate.mandate_id, autonomous=autonomous,
        guards_passed=verdict.guards_passed,
        settlement_note=result.detail,
        settlement_ref=result.reference,
        checkout_url=result.checkout_url,
    )
    service.save_payment(record)

    service.append_receipt(
        case_id, kind="payment.auto_initiated" if autonomous else "payment.approved",
        actor="payment_enforcer" if autonomous else "family",
        payload={
            "payment_id": payment_id,
            "bill_id": bill_id,
            "amount_inr": verdict.amount_inr,
            "payee_ref": verdict.payee_ref,
            "mandate_id": mandate.mandate_id,
            "guards_passed": verdict.guards_passed,
            "settlement": settlement.rail(),
            "confirmed": False,
            "note": ("Initiated, NOT settled. Every guard on the mandate "
                     "passed and the destination came from the mandate rather "
                     "than from the bill. Settlement is simulated in this "
                     "build; no banking credential is held or recorded."),
        })

    return {
        "outcome": "initiated",
        "paid": False,          # initiated is not paid, and never says it is
        "payment_id": payment_id,
        "bill_id": bill_id,
        "amount_inr": verdict.amount_inr,
        "payee_ref": verdict.payee_ref,
        "payee_label": mandate.payee_label,
        "guards_passed": verdict.guards_passed,
        "autonomous": autonomous,
        "settlement_note": result.detail,
        "checkout_url": result.checkout_url,
        "upi_intent": upi_intent(payee_vpa=verdict.payee_vpa,
                                 payee_label=mandate.payee_label,
                                 amount_inr=verdict.amount_inr,
                                 note=bill_id),
    }


def approve_escalated(*, case_id: str, parent_id: str, bill_id: str,
                      amount_inr: int, approved_by: str,
                      now: datetime | None = None) -> dict:
    """A human explicitly approved a bill the enforcer would not pay.

    This is the path Brief A describes and the path every case takes when no
    mandate exists. It still cannot invent a destination: without a mandate
    there is no verified payee, so there is nothing to pay to and the caller is
    told to grant one. Approval authorises an amount; it never authorises a
    place to send it.
    """
    now = now or datetime.now(UTC)
    mandate = mandates.live_for_case(case_id)
    if mandate is None:
        raise PaymentRefused(
            "there is no live mandate on this case, so there is no verified "
            "destination to pay. Approving an amount does not create one — "
            "grant a mandate with the hospital's UPI address first.",
            failed_check="mandate_present")
    if any(p.bill_id == bill_id for p in service.list_payments(case_id)):
        raise PaymentRefused(f"bill {bill_id} has already been paid",
                             failed_check="not_duplicate")
    if amount_inr <= 0:
        raise PaymentRefused("the amount is not positive",
                             failed_check="amount_positive")

    verdict = Decision(
        pay=True,
        guards_passed=["human_approved", "payee_from_mandate", "not_duplicate"],
        amount_inr=amount_inr, payee_vpa=mandate.payee_vpa,
        payee_ref=payee_ref(mandate.payee_vpa))

    outcome = _initiate(case_id=case_id, parent_id=parent_id, bill_id=bill_id,
                        verdict=verdict, mandate=mandate, autonomous=False)
    outcome["approved_by"] = approved_by
    return outcome


def confirm(*, case_id: str, payment_id: str) -> dict:
    """Record that a settlement confirmation actually arrived.

    Deliberately a separate call from the one that initiates. An initiated
    payment nobody confirms stays unconfirmed forever and shows as initiated,
    never as paid.
    """
    record = next((p for p in service.list_payments(case_id)
                   if p.payment_id == payment_id), None)
    if record is None:
        raise PaymentRefused(f"no payment {payment_id} on case {case_id}")
    if record.confirmed_at is not None:
        return {"outcome": "already_confirmed", "payment_id": payment_id}

    result = settlement.confirmation_for(record.settlement_ref or f"sim-{payment_id}")
    if not result.confirmed:
        raise PaymentRefused(result.detail)

    record.confirmed_at = datetime.now(UTC)
    record.settlement_note = result.detail
    service.save_payment(record)

    service.append_receipt(
        case_id, kind="payment.confirmed", actor="payment_rail",
        payload={"payment_id": payment_id, "bill_id": record.bill_id,
                 "amount_inr": record.amount_inr,
                 "payee_ref": record.payee_ref,
                 "settlement": settlement.rail(),
                 "note": ("A settlement confirmation arrived. This receipt is "
                          "never written by the code that initiates a payment.")})
    return {"outcome": "confirmed", "payment_id": payment_id,
            "amount_inr": record.amount_inr, "settlement_note": result.detail}


def confirm_by_reference(*, reference: str, note: str, failed: bool = False,
                        amount_paise: int | None = None) -> dict:
    """Settle or fail the payment carrying this provider reference.

    The webhook knows an order id and nothing about our cases, so the lookup
    goes the other way: find the stored payment that carries it. A reference
    nobody stored is ignored rather than guessed at, because an unmatched
    callback is far more likely to be somebody else's traffic than ours.
    """
    for payment in service.find_payments_by_settlement_ref(reference):
        if failed:
            payment.failed_at = datetime.now(UTC)
            payment.settlement_note = f"the provider reported this {note}"
            service.save_payment(payment)
            service.append_receipt(
                payment.case_id, kind="payment.failed", actor="payment_rail",
                payload={"payment_id": payment.payment_id,
                         "bill_id": payment.bill_id,
                         "amount_inr": payment.amount_inr,
                         "note": "The provider reported this payment failed. "
                                 "Nothing settled."})
            return {"status": "failed", "payment_id": payment.payment_id}

        if payment.confirmed_at is not None:
            return {"status": "already_confirmed", "payment_id": payment.payment_id}

        # A capture for LESS than the bill is a part payment, and a part
        # payment is not a settled one. Razorpay fires payment.captured for
        # whatever was actually paid, so confirming on the event alone would
        # mark a 38,450 bill settled off a 5,000 payment.
        if amount_paise is not None and amount_paise != payment.amount_inr * 100:
            service.append_receipt(
                payment.case_id, kind="payment.partial", actor="payment_rail",
                payload={"payment_id": payment.payment_id,
                         "bill_id": payment.bill_id,
                         "expected_inr": payment.amount_inr,
                         "received_inr": amount_paise // 100,
                         "note": ("The provider reported a different amount from "
                                  "the one this payment is for. Nothing is "
                                  "marked settled and it needs a person.")})
            return {"status": "amount_mismatch",
                    "payment_id": payment.payment_id,
                    "expected_inr": payment.amount_inr,
                    "received_inr": amount_paise // 100}

        return confirm(case_id=payment.case_id, payment_id=payment.payment_id)

    return {"status": "ignored", "reason": "no payment carries that reference"}


def money_view(case_id: str) -> dict:
    """Paid so far, initiated but unconfirmed, and what authority remains.

    An unconfirmed payment is counted separately and never added to "paid".
    """
    payments = service.list_payments(case_id)
    mandate = mandates.live_for_case(case_id)
    confirmed = [p for p in payments if p.is_settled]
    pending = [p for p in payments if not p.is_settled and p.failed_at is None]

    return {
        "paid_inr": sum(p.amount_inr for p in confirmed),
        "initiated_unconfirmed_inr": sum(p.amount_inr for p in pending),
        "payment_count": len(payments),
        "confirmed_count": len(confirmed),
        "mandate": None if mandate is None else {
            "mandate_id": mandate.mandate_id,
            "payee_label": mandate.payee_label,
            "payee_ref": payee_ref(mandate.payee_vpa),
            "per_bill_cap_inr": mandate.per_bill_cap_inr,
            "total_cap_inr": mandate.total_cap_inr,
            "remaining_inr": max(0, mandate.total_cap_inr
                                 - sum(p.amount_inr for p in payments)),
            "window_closes_at": mandate.window_closes_at.isoformat(),
        },
        "settlement": settlement.rail(),
        "note": settlement.label(),
    }


# ---- what was refused, and whether it still needs somebody ----------------
#
# A refusal pays nothing, so it stores no payment row. The only record is the
# `payment.escalated` receipt on the chain, which is the right place for it —
# a parallel table would be a second answer to "why was this not paid", and two
# answers to that question is worse than none.
#
# So the join happens here, server-side, reading the chain. The browser is
# never asked to work out from receipt payloads whether a bill still needs its
# owner; it renders what this returns.

_RESOLVING = ("payment.approved", "payment.auto_initiated", "payment.confirmed")


def escalations(case_id: str) -> list[dict]:
    """Bills the enforcer refused, each marked open or resolved.

    Open means: this bill was escalated and nothing has paid it since. A bill
    that was refused and then approved by a person is RESOLVED and must not
    keep asking to be approved — which is why this reconciles by sequence
    against the chain rather than trusting a flag somebody has to remember to
    clear.
    """
    receipts = service.get_chain(case_id).receipts

    # "Nobody authorised this" stops being true the moment somebody does. The
    # refusal was correct when it was made and stays on the chain, but it must
    # not keep leading the page with a reason that now contradicts the mandate
    # printed directly beneath it. The bill is not paid by this — it goes back
    # to waiting for a decision, which is a thing the family is asked to make
    # rather than one made for them while they were not looking.
    mandate_now_exists = mandates.live_for_case(case_id) is not None

    last_escalated: dict[str, object] = {}
    last_resolved: dict[str, int] = {}

    for receipt in receipts:
        bill_id = str(receipt.payload.get("bill_id") or "")
        if not bill_id:
            continue
        if receipt.kind == "payment.escalated":
            last_escalated[bill_id] = receipt
        elif receipt.kind in _RESOLVING:
            last_resolved[bill_id] = max(last_resolved.get(bill_id, 0), receipt.seq)

    out: list[dict] = []
    for bill_id, receipt in last_escalated.items():
        payload = receipt.payload
        out.append({
            "bill_id": bill_id,
            "amount_inr": payload.get("amount_inr"),
            "failing_check": payload.get("failed_check"),
            "reason": payload.get("reason"),
            "guards_passed": payload.get("guards_passed") or [],
            "at": receipt.created_at.isoformat(),
            # Resolved when something paid this bill AFTER it was refused, or
            # when the thing it was waiting for has since happened.
            "open": (last_resolved.get(bill_id, 0) <= receipt.seq
                     and not (payload.get("failed_check") == "mandate_present"
                              and mandate_now_exists)),
            "superseded_by_mandate": (payload.get("failed_check") == "mandate_present"
                                      and mandate_now_exists),
        })
    return sorted(out, key=lambda e: e["at"])
