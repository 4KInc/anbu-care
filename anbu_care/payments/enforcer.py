"""The deterministic choke point every payment passes through.

This module is the one approved addition to the guarantee layer, and it is
built on the same principle as the severity table and the comms gate: the
model reads, code decides. Gemini extracts an amount off a photograph. Whether
that amount is paid, and above all WHERE it goes, is decided here, by code that
cannot be talked round.

Two structural facts do most of the work:

**The destination is assigned, never compared.** Step 6 of the check order
reads `payee = mandate.payee_vpa`. It does not check the bill's payee against
the mandate's and proceed if they match — it takes the mandate's and discards
whatever the extraction thought. A bill can propose an amount. It can never
propose a destination. If an extraction proposed one anyway, that is treated as
evidence something is wrong with the bill, not as an instruction.

**Settlement is private to this module.** `_settle` is not exported, is not
reachable from `anbu_care.agents` or `anbu_care.tools`, and is called from
exactly one place: the end of `decide`, after every check has passed. No model
output can reach it. A test asserts this by import graph rather than by
convention, because a convention is not a guarantee.

Nothing here holds a banking credential. There is no field one could live in.
"""

from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from anbu_care.money import group
from anbu_care.schemas import PaymentMandate

# ---- anomaly thresholds ---------------------------------------------------
#
# One table, module level, so they can be read and argued with rather than
# discovered. These are code. No prompt can move them.
SPIKE_FACTOR = 3.0            # amount vs the running mean of prior bills
SPIKE_NEEDS_PRIORS = 2        # a mean of one number is not a mean
BURST_WINDOW_HOURS = 6        # how far back "unattended just now" reaches
BURST_FRACTION = 0.5          # of the total authority, spent unattended, in that window
BURST_COUNT = 10              # a backstop against a lane paying in a loop
NEAR_CAP_FRACTION = 0.90      # sitting just under a cap is a signature
LATE_WINDOW_FRACTION = 0.90   # the last tenth of the mandate window


@dataclass(frozen=True)
class Decision:
    """Pay or refuse, and the reasoning, in a form a person can read.

    A refusal that does not say which check failed is nearly useless to a
    family: "it did not pay" and "it did not pay because the amount was above
    the cap you set" are different messages, and only one of them tells them
    what to do next.
    """

    pay: bool
    guards_passed: list[str] = field(default_factory=list)
    failed_check: str = ""
    reason: str = ""
    amount_inr: int = 0
    payee_vpa: str = ""
    payee_ref: str = ""


def payee_ref(vpa: str) -> str:
    """A stable reference to a destination that is not a destination.

    Enough to prove two payments went to the same place. Not enough to send
    money anywhere, which is why this and not the raw VPA goes on the chain.
    """
    return hashlib.sha256(vpa.strip().lower().encode()).hexdigest()[:16]


