"""The claim files itself when the discharge summary arrives.

This was the last lane still waiting to be asked, which is why it read as the
most staged part of the system: the one moment a family would most want it to
move on its own was the one moment somebody had to call a tool.

Most of what follows is refusals. Filing twice against one admission, or filing
with no policy or no bills, is worse than not filing at all - each one puts a
claim in front of an insurer that should not be there.
"""

from __future__ import annotations

import json

import pytest

from anbu_care import service
from anbu_care.comms.storage import StoredArtifact
from anbu_care.tools import onboarding_tools, triage_tools
from anbu_care.tpa import on_discharge

DISCHARGE = {
    "admitted_on": "2026-08-19", "discharged_on": "2026-08-22",
    "hospital": "Sacred Heart Hospital", "consultant": "Dr A. Anand, Cardiology",
    "diagnosis": "Acute coronary syndrome, managed medically.",
    "condition_at_discharge": "Stable, ambulant.",
}


@pytest.fixture(autouse=True)
def stored(monkeypatch):
    kept = {}

    def _store(filename, data, content_type=""):
        kept[filename] = data
        return StoredArtifact(stored=True, url="https://signed/x",
                              object_name=filename, detail="",
                              expires_in_seconds=900)

    from anbu_care.comms import storage as gcs
    monkeypatch.setattr(gcs, "store", _store)
    return kept


@pytest.fixture
def case():
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"])["profile"]["parent_id"]
    cid = triage_tools.run_triage(
        parent_id=pid, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]
    return pid, cid


def _policy(parent_id):
    onboarding_tools.record_insurance_policy(
        parent_id, insurer="Star Health", policy_number="SH-NRI-4471902",
        sum_insured_inr=500_000, network_hospitals=["Sacred Heart Hospital"],
        cashless_eligible=True)


def _bill(monkeypatch, case_id, parent_id, total=96_000):
    from anbu_care.bills import extract as bill_vision
    from anbu_care.bills import ingest_bill_image
    from anbu_care.docvision import read as dv

    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(bill_vision, "_call_model", lambda i, m: json.dumps({
        "vendor": "Sacred Heart Hospital", "payee_vpa": None,
        "bill_date": "2026-08-22", "stated_total_inr": total,
        "balance_due_inr": total, "is_interim": True, "unreadable": False,
        "unreadable_reason": None,
        "line_items": [{"label": "ICU bed charges", "item": "icu",
                        "amount_inr": total}]}))
    monkeypatch.setattr(dv, "read", lambda image, mime_type="image/jpeg":
                        dv.Reading(ok=True, kind="bill", engine="stub",
                                   detail="stub"))
    return ingest_bill_image(case_id, parent_id, b"\xff\xd8\xff" + b"b" * 9000,
                             "image/jpeg")


# --- the lane it exists to start --------------------------------------------

def test_a_discharge_summary_files_the_claim_with_nobody_asking(case, monkeypatch):
    pid, cid = case
    _policy(pid)
    _bill(monkeypatch, cid, pid)

    filing = on_discharge.file_on_discharge(case_id=cid, parent_id=pid,
                                            payload=DISCHARGE, document_id="doc_1")

    assert filing.filed, filing.detail
    assert filing.packet_id and filing.submission_id
    assert filing.total_claimed_inr == 96_000
    assert filing.sla_deadline, "the thirty-day clock did not start"


def test_the_thirty_day_clock_is_real_wall_time(case, monkeypatch):
    from datetime import datetime

    pid, cid = case
    _policy(pid)
    _bill(monkeypatch, cid, pid)
    filing = on_discharge.file_on_discharge(case_id=cid, parent_id=pid,
                                            payload=DISCHARGE)
    due = datetime.fromisoformat(filing.sla_deadline)
    assert (due - datetime.now(due.tzinfo)).days >= 28


def test_filing_is_receipted_and_claims_only_what_happened(case, monkeypatch):
    pid, cid = case
    _policy(pid)
    _bill(monkeypatch, cid, pid)
    on_discharge.file_on_discharge(case_id=cid, parent_id=pid, payload=DISCHARGE)

    receipt = next(r for r in service.load_receipts(cid)
                   if r.kind == "claim.filed_on_discharge")
    note = receipt.payload["note"]
    assert "simulated adjudicator" in note
    assert "real wall time" in note
    assert "has not sent it to any insurer" in note
    assert receipt.payload["sla_kind"] == "reimbursement"


# --- the refusals ------------------------------------------------------------

