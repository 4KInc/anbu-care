"""Diagnostic referral: where a clinician-ordered test can be done.

Options, from a real search, with honest labels. Not an order, not a booking,
and not a promise about coverage.
"""

from anbu_care.diagnostics.referral import (
    ReferralRefused,
    group_by_mobility,
    options_for,
    record,
)

__all__ = ["ReferralRefused", "group_by_mobility", "options_for", "record"]
