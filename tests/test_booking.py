"""Booking: what the agent is allowed to do on her behalf, and to whom.

This is the first lane where being wrong reaches a third party who never agreed
to any of it. A wrong payment can be refunded; a wrong booking wastes a real
clinic's slot and sends a seventy-one year old across a city under her own name.

So, like the payment tests, almost everything below is about a REFUSAL. The two
that matter most are `test_a_page_can_never_choose_where_she_goes` — which is
`test_a_bill_can_never_set_the_destination` pointed at a different noun — and
`test_it_will_not_book_anywhere_it_cannot_unbook`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anbu_care import service
from anbu_care.booking import channels, disclosure, enforcer
from anbu_care.booking import mandate as mandates
from anbu_care.booking import run
from anbu_care.diagnostics import referral
from anbu_care.schemas import Appointment, DiagnosticOrder
from anbu_care.tools import onboarding_tools, triage_tools

NEAR = {"place_id": "place-near", "name": "Anbu Diagnostics", "distance_km": 2.1,
        "score": 0.71, "home_collection": False, "address": "Palayamkottai Rd"}
FAR = {"place_id": "place-far", "name": "Far Labs", "distance_km": 21.0,
       "score": 0.95, "home_collection": False, "address": "Tirunelveli"}
HOME = {"place_id": "place-home", "name": "Home Collect Labs", "distance_km": 4.0,
        "score": 0.60, "home_collection": True, "address": "Bryant Nagar"}
OFF_LIST = {"place_id": "place-sponsored", "name": "Partner Centre",
            "distance_km": 1.0, "score": 0.99, "home_collection": False}


@pytest.fixture
def case():
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    onboarding_tools.record_booking_disclosure_consent(pid)
    cid = triage_tools.run_triage(
        parent_id=pid, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]
    return pid, cid


def _order(parent_id, case_id, *, mobility="unknown", options=None):
    order = DiagnosticOrder(
        order_id=service.new_id("dxorder"), case_id=case_id, parent_id=parent_id,
        test_label="blood test", mobility=mobility,
        ordered_by="the treating team (unverified)",
        options=options if options is not None else [NEAR, FAR, HOME])
    service.save_diagnostic_order(order)
    return order


def _mandate(parent_id, **kw):
    return mandates.grant_standing(parent_id=parent_id, granted_by="Heartlin", **kw)


class Landing:
    """A driver that prepares, then takes the request, with a way to cancel."""

    name = "test-web"

    def __init__(self, outcome=channels.REQUESTED, cancel_url="https://x/cancel",
                 cancel_phone="", page_text=""):
        self.outcome, self.cancel_url = outcome, cancel_url
        self.cancel_phone, self.page_text = cancel_phone, page_text
        self.seen = []          # everything prepared
        self.committed = []     # everything actually sent

    def can_serve(self, centre):
        return True

    def prepare(self, *, centre, payload):
        self.seen.append((centre["place_id"], dict(payload)))
        return channels.Preparation(
            outcome=channels.READY, detail="filled", cancel_url=self.cancel_url,
            cancel_phone=self.cancel_phone, page_text=self.page_text,
            handle={"page_url": "https://x/book"})

    def commit(self, *, centre, payload, prepared, session_id="",
               otp_wait_seconds=0):
        self.committed.append((centre["place_id"], dict(payload)))
        self.otp_asked = bool(otp_wait_seconds)
        return channels.AttemptResult(
            outcome=self.outcome, detail="stub", cancel_url=prepared.cancel_url,
            cancel_phone=prepared.cancel_phone)


class Failing(Landing):
    """A driver that cannot serve anything, so the lane must fall through."""

    name = "test-failing"

    def prepare(self, *, centre, payload):
        self.seen.append((centre["place_id"], dict(payload)))
        return channels.Preparation(outcome=channels.UNAVAILABLE,
                                    detail="this centre has no online form")


class Exploding(Landing):
    """A driver that raises. Must never reach a person waiting on WhatsApp."""

    name = "test-exploding"

    def prepare(self, *, centre, payload):
        self.seen.append((centre["place_id"], dict(payload)))
        raise RuntimeError("chromium died")


def _drive(monkeypatch, driver):
    monkeypatch.setattr(channels, "available", lambda: [driver])
    monkeypatch.setattr(run.channel_registry, "available", lambda: [driver])


# =========================================================================
# THE PAGE NEVER CHOOSES
# =========================================================================


def test_a_page_can_never_choose_where_she_goes(case):
    """The one that matters most, and it is `payee_from_mandate` again.

    An interstitial offering "book at our partner centre instead", a redirect, a
    sponsored result read as an answer. The centre comes from the ranked list
    this system produced from its own search; a page may only fill in that
    choice.
    """
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    mandate = _mandate(parent_id)

    verdict = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id) or mandate,
        centre=OFF_LIST, options=order.options, existing=[], case_id=case_id,
        cancel_url="https://x/cancel")

    assert verdict.allowed is False
    assert verdict.failed_check == "centre_from_options"
    assert "cannot choose where she goes" in verdict.reason


def test_the_enforcer_matches_on_place_id_not_on_a_name(case):
    """A name is a string a page can print. A place id is ours."""
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    _mandate(parent_id)
    impostor = {**OFF_LIST, "name": NEAR["name"]}

    verdict = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id), centre=impostor,
        options=order.options, existing=[], case_id=case_id,
        cancel_url="https://x/cancel")
    assert verdict.failed_check == "centre_from_options"


# =========================================================================
# THE REFUSALS
# =========================================================================


def test_it_will_not_book_anywhere_it_cannot_unbook(case):
    """An agent that can create an obligation and cannot undo it is worse than
    one that does nothing."""
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    _mandate(parent_id)

    verdict = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id), centre=NEAR,
        options=order.options, existing=[], case_id=case_id)

    assert verdict.failed_check == "cancellable"
    assert "cannot be withdrawn" in verdict.reason


def test_booking_never_becomes_spending(case):
    """The payment lane has its own mandate, its own guards and its own
    destination lock. A browser filling a card field routes around all of it."""
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    _mandate(parent_id)

    verdict = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id), centre=NEAR,
        options=order.options, existing=[], case_id=case_id,
        cancel_url="https://x/cancel",
        page_text="Please pay now to confirm your slot")

    assert verdict.failed_check == "no_payment"
    assert "no authority to spend" in verdict.reason


def test_the_booking_mandate_has_nowhere_a_cap_could_live():
    """Not a promise about behaviour - a fact about the schema."""
    from anbu_care.schemas import BookingMandate

    fields = set(BookingMandate.model_fields)
    for money in ("per_bill_cap_inr", "total_cap_inr", "cap_inr", "amount_inr",
                  "payee_vpa", "method_ref"):
        assert money not in fields, f"the booking mandate grew {money}"


def test_one_order_never_gets_two_appointments(case):
    """This lane's version of paying the same bill twice, except the injured
    party is a clinic that never agreed to any of this."""
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    _mandate(parent_id)
    existing = [Appointment(
        appointment_id="appt-1", case_id=case_id, parent_id=parent_id,
        order_id=order.order_id, status="requested", place_id=NEAR["place_id"])]

    verdict = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id), centre=NEAR,
        options=order.options, existing=existing, case_id=case_id,
        cancel_url="https://x/cancel")

    assert verdict.failed_check == "not_duplicate"
    assert "somebody else needs" in verdict.reason


def test_a_cancelled_appointment_does_not_block_a_new_one(case):
    """Withdrawing one has to leave the test bookable again."""
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    _mandate(parent_id)
    existing = [Appointment(
        appointment_id="appt-1", case_id=case_id, parent_id=parent_id,
        order_id=order.order_id, status="cancelled", place_id=NEAR["place_id"],
        cancelled_at=datetime.now(UTC))]

    verdict = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id), centre=NEAR,
        options=order.options, existing=existing, case_id=case_id,
        cancel_url="https://x/cancel")
    assert verdict.allowed is True


def test_nothing_is_booked_without_an_order_from_a_clinician(case):
    """Anbu Care has never originated an order and this does not either."""
    parent_id, case_id = case
    _mandate(parent_id)

    verdict = enforcer.decide(
        order=None, mandate=mandates.live_for_case(case_id), centre=NEAR,
        options=[NEAR], existing=[], case_id=case_id, cancel_url="https://x/c")
    assert verdict.failed_check == "order_live"


def test_a_clinician_who_said_she_cannot_travel_is_not_second_guessed(case):
    """Never inferred, and never quietly softened into "probably fine"."""
    parent_id, case_id = case
    order = _order(parent_id, case_id, mobility=referral.NON_AMBULATORY)
    _mandate(parent_id)

    travel = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id), centre=NEAR,
        options=order.options, existing=[], case_id=case_id,
        cancel_url="https://x/c")
    assert travel.failed_check == "mobility_ok"

    home = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id), centre=HOME,
        options=order.options, existing=[], case_id=case_id,
        cancel_url="https://x/c")
    assert home.allowed is True


def test_a_centre_beyond_the_authorised_distance_is_refused(case):
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    _mandate(parent_id, max_distance_km=15.0)

    verdict = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id), centre=FAR,
        options=order.options, existing=[], case_id=case_id,
        cancel_url="https://x/c")
    assert verdict.failed_check == "within_distance"


def test_revoking_the_standing_grant_stops_admissions_carrying_it(case):
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    _mandate(parent_id)
    adopted = mandates.live_for_case(case_id)
    assert adopted is not None

    mandates.revoke_standing(parent_id, revoked_by="Heartlin")
    verdict = enforcer.decide(
        order=order, mandate=adopted, centre=NEAR, options=order.options,
        existing=[], case_id=case_id, cancel_url="https://x/c")

    assert verdict.failed_check == "standing_live"
    assert "has been withdrawn" in verdict.reason


def test_declining_it_on_one_admission_is_not_undone_by_the_next_question(case):
    parent_id, case_id = case
    _mandate(parent_id)
    assert mandates.live_for_case(case_id) is not None

    mandates.revoke(case_id, revoked_by="Heartlin")
    assert mandates.live_for_case(case_id) is None, "it re-adopted itself"


def test_an_expired_window_books_nothing(case):
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    mandate = _mandate(parent_id)
    adopted = mandates.live_for_case(case_id)

    verdict = enforcer.decide(
        order=order, mandate=adopted, centre=NEAR, options=order.options,
        existing=[], case_id=case_id, cancel_url="https://x/c",
        now=datetime.now(UTC) + timedelta(hours=1000))
    assert verdict.failed_check == "within_window"
    assert mandate.mandate_id


# =========================================================================
# WHAT LEAVES, AND WHAT NEVER DOES
# =========================================================================


def test_a_centre_is_told_the_narrowest_thing_that_does_the_job():
    payload = disclosure.payload_for(name="Ashanthi Machado", age=71,
                                     phone="+919000000000",
                                     test_label="blood test",
                                     home_collection=False)
    assert set(payload) == disclosure.ALLOWED_FIELDS


@pytest.mark.parametrize("leak", ["allergies", "conditions", "medications",
                                  "policy_number", "insurer", "case_id",
                                  "diagnosis", "dob", "son_phone"])
def test_her_record_never_leaves_with_the_booking(leak):
    """A whitelist checked against the payload, not a blacklist of things we
    remembered to strip. This fails closed."""
    with pytest.raises(disclosure.DisclosureRefused):
        disclosure.check({"name": "A", "age": "71", "phone": "+91",
                          "test_label": "blood test", "home_collection": False,
                          leak: "should never go"})


def test_a_refused_disclosure_sends_nothing_rather_than_filtering():
    """Silently dropping a field lets a caller believe it was sent and the next
    reader believe the whitelist is advisory."""
    with pytest.raises(disclosure.DisclosureRefused, match="allergies"):
        disclosure.check({"name": "A", "allergies": "Penicillin"})


def test_the_enforcer_checks_the_payload_that_is_actually_going(case):
    parent_id, case_id = case
    order = _order(parent_id, case_id)
    _mandate(parent_id)

    verdict = enforcer.decide(
        order=order, mandate=mandates.live_for_case(case_id), centre=NEAR,
        options=order.options, existing=[], case_id=case_id,
        cancel_url="https://x/c",
        payload={"name": "A", "allergies": "Penicillin"})
    assert verdict.failed_check == "disclosure_minimal"


def test_the_test_label_goes_out_exactly_as_the_clinician_wrote_it():
    """Rewriting it into something a form parses more easily would be this
    system deciding what was ordered."""
    payload = disclosure.payload_for(name="A", age=71, phone="+91",
                                     test_label="blood test",
                                     home_collection=False)
    assert payload["test_label"] == "blood test"
    assert "complete blood count" not in payload["test_label"]


# =========================================================================
# DECIDING, AND FALLING THROUGH
# =========================================================================


def test_it_chooses_and_says_why(case):
    parent_id, case_id = case
    _mandate(parent_id, prefer="nearest")
    mandate = mandates.live_for_case(case_id)

    queue = run.choose([FAR, NEAR, HOME], mandate)
    assert [c["place_id"] for c in queue] == ["place-near", "place-home"], \
        "the far centre was offered, or the order ignored the preference"
    assert "nearest" in run.why(queue[0], mandate)
    assert "no page suggested it" in run.why(queue[0], mandate)


def test_highest_score_and_nearest_are_different_choices(case):
    parent_id, case_id = case
    _mandate(parent_id, prefer="highest_score", max_distance_km=30.0)
    mandate = mandates.live_for_case(case_id)
    assert run.choose([NEAR, FAR, HOME], mandate)[0]["place_id"] == "place-far"

    mandates.revoke_standing(parent_id)
    _mandate(parent_id, prefer="nearest", max_distance_km=30.0)
    fresh = mandates.live_standing_for(parent_id)
    assert run.choose([NEAR, FAR, HOME], fresh)[0]["place_id"] == "place-near"


def test_it_tries_the_next_centre_when_one_fails(case, monkeypatch):
    """The agentic part. A lane that tries one centre and gives up is a script."""
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR, HOME])
    _mandate(parent_id, max_attempts=3)
    driver = Failing()
    _drive(monkeypatch, driver)

    out = run.arrange(case_id=case_id, order_id=order.order_id)

    assert out["outcome"] == "escalated"
    assert [p for p, _ in driver.seen] == ["place-near", "place-home"], \
        "it stopped after the first centre"
    assert len(out["attempts"]) == 2


def test_it_stops_at_the_number_of_attempts_the_family_allowed(case, monkeypatch):
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR, HOME])
    _mandate(parent_id, max_attempts=1)
    driver = Failing()
    _drive(monkeypatch, driver)

    run.arrange(case_id=case_id, order_id=order.order_id)
    assert len(driver.seen) == 1


def test_an_escalation_says_what_was_tried(case, monkeypatch):
    """The person picking this up starts where the system left off."""
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR, HOME])
    _mandate(parent_id)
    _drive(monkeypatch, Failing())

    run.arrange(case_id=case_id, order_id=order.order_id)

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "booking.escalated")
    assert receipt.payload["attempt_count"] == 2
    assert "a person needs to ring" in receipt.payload["note"]


def test_an_attempt_is_recorded_before_it_runs(case, monkeypatch):
    """An instance that dies mid-booking must leave evidence, or a retry
    double-books."""
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    _drive(monkeypatch, Failing())

    run.arrange(case_id=case_id, order_id=order.order_id)
    kinds = [r.kind for r in service.get_chain(case_id).receipts]
    assert kinds.index("booking.attempted") < kinds.index("booking.escalated")


def test_a_request_is_not_an_appointment(case, monkeypatch):
    """Same discipline as an initiated payment that is not a settled one."""
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    _drive(monkeypatch, Landing(outcome=channels.REQUESTED))

    out = run.arrange(case_id=case_id, order_id=order.order_id)

    assert out["outcome"] == "requested"
    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "booking.requested")
    assert "not an appointment yet" in receipt.payload["note"]
    assert service.list_appointments(case_id)[0].confirmed_at is None


def test_a_confirmed_booking_records_when(case, monkeypatch):
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    _drive(monkeypatch, Landing(outcome=channels.CONFIRMED))

    run.arrange(case_id=case_id, order_id=order.order_id)
    appointment = service.list_appointments(case_id)[0]
    assert appointment.status == "confirmed"
    assert appointment.confirmed_at is not None


def test_the_receipt_never_names_the_test(case, monkeypatch):
    """/verify is public. Same rule the referral receipt already holds."""
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    _drive(monkeypatch, Landing())

    run.arrange(case_id=case_id, order_id=order.order_id)
    for receipt in service.get_chain(case_id).receipts:
        if receipt.kind.startswith("booking."):
            blob = str(receipt.payload).lower()
            assert "blood test" not in blob, f"{receipt.kind} named the test"
            assert "ashanthi" not in blob
            assert "+9190" not in blob


def test_nothing_is_arranged_without_her_own_agreement(case, monkeypatch):
    """The son may authorise Anbu Care to act; he cannot agree on her behalf to
    be entered into a lab's database."""
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    onboarding_tools.record_booking_disclosure_consent(parent_id, granted=False)
    driver = Landing()
    _drive(monkeypatch, driver)

    out = run.arrange(case_id=case_id, order_id=order.order_id)

    assert out["outcome"] == "escalated"
    assert "has not agreed" in out["detail"]
    assert driver.seen == [], "a centre was contacted without her consent"


