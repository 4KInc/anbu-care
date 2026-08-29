"""Recovery check-ins: the son who keeps caring after the emergency ends.

Two things are being proved here, and they pull in opposite directions, which
is why they need each other.

The system must ASK. A product that handles the ambulance and then goes quiet
for the fortnight she is actually alone is not standing in for a son. So there
is a daily question, and it reaches her in Tamil.

The system must not ANSWER. Asking "how are you feeling" and recording what
comes back is care. Reading the reply and telling her what it means is
practising medicine. So every test about the reply path is a test that nothing
was added: no advice text, no verdict of the feature's own, no interpretation
anywhere between her words and the record.

The one thing that IS allowed to happen to a concerning reply is the thing that
already happened to every other check-in: the deterministic table sees it, and
if it matches, a human is told. That is not the recovery feature deciding
anything. It is the recovery feature declining to make an exception.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from anbu_care import service
from anbu_care.comms import consent, parent_replies
from anbu_care.provenance.store import PARENT_SUBJECT
from anbu_care.recovery import checkin, window
from anbu_care.tools import onboarding_tools
from anbu_care.webauth import DEMO_TOKEN
from anbu_care.wellbeing import store as wellbeing_store

AUTH_TOKEN = "test-auth-token-not-real"
MOTHER = "+919000000001"
SON = "+14155550142"

# Her morning, in her timezone. 09:00 Asia/Kolkata is 03:30 UTC.
DAY1 = datetime(2026, 8, 20, 4, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def auth_token(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", AUTH_TOKEN)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


@pytest.fixture
def parent():
    """A mother home from hospital, consented, and a son who will be told."""
    parent_id = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]

    onboarding_tools.record_parent_channel(parent_id, MOTHER, language="ta")
    onboarding_tools.record_recovery_checkin_consent(parent_id)
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Heartlin", relationship="son",
        whatsapp_e164=SON, timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=[consent.ADMISSION_ALERTS, consent.STATUS_UPDATES,
                          consent.OUTBOUND_NOTIFY, consent.INBOUND_WELLBEING],
    )
    return parent_id


@pytest.fixture
def discharged(parent):
    """A recorded discharge, and therefore an open recovery window."""
    case = service.open_case(parent)
    opened = window.open_window(parent, case.case_id,
                                discharged_on="2026-08-20", document_id="doc-1",
                                now=DAY1)
    assert opened is not None
    return parent, case.case_id, opened


def _signed(client, form: dict):
    url = "http://testserver/api/wellbeing/inbound"
    payload = url + "".join(f"{k}{v}" for k, v in sorted(form.items()))
    signature = base64.b64encode(
        hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()
    return client.post(
        "/api/wellbeing/inbound",
        content=urllib.parse.urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Twilio-Signature": signature},
    )


# ---- the window opens from a fact, not a judgement -----------------------


def test_the_window_counts_from_the_date_on_the_paper(discharged):
    _parent_id, case_id, w = discharged
    assert w.starts_on.isoformat() == "2026-08-20"
    assert w.starts_on_source == "document"
    assert w.days == 14

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "recovery.window_opened")
    assert receipt.payload["discharged_on"] == "2026-08-20"
    assert "counts from it" in receipt.payload["note"]
    assert "end by the calendar" in receipt.payload["note"]


def test_an_unreadable_discharge_date_is_never_invented(parent):
    """The window still opens. The date is not made up to make it tidy."""
    case = service.open_case(parent)
    w = window.open_window(parent, case.case_id, discharged_on=None, now=DAY1)

    assert w.discharged_on is None
    assert w.starts_on_source == "recorded_at"
    receipt = next(r for r in service.get_chain(case.case_id).receipts
                   if r.kind == "recovery.window_opened")
    assert "no readable discharge date" in receipt.payload["note"]
    assert "No discharge date has been inferred" in receipt.payload["note"]


def test_a_second_discharge_photo_does_not_start_a_second_stream(discharged):
    parent_id, case_id, _ = discharged
    again = window.open_window(parent_id, case_id, discharged_on="2026-08-20")
    assert again is None
    assert len([w for w in window.list_windows(parent_id) if w.open]) == 1


# ---- PROOF 2: the cycle records a reply through the UNMODIFIED path ------


def test_a_recovery_reply_is_recorded_through_the_existing_inbound_path(client, discharged):
    """The whole point of reusing W1: no second write path into her record.

    The prompt is sent, she answers over the same webhook that has always
    existed, and the entry lands with phase=recovery. Nothing about the
    signature check, the store, or the escalation table changed to make that
    work — only a label was added.
    """
    parent_id, _case_id, _ = discharged

    sent = checkin.send_due(parent_id, now=DAY1)
    assert sent is not None
    assert sent["day"] == 1

    response = _signed(client, {"From": f"whatsapp:{MOTHER}",
                                "Body": "I am alright, took the morning tablets"})
    assert response.status_code == 200

    entry = wellbeing_store.list_entries(parent_id)[0]
    assert entry.text == "I am alright, took the morning tablets"
    assert entry.phase == "recovery"
    assert entry.prompt_id == sent["prompt_id"]
    assert entry.source == "self-reported"

    receipt = next(r for r in service.get_chain(parent_id, subject=PARENT_SUBJECT).receipts
                   if r.kind == "wellbeing.recorded")
    assert receipt.payload["phase"] == "recovery"
    assert receipt.payload["note"] == (
        "recovery check-in — self-reported, not a clinical assessment")
    # Still the hash, never the words. Public /verify reads this chain's shape.
    assert "text_sha256" in receipt.payload
    assert "alright" not in str(receipt.payload)


def test_an_ordinary_recovery_reply_gets_no_interpretation_back(client, discharged):
    """What was recorded, and nothing else.

    This is where a helpful system would say "glad to hear it" or "keep taking
    them as prescribed". Both are things a doctor says.
    """
    parent_id, _, _ = discharged
    checkin.send_due(parent_id, now=DAY1)

    response = _signed(client, {"From": f"whatsapp:{MOTHER}", "Body": "feeling better today"})
    # She reads Tamil, so the reply is the Tamil one. Same sentence, chosen
    # ahead of time rather than translated inside a webhook that must answer.
    assert parent_replies.text(parent_replies.RECORDED, "ta", day=1) in response.text
    for advice in ("should", "keep taking", "glad", "recommend", "make sure",
                   "continue", "good news", "improving"):
        assert advice not in response.text.lower(), f"the reply advised: '{advice}'"


def test_a_message_outside_any_window_is_still_acute(client, parent):
    """No window, no label. The default is what it always was."""
    response = _signed(client, {"From": f"whatsapp:{MOTHER}", "Body": "slept well"})
    assert response.status_code == 200
    assert wellbeing_store.list_entries(parent)[0].phase == "acute"


def test_the_phase_label_is_derived_from_state_not_from_her_words(discharged):
    """`phase_for` never receives the text. It could not read it if it wanted to."""
    parent_id, _, _ = discharged

    assert checkin.phase_for(parent_id) == ("acute", None)   # no prompt yet
    sent = checkin.send_due(parent_id, now=DAY1)
    assert checkin.phase_for(parent_id) == ("recovery", sent["prompt_id"])

    # And it lapses on its own once the reply window closes.
    later = datetime.now(UTC) + timedelta(hours=25)
    assert checkin.phase_for(parent_id, now=later) == ("acute", None)


# ---- PROOF 3: a concerning reply escalates, and adds nothing -------------


def test_a_concerning_recovery_reply_fires_the_full_acute_escalation(client, discharged):
    """The wall, stated in both directions at once.

    What the recovery feature must NOT do: invent a severity, offer advice,
    name a cause, or soften the response because this is "only" a check-in.

    What it must NOT prevent: the escalation that would have happened anyway.
    A woman on day three saying she cannot breathe is a woman who cannot
    breathe, and a phase label is not a reason to handle it more gently.
    """
    parent_id, _, _ = discharged
    checkin.send_due(parent_id, now=DAY1)

    response = _signed(client, {
        "From": f"whatsapp:{MOTHER}",
        "Body": "the breathlessness is worse today and my chest is tight",
    })
    assert response.status_code == 200

    # --- the escalation fired, in full ---
    chain = service.get_chain(parent_id, subject=PARENT_SUBJECT)
    kinds = [r.kind for r in chain.receipts]
    assert "wellbeing.escalated" in kinds, "a worsening symptom did not reach a human"

    escalated = next(r for r in chain.receipts if r.kind == "wellbeing.escalated")
    assert escalated.payload["phase"] == "recovery"
    assert escalated.payload["matched_rules"], "escalated without naming what matched"
    # A case was opened and the deterministic table ran, exactly as for an acute
    # check-in. That severity is the pre-existing routing decision.
    assert escalated.payload["severity"] == "HIGH"
    assert "not a clinical assessment" in escalated.payload["note"]
    assert "not anything the recovery feature decided" in escalated.payload["note"]
    assert "108" in response.text

    # --- and the ENTRY invented nothing of its own ---
    entry = wellbeing_store.list_entries(parent_id)[0]
    assert entry.phase == "recovery"
    dumped = entry.model_dump(mode="json")
    for forbidden in ("severity", "diagnosis", "mood", "score", "risk",
                      "assessment", "advice", "recommendation"):
        assert forbidden not in dumped, f"the recovery entry gained '{forbidden}'"


def test_the_family_alert_actually_sent_is_the_recovery_one(client, discharged):
    """Not just that an alert went, but that it was the right shape.

    The acute alert leads with "she is being directed to <hospital>, 2.2 km
    away". Sending that about a woman sitting at home would describe a journey
    that is not happening, to a son who would then ring the hospital.
    """
    parent_id, _, _ = discharged
    checkin.send_due(parent_id, now=DAY1)
    _signed(client, {"From": f"whatsapp:{MOTHER}",
                     "Body": "the breathlessness is worse and my chest is tight"})

    case_id = next(r.payload["case_id"] for r in service.get_chain(
        parent_id, subject=PARENT_SUBJECT).receipts if r.kind == "wellbeing.escalated")
    comms = [r for r in service.get_chain(case_id).receipts if r.kind.startswith("comms.")]
    templates = {r.payload.get("template_name") for r in comms}

    assert "recovery_escalation_family" in templates
    assert "urgent_family_alert" not in templates

    body = next(r.payload["body"] for r in comms
                if r.payload.get("template_name") == "recovery_escalation_family")
    assert "We heard:" in body
    assert "the breathlessness is worse" in body
    assert "km away" not in body


def test_an_acute_check_in_still_gets_the_acute_alert(client, parent):
    """The recovery branch must not have moved the emergency lane.

    No window, no recovery label, and the alert that goes out is the one that
    always went out — hospital, distance, and why that hospital.
    """
    _signed(client, {"From": f"whatsapp:{MOTHER}",
                     "Body": "crushing chest pain, can't breathe"})

    case_id = next(r.payload["case_id"] for r in service.get_chain(
        parent, subject=PARENT_SUBJECT).receipts if r.kind == "wellbeing.escalated")
    templates = {r.payload.get("template_name")
                 for r in service.get_chain(case_id).receipts if r.kind.startswith("comms.")}

    assert "urgent_family_alert" in templates
    assert "recovery_escalation_family" not in templates


def test_the_escalation_says_what_was_heard_and_never_what_to_do(discharged):
    """The family alert, read line by line.

    "We heard X" is a report. "This may be Y" is a diagnosis and "you should Z"
    is advice, and a message that arrives at 2am is the version people act on.
    """
    from anbu_care.comms.policy import render_template

    body = render_template("recovery_escalation_family", {
        "parent_name": "Ashanthi", "timestamp": "9:20 AM", "day": "3",
        "said": "the breathlessness is worse today",
        "words_note": "Those are her own words, not a medical assessment.\\n",
        "understood_as": "Understood as: shortness of breath.\\n",
    }, case_id="case-x", parent_id="parent-x")

    assert "We heard:" in body
    assert "the breathlessness is worse today" in body
    assert "Nobody has assessed it and Anbu Care has not." in body

    # The one instruction it is allowed to give, and the disclaimer beside it.
    assert "Please call her now" in body
    assert "Anbu Care has not called an ambulance and cannot" in body

    lowered = body.lower()
    for advice in ("you should", "she should", "may be", "might be", "likely",
                   "probably", "suggests", "consistent with", "increase",
                   "reduce the", "take her to", "diagnos", "appears to be"):
        assert advice not in lowered, f"the escalation advised or diagnosed: '{advice}'"

    # It names no hospital, because nobody has taken her anywhere.
    assert "km away" not in body
    assert "is being directed to" not in body


def test_the_recovery_alert_never_claims_a_severity_to_the_family(discharged):
    from anbu_care.comms.policy import render_template

    body = render_template("recovery_escalation_family", {
        "parent_name": "Ashanthi", "timestamp": "9:20 AM", "day": "3",
        "said": "my chest is tight", "words_note": "", "understood_as": "",
    }, case_id="case-x", parent_id="parent-x")

    for verdict in ("HIGH", "MEDIUM", "LOW", "severity", "urgent care", "emergency room"):
        assert verdict not in body, f"the alert stated a verdict: '{verdict}'"


# ---- PROOF 4: consent withdrawal and STOP stop the next tick, live -------


def test_withdrawing_consent_stops_the_next_tick(discharged):
    parent_id, case_id, _ = discharged
    assert checkin.send_due(parent_id, now=DAY1) is not None

    onboarding_tools.record_recovery_checkin_consent(parent_id, granted=False)

    tomorrow = DAY1 + timedelta(days=1)
    assert checkin.send_due(parent_id, now=tomorrow) is None
    assert window.open_window_for(parent_id) is None

    stopped = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "recovery.stopped")
    assert stopped.payload["reason"] == "consent withdrawn"


def test_consent_is_read_live_and_never_from_a_cache(discharged):
    """Withdrawn directly on the profile, with no tool call to notice it.

    The tool stops the window itself, so this bypasses the tool entirely and
    edits the stored profile — the state a restarted process would load. The
    tick must still refuse.
    """
    parent_id, _, _ = discharged
    profile = service.load_profile(parent_id)
    profile.contact_consents.pop(consent.RECOVERY_CHECKINS)
    service.save_profile(profile)

    assert window.consent_held(parent_id) is False
    assert checkin.send_due(parent_id, now=DAY1) is None


def test_replying_stop_ends_the_check_ins_on_that_message(client, discharged):
    parent_id, case_id, _ = discharged
    checkin.send_due(parent_id, now=DAY1)

    response = _signed(client, {"From": f"whatsapp:{MOTHER}", "Body": "STOP"})
    assert response.status_code == 200
    assert parent_replies.text(parent_replies.STOPPED, "ta") in response.text

    assert window.open_window_for(parent_id) is None
    assert checkin.send_due(parent_id, now=DAY1 + timedelta(days=1)) is None

    stopped = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "recovery.stopped")
    assert stopped.payload["reason"] == "stopped by request"


def test_stop_is_not_stored_as_a_report_about_how_she_is(client, discharged):
    parent_id, _, _ = discharged
    checkin.send_due(parent_id, now=DAY1)
    _signed(client, {"From": f"whatsapp:{MOTHER}", "Body": "stop"})

    texts = [e.text for e in wellbeing_store.list_entries(parent_id)]
    assert "stop" not in texts, "an instruction was filed as a wellbeing check-in"


def test_stop_the_pain_is_a_symptom_and_not_an_opt_out(client, discharged):
    """The exact-match rule, where it matters.

    "stop" ends the service. "stop the pain" is a woman in pain, and swallowing
    it as an opt-out would be the worst possible false positive.
    """
    parent_id, _, _ = discharged
    checkin.send_due(parent_id, now=DAY1)

    _signed(client, {"From": f"whatsapp:{MOTHER}", "Body": "please make the pain stop"})
    assert window.open_window_for(parent_id) is not None
    assert wellbeing_store.list_entries(parent_id)[0].text == "please make the pain stop"


def test_the_window_ends_by_the_calendar(discharged):
    parent_id, case_id, w = discharged
    after = DAY1 + timedelta(days=w.days + 1)

    assert checkin.send_due(parent_id, now=after) is None
    stopped = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "recovery.stopped")
    assert stopped.payload["reason"] == "window ended"
    assert "nobody assessed her as recovered" in stopped.payload["detail"]


# ---- cadence: once a day, at her hour, never backfilled ------------------


def test_a_second_tick_the_same_day_sends_nothing(discharged):
    parent_id, _, _ = discharged
    assert checkin.send_due(parent_id, now=DAY1) is not None
    assert checkin.send_due(parent_id, now=DAY1 + timedelta(hours=2)) is None
    assert checkin.send_due(parent_id, now=DAY1 + timedelta(hours=8)) is None


def test_nothing_is_sent_before_her_morning(discharged):
    parent_id, _, _ = discharged
    # 06:00 Asia/Kolkata, three hours before the check-in hour.
    too_early = datetime(2026, 8, 20, 0, 30, tzinfo=UTC)
    assert checkin.send_due(parent_id, now=too_early) is None


def test_a_missed_day_is_absent_and_never_backfilled(discharged):
    """No catch-up burst.

    If nothing ticked on day two, day two has no check-in. The alternative — a
    scheduler that comes back and sends three at once — would put a stack of
    "good morning, day 2" messages on her phone at an hour nobody chose, and
    would claim on the trace that she was asked when she was not.
    """
    parent_id, _, _ = discharged
    checkin.send_due(parent_id, now=DAY1)
    # Day 2 never ticks. Day 3 does.
    day3 = DAY1 + timedelta(days=2)
    sent = checkin.send_due(parent_id, now=day3)

    assert sent["day"] == 3, "the tick made up a missed day"
    days = [r.payload["day"] for r in service.get_chain(
        window.open_window_for(parent_id).case_id).receipts
        if r.kind in {"recovery.prompt_sent", "recovery.prompt_not_delivered"}]
    assert days == [1, 3]


# ---- PROOF 5: the tick is locked -----------------------------------------


def test_the_tick_endpoint_rejects_an_unauthenticated_caller(client, discharged):
    """The abuse surface of the first-ever outbound channel to the mother.

    An open tick would be a public button for putting messages on a
    seventy-one year old's phone. The due-slot bounds it to one a day; the
    credential is what stops the attempt.
    """
    assert client.post("/api/recovery/tick").status_code == 401
    assert client.post("/api/recovery/tick",
                       headers={"Authorization": "Bearer not-the-token"}).status_code == 401
    assert client.post("/api/recovery/tick",
                       headers={"Authorization": "Basic whatever"}).status_code == 401


def test_an_unauthenticated_tick_sends_nothing_at_all(client, discharged):
    parent_id, _, _ = discharged
    client.post("/api/recovery/tick")
    assert window.recent_prompt(parent_id) is None, "a refused tick still messaged her"


def test_a_credentialed_tick_sends_what_is_due(client, discharged):
    parent_id, _, _ = discharged
    response = client.post(f"/api/recovery/tick?parent_id={parent_id}",
                           headers={"Authorization": f"Bearer {DEMO_TOKEN}"})
    assert response.status_code == 200
    assert response.json()["checked"] == 1


def test_the_recovery_view_is_credentialed(client, discharged):
    parent_id, _, _ = discharged
    assert client.get(f"/api/parents/{parent_id}/recovery").status_code == 401
    ok = client.get(f"/api/parents/{parent_id}/recovery",
                    headers={"Authorization": f"Bearer {DEMO_TOKEN}"})
    assert ok.status_code == 200
    assert ok.json()["consent_held"] is True
    assert "not a clinical assessment" in ok.json()["label"]


# ---- nothing clinical is authored TO her ---------------------------------


def test_the_check_in_question_names_no_medicine_and_gives_no_advice():
    from anbu_care.comms.policy import TEMPLATES, gate_message, render_template

    body = render_template("recovery_check_in", {"parent_name": "Ashanthi", "day": "3"})

    # It asks. It does not tell.
    assert "How are you feeling today?" in body
    assert "Did you take today's medicines?" in body
    assert "This is a check-in, not medical advice." in body
    assert "Nobody has assessed you." in body

    lowered = body.lower()
    for named in ("telmisartan", "atorvastatin", "metformin", "mg", "dose", "dosage"):
        assert named not in lowered, f"the check-in named prescription detail: '{named}'"
    for advice in ("you should", "make sure you", "remember to", "it is important",
                   "do not forget", "keep taking"):
        assert advice not in lowered, f"the check-in advised: '{advice}'"

    # And it passes its own gate, like every other template.
    spec = TEMPLATES["recovery_check_in"]
    assert gate_message(body, spec["message_class"],
                        template_name="recovery_check_in").allowed


def test_a_check_in_is_never_sent_without_her_own_consent(parent):
    """Her son's four consents authorise nothing addressed to her."""
    from anbu_care.tools import whatsapp_tools

    onboarding_tools.record_recovery_checkin_consent(parent, granted=False)
    case = service.open_case(parent)

    result = whatsapp_tools.send_parent_message(
        parent_id=parent, template_name="recovery_check_in",
        template_params={"parent_name": "Ashanthi", "day": "1"},
        message_class="logistics", purpose=consent.RECOVERY_CHECKINS,
        case_id=case.case_id,
    )
    assert result["status"] == "blocked"
    assert result["delivered"] is False
    assert consent.RECOVERY_CHECKINS in result["reason"]