def test_a_second_discharge_summary_does_not_file_a_second_claim(case, monkeypatch):
    # A retaken photograph of the same paper is the ordinary case, and two
    # claims against one policy for one admission is not a small error.
    pid, cid = case
    _policy(pid)
    _bill(monkeypatch, cid, pid)

    first = on_discharge.file_on_discharge(case_id=cid, parent_id=pid,
                                           payload=DISCHARGE)
    second = on_discharge.file_on_discharge(case_id=cid, parent_id=pid,
                                            payload=DISCHARGE)

    assert first.filed
    assert second.outcome == "already_filed"
    assert second.packet_id == first.packet_id
    filed = [r for r in service.load_receipts(cid)
             if r.kind == "claim.filed_on_discharge"]
    assert len(filed) == 1


def test_no_policy_means_no_claim_and_the_chain_says_why(case, monkeypatch):
    pid, cid = case
    _bill(monkeypatch, cid, pid)

    filing = on_discharge.file_on_discharge(case_id=cid, parent_id=pid,
                                            payload=DISCHARGE)
    assert filing.outcome == "no_policy"
    receipt = next(r for r in service.load_receipts(cid)
                   if r.kind == "claim.not_filed")
    assert "no insurer to claim from" in receipt.payload["detail"]


def test_no_bills_means_no_claim(case):
    # A reimbursement claim with no amounts asks for nothing and starts a
    # thirty-day clock against an empty form.
    pid, cid = case
    _policy(pid)

    filing = on_discharge.file_on_discharge(case_id=cid, parent_id=pid,
                                            payload=DISCHARGE)
    assert filing.outcome == "no_bills"
    assert any(r.kind == "claim.not_filed" for r in service.load_receipts(cid))


def test_a_discharge_summary_on_no_admission_files_nothing(case):
    pid, _ = case
    _policy(pid)
    filing = on_discharge.file_on_discharge(case_id="", parent_id=pid,
                                            payload=DISCHARGE)
    assert filing.outcome == "no_case"


def test_dates_the_reader_could_not_read_are_passed_through_empty(case, monkeypatch):
    # Not defaulted to today. The adjudicator queries rather than applying
    # per-day sub-limits to a guess, and this must not route around that.
    pid, cid = case
    _policy(pid)
    _bill(monkeypatch, cid, pid)

    filing = on_discharge.file_on_discharge(
        case_id=cid, parent_id=pid,
        payload={**DISCHARGE, "admitted_on": None, "discharged_on": None})

    packet = service.load_packet(cid, filing.packet_id)
    assert not packet.admitted_on
    assert not packet.discharged_on


# --- the form a person could actually sign -----------------------------------

def test_a_filled_claim_form_is_stored_as_a_pdf(case, monkeypatch, stored):
    pid, cid = case
    _policy(pid)
    _bill(monkeypatch, cid, pid)
    filing = on_discharge.file_on_discharge(case_id=cid, parent_id=pid,
                                            payload=DISCHARGE)

    assert filing.form_object.startswith("claim-forms/")
    assert stored[filing.form_object][:4] == b"%PDF"


def test_the_form_fills_from_the_record_and_guesses_nothing(case, monkeypatch):
    from anbu_care.tpa import claim_form

    pid, cid = case
    _policy(pid)
    _bill(monkeypatch, cid, pid)
    filing = on_discharge.file_on_discharge(case_id=cid, parent_id=pid,
                                            payload=DISCHARGE)

    fields = dict(
        row for _, rows in claim_form.fields_for(
            profile=service.load_profile(pid),
            packet=service.load_packet(cid, filing.packet_id),
            discharge=DISCHARGE) for row in rows)

    assert fields["Policy number"] == "SH-NRI-4471902"
    assert fields["Hospital"] == "Sacred Heart Hospital"
    assert fields["Date of admission"] == "2026-08-19"
    assert fields["TOTAL CLAIMED"] == "INR 96,000"
    # A field nobody supplied is named as absent, never filled with a guess.
    assert fields["Contact number"] == claim_form.UNKNOWN


def test_the_form_is_unsigned_and_says_anbu_care_sent_it_nowhere():
    from anbu_care.tpa import claim_form

    assert "must be signed before it is submitted" in claim_form.DECLARATION
    assert "has not been sent to any insurer" in claim_form.PREPARED_BY


def test_the_form_is_never_an_attachable_artifact():
    # It states a diagnosis. `comms/artifacts.py` is the path for documents
    # that may ride on a WhatsApp message, and this must not be on it.
    from anbu_care.comms import artifacts

    assert "claim_form" not in artifacts.ATTACHABLE
    assert "claim-form" not in artifacts.ATTACHABLE
