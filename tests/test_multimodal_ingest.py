"""Ingesting what a vision model actually emits.

A model reading a lab report returns the JSON number 232, not the string "232".
Rejecting that failed the whole ingest with a 500 and — worse — left the agent
free to tell a family the document was on file when nothing had been stored.
These tests pin both halves: the values are accepted, and a failed ingest is
visibly a failure.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from anbu_care import service
from anbu_care.schemas import Observation
from anbu_care.tools import onboarding_tools


@pytest.fixture
def parent_id() -> str:
    return onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]


# ---- value coercion ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (232, "232"),          # int, as a vision model emits it
        (8.4, "8.4"),          # float
        (165.0, "165"),        # trailing .0 must not create a second reading
        ("165", "165"),        # already a string
        ("Positive", "Positive"),
        ("<0.01", "<0.01"),    # plenty of real results are not numbers
        ("Trace", "Trace"),
    ],
)
def test_observation_accepts_numbers_and_text(raw, expected):
    assert Observation(name="LDL", value=raw).value == expected


def test_float_precision_is_not_leaked_into_the_record():
    """8.4 must not be stored as 8.400000000000001."""
    assert Observation(name="HbA1c", value=8.4).value == "8.4"


def test_observation_still_requires_a_name_and_value():
    with pytest.raises(ValidationError):
        Observation.model_validate({"value": 232})
    with pytest.raises(ValidationError):
        Observation.model_validate({"name": "LDL"})


# ---- ingest happy path ---------------------------------------------------


def test_numeric_observations_are_ingested_and_persisted(parent_id):
    result = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="lab_mar2026.png",
        summary="Lipid profile.",
        observations=[
            {"name": "LDL Cholesterol", "value": 165, "unit": "mg/dL",
             "reference_range": "< 100", "flag": "high", "observed_on": "2026-03-14"},
            {"name": "HbA1c", "value": 7.1, "unit": "%", "flag": "high"},
        ],
    )
    assert result["status"] == "ingested"
    stored = service.list_documents(parent_id)
    assert len(stored) == 1
    assert [o.value for o in stored[0].observations] == ["165", "7.1"]


# ---- ingest failure is visibly a failure ---------------------------------


def test_malformed_observations_return_an_error_and_store_nothing(parent_id):
    result = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="broken.png", summary="",
        observations=[{"analyte": "LDL", "result": 165}],  # wrong keys entirely
    )
    assert result["status"] == "error"
    assert "could not parse observations" in result["error"]
    assert result["expected_keys"]
    assert service.list_documents(parent_id) == []


def test_a_failed_ingest_never_reports_success(parent_id):
    """Ground truth for the agent: success is signalled by exactly one value.

    The agent is instructed never to claim an ingest without `status: "ingested"`,
    so no failure path may produce that string.
    """
    failures = [
        [{"analyte": "LDL", "result": 165}],           # wrong keys
        [{"value": 165}],                              # no name
        [{"name": "LDL"}],                             # no value
        ["not even an object"],                        # wrong type
    ]
    for observations in failures:
        result = onboarding_tools.ingest_document(
            parent_id, kind="blood_report", source_filename="x.png",
            summary="", observations=observations,
        )
        assert result["status"] != "ingested", observations
        assert result["status"] == "error"
    assert service.list_documents(parent_id) == []


def test_malformed_ingest_does_not_raise(parent_id):
    """It must return, not raise — raising 500s the request and loses the turn."""
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="x.png",
        summary="", observations=[{"analyte": "LDL", "result": 165}],
    )


def test_stored_document_count_is_the_ground_truth(parent_id):
    """What the demo and dashboard read to contradict a false claim."""
    assert len(service.list_documents(parent_id)) == 0
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="a.png", summary="",
        observations=[{"analyte": "bad"}],
    )
    assert len(service.list_documents(parent_id)) == 0, "failed ingest must not count"
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="b.png", summary="",
        observations=[{"name": "LDL", "value": 165}],
    )
    assert len(service.list_documents(parent_id)) == 1


# ---- baseline comparison is numeric, not string equality -----------------


def test_numerically_equal_readings_count_as_baseline_not_change(parent_id):
    """165, "165" and 165.0 are one reading, not three.

    String equality would call the second one a change and tell a family a
    stable value had moved.
    """
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="a.png", summary="",
        observations=[{"name": "LDL", "value": 165, "unit": "mg/dL", "flag": "high"}],
    )
    for repeat in ("165", 165.0, 165):
        result = onboarding_tools.ingest_document(
            parent_id, kind="blood_report", source_filename="b.png", summary="",
            observations=[{"name": "LDL", "value": repeat, "unit": "mg/dL", "flag": "high"}],
        )
        assert "consistent with baseline" in result["delta_vs_baseline"], repeat
        # ": changed from" and not bare "changed from" — "unchanged from"
        # contains the latter.
        assert ": changed from" not in result["delta_vs_baseline"], repeat


def test_a_genuine_numeric_change_is_still_reported(parent_id):
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="a.png", summary="",
        observations=[{"name": "HbA1c", "value": 7.1, "flag": "high"}],
    )
    result = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="b.png", summary="",
        observations=[{"name": "HbA1c", "value": 8.4, "flag": "high"}],
    )
    assert "new and abnormal" in result["delta_vs_baseline"]
    assert ": changed from 7.1" in result["delta_vs_baseline"]


def test_first_abnormal_reading_reads_as_genuinely_new(parent_id):
    result = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="a.png", summary="",
        observations=[{"name": "LDL", "value": 165, "unit": "mg/dL", "flag": "high"}],
    )
    assert "genuinely new" in result["delta_vs_baseline"]


def test_non_numeric_readings_compare_as_text(parent_id):
    onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="a.png", summary="",
        observations=[{"name": "Culture", "value": "Positive", "flag": "abnormal"}],
    )
    same = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="b.png", summary="",
        observations=[{"name": "Culture", "value": "positive", "flag": "abnormal"}],
    )
    assert "consistent with baseline" in same["delta_vs_baseline"]

    changed = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="c.png", summary="",
        observations=[{"name": "Culture", "value": "Negative", "flag": "normal"}],
    )
    assert ": changed from" in changed["delta_vs_baseline"]