def test_a_parent_with_no_number_is_not_pretended_to_be_messaged(parent):
    from anbu_care.tools import whatsapp_tools

    profile = service.load_profile(parent)
    profile.whatsapp_e164 = None
    service.save_profile(profile)

    result = whatsapp_tools.send_parent_message(
        parent_id=parent, template_name="recovery_check_in",
        template_params={"parent_name": "Ashanthi", "day": "1"},
        message_class="logistics", purpose=consent.RECOVERY_CHECKINS,
    )
    assert result["status"] == "error"
    assert "no WhatsApp number on file" in result["error"]


# ---- PROOF 6: it renders on the trace, and /verify leaks nothing ---------


def test_recovery_and_translation_receipts_render_on_the_trace(discharged):
    from anbu_care.trace.compose import compose_trace

    parent_id, case_id, _ = discharged
    checkin.send_due(parent_id, now=DAY1)

    trace = compose_trace(case_id)
    # The rule that makes the trace trustworthy still holds.
    assert len(trace.steps) == trace.receipt_count

    by_kind = {s.kind: s for s in trace.steps}
    assert by_kind["recovery.window_opened"].what == "Recovery check-ins began"
    assert "14 days from 2026-08-20" in by_kind["recovery.window_opened"].detail
    assert "the discharge date on the document" in by_kind["recovery.window_opened"].detail

    prompt = by_kind.get("recovery.prompt_sent") or by_kind["recovery.prompt_not_delivered"]
    assert "day 1 of 14" in prompt.detail