def test_booking_disclosure_is_not_the_bedside_consent(case):
    """Two disclosures, deliberately disjoint. Showing a treating team her
    record ends when the browser closes; a centre keeps her details."""
    from anbu_care.comms import consent

    parent_id, _case_id = case
    onboarding_tools.record_emergency_disclosure_consent(parent_id, granted=False)
    profile = service.load_profile(parent_id)

    assert consent.BOOKING_DISCLOSURE in profile.disclosure_consents
    assert consent.EMERGENCY_CLINICAL_SHARE not in profile.disclosure_consents


# =========================================================================
# THE STANDING GRANT
# =========================================================================


def test_a_case_opened_after_the_grant_carries_it(case):
    parent_id, case_id = case
    standing = _mandate(parent_id)
    adopted = mandates.live_for_case(case_id)

    assert adopted.standing_id == standing.mandate_id
    applied = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "booking.standing_applied")
    assert "NOBODY AUTHORISED ANYTHING FOR THIS ADMISSION" in applied.payload["note"]
    assert "no authority to spend" in applied.payload["note"]


def test_a_preference_this_system_cannot_honour_is_refused(case):
    """It has no price for a test, and ordering by one it cannot see would be
    worse than offering fewer."""
    parent_id, _case_id = case
    with pytest.raises(mandates.BookingMandateRejected, match="no price"):
        _mandate(parent_id, prefer="cheapest")


