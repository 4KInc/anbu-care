"""Triage is the differentiating layer, so its guarantees are the ones that
have to hold on every run — not most runs."""

from __future__ import annotations

import re

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
    assert "empanelled" in decision.explanation

    # The original bug this guards: claiming a capability edge that does not
    # exist. Asserted as an invariant rather than a fixed scenario, so it
    # survives the hospital coordinates being corrected against Google Places
    # — which changed which hospital is nearest, and therefore whether
    # capability genuinely differs.
    match = re.search(r"capability scored ([\d.]+) there versus ([\d.]+)",
                      decision.explanation)
    if match:
        assert match.group(1) != match.group(2), (
            "explanation cites a capability edge between two equal scores"
        )


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


def test_empanelment_is_reported_as_a_listing_not_asserted_as_fact(parent):
    """Empanelment is said to be *listed*, never flatly asserted.

    The seeded caveat was removed from this sentence by request, so the verb is
    the only thing left holding it honest. These are real, named hospitals and
    the sentence is about whether a real insurer pays at them, from network data
    that is seeded and unverified. "X is listed as empanelled" is true of the
    record; "X is empanelled" is a claim about a real business that nobody has
    checked. If someone tightens the wording later, this fails first.
    """
    decision = route(
        SymptomReport(parent_id=parent.parent_id, reported_by="parent",
                      symptoms=["chest pain"]),
        parent,
    )
    assert "empanelled" in decision.explanation
    assert "listed as empanelled" in decision.explanation, (
        "the routing explanation must report empanelment as a listing, not "
        "assert it as verified fact about a real hospital"
    )


def test_out_of_network_explanation_is_also_a_listing_claim():
    """The other branch of the same claim needs the same verb."""
    parent = ParentProfile(
        parent_id="p3", name="X", age=70, city="Thoothukudi", lat=8.7642, lon=78.1400,
        policy=InsurancePolicy(insurer="Unknown Insurer", policy_number="X-2",
                               sum_insured_inr=100_000),
    )
    decision = route(
        SymptomReport(parent_id="p3", reported_by="parent", symptoms=["chest pain"]), parent
    )
    assert "not listed as empanelled" in decision.explanation


def test_kb_still_carries_the_seeded_provenance_even_though_the_ui_does_not():
    """The caveat left the sentence; it must not have left the system.

    /api/hospitals and the triage payload are now the only places a reader can
    learn that empanelment is seeded, so they carry it or nothing does.
    """
    from anbu_care.kb.hospitals import KB_META

    assert "NOT A LIVE FEED" in KB_META()["capability_status"]
    assert "empanelment" in KB_META()["warning"].lower()


def test_ranking_is_deterministic(parent):
    report = SymptomReport(parent_id="p1", reported_by="neighbour", symptoms=["chest pain"])
    runs = [
        [s.hospital.hospital_id for s in route(report, parent).ranked_hospitals]
        for _ in range(5)
    ]
    assert all(run == runs[0] for run in runs)


def test_haversine_matches_a_known_short_distance():
    # Two fixed points about 1.4 km apart. Deliberately not tied to any
    # hospital's coordinates: this tests the arithmetic, and should not break
    # when the knowledge base is re-verified against a mapping provider.
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


def test_hospital_locations_carry_their_source(parent):
    """A coordinate nobody can trace is a coordinate nobody should trust. The
    seeded values were out by up to five kilometres, which changed which
    hospital was nearest and therefore what the routing explanation said."""
    for h in load_hospitals():
        assert h.location_source == "google_places", f"{h.name} has unverified coordinates"
        assert h.place_id, f"{h.name} has no place id to re-check against"
        assert h.location_verified_on


def test_empanelment_is_still_marked_as_seeded():
    """Google can confirm a hospital exists and where. It cannot say who it
    bills. That distinction is why the caveat was narrowed, not dropped."""
    import inspect

    from anbu_care import schemas

    source = inspect.getsource(schemas.Hospital)
    assert "snapshot" in source
    assert "cannot verify" in source