@pytest.mark.real_translation
def test_the_trace_says_a_message_was_rendered_and_from_what(monkeypatch, discharged):
    from anbu_care.comms import translate
    from anbu_care.trace.compose import compose_trace

    monkeypatch.setattr(translate, "_call_model",
                        lambda prompt, timeout_seconds: "காலை வணக்கம். இன்று எப்படி இருக்கிறீர்கள்?")
    parent_id, case_id, _ = discharged
    checkin.send_due(parent_id, now=DAY1)

    trace = compose_trace(case_id)
    comms = [s for s in trace.steps if s.kind.startswith("comms.")]
    assert comms, "the check-in was never recorded as a message"
    assert any("translated from the recorded check-in question" in s.detail for s in comms)

    prompt = next(s for s in trace.steps if s.kind.startswith("recovery.prompt"))
    assert "in Tamil, translated from the recorded check-in question" in prompt.detail


def test_verify_leaks_nothing_from_a_recovery_case(client, discharged):
    parent_id, case_id, _ = discharged
    checkin.send_due(parent_id, now=DAY1)

    response = client.get(f"/api/cases/{case_id}/verify")
    assert response.status_code == 200          # deliberately open
    body = response.json()
    assert body["verified"] is True
    assert set(body) == {"status", "case_id", "verified", "receipt_count",
                         "broken_at_seq", "reason", "public_key", "key_warning"}

    # Counts and hashes only. Nothing about her, her window, or what was said.
    blob = response.text.lower()
    for leaked in ("ashanthi", "recovery", "check-in", "medicine", "whatsapp",
                   "tamil", "9000000001", "discharge", "window"):
        assert leaked not in blob, f"/verify leaked '{leaked}'"