def test_a_second_standing_grant_is_refused_while_one_is_live(case):
    parent_id, _case_id = case
    _mandate(parent_id)
    with pytest.raises(mandates.BookingMandateRejected, match="already live"):
        _mandate(parent_id)


# =========================================================================
# PHASE 0 IS HONEST ABOUT REACHING NOTHING
# =========================================================================


def test_with_no_driver_configured_it_says_so_rather_than_pretending(case):
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)

    out = run.arrange(case_id=case_id, order_id=order.order_id)
    assert out["outcome"] == "escalated"
    assert service.list_appointments(case_id) == []


def test_a_channel_never_switches_itself_on(monkeypatch):
    """A driver that can act on her behalf should not be something that
    enabled itself."""
    monkeypatch.delenv("ANBU_BOOKING_CHANNELS", raising=False)
    assert [d.name for d in channels.available()] == ["none"]


def test_cancelling_does_not_claim_the_centre_was_told(case, monkeypatch):
    """Saying an appointment is cancelled when only our row changed would be
    the exact lie this lane exists to avoid."""
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    _drive(monkeypatch, Landing(cancel_phone="+914612000000"))

    booked = run.arrange(case_id=case_id, order_id=order.order_id)
    out = run.cancel(case_id=case_id,
                     appointment_id=booked["appointment_id"],
                     cancelled_by="Heartlin")

    assert out["cancel_phone"] == "+914612000000"
    assert "Anbu Care has not done that" in out["still_to_do"]
    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "booking.cancelled")
    assert "has not contacted them" in receipt.payload["note"]


