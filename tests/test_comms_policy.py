"""The WhatsApp boundary is a legal one. It has to hold against a message that
is trying to slip past it, not just against an obvious one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anbu_care.comms.policy import (
    TEMPLATES,
    classify_message,
    consent_ok,
    gate_message,
    render_template,
)
from anbu_care.schemas import MessageClass

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "body",
    [
        "ECG shows ST elevation in leads II, III, aVF.",
        "Troponin I is 0.94 ng/mL.",
        "Her HbA1c came back at 8.4%.",
        "Diagnosis: acute myocardial infarction.",
        "Start Clopidogrel 75 mg twice daily.",
        "LDL 165 mg/dL, flagged high.",
        "Scan findings suggest a small infarct.",
    ],
)
def test_clinical_content_is_blocked_however_it_is_phrased(body):
    result = gate_message(body, MessageClass.STATUS, last_inbound_at=NOW, now=NOW)
    assert not result.allowed
    assert result.message_class is MessageClass.CLINICAL


def test_declaring_a_clinical_message_as_logistics_does_not_get_it_through():
    """The gate classifies the content, not the caller's claim about it."""
    result = gate_message(
        "Just logistics: troponin 0.94 ng/mL, heading to cath lab.",
        MessageClass.LOGISTICS,
        last_inbound_at=NOW,
        now=NOW,
    )
    assert not result.allowed
    assert result.detected_clinical


@pytest.mark.parametrize(
    "body",
    [
        "Amma is being taken to Sacred Heart Hospital, Thoothukudi.",
        "Admitted at Sacred Heart, 4:12 PM.",
        "Dr. Ravi (Cardiology) is attending to her.",
        "Bill summary: INR 358500 across 4 items.",
    ],
)
def test_logistics_status_and_billing_pass(body):
    result = gate_message(body, MessageClass.STATUS, last_inbound_at=NOW, now=NOW)
    assert result.allowed


def test_free_form_allowed_inside_the_24_hour_window():
    result = gate_message(
        "She is being taken to Sacred Heart.",
        MessageClass.LOGISTICS,
        last_inbound_at=NOW - timedelta(hours=23),
        now=NOW,
    )
    assert result.allowed
    assert not result.requires_template


def test_free_form_blocked_outside_the_window_without_a_template():
    result = gate_message(
        "She is being taken to Sacred Heart.",
        MessageClass.LOGISTICS,
        last_inbound_at=NOW - timedelta(hours=25),
        now=NOW,
    )
    assert not result.allowed
    assert result.requires_template


def test_template_permits_a_send_outside_the_window():
    result = gate_message(
        "Anbu Care: Amma — admitted at Sacred Heart, 4:12 PM.",
        MessageClass.STATUS,
        last_inbound_at=NOW - timedelta(days=3),
        template_name="status_update",
        now=NOW,
    )
    assert result.allowed
    assert result.requires_template


def test_window_boundary_is_inclusive_at_exactly_24_hours():
    result = gate_message(
        "Status: stable.",
        MessageClass.STATUS,
        last_inbound_at=NOW - timedelta(hours=24),
        now=NOW,
    )
    assert result.allowed


def test_unknown_template_does_not_bypass_the_window():
    result = gate_message(
        "Status: stable.",
        MessageClass.STATUS,
        last_inbound_at=None,
        template_name="not_a_real_template",
        now=NOW,
    )
    assert not result.allowed


def test_every_template_renders_and_passes_its_own_gate():
    """A template that cannot pass the gate would be a trap for the agent."""
    sample = {
        "parent_name": "Amma", "hospital_name": "Sacred Heart Hospital",
        "hospital_area": "Thoothukudi", "reason_short": "chest pain, being assessed",
        "status": "admitted", "timestamp": "4:12 PM", "stage": "under review",
        "amount": "358500", "total": "358500", "line_count": "4",
        "doctor_name": "Ravi", "department": "Cardiology",
    }
    for name, spec in TEMPLATES.items():
        body = render_template(name, {k: sample[k] for k in spec["params"]})  # type: ignore[index]
        result = gate_message(body, spec["message_class"], template_name=name, now=NOW)  # type: ignore[arg-type]
        assert result.allowed, f"template '{name}' cannot pass its own gate: {result.reason}"


def test_missing_template_params_are_an_error_not_a_blank():
    with pytest.raises(ValueError):
        render_template("status_update", {"parent_name": "Amma"})


def test_consent_is_checked_per_purpose():
    consents = {"admission_alerts": NOW}
    assert consent_ok(consents, "admission_alerts")
    assert not consent_ok(consents, "billing_updates")


def test_classify_returns_the_signals_it_matched():
    _, hits = classify_message("Troponin I 0.94 ng/mL", MessageClass.STATUS)
    assert len(hits) >= 1
