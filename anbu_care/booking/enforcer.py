"""Whether holding this appointment, at this centre, is inside the authority.

Deterministic, ordered, and every refusal names the check that caused it. The
same discipline as `payments/enforcer.py`, and for a sharper reason: this is the
first lane where being wrong reaches a third party who never agreed to any of
this. A wrong payment can be refunded. A wrong booking wastes a real clinic's
slot and sends a seventy-one year old across a city for a test she did not need,
under her own name.

Two of the eleven carry most of the weight, and both are worth reading before
changing anything here.

`centre_from_options` IS `payee_from_mandate`, pointed at a different noun. The
single most important guard in the payment lane is that a bill can never set
where money goes. The identical failure here is a WEB PAGE setting where she
goes: an interstitial offering "book at our partner centre instead", a redirect,
a sponsored result read as an answer. The centre is chosen from the ranked list
this system produced from its own search, and a page may only be used to fill in
that choice. A centre that appears on the page and not in the options is
refused, always, however plausible it looks.

`cancellable` refuses to book anywhere it cannot unbook. An agent that can
create an obligation and cannot undo it is worse than one that does nothing, and
this is the cheapest available insurance against every other guard in this file
being wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from anbu_care.diagnostics import referral

# A slot that must be paid for is not this lane's to take. Recognised as a set
# of signals rather than a judgement, matching the anomaly guard next door.
PAYMENT_SIGNALS = ("pay now", "payment", "prepaid", "card number", "upi id",
                   "advance payment", "pay to confirm", "razorpay", "checkout")


@dataclass
class Decision:
    """Whether to go ahead, and exactly what stopped it if not."""

    allowed: bool
    passed: list[str] = field(default_factory=list)
    failed_check: str = ""
    reason: str = ""


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def decide(*, order, mandate, centre: dict, options: list[dict],
           existing: list, case_id: str, cancel_url: str = "",
           cancel_phone: str = "", page_text: str = "",
           payload: dict | None = None,
           now: datetime | None = None) -> Decision:
    """Run every guard in order. The first refusal is the answer."""
    passed: list[str] = []
    moment = now or datetime.now(UTC)

    def refuse(check: str, reason: str) -> Decision:
        return Decision(allowed=False, passed=passed, failed_check=check,
                        reason=reason)

    # 1 — a clinician ordered this
    #
    # First, because everything after it is downstream of an order existing. The
    # diagnostics lane has never originated one and this does not either: a
    # booking with no order behind it would be Anbu Care deciding she needs a
    # test.
    if order is None or not (order.test_label or "").strip():
        return refuse("order_live",
                      "no clinician has ordered a test on this admission")
    passed.append("order_live")

    # 2 — the family granted booking authority
    if mandate is None or not mandate.is_live:
        return refuse("mandate_live",
                      "nobody has authorised Anbu Care to hold an appointment")
    passed.append("mandate_live")

    # 3 — inside the window
    opens, closes = _aware(mandate.window_opens_at), _aware(mandate.window_closes_at)
    if not (opens <= moment <= closes):
        return refuse("within_window",
                      f"the authorised window ran from {opens:%d %b %H:%M} to "
                      f"{closes:%d %b %H:%M} and it is now outside it")
    passed.append("within_window")

    # 4 — the grant behind an adopted copy is still standing
    if mandate.standing_id:
        from anbu_care.booking import mandate as mandates

        behind = mandates.live_standing_for(mandate.parent_id)
        if behind is None or behind.mandate_id != mandate.standing_id:
            return refuse("standing_live",
                          "the standing booking authority this admission was "
                          "working under has been withdrawn")
        passed.append("standing_live")

    # 5 — this admission, not another
    if order.case_id != case_id or (mandate.case_id and mandate.case_id != case_id):
        return refuse("case_scope",
                      "the order or the authority belongs to a different admission")
    passed.append("case_scope")

    # 6 — one appointment per order, ever
    #
    # This lane's version of paying the same bill twice, except the injured
    # party is a clinic that never agreed to any of this. Keyed on the ORDER,
    # not the attempt, so a retry after a timeout cannot become a second slot.
    live = [a for a in existing
            if a.order_id == order.order_id
            and a.status in {"requested", "confirmed"}
            and a.cancelled_at is None]
    if live:
        return refuse("not_duplicate",
                      f"this test already has an appointment ({live[0].status}) "
                      f"and booking a second would take a slot somebody else "
                      f"needs")
    passed.append("not_duplicate")

    # 7 — the centre came from OUR search, not from a page
    place_id = str((centre or {}).get("place_id") or "")
    if not place_id or not any(o.get("place_id") == place_id for o in options):
        return refuse("centre_from_options",
                      "that centre is not one of the options this system found. "
                      "A page cannot choose where she goes")
    passed.append("centre_from_options")

    # 8 — far enough is too far
    distance = float((centre or {}).get("distance_km") or 0.0)
    if distance > mandate.max_distance_km:
        return refuse("within_distance",
                      f"{distance:.1f} km is beyond the {mandate.max_distance_km:.0f} km "
                      f"the family authorised")
    passed.append("within_distance")

    # 9 — mobility, as the clinician left it
    #
    # Never inferred. If they recorded that she cannot travel, a travel booking
    # is refused rather than being quietly softened into "probably fine".
    home = bool((centre or {}).get("home_collection"))
    if (order.mobility == referral.NON_AMBULATORY or mandate.home_collection_only) \
            and not home:
        return refuse("mobility_ok",
                      "the clinician recorded that she cannot travel to a "
                      "centre, and this one is not listed for home collection")
    passed.append("mobility_ok")

    # 10 — do not book somewhere you cannot unbook
    if mandate.requires_cancellable and not (cancel_url or cancel_phone):
        return refuse("cancellable",
                      "no way to cancel was found before committing, and an "
                      "appointment that cannot be withdrawn is not one this "
                      "system will make")
    passed.append("cancellable")

    # 11 — booking never becomes spending
    #
    # The payment lane has its own mandate, its own guards and its own
    # destination lock. A browser session filling in a card field would route
    # around every one of them, so this stops and asks instead.
    lowered = (page_text or "").lower()
    hit = next((s for s in PAYMENT_SIGNALS if s in lowered), "")
    if hit:
        return refuse("no_payment",
                      f"this centre wants payment to confirm ({hit!r}), and "
                      f"booking carries no authority to spend")
    passed.append("no_payment")

    # 12 — nothing leaves that may not
    #
    # Last, so it guards the payload that is actually about to be sent rather
    # than an earlier one somebody assembled.
    if payload is not None:
        from anbu_care.booking import disclosure

        try:
            disclosure.check(payload)
        except disclosure.DisclosureRefused as refused:
            return refuse("disclosure_minimal", str(refused))
    passed.append("disclosure_minimal")

    return Decision(allowed=True, passed=passed)
