"""What the screens display must reconcile with the receipt order.

The Record tab shows a document on file. The Claim tab shows the adjudicator
saying that document was missing. Both are true — the query fired before the
document existed — but a judge reading one tab then the other sees a
contradiction unless the ordering is made visible.

These tests pin the ordering the UI relies on, so the reconciliation cannot
quietly start telling a story the chain does not support.
"""

from __future__ import annotations

import pytest

from anbu_care import service
from anbu_care.tools import insurer_tools, onboarding_tools
from anbu_care.tools.onboarding_tools import MATERIAL_CHANGE_FRACTION, _is_material


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


# ---- the document timeline the UI renders --------------------------------


def test_query_precedes_the_document_that_resolves_it(parent_id):
    """The ordering the Claim tab asserts on screen.

    If this ever inverts, the timeline line "added to the record, then
    resubmitted" would be describing something that did not happen.
    """
    case = service.open_case(parent_id)
    packet = insurer_tools.assemble_claim_packet(
        case_id=case.case_id, parent_id=parent_id, admission_summary="ICU",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[], admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    submitted = insurer_tools.submit_claim(
        case.case_id, packet["packet"]["packet_id"], "reimbursement")
    assert submitted["outcome"] == "QUERY"

    query_receipt = next(
        r for r in service.get_chain(case.case_id).receipts
        if r.kind == "claim.adjudicated"
    )

    doc = onboarding_tools.ingest_document(
        parent_id, kind="discharge_summary", source_filename="d.pdf",
        summary="", observations=[],
    )["document"]
    stored = next(d for d in service.list_documents(parent_id)
                  if d.document_id == doc["document_id"])

    # The document did not exist when the query fired. That is the whole point.
    assert stored.parsed_at > query_receipt.created_at

    insurer_tools.respond_to_query(
        case.case_id, submitted["submission"]["submission_id"], [doc["document_id"]])

    adjudications = [r for r in service.get_chain(case.case_id).receipts
                     if r.kind == "claim.adjudicated"]
    assert [r.payload["outcome"] for r in adjudications] == ["QUERY", "PARTIAL"]
    # Attempt 1 names what is missing; attempt 2 names nothing.
    assert adjudications[0].payload["missing_documents"] == ["discharge_summary"]
    assert adjudications[1].payload["missing_documents"] == []
    # And the resolution the UI displays is exactly that difference.
    resolved = set(adjudications[0].payload["missing_documents"]) - set(
        adjudications[1].payload["missing_documents"])
    assert resolved == {"discharge_summary"}


def test_a_document_present_from_the_start_never_triggers_a_query(parent_id):
    """Guards the other direction: no staged query when the doc was there."""
    case = service.open_case(parent_id)
    doc = onboarding_tools.ingest_document(
        parent_id, kind="discharge_summary", source_filename="d.pdf",
        summary="", observations=[],
    )["document"]["document_id"]
    packet = insurer_tools.assemble_claim_packet(
        case_id=case.case_id, parent_id=parent_id, admission_summary="ICU",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[doc], admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    result = insurer_tools.submit_claim(
        case.case_id, packet["packet"]["packet_id"], "reimbursement")
    assert result["outcome"] == "PARTIAL"
    assert result["adjudication"]["attempt"] == 1


def test_documents_arriving_mid_episode_are_distinguishable(parent_id):
    """The Record tab marks these; it needs the timestamps to do it."""
    before = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="early.png", summary="",
        observations=[{"name": "LDL", "value": 165}],
    )["document"]
    case = service.open_case(parent_id)
    after = onboarding_tools.ingest_document(
        parent_id, kind="discharge_summary", source_filename="late.pdf",
        summary="", observations=[],
    )["document"]

    opened = service.get_chain(case.case_id).receipts[0].created_at
    stored = {d.document_id: d for d in service.list_documents(parent_id)}
    assert stored[before["document_id"]].parsed_at < opened
    assert stored[after["document_id"]].parsed_at > opened


# ---- materiality band on the change narrative ----------------------------


@pytest.mark.parametrize(
    ("before", "after", "material"),
    [
        ("232", "236", False),   # 1.7% — assay drift
        ("38", "37", False),     # 2.6%
        ("180", "186", False),   # 3.3%
        ("7.1", "8.4", True),    # 18.3% — the reading that actually moved
        ("165", "198", True),    # 20%
        ("Positive", "Negative", True),   # no such thing as a small change
        ("0", "0", False),
        ("0", "4", True),
    ],
)
def test_materiality_band(before, after, material):
    assert _is_material(before, after) is material


def test_small_move_reads_as_variation_not_as_new_and_abnormal(parent_id):
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="a.png", summary="",
        observations=[{"name": "HDL", "value": 38, "unit": "mg/dL", "flag": "low"}],
    )
    result = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="b.png", summary="",
        observations=[{"name": "HDL", "value": 37, "unit": "mg/dL", "flag": "low"}],
    )
    delta = result["delta_vs_baseline"]
    assert "within normal variation" in delta
    assert "new and abnormal" not in delta
    # The reference-range flag is untouched — it is still abnormal, just stable.
    assert "still flagged abnormal" in delta


def test_hba1c_crossing_still_reads_new_and_abnormal(parent_id):
    """The standout must stay the standout."""
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="a.png", summary="",
        observations=[{"name": "HbA1c", "value": 7.1, "unit": "%", "flag": "high"}],
    )
    result = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="b.png", summary="",
        observations=[{"name": "HbA1c", "value": 8.4, "unit": "%", "flag": "high"}],
    )
    assert "new and abnormal" in result["delta_vs_baseline"]
    assert "within normal variation" not in result["delta_vs_baseline"]


def test_the_real_panel_leaves_only_hba1c_standing_out(parent_id):
    """The whole point of the band, on the actual demo numbers."""
    march = {"Total Cholesterol": 232, "LDL": 165, "HDL": 38,
             "Triglycerides": 180, "HbA1c": 7.1}
    august = {"Total Cholesterol": 236, "LDL": 165, "HDL": 37,
              "Triglycerides": 186, "HbA1c": 8.4}
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="mar.png", summary="",
        observations=[{"name": k, "value": v, "flag": "high"} for k, v in march.items()],
    )
    result = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="aug.png", summary="",
        observations=[{"name": k, "value": v, "flag": "high"} for k, v in august.items()],
    )
    notes = result["delta_vs_baseline"].split("; ")
    flagged = [n for n in notes if "new and abnormal" in n]
    assert len(flagged) == 1, notes
    assert flagged[0].startswith("HbA1c")


def test_the_band_is_narrative_only_and_never_reaches_triage():
    """Triage reads symptoms and history. It has never read a lab delta, and
    this test exists so that stays true if someone wires them together."""
    import inspect

    from anbu_care.triage import severity

    source = inspect.getsource(severity)
    for token in ("delta_vs_baseline", "_is_material", "MATERIAL_CHANGE_FRACTION"):
        assert token not in source
    assert 0 < MATERIAL_CHANGE_FRACTION < 1