def test_an_unreachable_centre_is_not_reported_as_uncancellable(case, monkeypatch):
    """The reason a family is given has to be the real one.

    With no driver configured every attempt came back "no way to cancel was
    found", which is true of a request that was never made and is not why it
    did not happen.
    """
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    _drive(monkeypatch, Failing())

    out = run.arrange(case_id=case_id, order_id=order.order_id)

    attempt = out["attempts"][0]
    assert attempt["outcome"] == channels.UNAVAILABLE
    assert attempt.get("failed_check") is None, "a guard was blamed for a request never made"
    assert "no online form" in attempt["detail"]


# =========================================================================
# NOTHING IS SENT BEFORE THE GUARDS HAVE RULED
# =========================================================================


def test_a_refused_booking_is_prepared_but_never_submitted(case, monkeypatch):
    """The reason prepare and commit are two halves.

    The first version had one attempt() that navigated, filled and submitted,
    with the enforcer ruling afterwards on what it saw - so `no_payment` and
    `cancellable` were reporting on a form that had already gone.
    """
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    driver = Landing(page_text="Please pay now to confirm your slot")
    _drive(monkeypatch, driver)

    out = run.arrange(case_id=case_id, order_id=order.order_id)

    assert out["outcome"] == "escalated"
    assert driver.seen, "it never even prepared"
    assert driver.committed == [], "a form was submitted before the guards ruled"
    assert out["attempts"][0]["failed_check"] == "no_payment"