def test_the_prompt_receipt_carries_no_message_text(discharged):
    """The parent chain hashes her words. The case chain must not undo that."""
    parent_id, case_id, _ = discharged
    checkin.send_due(parent_id, now=DAY1)

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind.startswith("recovery.prompt"))
    assert "How are you feeling" not in str(receipt.payload)
    assert receipt.payload["day"] == 1
    assert receipt.payload["window_id"]


def test_a_read_document_gets_a_label_on_the_trace_like_everything_else():
    """It was rendering as the raw receipt kind.

    Found while live-verifying the recovery trace: every other beat reads as a
    sentence and this one read as `document.ingested`. One step per receipt was
    never in doubt; what was missing was the label.
    """
    from anbu_care.provenance.chain import Receipt
    from anbu_care.trace.compose import _WHAT, _detail

    assert _WHAT["document.ingested"] == "A document was photographed and read"
    receipt = Receipt(
        receipt_id="r-1", case_id="case-x", seq=0, kind="document.ingested",
        actor="document_capture", payload={
            "document_kind": "discharge_summary", "observation_count": 0,
            "applied": "Filled in the admission dates",
        },
        prev_hash="0" * 64, hash="a" * 64, signature="sig", public_key="pk",
    )
    assert _detail(receipt) == "discharge summary — Filled in the admission dates"


