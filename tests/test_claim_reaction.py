"""The QUERY → respond → re-adjudicate loop, at the tool layer with no model.

This is the agentic centrepiece, so its guarantees cannot depend on the model
behaving. In particular: responding to a query must be grounded in documents
that actually exist, the same way ingestion is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anbu_care import service
from anbu_care.schemas import AdjudicationOutcome, ClaimStage
from anbu_care.tools import insurer_tools, onboarding_tools


@pytest.fixture
def parent_id() -> str:
    pid = onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-1", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True,
    )
    return pid


@pytest.fixture
def queried(parent_id):
    """A submitted claim sitting on QUERY, because no discharge summary exists."""
    case = service.open_case(parent_id)
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case.case_id, parent_id=parent_id,
        admission_summary="Chest pain, cardiac ICU.",
        itemized_bills_inr={"cardiac_icu_room": 96_000, "procedures": 210_000},
        diagnostics=["ECG"], attached_document_ids=[],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    sub = insurer_tools.submit_claim(case.case_id, pkt["packet"]["packet_id"], "reimbursement")
    return case.case_id, parent_id, sub


def _add_discharge_summary(parent_id: str) -> str:
    return onboarding_tools.ingest_document(
        parent_id, kind="discharge_summary", source_filename="discharge.pdf",
        summary="Admitted 19 Aug, discharged 22 Aug.", observations=[],
    )["document"]["document_id"]


# ---- the query fires -----------------------------------------------------


def test_submission_without_a_discharge_summary_is_queried(queried):
    _, _, sub = queried
    assert sub["outcome"] == AdjudicationOutcome.QUERY.value
    assert sub["stage"] == ClaimStage.QUERIED.value
    assert sub["adjudication"]["missing_documents"] == ["discharge_summary"]


def test_a_query_starts_its_own_clock_without_stopping_the_original(queried):
    case_id, _, sub = queried
    submission = service.load_submission(case_id, sub["submission"]["submission_id"])
    assert submission.query_raised_at is not None
    assert submission.query_response_deadline is not None
    # The original SLA deadline is untouched and still reported.
    assert submission.sla_deadline is not None
    assert sub["sla"]["tracked"] is True


# ---- GUARD 3: no fabricating the missing document ------------------------


def test_responding_with_a_document_that_does_not_exist_is_refused(queried):
    case_id, _, sub = queried
    result = insurer_tools.respond_to_query(
        case_id, sub["submission"]["submission_id"], ["doc-that-never-existed"],
    )
    assert result["status"] == "error"
    assert result["unknown_document_ids"] == ["doc-that-never-existed"]
    assert "not on this parent's record" in result["error"]


def test_a_query_that_cannot_be_satisfied_reports_unresolved(parent_id, queried):
    """The record has documents, but not the one the query asked for.

    The claim has not progressed, and the tool must say so rather than let the
    agent describe movement that did not happen.
    """
    case_id, pid, sub = queried
    lab = onboarding_tools.ingest_document(
        pid, kind="blood_report", source_filename="lab.png", summary="",
        observations=[{"name": "LDL", "value": 165}],
    )["document"]["document_id"]

    result = insurer_tools.respond_to_query(
        case_id, sub["submission"]["submission_id"], [lab],
    )
    assert result["adjudication"]["outcome"] == AdjudicationOutcome.QUERY.value
    assert result["unresolved"] is True
    assert "still open" in result["unresolved_note"]


def test_packet_ids_with_no_stored_document_are_not_counted_as_attached(parent_id):
    """Ground truth: an id on the packet is not evidence a document exists."""
    case = service.open_case(parent_id)
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case.case_id, parent_id=parent_id, admission_summary="x",
        itemized_bills_inr={"procedures": 10_000}, diagnostics=[],
        attached_document_ids=["doc-imaginary"],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    sub = insurer_tools.submit_claim(case.case_id, pkt["packet"]["packet_id"], "reimbursement")
    assert sub["outcome"] == AdjudicationOutcome.QUERY.value
    assert sub["ignored_document_ids"] == ["doc-imaginary"]


# ---- the reaction resolves it -------------------------------------------


def test_attaching_the_real_document_resolves_the_query_to_partial(queried):
    case_id, pid, sub = queried
    doc_id = _add_discharge_summary(pid)

    result = insurer_tools.respond_to_query(
        case_id, sub["submission"]["submission_id"], [doc_id],
    )
    assert result["adjudication"]["outcome"] == AdjudicationOutcome.PARTIAL.value
    assert result["adjudication"]["total_disallowed_inr"] == 66_000
    assert result.get("unresolved") is not True
    assert service.load_submission(case_id, sub["submission"]["submission_id"]).stage \
        is ClaimStage.PARTIALLY_APPROVED


def test_the_whole_reaction_is_written_to_the_chain_and_verifies(queried):
    case_id, pid, sub = queried
    doc_id = _add_discharge_summary(pid)
    insurer_tools.respond_to_query(case_id, sub["submission"]["submission_id"], [doc_id])

    chain = service.get_chain(case_id)
    kinds = [r.kind for r in chain.receipts]
    assert kinds.count("claim.adjudicated") == 2, kinds
    outcomes = [r.payload["outcome"] for r in chain.receipts if r.kind == "claim.adjudicated"]
    assert outcomes == ["QUERY", "PARTIAL"]
    assert chain.verify().ok


def test_every_adjudication_receipt_is_labelled_simulated(queried):
    case_id, pid, sub = queried
    doc_id = _add_discharge_summary(pid)
    insurer_tools.respond_to_query(case_id, sub["submission"]["submission_id"], [doc_id])

    for receipt in service.get_chain(case_id).receipts:
        if receipt.kind == "claim.adjudicated":
            assert receipt.payload["simulated"] is True
            assert "SIMULATED" in receipt.payload["adjudicator"]


def test_adjudication_history_is_retrievable_in_order(queried):
    case_id, pid, sub = queried
    doc_id = _add_discharge_summary(pid)
    insurer_tools.respond_to_query(case_id, sub["submission"]["submission_id"], [doc_id])

    history = insurer_tools.list_adjudications(case_id)
    assert history["count"] == 2
    assert [a["outcome"] for a in history["adjudications"]] == ["QUERY", "PARTIAL"]
    assert [a["attempt"] for a in history["adjudications"]] == [1, 2]


# ---- guards on the reaction path ----------------------------------------


def test_cannot_respond_to_a_submission_that_was_not_queried(parent_id):
    case = service.open_case(parent_id)
    doc_id = _add_discharge_summary(parent_id)
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case.case_id, parent_id=parent_id, admission_summary="x",
        itemized_bills_inr={"pharmacy": 2_000}, diagnostics=[],
        attached_document_ids=[doc_id],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    sub = insurer_tools.submit_claim(case.case_id, pkt["packet"]["packet_id"], "reimbursement")
    assert sub["outcome"] == AdjudicationOutcome.PASS.value

    result = insurer_tools.respond_to_query(
        case.case_id, sub["submission"]["submission_id"], [doc_id],
    )
    assert result["status"] == "error"
    assert "not 'queried'" in result["error"]


def test_existing_sla_clock_maths_is_unchanged(parent_id):
    """Regression guard: the 1-hour and 30-day windows must not have moved."""
    case = service.open_case(parent_id)
    doc_id = _add_discharge_summary(parent_id)
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case.case_id, parent_id=parent_id, admission_summary="x",
        itemized_bills_inr={"pharmacy": 2_000}, diagnostics=[],
        attached_document_ids=[doc_id],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    cashless = insurer_tools.submit_claim(case.case_id, pkt["packet"]["packet_id"], "cashless_preauth")
    assert 3500 < cashless["sla"]["seconds_remaining"] <= 3600
    reimb = insurer_tools.submit_claim(case.case_id, pkt["packet"]["packet_id"], "reimbursement")
    assert reimb["sla"]["seconds_remaining"] > 29 * 24 * 3600


def test_sla_breach_still_reported_after_adjudication(queried):
    case_id, _, sub = queried
    submission = service.load_submission(case_id, sub["submission"]["submission_id"])
    submission.sla_deadline = datetime.now(UTC) - timedelta(minutes=5)
    service.save_submission(submission)
    assert insurer_tools.check_claim_sla(case_id, submission.submission_id)["sla"]["breached"] is True


# ---- the chain records causality, not just contents ----------------------


def test_a_claim_is_submitted_before_it_is_adjudicated(queried):
    """The chain must not say the insurer replied before the claim was sent.

    These two receipts are written by the same call, so their order is decided
    by us rather than by the world, and for a while we wrote it backwards. That
    was invisible until the trace view rendered the chain as a sequence a human
    reads top to bottom, where it read as "answered, then asked".

    Only the sequence position was ever wrong — the adjudication is computed
    from the same packet either way. But a provenance chain whose whole claim is
    "this is what happened, in order" cannot be out of order.
    """
    case_id, _, _ = queried
    receipts = service.get_chain(case_id).receipts

    submitted = [r for r in receipts if r.kind == "claim.submitted"]
    adjudicated = [r for r in receipts if r.kind == "claim.adjudicated"]
    assert submitted and adjudicated

    # Pair them by submission, so a second round cannot mask a regression in
    # the first by being globally later.
    for adj in adjudicated:
        submission_id = adj.payload.get("submission_id")
        its_submission = next(
            (r for r in submitted if r.payload.get("submission_id") == submission_id), None
        )
        assert its_submission is not None, "an adjudication with no submission receipt"
        assert its_submission.seq < adj.seq, (
            f"claim.adjudicated (seq {adj.seq}) precedes its own claim.submitted "
            f"(seq {its_submission.seq}) — the chain claims the insurer replied "
            f"before the claim was sent"
        )

    assert service.verify_case(case_id).ok


def test_the_order_fix_did_not_change_the_adjudication(queried):
    """Guard on the fix: only the sequence position moved."""
    case_id, _, submission = queried
    adjudication = submission["adjudication"]

    assert adjudication["outcome"] == AdjudicationOutcome.QUERY.value
    assert "discharge_summary" in adjudication["missing_documents"]
    assert adjudication["simulated"] is True

    on_chain = next(r for r in service.get_chain(case_id).receipts
                    if r.kind == "claim.adjudicated")
    assert on_chain.payload["outcome"] == adjudication["outcome"]
    assert on_chain.payload["missing_documents"] == adjudication["missing_documents"]
