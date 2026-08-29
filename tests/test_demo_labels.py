"""The demo caption: who a message was for, written on the front of it.

One handset stands in for four people during a recording, so every message
lands in the same thread and the only thing separating the mother's Tamil
check-in from the son's English alert is a narrator saying so. The caption
replaces the narration.

It is a caption, which means the tests that matter are the ones about what it
must NOT do: appear when nobody asked for it, reach the content gate, hide from
the receipt, or name the wrong person.
"""

from __future__ import annotations

import pytest

from anbu_care.comms import demo_labels
from anbu_care.tools import onboarding_tools, whatsapp_tools

PARENT_PHONE = "+919000000401"
SON = "+14155550401"
NEIGHBOUR = "+919000000402"


@pytest.fixture
def family() -> str:
    parent_id = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    onboarding_tools.record_parent_channel(parent_id, PARENT_PHONE, language="ta")
    onboarding_tools.record_recovery_checkin_consent(parent_id)
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Arun Machado", relationship="son",
        whatsapp_e164=SON, timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=["admission_alerts", "status_updates", "outbound_notify"])
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Meena", relationship="neighbour",
        whatsapp_e164=NEIGHBOUR, timezone_name="Asia/Kolkata", is_primary=False,
        consent_purposes=["outbound_notify"], role="care_circle")
    return parent_id


@pytest.fixture
def carried(monkeypatch) -> list[str]:
    """Every body that actually reached a transport."""
    from anbu_care.comms import transport

    bodies: list[str] = []

    def fake(to_e164, body, mode=None, media_url=None):
        bodies.append(body)
        return transport.DeliveryResult(delivered=True, channel="test", detail="ok")

    monkeypatch.setattr(transport, "send", fake)
    return bodies


@pytest.fixture
def tags_on(monkeypatch):
    monkeypatch.setenv("ANBU_DEMO_ROLE_TAGS", "on")


def _alert(parent_id, to_e164):
    return whatsapp_tools.send_family_update(
        case_id="case-x", parent_id=parent_id, to_e164=to_e164,
        template_name="care_circle_notice",
        template_params={"parent_name": "Ashanthi", "hospital_name": "Sacred Heart",
                         "timestamp": "02:40", "cashless_status": "Cashless is being arranged"},
        message_class="logistics", purpose_override="outbound_notify")


# ---- off unless asked for ------------------------------------------------


def test_nothing_is_added_when_nobody_switched_it_on(family, carried):
    """The default is a real family's inbox, where a caption would be noise."""
    whatsapp_tools.send_parent_message(
        parent_id=family, template_name="recovery_check_in",
        template_params={"parent_name": "Ashanthi", "day": "5"},
        message_class="logistics", purpose="recovery_checkins")

    assert carried, "nothing was carried"
    assert not carried[0].startswith("\N{OLDER WOMAN}")
    assert demo_labels.tag_for(demo_labels.PARENT) == ""


def test_only_affirmative_words_switch_it_on(monkeypatch):
    for value in ("off", "false", "0", "no", "", "maybe"):
        monkeypatch.setenv("ANBU_DEMO_ROLE_TAGS", value)
        assert demo_labels.enabled() is False, value
    for value in ("on", "true", "1", "yes", "ON"):
        monkeypatch.setenv("ANBU_DEMO_ROLE_TAGS", value)
        assert demo_labels.enabled() is True, value


# ---- the four addressees are told apart ----------------------------------


def test_the_mother_and_the_son_are_captioned_differently(
        family, carried, tags_on):
    whatsapp_tools.send_parent_message(
        parent_id=family, template_name="recovery_check_in",
        template_params={"parent_name": "Ashanthi", "day": "5"},
        message_class="logistics", purpose="recovery_checkins")
    _alert(family, SON)

    to_her, to_him = carried[0], carried[1]
    assert to_her.startswith("\N{OLDER WOMAN} TO AMMA")
    assert to_him.startswith("\N{MOBILE PHONE} TO HER SON, ARUN")
    assert to_her.splitlines()[0] != to_him.splitlines()[0]


def test_the_neighbour_is_not_captioned_as_the_family(family, carried, tags_on):
    """A care circle member is a notified party, not the son. The whole point
    of the caption is that a viewer can see that difference without being told
    it."""
    _alert(family, NEIGHBOUR)

    assert carried[0].startswith("\N{HOUSE BUILDING} TO THE NEIGHBOUR, MEENA")


def test_an_unknown_addressee_gets_no_caption_rather_than_a_guess(tags_on):
    """A message captioned for the wrong person is worse than one captioned for
    nobody, because the viewer believes the caption."""
    assert demo_labels.tag_for("", "Arun") == ""
    assert demo_labels.tag_for("someone", "Arun") == ""
    body, tag = demo_labels.apply("Anbu Care: hello.", "someone")
    assert body == "Anbu Care: hello."
    assert tag == ""


# ---- it cannot become a hole ---------------------------------------------


def test_the_caption_is_added_after_the_gate_and_never_reaches_it(
        family, carried, tags_on):
    """The gate rules on the message; this puts a fixed string in front of what
    it permitted. The recorded body is the one the gate saw, so the receipt and
    the decision are about the same words."""
    result = whatsapp_tools.send_parent_message(
        parent_id=family, template_name="recovery_check_in",
        template_params={"parent_name": "Ashanthi", "day": "5"},
        message_class="logistics", purpose="recovery_checkins")

    assert result["allowed"] is True
    assert not result["message"]["body"].startswith("\N{OLDER WOMAN}"), \
        "the caption reached the record the gate ruled on"
    assert carried[0].startswith("\N{OLDER WOMAN}")


def test_a_captioned_message_says_so_on_the_delivery(family, carried, tags_on):
    """An edit to what left the platform that no receipt mentions would be an
    undisclosed edit, which is the one thing a caption must not be."""
    result = whatsapp_tools.send_parent_message(
        parent_id=family, template_name="recovery_check_in",
        template_params={"parent_name": "Ashanthi", "day": "5"},
        message_class="logistics", purpose="recovery_checkins")

    assert result["delivery"]["demo_tag"] == "\N{OLDER WOMAN} TO AMMA, ASHANTHI"


def test_a_blocked_message_is_never_captioned(family, carried, tags_on):
    """Blocked messages do not reach a transport at all, so there is nothing to
    caption. Asserted because a caption applied earlier in the chain would have
    quietly changed that."""
    whatsapp_tools.send_family_update(
        case_id="case-x", parent_id=family, to_e164=SON,
        template_name="status_update",
        template_params={"parent_name": "Ashanthi", "status": "troponin 0.94 ng/mL",
                         "hospital_name": "Sacred Heart", "timestamp": "10:00"},
        message_class="status")

    assert carried == [], "a blocked message reached a transport"
