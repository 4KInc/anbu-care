"""Photographed documents: classification, extraction, and where each one lands.

Four kinds of document reach four different parts of the record, and two of
them OVERWRITE something a person relies on. So the tests are mostly about the
refusals: what happens when a reading is partial, when a document is the wrong
kind, and when the same photograph arrives twice.
"""

from __future__ import annotations

import json

import pytest

from anbu_care import service
from anbu_care.comms.storage import StoredArtifact
from anbu_care.docvision import DocumentRejected, ingest_document_image
from anbu_care.docvision import read as dv
from anbu_care.schemas import DocumentKind
from anbu_care.tools import onboarding_tools, triage_tools

IMAGE = b"\x89PNG\r\n\x1a\n" + b"x" * 9000


@pytest.fixture
def parent_id() -> str:
    pid = onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    onboarding_tools.record_medications(pid, [{"name": "Telmisartan", "dose": "40 mg"}])
    return pid


@pytest.fixture(autouse=True)
def storage_stub(monkeypatch):
    from anbu_care.comms import storage as gcs

    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=True, url="https://signed/x", object_name=f"artifacts/{filename}",
        detail="stub", expires_in_seconds=900))


def _reads(monkeypatch, kind, body, **top):
    payload = {"kind": kind, "confidence": 0.95, "unreadable": False,
               "patient_name": "Rajeswari M.", kind: body, **top}
    monkeypatch.setenv("ANBU_DOC_VISION_MODE", "gemini")
    monkeypatch.setattr(dv, "_call_model",
                        lambda image, mime_type: json.dumps(payload))


# =========================================================================
# CLASSIFICATION
# =========================================================================


def test_each_kind_is_recognised_and_extracted(parent_id, monkeypatch):
    _reads(monkeypatch, "lab_report", {
        "collected_on": "2026-08-19",
        "observations": [{"name": "Troponin I", "value": "0.94", "unit": "ng/mL",
                          "reference_range": "< 0.04", "flag": "high"}]})
    result = ingest_document_image(parent_id, IMAGE, "image/png")

    assert result["kind"] == "lab_report"
    assert result["observations"] == 1
    doc = service.list_documents(parent_id)[-1]
    assert doc.kind is DocumentKind.BLOOD_REPORT
    obs = doc.observations[0]
    assert (obs.unit, obs.reference_range, obs.flag) == ("ng/mL", "< 0.04", "high")


def test_a_bill_is_sent_back_to_the_bill_lane(parent_id, monkeypatch):
    """Bills have their own extractor. Reading one here would lose the line items."""
    _reads(monkeypatch, "bill", {})
    with pytest.raises(DocumentRejected) as rejected:
        ingest_document_image(parent_id, IMAGE, "image/png")
    assert "hospital bill" in str(rejected.value)


def test_an_unrecognised_document_is_refused_rather_than_forced(parent_id, monkeypatch):
    """Filing a wedding invitation as a discharge summary would put a
    fabricated admission date on a claim."""
    _reads(monkeypatch, "other", {})
    with pytest.raises(DocumentRejected) as rejected:
        ingest_document_image(parent_id, IMAGE, "image/png")
    assert "does not look like" in str(rejected.value)
    assert service.list_documents(parent_id) == []


def test_an_unreadable_photograph_records_nothing(parent_id, monkeypatch):
    monkeypatch.setenv("ANBU_DOC_VISION_MODE", "gemini")
    monkeypatch.setattr(dv, "_call_model", lambda image, mime_type: json.dumps(
        {"kind": "lab_report", "unreadable": True, "unreadable_reason": "too dark"}))

    with pytest.raises(DocumentRejected) as rejected:
        ingest_document_image(parent_id, IMAGE, "image/png")
    assert "too dark" in str(rejected.value)
    assert service.list_documents(parent_id) == []


def test_the_same_photograph_twice_is_one_document(parent_id, monkeypatch):
    _reads(monkeypatch, "lab_report", {"observations": [
        {"name": "Creatinine", "value": "1.3", "unit": "mg/dL"}]})
    first = ingest_document_image(parent_id, IMAGE, "image/png")

    with pytest.raises(DocumentRejected) as rejected:
        ingest_document_image(parent_id, IMAGE, "image/png")
    assert first["document_id"] in str(rejected.value)
    assert len(service.list_documents(parent_id)) == 1


# =========================================================================
# WHAT OVERWRITES A RECORD, AND WHAT REFUSES TO
# =========================================================================


def test_a_prescription_replaces_the_medication_list(parent_id, monkeypatch):
    _reads(monkeypatch, "prescription", {"medications": [
        {"name": "Aspirin", "dose": "75 mg", "frequency": "once daily"},
        {"name": "Clopidogrel", "dose": "75 mg", "frequency": "once daily"}]})
    result = ingest_document_image(parent_id, IMAGE, "image/png")

    meds = service.load_profile(parent_id).medications
    assert [m.name for m in meds] == ["Aspirin", "Clopidogrel"]
    assert "medication list updated" in result["applied"]


