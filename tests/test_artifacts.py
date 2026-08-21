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


# ---- the attachment cannot get past the gate either -----------------------


@pytest.fixture
def seeded_case(monkeypatch):
    """A case with an adjudication, and a family contact who consented."""
    from anbu_care import service
    from anbu_care.tools import onboarding_tools

    parent_id = onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Heartlin", relationship="son",
        whatsapp_e164="+14155550142", timezone_name="America/Los_Angeles",
        is_primary=True,
        consent_purposes=["admission_alerts", "status_updates", "billing_updates",
                          "claim_updates"],
    )
    case = service.open_case(parent_id)
    service.save_adjudication(_adjudication(case_id=case.case_id))
    return parent_id, case.case_id


def _send(parent_id, case_id, status, attach):
    from anbu_care.tools import whatsapp_tools

    return whatsapp_tools.send_family_update(
        case_id=case_id, parent_id=parent_id, to_e164="+14155550142",
        template_name="status_update",
        template_params={"parent_name": "Amma", "status": status,
                         "hospital_name": "Sacred Heart Hospital", "timestamp": "4:12 PM"},
        message_class="status", attach_claim_summary=attach,
    )


def test_a_blocked_message_attaches_nothing(seeded_case, monkeypatch):
    """The gate returns before the artifact is ever built.

    If a blocked message could still trigger an upload, a clinical case's
    paperwork would be sitting behind a live signed URL despite the send being
    refused.
    """
    from anbu_care.comms import artifacts

    built: list[str] = []
    real_build = artifacts.build
    monkeypatch.setattr(
        artifacts, "build",
        lambda kind, adj: (built.append(kind), real_build(kind, adj))[1],
    )

    parent_id, case_id = seeded_case
    result = _send(parent_id, case_id,
                   "stable, troponin I 0.94 ng/mL and ECG shows ST elevation", attach=True)

    assert result["allowed"] is False
    assert built == [], "an artifact was built for a message the gate blocked"


def test_no_bucket_means_no_attachment_and_no_false_claim(seeded_case, monkeypatch):
    """Storage unavailable must degrade to a plain message, not a broken one."""
    monkeypatch.delenv("ANBU_ARTIFACT_BUCKET", raising=False)
    parent_id, case_id = seeded_case
    result = _send(parent_id, case_id, "resting comfortably", attach=True)

    assert result["allowed"] is True
    attachment = result.get("attachment") or {}
    assert attachment.get("attached") is False
    assert "nothing was uploaded" in attachment.get("reason", "")
    assert "url" not in attachment


def test_a_case_with_no_adjudication_attaches_nothing(monkeypatch):
    from anbu_care import service
    from anbu_care.tools import onboarding_tools

    parent_id = onboarding_tools.create_parent_profile(
        name="No Claim", age=70, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Child", relationship="son",
        whatsapp_e164="+14155550142", timezone_name="Asia/Kolkata", is_primary=True,
        consent_purposes=["status_updates"],
    )
    case = service.open_case(parent_id)
    result = _send(parent_id, case.case_id, "resting comfortably", attach=True)

    assert result["allowed"] is True
    assert (result.get("attachment") or {}).get("attached") is False
    assert "no adjudication" in (result.get("attachment") or {}).get("reason", "")


def test_not_asking_for_an_attachment_leaves_the_message_untouched(seeded_case):
    parent_id, case_id = seeded_case
    result = _send(parent_id, case_id, "resting comfortably", attach=False)
    assert result["allowed"] is True
    assert result.get("attachment") is None


def test_latest_adjudication_wins(seeded_case):
    """A QUERY answered later must not be represented by the first attempt."""
    from anbu_care import service

    _, case_id = seeded_case
    service.save_adjudication(
        _adjudication(case_id=case_id, adjudication_id="adj-2", attempt=2,
                      outcome=AdjudicationOutcome.PASS, total_allowed_inr=96000,
                      total_disallowed_inr=0)
    )
    latest = service.latest_adjudication(case_id)
    assert latest is not None
    assert latest.adjudication_id == "adj-2"
    assert latest.outcome is AdjudicationOutcome.PASS


def test_the_document_is_written_like_the_messages_are():
    """Same rule as the templates. A family reads both."""
    art = artifacts.build("claim_summary", _adjudication())
    assert "—" not in art.text
    assert "–" not in art.text


def test_the_document_says_what_the_family_will_pay():
    """The figure a family actually wants is the shortfall, and it was already
    in the adjudicator output. Reporting claimed and allowed while leaving the
    reader to subtract is a worse document, not a more careful one."""
    art = artifacts.build("claim_summary", _adjudication())
    assert "expected to pay INR 66,000" in art.text


def test_a_fully_covered_claim_does_not_ask_for_money():
    art = artifacts.build(
        "claim_summary",
        _adjudication(outcome=AdjudicationOutcome.PASS, total_allowed_inr=96000,
                      total_disallowed_inr=0,
                      lines=[LineAssessment(item="ICU bed", claimed_inr=96000,
                                            allowed_inr=96000, disallowed_inr=0,
                                            rule="within sub-limit")]),
    )
    assert "expected to pay" not in art.text


def test_the_timestamp_is_readable():
    art = artifacts.build("claim_summary", _adjudication())
    assert "T" not in art.text.split("Assessed on ")[1][:20]  # not ISO-8601
    assert "UTC" in art.text
