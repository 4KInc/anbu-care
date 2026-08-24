"""The emergency clinical summary.

The failure mode here is not fabrication, which the arrival brief already
guards. It is **drift into advice**. A record that says "penicillin allergy" is
useful and honest; a record that says "avoid beta-lactams" is practising
medicine, and Anbu Care is not qualified to and does not claim to. These tests
hold that line at the schema, so it cannot be crossed by adding a field.
"""

from __future__ import annotations

import pytest

from anbu_care.handoff import NOT_ON_FILE, compose_emergency_summary, render_summary_text
from anbu_care.schemas import EmergencySummary
from anbu_care.tools import onboarding_tools


@pytest.fixture
def parent() -> str:
    return onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension", "High cholesterol", "Type 2 diabetes"],
        allergies=["Penicillin"],
    )["profile"]["parent_id"]


@pytest.fixture
def bare_parent() -> str:
    return onboarding_tools.create_parent_profile(
        name="X", age=70, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]


# =========================================================================
# FACTS, NEVER ADVICE — the line this feature exists behind
# =========================================================================


def test_the_schema_has_nowhere_for_advice_to_live():
    """Structural, not textual. A field is an invitation."""
    forbidden = {
        "recommendation", "recommendations", "guidance", "advice",
        "suggested_treatment", "treatment", "plan", "severity",
        "assessment", "diagnosis", "impression", "notes", "next_steps",
    }
    present = set(EmergencySummary.model_fields)
    assert not (present & forbidden), (
        f"EmergencySummary grew a field advice could live in: {present & forbidden}"
    )


def test_rendered_summary_contains_no_directive_language(parent):
    onboarding_tools.ingest_document(
        parent, kind="lab_report", source_filename="labs.pdf", summary="Cardiac panel",
        observations=[{"name": "Troponin I", "value": "0.94 ng/mL"}],
    )
    summary = compose_emergency_summary(parent)

    # Scan what the summary ASSERTS, not the boilerplate that disclaims it —
    # the footer legitimately contains "does not recommend treatment".
    asserted = " ".join(
        f"{fact.label} {fact.value or ''} {fact.source.note or ''}"
        for bucket in (summary.allergies, summary.identity, summary.conditions,
                       summary.medications, summary.recent_labs,
                       summary.source_documents)
        for fact in bucket
    ).lower()

    for directive in (
        "avoid ", "administer", "prescribe", "recommend", "should be given",
        "do not give", "consider ", "advise", "treat with", "start ",
        "contraindicat", "dose adjust", "switch to",
    ):
        assert directive not in asserted, f"summary drifted into advice: {directive!r}"

    assert "not advice" in render_summary_text(summary).lower()


# =========================================================================
# REAL STORED FIELDS, WITH PROVENANCE
# =========================================================================


def test_summary_reads_back_stored_fields_with_their_source(parent):
    onboarding_tools.ingest_document(
        parent, kind="lab_report", source_filename="labs.pdf", summary="Cardiac panel",
        observations=[
            {"name": "Troponin I", "value": "0.94 ng/mL"},
            {"name": "Creatinine", "value": "1.3 mg/dL"},
        ],
    )
    s = compose_emergency_summary(parent)

    assert [f.value for f in s.allergies] == ["Penicillin"]
    assert all(f.source.field == "allergies" for f in s.allergies)

    conditions = {f.value for f in s.conditions}
    assert conditions == {"Hypertension", "High cholesterol", "Type 2 diabetes"}
    assert all(f.source.kind == "profile" for f in s.conditions)

    labs = {f.label: f for f in s.recent_labs}
    assert "Troponin I" in labs
    assert "0.94 ng/mL" in labs["Troponin I"].value
    assert all(f.source.kind == "document" for f in s.recent_labs)

    # Every single line, known or not, carries a source. No orphan facts.
    every = (s.allergies + s.identity + s.conditions + s.medications
             + s.recent_labs + s.source_documents)
    assert every and all(f.source is not None for f in every)


def test_a_lab_value_is_never_shown_without_its_date(parent):
    """An undated troponin is a hazard, not a data point."""
    onboarding_tools.ingest_document(
        parent, kind="lab_report", source_filename="labs.pdf", summary="",
        observations=[{"name": "Troponin I", "value": "0.94 ng/mL"}],
    )
    for fact in compose_emergency_summary(parent).recent_labs:
        if fact.known:
            assert "(" in fact.value and ")" in fact.value


# =========================================================================
# MISSING IS STATED, NEVER INFERRED
# =========================================================================


def test_missing_fields_read_not_on_file_and_never_guess(bare_parent):
    s = compose_emergency_summary(bare_parent)
    text = render_summary_text(s)

    for bucket in (s.conditions, s.medications, s.recent_labs):
        assert all(not f.known and f.value is None for f in bucket)
    assert NOT_ON_FILE in text


def test_absent_allergies_do_not_read_as_no_known_allergies(bare_parent):
    """The most dangerous empty field in the document.

    "No allergies recorded" describes our file. "No known allergies" describes
    the patient. Across a resus bay the first is read as the second, so the
    summary must refuse the shorthand and say which one it means.
    """
    s = compose_emergency_summary(bare_parent)
    assert len(s.allergies) == 1
    allergy = s.allergies[0]

    assert allergy.known is False
    assert allergy.value is None

    note = allergy.source.note.lower()
    assert "not the same as no known allergies" in note

    rendered = render_summary_text(s).lower()
    assert "no known allergies" in rendered  # only ever inside the disclaimer
    assert "nka" not in rendered


def test_summary_for_an_unknown_parent_states_it_rather_than_erroring():
    s = compose_emergency_summary("parent-that-does-not-exist")
    assert all(not f.known for f in s.allergies)
    assert all(not f.known for f in s.identity)


# =========================================================================
# HONEST LABELLING
# =========================================================================


def test_summary_says_it_is_not_connected_to_any_hospital_system(parent):
    s = compose_emergency_summary(parent)
    assert "not connected to any hospital system" in s.disclaimer.lower()
    # NOT "read-only" any more. A write-scoped link renders an order form under
    # this very sentence, and a page that understates what it grants is the
    # same defect as one that overstates it.
    assert "read-only" not in s.disclaimer.lower()
    assert "not connected to any hospital system" in render_summary_text(s).lower()
