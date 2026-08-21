"""Notifying the people around a parent, without overclaiming any of it.

Three things could go wrong here and each one would be a lie of a different
kind. Sending to someone who did not agree. Letting a diagnosis out because
"it's only logistics". And reporting a fan-out as though everyone got it when
one number was dead. The tests below are arranged in that order.
"""

from __future__ import annotations

import pytest

from anbu_care import service
from anbu_care.care_circle import notify as care_notify
from anbu_care.comms import consent, transport
from anbu_care.tools import onboarding_tools, whatsapp_tools

NEIGHBOUR = "+919000000101"
SIBLING = "+919000000102"
DOCTOR = "+919000000103"


@pytest.fixture
def accepting(monkeypatch):
    """A transport that accepts everything, and counts what it was handed."""
    carried: list[tuple[str, str]] = []

    def fake_send(to_e164, body, mode=None, media_url=None):
        carried.append((to_e164, body))
        return transport.DeliveryResult(
            delivered=True, channel="spy", detail="accepted", provider_id="SM-spy",
        )

    monkeypatch.setattr(transport, "send", fake_send)
    return carried


def _parent_with(contacts: list[tuple[str, str, list[str]]]) -> str:
    parent_id = onboarding_tools.create_parent_profile(
        name="Rajeswari Manickam", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        parent_id, insurer="Star Health", policy_number="SH-1", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True,
    )
    for name, number, purposes in contacts:
        onboarding_tools.record_family_contact(
            parent_id=parent_id, name=name, relationship="neighbour",
            whatsapp_e164=number, timezone_name="Asia/Kolkata",
            is_primary=False, consent_purposes=purposes,
        )
    return parent_id


def _notify(case_id, parent_id):
    return care_notify.notify(
        case_id=case_id, parent_id=parent_id,
        hospital_name="Sacred Heart Hospital", timestamp="21 Aug, 04:12 UTC",
        cashless_status="Cashless is available at network hospitals",
    )


# ---- GUARD 1: the two directions are different agreements ----------------


def test_a_contact_who_may_only_report_is_never_sent_anything(accepting):
    """Agreeing to file check-ins is not agreeing to be messaged.

    This is the same conflation that was live in W1, in the opposite
    direction. If one flag governed both, this contact would be notified.
    """
    parent_id = _parent_with([("Meena", NEIGHBOUR, [consent.INBOUND_WELLBEING])])
    case = service.open_case(parent_id)

    results = _notify(case.case_id, parent_id)

    assert len(results) == 1
    assert results[0].consented is False
    assert results[0].delivered is False
    assert consent.OUTBOUND_NOTIFY in results[0].reason
    assert accepting == [], "a contact with inbound-only consent was sent a message"


def test_a_contact_who_may_only_be_notified_cannot_file_a_report(accepting):
    """The mirror. Outbound consent grants nothing inbound."""
    from anbu_care.comms import inbound

    parent_id = _parent_with([("Meena", NEIGHBOUR, [consent.OUTBOUND_NOTIFY])])
    service.register_whatsapp_number(NEIGHBOUR, parent_id, "Meena")

    assert inbound.resolve_sender(NEIGHBOUR) is None


def test_the_care_circle_is_the_consent_not_a_roster(accepting):
    """Membership is computed from consent, so it cannot drift from it."""
    parent_id = _parent_with([
        ("Meena", NEIGHBOUR, [consent.OUTBOUND_NOTIFY]),
        ("Ravi", SIBLING, [consent.INBOUND_WELLBEING]),
    ])
    circle = [c.name for c in care_notify.care_circle(parent_id)]
    assert circle == ["Meena"]


def test_role_is_a_label_and_grants_nothing(accepting):
    """Being called care_circle is not consent."""
    parent_id = _parent_with([("Dr Iyer", DOCTOR, [])])
    profile = service.load_profile(parent_id)
    profile.family_contacts[0].role = "care_circle"
    service.save_profile(profile)
    case = service.open_case(parent_id)

    results = _notify(case.case_id, parent_id)
    assert results[0].role == "care_circle"
    assert results[0].consented is False
    assert accepting == [], "the role field was treated as authorisation"


# ---- GUARD 2: the override is not a bypass -------------------------------


def test_clinical_content_is_blocked_even_with_the_override_set(monkeypatch):
    """purpose_override changes which consent is demanded. Nothing else.

    A second outbound route "because it's only logistics" is how a diagnosis
    eventually escapes, so the notice goes through the same gate as everything
    else, and the gate still reads the rendered body.
    """
    carried: list[str] = []
    monkeypatch.setattr(
        transport, "send",
        lambda to, body, mode=None, media_url=None: carried.append(body),
    )

    parent_id = _parent_with([("Meena", NEIGHBOUR, [consent.OUTBOUND_NOTIFY])])
    case = service.open_case(parent_id)

    result = whatsapp_tools.send_family_update(
        case_id=case.case_id, parent_id=parent_id, to_e164=NEIGHBOUR,
        template_name="care_circle_notice",
        template_params={
            "parent_name": "Amma", "hospital_name": "Sacred Heart Hospital",
            "timestamp": "04:12 UTC",
            # A clinical finding smuggled into a logistics slot.
            "cashless_status": "troponin I 0.94 ng/mL, ECG shows ST elevation",
        },
        message_class="logistics",
        purpose_override=consent.OUTBOUND_NOTIFY,
    )

    assert result["allowed"] is False
    assert result["status"] == "blocked"
    assert carried == [], "clinical content reached the transport with an override set"

    chain = service.get_chain(case.case_id)
    assert [r.kind for r in chain.receipts if r.kind.startswith("comms.")] == ["comms.blocked"]


