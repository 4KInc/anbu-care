"""Photographed documents: classification, extraction, and where each one lands.

Four kinds of document reach four different parts of the record, and two of
them OVERWRITE something a person relies on. So the tests are mostly about the
refusals: what happens when a reading is partial, when a document is the wrong
kind, and when the same photograph arrives twice.
"""

from __future__ import annotations

import json
import pathlib

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
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
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
               "patient_name": "Ashanthi M.", kind: body, **top}
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
    assert "2 results" in summary
    assert "troponin i" in summary                 # names what was flagged
    for verdict in ("serious", "urgent", "concerning", "suggests", "consistent with"):
        assert verdict not in summary


# =========================================================================
# THE MESSAGE MUST SURVIVE THE GATE IT SHARES A SYSTEM WITH
# =========================================================================


def test_the_message_summary_never_names_a_clinical_finding(parent_id, monkeypatch):
    """This shipped, and the family got silence.

    The record summary names which analytes were flagged, which is right for a
    credentialed view. The same sentence in a WhatsApp message was classified
    clinical and BLOCKED — correctly, because "Flagged: Troponin I, CK-MB" is
    exactly what the gate exists to stop. The document was on file and nobody
    was told.

    So there are two summaries: the count is the news, the names are the record.
    """
    from anbu_care.comms.policy import MessageClass, TEMPLATES, classify_message, gate_message
    from anbu_care.docvision.ingest import message_summary_for

    payload = {"observations": [
        {"name": "Troponin I", "value": "0.94", "flag": "high"},
        {"name": "CK-MB", "value": "38", "flag": "high"},
        {"name": "Sodium", "value": "138", "flag": "normal"}]}

    safe = message_summary_for("lab_report", payload)
    assert "3 results" in safe and "2 outside" in safe
    for analyte in ("troponin", "ck-mb", "sodium"):
        assert analyte not in safe.lower()

    body = str(TEMPLATES["document_recorded"]["body"]).format(
        parent_name="Ashanthi", document_kind="lab report", summary=safe,
        applied_line="", dashboard_url="https://example/app")
    assert classify_message(body)[0] is not MessageClass.CLINICAL
    assert gate_message(body, "logistics", template_name="document_recorded").allowed


def test_the_record_summary_still_names_them(parent_id, monkeypatch):
    """Behind the credential, a family should see which results were flagged."""
    _reads(monkeypatch, "lab_report", {"observations": [
        {"name": "Troponin I", "value": "0.94", "flag": "high"}]})
    result = ingest_document_image(parent_id, IMAGE, "image/png")

    assert "Troponin I" in result["summary"]
    assert "Troponin" not in result["message_summary"]


def test_every_document_kind_produces_a_sendable_message(parent_id):
    """A template that cannot be sent is a family left in silence."""
    from anbu_care.comms.policy import TEMPLATES, gate_message
    from anbu_care.docvision.ingest import message_summary_for

    payloads = {
        "lab_report": {"observations": [{"name": "Troponin I", "value": "0.94", "flag": "high"}]},
        "discharge_summary": {"admitted_on": "2026-08-19", "discharged_on": "2026-08-22",
                              "diagnosis": "Acute coronary syndrome",
                              "discharge_medications": [{"name": "Aspirin"}]},
        "prescription": {"medications": [{"name": "Aspirin"}, {"name": "Clopidogrel"}]},
        "policy_schedule": {"insurer": "Star Health", "sum_insured_inr": 500_000,
                            "copay_percent": 10},
    }
    for kind, payload in payloads.items():
        summary = message_summary_for(kind, payload)
        body = str(TEMPLATES["document_recorded"]["body"]).format(
            parent_name="Ashanthi", document_kind=kind.replace("_", " "),
            summary=summary, applied_line="", dashboard_url="https://example/app")
        verdict = gate_message(body, "logistics", template_name="document_recorded")
        assert verdict.allowed, f"{kind} would be blocked: {summary}"
        # And the diagnosis in particular must not ride along.
        assert "coronary" not in body.lower()