def test_a_prescription_that_read_nothing_does_not_empty_the_list(parent_id, monkeypatch):
    """A badly read photograph must not delete what a clinician reads in an
    emergency. Fewer medications is a plausible reading; none is a failure."""
    _reads(monkeypatch, "prescription", {"medications": []})
    result = ingest_document_image(parent_id, IMAGE, "image/png")

    assert [m.name for m in service.load_profile(parent_id).medications] == ["Telmisartan"]
    assert "unchanged" in result["applied"]


def test_a_policy_schedule_sets_the_limits_the_estimate_uses(parent_id, monkeypatch):
    _reads(monkeypatch, "policy_schedule", {
        "insurer": "Star Health", "policy_number": "SH-1", "sum_insured_inr": 500_000,
        "room_rent_percent_per_day": 1, "icu_percent_per_day": 2,
        "copay_percent": 10, "proportionate_deduction": True,
        "network_hospitals": ["Sacred Heart Hospital"]})
    ingest_document_image(parent_id, IMAGE, "image/png")

    policy = service.load_profile(parent_id).policy
    assert policy.sum_insured_inr == 500_000
    assert policy.sub_limits_inr == {"room_rent_per_day": 5_000, "icu_per_day": 10_000}
    assert policy.copay_percent == 10
    assert policy.proportionate_deduction is True


def test_a_policy_reading_with_no_sum_insured_does_not_zero_the_cover(parent_id, monkeypatch):
    onboarding_tools.record_insurance_policy(
        parent_id, insurer="Star Health", policy_number="SH-1",
        sum_insured_inr=500_000, network_hospitals=[], cashless_eligible=True)

    _reads(monkeypatch, "policy_schedule", {"insurer": "Star Health",
                                            "sum_insured_inr": None})
    result = ingest_document_image(parent_id, IMAGE, "image/png")

    assert service.load_profile(parent_id).policy.sum_insured_inr == 500_000
    assert "unchanged" in result["applied"]


def test_a_rupee_limit_is_taken_as_printed_rather_than_as_a_percentage(parent_id, monkeypatch):
    """Policies state sub-limits either way; both end up as rupees per day."""
    _reads(monkeypatch, "policy_schedule", {
        "insurer": "X", "policy_number": "Y", "sum_insured_inr": 300_000,
        "room_rent_inr_per_day": 4_000, "icu_inr_per_day": 8_000})
    ingest_document_image(parent_id, IMAGE, "image/png")

    limits = service.load_profile(parent_id).policy.sub_limits_inr
    assert limits == {"room_rent_per_day": 4_000, "icu_per_day": 8_000}


# =========================================================================
# THE CHAIN CARRIES HASHES, NOT DIAGNOSES
# =========================================================================


def test_the_receipt_never_carries_the_document_contents(parent_id, monkeypatch):
    """A discharge summary names a diagnosis and a lab report carries results.

    Both are exactly what the comms gate refuses to put on WhatsApp, so neither
    belongs on a chain anyone can read without a credential.
    """
    case_id = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]

    _reads(monkeypatch, "discharge_summary", {
        "admitted_on": "2026-08-19", "discharged_on": "2026-08-22",
        "diagnosis": "Non-ST elevation acute coronary syndrome",
        "allergies": ["Penicillin"], "discharge_medications": []})
    ingest_document_image(parent_id, IMAGE, "image/png", case_id=case_id)

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "document.ingested")
    blob = json.dumps(receipt.payload)

    assert receipt.payload["content_sha256"]
    assert receipt.payload["document_kind"] == "discharge_summary"
    for secret in ("acute coronary", "Penicillin", "Non-ST"):
        assert secret not in blob, f"the chain leaked {secret!r}"
    assert service.verify_case(case_id).ok


def test_no_receipt_is_written_without_a_case(parent_id, monkeypatch):
    """A document can belong to a parent without belonging to an episode."""
    _reads(monkeypatch, "prescription", {"medications": [{"name": "Aspirin"}]})
    result = ingest_document_image(parent_id, IMAGE, "image/png")
    assert result["document_id"]
    assert service.list_documents(parent_id)


def test_a_document_whose_photograph_cannot_be_stored_is_refused(parent_id, monkeypatch):
    from anbu_care.comms import storage as gcs

    _reads(monkeypatch, "prescription", {"medications": [{"name": "Aspirin"}]})
    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=False, url=None, detail="ANBU_ARTIFACT_BUCKET is not set"))

    with pytest.raises(DocumentRejected) as rejected:
        ingest_document_image(parent_id, IMAGE, "image/png")
    assert "could not be stored" in str(rejected.value)
    assert service.list_documents(parent_id) == []


def test_the_summary_repeats_the_document_and_never_characterises_it(parent_id, monkeypatch):
    """It says what the paper said. It does not decide what that means."""
    _reads(monkeypatch, "lab_report", {"observations": [
        {"name": "Troponin I", "value": "0.94", "flag": "high"},
        {"name": "Sodium", "value": "138", "flag": "normal"}]})
    result = ingest_document_image(parent_id, IMAGE, "image/png")

    summary = result["summary"].lower()
    assert "2 result(s)" in summary
    assert "troponin i" in summary                 # names what was flagged
    for verdict in ("serious", "urgent", "concerning", "suggests", "consistent with"):
        assert verdict not in summary
