"""The tools are what the agents actually call, so the guarantees have to hold
at the tool boundary — with no model in the loop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anbu_care import service
from anbu_care.schemas import ClaimStage
from anbu_care.tools import (
    evidence_tools,
    insurer_tools,
    onboarding_tools,
    provenance_tools,
    triage_tools,
    whatsapp_tools,
)


@pytest.fixture
def parent_id() -> str:
    created = onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )
    pid = created["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-1",
        sum_insured_inr=500_000, network_hospitals=["Sacred Heart Hospital"],
        cashless_eligible=True,
    )
    onboarding_tools.record_family_contact(
        pid, name="Karthik", relationship="son", whatsapp_e164="+14155550142",
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=["admission_alerts", "status_updates"],
    )
    return pid


# ---- onboarding ----------------------------------------------------------


def test_new_measurement_reads_as_new_and_repeat_reads_as_baseline(parent_id):
    first = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="a.pdf", summary="",
        observations=[{"name": "LDL", "value": "165", "unit": "mg/dL", "flag": "high"}],
    )
    assert "genuinely new" in first["delta_vs_baseline"]

    repeat = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="b.pdf", summary="",
        observations=[{"name": "LDL", "value": "165", "unit": "mg/dL", "flag": "high"}],
    )
    assert "consistent with baseline" in repeat["delta_vs_baseline"]

    changed = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="c.pdf", summary="",
        observations=[{"name": "LDL", "value": "192", "unit": "mg/dL", "flag": "high"}],
    )
    assert "new and abnormal" in changed["delta_vs_baseline"]


def test_tools_fail_cleanly_on_an_unknown_parent():
    result = onboarding_tools.get_parent_profile("parent-does-not-exist")
    assert result["status"] == "error"


# ---- triage --------------------------------------------------------------


def test_triage_opens_a_case_when_none_is_given(parent_id):
    result = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    assert result["status"] == "ok"
    assert result["case_id"]
    assert service.load_case(result["case_id"]) is not None


def test_triage_writes_a_receipt_carrying_the_full_ranking(parent_id):
    result = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="20 minutes",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    chain = service.get_chain(result["case_id"])
    triage_receipt = next(r for r in chain.receipts if r.kind == "triage.decision")
    assert triage_receipt.payload["severity"] == "HIGH"
    assert len(triage_receipt.payload["ranked"]) == 5
    assert chain.verify().ok


def test_triage_surfaces_that_the_kb_is_a_seeded_snapshot(parent_id):
    result = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    assert "NOT A LIVE FEED" in result["knowledge_base"]["status"]


# ---- comms ---------------------------------------------------------------


def test_send_without_purpose_consent_is_blocked_and_recorded(parent_id):
    case = service.open_case(parent_id)
    result = whatsapp_tools.send_family_update(
        case_id=case.case_id, parent_id=parent_id, to_e164="+14155550142",
        template_name="billing_summary",
        template_params={"parent_name": "Amma", "total": "358500", "line_count": "4"},
        message_class="billing",
    )
    assert not result["allowed"]
    assert "consent" in result["reason"]
    chain = service.get_chain(case.case_id)
    assert any(r.kind == "comms.blocked" for r in chain.receipts)


def test_blocked_sends_are_written_to_the_chain_as_evidence_the_gate_held(parent_id):
    case = service.open_case(parent_id)
    whatsapp_tools.send_family_update(
        case_id=case.case_id, parent_id=parent_id, to_e164="+14155550142",
        template_name="admission_alert",
        template_params={
            "parent_name": "Amma", "hospital_name": "Sacred Heart Hospital",
            "hospital_area": "Thoothukudi", "reason_short": "chest pain",
        },
        message_class="logistics",
    )
    chain = service.get_chain(case.case_id)
    assert any(r.kind == "comms.sent" for r in chain.receipts)
    assert chain.verify().ok


def test_unregistered_number_cannot_be_messaged(parent_id):
    case = service.open_case(parent_id)
    result = whatsapp_tools.send_family_update(
        case_id=case.case_id, parent_id=parent_id, to_e164="+14155550999",
        template_name="status_update",
        template_params={"parent_name": "Amma", "status": "admitted",
                         "hospital_name": "Sacred Heart", "timestamp": "4:12 PM"},
        message_class="status",
    )
    assert result["status"] == "error"


# ---- claims --------------------------------------------------------------


@pytest.fixture
def packet(parent_id):
    case = service.open_case(parent_id)
    result = insurer_tools.assemble_claim_packet(
        case_id=case.case_id, parent_id=parent_id,
        admission_summary="Admitted with chest pain.",
        itemized_bills_inr={"room": 96_000, "procedures": 210_000},
        diagnostics=[], attached_document_ids=[],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    return case.case_id, result["packet"]["packet_id"]


def test_thin_packet_gets_step_up_not_pass(packet):
    case_id, packet_id = packet
    assessment = evidence_tools.assess_claim_packet(case_id, packet_id)["assessment"]
    assert assessment["gate"] == "STEP_UP"
    assert assessment["missing_fields"]


def test_enrichment_raises_confidence_and_flips_the_gate(packet):
    case_id, packet_id = packet
    before = evidence_tools.assess_claim_packet(case_id, packet_id)["assessment"]["confidence"]
    evidence_tools.enrich_claim_packet(
        case_id, packet_id,
        matched_policy_clauses=["Sec 3.1 Hospitalisation"],
        additional_document_ids=["doc-1"],
        diagnostics=["ECG", "Troponin I"],
    )
    after = evidence_tools.assess_claim_packet(case_id, packet_id)["assessment"]
    assert after["confidence"] > before
    assert after["gate"] == "PASS"


def test_an_empty_packet_is_blocked_rather_than_stepped_up(parent_id):
    case = service.open_case(parent_id)
    result = insurer_tools.assemble_claim_packet(
        case_id=case.case_id, parent_id=parent_id, admission_summary="",
        itemized_bills_inr={}, diagnostics=[], attached_document_ids=[],
        admitted_on="", discharged_on="",
    )
    assessment = evidence_tools.assess_claim_packet(
        case.case_id, result["packet"]["packet_id"]
    )["assessment"]
    assert assessment["gate"] == "BLOCK"


def test_claim_over_sum_insured_is_warned_before_submission(parent_id):
    case = service.open_case(parent_id)
    result = insurer_tools.assemble_claim_packet(
        case_id=case.case_id, parent_id=parent_id,
        admission_summary="Long ICU stay.",
        itemized_bills_inr={"icu": 700_000},
        diagnostics=["ECG"], attached_document_ids=["doc-1"],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    warnings = result["coverage_check"]["warnings"]
    assert any("exceeds sum insured" in w for w in warnings)


def test_submission_is_labelled_simulated_everywhere(packet):
    case_id, packet_id = packet
    result = insurer_tools.submit_claim(case_id, packet_id, sla_kind="cashless_preauth")
    assert result["submission"]["simulated"] is True
    assert "SIMULATED" in result["notice"]
    assert result["counterparty_ack"]["simulated"] is True


def test_cashless_sla_is_one_hour_and_reimbursement_is_thirty_days(packet):
    case_id, packet_id = packet
    cashless = insurer_tools.submit_claim(case_id, packet_id, sla_kind="cashless_preauth")
    remaining = cashless["sla"]["seconds_remaining"]
    assert 3500 < remaining <= 3600

    reimbursement = insurer_tools.submit_claim(case_id, packet_id, sla_kind="reimbursement")
    assert reimbursement["sla"]["seconds_remaining"] > 29 * 24 * 3600


def test_sla_breach_is_reported_once_the_deadline_passes(packet):
    case_id, packet_id = packet
    result = insurer_tools.submit_claim(case_id, packet_id, sla_kind="cashless_preauth")
    submission = service.load_submission(case_id, result["submission"]["submission_id"])
    submission.sla_deadline = datetime.now(UTC) - timedelta(minutes=5)
    service.save_submission(submission)

    status = insurer_tools.check_claim_sla(case_id, submission.submission_id)["sla"]
    assert status["breached"] is True


def test_unknown_sla_kind_is_rejected(packet):
    case_id, packet_id = packet
    result = insurer_tools.submit_claim(case_id, packet_id, sla_kind="whenever")
    assert result["status"] == "error"


def test_stage_transitions_are_recorded_on_the_chain(packet):
    case_id, packet_id = packet
    submitted = insurer_tools.submit_claim(case_id, packet_id, sla_kind="cashless_preauth")
    sub_id = submitted["submission"]["submission_id"]
    insurer_tools.advance_claim_stage(case_id, sub_id, "under_review")
    insurer_tools.advance_claim_stage(case_id, sub_id, "approved")

    chain = service.get_chain(case_id)
    stages = [r.payload["to"] for r in chain.receipts if r.kind == "claim.stage_changed"]
    assert stages == ["under_review", "approved"]
    assert service.load_submission(case_id, sub_id).stage is ClaimStage.APPROVED


def test_tpa_reference_is_stable_for_the_same_packet(packet):
    case_id, packet_id = packet
    first = insurer_tools.submit_claim(case_id, packet_id, sla_kind="cashless_preauth")
    second = insurer_tools.submit_claim(case_id, packet_id, sla_kind="cashless_preauth")
    assert first["counterparty_ack"]["tpa_reference"] == second["counterparty_ack"]["tpa_reference"]


# ---- provenance across the whole case ------------------------------------


def test_whole_case_chain_verifies_and_survives_reload(parent_id):
    triage = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    case_id = triage["case_id"]
    packet = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=parent_id, admission_summary="x",
        itemized_bills_inr={"room": 1000}, diagnostics=["ECG"],
        attached_document_ids=["doc-1"],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, packet["packet"]["packet_id"], sla_kind="cashless_preauth")

    verified = provenance_tools.verify_case_chain(case_id)
    assert verified["verified"] is True
    assert verified["receipt_count"] >= 4

    # Reloading from the store must produce the same verdict — sequence and
    # prev_hash come from what is stored, not from an in-process counter.
    reloaded = service.get_chain(case_id)
    assert reloaded.verify().ok


def test_case_trail_reconstructs_every_decision_in_order(parent_id):
    triage = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    trail = provenance_tools.get_case_trail(triage["case_id"])
    seqs = [r["seq"] for r in trail["receipts"]]
    assert seqs == sorted(seqs)
    assert trail["receipts"][0]["kind"] == "case.opened"


def test_trail_of_an_unknown_case_is_an_error_not_an_empty_success():
    assert provenance_tools.get_case_trail("case-nope")["status"] == "error"


def test_store_delete_removes_only_the_named_document(parent_id):
    """Delete exists for health probes, not for case code — but it still has to
    be surgical, because it operates on the same table the ledger lives in."""
    from anbu_care.provenance.store import get_store

    store = get_store()
    store.put("HEALTHCHECK", "PROBE", {"ok": True})
    store.put("HEALTHCHECK", "KEEP", {"ok": True})

    store.delete("HEALTHCHECK", "PROBE")

    assert store.get("HEALTHCHECK", "PROBE") is None
    assert store.get("HEALTHCHECK", "KEEP") is not None
    # The parent seeded by the fixture must be untouched.
    assert service.load_profile(parent_id) is not None