def test_a_centre_with_no_cancellation_path_is_prepared_and_dropped(case, monkeypatch):
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    driver = Landing(cancel_url="", cancel_phone="")
    _drive(monkeypatch, driver)

    run.arrange(case_id=case_id, order_id=order.order_id)
    assert driver.committed == [], "it booked somewhere it cannot unbook"


def test_an_allowed_booking_commits_exactly_once(case, monkeypatch):
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR, HOME])
    _mandate(parent_id)
    driver = Landing()
    _drive(monkeypatch, driver)

    run.arrange(case_id=case_id, order_id=order.order_id)
    assert len(driver.committed) == 1
    assert driver.committed[0][0] == "place-near"


def test_a_driver_that_crashes_never_reaches_the_family(case, monkeypatch):
    """A browser fault is an outcome for this lane, not an exception for
    somebody waiting on WhatsApp."""
    parent_id, case_id = case
    order = _order(parent_id, case_id, options=[NEAR, HOME])
    _mandate(parent_id)
    _drive(monkeypatch, Exploding())

    out = run.arrange(case_id=case_id, order_id=order.order_id)

    assert out["outcome"] == "escalated"
    assert len(out["attempts"]) == 2, "it stopped falling through after a crash"
    assert "could not prepare" in out["attempts"][0]["detail"]


def test_the_web_channel_only_drives_a_site_google_named(monkeypatch):
    """The URL comes from Places, never from a page and never from a model.
    Same rule as the destination on a payment."""
    from anbu_care.booking.web import WebChannel

    monkeypatch.setenv("ANBU_BOOKER_URL", "https://booker.example")
    channel = WebChannel()
    assert channel.can_serve({"website": "https://lab.example/book"}) is True
    assert channel.can_serve({"website": ""}) is False
    assert channel.can_serve({"website": "javascript:alert(1)"}) is False
    assert channel.can_serve({}) is False


def test_a_booker_that_is_down_is_unavailable_not_an_error(monkeypatch):
    """Every failure comes back as unavailable so the lane moves on."""
    from anbu_care.booking import web

    monkeypatch.setenv("ANBU_BOOKER_URL", "https://booker.invalid")
    monkeypatch.setattr(web.WebChannel, "_call", lambda *a, **k: None)

    prepared = web.WebChannel().prepare(centre={"website": "https://x"}, payload={})
    assert prepared.outcome == channels.UNAVAILABLE
    assert prepared.ready is False

    result = web.WebChannel().commit(centre={"website": "https://x"}, payload={},
                                     prepared=prepared)
    assert result.outcome == channels.UNAVAILABLE
    assert "not known" in result.detail, "it claimed to know what happened"


def test_the_web_channel_is_off_unless_it_is_configured(monkeypatch):
    from anbu_care.booking.web import WebChannel

    monkeypatch.delenv("ANBU_BOOKER_URL", raising=False)
    assert WebChannel.configured() is False
    monkeypatch.setenv("ANBU_BOOKING_CHANNELS", "web")
    assert [d.name for d in channels.available()] == ["none"]


# =========================================================================
# THE ONE-TIME CODE, RELAYED THROUGH THE PERSON IN THE ROOM
# =========================================================================


