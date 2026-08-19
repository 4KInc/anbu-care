"""The simulated adjudicator.

Deterministic rules, no model. The arithmetic here goes on screen next to a
visible policy, so it has to tie out — a judge doing mental math that does not
reconcile is worse than showing no math at all.
"""

from __future__ import annotations

from datetime import date

import pytest

from anbu_care.schemas import (
    AdjudicationOutcome,
    ClaimPacket,
    DocumentKind,
    InsurancePolicy,
)
from anbu_care.tpa import SUBLIMIT_RULES, adjudicate, stay_days

SUM_INSURED = 500_000
DISCHARGE = {DocumentKind.DISCHARGE_SUMMARY}


def policy(**kw) -> InsurancePolicy:
    return InsurancePolicy(
        insurer=kw.pop("insurer", "Star Health"),
        policy_number=kw.pop("policy_number", "SH-NRI-4471902"),
        sum_insured_inr=kw.pop("sum_insured_inr", SUM_INSURED),
        **kw,
    )


def packet(**kw) -> ClaimPacket:
    bills = kw.pop("itemized_bills_inr", {"cardiac_icu_room": 96_000, "procedures": 210_000})
    return ClaimPacket(
        packet_id=kw.pop("packet_id", "pkt-1"),
        case_id="case-1",
        parent_id="parent-1",
        itemized_bills_inr=bills,
        total_claimed_inr=sum(bills.values()),
        admitted_on=kw.pop("admitted_on", "2026-08-19"),
        discharged_on=kw.pop("discharged_on", "2026-08-22"),
        **kw,
    )


# ---- GUARD 1: the number the demo beat rests on --------------------------


def test_the_sixty_six_thousand_reconciles_from_first_principles():
    """₹66,000 must fall out of the convention, not be asserted anywhere.

    2% of sum insured per day is the conventional ICU cap. On ₹5,00,000 that is
    ₹10,000/day; a 19–22 Aug stay is 3 days, so ₹30,000 is payable against a
    ₹96,000 ICU bill, leaving ₹66,000 disallowed. Every step is recomputed here
    rather than hard-coded, so narration and code cannot drift apart.
    """
    days = stay_days("2026-08-19", "2026-08-22")
    assert days == 3

    per_day = round(SUM_INSURED * SUBLIMIT_RULES["cardiac_icu_room"])
    assert per_day == 10_000
    cap = per_day * days
    assert cap == 30_000
    expected_disallowed = 96_000 - cap
    assert expected_disallowed == 66_000

    result = adjudicate(packet(), policy(), DISCHARGE)
    icu = next(line for line in result.lines if line.item == "cardiac_icu_room")

    assert icu.allowed_inr == cap
    assert icu.disallowed_inr == expected_disallowed
    assert result.total_disallowed_inr == expected_disallowed
    # The rule string is what a judge reads on screen — it must carry the math.
    assert "10,000/day" in icu.rule
    assert "3 day(s)" in icu.rule
    assert "30,000" in icu.rule


def test_displayed_totals_equal_the_sum_of_the_lines():
    """Narration reads totals; totals must equal the lines they summarise."""
    result = adjudicate(packet(), policy(), DISCHARGE)
    assert result.total_allowed_inr == sum(line.allowed_inr for line in result.lines)
    assert result.total_claimed_inr - result.total_allowed_inr == result.total_disallowed_inr


# ---- the four branches ---------------------------------------------------


def test_pass_when_everything_is_within_limits():
    result = adjudicate(
        packet(itemized_bills_inr={"cardiac_icu_room": 25_000, "pharmacy": 4_000}),
        policy(), DISCHARGE,
    )
    assert result.outcome is AdjudicationOutcome.PASS
    assert result.total_disallowed_inr == 0
    assert result.reasons


def test_partial_cites_the_rule_not_just_the_amount():
    result = adjudicate(packet(), policy(), DISCHARGE)
    assert result.outcome is AdjudicationOutcome.PARTIAL
    assert any("sub-limit" in r for r in result.reasons)
    assert any("66,000" in r for r in result.reasons)


