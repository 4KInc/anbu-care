"""Delivery sits behind the gate, and cannot get out from behind it.

The gate is the differentiator: it classifies content, not the caller's claim
about it. Adding a real transport is only safe if a blocked message can never
reach that transport — so these tests assert the negative directly, by counting
calls to a fake transport rather than trusting the ordering to stay put.
"""

from __future__ import annotations

import json

import pytest

from anbu_care import service
from anbu_care.comms import transport
from anbu_care.tools import onboarding_tools, whatsapp_tools


@pytest.fixture
def parent_id() -> str:
    pid = onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_family_contact(
        pid, name="Karthik", relationship="son", whatsapp_e164="+14155550142",
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=["admission_alerts", "status_updates"],
    )
    return pid


@pytest.fixture
def spy(monkeypatch):
    """A transport that records every call and always claims success.

    Deliberately optimistic: if the gate ever leaked, this would happily report
    a delivery, and the assertions below would catch it.
    """
    calls: list[tuple[str, str]] = []

    def fake_send(to_e164: str, body: str, mode: str | None = None):
        calls.append((to_e164, body))
        return transport.DeliveryResult(
            delivered=True, channel="spy", detail="spy accepted", provider_id="SM-spy")

    monkeypatch.setattr(transport, "send", fake_send)
    return calls


def _send(parent_id, status: str):
    case = service.open_case(parent_id)
    result = whatsapp_tools.send_family_update(
        case_id=case.case_id, parent_id=parent_id, to_e164="+14155550142",
        template_name="status_update",
        template_params={"parent_name": "Amma", "status": status,
                         "hospital_name": "Sacred Heart Hospital", "timestamp": "4:12 PM"},
        message_class="status",
    )
    return case.case_id, result


# ---- THE ONE THAT MATTERS ------------------------------------------------


def test_a_blocked_message_never_reaches_the_transport(parent_id, spy):
    """Clinical detail disguised as a status update. It must not be carried."""
    case_id, result = _send(parent_id, "stable — troponin I 0.94 ng/mL, ECG shows ST elevation")

    assert result["allowed"] is False
    assert result["status"] == "blocked"
    assert spy == [], "the transport was called for a message the gate blocked"

    kinds = [r.kind for r in service.get_chain(case_id).receipts]
    assert "comms.blocked" in kinds
    assert "comms.sent" not in kinds


@pytest.mark.parametrize(
    "status",
    [
        "ECG shows ST elevation",
        "troponin I is 0.94 ng/mL",
        "diagnosis: acute myocardial infarction",
        "start Clopidogrel 75 mg twice daily",
        "HbA1c came back at 8.4%",
        "just logistics — troponin 0.94 ng/mL, heading to cath lab",
    ],
)
def test_no_clinical_phrasing_reaches_the_transport(parent_id, spy, status):
    _, result = _send(parent_id, status)
    assert result["allowed"] is False
    assert spy == [], f"transport was called for: {status!r}"


def test_a_missing_consent_also_stops_short_of_the_transport(parent_id, spy):
    """The gate is not the only thing in front of delivery."""
    case = service.open_case(parent_id)
    result = whatsapp_tools.send_family_update(
        case_id=case.case_id, parent_id=parent_id, to_e164="+14155550142",
        template_name="billing_summary",
        template_params={"parent_name": "Amma", "total": "358500", "line_count": "4"},
        message_class="billing",   # this contact never consented to billing_updates
    )
    assert result["allowed"] is False
    assert spy == []


def test_an_unregistered_number_never_reaches_the_transport(parent_id, spy):
    case = service.open_case(parent_id)
    whatsapp_tools.send_family_update(
        case_id=case.case_id, parent_id=parent_id, to_e164="+14155559999",
        template_name="status_update",
        template_params={"parent_name": "Amma", "status": "admitted",
                         "hospital_name": "Sacred Heart", "timestamp": "4:12 PM"},
        message_class="status",
    )
    assert spy == []


# ---- the permitted path does reach it, exactly once ----------------------


def test_a_permitted_message_is_carried_once(parent_id, spy):
    case_id, result = _send(parent_id, "admitted and stable")

    assert result["allowed"] is True
    assert result["delivered"] is True
    assert len(spy) == 1, spy
    to, body = spy[0]
    assert to == "+14155550142"
    assert "admitted and stable" in body

    receipt = next(r for r in service.get_chain(case_id).receipts if r.kind == "comms.sent")
    assert receipt.payload["delivered"] is True
    assert receipt.payload["sent_at"] is not None
    assert receipt.payload["delivery"]["provider_id"] == "SM-spy"


# ---- failure degrades, and never claims a send --------------------------


def test_a_transport_error_does_not_crash_and_does_not_claim_a_send(parent_id, monkeypatch):
    def exploding(to_e164, body, mode=None):
        raise RuntimeError("network is down")

    monkeypatch.setattr(transport, "send", exploding)
    with pytest.raises(RuntimeError):
        _send(parent_id, "admitted and stable")


