"""What the family is told after they send a photograph.

Every defect this lane has shipped was in the REPLY, not the reading. The
document was ingested and the follow-up was blocked by the gate. The duplicate
was correctly refused and described as an unreadable bill. Both times the
record was right and the person holding the phone was told something false.

The reason they reached the deployed path is that nothing tested this function.
`_read_bill_and_report` runs as a background task, after the response, so a
fault in it reaches no caller, no test client, and no status code — only a
WhatsApp message nobody asserted on.

So these tests call it directly and assert on the TEMPLATE it chose. The seam
is `send_family_update`, which is where a real message would leave.
"""

from __future__ import annotations

import json

import pytest

from anbu_care.comms.storage import StoredArtifact
from anbu_care.docvision import read as dv
from anbu_care.tools import onboarding_tools, triage_tools

IMAGE = b"\x89PNG\r\n\x1a\n" + b"x" * 9000
OTHER_IMAGE = b"\x89PNG\r\n\x1a\n" + b"y" * 9000


@pytest.fixture
def case(monkeypatch):
    from anbu_care.comms import storage as gcs

    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="":
                        StoredArtifact(stored=True, url="https://signed/x",
                                       object_name=f"a/{filename}", detail="",
                                       expires_in_seconds=900))
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-1", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True)
    onboarding_tools.record_family_contact(
        pid, name="Arun", relationship="son", whatsapp_e164="+15550001111",
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=["status_updates", "billing_updates", "wellbeing_updates"])
    cid = triage_tools.run_triage(
        parent_id=pid, symptoms=["chest pain"], free_text="", reported_by="caregiver",
        lat=0.0, lon=0.0, case_id="")["case_id"]
    return pid, cid


@pytest.fixture
def sent(monkeypatch):
    """Capture every message the background task tries to send."""
    from anbu_care.tools import whatsapp_tools

    captured: list[dict] = []

    def fake(**kwargs):
        captured.append(kwargs)
        return {"allowed": True, "sid": "SM1"}

    monkeypatch.setattr(whatsapp_tools, "send_family_update", fake)
    return captured


def _document(monkeypatch, kind, body):
    monkeypatch.setenv("ANBU_DOC_VISION_MODE", "gemini")
    monkeypatch.setattr(dv, "_call_model", lambda image, mime_type: json.dumps(
        {"kind": kind, "confidence": 0.95, "unreadable": False,
         "patient_name": "Ashanthi M.", kind: body}))


def _bill(monkeypatch):
    from anbu_care.bills import extract as bill_vision

    monkeypatch.setenv("ANBU_DOC_VISION_MODE", "gemini")
    monkeypatch.setattr(dv, "_call_model", lambda image, mime_type: json.dumps(
        {"kind": "bill", "confidence": 0.95, "unreadable": False, "bill": {}}))
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(bill_vision, "_call_model", lambda image, mime_type: json.dumps({
        "vendor": "Sacred Heart Hospital", "bill_date": "2026-08-19",
        "stated_total_inr": 9000, "unreadable": False, "unreadable_reason": None,
        "line_items": [{"label": "General ward bed rent", "item": "room_rent",
                        "amount_inr": 9000, "source_hint": "1 day"}]}))


def _run(case_id, parent_id, image=IMAGE):
    from anbu_care.server import _read_bill_and_report

    _read_bill_and_report(case_id, parent_id, image, "image/png")


def _templates(sent):
    return [m["template_name"] for m in sent]


# =========================================================================


def test_a_lab_report_is_reported_as_recorded(case, sent, monkeypatch):
    parent_id, case_id = case
    _document(monkeypatch, "lab_report", {"observations": [
        {"name": "Troponin I", "value": "0.94", "flag": "high"},
        {"name": "Sodium", "value": "138", "flag": "normal"}]})
    _run(case_id, parent_id)

    assert _templates(sent) == ["document_recorded"]
    params = sent[0]["template_params"]
    assert params["document_kind"] == "lab report"
    assert "2 results" in params["summary"]
    assert "troponin" not in json.dumps(params).lower()


def test_the_same_lab_report_again_is_reported_as_already_on_file(
        case, sent, monkeypatch):
    """The bug, at the level it was actually seen: what arrives on the phone."""
    parent_id, case_id = case
    _document(monkeypatch, "lab_report", {"observations": [
        {"name": "Troponin I", "value": "0.94", "flag": "high"}]})
    _run(case_id, parent_id)
    sent.clear()

    _run(case_id, parent_id)

    assert _templates(sent) == ["document_already_recorded"]
    assert sent[0]["template_params"]["subject"] == "lab report"


def test_an_unreadable_document_is_not_called_a_bill(case, sent, monkeypatch):
    parent_id, case_id = case
    monkeypatch.setenv("ANBU_DOC_VISION_MODE", "gemini")
    monkeypatch.setattr(dv, "_call_model", lambda image, mime_type: json.dumps(
        {"kind": "lab_report", "unreadable": True, "unreadable_reason": "too dark"}))
    _run(case_id, parent_id)

    assert _templates(sent) == ["document_unreadable"]
    assert "too dark" in sent[0]["template_params"]["reason"]


def test_a_bill_is_reported_with_its_money(case, sent, monkeypatch):
    parent_id, case_id = case
    _bill(monkeypatch)
    _run(case_id, parent_id)

    assert _templates(sent) == ["bill_recorded"]
    assert sent[0]["template_params"]["this_bill"] == "9,000"


def test_the_same_bill_again_says_the_amount_was_not_counted_twice(
        case, sent, monkeypatch):
    """The consequence is the whole point of the check, so state it."""
    parent_id, case_id = case
    _bill(monkeypatch)
    _run(case_id, parent_id)
    sent.clear()

    _run(case_id, parent_id)

    assert _templates(sent) == ["bill_already_recorded"]
    from anbu_care.comms.policy import render_template
    body = render_template("bill_already_recorded", sent[0]["template_params"])
    assert "not been counted twice" in body


def test_a_blocked_document_message_still_reaches_the_family(
        case, sent, monkeypatch):
    """If the gate refuses even the safe summary, silence is not the answer."""
    from anbu_care.tools import whatsapp_tools

    def refusing(**kwargs):
        sent.append(kwargs)
        allowed = kwargs["template_name"] != "document_recorded"
        return {"allowed": allowed, "reason": "clinical"}

    monkeypatch.setattr(whatsapp_tools, "send_family_update", refusing)
    parent_id, case_id = case
    _document(monkeypatch, "lab_report", {"observations": [
        {"name": "Troponin I", "value": "0.94", "flag": "high"}]})
    _run(case_id, parent_id)

    assert _templates(sent) == ["document_recorded", "document_recorded_withheld"]


def test_every_outcome_says_something(case, sent, monkeypatch):
    """A background task that dies quietly is indistinguishable from one that
    never ran, which is precisely what 'nothing happens' looked like."""
    parent_id, case_id = case
    monkeypatch.setenv("ANBU_DOC_VISION_MODE", "gemini")

    def explode(image, mime_type):
        raise RuntimeError("vertex is down")

    monkeypatch.setattr(dv, "_call_model", explode)
    _run(case_id, parent_id)
    assert len(sent) == 1, "an exploded read told the family nothing"
