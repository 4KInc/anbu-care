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
        "cashless_status": "Cashless approval is in progress",
        "said": "I cannot catch my breath", "distance_km": "2.2",
        "why_hospital": "It is in your Star Health network, so the admission stays cashless.",
        "understood_as": "Understood as: chest pain.\n",
        "words_note": "Those are her own words, not a medical assessment.\n",
        "handoff_url": "https://example.run.app/handoff/case-x.read.0.9.sig",
        "expires_minutes": "60",
        "line_count": "16", "this_bill": "3,82,720", "running_total_line": "",
        "estimated_covered": "2,54,500", "estimated_you_pay": "1,28,220",
        "reason": "the photograph was too dark to read.",
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


# ---- the messages a person actually reads ---------------------------------


def test_templates_read_like_a_person_wrote_them():
    """No em dashes, no semicolons, no stacked clauses.

    These land on a phone at a bad hour. Punctuation that reads as generated
    costs trust exactly when the family has least of it to spare.
    """
    for name, spec in TEMPLATES.items():
        body = str(spec["body"])
        assert "—" not in body, f"{name} uses an em dash"
        assert "–" not in body, f"{name} uses an en dash"
        assert ";" not in body, f"{name} uses a semicolon"


# The dashboard link goes to the parent's credentialed record. Family get it.
# A care-circle contact is a notified party, not someone entitled to read the
# record, so their notice carries no link at all.
LINKLESS = {"care_circle_notice", "care_circle_unclear"}

# The handoff message carries exactly ONE link, and it is not the dashboard.
# Two links in one message read by a frightened person at 2am is how the wrong
# one gets shown to a nurse — and the dashboard would answer that nurse with a
# 401, which looks like the system failing rather than like a boundary holding.
# So this template is deliberately single-link, and a separate test below pins
# that the one link it carries is the handoff.
SINGLE_LINK = {"clinician_handoff_link"}


def test_every_family_template_links_to_the_dashboard():
    """The gate refuses to send clinical detail. The least it can do is say
    where that detail lives, in the same message, as something tappable."""
    for name, spec in TEMPLATES.items():
        if name in LINKLESS or name in SINGLE_LINK:
            continue
        assert "{dashboard_url}" in str(spec["body"]), f"{name} has no dashboard link"


def test_no_care_circle_template_links_into_the_record():
    """A neighbour told where someone was taken has not thereby been granted
    access to their medical record. Handing them a link to it would be a
    disclosure the consent never covered."""
    for name in ("care_circle_notice", "care_circle_unclear"):
        body = str(TEMPLATES[name]["body"])
        assert "{dashboard_url}" not in body, name
        assert "http" not in body, name


def test_the_link_cannot_be_supplied_by_the_caller():
    """dashboard_url is injected by the renderer. A caller that tries to
    override it must not be able to redirect a worried family member."""
    from anbu_care.comms.policy import DASHBOARD_URL

    body = render_template(
        "status_update",
        {"parent_name": "Amma", "status": "resting", "hospital_name": "Sacred Heart",
         "timestamp": "4:12 PM", "dashboard_url": "https://not-us.example.com"},
    )
    assert DASHBOARD_URL in body
    assert "not-us.example.com" not in body


def test_a_rendered_template_still_passes_the_gate():
    """Rewriting the copy must not accidentally trip the clinical classifier."""
    sample = {
        "parent_name": "Amma", "status": "resting comfortably",
        "hospital_name": "Sacred Heart Hospital", "timestamp": "4:12 PM",
        "hospital_area": "Palayamkottai", "reason_short": "a fall at home",
        "stage": "approved", "amount": "66,000", "total": "1,20,000",
        "line_count": "14", "doctor_name": "Iyer", "department": "Cardiology",
        "cashless_status": "Cashless approval is in progress",
        "said": "I cannot catch my breath", "distance_km": "2.2",
        "why_hospital": "It is in your Star Health network, so the admission stays cashless.",
        "understood_as": "Understood as: chest pain.\n",
        "words_note": "Those are her own words, not a medical assessment.\n",
        "handoff_url": "https://example.run.app/handoff/case-x.read.0.9.sig",
        "expires_minutes": "60",
        "line_count": "16", "this_bill": "3,82,720", "running_total_line": "",
        "estimated_covered": "2,54,500", "estimated_you_pay": "1,28,220",
        "reason": "the photograph was too dark to read.",
        "cashless_status": "Cashless approval is in progress",
        "said": "I cannot catch my breath", "distance_km": "2.2",
        "why_hospital": "It is in your Star Health network, so the admission stays cashless.",
        "understood_as": "Understood as: chest pain.\n",
        "words_note": "Those are her own words, not a medical assessment.\n",
        "handoff_url": "https://example.run.app/handoff/case-x.read.0.9.sig",
        "expires_minutes": "60",
        "line_count": "16", "this_bill": "3,82,720", "running_total_line": "",
        "estimated_covered": "2,54,500", "estimated_you_pay": "1,28,220",
        "reason": "the photograph was too dark to read.",
    }
    for name, spec in TEMPLATES.items():
        body = render_template(name, {k: sample[k] for k in spec["params"]})  # type: ignore[index]
        gate = gate_message(body, spec["message_class"], template_name=name)  # type: ignore[arg-type]
        assert gate.allowed is True, f"{name} now trips the gate: {gate.reason}"


