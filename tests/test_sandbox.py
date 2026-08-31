"""A family of your own, and everything it refuses to do.

The sibling project can let a stranger text its number because the data belongs
to an organisation. This one cannot: its data subject is a person, and its whole
claim is that who you are decides what you get. So the sandbox does not open the
door, it hands the visitor their own record and their own roles in it, and
almost every test here is about a boundary rather than a feature.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anbu_care import sandbox, service
from anbu_care.tools import onboarding_tools


@pytest.fixture
def client_and_signed(monkeypatch):
    """The signed Twilio webhook, borrowed from the wellbeing suite.

    A stranger only ever reaches the sandbox through this, so testing
    `provision` alone would leave uncovered the one path a visitor actually
    takes. The auth token has to be set here too: the webhook verifies the
    signature before it looks at anything else, and an unsigned post is a 403
    that never reaches the code under test.
    """
    from fastapi.testclient import TestClient

    from anbu_care.server import app
    from tests.test_wellbeing import AUTH_TOKEN, _signed

    monkeypatch.setenv("TWILIO_AUTH_TOKEN", AUTH_TOKEN)
    return TestClient(app), _signed


VISITOR = "+15551230000"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("ANBU_SANDBOX", "on")
    monkeypatch.setenv("ANBU_SANDBOX_DAILY_CAP", "3")
    monkeypatch.setenv("ANBU_SANDBOX_TTL_HOURS", "24")


# --- the keyword is the consent moment --------------------------------------

@pytest.mark.parametrize("body", ["START", " start ", "Start.", "start!"])
def test_the_keyword_is_recognised_however_it_is_typed(body):
    assert sandbox.asked_for_one(body) is True


@pytest.mark.parametrize("body", [
    "hello", "", "let's get started", "restart", "start the check-ins",
    "she has chest pain",
])
def test_a_sentence_containing_the_word_is_not_a_request(body):
    # The same discipline STOP uses: an exact whole-message match, because
    # provisioning somebody who did not ask sends them a message they never
    # consented to receive.
    assert sandbox.asked_for_one(body) is False


def test_switched_off_provisions_nobody(monkeypatch):
    monkeypatch.setenv("ANBU_SANDBOX", "off")
    got = sandbox.provision(VISITOR, now=NOW)
    assert got.provisioned is False
    assert got.status == "disabled"
    assert not got.parent_id


# --- what a visitor gets -----------------------------------------------------

def test_a_visitor_gets_their_own_family_and_three_roles(on):
    got = sandbox.provision(VISITOR, now=NOW)
    assert got.provisioned, got.reply

    profile = service.load_profile(got.parent_id)
    # Hers, so a check-in has somewhere to go and her answer is attributed to
    # her rather than to a family member.
    assert profile.whatsapp_e164 == VISITOR
    roles = {c.role for c in profile.family_contacts}
    assert "family" in roles and "care_circle" in roles
    assert all(c.whatsapp_e164 == VISITOR for c in profile.family_contacts)
    assert profile.policy is not None


def test_the_welcome_says_it_is_synthetic_before_anything_else(on):
    got = sandbox.provision(VISITOR, now=NOW)
    body = got.reply
    assert "ALL SYNTHETIC" in body
    assert "do not send real personal or health information" in body.lower()
    # And it does not promise it forever.
    assert "released" in body.lower() or "a day" in body.lower()


def test_asking_twice_returns_the_same_family(on):
    first = sandbox.provision(VISITOR, now=NOW)
    second = sandbox.provision(VISITOR, now=NOW + timedelta(minutes=5))
    assert second.status == "already"
    assert second.parent_id == first.parent_id
    # A second family for one number would leave the first being messaged by
    # nobody, and the number can only resolve to one of them anyway.
    assert len(sandbox._rows()) == 1


# --- the boundaries ----------------------------------------------------------

def test_it_never_touches_the_demo_family(on):
    family = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=[], allergies=[])["profile"]["parent_id"]
    service.register_whatsapp_number("+14155550142", family, "Heartlin")

    got = sandbox.provision(VISITOR, now=NOW)
    assert got.parent_id != family
    # The demo family's handset still resolves to the demo family.
    assert service.lookup_whatsapp_number("+14155550142")["parent_id"] == family


def test_the_daily_cap_is_enforced(on):
    for i in range(3):
        assert sandbox.provision(f"+1555000000{i}", now=NOW).provisioned
    refused = sandbox.provision("+15550000009", now=NOW)
    assert refused.status == "capped"
    assert not refused.parent_id
    assert "public number" in refused.reply


def test_the_cap_is_per_day_not_for_ever(on):
    for i in range(3):
        sandbox.provision(f"+1555000000{i}", now=NOW)
    tomorrow = sandbox.provision("+15550000009", now=NOW + timedelta(days=1))
    assert tomorrow.provisioned


# --- letting go --------------------------------------------------------------

def test_an_expired_sandbox_stops_being_messaged_and_releases_its_number(on):
    from anbu_care.recovery import window as recovery

    got = sandbox.provision(VISITOR, now=NOW)
    recovery.open_window(got.parent_id, "", discharged_on="2026-08-30")
    assert recovery.open_window_for(got.parent_id) is not None
    assert service.lookup_whatsapp_number(VISITOR) is not None

    released = sandbox.release_expired(now=NOW + timedelta(hours=25))
    assert released == [got.parent_id]
    # Outbound stops.
    assert recovery.open_window_for(got.parent_id) is None
    # And the number resolves to nobody again, which is where it started.
    assert service.lookup_whatsapp_number(VISITOR) is None


def test_the_record_survives_release(on):
    # Not a delete. A chain that can be made to vanish is not evidence of
    # anything; what ends is the messaging.
    got = sandbox.provision(VISITOR, now=NOW)
    sandbox.release_expired(now=NOW + timedelta(hours=25))
    assert service.load_profile(got.parent_id) is not None


def test_a_sandbox_inside_its_day_is_left_alone(on):
    sandbox.provision(VISITOR, now=NOW)
    assert sandbox.release_expired(now=NOW + timedelta(hours=23)) == []
    assert service.lookup_whatsapp_number(VISITOR) is not None


def test_releasing_twice_releases_nothing_the_second_time(on):
    sandbox.provision(VISITOR, now=NOW)
    late = NOW + timedelta(hours=25)
    assert len(sandbox.release_expired(now=late)) == 1
    assert sandbox.release_expired(now=late + timedelta(hours=1)) == []


# --- it must never raise into a webhook --------------------------------------

def test_a_provisioning_failure_is_a_sentence_not_a_stack_trace(on, monkeypatch):
    monkeypatch.setattr(sandbox, "_build",
                        lambda n, now: (_ for _ in ()).throw(RuntimeError("boom")))
    got = sandbox.provision(VISITOR, now=NOW)
    assert got.status == "failed"
    assert not got.parent_id
    assert "nothing was half-made" in got.reply
    # And nothing was recorded, so START still works once the fault is fixed.
    assert sandbox._rows() == []


# --- through the webhook, which is the only way a visitor ever reaches it ----

def test_a_stranger_saying_hello_is_told_what_this_is_and_stored(client_and_signed):
    client, signed = client_and_signed
    r = signed(client, {"From": "whatsapp:+15559998888", "Body": "hello"})
    assert r.status_code == 200
    assert "not stored" in r.text
    assert "reply START" in r.text


def test_a_stranger_saying_start_is_given_a_family(client_and_signed, on):
    client, signed = client_and_signed
    r = signed(client, {"From": "whatsapp:+15559997777", "Body": "START"})
    assert r.status_code == 200
    assert "ALL SYNTHETIC" in r.text
    # And the number now resolves, so their next message is theirs.
    assert service.lookup_whatsapp_number("+15559997777") is not None


def test_with_the_sandbox_off_start_is_just_another_stranger(client_and_signed,
                                                            monkeypatch):
    monkeypatch.setenv("ANBU_SANDBOX", "off")
    client, signed = client_and_signed
    r = signed(client, {"From": "whatsapp:+15559996666", "Body": "START"})
    assert r.status_code == 200
    assert "switched off" in r.text
    assert service.lookup_whatsapp_number("+15559996666") is None


def test_a_visitor_can_actually_write_in(on):
    """The bug this caught live.

    A provisioned number was welcomed and then met with silence: the roles it
    was given could be written TO but not FROM, so `resolve_sender` found a
    contact with no permission to file a report and refused. Correct, and
    useless. Being able to send the first message is the whole point of handing
    somebody a family.
    """
    from anbu_care.comms import consent, inbound

    got = sandbox.provision(VISITOR, now=NOW)
    sender = inbound.resolve_sender(VISITOR)
    assert sender is not None, "a provisioned visitor could not write in"
    assert sender.parent_id == got.parent_id

    profile = service.load_profile(got.parent_id)
    for contact in profile.family_contacts:
        assert consent.INBOUND_WELLBEING in contact.consents, contact.name