def test_a_withheld_fallback_exists_for_when_the_gate_still_refuses():
    """The wellbeing lane already learned this: never leave them untold."""
    from anbu_care.comms.policy import TEMPLATES, gate_message

    body = str(TEMPLATES["document_recorded_withheld"]["body"]).format(
        parent_name="Ashanthi", document_kind="lab report",
        dashboard_url="https://example/app")
    assert gate_message(body, "logistics",
                        template_name="document_recorded_withheld").allowed
    assert "not carried over WhatsApp" in body


# =========================================================================
# A DUPLICATE IS NOT A FAILURE
# =========================================================================


def test_the_second_send_says_it_is_already_on_file_not_that_it_failed(
        parent_id, monkeypatch):
    """This shipped, and it read as total failure.

    A lab report was sent twice. The second one was correctly recognised as the
    same photograph and correctly not recorded again — and the family was told
    "that bill could not be read. Send a clearer one, or add the amounts by
    hand." Three lies in one message: it WAS read, it is not a bill, and there
    is nothing to send again.
    """
    _reads(monkeypatch, "lab_report", {"observations": [
        {"name": "Troponin I", "value": "0.94", "flag": "high"}]})
    ingest_document_image(parent_id, IMAGE, "image/png")

    with pytest.raises(DocumentRejected) as caught:
        ingest_document_image(parent_id, IMAGE, "image/png")

    rejected = caught.value
    assert rejected.already_recorded is True
    assert rejected.subject == "lab report"
    assert "could not be read" not in str(rejected)
    assert "clearer" not in str(rejected)


def test_a_document_that_really_is_unreadable_is_not_marked_already_recorded(
        parent_id, monkeypatch):
    """The flag has to separate the two, or it has done nothing."""
    monkeypatch.setenv("ANBU_DOC_VISION_MODE", "gemini")
    monkeypatch.setattr(dv, "_call_model", lambda image, mime_type: json.dumps(
        {"kind": "lab_report", "unreadable": True, "unreadable_reason": "too dark"}))

    with pytest.raises(DocumentRejected) as caught:
        ingest_document_image(parent_id, IMAGE, "image/png")
    assert caught.value.already_recorded is False


def test_a_duplicate_costs_neither_a_stored_object_nor_a_model_call(
        parent_id, monkeypatch):
    """The check runs before both, the way the bill lane already does it.

    Reading a photograph that is about to be thrown away is a wasted Gemini
    call, and storing it is a second copy of a file already held.
    """
    _reads(monkeypatch, "prescription", {"medications": [{"name": "Aspirin"}]})
    ingest_document_image(parent_id, IMAGE, "image/png")

    calls = {"read": 0, "store": 0}
    from anbu_care.comms import storage as gcs

    monkeypatch.setattr(dv, "_call_model", lambda *a, **k: calls.__setitem__(
        "read", calls["read"] + 1) or "{}")
    monkeypatch.setattr(gcs, "store", lambda *a, **k: calls.__setitem__(
        "store", calls["store"] + 1) or StoredArtifact(
            stored=True, url="x", object_name="y", detail="", expires_in_seconds=1))

    with pytest.raises(DocumentRejected):
        ingest_document_image(parent_id, IMAGE, "image/png")
    assert calls == {"read": 0, "store": 0}


def test_no_document_message_ever_calls_it_a_bill():
    """The document lane borrowed the bill lane's failure template. A lab
    report described as an unreadable bill is how this was found."""
    from anbu_care.comms.policy import TEMPLATES

    for name in ("document_recorded", "document_recorded_withheld",
                 "document_unreadable", "document_already_recorded"):
        body = str(TEMPLATES[name]["body"])
        assert "bill" not in body.lower(), f"{name} calls a document a bill"
        assert "amounts by hand" not in body


