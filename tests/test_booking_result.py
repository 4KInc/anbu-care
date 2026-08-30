"""What an arriving lab report closes, and everything it refuses to close.

The booking lane could submit a request and then never learn anything again, so
a case sat at "the centre has not answered" for as long as it existed. A report
photographed by the family is the one signal that arrives on its own, and this
is the loop that reads it.

Almost every test here is a refusal, for the same reason the rest of the
booking suite is. Closing the wrong test is not a cosmetic error: it tells a
family a blood test was carried out when it was not, and it stops the lane
booking the one that still needs booking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anbu_care import service
from anbu_care.booking import result as booking_result
from anbu_care.schemas import Appointment, DiagnosticOrder
from anbu_care.tools import onboarding_tools, triage_tools

TODAY = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

CENTRE = {"place_id": "place-near", "name": "Anbu Diagnostics", "distance_km": 2.1,
          "score": 0.71, "home_collection": False, "address": "Palayamkottai Rd"}


@pytest.fixture
def case():
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    cid = triage_tools.run_triage(
        parent_id=pid, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]
    return pid, cid


def _appointment(parent_id, case_id, *, status="requested", label="blood test",
                 requested_at=None, cancelled_at=None):
    order = DiagnosticOrder(
        order_id=service.new_id("dxorder"), case_id=case_id, parent_id=parent_id,
        test_label=label, ordered_by="the treating team (unverified)")
    service.save_diagnostic_order(order)
    appt = Appointment(
        appointment_id=service.new_id("appt"), case_id=case_id,
        parent_id=parent_id, order_id=order.order_id, status=status,
        place_id="place-near", centre_name="Anbu Diagnostics",
        requested_at=requested_at or (TODAY - timedelta(days=2)),
        cancelled_at=cancelled_at,
    )
    service.save_appointment(appt)
    return appt


def _report(collected_on="2026-08-20"):
    return {"collected_on": collected_on,
            "observations": [{"name": "Haemoglobin", "value": "11.2"}]}


def _reload(case_id, appointment_id):
    return next(a for a in service.list_appointments(case_id)
                if a.appointment_id == appointment_id)


# --- the loop it exists to close --------------------------------------------

def test_a_report_closes_the_one_test_that_was_outstanding(case):
    pid, cid = case
    appt = _appointment(pid, cid)

    closure = booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)

    assert closure.outcome == "closed"
    assert closure.appointment_id == appt.appointment_id
    after = _reload(cid, appt.appointment_id)
    assert after.status == booking_result.RESULTED
    assert after.resulted_by_document == "doc_1"
    assert after.resulted_at == TODAY


def test_a_confirmed_slot_is_superseded_by_evidence_she_actually_went(case):
    # A centre confirming and her attending are different facts, and the
    # second is the one a family is waiting to hear.
    pid, cid = case
    appt = _appointment(pid, cid, status="confirmed")
    closure = booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)
    assert closure.outcome == "closed"
    assert _reload(cid, appt.appointment_id).status == booking_result.RESULTED


def test_closing_is_receipted_and_the_receipt_claims_only_what_happened(case):
    pid, cid = case
    _appointment(pid, cid)
    booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)

    receipt = next(r for r in service.load_receipts(cid)
                   if r.kind == "booking.resulted")
    note = receipt.payload["note"]
    assert "not the centre confirming" in note
    assert "No value in the report was read" in note
    # The chain carries the date and the document, never a reading.
    assert receipt.payload["collected_on"] == "2026-08-20"
    assert "observations" not in receipt.payload
    assert "Haemoglobin" not in str(receipt.payload)


# --- the refusals ------------------------------------------------------------

def test_two_outstanding_tests_means_nothing_is_closed(case):
    # The one that matters. Attributing a report to one of two orders means
    # reading it to decide which, and that is a model choosing which clinical
    # order was carried out.
    pid, cid = case
    a = _appointment(pid, cid, label="blood test")
    b = _appointment(pid, cid, label="thyroid panel")

    closure = booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)

    assert closure.outcome == "ambiguous"
    assert _reload(cid, a.appointment_id).status == "requested"
    assert _reload(cid, b.appointment_id).status == "requested"


def test_an_unattributable_report_still_leaves_a_trace(case):
    pid, cid = case
    _appointment(pid, cid)
    _appointment(pid, cid, label="thyroid panel")
    booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)

    receipt = next(r for r in service.load_receipts(cid)
                   if r.kind == "booking.result_not_attributed")
    assert len(receipt.payload["open_appointments"]) == 2
    assert "will not guess" in receipt.payload["note"]


def test_a_report_older_than_the_order_closes_nothing(case):
    # An old result photographed for the insurer is not evidence of a visit
    # that had not been arranged yet.
    pid, cid = case
    appt = _appointment(pid, cid, requested_at=TODAY - timedelta(days=1))
    closure = booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(collected_on="2026-08-01"), now=TODAY)

    assert closure.outcome == "predates_the_order"
    assert _reload(cid, appt.appointment_id).status == "requested"


def test_a_report_with_no_readable_date_still_closes_and_says_so(case):
    pid, cid = case
    appt = _appointment(pid, cid)
    closure = booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(collected_on=None), now=TODAY)

    assert closure.outcome == "closed"
    assert _reload(cid, appt.appointment_id).status == booking_result.RESULTED
    receipt = next(r for r in service.load_receipts(cid)
                   if r.kind == "booking.resulted")
    assert "no date the reader could make out" in receipt.payload["note"]


@pytest.mark.parametrize("kind", ["bill", "prescription", "discharge_summary",
                                  "policy_schedule", "other",
                                  # the stored spelling, which is NOT the
                                  # reader's word and must not match
                                  "blood_report"])
def test_no_other_document_can_say_she_attended(case, kind):
    # A bill or a prescription can arrive for the same admission without
    # anybody having gone anywhere.
    pid, cid = case
    appt = _appointment(pid, cid)
    closure = booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind=kind, payload=_report(), now=TODAY)

    assert closure.outcome == "not_a_result"
    assert _reload(cid, appt.appointment_id).status == "requested"


def test_a_cancelled_appointment_is_not_reopened_by_a_result(case):
    pid, cid = case
    appt = _appointment(pid, cid, status="cancelled",
                        cancelled_at=TODAY - timedelta(hours=1))
    closure = booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)

    assert closure.outcome == "nothing_open"
    assert _reload(cid, appt.appointment_id).status == "cancelled"


def test_an_escalated_appointment_is_not_closed_by_a_result(case):
    # Escalated means every attempt failed and nothing was ever booked, so a
    # report arriving is about a visit somebody arranged themselves.
    pid, cid = case
    appt = _appointment(pid, cid, status="escalated")
    closure = booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)

    assert closure.outcome == "nothing_open"
    assert _reload(cid, appt.appointment_id).status == "escalated"


def test_a_report_on_no_admission_closes_nothing(case):
    closure = booking_result.close_from_document(
        case_id="", document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)
    assert closure.outcome == "no_case"


def test_closing_twice_is_not_possible(case):
    # The second report finds nothing open, because the first one closed it.
    pid, cid = case
    appt = _appointment(pid, cid)
    first = booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)
    second = booking_result.close_from_document(
        case_id=cid, document_id="doc_2", kind="lab_report",
        payload=_report(), now=TODAY)

    assert first.outcome == "closed"
    assert second.outcome == "nothing_open"
    assert _reload(cid, appt.appointment_id).resulted_by_document == "doc_1"


def test_a_resulted_test_is_never_booked_again(case):
    # The duplicate guard has to count `resulted` as live, or the lane would
    # helpfully book a second slot for a test whose result is already filed —
    # and take it from somebody who still needs one.
    from anbu_care.booking import enforcer
    from anbu_care.booking import mandate as mandates

    pid, cid = case
    onboarding_tools.record_booking_disclosure_consent(pid)
    appt = _appointment(pid, cid)
    booking_result.close_from_document(
        case_id=cid, document_id="doc_1", kind="lab_report",
        payload=_report(), now=TODAY)

    order = next(o for o in service.list_diagnostic_orders(cid)
                 if o.order_id == appt.order_id)
    order.options = [CENTRE]
    service.save_diagnostic_order(order)
    mandate = mandates.grant_standing(parent_id=pid, granted_by="Heartlin")

    verdict = enforcer.decide(
        order=order, mandate=mandates.live_for_case(cid) or mandate,
        centre=CENTRE, options=order.options,
        existing=service.list_appointments(cid), case_id=cid,
        cancel_url="https://x/cancel")

    assert verdict.allowed is False
    assert verdict.failed_check == "not_duplicate"
    assert "resulted" in verdict.reason


# --- through the real ingest, which is where the wiring can be wrong ---------
#
# The unit tests above call close_from_document directly, so they pass happily
# while the hook that reaches it is comparing against a word the reader never
# emits. These go through ingest_document_image, which is the only place that
# mismatch shows up.

def test_a_photographed_report_closes_the_test_end_to_end(case, monkeypatch):
    import json

    from anbu_care.comms import storage as gcs
    from anbu_care.comms.storage import StoredArtifact
    from anbu_care.docvision import ingest as ingest_mod
    from anbu_care.docvision import read as dv

    pid, cid = case
    appt = _appointment(pid, cid)

    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=True, url="https://signed/x", object_name=f"artifacts/{filename}",
        detail="stub", expires_in_seconds=900))
    monkeypatch.setenv("ANBU_DOC_VISION_MODE", "gemini")
    monkeypatch.setattr(dv, "_call_model", lambda image, mime_type: json.dumps({
        "kind": "lab_report", "confidence": 0.95, "unreadable": False,
        "patient_name": "Ashanthi Machado",
        "lab_report": {"collected_on": "2026-08-20",
                       "observations": [{"name": "Haemoglobin", "value": "11.2"}]},
    }))

    out = ingest_mod.ingest_document_image(
        pid, b"\x89PNG\r\n\x1a\n" + b"x" * 9000, "image/png", case_id=cid)

    assert out["closed_test"] is not None, (
        "the report went in and closed nothing; the hook and the reader "
        "disagree about what a lab report is called")
    assert out["closed_test"]["outcome"] == "closed"
    assert _reload(cid, appt.appointment_id).status == booking_result.RESULTED


def test_a_photographed_prescription_closes_nothing_end_to_end(case, monkeypatch):
    import json

    from anbu_care.comms import storage as gcs
    from anbu_care.comms.storage import StoredArtifact
    from anbu_care.docvision import ingest as ingest_mod
    from anbu_care.docvision import read as dv

    pid, cid = case
    appt = _appointment(pid, cid)

    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=True, url="https://signed/x", object_name=f"artifacts/{filename}",
        detail="stub", expires_in_seconds=900))
    monkeypatch.setenv("ANBU_DOC_VISION_MODE", "gemini")
    monkeypatch.setattr(dv, "_call_model", lambda image, mime_type: json.dumps({
        "kind": "prescription", "confidence": 0.95, "unreadable": False,
        "patient_name": "Ashanthi Machado",
        "prescription": {"medications": [{"name": "Telmisartan", "dose": "40 mg"}]},
    }))

    out = ingest_mod.ingest_document_image(
        pid, b"\x89PNG\r\n\x1a\n" + b"y" * 9000, "image/png", case_id=cid)

    assert out["closed_test"] is None
    assert _reload(cid, appt.appointment_id).status == "requested"
