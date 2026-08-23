"""Interim bill payment.

The model reads a bill. Deterministic code decides whether paying it is inside
the authority a human granted, and above all where the money would go — which
is never anywhere a bill proposed.

`settlement` is deliberately NOT re-exported here. It is reachable only through
`run`, which calls it in one place after the enforcer has passed every check.
"""

from anbu_care.payments.enforcer import Decision, decide, payee_ref, upi_intent
from anbu_care.payments.mandate import MandateRejected, grant, live_for_case, revoke
from anbu_care.payments.run import (
    PaymentRefused,
    approve_escalated,
    confirm,
    consider_bill,
    money_view,
)

__all__ = ["Decision", "MandateRejected", "PaymentRefused", "approve_escalated",
           "confirm", "consider_bill", "decide", "grant", "live_for_case",
           "money_view", "payee_ref", "revoke", "upi_intent"]
