"""Settlement. A real provider in test mode, or a stub, and it says which.

`ANBU_PAYMENT_MODE` picks:

  razorpay   a real API call to Razorpay in TEST MODE. Real order, real
             identifier, real webhook. Test-mode money, which does not exist.
  razorpayx  a real payout through RazorpayX in TEST MODE. Real API call,
             real payout object, real webhook, against a test balance topped
             up in the dashboard. Money is pushed rather than requested, so
             nobody opens anything. KYC gates LIVE payouts, not these.
  payout     the payout SHAPE with no provider behind it. What razorpayx does,
             minus the API call, for a deployment with no RazorpayX keys. It
             says so on every surface.
  simulated  no call at all. The fallback when no keys are configured.
  off        refuse to do anything.

A Payment Link is a COLLECTION instrument. Its whole purpose is to ask a
person to pay, so a lane that ends in one ends with a human clicking, no
matter how autonomous the deciding was. Paying a hospital is a payout, which
is the opposite direction, and the link was only ever there because test keys
hand you collections.

The distinction matters and the label follows it, because "we integrate a
payment provider" and "we move money" are different claims and only the first
is true here.

What is real either way: the mandate, the envelope, the payee lock,
idempotency, the anomaly step-up, the receipts.

What is NOT real in any mode: autonomous debit. Creating an order is an
instruction; pulling funds from somebody's account while they are asleep needs
UPI Autopay or an e-mandate under NPCI, which needs a registered merchant and
approved mandates rather than more code.

`initiate` deliberately returns UNCONFIRMED. Confirmation is a separate act,
because a system that assumes its own success reports money as paid that is
not — the same reason `comms.sent` is a different receipt from
`comms.not_delivered`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SIMULATED_LABEL = ("Settlement simulated. Production integrates a licensed "
                   "payment provider; no money moved.")
PAYOUT_LABEL = ("Simulated payout. The instruction, the checks and the receipts "
                "are real; no money moved. A live payout needs RazorpayX "
                "credentials and a funded account.")
RAZORPAYX_LABEL = ("RazorpayX payout in test mode. The payout is real and so is "
                   "the balance it came from; that balance is test money. "
                   "Going live needs an activated account.")
TEST_MODE_LABEL = ("Razorpay in test mode. The order is real; the money is not. "
                   "Going live needs a registered merchant account.")

# What the surfaces show. Read at call time so a deployment without keys says
# the honest thing rather than the configured thing.
def label() -> str:
    from anbu_care.payments import providers

    if _mode() == "razorpayx":
        return RAZORPAYX_LABEL if providers.x_configured() else PAYOUT_LABEL
    if _mode() == "payout":
        return PAYOUT_LABEL
    return TEST_MODE_LABEL if _mode() == "razorpay" and providers.configured() \
        else SIMULATED_LABEL


def rail() -> str:
    """Which rail actually carried this, for the record and the trace.

    A receipt saying "simulated" for a real Razorpay order is the same class of
    untruth as calling an unconfirmed payment paid, and it was hardcoded in
    three places before a real provider existed to contradict it.
    """
    from anbu_care.payments import providers

    if _mode() == "razorpayx":
        # Falls back rather than pretending: keys missing means the payout was
        # not carried by RazorpayX, and the receipt must not say it was.
        return "razorpayx-test" if providers.x_configured() else "payout-simulated"
    if _mode() == "payout":
        return "payout-simulated"
    return "razorpay-test" if _mode() == "razorpay" and providers.configured() \
        else "simulated"


def _mode() -> str:
    return os.getenv("ANBU_PAYMENT_MODE", "simulated").strip().lower()


# Kept as a module attribute because callers import it directly.
LABEL = SIMULATED_LABEL


@dataclass(frozen=True)
class Settlement:
    initiated: bool
    reference: str
    confirmed: bool
    detail: str
    simulated: bool = True
    # A page the payer can actually open. Empty in simulated mode, where there
    # is nothing to open.
    checkout_url: str = ""


def self_confirming() -> bool:
    """Whether this rail reports its own completion, having no provider to wait for.

    True only for the simulated payout. Every rail with something real behind
    it — a payment link, a RazorpayX payout — leaves here unconfirmed and is
    settled later by a webhook, because a rail that reports its own success is
    the exact failure this lane is built to avoid.
    """
    return rail() == "payout-simulated"


def initiate(*, payment_id: str, amount_inr: int, payee_ref: str,
             payee_label: str = "", bill_id: str = "",
             payee_vpa: str = "") -> Settlement:
    """Hand the payment to the rail. Returns INITIATED, never confirmed.

    Nothing here takes a banking credential; the API keys authorise our own
    account and nothing else.

    On the collecting rails the destination arrives as a REFERENCE rather than
    an address, because asking somebody to pay does not require knowing where
    the money finally lands. A payout does — pushing money somewhere means
    naming the somewhere — so `payee_vpa` is passed for that rail alone and
    ignored by the others.

    That is not a loosening of the rule that matters. The address still comes
    from the mandate and never from a bill, and the payee guard has already
    passed by the time anything here runs. What changed is only that the last
    mile can no longer pretend not to know.
    """
    mode = _mode()
    if mode == "off":
        return Settlement(initiated=False, reference="", confirmed=False,
                          detail="payment is switched off on this deployment")

    if mode == "razorpay":
        from anbu_care.payments import providers

        result = providers.create_order(
            payment_id=payment_id, amount_inr=amount_inr,
            payee_label=payee_label, bill_id=bill_id)
        if not result.ok:
            # A provider that refuses is a refusal, not a silent fall back to
            # pretending. The payment stays uninitiated and somebody is told.
            return Settlement(initiated=False, reference="", confirmed=False,
                              detail=result.detail, simulated=False)
        return Settlement(initiated=True, reference=result.reference,
                          confirmed=False,
                          detail=f"{result.detail}. {TEST_MODE_LABEL}",
                          simulated=False, checkout_url=result.checkout_url)

    if mode == "razorpayx":
        from anbu_care.payments import providers

        if providers.x_configured():
            result = providers.create_payout(
                payment_id=payment_id, amount_inr=amount_inr,
                payee_vpa=payee_vpa, payee_label=payee_label, bill_id=bill_id)
            if not result.ok:
                return Settlement(initiated=False, reference="", confirmed=False,
                                  detail=result.detail, simulated=False)
            return Settlement(initiated=True, reference=result.reference,
                              confirmed=False,
                              detail=f"{result.detail}. {RAZORPAYX_LABEL}",
                              simulated=False)
        # Configured to use RazorpayX without the keys to do it. Falls through
        # to the simulated payout, which says what it is.

    if mode in {"payout", "razorpayx"}:
        # No checkout_url, deliberately. There is nobody to hand a page to:
        # a payout is pushed, and the absence of a link is the difference
        # between this rail and the collection one, not an omission.
        return Settlement(
            initiated=True,
            reference=f"payout-{payment_id}",
            confirmed=False,
            detail=PAYOUT_LABEL,
        )

    return Settlement(
        initiated=True,
        reference=f"sim-{payment_id}",
        confirmed=False,
        detail=SIMULATED_LABEL,
    )


def confirmation_for(reference: str) -> Settlement:
    """The confirmation signal, when one actually arrives.

    In this build it is produced by an explicit call — the simulated rail
    reporting back — and never by the code path that initiated the payment.
    That separation is the point: an initiated payment that is never confirmed
    stays unconfirmed forever, and shows in the money view as initiated rather
    than as paid.
    """
    if not reference:
        return Settlement(initiated=False, reference="", confirmed=False,
                          detail="no settlement reference to confirm")
    if reference.startswith("pout_"):
        # A RazorpayX payout id. Confirmation for it arrives by webhook; this
        # only describes what having one means.
        return Settlement(initiated=True, reference=reference, confirmed=True,
                          simulated=False,
                          detail="the payout was reported processed. " + RAZORPAYX_LABEL)
    if reference.startswith("payout-"):
        return Settlement(initiated=True, reference=reference, confirmed=True,
                          simulated=True,
                          detail="the payout was reported complete. " + PAYOUT_LABEL)
    simulated = reference.startswith("sim-")
    return Settlement(initiated=True, reference=reference, confirmed=True,
                      simulated=simulated,
                      detail=("simulated settlement confirmed. " + SIMULATED_LABEL)
                      if simulated else
                      ("the provider reported this captured. " + TEST_MODE_LABEL))