def test_the_duplicate_messages_do_not_read_as_failures():
    from anbu_care.comms.policy import TEMPLATES, gate_message, render_template

    doc = render_template("document_already_recorded",
                          {"parent_name": "Amma", "subject": "lab report"})
    bill = render_template("bill_already_recorded", {"parent_name": "Amma"})
    for body in (doc, bill):
        for wrong in ("could not", "unreadable", "failed", "try again",
                      "send a clearer"):
            assert wrong not in body.lower(), f"reads as a failure: {body}"
        assert "not been" in body.lower()  # says what did NOT happen: no double

    assert gate_message(doc, "logistics",
                        template_name="document_already_recorded").allowed
    assert gate_message(bill, "billing",
                        template_name="bill_already_recorded").allowed


def test_the_record_view_reports_a_prescription_as_a_prescription():
    """A prescription filed under "Lab results" and rendered as "not yet known"
    is a successfully-read document reported as missing data.

    Six medications came off that photograph and reached the profile. The view
    showed an unknown, because it asked every document for lab observations and
    treated their absence as a gap rather than as what a prescription is.
    """
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()

    assert '<div class="sec">Documents on file</div>' in page
    assert '<div class="sec">Lab results</div>' not in page
    assert 'DOC_LABEL' in page and 'prescription:"Prescription"' in page
    # The unknown is now reserved for a lab report that read nothing.
    assert 'this document carries no structured observations' not in page
    assert 'd.kind==="blood_report"' in page
    # The extractor's own sentence is shown rather than discarded.
    assert 'd.summary?`<p' in page


def test_the_baseline_shows_medication():
    """It is what a treating clinician reads immediately after allergies, and
    it is what a photographed prescription updates."""
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()
    assert 'row("Medication"' in page


def test_the_record_view_does_not_explain_itself_to_a_judge():
    """Copy that argues for the architecture belongs on the audit tab.

    The record is what a family reads when someone is in hospital. A panel
    explaining why the WhatsApp gate can refuse lab values, and a counter
    labelled "ground truth", are addressed to a reviewer rather than to them.
    """
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()
    record = page[page.index("function vRecord()"):page.index("function openBillPhoto")]

    for pitch in ("This is the clinical view", "ground truth",
                  "not from what an agent said", "server-enforced"):
        assert pitch not in record, f"the record view still pitches: {pitch}"
    # The lock chip went too, on a second pass. The view is credentialed either
    # way; saying so on the page was one more line addressed to a reviewer.
    assert "Credentialed access" not in record
    # And nothing in this view speaks the system's vocabulary at the reader.
    assert "ingested" not in record


# =========================================================================
# THE DOCUMENT SAYS MORE THAN ONE SENTENCE
# =========================================================================


DISCHARGE = {
    "admitted_on": "2026-08-19", "discharged_on": "2026-08-22",
    "hospital": "Sacred Heart Hospital", "consultant": "Dr A. Anand",
    "diagnosis": "Non-ST elevation acute coronary syndrome",
    "condition_at_discharge": "Stable, ambulant",
    "allergies": ["Penicillin", "Sulfa drugs"],
    "discharge_medications": [{"name": "Aspirin", "dose": "75 mg"},
                              {"name": "Clopidogrel", "dose": "75 mg"}],
    "follow_up_on": "2026-08-29",
}


def test_everything_read_off_the_page_is_kept(parent_id, monkeypatch):
    """A one-line summary was the only thing stored, so a discharge summary
    with a diagnosis, two dates, a consultant and a follow-up date came out the
    other side as a single sentence and nothing else."""
    _reads(monkeypatch, "discharge_summary", DISCHARGE)
    ingest_document_image(parent_id, IMAGE, "image/png")

    doc = service.list_documents(parent_id)[-1]
    assert doc.details["diagnosis"] == "Non-ST elevation acute coronary syndrome"
    assert doc.details["follow_up_on"] == "2026-08-29"
    assert doc.details["consultant"] == "Dr A. Anand"
    assert len(doc.details["discharge_medications"]) == 2


