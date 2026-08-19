"""Triage is the differentiating layer, so its guarantees are the ones that
have to hold on every run — not most runs."""

from __future__ import annotations

import pytest

from anbu_care.kb.hospitals import load_hospitals
from anbu_care.schemas import InsurancePolicy, ParentProfile, Severity, SymptomReport
from anbu_care.triage.routing import haversine_km, rank_hospitals, route
from anbu_care.triage.severity import classify_severity


@pytest.fixture
def parent() -> ParentProfile:
    return ParentProfile(
        parent_id="p1",
        name="Rajeswari M.",
        age=71,
        city="Thoothukudi",
        lat=8.7642,
        lon=78.1400,
        chronic_conditions=["Hypertension", "High cholesterol"],
        policy=InsurancePolicy(
            insurer="Star Health",
            policy_number="SH-1",
            sum_insured_inr=500_000,
            network_hospitals=["Sacred Heart Hospital"],
        ),
    )


# ---- severity ------------------------------------------------------------


@pytest.mark.parametrize(
    "symptoms",
    [
        ["chest pain"],
        ["crushing chest pressure"],
        ["shortness of breath"],
        ["slurred speech"],
        ["seizure"],
        ["unconscious"],
    ],
)
def test_red_flags_always_escalate_to_high(symptoms):
    assert classify_severity(symptoms).severity is Severity.HIGH


def test_red_flag_wins_even_when_wrapped_in_reassuring_words():
    result = classify_severity(
        ["chest pain"],
        "she says it's probably just gas and she feels fine now",
    )
    assert result.severity is Severity.HIGH


def test_cardiac_history_escalates_a_history_sensitive_symptom():
    without = classify_severity(["dizziness"], "", [])
    with_history = classify_severity(["dizziness"], "", ["Prior MI"])
    assert without.severity is Severity.MEDIUM
    assert with_history.severity is Severity.HIGH
    assert "cardiology" in with_history.specialties


def test_unrelated_history_does_not_escalate():
    result = classify_severity(["dizziness"], "", ["Type 2 diabetes"])
    assert result.severity is Severity.MEDIUM


def test_unmatched_complaint_defaults_to_medium_not_low():
    """An unrecognised complaint in an elderly patient is not evidence of a mild one."""
    result = classify_severity(["something feels off"], "cannot describe it")
    assert result.severity is Severity.MEDIUM


def test_low_severity_for_a_plain_minor_complaint():
    assert classify_severity(["cough"], "mild, three days").severity is Severity.LOW


def test_rationale_is_never_empty():
    for symptoms in (["chest pain"], ["cough"], ["zzz unmatched"]):
        assert classify_severity(symptoms).rationale


# ---- routing -------------------------------------------------------------


def test_high_severity_cardiac_routes_to_a_cardiac_capable_hospital(parent):
    decision = route(
        SymptomReport(parent_id="p1", reported_by="neighbour", symptoms=["chest pain"]),
        parent,
    )
    assert decision.severity is Severity.HIGH
    top = decision.ranked_hospitals[0]
    assert top.hospital.cardiac_icu is True


def test_high_severity_accepts_extra_distance_for_capability_and_network(parent):
    decision = route(
        SymptomReport(parent_id="p1", reported_by="neighbour", symptoms=["chest pain"]),
        parent,
    )
    chosen = decision.ranked_hospitals[0]
    nearest = min(decision.ranked_hospitals, key=lambda s: s.distance_km)
    assert chosen.hospital.hospital_id != nearest.hospital.hospital_id
    assert chosen.distance_km > nearest.distance_km
    assert chosen.network_match and not nearest.network_match


def test_low_severity_prefers_proximity_over_specialist_capability(parent):
    decision = route(
        SymptomReport(parent_id="p1", reported_by="parent", symptoms=["cough"]),
        parent,
    )
    assert decision.severity is Severity.LOW
    chosen = decision.ranked_hospitals[0]
    # The cardiac centres are not chosen for a cough.
    assert not chosen.hospital.cardiac_icu


def test_explanation_names_the_term_that_actually_differed(parent):
    decision = route(
        SymptomReport(parent_id="p1", reported_by="neighbour", symptoms=["chest pain"]),
        parent,
    )
    assert "farther" in decision.explanation
    # Capability is tied between the two cardiac centres, so the explanation must
    # cite the network difference and not claim a capability edge that is not there.
    assert "empanelled" in decision.explanation
    assert "capability scored" not in decision.explanation


def test_every_ranked_hospital_carries_its_reasons(parent):
    decision = route(
        SymptomReport(parent_id="p1", reported_by="neighbour", symptoms=["chest pain"]),
        parent,
    )
    assert len(decision.ranked_hospitals) == len(load_hospitals())
    for scored in decision.ranked_hospitals:
        assert len(scored.reasons) >= 3


def test_report_location_overrides_the_home_address(parent):
    away = route(
        SymptomReport(
            parent_id="p1", reported_by="neighbour", symptoms=["cough"],
            lat=8.8052, lon=78.1465,  # next to the government hospital
        ),
        parent,
    )
    home = route(
        SymptomReport(parent_id="p1", reported_by="neighbour", symptoms=["cough"]),
        parent,
    )
    away_dist = {s.hospital.hospital_id: s.distance_km for s in away.ranked_hospitals}
    home_dist = {s.hospital.hospital_id: s.distance_km for s in home.ranked_hospitals}
    assert away_dist["tut-govt-medical-college"] < home_dist["tut-govt-medical-college"]


def test_out_of_network_recommendation_is_flagged_in_the_explanation():
    """A parent whose insurer empanels nobody nearby must be told to expect
    reimbursement rather than cashless."""
    parent = ParentProfile(
        parent_id="p2", name="X", age=70, city="Thoothukudi", lat=8.7642, lon=78.1400,
        policy=InsurancePolicy(insurer="Unknown Insurer", policy_number="X-1", sum_insured_inr=100_000),
    )
    decision = route(
        SymptomReport(parent_id="p2", reported_by="parent", symptoms=["chest pain"]), parent
    )
    assert "reimbursement claim rather than cashless" in decision.explanation


def test_ranking_is_deterministic(parent):
    report = SymptomReport(parent_id="p1", reported_by="neighbour", symptoms=["chest pain"])
    runs = [
        [s.hospital.hospital_id for s in route(report, parent).ranked_hospitals]
        for _ in range(5)
    ]
    assert all(run == runs[0] for run in runs)


def test_haversine_matches_a_known_short_distance():
    # Sacred Heart to Idhayalaya, both in Thoothukudi — roughly 1.4 km apart.
    km = haversine_km(8.7833, 78.1345, 8.7712, 78.1401)
    assert 1.0 < km < 2.0


def test_specialty_requirement_beats_a_merely_listed_service():
    """A hospital that lists cardiology without a cardiac ICU must not outrank
    one that has the unit, at HIGH severity."""
    ranked = rank_hospitals(
        lat=8.7642, lon=78.1400,
        severity=Severity.HIGH,
        specialties=["cardiology", "emergency"],
        insurer="HDFC ERGO",
    )
    with_unit = [s for s in ranked if s.hospital.cardiac_icu]
    without_unit = [s for s in ranked if not s.hospital.cardiac_icu]
    assert min(s.capability_score for s in with_unit) > max(s.capability_score for s in without_unit)
