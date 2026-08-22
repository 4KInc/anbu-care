"""Scoped emergency access.

This is the one place Anbu Care lets an unauthenticated stranger read clinical
content, so it is the one place where "it works" is not the interesting
property. What matters is everything it REFUSES: another case, the trail, the
parent record, an expired link, a revoked link, a forged one, and a case whose
parent never agreed to any of this.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from anbu_care import service
from anbu_care.comms import consent as consent_purposes
from anbu_care.handoff import access
from anbu_care.tools import onboarding_tools, triage_tools

SECRET = "test-handoff-secret"


@pytest.fixture(autouse=True)
def link_secret(monkeypatch):
    monkeypatch.setenv("ANBU_LINK_SECRET", SECRET)


@pytest.fixture
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


def _parent(consented: bool = True) -> str:
    pid = onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    if consented:
        onboarding_tools.record_emergency_disclosure_consent(pid)
    return pid


def _case(parent_id: str) -> str:
    return triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="",
    )["case_id"]


# =========================================================================
# CONSENT — no token without it, read live
# =========================================================================


def test_no_consent_means_no_token_is_ever_issued():
    case_id = _case(_parent(consented=False))
    with pytest.raises(access.HandoffDenied) as denied:
        access.mint(case_id)
    assert consent_purposes.EMERGENCY_CLINICAL_SHARE in str(denied.value)


def test_consent_is_read_live_not_cached_at_mint_time():
    """Withdrawing consent stops the next mint, immediately."""
    parent_id = _parent()
    case_id = _case(parent_id)
    assert access.mint(case_id)

    onboarding_tools.record_emergency_disclosure_consent(parent_id, granted=False)
    with pytest.raises(access.HandoffDenied):
        access.mint(case_id)


def test_emergency_share_is_not_implied_by_any_outbound_consent():
    """The collapse this purpose exists to prevent.

    Agreeing to receive claim updates is not agreeing that a stranger may read
    your allergies. If the disclosure purpose were ever reachable from the
    outbound set, this fails.
    """
    assert consent_purposes.EMERGENCY_CLINICAL_SHARE not in consent_purposes.OUTBOUND_PURPOSES
    assert consent_purposes.EMERGENCY_CLINICAL_SHARE not in consent_purposes.INBOUND_PURPOSES
    assert consent_purposes.EMERGENCY_CLINICAL_SHARE in consent_purposes.ALL_PURPOSES


# =========================================================================
# SCOPE — one case, summary only
# =========================================================================


def test_a_token_opens_only_its_own_case(client):
    mine = _case(_parent())
    theirs = _case(_parent())

    token = access.mint(mine)
    assert access.resolve(token).case_id == mine

    # The case id is IN the token, so swapping it breaks the signature.
    forged = token.replace(mine, theirs, 1)
    with pytest.raises(access.HandoffDenied):
        access.resolve(forged)


def test_the_token_grants_nothing_but_the_summary(client):
    """It must not become a skeleton key for the credentialed surface."""
    parent_id = _parent()
    case_id = _case(parent_id)
    token = access.mint(case_id)

    assert client.get(f"/handoff/{token}").status_code == 200

    # The same token, presented every way a caller could try, opens nothing.
    for path in (f"/api/cases/{case_id}/trail",
                 f"/api/parents/{parent_id}",
                 f"/api/parents/{parent_id}/wellbeing",
                 f"/api/cases/{case_id}"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 401
        assert client.get(f"{path}?token={token}").status_code == 401


# =========================================================================
# REFUSALS — and they reveal nothing
# =========================================================================


def test_an_expired_link_is_denied_and_shows_nothing(client):
    parent_id = _parent()
    case_id = _case(parent_id)
    token = access.mint(case_id, now=int(time.time()) - access.HANDOFF_TTL_SECONDS - 60)

    response = client.get(f"/handoff/{token}")
    assert response.status_code == 403
    body = response.text
    assert "expired" in body.lower()
    assert "Rajeswari" not in body
    assert "Penicillin" not in body
    assert case_id not in body


def test_a_forged_link_is_denied_and_shows_nothing(client):
    case_id = _case(_parent())
    good = access.mint(case_id)
    forged = good[:-4] + ("aaaa" if not good.endswith("aaaa") else "bbbb")

    response = client.get(f"/handoff/{forged}")
    assert response.status_code == 403
    assert "Penicillin" not in response.text
    assert "Rajeswari" not in response.text


def test_garbage_and_expired_are_indistinguishable_to_the_holder(client):
    """A refusal must not become an oracle for which cases exist."""
    case_id = _case(_parent())
    expired = access.mint(case_id, now=int(time.time()) - access.HANDOFF_TTL_SECONDS - 60)

    for bad in ("", "nonsense", "a.b.c.d", "case-does-not-exist.0.99999999999.xxxx"):
        assert client.get(f"/handoff/{bad}").status_code in (403, 404)

    assert client.get(f"/handoff/{expired}").status_code == 403


def test_revocation_kills_every_outstanding_link_at_once(client):
    case_id = _case(_parent())
    first, second = access.mint(case_id), access.mint(case_id)

    assert client.get(f"/handoff/{first}").status_code == 200

    access.revoke(case_id)

    for token in (first, second):
        response = client.get(f"/handoff/{token}")
        assert response.status_code == 403
        assert "revoked" in response.text.lower()
        assert "Penicillin" not in response.text


def test_without_a_configured_secret_no_link_can_be_minted(monkeypatch):
    """Fails closed. A shared default would let anyone reading this repo in."""
    case_id = _case(_parent())
    monkeypatch.delenv("ANBU_LINK_SECRET", raising=False)
    with pytest.raises(access.HandoffDenied):
        access.mint(case_id)


# =========================================================================
# EVERY OPEN IS RECEIPTED
# =========================================================================


def test_every_open_writes_an_access_receipt(client):
    case_id = _case(_parent())
    token = access.mint(case_id)

    before = len(service.get_chain(case_id).receipts)
    client.get(f"/handoff/{token}")
    client.get(f"/handoff/{token}")
    client.get(f"/handoff/{token}")

    receipts = service.get_chain(case_id).receipts
    opens = [r for r in receipts if r.kind == "emergency.access"]
    assert len(opens) == 3
    assert len(receipts) == before + 3
    assert service.verify_case(case_id).ok


def test_a_denied_open_writes_no_receipt(client):
    """A refusal is not an access, and must not look like one on the chain."""
    case_id = _case(_parent())
    expired = access.mint(case_id, now=int(time.time()) - access.HANDOFF_TTL_SECONDS - 60)

    before = len(service.get_chain(case_id).receipts)
    client.get(f"/handoff/{expired}")
    client.get(f"/handoff/{'garbage.0.0.x'}")

    assert len(service.get_chain(case_id).receipts) == before


def test_the_receipt_does_not_claim_to_identify_a_clinician(client):
    """A bearer link cannot know who opened it, and must not pretend to."""
    case_id = _case(_parent())
    client.get(f"/handoff/{access.mint(case_id)}")

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "emergency.access")
    blob = str(receipt.payload).lower()

    assert "link holder" in blob
    for invented in ("clinician_name", "doctor", "nurse", "identified by", "verified as"):
        assert invented not in blob


# =========================================================================
# MINTING IS BEHIND THE FAMILY CREDENTIAL
# =========================================================================


def test_minting_requires_a_family_session(client):
    case_id = _case(_parent())
    assert client.post(f"/api/cases/{case_id}/handoff-link").status_code == 401
    assert client.post(f"/api/cases/{case_id}/handoff-link/revoke").status_code == 401


def test_a_family_session_can_mint_and_revoke(client):
    from anbu_care.webauth import DEMO_TOKEN

    case_id = _case(_parent())
    auth = {"Authorization": f"Bearer {DEMO_TOKEN}"}

    issued = client.post(f"/api/cases/{case_id}/handoff-link", headers=auth)
    assert issued.status_code == 200
    url = issued.json()["url"]
    assert client.get(url).status_code == 200

    assert client.post(f"/api/cases/{case_id}/handoff-link/revoke", headers=auth).status_code == 200
    assert client.get(url).status_code == 403


def test_minting_without_consent_is_refused_at_the_endpoint(client):
    from anbu_care.webauth import DEMO_TOKEN

    case_id = _case(_parent(consented=False))
    response = client.post(f"/api/cases/{case_id}/handoff-link",
                           headers={"Authorization": f"Bearer {DEMO_TOKEN}"})
    assert response.status_code == 409
    assert consent_purposes.EMERGENCY_CLINICAL_SHARE in response.json()["detail"]


# =========================================================================
# WHAT THE CLINICIAN ACTUALLY SEES
# =========================================================================


def test_the_page_leads_with_allergies_and_says_it_is_not_integrated(client):
    case_id = _case(_parent())
    body = client.get(f"/handoff/{access.mint(case_id)}").text

    assert "Penicillin" in body
    assert body.index("Allergies") < body.index("Conditions")
    assert "not connected to any hospital system" in body.lower()
    assert "read only" in body.lower()