class OtpNeeded(Landing):
    """A centre whose form texts a code before it will take a booking."""

    name = "test-otp"

    def prepare(self, *, centre, payload):
        prep = super().prepare(centre=centre, payload=payload)
        return channels.Preparation(
            outcome=prep.outcome, detail=prep.detail, cancel_url=prep.cancel_url,
            cancel_phone=prep.cancel_phone, page_text=prep.page_text,
            handle=prep.handle, expects_otp=True,
            expects_otp_because="the form asked only for a telephone number")


def _with_a_neighbour(parent_id):
    from anbu_care.tools import onboarding_tools

    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Meena", relationship="neighbour",
        whatsapp_e164="+919000055555", timezone_name="Asia/Kolkata",
        is_primary=False, role="care_circle",
        consent_purposes=["outbound_notify", "inbound_wellbeing"])


def test_the_code_is_asked_of_the_neighbour_before_it_is_sent(case, monkeypatch):
    """She must be warned BEFORE the centre texts, or six digits arrive from a
    lab nobody told her to expect."""
    from anbu_care.booking import otp
    from anbu_care.tools import whatsapp_tools

    parent_id, case_id = case
    _with_a_neighbour(parent_id)
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)

    sent = []
    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: sent.append(kw) or {"status": "sent"})
    driver = OtpNeeded()
    _drive(monkeypatch, driver)

    run.arrange(case_id=case_id, order_id=order.order_id)

    assert sent, "nobody was asked for the code"
    assert sent[0]["template_name"] == "booking_code_needed"
    assert sent[0]["to_e164"] == "+919000055555", "the son was asked, not the neighbour"
    assert driver.otp_asked is True, "the driver was not told to wait for one"


def test_the_message_asking_for_a_code_carries_no_link(case):
    """A message that asks for a code AND carries a link is the exact shape of
    every phishing text anybody has been warned about."""
    from anbu_care.comms import policy

    body = policy.TEMPLATES["booking_code_needed"]["body"]
    assert "{dashboard_url}" not in body
    assert "http" not in body
    assert "never asks for a password or a card" in body


def test_a_code_is_only_read_as_one_while_a_request_is_outstanding(case):
    """Digits are a shape ordinary messages have. "6" is an answer to how she
    slept; "104" is a temperature."""
    from anbu_care.booking import otp

    parent_id, case_id = case
    assert otp.live_for(parent_id) is None
    assert otp.looks_like_code("123456") == "123456"
    for not_a_code in ("she took 6 tablets", "temperature is 101 now",
                       "", "123456 and she is fine", "ANBU-case-x-0-1-ab"):
        assert otp.looks_like_code(not_a_code) == "", not_a_code


def test_an_expired_request_cannot_be_answered(case):
    from anbu_care.booking import otp

    parent_id, case_id = case
    order = _order(parent_id, case_id)
    request = otp.open_request(parent_id=parent_id, case_id=case_id,
                               order_id=order.order_id, centre_name="X",
                               place_id="place-near", now=1_000.0)

    assert otp.live_for(parent_id, now=1_000.0) is not None
    assert otp.live_for(parent_id, now=1_000.0 + otp.TTL_SECONDS + 1) is None
    assert request.request_id


def test_a_code_cannot_be_used_twice(case):
    """One shot. A stale reply must not resume a session that moved on."""
    from anbu_care.booking import otp

    parent_id, case_id = case
    order = _order(parent_id, case_id)
    request = otp.open_request(parent_id=parent_id, case_id=case_id,
                               order_id=order.order_id, centre_name="X",
                               place_id="place-near")
    otp.close(request, outcome="used")
    assert otp.live_for(parent_id) is None


def test_the_code_itself_never_reaches_the_chain(case, monkeypatch):
    """It passes through the process into a form and stops existing."""
    from anbu_care.booking import otp

    parent_id, case_id = case
    order = _order(parent_id, case_id)
    request = otp.open_request(parent_id=parent_id, case_id=case_id,
                               order_id=order.order_id, centre_name="DLABS",
                               place_id="place-near", asked_of="Meena")
    otp.close(request, outcome="used")

    for receipt in service.get_chain(case_id).receipts:
        if receipt.kind.startswith("booking.otp"):
            blob = str(receipt.payload)
            assert "code" not in blob or "one-time code" in blob or "The code" in blob
            assert "session" not in blob.lower(), "the session id is on the chain"


def test_the_session_id_is_minted_before_the_reply_can_arrive(case):
    """A system that has to wait for a stranger's answer to learn where to send
    it has a race it cannot win."""
    from anbu_care.booking import otp

    parent_id, case_id = case
    order = _order(parent_id, case_id)
    request = otp.open_request(parent_id=parent_id, case_id=case_id,
                               order_id=order.order_id, centre_name="X",
                               place_id="p")
    assert len(request.session_id) >= 20
    assert otp.live_for(parent_id).session_id == request.session_id


def test_a_centre_with_no_code_step_is_not_asked_for_one(case, monkeypatch):
    parent_id, case_id = case
    _with_a_neighbour(parent_id)
    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)

    from anbu_care.tools import whatsapp_tools

    sent = []
    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: sent.append(kw) or {"status": "sent"})
    driver = Landing()          # plain, no OTP
    _drive(monkeypatch, driver)

    run.arrange(case_id=case_id, order_id=order.order_id)
    assert not any(k["template_name"] == "booking_code_needed" for k in sent)
    assert driver.otp_asked is False