# ---- the pre-flight catches a window an earlier take left open ------------


def _preflight_check(client, name: str) -> dict:
    response = client.get("/api/preflight",
                          headers={"Authorization": f"Bearer {DEMO_TOKEN}"})
    assert response.status_code == 200, response.text
    checks = {c["name"]: c for c in response.json()["checks"]}
    assert name in checks, f"{name} is not among {sorted(checks)}"
    return checks[name]


def test_the_preflight_says_so_when_no_window_is_open(client, parent, monkeypatch):
    monkeypatch.setenv("ANBU_DEMO_CIRCLE_E164", MOTHER)

    check = _preflight_check(client, "no recovery window already open")

    assert check["ok"] is True
    assert "discharge summary opens the one the take uses" in check["detail"]


def test_the_preflight_names_a_window_an_earlier_take_left_open(
        client, discharged, monkeypatch):
    """The fault this exists for.

    due_now resolves a parent's window with max(starts_on), so a leftover that
    started later than the discharge date on the paper answers instead of the
    one beat 7 just opened. The check-in then reports ITS day number under a
    trace line describing the other window: nothing throws, nothing fails, and
    the number on screen is wrong.
    """
    _parent_id, case_id, leftover = discharged
    monkeypatch.setenv("ANBU_DEMO_CIRCLE_E164", MOTHER)

    check = _preflight_check(client, "no recovery window already open")

    assert check["ok"] is False
    assert check["fatal"] is True
    assert leftover.window_id in check["detail"]
    assert case_id in check["detail"]


