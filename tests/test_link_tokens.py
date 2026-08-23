"""A link a family member can tap, that is not a key to everything.

A son woken at 2am should not have to find and paste a shared token before he
can see why his phone is buzzing. But the fix for that friction must not become
a credential that opens any record to anyone who has ever received one alert.

So the link names one parent and one case, expires, and is signed with a secret
the recipient never sees. These tests try to make it do more than that.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from anbu_care import service
from anbu_care.tools import onboarding_tools
from anbu_care.webauth import DEMO_TOKEN, link_token_grants, make_link_token

SECRET = "test-link-secret-not-real"


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("ANBU_LINK_SECRET", SECRET)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


@pytest.fixture
def two_families():
    made = []
    for name in ("Ashanthi M.", "Lakshmi K."):
        pid = onboarding_tools.create_parent_profile(
            name=name, age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
            chronic_conditions=[], allergies=[],
        )["profile"]["parent_id"]
        made.append((pid, service.open_case(pid).case_id))
    return made


# ---- THE ONES THAT MATTER ------------------------------------------------


def test_a_link_for_one_case_cannot_read_another(client, two_families):
    """Two different families. A link minted for one must be inert on the other."""
    (pid_a, case_a), (pid_b, case_b) = two_families
    token = make_link_token(pid_a, case_a)

    assert client.get(f"/api/cases/{case_a}?t={token}").status_code == 200
    assert client.get(f"/api/cases/{case_b}?t={token}").status_code == 401


def test_a_link_cannot_read_a_different_parents_record(client, two_families):
    (pid_a, case_a), (pid_b, case_b) = two_families
    token = make_link_token(pid_a, case_a)

    assert client.get(f"/api/parents/{pid_a}?t={token}&case={case_a}").status_code == 200
    assert client.get(f"/api/parents/{pid_b}?t={token}&case={case_a}").status_code == 401


def test_a_link_cannot_trigger_an_outbound_message(client, two_families):
    """Read access is not permission to make the system message people.

    A link travels over WhatsApp and can be forwarded. It must never let a
    recipient cause real messages or phone calls to go out.
    """
    (pid_a, case_a), _ = two_families
    token = make_link_token(pid_a, case_a)

    assert client.post(f"/api/cases/{case_a}/notify-claim?t={token}").status_code == 401
    assert client.post(f"/api/cases/{case_a}/notify-care-circle?t={token}").status_code == 401


def test_an_expired_link_stops_working(client, two_families):
    (pid_a, case_a), _ = two_families
    stale = make_link_token(pid_a, case_a, now=int(time.time()) - (48 * 60 * 60))

    assert link_token_grants(stale, parent_id=pid_a, case_id=case_a) is False
    assert client.get(f"/api/cases/{case_a}?t={stale}").status_code == 401


def test_a_forged_signature_is_refused(client, two_families):
    """Right shape, right length, wrong key."""
    (pid_a, case_a), _ = two_families
    real = make_link_token(pid_a, case_a)
    expiry, _, signature = real.partition(".")
    forged = f"{expiry}.{'A' * len(signature)}"

    assert client.get(f"/api/cases/{case_a}?t={forged}").status_code == 401


@pytest.mark.parametrize("junk", ["", "nonsense", "123", "123.", ".sig", "abc.def"])
def test_malformed_tokens_are_refused_not_crashed(client, two_families, junk):
    (pid_a, case_a), _ = two_families
    assert client.get(f"/api/cases/{case_a}?t={junk}").status_code == 401


def test_extending_the_expiry_invalidates_the_signature(two_families):
    """The expiry is signed, so a recipient cannot simply edit it."""
    (pid_a, case_a), _ = two_families
    real = make_link_token(pid_a, case_a)
    _, _, signature = real.partition(".")
    extended = f"{int(time.time()) + 999_999}.{signature}"

    assert link_token_grants(extended, parent_id=pid_a, case_id=case_a) is False


# ---- fails closed --------------------------------------------------------


def test_no_secret_means_no_links_rather_than_a_default(monkeypatch, two_families):
    """A hardcoded fallback would mean every deployment shared a signing key,
    so anyone who read the source could mint a link into any record."""
    (pid_a, case_a), _ = two_families
    monkeypatch.delenv("ANBU_LINK_SECRET", raising=False)

    assert make_link_token(pid_a, case_a) is None
    assert link_token_grants("anything", parent_id=pid_a, case_id=case_a) is False


def test_without_a_secret_the_alert_still_links_and_asks_for_a_sign_in(monkeypatch):
    """Degrades to the old behaviour, never to a broken link."""
    from anbu_care.comms.policy import render_template

    monkeypatch.delenv("ANBU_LINK_SECRET", raising=False)
    body = render_template(
        "claim_stage", {"parent_name": "Amma", "stage": "approved", "amount": "30,000"},
        case_id="case-x", parent_id="parent-y",
    )
    assert "/app?case=case-x" in body
    assert "&t=" not in body


# ---- the session still works, and still gates ---------------------------


def test_the_family_session_still_opens_everything(client, two_families):
    (pid_a, case_a), _ = two_families
    headers = {"Authorization": f"Bearer {DEMO_TOKEN}"}
    assert client.get(f"/api/cases/{case_a}", headers=headers).status_code == 200
    assert client.get(f"/api/parents/{pid_a}", headers=headers).status_code == 200


def test_no_credential_at_all_is_still_401(client, two_families):
    (pid_a, case_a), _ = two_families
    assert client.get(f"/api/cases/{case_a}").status_code == 401
    assert client.get(f"/api/parents/{pid_a}").status_code == 401


def test_verification_is_still_open_to_everyone(client, two_families):
    """The signed link must not have quietly closed the public half."""
    (_, case_a), _ = two_families
    assert client.get(f"/api/cases/{case_a}/verify").status_code == 200