def test_a_discharge_summary_updates_the_medication_and_allergies(
        parent_id, monkeypatch):
    _reads(monkeypatch, "discharge_summary", DISCHARGE)
    result = ingest_document_image(parent_id, IMAGE, "image/png")

    profile = service.load_profile(parent_id)
    assert [m.name for m in profile.medications] == ["Aspirin", "Clopidogrel"]
    assert "Sulfa drugs" in profile.allergies
    assert "discharged on 2026-08-22" in result["applied"]


def test_a_discharge_summary_never_removes_an_allergy(parent_id, monkeypatch):
    """It lists what that admission recorded. A shorter list is not a
    retraction of an allergy somebody has carried for years, and dropping one
    on that reading could kill them."""
    payload = dict(DISCHARGE, allergies=["Sulfa drugs"])
    _reads(monkeypatch, "discharge_summary", payload)
    ingest_document_image(parent_id, IMAGE, "image/png")

    allergies = service.load_profile(parent_id).allergies
    assert "Penicillin" in allergies, "an allergy on file was dropped"
    assert "Sulfa drugs" in allergies


def test_the_arrival_brief_learns_the_discharge_date_from_the_photograph(
        parent_id, monkeypatch):
    """"No discharge date has been recorded" while the family is holding the
    discharge summary they just sent tells them the system lost it."""
    from anbu_care.brief import composer

    case_id = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]

    before = {f.label: f for f in composer.compose_brief(case_id).facts}
    assert before["Expected discharge"].known is False

    _reads(monkeypatch, "discharge_summary", DISCHARGE)
    ingest_document_image(parent_id, IMAGE, "image/png", case_id=case_id)

    after = {f.label: f for f in composer.compose_brief(case_id).facts}
    assert after["Discharged on"].value == "2026-08-22"
    assert after["Admitted on"].value == "2026-08-19"
    assert after["Diagnosis on discharge"].value.startswith("Non-ST")
    assert after["Follow-up due"].value == "2026-08-29"
    # And it says where it came from, rather than appearing by magic.
    assert after["Discharged on"].source.kind == "document"


def test_a_packet_date_is_not_relabelled_as_an_actual_discharge():
    """They are different claims. A packet carries the date the claim was built
    around; a discharge summary is the hospital saying she went home."""
    composer = (pathlib.Path(__file__).resolve().parents[1]
                / "anbu_care" / "brief" / "composer.py").read_text()
    assert 'discharge_label = "Expected discharge"' in composer
    assert 'discharge_label = "Discharged on"' in composer


def test_the_record_view_can_open_the_paper_it_read(parent_id, monkeypatch):
    """A figure nobody can check against the page it came from is worth little,
    which is why the bill lane has this and the document lane did not."""
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()
    assert "openDocPhoto" in page
    assert "documents/${encodeURIComponent(documentId)}/image" in page
    assert "docDetails(d)" in page


def test_a_dosing_schedule_is_shown_as_a_schedule_not_a_sentence():
    """"1 - 0 - 0 after breakfast 30 days" is three doses, a food rule and a
    duration, printed as one string because that is how the paper prints it."""
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()
    assert "function parseDose(" in page
    assert 'const SLOT_TITLES = ["morning", "afternoon", "night"]' in page
    assert ".slot.on{" in page


def test_the_renderer_never_invents_a_dosing_time():
    """Where the prescription says "once daily" rather than 1-0-0, the words
    are shown as written. Choosing a slot would be a renderer inventing a
    dosing time, which is exactly the kind of thing nobody double-takes at."""
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "anbu_care" / "webui" / "index.html").read_text()
    body = page[page.index("function parseDose("):page.index("const SLOT_TITLES")]
    # The only thing that can produce slots is the printed d-d-d pattern.
    assert body.count("slots =") == 2          # the null init and the pattern branch
    assert "slots = [pattern[1], pattern[2], pattern[3]]" in body
    for guess in ("once daily", "twice", "morning", "night"):
        assert guess not in body, f"parseDose infers a slot from {guess!r}"


def test_counts_read_as_sentences():
    from anbu_care.docvision.ingest import _plural

    assert _plural(1, "result") == "1 result"
    assert _plural(2, "result") == "2 results"
