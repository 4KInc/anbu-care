"""An attachment must not become a way around the gate.

The classifier reads text. An attachment fetched from somewhere else would be
opaque to it, and a discharge summary could leave the platform with nothing
having inspected it — worse, the chain would record comms.sent as though the
gate had approved. So the only attachable document is one this codebase
generates, and its text is classified before release.

These tests assert the two halves: kinds outside the allowlist are refused, and
text that trips the classifier is refused even when its kind is allowed.
"""

from __future__ import annotations

import pytest

from anbu_care.comms import artifacts
from anbu_care.schemas import Adjudication, AdjudicationOutcome, LineAssessment


def _adjudication(**over) -> Adjudication:
    base = {
        "adjudication_id": "adj-1", "submission_id": "sub-1", "packet_id": "pkt-1",
        "case_id": "case-test01", "outcome": AdjudicationOutcome.PARTIAL,
        "reasons": ["ICU charges exceed the 2% per day sub-limit."],
        "lines": [LineAssessment(item="ICU bed", claimed_inr=96000, allowed_inr=30000,
                                 disallowed_inr=66000, rule="icu sub-limit 2%/day")],
        "total_claimed_inr": 96000, "total_allowed_inr": 30000, "total_disallowed_inr": 66000,
    }
    base.update(over)
    return Adjudication(**base)


# ---- THE ONES THAT MATTER ------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    ["discharge_summary", "blood_report", "ecg", "prescription", "anything", ""],
)
def test_only_allowlisted_kinds_can_be_built(kind):
    """A caller cannot name a clinical document and have it packaged."""
    with pytest.raises(artifacts.ArtifactRefused) as err:
        artifacts.build(kind, _adjudication())
    assert "not an attachable artifact kind" in str(err.value)


@pytest.mark.parametrize(
    "reason",
    [
        "Troponin I of 0.94 ng/mL supports the admission.",
        "ECG showed ST elevation on arrival.",
        "Discharge diagnosis: myocardial infarction.",
        "Patient prescribed 40 mg twice daily.",
    ],
)
def test_an_allowed_kind_is_still_refused_if_its_text_is_clinical(reason):
    """The allowlist is not trusted on its own.

    Adjudicator reasons are free text. If one ever quotes a reading, the
    artifact must not be released just because 'claim_summary' is allowed.
    """
    with pytest.raises(artifacts.ArtifactRefused) as err:
        artifacts.build("claim_summary", _adjudication(reasons=[reason]))
    assert "clinical detail" in str(err.value)


def test_a_clean_claim_summary_builds_and_carries_proof():
    art = artifacts.build("claim_summary", _adjudication())
    assert art.kind == "claim_summary"
    assert art.filename.endswith(".pdf")
    assert art.pdf[:4] == b"%PDF"
    assert len(art.sha256) == 64
    payload = art.as_receipt_payload()
    assert payload["sha256"] == art.sha256
    assert payload["bytes"] == len(art.pdf)


def test_the_document_agrees_with_the_adjudicator():
    """Every figure comes from adjudicator output, so the attachment cannot
    contradict the receipt chain."""
    art = artifacts.build("claim_summary", _adjudication())
    assert "PARTIAL" in art.text
    assert "96,000" in art.text      # Indian grouping, not 96,000 by accident
    assert "30,000" in art.text
    assert "icu sub-limit 2%/day" in art.text


def test_an_unpriced_outcome_is_not_written_as_zero():
    """QUERY has no payable figure yet. Printing INR 0 would state a decision
    that was never made."""
    art = artifacts.build(
        "claim_summary",
        _adjudication(outcome=AdjudicationOutcome.QUERY, lines=[],
                      total_claimed_inr=0, total_allowed_inr=0, total_disallowed_inr=0,
                      missing_documents=["discharge summary"],
                      reasons=["A required document is missing."]),
    )
    assert "not yet known" in art.text
    assert "INR 0" not in art.text


def test_the_document_says_what_it_is_not():
    art = artifacts.build("claim_summary", _adjudication())
    assert "SIMULATED TPA" in art.text
    assert "not an insurer" in art.text
    assert "Clinical detail is not included" in art.text


def test_indian_digit_grouping():
    assert artifacts._inr(500) == "500"
    assert artifacts._inr(66000) == "66,000"
    assert artifacts._inr(120000) == "1,20,000"
    assert artifacts._inr(500000) == "5,00,000"
    assert artifacts._inr(12345678) == "1,23,45,678"


# ---- storage refuses to invent a link ------------------------------------


def test_storage_without_a_bucket_reports_no_link(monkeypatch):
    from anbu_care.comms import storage

    monkeypatch.delenv("ANBU_ARTIFACT_BUCKET", raising=False)
    result = storage.store("x.pdf", b"%PDF-1.4")
    assert result.stored is False
    assert result.url is None
    assert "nothing was uploaded" in result.detail


def test_storage_failure_does_not_produce_a_url(monkeypatch):
    """A failed upload must not leave a caller holding a link to nothing."""
    from anbu_care.comms import storage

    monkeypatch.setenv("ANBU_ARTIFACT_BUCKET", "bucket-that-does-not-exist")

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no credentials")

    import google.cloud.storage as gcs

    monkeypatch.setattr(gcs, "Client", Boom)
    result = storage.store("x.pdf", b"%PDF-1.4")
    assert result.stored is False
    assert result.url is None
    assert "no link exists" in result.detail