def test_a_window_the_preflight_closes_is_receipted_not_deleted(discharged):
    """--fix closes it the honest way: the check-ins really do end, the reason
    is on the chain, and the row stays where it was. A pre-flight that quietly
    deleted a window would be tidying away the evidence it exists to surface."""
    parent_id, case_id, leftover = discharged

    closed = window.stop(parent_id, "cleared before a recording",
                         detail="An earlier take left it open.")

    assert [w.window_id for w in closed] == [leftover.window_id]
    assert window.open_window_for(parent_id) is None
    still_there = next(w for w in window.list_windows(parent_id)
                       if w.window_id == leftover.window_id)
    assert still_there.status == "stopped"
    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "recovery.stopped")
    assert receipt.payload["reason"] == "cleared before a recording"


# ---- the first check-in does not wait for the next poll -------------------


def test_the_first_check_in_goes_out_when_the_window_opens(parent, monkeypatch):
    """She came home today. A question that arrives up to a polling interval
    late, for no reason other than when the scheduler last ran, is a worse
    answer than one that arrives now.

    Nothing here is a new permission: it is the same send_due the scheduler
    calls, so the same consent read, the same hour gate, the same day slot.
    """
    from anbu_care.docvision import ingest

    sent: list[dict] = []
    monkeypatch.setattr(checkin, "send_due",
                        lambda pid, now=None: sent.append({"parent_id": pid}))

    case = service.open_case(parent)
    ingest._open_recovery_window(parent, case.case_id,
                                 {"discharged_on": "2026-08-20"}, "doc-1")

    assert [s["parent_id"] for s in sent] == [parent]