def test_digits_outside_a_request_are_still_a_wellbeing_message(case, monkeypatch):
    """The inbound branch is the narrowest in the webhook, and this is why:
    outside the window "123456" must fall through and be recorded as what it
    is, not swallowed by a booking lane."""
    import inspect

    from anbu_care import server

    source = inspect.getsource(server)
    branch = source[source.index("# A ONE-TIME CODE, from the person who is with her"):]
    branch = branch[:branch.index("media = inbound.media_from")]

    assert "sender is not None" in branch, "an unregistered number could send a code"
    assert "otp.looks_like_code(body)" in branch
    assert "otp.live_for(sender.parent_id)" in branch, \
        "digits are read as a code with no outstanding request"
    assert "if pending is not None" in branch, "it does not fall through"


def test_a_late_code_is_answered_rather_than_dropped(case, monkeypatch):
    """She typed it out and pressed send. Silence would be the worst answer."""
    from anbu_care import server
    from anbu_care.booking import otp
    from anbu_care.booking import web as booking_web

    parent_id, case_id = case
    order = _order(parent_id, case_id)
    request = otp.open_request(parent_id=parent_id, case_id=case_id,
                               order_id=order.order_id, centre_name="DLABS",
                               place_id="p")
    monkeypatch.setattr(booking_web, "deliver_otp", lambda s, c: False)

    said = server._relay_booking_code(request, "123456").body.decode()

    assert "timed out" in said
    assert "Nothing has been booked" in said
    assert otp.live_for(parent_id) is None, "the request stayed answerable"


def test_a_delivered_code_leaves_the_request_open_for_the_lane_to_close(case, monkeypatch):
    """Only the lane knows whether the code actually completed the booking."""
    from anbu_care import server
    from anbu_care.booking import otp
    from anbu_care.booking import web as booking_web

    parent_id, case_id = case
    order = _order(parent_id, case_id)
    request = otp.open_request(parent_id=parent_id, case_id=case_id,
                               order_id=order.order_id, centre_name="DLABS",
                               place_id="p")
    monkeypatch.setattr(booking_web, "deliver_otp", lambda s, c: True)

    said = server._relay_booking_code(request, "123456").body.decode()

    assert "finishing the booking" in said
    assert otp.live_for(parent_id) is not None, "closed before the lane finished"


# =========================================================================
# WHAT A FORM KEPT IS NOT ALWAYS WHAT WAS TYPED
# =========================================================================


def test_a_truncated_foreign_number_is_never_submitted():
    """The one that would have texted a stranger.

    Aarthi Scans' booking field is maxlength=10, sized for an Indian mobile.
    Typing +14155550143 into it keeps ten characters, and ten digits of a
    truncated foreign number is a well-formed Indian mobile belonging to
    SOMEBODY ELSE - who is then sent a verification code for an appointment in
    a woman's name they have never heard of.
    """
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "booker_driver", pathlib.Path("booker/driver.py"))
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    # what the field kept  vs  what was typed
    assert driver._same("1415555014", "+14155550143") is False
    assert driver._same("Ashanthi Mac", "Ashanthi Machado") is False
    # presentation is not alteration
    assert driver._same("+91 88707 20883", "+918870720883") is True
    assert driver._same("8870720883", "8870720883") is True


def test_an_indian_number_is_offered_in_the_shape_the_field_holds():
    """A lab form wants ten digits with the country code implied, which is what
    a person filling the same form would type. A number from anywhere else is
    never adapted - trimming one to fit is how a code reaches a stranger."""
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "booker_driver2", pathlib.Path("booker/driver.py"))
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    class _Field:
        def __init__(self, maxlength):
            self.maxlength = maxlength

        def get_attribute(self, _name):
            return self.maxlength

    class _Locator:
        def __init__(self, maxlength):
            self.first = _Field(maxlength)

    class _Page:
        def __init__(self, maxlength):
            self.maxlength = maxlength

        def locator(self, _selector):
            return _Locator(self.maxlength)

    assert driver._phone_for(_Page("10"), "#p", "+918870720883") == "8870720883"
    assert driver._phone_for(_Page(None), "#p", "+918870720883") == "+918870720883"
    # foreign numbers are left exactly as they are, and the read-back refuses
    for foreign in ("+14155550143", "+16692167706", "+442071234567"):
        assert driver._phone_for(_Page("10"), "#p", foreign) == foreign


def test_the_code_is_asked_of_the_neighbour_not_the_son(case, monkeypatch):
    """He is asleep eleven time zones away and cannot read a text sent to a
    phone in Thoothukudi. Taking the first name on the care-circle list asked
    HIM, because he is listed first and holds outbound_notify like everyone
    else on it - the mechanism was right and the ordering made it a no-op."""
    from anbu_care.tools import onboarding_tools, whatsapp_tools

    parent_id, case_id = case
    # the son first, as he is in the real family
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Heartlin Machado", relationship="son",
        whatsapp_e164="+16692167706", timezone_name="America/Chicago",
        is_primary=True, role="family",
        consent_purposes=["outbound_notify", "status_updates"])
    _with_a_neighbour(parent_id)

    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    sent = []
    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: sent.append(kw) or {"status": "sent"})
    _drive(monkeypatch, OtpNeeded())

    run.arrange(case_id=case_id, order_id=order.order_id)

    asked = [k for k in sent if k["template_name"] == "booking_code_needed"]
    assert asked, "nobody was asked"
    assert asked[0]["to_e164"] == "+919000055555", \
        "the son was asked for a code he cannot possibly read"


