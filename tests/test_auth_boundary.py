"""The two access models, both enforced server-side.

The whole DPDP argument is that clinical detail cannot leave over WhatsApp
*because* it lives somewhere protected. If "somewhere protected" were readable
by anyone with the URL, that argument would be hollow — and we would have
published, on camera, the exact data we claim to guard.

So: content endpoints must reject an unauthenticated request at the server, and
verification must stay open to everyone. Both halves are asserted here, because
either one failing breaks the pitch in a different direction.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from anbu_care import service
from anbu_care.tools import onboarding_tools, triage_tools
from anbu_care.webauth import DEMO_TOKEN

CONTENT_ENDPOINTS = "content"
OPEN_ENDPOINTS = "open"


@pytest.fixture(scope="module")
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


@pytest.fixture
def seeded():
    parent_id = onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="lab.png",
        summary="Lipid panel.",
        observations=[{"name": "LDL", "value": 165, "unit": "mg/dL", "flag": "high"}],
    )
    triage = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    return parent_id, triage["case_id"]


def _content_paths(parent_id: str, case_id: str) -> list[str]:
    return [
        f"/api/parents/{parent_id}",
        f"/api/cases/{case_id}",
        f"/api/cases/{case_id}/trail",
        f"/api/cases/{case_id}/brief",
    ]


def _open_paths(case_id: str) -> list[str]:
    return [
        f"/api/cases/{case_id}/verify",
        "/api/hospitals",
        "/api/healthz",
        "/api/intake-channels",
    ]


# ---- THE ACCEPTANCE TEST -------------------------------------------------


def test_content_endpoints_are_denied_without_a_credential(client, seeded):
    parent_id, case_id = seeded
    for path in _content_paths(parent_id, case_id):
        response = client.get(path)
        assert response.status_code == 401, f"{path} served content unauthenticated"
        assert "verify" in response.json()["detail"]


def test_verification_is_open_without_a_credential(client, seeded):
    """The other half. A gate that also closed /verify would defeat the design."""
    _, case_id = seeded
    for path in _open_paths(case_id):
        response = client.get(path)
        assert response.status_code == 200, f"{path} required auth but must be open"


def test_the_two_models_side_by_side(client, seeded):
    """The demo beat, as an assertion."""
    parent_id, case_id = seeded
    assert client.get(f"/api/parents/{parent_id}").status_code == 401
    assert client.get(f"/api/cases/{case_id}/verify").status_code == 200


# ---- the gate actually gates --------------------------------------------


def test_no_lab_value_leaks_in_an_unauthenticated_response(client, seeded):
    """Not just the status code — the body must not carry the reading."""
    parent_id, case_id = seeded
    for path in _content_paths(parent_id, case_id):
        body = client.get(path).text
        assert "165" not in body, f"{path} leaked a lab value while unauthenticated"
        assert "LDL" not in body


def test_a_valid_credential_opens_the_content_endpoints(client, seeded):
    parent_id, case_id = seeded
    headers = {"Authorization": f"Bearer {DEMO_TOKEN}"}
    for path in _content_paths(parent_id, case_id):
        assert client.get(path, headers=headers).status_code == 200, path


def test_the_record_is_readable_only_with_the_credential(client, seeded):
    parent_id, _ = seeded
    headers = {"Authorization": f"Bearer {DEMO_TOKEN}"}
    authed = client.get(f"/api/parents/{parent_id}", headers=headers).json()
    assert authed["documents"][0]["observations"][0]["value"] == "165"


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer wrong-token",
        "Basic anbu-demo-family-token",
        "anbu-demo-family-token",          # right value, no scheme
        "Bearer anbu-demo-family-token ",  # trailing space is tolerated
    ],
)
def test_malformed_or_wrong_credentials_are_rejected(client, seeded, header):
    parent_id, _ = seeded
    headers = {"Authorization": header} if header is not None else {}
    response = client.get(f"/api/parents/{parent_id}", headers=headers)
    expected = 200 if header == "Bearer anbu-demo-family-token " else 401
    assert response.status_code == expected, f"{header!r} -> {response.status_code}"


def test_verify_still_reports_tampering_without_a_credential(client, seeded):
    """Open verification has to stay useful, not just reachable."""
    _, case_id = seeded
    chain = service.get_chain(case_id)
    target = next(r for r in chain.receipts if r.kind == "triage.decision")
    from anbu_care.provenance.store import get_store, receipt_sk

    row = target.model_dump(mode="json")
    row["payload"]["severity"] = "LOW"
    get_store().put(f"CASE#{case_id}", receipt_sk(target.seq), row)

    body = client.get(f"/api/cases/{case_id}/verify").json()
    assert body["verified"] is False
    assert body["broken_at_seq"] == target.seq


# ---- the dashboard is a view, not a second implementation ---------------


def test_dashboard_is_served(client):
    response = client.get("/app")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_dashboard_carries_the_honesty_labels(client):
    """Labels are not decoration; a screenshot of this outlives the demo."""
    html = client.get("/app").text
    assert "SYNTHETIC — DEMO DATA" in html
    assert "SEEDED SNAPSHOT — NOT A LIVE FEED" in html
    assert "SIMULATED TPA" in html
    assert "not yet known" in html
    assert "does not monitor continuously" in html


def test_dashboard_does_not_reimplement_any_guarantee(client):
    """No severity rules, no sub-limit arithmetic, no hashing in the client.

    The browser must not compute anything the backend audits — otherwise a
    guarantee has quietly moved into unversioned client code.
    """
    html = client.get("/app").text

    # Never re-derives a guarantee. Symptom strings may appear as demo *input*
    # sent to the API — what must not appear is the client deciding anything.
    forbidden = [
        "red_flag", "redflag",                       # severity rule table
        "sha256", "crypto.subtle", "createHash",     # chain verification
        "sub_limit", "sublimit", "0.02",             # adjudication arithmetic
        "prev_hash",
    ]
    for token in forbidden:
        assert token.lower() not in html.lower(), (
            f"client appears to reimplement a guarantee: {token!r}"
        )

    # No severity assignment anywhere: severity is displayed, never decided.
    # (Prose containing the word is fine — what must not exist is the client
    # producing a value rather than rendering one.)
    assert not re.search(r"severity\s*=\s*['\"]", html)
    # Assignment only. `sev === "HIGH"` is a comparison against a value the
    # server decided, used to pick a CSS class — that is rendering, not deciding.
    assert not re.search(r"(?<![=!<>])=\s*['\"](HIGH|MEDIUM|LOW)['\"]", html)
    assert "p.severity" in html or "payload.severity" in html, (
        "expected severity to be read off an API payload"
    )


def test_dashboard_ships_no_third_party_code(client):
    """Self-contained: no CDN, no build step, nothing to drift."""
    html = client.get("/app").text.lower()
    for pattern in ("http://", "cdn.", "unpkg", "jsdelivr", "googleapis.com/ajax"):
        assert pattern not in html, f"dashboard reaches outside: {pattern!r}"


# ---- the endpoint that causes a message to leave --------------------------


def test_notify_claim_is_denied_without_a_credential(client, seeded):
    """It sends a real message. It must not be reachable anonymously."""
    _, case_id = seeded
    assert client.post(f"/api/cases/{case_id}/notify-claim").status_code == 401


def test_notify_claim_refuses_a_case_with_no_adjudication(client, seeded):
    """No assessment means there is nothing truthful to tell the family."""
    parent_id, case_id = seeded
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Child", relationship="son",
        whatsapp_e164="+14155550142", timezone_name="Asia/Kolkata", is_primary=True,
        consent_purposes=["claim_updates", "billing_updates"],
    )
    headers = {"Authorization": f"Bearer {DEMO_TOKEN}"}
    response = client.post(f"/api/cases/{case_id}/notify-claim", headers=headers)
    assert response.status_code == 409
    assert "not been adjudicated" in response.json()["detail"]


def test_notify_claim_refuses_a_parent_with_no_family_contact(client, seeded):
    """Nobody to tell is a refusal, not a silent success."""
    _, case_id = seeded
    headers = {"Authorization": f"Bearer {DEMO_TOKEN}"}
    response = client.post(f"/api/cases/{case_id}/notify-claim", headers=headers)
    assert response.status_code == 404
    assert "no family contact" in response.json()["detail"]


def test_notify_claim_does_not_take_the_recipient_from_the_caller(client):
    """The number comes from the parent's registered contact.

    Anyone holding the demo token could otherwise use the deployed service to
    send WhatsApp messages to numbers of their choosing.
    """
    import inspect

    from anbu_care import server

    source = inspect.getsource(server.notify_claim)
    assert "contact.whatsapp_e164" in source
    assert "request" not in inspect.signature(server.notify_claim).parameters
