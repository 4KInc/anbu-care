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

import pathlib
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
    # The SYNTHETIC — DEMO DATA banner was removed from the chrome by request,
    # for the same reason as the seeded badges below: it addressed a reviewer
    # rather than the reader. Where a claim genuinely needs qualifying, the
    # qualifier now sits inside the sentence making the claim.
    #
    # The seeded-empanelment badges were removed from the chrome by request.
    # The claim they qualified did not go anywhere, so the caveat moved into the
    # sentence that makes it — see
    # test_explanation_carries_its_own_seeded_caveat in test_triage.py, which is
    # now the thing standing between us and an unqualified assertion about which
    # real hospital a real insurer pays at.
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


def test_the_page_title_reads_like_the_messages_do(client):
    """The title is not only a browser tab. WhatsApp renders it inside the
    message bubble as the link preview, so it is part of what the family reads
    and the same punctuation rule applies."""
    html = client.get("/app").text
    title = html.split("<title>")[1].split("</title>")[0]
    assert "—" not in title
    assert "–" not in title


def test_the_synthetic_banner_is_shown_once_not_on_every_panel(client):
    """Repetition trains the eye to skip it, which is worse than saying it
    plainly once. It must still be there — just not eight times."""
    html = client.get("/app").text
    assert "SYNTH_BANNER" in html
    assert "synthOnce" in html
    # And it resets per render, so the tab a judge screenshots still carries it.
    assert "SYNTH_SHOWN=false" in html.replace(" ", "")


def test_the_map_shows_the_decision_not_a_live_position(client):
    """No location is ever collected from the parent. A moving dot would
    invent tracking the system does not do and explicitly says it does not."""
    html = client.get("/app").text
    # Collapse whitespace: the copy wraps across lines in the source.
    flat = " ".join(html.split())
    assert "not where she is" in flat
    assert "does not track anyone" in flat
    for fabrication in ("watchPosition", "geolocation", "currentPosition", "live location"):
        assert fabrication not in html, f"the dashboard references {fabrication}"


def test_the_map_key_is_served_but_the_restriction_is_the_control(client):
    """A browser maps key is not a secret — it is restricted by referrer. What
    would be wrong is shipping an unrestricted one, or pretending it is
    hidden."""
    body = client.get("/api/map-config").json()
    assert "maps_api_key" in body
    assert "Google Places" in body["label"]
    assert "empanelment" in body["label"].lower()


def test_the_dashboard_script_actually_parses():
    """The dashboard is one HTML file with one inline script, and a syntax
    error in it renders a blank page with no server-side symptom at all.

    That happened: a duplicated `function caseFromLink(){` header shipped and
    survived five deploys, because every check we had was a grep for a string
    the broken file still contained. The page returned 200, the API returned
    200, the tests were green, and the dashboard showed nothing.

    Balanced-delimiter counting is not enough — the bug was inside otherwise
    balanced code. This parses the script the way a browser would.
    """
    import json
    import shutil
    import subprocess
    from pathlib import Path

    html = (Path(__file__).parent.parent / "anbu_care" / "webui" / "index.html").read_text()
    assert html.count("<script>") == 1 and html.count("</script>") == 1
    script = html[html.index("<script>") + len("<script>"):html.rindex("</script>")]

    node = shutil.which("node")
    if node:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        result = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert result.returncode == 0, (
            f"the dashboard script does not parse:\n{result.stderr[:400]}")
    else:  # pragma: no cover - CI without node
        # Weaker, but catches the exact class of bug that shipped: a declaration
        # repeated on one line, which is always a botched edit.
        import re

        for match in re.finditer(r"^(?:async )?function (\w+)\s*\(", script, re.M):
            line = script[match.start():script.index("\n", match.start())]
            assert line.count("function ") == 1, f"duplicated declaration: {line[:70]}"


def test_no_view_function_is_declared_twice():
    """A second declaration silently shadows the first and is a botched edit."""
    import re
    from collections import Counter
    from pathlib import Path

    html = (Path(__file__).parent.parent / "anbu_care" / "webui" / "index.html").read_text()
    script = html[html.index("<script>"):html.rindex("</script>")]
    names = re.findall(r"^(?:async )?function (\w+)\s*\(", script, re.M)
    duplicated = [n for n, c in Counter(names).items() if c > 1]
    assert not duplicated, f"declared more than once: {duplicated}"


# =========================================================================
# A SIGNED LINK IS A CREDENTIAL, AND IT IS SCOPED
# =========================================================================


def test_a_signed_link_opens_the_health_record_it_was_minted_for(client, seeded):
    """The message says "what was read from it is here". The link must arrive.

    A family member who followed the link they were sent was shown a credential
    wall, because the browser refused to ask for a record the server would have
    given it. The server was never the thing saying no.
    """
    from anbu_care.webauth import make_link_token

    parent_id, case_id = seeded
    token = make_link_token(parent_id, case_id)
    assert token, "no link secret configured for the test"

    response = client.get(f"/api/parents/{parent_id}?t={token}&case={case_id}")
    assert response.status_code == 200
    assert "profile" in response.json()


def test_a_link_minted_for_one_parent_cannot_read_another(client, seeded):
    """Scope is the whole reason this is a credential rather than a URL."""
    from anbu_care.webauth import make_link_token

    parent_id, case_id = seeded
    other = onboarding_tools.create_parent_profile(
        name="Someone Else", age=64, city="Madurai", lat=9.9, lon=78.1,
        chronic_conditions=[], allergies=[])["profile"]["parent_id"]

    token = make_link_token(parent_id, case_id)
    assert client.get(f"/api/parents/{other}?t={token}&case={case_id}").status_code == 401


def test_an_expired_link_reads_nothing(client, seeded):
    from anbu_care.webauth import make_link_token

    parent_id, case_id = seeded
    stale = make_link_token(parent_id, case_id, now=1)
    assert client.get(f"/api/parents/{parent_id}?t={stale}&case={case_id}"
                      ).status_code == 401


def test_a_tampered_link_reads_nothing(client, seeded):
    from anbu_care.webauth import make_link_token

    parent_id, case_id = seeded
    token = make_link_token(parent_id, case_id)
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    assert client.get(f"/api/parents/{parent_id}?t={tampered}&case={case_id}"
                      ).status_code == 401


def test_the_browser_is_not_stricter_than_the_server():
    """The record view gated on a full session while the server accepted a
    signed link, so the link in every document message led to a locked door."""
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()

    assert "if(!S.token && !S.linkToken) return S.caseId ? gate() : vOpen();" in page
    assert "if(S.parentId && (S.token || S.linkToken)){" in page
    assert "if(!S.token) return gate();" not in page


def test_the_dashboard_is_never_served_stale(client):
    """The page carries its own JavaScript inline. With no cache-control a
    browser caches it heuristically off last-modified, so a deployed fix can
    stay invisible in an already-open tab — which happened while verifying one.
    """
    response = client.get("/app")
    assert response.status_code == 200
    assert "no-cache" in response.headers.get("cache-control", "")


def test_the_sign_in_is_reachable_from_the_view_it_gates():
    """The parent id is read from the brief, which itself needs a credential.
    Checking for it first meant an uncredentialed visitor fell through to
    "open a case" and was never offered a sign-in — the gate was unreachable
    from the one view that exists behind it."""
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()
    record = page[page.index("function vRecord()"):page.index("function docDetails")]

    credential_check = record.index("if(!S.token && !S.linkToken)")
    parent_check = record.index("if(!S.parentId) return vOpen();")
    assert credential_check < parent_check, "the gate is behind the parent id again"
