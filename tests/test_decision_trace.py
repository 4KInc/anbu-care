"""The decision trace.

One property matters more than every other test in this file: **the trace
cannot invent a step**. A view that could add a beat the chain does not contain
would turn a record of an agent into a story about one, and the difference is
the entire argument this project makes.

So the tests are mostly adversarial: feed it a case, count, and assert the
trace is exactly the receipts — no smoothing, no summarising, no inferred step
in a gap where the narrative would flow better with one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from anbu_care import service
from anbu_care.tools import insurer_tools, onboarding_tools, triage_tools
from anbu_care.trace import compose_trace


@pytest.fixture
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


@pytest.fixture
def parent_id() -> str:
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-1", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True,
    )
    return pid


@pytest.fixture
def simple_case(parent_id) -> str:
    return triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="",
    )["case_id"]


@pytest.fixture
def query_fork_case(parent_id) -> str:
    """QUERY -> gather -> re-check -> resolved, entirely from real receipts."""
    case_id = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="",
    )["case_id"]

    # Round one: no discharge summary, so the adjudicator asks for it.
    first = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=parent_id, admission_summary="Cardiac ICU.",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[], admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, first["packet"]["packet_id"], "reimbursement")

    # Gather what was asked for.
    doc = onboarding_tools.ingest_document(
        parent_id, kind="discharge_summary", source_filename="d.pdf",
        summary="Admitted 19 Aug, discharged 22 Aug.", observations=[],
    )["document"]["document_id"]

    # Round two: re-assembled with the document, re-submitted.
    second = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=parent_id, admission_summary="Cardiac ICU.",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[doc], admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, second["packet"]["packet_id"], "reimbursement")
    return case_id


# =========================================================================
# (2) THE TRACE IS EXACTLY THE RECEIPTS — no synthesized step
# =========================================================================


def test_the_trace_is_exactly_the_receipts_on_the_case(query_fork_case):
    """The load-bearing test. One step per receipt, in order, nothing added."""
    receipts = service.get_chain(query_fork_case).receipts
    trace = compose_trace(query_fork_case)

    assert len(trace.steps) == len(receipts)
    assert trace.receipt_count == len(receipts)
    assert trace.synthesized_steps == 0

    # Not merely the same count — the same receipts, pairwise, in order.
    assert [s.seq for s in trace.steps] == [r.seq for r in receipts]
    assert [s.kind for s in trace.steps] == [r.kind for r in receipts]
    assert [s.actor for s in trace.steps] == [r.actor for r in receipts]
    assert [s.receipt_hash for s in trace.steps] == [r.hash for r in receipts]


def test_every_step_traces_back_to_a_real_receipt_hash(query_fork_case):
    """A step whose hash is not on the chain would be fabricated."""
    on_chain = {r.hash for r in service.get_chain(query_fork_case).receipts}
    for step in compose_trace(query_fork_case).steps:
        assert step.receipt_hash in on_chain


def test_an_unknown_receipt_kind_still_renders_rather_than_disappearing(simple_case):
    """Dropping an unlabelled receipt would also break one-step-per-receipt.

    A trace that silently omits what it does not recognise is as dishonest as
    one that invents — it just fails in the quieter direction.
    """
    service.append_receipt(
        simple_case, kind="something.unrecognised", actor="future_agent",
        payload={"note": "a kind this build has no label for"},
    )
    receipts = service.get_chain(simple_case).receipts
    trace = compose_trace(simple_case)

    assert len(trace.steps) == len(receipts)
    step = trace.steps[-1]
    assert step.kind == "something.unrecognised"
    assert step.what  # falls back to the kind rather than vanishing


def test_an_empty_case_yields_an_empty_trace_not_a_narrative(parent_id):
    case_id = service.open_case(parent_id).case_id
    receipts = service.get_chain(case_id).receipts
    trace = compose_trace(case_id)

    assert len(trace.steps) == len(receipts)
    assert trace.synthesized_steps == 0
    assert trace.query_fork is None


# =========================================================================
# (1) IT RENDERS A REAL DECISION SEQUENCE
# =========================================================================


def test_the_trace_reads_details_from_stored_payload_fields(query_fork_case):
    trace = compose_trace(query_fork_case)
    by_kind = {}
    for step in trace.steps:
        by_kind.setdefault(step.kind, []).append(step)

    triage = by_kind["triage.decision"][0]
    assert "severity" in triage.detail.lower()
    assert "HIGH" in triage.detail

    query = next(s for s in by_kind["claim.adjudicated"] if "QUERY" in s.detail)
    assert "discharge summary" in query.detail

    assembled = by_kind["claim.packet_assembled"][0]
    assert "96,000" in assembled.detail


def test_submitted_precedes_adjudicated_in_the_rendered_sequence(query_fork_case):
    """The causality fix, seen from the surface that exposed it."""
    steps = compose_trace(query_fork_case).steps
    kinds = [s.kind for s in steps]

    first_submit = kinds.index("claim.submitted")
    first_adj = kinds.index("claim.adjudicated")
    assert first_submit < first_adj


# =========================================================================
# (3) THE QUERY -> GATHER -> RE-CHECK FORK IS LEGIBLE
# =========================================================================


def test_the_query_fork_is_described_from_real_sequence_numbers(query_fork_case):
    trace = compose_trace(query_fork_case)
    fork = trace.query_fork
    assert fork is not None

    real_seqs = {s.seq for s in trace.steps}
    assert fork["queried_at_seq"] in real_seqs
    assert fork["resolved_at_seq"] in real_seqs
    assert all(seq in real_seqs for seq in fork["gathered_at_seqs"])

    assert "discharge summary" in fork["asked_for"]
    assert fork["resolved_outcome"] in {"PASS", "PARTIAL", "DENY"}
    assert fork["still_open"] is False
    assert fork["queried_at_seq"] < fork["resolved_at_seq"]


def test_rounds_are_counted_from_receipts_not_the_attempt_field(query_fork_case):
    """`attempt` stays 1 per submission, so counting it would under-report."""
    receipts = service.get_chain(query_fork_case).receipts
    adjudications = [r for r in receipts if r.kind == "claim.adjudicated"]

    assert all(r.payload.get("attempt") == 1 for r in adjudications)
    assert len(adjudications) > 1

    assert compose_trace(query_fork_case).query_fork["rounds"] == len(adjudications)


def test_a_case_with_no_query_has_no_fork(simple_case):
    """Absent means absent. The fork is never narrated into existence."""
    assert compose_trace(simple_case).query_fork is None


def test_an_unresolved_query_says_so_rather_than_implying_success(parent_id):
    case_id = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="",
    )["case_id"]
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=parent_id, admission_summary="ICU",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[], admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, pkt["packet"]["packet_id"], "reimbursement")

    fork = compose_trace(case_id).query_fork
    assert fork["still_open"] is True
    assert fork["resolved_at_seq"] is None
    assert fork["resolved_outcome"] is None


# =========================================================================
# (4) AUDITABLE AUTONOMY — verify sits beside the trace
# =========================================================================


def test_the_trace_carries_chain_integrity_and_points_at_public_verify(
    client, query_fork_case
):
    from anbu_care.webauth import DEMO_TOKEN

    response = client.get(f"/api/cases/{query_fork_case}/trace",
                          headers={"Authorization": f"Bearer {DEMO_TOKEN}"})
    assert response.status_code == 200
    body = response.json()

    assert body["chain_verified"] is True
    assert body["chain_head_hash"]
    assert body["verify_url"] == f"/api/cases/{query_fork_case}/verify"
    assert body["verify_is_public"] is True

    # And that URL really is reachable with no credential at all.
    assert client.get(body["verify_url"]).status_code == 200


def test_a_tampered_chain_shows_as_unverified_in_the_trace(query_fork_case):
    """The trace must stop saying "verified" the moment the chain is broken.

    Otherwise the pairing is worthless: a view that renders a decision sequence
    while asserting integrity it has not checked is exactly the reassurance this
    project refuses to give anywhere else.
    """
    from anbu_care.provenance.store import get_store, receipt_sk

    assert compose_trace(query_fork_case).chain_verified is True

    victim = next(r for r in service.get_chain(query_fork_case).receipts
                  if r.kind == "triage.decision")
    row = victim.model_dump(mode="json")
    row["payload"]["severity"] = "LOW"
    get_store().put(f"CASE#{query_fork_case}", receipt_sk(victim.seq), row)

    tampered = compose_trace(query_fork_case)
    assert tampered.chain_verified is False
    # The steps still render — the trace reports the damage rather than hiding
    # it, and still contains exactly the receipts that are stored.
    assert tampered.synthesized_steps == 0
    assert len(tampered.steps) == tampered.receipt_count


# =========================================================================
# CONTENT STAYS CREDENTIALED, INTEGRITY STAYS PUBLIC
# =========================================================================


def test_the_trace_is_credentialed_and_verify_is_not(client, query_fork_case):
    from anbu_care.webauth import DEMO_TOKEN

    assert client.get(f"/api/cases/{query_fork_case}/trace").status_code == 401
    assert client.get(f"/api/cases/{query_fork_case}/verify").status_code == 200
    assert client.get(
        f"/api/cases/{query_fork_case}/trace",
        headers={"Authorization": f"Bearer {DEMO_TOKEN}"},
    ).status_code == 200


def test_the_public_verify_response_leaks_no_case_content(client, query_fork_case):
    """Integrity without disclosure — the boundary the trace must not cross."""
    public = client.get(f"/api/cases/{query_fork_case}/verify").text

    for secret in ("Ashanthi", "Penicillin", "Sacred Heart", "96,000", "96000"):
        assert secret not in public


def test_an_unknown_case_is_a_404_not_an_empty_trace(client):
    from anbu_care.webauth import DEMO_TOKEN

    assert client.get("/api/cases/case-does-not-exist/trace",
                      headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).status_code == 404


# =========================================================================
# THE GATHER IS NOW A REAL STEP
# =========================================================================


def test_the_fork_shows_the_gather_from_a_real_receipt(parent_id):
    """gathered@ was empty on the deployed path because nothing recorded it.

    Now it points at claim.query_answered — and it is still a real receipt, so
    synthesized stays zero. That is the distinction the whole view rests on:
    the step became visible because it started being recorded, not because the
    renderer started inferring it.
    """
    from anbu_care.tools import insurer_tools as it

    case_id = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="",
    )["case_id"]
    pkt = it.assemble_claim_packet(
        case_id=case_id, parent_id=parent_id, admission_summary="Cardiac ICU.",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[], admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    submitted = it.submit_claim(case_id, pkt["packet"]["packet_id"], "reimbursement")

    doc = onboarding_tools.ingest_document(
        parent_id, kind="discharge_summary", source_filename="d.pdf",
        summary="Admitted 19 Aug, discharged 22 Aug.", observations=[],
    )["document"]["document_id"]
    it.respond_to_query(case_id, submitted["submission"]["submission_id"], [doc])

    trace = compose_trace(case_id)
    fork = trace.query_fork

    assert fork["gathered_at_seqs"], "the gather is still invisible"

    gather_seq = fork["gathered_at_seqs"][0]
    step = next(s for s in trace.steps if s.seq == gather_seq)
    assert step.kind == "claim.query_answered"
    assert "attached" in step.detail
    assert "discharge summary" in step.detail

    # queried -> gathered -> resolved, in that order, all real receipts.
    assert fork["queried_at_seq"] < gather_seq < fork["resolved_at_seq"]

    # The guarantee is untouched by the new kind.
    assert trace.synthesized_steps == 0
    assert len(trace.steps) == len(service.get_chain(case_id).receipts)
    assert trace.chain_verified is True