# =========================================================================
# GENDER AND PINCODE: SENT BECAUSE REAL FORMS DEMAND THEM
# =========================================================================


def test_a_centre_may_be_told_a_gender_and_a_pincode():
    """Added only after a real centre refused without them. Aarthi Scans in
    Thoothukudi will not take a booking with the gender radio empty."""
    payload = disclosure.payload_for(
        name="Ashanthi Machado", age=71, phone="+919488581822",
        test_label="blood test", home_collection=True,
        gender="female", pincode="628002")
    assert payload["gender"] == "female"
    assert payload["pincode"] == "628002"
    assert set(payload) == disclosure.ALLOWED_FIELDS


def test_widening_the_whitelist_did_not_widen_it_further():
    """The two new fields are the two new fields. Everything else that was
    refused before is still refused."""
    for leak in ("allergies", "conditions", "medications", "policy_number",
                 "insurer", "case_id", "diagnosis", "dob", "son_phone",
                 "aadhaar", "email"):
        with pytest.raises(disclosure.DisclosureRefused):
            disclosure.check({"name": "A", "gender": "female", leak: "no"})


def test_an_unrecorded_gender_is_sent_as_empty_rather_than_guessed(case):
    """A gender read off a name is wrong often enough to matter."""
    payload = disclosure.payload_for(
        name="A. Machado", age=71, phone="+919488581822",
        test_label="blood test", home_collection=False)
    assert payload["gender"] == ""
    assert payload["pincode"] == ""


def test_the_profile_refuses_a_gender_it_does_not_understand(case):
    from anbu_care.tools import onboarding_tools

    parent_id, _case_id = case
    assert onboarding_tools.record_booking_details(
        parent_id, gender="f")["status"] == "rejected"
    assert onboarding_tools.record_booking_details(
        parent_id, gender="female")["gender"] == "female"


def test_nothing_infers_a_gender_or_a_pincode():
    """Read the code, not the behaviour. An inference added later would still
    pass a test that only checked today's output."""
    import inspect

    from anbu_care.booking import run as booking_run

    source = inspect.getsource(booking_run.arrange)
    assert 'gender=getattr(profile, "gender", "")' in source
    assert 'pincode=getattr(profile, "pincode", "")' in source

    # Statements only. The comment beside these lines explains why nothing is
    # guessed, and scanning prose for the word "guess" fails on the sentence
    # promising not to.
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    for derived in ("Mrs", "female if", '"female"', "'female'",
                    "startswith", "in name"):
        assert derived not in code, f"a gender is being derived: {derived}"


def test_a_booking_that_happened_is_told_to_somebody(case, monkeypatch):
    """The template was written and never wired, so the lane made a real
    enquiry at a real clinic and nobody was told. An agent that acts without
    saying so is not autonomous, it is unaccountable."""
    from anbu_care.tools import onboarding_tools, whatsapp_tools

    parent_id, case_id = case
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Heartlin Machado", relationship="son",
        whatsapp_e164="+16692167706", timezone_name="America/Chicago",
        is_primary=True, role="family",
        consent_purposes=["outbound_notify", "status_updates"])
    _with_a_neighbour(parent_id)

    order = _order(parent_id, case_id, options=[NEAR])
    _mandate(parent_id)
    sent = []
    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: sent.append(kw) or {"status": "sent"})
    _drive(monkeypatch, Landing(cancel_phone="+917550075500"))

    out = run.arrange(case_id=case_id, order_id=order.order_id)
    assert out["outcome"] == "requested"

    done = [k for k in sent if k["template_name"] == "booking_done"]
    assert done, "a real booking was made and nobody was told"
    # the person who takes her, and the son who should know it happened
    assert {k["to_e164"] for k in done} == {"+919000055555", "+16692167706"}
    assert done[0]["template_params"]["cancel"] == "+917550075500"


def test_the_booking_message_says_how_to_undo_it():
    from anbu_care.comms import policy

    body = policy.TEMPLATES["booking_done"]["body"]
    assert "{cancel}" in body
    assert "not confirmed a time" in body
    assert "Nothing was paid" in body


def test_a_reference_without_a_digit_is_not_a_reference():
    """The pattern matched "Booking Info" off Aarthi's page and recorded it as
    a booking reference, which is a made-up fact on a medical record."""
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "booker_driver3", pathlib.Path("booker/driver.py"))
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    assert driver._slot_from("Booking Info | About us") == ""
    assert driver._slot_from("Your booking") == ""
    assert "AB12345" in driver._slot_from("Booking No: AB12345")
    assert "998877" in driver._slot_from("Reference #998877")