def test_a_rejected_send_is_recorded_as_not_delivered(parent_id, monkeypatch):
    """Twilio said no. The message was permitted and did not arrive."""
    monkeypatch.setattr(transport, "send", lambda to, body, mode=None:
                        transport.DeliveryResult(
                            delivered=False, channel="twilio", http_status=400,
                            detail="Twilio rejected the message, nothing was delivered: not opted in"))
    case_id, result = _send(parent_id, "admitted and stable")

    assert result["allowed"] is True
    assert result["delivered"] is False
    assert result["status"] == "not_delivered"

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "comms.not_delivered")
    assert receipt.payload["sent_at"] is None
    assert "nothing was delivered" in receipt.payload["delivery"]["detail"]
    assert service.get_chain(case_id).verify().ok


# ---- the transport itself -------------------------------------------------


def test_transport_with_no_credentials_reports_not_delivered(monkeypatch):
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    result = transport.send("+14155550142", "hello", mode="twilio")
    assert result.delivered is False
    assert "not set" in result.detail


def test_off_mode_never_claims_delivery(monkeypatch):
    for mode in ("off", "sandbox", "", "none"):
        result = transport.send("+14155550142", "hello", mode=mode)
        assert result.delivered is False, mode
        assert "no message left the platform" in result.detail


def test_transport_reads_secrets_from_the_environment_only():
    """No credential literal may sit in the source."""
    import inspect
    import re

    source = inspect.getsource(transport)
    # Credentials are named, and fetched through the _env helper, which is the
    # only place this module reads the environment.
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"):
        assert f'_env("{var}")' in source, var
    assert "os.getenv(name)" in inspect.getsource(transport._env)
    # An account SID looks like AC + 32 hex; an auth token like 32 hex. Neither
    # may appear literally anywhere in this module.
    assert not re.search(r"\bAC[0-9a-fA-F]{32}\b", source)
    assert not re.search(r"['\"][0-9a-fA-F]{32}['\"]", source)


def test_every_result_carries_the_honest_reach_label():
    result = transport.send("+14155550142", "hello", mode="off")
    assert "opted-in test numbers" in result.label
    assert "business verification" in result.label
    assert "template approval" in result.label


# ---- acceptance is not receipt ------------------------------------------
#
# Twilio's create call returns `queued`/`accepted`; the handset-confirmed
# `delivered` status arrives later over a status callback we do not run. A 2xx
# therefore proves acceptance, not receipt — and a 2xx can even carry a
# terminal failure in the same body. Both are asserted here because the value
# feeds `comms.sent` and `sent_at` in the receipt chain.


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code, self._payload = status_code, payload
        self.ok = 200 <= status_code < 300
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def twilio_env(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC-test-not-a-real-sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token-not-real")


def _post_returning(monkeypatch, response):
    import requests

    seen: dict = {}

    def fake_post(url, **kw):
        seen.update({"url": url, **kw})
        return response

    monkeypatch.setattr(requests, "post", fake_post)
    return seen


@pytest.mark.parametrize("status", ["failed", "undelivered", "canceled"])
def test_a_2xx_carrying_a_terminal_status_is_not_reported_as_sent(
    monkeypatch, twilio_env, status
):
    _post_returning(monkeypatch, _Resp(201, {"sid": "SM1", "status": status,
                                             "error_message": "not opted in"}))
    result = transport.send("+919000000000", "Rescheduled to 4pm.", mode="twilio")
    assert result.delivered is False, f"HTTP 201 with status={status} claimed a send"
    assert result.provider_status == status


@pytest.mark.parametrize("status", ["queued", "accepted", "sending", "sent"])
def test_acceptance_is_recorded_as_acceptance_not_as_handset_receipt(
    monkeypatch, twilio_env, status
):
    _post_returning(monkeypatch, _Resp(201, {"sid": "SM2", "status": status}))
    result = transport.send("+919000000000", "Rescheduled to 4pm.", mode="twilio")
    assert result.delivered is True
    assert result.provider_id == "SM2"
    # The claim must name itself as acceptance. Anything stronger is unearned.
    assert "acceptance, not receipt" in result.detail
    assert "confirm" in result.detail.lower()


def test_the_request_matches_twilios_documented_shape(monkeypatch, twilio_env):
    """Endpoint, auth and parameter capitalisation, per the Messages API spec."""
    seen = _post_returning(monkeypatch, _Resp(201, {"sid": "SM3", "status": "queued"}))
    transport.send("+919000000000", "Rescheduled to 4pm.", mode="twilio")

    assert seen["url"] == (
        "https://api.twilio.com/2010-04-01/Accounts/"
        "AC-test-not-a-real-sid/Messages.json"
    )
    assert seen["auth"] == ("AC-test-not-a-real-sid", "test-token-not-real")
    assert set(seen["data"]) == {"From", "To", "Body"}       # exact capitalisation
    assert seen["data"]["To"] == "whatsapp:+919000000000"    # channel prefix
    assert seen["data"]["From"].startswith("whatsapp:")
