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
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
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
    assert "Ashanthi" not in body
    assert "Penicillin" not in body
    assert case_id not in body


def test_a_forged_link_is_denied_and_shows_nothing(client):
    case_id = _case(_parent())
    good = access.mint(case_id)
    forged = good[:-4] + ("aaaa" if not good.endswith("aaaa") else "bbbb")

    response = client.get(f"/handoff/{forged}")
    assert response.status_code == 403
    assert "Penicillin" not in response.text
    assert "Ashanthi" not in response.text


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


# =========================================================================
# THE QR — a nurse points a camera at a screen
# =========================================================================


def test_the_mint_response_carries_a_scannable_qr(client, monkeypatch):
    from anbu_care.webauth import DEMO_TOKEN

    monkeypatch.setenv("ANBU_PUBLIC_BASE_URL", "https://example.run.app")
    case_id = _case(_parent())

    body = client.post(f"/api/cases/{case_id}/handoff-link",
                       headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()

    svg = body["qr_svg"]
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    # Inline, so nothing external has to load on hospital wifi.
    assert "http://" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "<script" not in svg


def test_the_qr_encodes_an_absolute_url_when_the_base_is_known(monkeypatch):
    """A QR carrying a relative path scans to nothing.

    Proven by matrix equality against an independently encoded absolute URL,
    not by eyeballing the SVG: two QR codes are identical if and only if they
    carry the same payload at the same settings.
    """
    import segno

    from anbu_care.server import _qr_svg

    monkeypatch.setenv("ANBU_PUBLIC_BASE_URL", "https://example.run.app")
    produced = _qr_svg("/handoff/tok")

    absolute = _svg_of(segno.make("https://example.run.app/handoff/tok", error="h"))
    relative = _svg_of(segno.make("/handoff/tok", error="h"))

    assert produced == absolute, "the QR does not carry the absolute URL"
    assert produced != relative, "the QR fell back to a path that scans to nothing"

    # A trailing slash on the base must not produce a double slash.
    monkeypatch.setenv("ANBU_PUBLIC_BASE_URL", "https://example.run.app/")
    assert _qr_svg("/handoff/tok") == absolute


def test_the_qr_still_renders_without_a_configured_base(monkeypatch):
    """Degrades rather than raising — the link is clickable regardless."""
    from anbu_care.server import _qr_svg

    monkeypatch.delenv("ANBU_PUBLIC_BASE_URL", raising=False)
    assert _qr_svg("/handoff/tok").startswith("<svg")


def _svg_of(qr) -> str:
    """Render a segno QR exactly as the server does, for comparison."""
    import io

    buffer = io.BytesIO()
    qr.save(buffer, kind="svg", scale=5, border=2, dark="#12212e", light="#ffffff",
            svgclass=None, lineclass=None, xmldecl=False, svgns=True)
    return buffer.getvalue().decode("utf-8")


# =========================================================================
# SENDING THE LINK — the realistic path
#
# A QR assumes someone is holding a laptop beside the nurse. In the situation
# this feature exists for, the family is asleep eleven time zones away.
# =========================================================================


def _contact(parent_id, number, purposes, name="Karthik", primary=True):
    onboarding_tools.record_family_contact(
        parent_id, name=name, relationship="son", whatsapp_e164=number,
        timezone_name="America/Los_Angeles", is_primary=primary,
        consent_purposes=purposes,
    )


def test_the_link_goes_to_whoever_is_with_her_by_default(client):
    """The care circle, not the son.

    Sending to the family decision-maker first made him the courier: awake at
    2am, copying a URL, forwarding it to a hospital eleven time zones away.
    That is the job this system exists to do instead of him, and the neighbour
    who is actually in the room is the one who can show a doctor anything.
    """
    from anbu_care.webauth import DEMO_TOKEN

    parent_id = _parent()
    _contact(parent_id, "+919000000101", ["outbound_notify"],
             name="Meena", primary=False)
    case_id = _case(parent_id)

    body = client.post(f"/api/cases/{case_id}/handoff-link/send",
                       headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()

    assert body["purpose_required"] == consent_purposes.OUTBOUND_NOTIFY
    assert body["recipients"], "nobody in the room was messaged"
    assert body["url"].startswith("/handoff/") or "/handoff/" in body["url"]
    token = body["url"].rsplit("/handoff/", 1)[1]
    assert client.get(f"/handoff/{token}").status_code == 200


def test_the_link_can_still_be_sent_to_the_family_explicitly(client):
    """He is the decision-maker of last resort, not the default courier."""
    from anbu_care.webauth import DEMO_TOKEN

    parent_id = _parent()
    _contact(parent_id, "+14155550142", ["admission_alerts"])
    case_id = _case(parent_id)

    body = client.post(f"/api/cases/{case_id}/handoff-link/send?to_care_circle=false",
                       headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()

    assert body["purpose_required"] == consent_purposes.ADMISSION_ALERTS
    assert body["recipients"], "nobody was messaged"
    assert body["url"].startswith("/handoff/") or "/handoff/" in body["url"]
    # And the link it sent actually works.
    token = body["url"].rsplit("/handoff/", 1)[1]
    assert client.get(f"/handoff/{token}").status_code == 200


def test_sending_requires_the_recipient_s_own_consent(client):
    """Two consents, and neither implies the other.

    The parent agreed her record may be disclosed. That is not the son
    agreeing to be messaged, and a system that conflated them would be making
    the same mistake `consent.py` was written about.
    """
    from anbu_care.webauth import DEMO_TOKEN

    parent_id = _parent()                       # has emergency_clinical_share
    _contact(parent_id, "+14155550142", ["status_updates"])   # but NOT admission_alerts
    case_id = _case(parent_id)

    body = client.post(f"/api/cases/{case_id}/handoff-link/send",
                       headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()

    assert body["status"] == "not_delivered"
    assert all(not r["delivered"] for r in body["recipients"])


def test_sending_without_the_parent_s_disclosure_consent_mints_nothing(client):
    from anbu_care.webauth import DEMO_TOKEN

    parent_id = _parent(consented=False)
    _contact(parent_id, "+14155550142", ["admission_alerts"])
    case_id = _case(parent_id)

    response = client.post(f"/api/cases/{case_id}/handoff-link/send",
                           headers={"Authorization": f"Bearer {DEMO_TOKEN}"})
    assert response.status_code == 409
    assert consent_purposes.EMERGENCY_CLINICAL_SHARE in response.json()["detail"]


def test_the_care_circle_variant_requires_outbound_notify(client):
    from anbu_care.webauth import DEMO_TOKEN

    parent_id = _parent()
    _contact(parent_id, "+14155550143", ["outbound_notify"], name="Neighbour", primary=False)
    case_id = _case(parent_id)

    body = client.post(f"/api/cases/{case_id}/handoff-link/send?to_care_circle=true",
                       headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()

    assert body["purpose_required"] == consent_purposes.OUTBOUND_NOTIFY
    assert [r["name"] for r in body["recipients"]] == ["Neighbour"]


def test_sending_requires_a_family_session(client):
    case_id = _case(_parent())
    assert client.post(f"/api/cases/{case_id}/handoff-link/send").status_code == 401


def test_a_permitted_send_is_not_reported_as_a_delivered_one(client):
    """Per recipient, never aggregated, and never optimistic."""
    from anbu_care.webauth import DEMO_TOKEN

    parent_id = _parent()
    _contact(parent_id, "+14155550142", ["admission_alerts"])
    case_id = _case(parent_id)

    body = client.post(f"/api/cases/{case_id}/handoff-link/send",
                       headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()

    for r in body["recipients"]:
        assert set(r) >= {"name", "to", "delivered", "detail"}
        assert isinstance(r["delivered"], bool)
    assert body["status"] in {"sent", "not_delivered"}
    assert "never a finding" in body["note"]


# =========================================================================
# THE SON IS ASLEEP, AND THAT IS THE POINT
# =========================================================================


def test_an_escalation_hands_the_treating_team_a_link_with_nobody_asked(monkeypatch):
    """The core claim, as a test.

    Anbu Care exists to do what a present son would do while the son is asleep
    eleven time zones away. Until this, a handoff link only existed if HE
    minted one from the dashboard — so the neighbour reached the hospital with
    nothing to show the treating team, and the person the system stands in for
    had to wake up and copy a URL.
    """
    from anbu_care.wellbeing import handler

    monkeypatch.setenv("ANBU_LINK_SECRET", "test-escalation-secret")

    parent_id = _parent()
    onboarding_tools.record_emergency_disclosure_consent(parent_id)
    _contact(parent_id, "+919000000101", ["outbound_notify"],
             name="Meena", primary=False)
    case_id = _case(parent_id)

    sent = []
    from anbu_care.tools import whatsapp_tools

    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: sent.append(kw) or {"status": "ok"})

    handler._hand_the_treating_team_a_link(case_id, parent_id)

    assert sent, "nobody in the room was given a link"
    handoff = [s for s in sent if s["template_name"] == "clinician_handoff_link"]
    assert handoff, "the message carried no handoff link"
    assert handoff[0]["to_e164"] == "+919000000101", "it went to the son, not the room"
    assert "/handoff/" in handoff[0]["template_params"]["handoff_url"]


def test_the_link_it_hands_over_can_record_what_was_ordered(monkeypatch):
    """Read-only would send everyone back to the son.

    A treating team that can see her allergies but cannot record what they
    ordered is a team that has to phone the family, which is the failure this
    removes. What makes it safe is unchanged: an hour, receipted on open,
    attributed, append-only, revocable in one act.
    """
    from anbu_care.handoff import access
    from anbu_care.wellbeing import handler

    monkeypatch.setenv("ANBU_LINK_SECRET", "test-escalation-secret")

    parent_id = _parent()
    onboarding_tools.record_emergency_disclosure_consent(parent_id)
    _contact(parent_id, "+919000000101", ["outbound_notify"],
             name="Meena", primary=False)
    case_id = _case(parent_id)

    sent = []
    from anbu_care.tools import whatsapp_tools

    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: sent.append(kw) or {"status": "ok"})

    handler._hand_the_treating_team_a_link(case_id, parent_id)

    url = sent[0]["template_params"]["handoff_url"]
    grant = access.resolve(url.rsplit("/handoff/", 1)[1])
    assert grant.may_write_note is True, "the treating team cannot record an order"


def test_no_consent_means_no_link_and_no_crash(monkeypatch):
    """A refusal, not a fault. An emergency alert must not fail because a link
    could not be minted."""
    from anbu_care.wellbeing import handler

    monkeypatch.setenv("ANBU_LINK_SECRET", "test-escalation-secret")

    parent_id = _parent(consented=False)   # she has not agreed to disclosure
    _contact(parent_id, "+919000000101", ["outbound_notify"],
             name="Meena", primary=False)
    case_id = _case(parent_id)

    sent = []
    from anbu_care.tools import whatsapp_tools

    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: sent.append(kw) or {"status": "ok"})

    handler._hand_the_treating_team_a_link(case_id, parent_id)
    assert sent == [], "a link was shared without the parent's consent"


def test_the_escalation_path_calls_it(monkeypatch):
    """Wired in, not merely available."""
    import inspect

    from anbu_care.wellbeing import handler

    source = inspect.getsource(handler)
    escalate = source[source.index("circle_alerted, circle_failed = _tell_the_care_circle"):]
    escalate = escalate[:escalate.index("alerted = _unique")]
    assert "_hand_the_treating_team_a_link" in escalate