def test_query_when_the_discharge_summary_is_missing():
    result = adjudicate(packet(), policy(), set())
    assert result.outcome is AdjudicationOutcome.QUERY
    assert result.missing_documents == ["discharge_summary"]
    assert any("discharge summary" in r for r in result.reasons)


def test_query_is_evaluated_before_partial():
    """A payable figure cannot be computed while a document is missing.

    The same packet that would price to PARTIAL must return QUERY when the
    discharge summary is absent — asking first, pricing second.
    """
    priced = adjudicate(packet(), policy(), DISCHARGE)
    queried = adjudicate(packet(), policy(), set())
    assert priced.outcome is AdjudicationOutcome.PARTIAL
    assert queried.outcome is AdjudicationOutcome.QUERY
    assert queried.total_allowed_inr == 0
    assert not queried.lines


def test_deny_on_a_lapsed_policy():
    result = adjudicate(
        packet(), policy(valid_until="2026-01-01"), DISCHARGE,
        today=date(2026, 8, 19),
    )
    assert result.outcome is AdjudicationOutcome.DENY
    assert any("lapsed" in r for r in result.reasons)


def test_deny_when_every_line_is_a_conventional_exclusion():
    result = adjudicate(
        packet(itemized_bills_inr={"toiletries": 900, "attendant_charges": 4_000}),
        policy(), DISCHARGE,
    )
    assert result.outcome is AdjudicationOutcome.DENY
    assert any("exclusion" in r for r in result.reasons)


def test_deny_with_no_policy_on_record():
    assert adjudicate(packet(), None, DISCHARGE).outcome is AdjudicationOutcome.DENY


def test_deny_on_an_empty_bill():
    result = adjudicate(packet(itemized_bills_inr={}), policy(), DISCHARGE)
    assert result.outcome is AdjudicationOutcome.DENY


# ---- honesty invariants --------------------------------------------------


def test_every_result_labels_itself_simulated():
    for kinds in (DISCHARGE, set()):
        result = adjudicate(packet(), policy(), kinds)
        assert result.simulated is True
        assert "SIMULATED" in result.adjudicator
        assert "not an insurer" in result.adjudicator


def test_adjudication_is_deterministic():
    """Same packet twice must give an identical answer, or the demo is not
    reproducible and the receipt trail means nothing."""
    runs = [adjudicate(packet(), policy(), DISCHARGE) for _ in range(5)]
    assert len({r.outcome for r in runs}) == 1
    assert len({r.total_disallowed_inr for r in runs}) == 1
    assert len({tuple(r.reasons) for r in runs}) == 1


# ---- dates: refuse to guess ----------------------------------------------


def test_missing_stay_dates_raise_a_query_rather_than_assuming_one_day():
    result = adjudicate(packet(admitted_on=None, discharged_on=None), policy(), DISCHARGE)
    assert result.outcome is AdjudicationOutcome.QUERY
    assert result.missing_documents == ["admission_and_discharge_dates"]


@pytest.mark.parametrize(
    ("admitted", "discharged", "expected"),
    [
        ("2026-08-19", "2026-08-22", 3),
        ("2026-08-19", "2026-08-19", 1),   # same-day stay counts as one
        ("2026-08-19", "2026-08-18", None),  # discharge before admission
        ("not-a-date", "2026-08-22", None),
        (None, "2026-08-22", None),
    ],
)
def test_stay_days(admitted, discharged, expected):
    assert stay_days(admitted, discharged) == expected


def test_sum_insured_is_the_ceiling_on_everything_payable():
    result = adjudicate(
        packet(itemized_bills_inr={"procedures": 900_000}), policy(), DISCHARGE,
    )
    assert result.outcome is AdjudicationOutcome.PARTIAL
    assert result.total_allowed_inr == SUM_INSURED
    assert any("sum insured" in line.rule for line in result.lines)
