"""Signing in with Google, and the difference between who you are and what you may read.

The demo token proves server-side enforcement, which is what it is for. It does
not prove IDENTITY — everyone who has read the README holds it — and that is a
fair thing to point at in a system whose central claim is that clinical detail
is refused over WhatsApp *because* it lives behind a credential.

A Google account is the second credential, and the two halves are kept apart:

  AUTHENTICATION  Google says this is a real, verified account.
  AUTHORISATION   That account is already a family contact on this parent.

Almost every test here is about the second half, because that is where a real
system leaks: verifying a token is a library call, and deciding whose record it
opens is the part somebody has to get right.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from anbu_care import webauth
from anbu_care.tools import onboarding_tools, triage_tools

FAMILY_EMAIL = "karthik@example.com"
STRANGER_EMAIL = "someone.else@example.com"
# Shaped like a JWT so it takes the Google path rather than being dismissed.
TOKEN = "header.payload.signature"


@pytest.fixture
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


@pytest.fixture
def parent_id() -> str:
    pid = onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    onboarding_tools.record_family_contact(
        pid, name="Karthik", relationship="son", whatsapp_e164="+14155550142",
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=["status_updates"], email=FAMILY_EMAIL)
    return pid


@pytest.fixture
def case_id(parent_id) -> str:
    return triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]


def _google_says(monkeypatch, claims):
    """Pin what Google's verifier returns, at the one seam that calls it."""
    monkeypatch.setenv("ANBU_GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(webauth, "verify_google_identity", lambda token: claims)


def _verified(email=FAMILY_EMAIL, **extra):
    return {"sub": "1029384756", "email": email, "email_verified": True,
            "name": "Karthik Manickam", **extra}


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


# =========================================================================
# AUTHENTICATION: the token is verified by Google, not by us
# =========================================================================


def test_a_token_is_verified_against_googles_keys_not_decoded_here():
    """A JWT read in the browser proves nothing; anyone can mint one. The
    verification has to be a signature check against Google's published keys
    with our own client id pinned as the audience."""
    source = (webauth.__file__ and open(webauth.__file__).read()) or ""
    assert "verify_oauth2_token" in source
    assert "google_requests.Request()" in source
    # The audience is pinned. Without it, a token minted for ANY Google app is
    # a valid Google token and would be accepted here.
    assert "token, google_requests.Request(), client_id" in source


def test_google_sign_in_is_off_when_no_client_id_is_configured(monkeypatch):
    monkeypatch.delenv("ANBU_GOOGLE_CLIENT_ID", raising=False)
    assert webauth.google_client_id() is None
    assert webauth.verify_google_identity("a.b.c") is None


def test_an_unverified_email_is_not_an_identity(monkeypatch, client, parent_id):
    """An unverified address is a string somebody typed, and this one decides
    whose medical record opens."""
    monkeypatch.setenv("ANBU_GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *a, **k: {"sub": "x", "email": FAMILY_EMAIL, "email_verified": False})

    assert webauth.verify_google_identity(TOKEN) is None
    assert client.get(f"/api/parents/{parent_id}", headers=_auth()).status_code == 401


def test_a_token_google_rejects_is_not_a_session(monkeypatch, client, parent_id):
    monkeypatch.setenv("ANBU_GOOGLE_CLIENT_ID", "test-client-id")

    def refuse(*args, **kwargs):
        raise ValueError("Token expired")

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", refuse)
    assert webauth.verify_google_identity(TOKEN) is None
    assert client.get(f"/api/parents/{parent_id}", headers=_auth()).status_code == 401


# =========================================================================
# AUTHORISATION: being a real person is not permission
# =========================================================================


def test_a_family_contact_reads_the_record(monkeypatch, client, parent_id):
    _google_says(monkeypatch, _verified())
    response = client.get(f"/api/parents/{parent_id}", headers=_auth())
    assert response.status_code == 200
    assert "profile" in response.json()


def test_a_verified_stranger_is_refused(monkeypatch, client, parent_id):
    """The whole point. A real Google account that nobody added to this
    parent's contacts must not open her lab results."""
    _google_says(monkeypatch, _verified(email=STRANGER_EMAIL))
    response = client.get(f"/api/parents/{parent_id}", headers=_auth())

    assert response.status_code == 403
    assert "not on this parent's list" in response.json()["detail"]


def test_the_refusal_is_403_rather_than_401(monkeypatch, client, parent_id):
    """Telling someone already signed in to sign in sends them round a loop.
    They are authenticated; they are not authorised, and no amount of signing
    in again will change that."""
    _google_says(monkeypatch, _verified(email=STRANGER_EMAIL))
    assert client.get(f"/api/parents/{parent_id}", headers=_auth()).status_code == 403


def test_a_contact_on_one_parent_cannot_read_another(monkeypatch, client, parent_id):
    other = onboarding_tools.create_parent_profile(
        name="Someone Else", age=64, city="Madurai", lat=9.9, lon=78.1,
        chronic_conditions=[], allergies=[])["profile"]["parent_id"]
    _google_says(monkeypatch, _verified())

    assert client.get(f"/api/parents/{parent_id}", headers=_auth()).status_code == 200
    assert client.get(f"/api/parents/{other}", headers=_auth()).status_code == 403


def test_a_contact_with_no_email_cannot_be_matched(monkeypatch, client):
    """A contact who does not sign in still receives every message. They just
    have no dashboard identity, and an empty email must never match one."""
    pid = onboarding_tools.create_parent_profile(
        name="No Email", age=70, city="Chennai", lat=13.0, lon=80.2,
        chronic_conditions=[], allergies=[])["profile"]["parent_id"]
    onboarding_tools.record_family_contact(
        pid, name="Someone", relationship="son", whatsapp_e164="+14155550999",
        timezone_name="UTC", is_primary=True, consent_purposes=["status_updates"])

    _google_says(monkeypatch, _verified(email=""))
    assert client.get(f"/api/parents/{pid}", headers=_auth()).status_code in (401, 403)

    # And a blank claim must not match the blank stored value either.
    _google_says(monkeypatch, {"sub": "x", "email": "", "email_verified": True})
    assert client.get(f"/api/parents/{pid}", headers=_auth()).status_code in (401, 403)


def test_the_match_ignores_case(monkeypatch, client, parent_id):
    _google_says(monkeypatch, _verified(email="KARTHIK@Example.COM"))
    assert client.get(f"/api/parents/{parent_id}", headers=_auth()).status_code == 200


def test_a_case_scoped_endpoint_resolves_the_parent_from_the_case(
        monkeypatch, client, parent_id, case_id):
    _google_says(monkeypatch, _verified())
    assert client.get(f"/api/cases/{case_id}/trail", headers=_auth()).status_code == 200

    _google_says(monkeypatch, _verified(email=STRANGER_EMAIL))
    assert client.get(f"/api/cases/{case_id}/trail", headers=_auth()).status_code == 403


def test_a_signed_in_stranger_cannot_mint_a_clinician_link(
        monkeypatch, client, case_id):
    """Disclosure is the act that matters most, so it gets the same check."""
    _google_says(monkeypatch, _verified(email=STRANGER_EMAIL))
    response = client.post(f"/api/cases/{case_id}/handoff-link", headers=_auth())
    assert response.status_code == 403


# =========================================================================
# THE OTHER CREDENTIALS STILL WORK
# =========================================================================


def test_the_demo_credential_is_unaffected(client, parent_id):
    from anbu_care.webauth import DEMO_TOKEN

    assert client.get(f"/api/parents/{parent_id}",
                      headers=_auth(DEMO_TOKEN)).status_code == 200


def test_no_credential_is_still_a_401(client, parent_id):
    assert client.get(f"/api/parents/{parent_id}").status_code == 401


def test_a_random_bearer_is_still_a_401(monkeypatch, client, parent_id):
    _google_says(monkeypatch, None)
    assert client.get(f"/api/parents/{parent_id}",
                      headers=_auth("not-a-real-token")).status_code == 401


def test_whoami_never_401s(client, parent_id, monkeypatch):
    """A sign-in that succeeds but shows nothing is indistinguishable from one
    that failed."""
    assert client.get("/api/whoami").json() == {"signed_in": False}

    _google_says(monkeypatch, _verified())
    body = client.get("/api/whoami", headers=_auth()).json()
    assert body["signed_in"] and body["method"] == "google"
    assert body["name"] == "Karthik Manickam"


def test_the_auth_config_says_what_is_offered(client, monkeypatch):
    monkeypatch.setenv("ANBU_GOOGLE_CLIENT_ID", "abc.apps.googleusercontent.com")
    body = client.get("/api/auth-config").json()
    assert body["google_client_id"] == "abc.apps.googleusercontent.com"
    assert body["demo_sign_in"] is True


# =========================================================================
# THE HEADER SAYS WHO, AND WHAT THAT PERMITS
# =========================================================================


def _page() -> str:
    import pathlib

    return (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()


def test_the_account_menu_shows_authorisation_not_only_identity():
    """"Signed in" answers the smaller half of the question. Signing in says
    who you are; it does not say whose record you may open, and here those are
    deliberately different facts."""
    page = _page()
    assert "function accountMenu()" in page
    assert "On the record as" in page
    assert "Can open" in page
    assert "Signing in proves who you are." in page


def test_the_menu_shows_the_consents_that_were_actually_given():
    """DPDP consent is recorded per purpose with its own timestamp. A consent
    the person who gave it cannot look at is a checkbox."""
    page = _page()
    assert "Consents you have given" in page
    assert "CONSENT_LABEL" in page
    assert "contact?.consents" in page


def test_signing_out_clears_the_session_and_the_record():
    """Leaving the record in memory after a sign-out means the next person at
    the same laptop reads it."""
    page = _page()
    body = page[page.index("function signOut()"):]
    body = body[:body.index("\n}")]
    for cleared in ("S.token = null", "S.who = null", "S.record = null"):
        assert cleared in body, f"signOut does not clear {cleared}"
    # And Google is told, so the next sign-in asks rather than auto-selecting.
    assert "disableAutoSelect" in body


def test_the_avatar_never_falls_back_to_a_stock_person_icon():
    """A generic silhouette where a name is known reads as a broken image."""
    page = _page()
    assert "function initials(" in page
    assert 'S.who?.picture' in page


def test_a_link_holder_is_not_told_they_are_not_signed_in():
    """They are reading the record on the page that link just opened. A signed
    link is a credential; the menu saying otherwise contradicts the screen."""
    page = _page()
    assert "if(!S.token && !S.linkToken) return" in page
    assert "Opened from a link" in page
    # And it says the one thing a link cannot do, which is why the share card
    # asks them to sign in.
    assert "share with a clinician" in page


def test_the_account_menu_sits_above_the_nav():
    """The bar creates a stacking context, so a menu inside it could never rise
    above the nav while both sat at the same z-index and the nav came later."""
    page = _page()
    bar = page[page.index(".bar{position:sticky"):]
    bar = bar[:bar.index("}")]
    assert "z-index:50" in bar


def test_the_seeded_family_can_be_bound_to_a_real_account(monkeypatch, client):
    """A recorded demo re-seeds between takes. If the seeded contact carried no
    address, the sign-in beat would need a manual link before every take — and
    a step that is easy to forget between takes is a step that will be
    forgotten during one."""
    monkeypatch.setenv("ANBU_DEMO_FAMILY_EMAIL", "demo@example.com")
    parent_id = client.post("/api/demo/seed").json()["parent_id"]

    profile = client.get(f"/api/parents/{parent_id}",
                         headers=_auth("anbu-demo-family-token")).json()["profile"]
    assert profile["family_contacts"][0]["email"] == "demo@example.com"


def test_the_seeded_family_cannot_sign_in_by_default(monkeypatch, client):
    """Unset, the seeded contact has no address and therefore no way in.
    Receiving messages and reading the record are separate permissions."""
    monkeypatch.delenv("ANBU_DEMO_FAMILY_EMAIL", raising=False)
    parent_id = client.post("/api/demo/seed").json()["parent_id"]

    profile = client.get(f"/api/parents/{parent_id}",
                         headers=_auth("anbu-demo-family-token")).json()["profile"]
    assert profile["family_contacts"][0]["email"] == ""


def test_the_seeded_contact_can_be_named(monkeypatch, client):
    """Whoever records the demo is the person on camera, and the family contact
    should be them rather than a placeholder they have to explain away."""
    monkeypatch.setenv("ANBU_DEMO_FAMILY_NAME", "Heartlin Machado")
    parent_id = client.post("/api/demo/seed").json()["parent_id"]

    profile = client.get(f"/api/parents/{parent_id}",
                         headers=_auth("anbu-demo-family-token")).json()["profile"]
    contact = profile["family_contacts"][0]
    assert contact["name"] == "Heartlin Machado"
    assert contact["relationship"] == "son"


def test_the_default_stays_synthetic(monkeypatch, client):
    """Unset, the repo seeds a synthetic family, so a stranger cloning this
    does not get somebody's real name in their demo data."""
    monkeypatch.delenv("ANBU_DEMO_FAMILY_NAME", raising=False)
    parent_id = client.post("/api/demo/seed").json()["parent_id"]

    profile = client.get(f"/api/parents/{parent_id}",
                         headers=_auth("anbu-demo-family-token")).json()["profile"]
    assert profile["family_contacts"][0]["name"] == "Karthik Manickam"