def test_a_check_in_that_could_not_be_sent_does_not_cost_her_the_window(
        parent, monkeypatch):
    """The window is the thing that makes anybody ask how she is for a
    fortnight. A transport that failed on the first morning must not take the
    other thirteen days with it."""
    from anbu_care.docvision import ingest

    def boom(pid, now=None):
        raise RuntimeError("no transport")

    monkeypatch.setattr(checkin, "send_due", boom)

    case = service.open_case(parent)
    window_id = ingest._open_recovery_window(
        parent, case.case_id, {"discharged_on": "2026-08-20"}, "doc-1")

    assert window_id, "the window was lost because a message could not be sent"
    assert window.open_window_for(parent) is not None


def test_a_second_send_on_the_same_day_is_owed_nothing(discharged):
    """What stops the scheduler duplicating the opening send.

    The check-in that goes out when the window opens claims that day's slot, so
    the next scheduled tick finds the morning already answered. Without this the
    first day would be the one day she is asked twice.
    """
    parent_id, _case_id, _ = discharged

    assert checkin.send_due(parent_id, now=DAY1) is not None
    assert checkin.send_due(parent_id, now=DAY1) is None, \
        "she was asked twice on the same day"


def test_two_callers_racing_for_the_same_morning_send_one_message(discharged):
    """The defect this reserve exists for, reproduced.

    A check-in sent when the discharge summary opened the window collided with
    the scheduled tick thirty seconds later. Both had read the slot as empty
    while the first was still translating and calling the provider, so both
    sent, and a seventy-one year old was asked how she was feeling twice in one
    minute.

    The claim is atomic and happens BEFORE the send, so of two callers exactly
    one proceeds. This drives the race directly: reserve, then let a second
    caller try the whole path.
    """
    parent_id, _case_id, _w = discharged

    due = window.due_now(parent_id, now=DAY1)
    assert due is not None
    assert window.reserve_slot(parent_id, due) is True, "the first caller lost its own slot"
    assert window.reserve_slot(parent_id, due) is False, "two callers both took the day"

    # And the full path agrees: the day is gone, so nothing further is owed.
    assert checkin.send_due(parent_id, now=DAY1) is None


def test_a_reserved_day_still_records_what_the_transport_said(discharged):
    """Reserving early must not lose the delivery outcome. The slot is written
    twice on purpose: once to take the day, once to say what happened."""
    parent_id, _case_id, _w = discharged
    from anbu_care.provenance.store import get_store

    sent = checkin.send_due(parent_id, now=DAY1)
    assert sent is not None

    due = window.due_now(parent_id, now=DAY1 + timedelta(days=1))
    assert due is not None, "the next day should be owed"

    row = get_store().get(f"PARENT#{parent_id}",
                          window.prompt_sk(_w.window_id, DAY1.date()))
    assert row is not None
    assert row["prompt_id"] == sent["prompt_id"], "the slot forgot which prompt it was"


