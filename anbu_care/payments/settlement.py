"""Settlement. A real provider in test mode, or a stub, and it says which.

`ANBU_PAYMENT_MODE` picks:

  razorpay   a real API call to Razorpay in TEST MODE. Real order, real
             identifier, real webhook. Test-mode money, which does not exist.
  simulated  no call at all. The fallback when no keys are configured.
  off        refuse to do anything.

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
TEST_MODE_LABEL = ("Razorpay in test mode. The order is real; the money is not. "
                   "Going live needs a registered merchant account.")

# What the surfaces show. Read at call time so a deployment without keys says
# the honest thing rather than the configured thing.
def label() -> str:
    from anbu_care.payments import providers

    return TEST_MODE_LABEL if _mode() == "razorpay" and providers.configured() \
        else SIMULATED_LABEL


def rail() -> str:
    """Which rail actually carried this, for the record and the trace.

    A receipt saying "simulated" for a real Razorpay order is the same class of
    untruth as calling an unconfirmed payment paid, and it was hardcoded in
    three places before a real provider existed to contradict it.
    """
    from anbu_care.payments import providers

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


def initiate(*, payment_id: str, amount_inr: int, payee_ref: str,
             payee_label: str = "", bill_id: str = "") -> Settlement:
    """Hand the payment to the rail. Returns INITIATED, never confirmed.

    Nothing here takes a credential, and there is no parameter one could be
    passed in. The destination arrives as a reference, not as an address,
    because this function does not need to know where money goes in order to
    model that it was asked to send it.
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
    simulated = reference.startswith("sim-")
    return Settlement(initiated=True, reference=reference, confirmed=True,
                      simulated=simulated,
                      detail=("simulated settlement confirmed. " + SIMULATED_LABEL)
                      if simulated else
                      ("the provider reported this captured. " + TEST_MODE_LABEL))