def _aware(moment: datetime) -> datetime:
    """A stored timestamp that lost its tzinfo is still UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _hours_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


# Words that carry no identity. "Sacred Heart Hospital" and "Sacred Heart
# Hospitals Pvt Ltd" are the same place; "Sundaram Arulrhaj Hospitals" is not.
_VENDOR_NOISE = {"hospital", "hospitals", "clinic", "centre", "center", "medical",
                 "pvt", "private", "ltd", "limited", "the", "and", "institute",
                 "healthcare", "health", "care", "multispeciality", "speciality"}


def _identity_tokens(name: str) -> set[str]:
    cleaned = "".join(c if c.isalnum() else " " for c in (name or "").lower())
    return {t for t in cleaned.split() if len(t) > 2 and t not in _VENDOR_NOISE}


def _anomalies(amount: int, mandate: PaymentMandate, history: list,
               now: datetime, extracted_payee: str | None,
               extracted_vendor: str | None = None) -> list[str]:
    """Deterministic signals. Any one of these refuses an in-cap payment.

    The point is not to catch fraud. It is that routine bills should flow
    without waking anyone, and unusual ones should stop — and that "unusual"
    has to mean something a person can check afterwards.
    """
    found: list[str] = []

    priors = [p.amount_inr for p in history]
    if len(priors) >= SPIKE_NEEDS_PRIORS:
        mean = statistics.fmean(priors)
        if mean > 0 and amount > SPIKE_FACTOR * mean:
            found.append(
                f"amount_spike: INR {group(amount)} is more than {SPIKE_FACTOR:g}x the "
                f"INR {mean:,.0f} average of {len(priors)} earlier bills")

    # NOT used as a destination. Used as evidence that this bill is not what it
    # should be, which is a different and much safer role for the same string.
    #
    # Only a string that LOOKS LIKE A DESTINATION counts. Every real bill prints
    # a hospital name in this field, and comparing a name to a VPA never matches
    # — which would escalate every legitimate bill and make the feature useless.
    # A bill printing "Sacred Heart Hospital" is normal. A bill printing
    # "pay to attacker@okaxis" is the attack, and that is what this catches.
    claimed = (extracted_payee or "").strip()
    looks_payable = "@" in claimed or claimed.lower().startswith("upi:")
    if looks_payable and claimed.lower() != mandate.payee_vpa.strip().lower():
        found.append(
            "payee_mismatch: the bill named a different payee from the one on "
            "the mandate. The mandate's payee is the only destination this "
            "system will ever use, and a bill disagreeing with it is a reason "
            "to stop rather than a reason to switch")

    # Only AUTONOMOUS payments. This signal exists to catch the agent being
    # drained while nobody is watching, and a payment a person approved means
    # somebody was watching minutes ago. Counting those made an ordinary second
    # bill suspicious purely because its owner had just dealt with the first.
    #
    # And it measures MONEY, not events. It used to refuse any second automatic
    # payment inside the window, which is not what draining looks like: two
    # small pharmacy bills an hour apart drain nothing, while the second real
    # bill of any hospital stay was made to wait six hours for no reason a
    # family could follow. A stay bills more than once a day; that is the
    # normal case, not the suspicious one.
    #
    # What is worth stopping is the authority going quickly while nobody looks.
    # So: how much of the total the family granted has been spent unattended in
    # the window, including this bill. Half of it in six hours is a thing a
    # person should see before it becomes all of it.
    unattended = [p for p in history if getattr(p, "autonomous", True)]
    recent = [p for p in unattended
              if 0 <= _hours_between(now, _aware(p.initiated_at)) < BURST_WINDOW_HOURS]
    if recent:
        spent = sum(p.amount_inr for p in recent) + amount
        ceiling = BURST_FRACTION * mandate.total_cap_inr
        if mandate.total_cap_inr > 0 and spent > ceiling:
            found.append(
                f"burst: INR {group(spent)} would be paid automatically within "
                f"{BURST_WINDOW_HOURS}h, over {BURST_FRACTION:.0%} of the INR "
                f"{group(mandate.total_cap_inr)} you authorised in total")
        elif len(recent) + 1 > BURST_COUNT:
            # Amounts can be small enough to stay under the fraction for ever.
            # No stay bills eleven times in six hours; a lane in a loop does.
            found.append(
                f"burst: this would be automatic payment number {len(recent) + 1} "
                f"within {BURST_WINDOW_HOURS}h, which is more than a billing "
                f"cycle produces")

    # A bill from a DIFFERENT HOSPITAL. The destination is locked either way, so
    # this is not a redirection risk — it is worse in a quieter way: paying the
    # authorised hospital for another hospital's bill leaves the family still
    # owing the one that sent it, and having spent the authority meant for this
    # admission. Found live: a Sundaram Arulrhaj bill was paid to Sacred Heart.
    theirs = _identity_tokens(extracted_vendor)
    ours = _identity_tokens(mandate.payee_label)
    if theirs and ours and not (theirs & ours):
        found.append(
            f"vendor_mismatch: the bill is from {extracted_vendor}, and the "
            f"authority was granted for {mandate.payee_label}. Paying it would "
            f"send money to the right account for the wrong hospital's bill")

    if amount >= NEAR_CAP_FRACTION * mandate.per_bill_cap_inr:
        found.append(
            f"near_cap: INR {group(amount)} is at or above "
            f"{NEAR_CAP_FRACTION:.0%} of the per-bill cap")

    span = (mandate.window_closes_at - mandate.window_opens_at).total_seconds()
    if span > 0:
        elapsed = (now - mandate.window_opens_at).total_seconds() / span
        if elapsed >= LATE_WINDOW_FRACTION:
            found.append(
                "late_window: the bill arrived in the last tenth of the "
                "authorised window, when nobody is likely to be watching")

    return found


def decide(*, bill_id: str, case_id: str, amount_inr: int,
           mandate: PaymentMandate | None, history: list,
           now: datetime | None = None,
           extracted_payee: str | None = None,
           extracted_vendor: str | None = None) -> Decision:
    """Should this bill be paid autonomously, and where would it go.

    Every check must pass. The order is deliberate: revocation and case scope
    are absolute and cheap, so they run first and nothing after them can
    resurrect a dead mandate.

    `history` is the payments already initiated on this case.
    """
    now = now or datetime.now(UTC)
    passed: list[str] = []

    def refuse(check: str, reason: str) -> Decision:
        return Decision(pay=False, guards_passed=passed, failed_check=check,
                        reason=reason, amount_inr=amount_inr)

    # 1 — a mandate at all
    if mandate is None:
        return refuse("mandate_present",
                      "no payment mandate has been granted for this case, so "
                      "nothing is paid without someone approving it")
    if not mandate.is_live:
        return refuse("mandate_live",
                      "the mandate was revoked, which stops every further "
                      "automatic payment immediately")
    passed.append("mandate_live")

    # 2 — inside the authorised window
    opens, closes = mandate.window_opens_at, mandate.window_closes_at
    if opens.tzinfo is None:
        opens = opens.replace(tzinfo=UTC)
    if closes.tzinfo is None:
        closes = closes.replace(tzinfo=UTC)
    if not (opens <= now <= closes):
        return refuse("within_window",
                      f"the authorised window ran from {opens:%d %b %H:%M} to "
                      f"{closes:%d %b %H:%M} and it is now outside it")
    passed.append("within_window")

    # 3 — this admission, not another
    if case_id != mandate.case_id:
        return refuse("case_scope",
                      "the mandate was granted for a different admission")
    passed.append("case_scope")

    # 4 — paid at most once, ever
    if any(p.bill_id == bill_id for p in history):
        return refuse("not_duplicate",
                      f"bill {bill_id} has already been paid on this case")
    passed.append("not_duplicate")

    # 5 — a real, capped amount
    if amount_inr <= 0:
        return refuse("amount_positive", "the amount read off the bill was not positive")
    if amount_inr > mandate.per_bill_cap_inr:
        return refuse("per_bill_cap",
                      f"INR {group(amount_inr)} is above the per-bill cap of "
                      f"INR {group(mandate.per_bill_cap_inr)}")
    passed.append("per_bill_cap")

    # 6 — the grant behind a standing copy is still standing
    #
    # An adopted copy is a copy, and a copy can outlive the thing it came from.
    # Revoking the standing grant has to stop the admissions already carrying
    # it in the same act, or "revoke" means "revoke, and also go and find every
    # case that inherited it", which nobody will do at 3am.
    if mandate.standing_id:
        from anbu_care.payments import mandate as mandates

        behind = mandates.live_standing_for(mandate.parent_id)
        if behind is None or behind.mandate_id != mandate.standing_id:
            return refuse("standing_live",
                          "the standing authority this admission was paying "
                          "under has been withdrawn")
        passed.append("standing_live")

    # 7 — a capped total, counted across everything the grant covers
    #
    # For a standing grant this counts EVERY admission it covers, not just this
    # one. The alternative - each case starting fresh at the full cap - turns
    # one authorisation into as many as there are admissions, and a family that
    # authorised INR 400,000 would find INR 1,200,000 gone across three of them
    # with every individual decision looking correct.
    spent = sum(p.amount_inr for p in _history_under(mandate, history))
    if spent + amount_inr > mandate.total_cap_inr:
        return refuse("total_cap",
                      f"INR {group(spent)} has been paid already and INR "
                      f"{group(amount_inr)} more would pass the total cap of "
                      f"INR {group(mandate.total_cap_inr)}"
                      + (" authorised across every admission this standing "
                         "authority covers" if mandate.standing_id else ""))
    passed.append("total_cap")

    # 8 — the destination. ASSIGNED from the mandate, never taken from the bill.
    payee = mandate.payee_vpa
    passed.append("payee_from_mandate")

    # 9 — nothing unusual about this bill
    # The same widening as the total cap, for the same reason: a burst is a
    # fraction of the authority the family granted, and under a standing
    # grant that authority is not this admission's alone.
    anomalies = _anomalies(amount_inr, mandate, _history_under(mandate, history),
                           now, extracted_payee, extracted_vendor)
    if anomalies:
        return refuse("no_anomaly", "; ".join(anomalies))
    passed.append("no_anomaly")

    return Decision(pay=True, guards_passed=passed, amount_inr=amount_inr,
                    payee_vpa=payee, payee_ref=payee_ref(payee))


def upi_intent(*, payee_vpa: str, payee_label: str, amount_inr: int,
               note: str) -> str:
    """A real UPI intent URI. Opens a UPI app with payee and amount filled in.

    Genuinely valid — this is the string a QR would encode. It initiates
    nothing on its own: a UPI app opens, and a human authenticates there. That
    is the whole point of handing back an intent rather than moving money.
    """
    from urllib.parse import quote

    return (f"upi://pay?pa={quote(payee_vpa)}&pn={quote(payee_label)}"
            f"&am={amount_inr}&cu=INR&tn={quote(note[:50])}")


def _history_under(mandate: PaymentMandate, history: list) -> list:
    """The payments the total cap is measured against.

    For an admission-scoped mandate that is this admission's payments, which is
    what `history` already holds. For a copy adopted from a standing grant it is
    every admission that adopted the same grant - the cap is a ceiling on the
    money, not on the money per episode.
    """
    if not mandate.standing_id:
        return history

    from anbu_care import service

    seen = {p.payment_id for p in history}
    everything = list(history)
    for case_id in service.cases_adopting(mandate.standing_id):
        if case_id == mandate.case_id:
            continue
        for payment in service.list_payments(case_id):
            if payment.payment_id not in seen:
                seen.add(payment.payment_id)
                everything.append(payment)
    return everything