# ---- the link has to land somewhere useful -------------------------------


def test_the_family_link_opens_the_case_it_is_about():
    """A family member arrives from an alert, on a phone, at 2am. Landing them
    on an empty dashboard and asking them to paste a case id would be the
    product failing at the only moment it matters."""
    body = render_template(
        "urgent_family_alert",
        {"parent_name": "Rajeswari", "timestamp": "1:03 AM", "said": "chest pain",
         "words_note": "Those are her own words.\n", "understood_as": "",
         "hospital_name": "Sacred Heart", "distance_km": "2.2",
         "why_hospital": "In network.", "cashless_status": "Cashless applies"},
        case_id="case-abc123",
    )
    assert "/app?case=case-abc123" in body


def test_the_host_is_still_not_caller_supplied():
    """The caller may name which of its own cases to open. It may not name
    where the link points."""
    from anbu_care.comms.policy import DASHBOARD_URL

    body = render_template(
        "claim_stage",
        {"parent_name": "Amma", "stage": "approved", "amount": "30,000",
         "dashboard_url": "https://not-us.example.com"},
        case_id="case-abc123",
    )
    assert DASHBOARD_URL in body
    assert "not-us.example.com" not in body


def test_a_case_id_is_optional():
    """Templates sent outside a case context still render."""
    body = render_template(
        "billing_summary",
        {"parent_name": "Amma", "total": "1,20,000", "line_count": "14"},
    )
    assert "?case=" not in body
    assert "/app" in body


def test_the_handoff_message_carries_the_handoff_link_and_only_that():
    """One link, and it is the one a nurse can actually open.

    The dashboard would 401 for them, so offering both links invites showing
    the wrong one to the person who needs the right one.
    """
    body = str(TEMPLATES["clinician_handoff_link"]["body"])
    assert "{handoff_url}" in body
    assert "{dashboard_url}" not in body
    assert body.count("{") - body.count("{parent_name}") - body.count(
        "{expires_minutes}") == body.count("{handoff_url}")


def test_the_handoff_message_carries_no_clinical_detail():
    """The allergies live BEHIND the link. That is the point of the link.

    A message naming what she is allergic to would be exactly what the comms
    gate exists to refuse, and putting it in a pre-approved template would be
    the most damaging way to get it past the gate.
    """
    from anbu_care.comms.policy import MessageClass, classify_message

    body = str(TEMPLATES["clinician_handoff_link"]["body"]).format(
        parent_name="Rajeswari", handoff_url="https://x/handoff/abc",
        expires_minutes="60")

    klass, hits = classify_message(body)
    assert klass is not MessageClass.CLINICAL, f"handoff message classified clinical: {hits}"
    for leak in ("penicillin", "troponin", "diabetes", "hypertension", "mg/dL"):
        assert leak not in body.lower()


def test_the_handoff_template_says_the_link_expires_and_is_recorded():
    """Both are true and both are load-bearing for the person receiving it."""
    body = str(TEMPLATES["clinician_handoff_link"]["body"]).lower()
    assert "stops working" in body
    assert "recorded" in body
    assert "no login" in body


def test_a_template_may_choose_a_view_but_never_an_address():
    """The link is still injected. A template names a tab, not a host."""
    from anbu_care.comms.policy import render_template

    body = render_template("bill_recorded", {
        "parent_name": "Rajeswari", "line_count": "16", "this_bill": "3,82,720", "running_total_line": "",
        "estimated_covered": "2,54,500", "estimated_you_pay": "1,28,220",
    }, case_id="case-x", parent_id="parent-x")

    assert "&view=claim" in body, "a bill message must open on the bill"
    assert "case=case-x" in body
    # And a caller still cannot supply the address.
    hijacked = render_template("bill_recorded", {
        "parent_name": "R", "line_count": "1", "this_bill": "1", "running_total_line": "",
        "estimated_covered": "1", "estimated_you_pay": "1",
        "dashboard_url": "https://evil.example/steal",
    }, case_id="case-x", parent_id="parent-x")
    assert "evil.example" not in hijacked


def test_only_the_bill_template_redirects_to_a_view():
    """Every other message still lands on the front page, as before."""
    for name, spec in TEMPLATES.items():
        if name == "bill_recorded":
            assert spec.get("view") == "claim"
        else:
            assert "view" not in spec, f"{name} unexpectedly names a view"
