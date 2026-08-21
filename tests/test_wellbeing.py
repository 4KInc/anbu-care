"""A check-in is words, and must never become a finding.

The hazard is concrete, not theoretical. severity.py matches RED_FLAGS against
free_text, so any path that carried a wellbeing message into run_triage would
manufacture a clinical severity out of a sentence someone typed on a phone.
This file exists to prove no such path exists, and to keep proving it.

The second half is the webhook itself. It is an unauthenticated write into a
person's health record, and the signature is not a hardening measure in front
of some other control — it is the only control there is.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from anbu_care import service
from anbu_care.comms import consent
from anbu_care.schemas import WellbeingEntry
from anbu_care.tools import onboarding_tools
from anbu_care.webauth import DEMO_TOKEN
from anbu_care.wellbeing import store as wellbeing_store

AUTH_TOKEN = "test-auth-token-not-real"
PARENT_NUMBER = "+919000000001"
CAREGIVER_NUMBER = "+919000000002"
STRANGER_NUMBER = "+919000009999"


@pytest.fixture(autouse=True)
def auth_token(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", AUTH_TOKEN)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


@pytest.fixture
def parent():
    parent_id = onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]

    profile = service.load_profile(parent_id)
    profile.whatsapp_e164 = PARENT_NUMBER
    service.save_profile(profile)
    service.register_whatsapp_number(PARENT_NUMBER, parent_id, None)

    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Meena", relationship="neighbour",
        whatsapp_e164=CAREGIVER_NUMBER, timezone_name="Asia/Kolkata",
        is_primary=False, consent_purposes=[consent.INBOUND_WELLBEING],
    )
    return parent_id


def _signed(client, form: dict, token: str = AUTH_TOKEN, signature: str | None = None):
    """Post to the webhook, signing exactly as Twilio does."""
    url = "http://testserver/api/wellbeing/inbound"
    body = urllib.parse.urlencode(form)
    if signature is None:
        payload = url + "".join(f"{k}{v}" for k, v in sorted(form.items()))
        signature = base64.b64encode(
            hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
        ).decode()
    return client.post(
        "/api/wellbeing/inbound",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Twilio-Signature": signature},
    )


# ---- GUARD 4: THE ONE THIS PHASE RESTS ON --------------------------------


def test_the_scariest_possible_check_in_still_only_stores_words(client, parent, monkeypatch):
    """A check-in reading like a cardiac emergency must change nothing.

    "crushing chest pain" is in RED_FLAGS. Routed into run_triage it would
    return HIGH severity, open a case, and pick a cardiac hospital — a clinical
    assessment conjured from one self-reported sentence, with a receipt to make
    it look official.

    So: the triage entry point is replaced with a bomb. If the inbound path can
    reach it at all, this test explodes.
    """
    from anbu_care.tools import triage_tools

    def detonate(*args, **kwargs):
        raise AssertionError(
            "the inbound wellbeing path called run_triage. A self-reported "
            "sentence was about to become a clinical severity."
        )

    monkeypatch.setattr(triage_tools, "run_triage", detonate)

    response = _signed(client, {"From": f"whatsapp:{PARENT_NUMBER}",
                                "Body": "crushing chest pain, can't breathe"})

    assert response.status_code == 200

    # The parent chain must hold this and nothing else. A triage decision or an
    # opened case would show up here as another kind of receipt.
    from anbu_care.provenance.store import PARENT_SUBJECT

    kinds = [r.kind for r in service.get_chain(parent, subject=PARENT_SUBJECT).receipts]
    assert kinds == ["wellbeing.recorded"], f"the inbound path did more than record words: {kinds}"

    entries = wellbeing_store.list_entries(parent)
    assert len(entries) == 1
    stored = entries[0]
    assert stored.text == "crushing chest pain, can't breathe"
    assert stored.source == "self-reported"

    # Nothing anywhere in the stored record resembles an assessment.
    dumped = stored.model_dump(mode="json")
    for forbidden in ("severity", "diagnosis", "triage", "mood", "score", "risk", "assessment"):
        assert forbidden not in dumped, f"stored entry carries a '{forbidden}' field"


def test_the_entry_type_has_nowhere_to_put_a_finding():
    """Field inspection, so this fails the day someone adds `severity: str`."""
    fields = set(WellbeingEntry.model_fields)
    assert fields == {"entry_id", "parent_id", "source", "text", "received_at", "channel"}
    for forbidden in ("severity", "diagnosis", "mood", "score", "sentiment", "risk", "state"):
        assert forbidden not in fields


# ---- the signature is the whole boundary ---------------------------------


def test_a_missing_signature_stores_nothing(client, parent):
    response = client.post(
        "/api/wellbeing/inbound",
        content=urllib.parse.urlencode({"From": f"whatsapp:{PARENT_NUMBER}", "Body": "ok today"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 403
    assert wellbeing_store.list_entries(parent) == []


def test_a_well_formed_but_wrong_signature_stores_nothing(client, parent):
    """The dangerous case. Correct shape, correct length, signed with the wrong
    key — a check that only tested for presence would wave this through."""
    forged = _signed(client, {"From": f"whatsapp:{PARENT_NUMBER}", "Body": "ok today"},
                     token="an-attackers-token")
    assert forged.status_code == 403
    assert wellbeing_store.list_entries(parent) == []


def test_a_malformed_signature_is_refused_not_crashed(client, parent):
    """Garbage must produce 403, not a 500 that leaks a stack trace."""
    response = _signed(client, {"From": f"whatsapp:{PARENT_NUMBER}", "Body": "ok"},
                       signature="!!!not-base64!!!")
    assert response.status_code == 403
    assert wellbeing_store.list_entries(parent) == []


def test_a_tampered_body_no_longer_matches(client, parent):
    """Signed for one message, delivered with another."""
    url = "http://testserver/api/wellbeing/inbound"
    honest = {"From": f"whatsapp:{PARENT_NUMBER}", "Body": "feeling better"}
    payload = url + "".join(f"{k}{v}" for k, v in sorted(honest.items()))
    signature = base64.b64encode(
        hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()

    response = client.post(
        "/api/wellbeing/inbound",
        content=urllib.parse.urlencode({"From": honest["From"], "Body": "swapped text"}),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Twilio-Signature": signature},
    )
    assert response.status_code == 403
    assert wellbeing_store.list_entries(parent) == []


# ---- consent decides whether anything is kept ----------------------------


def test_the_parents_own_number_is_labelled_self_reported(client, parent):
    _signed(client, {"From": f"whatsapp:{PARENT_NUMBER}", "Body": "slept well, ate breakfast"})
    entries = wellbeing_store.list_entries(parent)
    assert len(entries) == 1
    assert entries[0].source == "self-reported"
    assert entries[0].text == "slept well, ate breakfast"


def test_a_consented_caregiver_is_named(client, parent):
    _signed(client, {"From": f"whatsapp:{CAREGIVER_NUMBER}", "Body": "she ate lunch today"})
    entries = wellbeing_store.list_entries(parent)
    assert len(entries) == 1
    assert entries[0].source == "caregiver:Meena"


def test_an_unregistered_number_stores_nothing(client, parent):
    """A valid Twilio signature proves the request came from Twilio. It says
    nothing about who sent the message, and Twilio relays anyone."""
    response = _signed(client, {"From": f"whatsapp:{STRANGER_NUMBER}", "Body": "hello"})
    assert response.status_code == 204
    assert wellbeing_store.list_entries(parent) == []


def test_withdrawn_consent_stops_storage_immediately(client, parent):
    """Consent is read from the live profile, not frozen into the index."""
    profile = service.load_profile(parent)
    for contact in profile.family_contacts:
        contact.consents = {}
    service.save_profile(profile)

    response = _signed(client, {"From": f"whatsapp:{CAREGIVER_NUMBER}", "Body": "she ate lunch"})
    assert response.status_code == 204
    assert wellbeing_store.list_entries(parent) == []


# ---- clinical words go in, and do not come back out ----------------------


def test_clinical_content_is_stored_but_never_echoed(client, parent, monkeypatch):
    """The reply is fixed text. Even a message full of readings cannot bounce
    back out over WhatsApp, because nothing of what they wrote is in it."""
    from anbu_care.comms import transport

    carried: list[str] = []
    monkeypatch.setattr(
        transport, "send",
        lambda to, body, mode=None, media_url=None: carried.append(body),
    )

    said = "troponin I 0.94 ng/mL and the ECG showed ST elevation"
    response = _signed(client, {"From": f"whatsapp:{PARENT_NUMBER}", "Body": said})

    assert response.status_code == 200
    assert carried == [], "the inbound path sent something over the transport"

    reply = response.text
    assert "Thanks, that's noted." in reply
    for fragment in ("troponin", "0.94", "ECG", "ST elevation"):
        assert fragment not in reply, f"the reply echoed '{fragment}' back over WhatsApp"

    # Stored, though. It belongs in the credentialed record.
    assert wellbeing_store.list_entries(parent)[0].text == said


# ---- the receipt proves integrity without leaking the words --------------


def test_the_receipt_carries_a_hash_not_the_words(client, parent):
    """Chain verification is public. A receipt holding the text would hand
    "chest hurts, dizzy" to any unauthenticated caller who can reach /verify."""
    said = "chest hurts, dizzy"
    _signed(client, {"From": f"whatsapp:{PARENT_NUMBER}", "Body": said})

    from anbu_care.provenance.store import PARENT_SUBJECT

    chain = service.get_chain(parent, subject=PARENT_SUBJECT)
    receipt = next(r for r in chain.receipts if r.kind == "wellbeing.recorded")

    serialised = str(receipt.model_dump(mode="json"))
    assert said not in serialised
    assert "chest" not in serialised
    assert receipt.payload["text_sha256"] == hashlib.sha256(said.encode()).hexdigest()
    assert chain.verify().ok


# ---- surfacing -----------------------------------------------------------


def test_the_brief_quotes_the_latest_check_in(client, parent):
    _signed(client, {"From": f"whatsapp:{PARENT_NUMBER}", "Body": "feeling better"})
    case = service.open_case(parent)

    from anbu_care.brief.composer import compose_brief

    brief = compose_brief(case.case_id)
    fact = next(f for f in brief.facts if f.label == "Latest check-in")
    assert fact.known is True
    assert '"feeling better"' in fact.value
    assert "self-reported" in fact.value


def test_the_brief_says_no_check_in_yet_rather_than_guessing(parent):
    """Silence is not evidence that anything is well."""
    case = service.open_case(parent)

    from anbu_care.brief.composer import compose_brief

    brief = compose_brief(case.case_id)
    fact = next(f for f in brief.facts if f.label == "Latest check-in")
    assert fact.known is False
    assert fact.value is None
    assert "no check-in yet" in fact.source.note


def test_reading_check_ins_requires_a_credential(client, parent):
    """It returns what someone said about their health."""
    _signed(client, {"From": f"whatsapp:{PARENT_NUMBER}", "Body": "slept badly"})

    assert client.get(f"/api/parents/{parent}/wellbeing").status_code == 401

    authed = client.get(f"/api/parents/{parent}/wellbeing",
                        headers={"Authorization": f"Bearer {DEMO_TOKEN}"})
    assert authed.status_code == 200
    payload = authed.json()
    assert payload["count"] == 1
    assert payload["entries"][0]["text"] == "slept badly"
    assert "not a clinical assessment" in payload["label"].lower()


def test_an_unauthenticated_leak_check_on_the_content(client, parent):
    """Not just the status code: the words must not appear in the body."""
    _signed(client, {"From": f"whatsapp:{PARENT_NUMBER}", "Body": "dizzy and unwell"})
    body = client.get(f"/api/parents/{parent}/wellbeing").text
    assert "dizzy" not in body


# ---- the guarantee layer is untouched ------------------------------------


def test_wellbeing_does_not_appear_in_any_triage_input():
    """Structural, not behavioural: the triage package must not import the
    wellbeing store at all. A path that does not exist cannot be taken."""
    import inspect

    from anbu_care import triage
    from anbu_care.tools import triage_tools

    for module in (triage.severity, triage.routing, triage_tools):
        source = inspect.getsource(module)
        assert "wellbeing" not in source.lower(), (
            f"{module.__name__} references wellbeing; triage must never read a check-in"
        )


def test_the_inbound_endpoint_cannot_reach_triage():
    import ast
    import inspect
    import textwrap

    from anbu_care import server

    source = textwrap.dedent(inspect.getsource(server.wellbeing_inbound))
    tree = ast.parse(source)
    fn = tree.body[0]
    # Drop the docstring node. It correctly states the endpoint cannot set a
    # severity, and grepping prose would punish the documentation for saying so.
    if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        fn.body = fn.body[1:]
    body = ast.unparse(fn)

    for forbidden in ("run_triage", "triage_tools", "open_case", "severity"):
        assert forbidden not in body, f"the webhook body references {forbidden}"


# ---- the conflation that was live, pinned shut ---------------------------


def test_a_contact_consented_only_to_receive_updates_cannot_file_reports(client, parent):
    """The W1 defect, as a test. It would have PASSED before the fix.

    "status_updates" is the purpose for SENDING someone status messages. It was
    briefly also the purpose checked for accepting wellbeing check-ins, so
    agreeing to hear about your parent silently made you eligible to file
    reports about them. Two agreements, opposite directions, one flag.

    Old consents are deliberately not accepted as a fallback: dual-accepting
    would restore the conflation. A contact holding only the old purpose is
    refused until re-registered.
    """
    profile = service.load_profile(parent)
    for contact in profile.family_contacts:
        contact.consents = {"status_updates": contact.consents.get(
            consent.INBOUND_WELLBEING) or datetime.now(UTC)}
    service.save_profile(profile)

    response = _signed(client, {"From": f"whatsapp:{CAREGIVER_NUMBER}",
                                "Body": "she seems brighter today"})
    assert response.status_code == 204
    assert wellbeing_store.list_entries(parent) == [], (
        "a contact holding only the outbound status_updates purpose was allowed "
        "to write into the parent's record"
    )


def test_the_two_directions_are_different_agreements(parent):
    """Neither purpose implies the other."""
    from anbu_care.comms.consent import INBOUND_WELLBEING, OUTBOUND_PURPOSES

    assert INBOUND_WELLBEING not in OUTBOUND_PURPOSES
    assert "status_updates" != INBOUND_WELLBEING


# ---- the URL Twilio signed, not the one this process received ------------


def test_the_signed_url_is_rebuilt_from_forwarded_headers():
    """Behind Cloud Run the request arrives from a proxy over http, while
    Twilio signed the public https address. Without this the check fails on
    every legitimate message — closed, which is safe, but useless."""
    from anbu_care.comms.inbound import public_url

    class FakeURL:
        scheme, path, query = "http", "/api/wellbeing/inbound", ""

    class FakeRequest:
        url = FakeURL()
        headers = {"x-forwarded-proto": "https", "host": "anbu-care.example.app"}

    assert public_url(FakeRequest()) == "https://anbu-care.example.app/api/wellbeing/inbound"


def test_a_spoofed_forwarded_header_cannot_redirect_verification():
    """Scheme and host come from the proxy, the path never does."""
    from anbu_care.comms.inbound import public_url

    class FakeURL:
        scheme, path, query = "http", "/api/wellbeing/inbound", ""

    class FakeRequest:
        url = FakeURL()
        headers = {"x-forwarded-proto": "https", "host": "evil.example.com",
                   "x-original-path": "/somewhere/else"}

    assert public_url(FakeRequest()).endswith("/api/wellbeing/inbound")


def test_a_proxy_chain_uses_the_first_scheme_and_host():
    from anbu_care.comms.inbound import public_url

    class FakeURL:
        scheme, path, query = "http", "/api/wellbeing/inbound", ""

    class FakeRequest:
        url = FakeURL()
        headers = {"x-forwarded-proto": "https, http", "host": "front.example, back.internal"}

    assert public_url(FakeRequest()) == "https://front.example/api/wellbeing/inbound"
