"""Intake signals.

The honesty constraint here is narrow and absolute: Anbu Care reacts to signals
that arrive, and never claims to have sensed anything. These tests pin the
wording as well as the behaviour, because on this product the wording *is* the
claim.
"""

from __future__ import annotations

import re

import pytest

from anbu_care import service
from anbu_care.tools import intake_tools, onboarding_tools, triage_tools


@pytest.fixture
def parent_id() -> str:
    return onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]


# ---- the signal opens a case --------------------------------------------


def test_signal_opens_a_case_and_writes_a_receipt(parent_id):
    result = intake_tools.receive_intake_signal(
        parent_id, channel="er_desk_webhook",
        raw_text="71F brought in with chest pain, BP 160/95",
        reported_by="ER desk",
    )
    assert result["status"] == "ok"

    chain = service.get_chain(result["case_id"])
    kinds = [r.kind for r in chain.receipts]
    assert kinds[0] == "case.opened"
    assert "intake.signal_received" in kinds
    assert chain.verify().ok


def test_signal_receipt_records_the_channel_and_the_raw_text(parent_id):
    raw = "amma fell, chest heavy, sweating, BP 160"
    result = intake_tools.receive_intake_signal(
        parent_id, channel="neighbour_button", raw_text=raw, reported_by="neighbour",
    )
    signal = next(
        r for r in service.get_chain(result["case_id"]).receipts
        if r.kind == "intake.signal_received"
    )
    assert signal.payload["channel"] == "neighbour_button"
    assert signal.payload["raw_text"] == raw          # unedited
    assert signal.payload["simulated"] is True


def test_unknown_channel_is_refused(parent_id):
    result = intake_tools.receive_intake_signal(
        parent_id, channel="satellite_telemetry", raw_text="x", reported_by="y",
    )
    assert result["status"] == "error"
    assert "known_channels" in result


def test_signal_for_an_unknown_parent_is_refused():
    result = intake_tools.receive_intake_signal(
        "parent-nope", channel="family_form", raw_text="x", reported_by="y",
    )
    assert result["status"] == "error"


# ---- the honesty constraint: received, never detected --------------------


FORBIDDEN = [
    r"\bdetect(ed|s|ing)?\b",
    r"\bnotice[ds]?\b",
    r"\bsens(ed|es|ing)\b",
    r"\bmonitor(ed|s|ing)\b",
    r"\bobserv(ed|es|ing)\b",
    r"\bwatch(ed|es|ing)\b",
]


def _assert_no_sensing_language(text: str, where: str) -> None:
    lowered = text.lower()
    for pattern in FORBIDDEN:
        # "does not monitor" / "not detected by" are disclaimers, not claims.
        for match in re.finditer(pattern, lowered):
            window = lowered[max(0, match.start() - 30):match.start()]
            if any(neg in window for neg in ("not ", "never ", "no ", "cannot ", "does not ")):
                continue
            raise AssertionError(f"{where} claims sensing: ...{lowered[max(0,match.start()-60):match.end()+30]}...")


def test_signal_payload_uses_received_language_not_sensing(parent_id):
    result = intake_tools.receive_intake_signal(
        parent_id, channel="er_desk_webhook", raw_text="chest pain", reported_by="ER desk",
    )
    assert "received" in result["label"].lower()
    _assert_no_sensing_language(result["label"], "signal label")
    _assert_no_sensing_language(result["next_step"], "next_step")


def test_channel_listing_states_the_limitation(parent_id):
    listing = intake_tools.list_intake_channels()
    assert "does not watch" in listing["note"].lower()
    _assert_no_sensing_language(listing["note"], "channel note")
    for description in listing["channels"].values():
        _assert_no_sensing_language(description, "channel description")


def test_every_intake_receipt_carries_the_simulated_label(parent_id):
    result = intake_tools.receive_intake_signal(
        parent_id, channel="whatsapp_inbound", raw_text="amma unwell", reported_by="son",
    )
    signal = next(
        r for r in service.get_chain(result["case_id"]).receipts
        if r.kind == "intake.signal_received"
    )
    assert "SIMULATED INTAKE SIGNAL" in signal.payload["label"]
    assert "not detected by" in signal.payload["label"]


# ---- signal then triage --------------------------------------------------


def test_signal_does_not_itself_decide_anything(parent_id):
    """Receiving is not assessing. Triage is a separate, later decision."""
    result = intake_tools.receive_intake_signal(
        parent_id, channel="er_desk_webhook", raw_text="chest pain", reported_by="ER desk",
    )
    kinds = [r.kind for r in service.get_chain(result["case_id"]).receipts]
    assert "triage.decision" not in kinds
    assert service.load_case(result["case_id"]).stage == "signal_received"


def test_the_full_chain_is_signal_then_triage(parent_id):
    signal = intake_tools.receive_intake_signal(
        parent_id, channel="er_desk_webhook",
        raw_text="71F chest pain radiating to left arm, sweating",
        reported_by="ER desk",
    )
    triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain", "sweating"],
        free_text="radiating to left arm", reported_by="ER desk",
        lat=0.0, lon=0.0, case_id=signal["case_id"],
    )
    chain = service.get_chain(signal["case_id"])
    kinds = [r.kind for r in chain.receipts]
    assert kinds == ["case.opened", "intake.signal_received", "triage.decision"]
    assert chain.verify().ok


def test_triage_after_a_signal_still_holds_a_red_flag(parent_id):
    """The guarantee layer is unchanged by the new channel."""
    signal = intake_tools.receive_intake_signal(
        parent_id, channel="neighbour_button",
        raw_text="she says it's probably just gas", reported_by="neighbour",
    )
    result = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"],
        free_text="she says it's probably just gas", reported_by="neighbour",
        lat=0.0, lon=0.0, case_id=signal["case_id"],
    )
    assert result["severity"] == "HIGH"