def test_the_override_does_not_change_the_gate_verdict(monkeypatch):
    """Same body, same gate result, with and without the override."""
    from anbu_care.comms.policy import gate_message, render_template
    from anbu_care.schemas import MessageClass

    body = render_template("care_circle_notice", {
        "parent_name": "Amma", "hospital_name": "Sacred Heart Hospital",
        "timestamp": "04:12 UTC", "cashless_status": "Cashless is available",
    })
    plain = gate_message(body, MessageClass.LOGISTICS, template_name="care_circle_notice")
    assert plain.allowed is True

    dirty = render_template("care_circle_notice", {
        "parent_name": "Amma", "hospital_name": "Sacred Heart Hospital",
        "timestamp": "04:12 UTC", "cashless_status": "HbA1c 8.4, diagnosis confirmed",
    })
    assert gate_message(dirty, MessageClass.LOGISTICS,
                        template_name="care_circle_notice").allowed is False


# ---- GUARD 3: per contact, never aggregated ------------------------------


def test_one_bad_number_produces_one_failure_and_two_sends(monkeypatch):
    """The whole point of the fan-out.

    "The care circle was notified" is false for the person who was not, and a
    single aggregate would make that false statement on the chain.
    """
    def selective(to_e164, body, mode=None, media_url=None):
        if to_e164 == SIBLING:
            return transport.DeliveryResult(
                delivered=False, channel="spy", http_status=400,
                detail="provider rejected the recipient: unreachable number",
            )
        return transport.DeliveryResult(
            delivered=True, channel="spy", detail="accepted", provider_id="SM-ok",
        )

    monkeypatch.setattr(transport, "send", selective)

    parent_id = _parent_with([
        ("Meena", NEIGHBOUR, [consent.OUTBOUND_NOTIFY]),
        ("Ravi", SIBLING, [consent.OUTBOUND_NOTIFY]),
        ("Dr Iyer", DOCTOR, [consent.OUTBOUND_NOTIFY]),
    ])
    case = service.open_case(parent_id)

    results = _notify(case.case_id, parent_id)
    delivered = [r.contact_name for r in results if r.delivered]
    failed = [r.contact_name for r in results if r.consented and not r.delivered]

    assert sorted(delivered) == ["Dr Iyer", "Meena"]
    assert failed == ["Ravi"]

    kinds = [r.kind for r in service.get_chain(case.case_id).receipts
             if r.kind.startswith("comms.")]
    assert sorted(kinds) == ["comms.not_delivered", "comms.sent", "comms.sent"]

    # And no receipt claims Ravi was sent to.
    for receipt in service.get_chain(case.case_id).receipts:
        if receipt.kind == "comms.sent":
            assert receipt.payload["to_e164"] != SIBLING


def test_every_contact_appears_in_the_result_including_the_skipped(accepting):
    """A contact without consent is reported as skipped, not omitted.

    Omitting them would make the result read as though the circle were smaller
    than it is.
    """
    parent_id = _parent_with([
        ("Meena", NEIGHBOUR, [consent.OUTBOUND_NOTIFY]),
        ("Ravi", SIBLING, []),
    ])
    case = service.open_case(parent_id)

    results = _notify(case.case_id, parent_id)
    assert {r.contact_name for r in results} == {"Meena", "Ravi"}
    assert [r.consented for r in results] == [True, False]


# ---- GUARD 4: consent is read live ---------------------------------------


def test_no_consent_means_nothing_sent_and_nothing_receipted_as_sent(accepting):
    parent_id = _parent_with([("Meena", NEIGHBOUR, [])])
    case = service.open_case(parent_id)

    results = _notify(case.case_id, parent_id)
    assert results[0].consented is False
    assert accepting == []
    assert [r.kind for r in service.get_chain(case.case_id).receipts
            if r.kind == "comms.sent"] == []


def test_withdrawing_consent_takes_effect_on_the_next_notification(accepting):
    """Read live from the profile, not cached at registration."""
    parent_id = _parent_with([("Meena", NEIGHBOUR, [consent.OUTBOUND_NOTIFY])])
    case = service.open_case(parent_id)

    assert _notify(case.case_id, parent_id)[0].delivered is True
    assert len(accepting) == 1

    profile = service.load_profile(parent_id)
    profile.family_contacts[0].consents = {}
    service.save_profile(profile)

    second = _notify(case.case_id, parent_id)
    assert second[0].consented is False
    assert len(accepting) == 1, "a message went out after consent was withdrawn"


# ---- outbound only, and it says so ---------------------------------------


def test_the_notice_says_no_reply_is_needed_and_shares_no_medicine(accepting):
    parent_id = _parent_with([("Dr Iyer", DOCTOR, [consent.OUTBOUND_NOTIFY])])
    case = service.open_case(parent_id)
    _notify(case.case_id, parent_id)

    _, body = accepting[0]
    assert "No reply is needed" in body
    assert "no medical details are shared here" in body
    assert "Sacred Heart Hospital" in body


def test_nothing_in_the_care_circle_opens_a_channel_back():
    """Structural: this package must not import the inbound machinery."""
    import inspect

    source = inspect.getsource(care_notify)
    for forbidden in ("resolve_sender", "verify_twilio_signature", "wellbeing"):
        assert forbidden not in source, (
            f"care_circle references {forbidden}; this is an outbound-only feature"
        )
