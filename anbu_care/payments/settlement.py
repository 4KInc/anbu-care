"""Settlement. Simulated, and labelled everywhere it surfaces.

Real autonomous UPI debit needs a licensed payment provider plus UPI Autopay
or e-mandate rails under NPCI. That is out of scope in this window, and
pretending otherwise would be the one dishonesty this project has avoided
everywhere else — the TPA is simulated and says so, the coverage figure is an
estimate and says so, and this is the same.

What is real: the mandate, the envelope, the payee lock, idempotency, the
anomaly step-up, the receipts. What is simulated: the movement of money.

`initiate` deliberately returns UNCONFIRMED. Confirmation is a separate act,
because a system that assumes its own success reports money as paid that is
not — the same reason `comms.sent` is a different receipt from
`comms.not_delivered`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

LABEL = ("Settlement simulated. Production integrates a licensed payment "
         "provider; no money moved.")


@dataclass(frozen=True)
class Settlement:
    initiated: bool
    reference: str
    confirmed: bool
    detail: str
    simulated: bool = True


def initiate(*, payment_id: str, amount_inr: int, payee_ref: str) -> Settlement:
    """Hand the payment to the rail. Returns INITIATED, never confirmed.

    Nothing here takes a credential, and there is no parameter one could be
    passed in. The destination arrives as a reference, not as an address,
    because this function does not need to know where money goes in order to
    model that it was asked to send it.
    """
    if os.getenv("ANBU_PAYMENT_MODE", "simulated").strip().lower() == "off":
        return Settlement(initiated=False, reference="", confirmed=False,
                          detail="payment is switched off on this deployment")

    return Settlement(
        initiated=True,
        reference=f"sim-{payment_id}",
        confirmed=False,
        detail=LABEL,
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
    return Settlement(initiated=True, reference=reference, confirmed=True,
                      detail=f"simulated settlement confirmed. {LABEL}")