def test_an_answer_is_told_which_check_in_it_landed_on(client, discharged):
    """"Thanks, that's noted" was true and told her nothing.

    She had just answered a question the system asked her that morning, and the
    reply did not say it had landed anywhere. On a recording it looks like a
    voice note went into a void. The day number comes off the prompt she is
    answering, never off her words, and the sentence after it is the same one
    the question carried.
    """
    parent_id, _case_id, _w = discharged
    sent = checkin.send_due(parent_id, now=DAY1)
    assert sent is not None

    response = _signed(client, {"From": f"whatsapp:{MOTHER}",
                                "Body": "I am alright, took the morning tablets"})

    assert parent_replies.text(parent_replies.RECORDED, "ta", day=1) in response.text
    assert "1" in response.text, "the day number did not survive the substitution"
    # And an English reader gets the English, from the same table.
    assert "recorded against today's check-in, day 1" in parent_replies.text(
        parent_replies.RECORDED, "en", day=1)


def test_an_answer_to_nothing_is_still_only_noted(client, parent):
    """Outside a recovery window there is no check-in to land on, so the reply
    must not name one. It stays the short acknowledgement it always was."""
    response = _signed(client, {"From": f"whatsapp:{MOTHER}",
                                "Body": "I am alright today"})

    assert parent_replies.text(parent_replies.NOTED, "ta") in response.text
    assert parent_replies.text(parent_replies.RECORDED, "ta", day=1) not in response.text


def test_the_day_is_read_off_the_prompt_and_not_off_her_words(discharged):
    """She could say "day 9" in her answer and it would change nothing."""
    from anbu_care.wellbeing import handler

    parent_id, _case_id, _w = discharged
    checkin.send_due(parent_id, now=DAY1)

    entry = wellbeing_store.record(parent_id, "self", "day 9, feeling fine",
                                   phase="recovery", prompt_id="rp-x")
    assert handler._answered_day(entry, parent_id) == 1


def test_the_replies_she_gets_are_chosen_and_never_translated_late(parent):
    """Every other outbound message is rendered by the model on the way out.
    These cannot be: they are the webhook's own answer, and a translation call
    inside it is a coin flip that has already come up tails once - a 504 on the
    one path that must always respond.

    So they are picked from a table. An unknown language falls back to English
    rather than to an approximation of it.
    """
    from anbu_care.comms import parent_replies

    assert parent_replies.text(parent_replies.NOTED, "ta") != \
        parent_replies.text(parent_replies.NOTED, "en")
    assert parent_replies.text(parent_replies.NOTED, "de") == \
        parent_replies.text(parent_replies.NOTED, "en")
    assert "7" in parent_replies.text(parent_replies.RECORDED, "ta", day=7)
    assert "108" in parent_replies.text(parent_replies.STOPPED, "ta")


# ---- whose words are these -----------------------------------------------


def test_her_answer_to_her_own_check_in_is_recorded_as_hers(client, discharged):
    """One handset, two people, and the record contradicting itself.

    The number index keeps one owner and the last registered wins, so on a
    phone she shares with her son it resolved to him. Her answer to her own
    check-in was filed as his report of it, while the same entry carried
    `phase=recovery` and the id of the prompt it answers.
    """
    parent_id, _case_id, _w = discharged
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Heartlin", relationship="son",
        whatsapp_e164=MOTHER, timezone_name="Asia/Kolkata", is_primary=False,
        consent_purposes=[consent.INBOUND_WELLBEING, consent.STATUS_UPDATES])
    checkin.send_due(parent_id, now=DAY1)

    _signed(client, {"From": f"whatsapp:{MOTHER}", "Body": "I am alright today"})

    entry = wellbeing_store.list_entries(parent_id)[0]
    assert entry.phase == "recovery"
    assert entry.source == wellbeing_store.SELF_REPORTED, \
        "her answer was filed as somebody else's report of it"


def test_a_message_nobody_asked_for_is_still_a_caregiver_report(client, parent):
    """Without a check-in waiting, a message from a shared handset is what it
    always was. The attribution turns on a question having been asked, not on
    whose phone it is."""
    onboarding_tools.record_family_contact(
        parent_id=parent, name="Heartlin", relationship="son",
        whatsapp_e164=MOTHER, timezone_name="Asia/Kolkata", is_primary=False,
        consent_purposes=[consent.INBOUND_WELLBEING, consent.STATUS_UPDATES])

    _signed(client, {"From": f"whatsapp:{MOTHER}", "Body": "she slept well"})

    entry = wellbeing_store.list_entries(parent)[0]
    assert entry.source == "caregiver:Heartlin"


def test_a_caregivers_own_phone_never_speaks_as_her(client, discharged):
    """The handset has to be one SHE is registered on. Otherwise a caregiver
    answering a check-in on his own phone would be recorded as her."""
    from anbu_care.comms import inbound

    parent_id, _case_id, _w = discharged
    checkin.send_due(parent_id, now=DAY1)

    profile = service.load_profile(parent_id)
    assert inbound._answers_her_own_check_in(profile, "+14155550999") is False
    assert inbound._answers_her_own_check_in(profile, MOTHER) is True
