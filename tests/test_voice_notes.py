"""A voice note is the realistic input, and a transcript is not her words.

Everything else in this system rests on quoting exactly what she wrote. This is
the first place that weakens: a transcript is what a model heard. So the audio
is kept as the record, the alerts say "we heard", and a recording nobody could
make out is treated as MORE alarming than one that could — slurring, gasping
and weakness break speech recognition, and they are red flags themselves.
"""

from __future__ import annotations

import pytest
from conftest import followed

from anbu_care import service
from anbu_care.comms import consent, transcribe, transport
from anbu_care.provenance.store import PARENT_SUBJECT
from anbu_care.tools import onboarding_tools
from anbu_care.wellbeing import handler
from anbu_care.wellbeing import store as wb


@pytest.fixture
def sent(monkeypatch):
    out: list[tuple[str, str]] = []
    monkeypatch.setattr(
        transport, "send",
        lambda to, body, mode=None, media_url=None: (
            out.append((to, body)),
            transport.DeliveryResult(delivered=True, channel="spy", detail="ok"),
        )[1],
    )
    return out


@pytest.fixture
def household():
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-1", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True,
    )
    onboarding_tools.record_family_contact(
        parent_id=pid, name="Karthik", relationship="son", whatsapp_e164="+16692167706",
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=[consent.ADMISSION_ALERTS],
    )
    onboarding_tools.record_family_contact(
        parent_id=pid, name="Meena", relationship="neighbour", whatsapp_e164="+919000000101",
        timezone_name="Asia/Kolkata", is_primary=False,
        consent_purposes=[consent.OUTBOUND_NOTIFY],
    )
    return pid


# ---- a note nobody could make out ---------------------------------------


def test_an_unintelligible_voice_note_still_opens_a_case(household, sent):
    """She recorded something urgent enough to send. Failing to understand it
    makes the event less legible, not less real, which is a reason to involve
    people rather than fewer."""
    entry = wb.record(household, "self-reported", "(voice note, not transcribed)",
                      source_kind="voice", audio_object="voice/x.ogg")
    out = handler.handle_unclear_voice(entry, household)

    assert out.case_id is not None
    kinds = [r.kind for r in service.get_chain(household, subject=PARENT_SUBJECT).receipts]
    assert "wellbeing.unclear" in kinds


def test_an_unintelligible_note_asserts_no_symptom_and_no_severity(household, sent):
    """A case with no triage behind it. Inventing a severity to justify opening
    it would be exactly the inference this path refuses to make."""
    entry = wb.record(household, "self-reported", "(voice note, not transcribed)",
                      source_kind="voice", audio_object="voice/x.ogg")
    out = handler.handle_unclear_voice(entry, household)

    case_kinds = [r.kind for r in service.get_chain(out.case_id).receipts]
    assert "triage.decision" not in case_kinds, "a severity was assessed from silence"

    receipt = next(r for r in service.get_chain(household, subject=PARENT_SUBJECT).receipts
                   if r.kind == "wellbeing.unclear")
    assert "No symptom was identified" in receipt.payload["note"]
    assert "severity" not in receipt.payload


def test_both_the_family_and_the_care_circle_are_told(household, sent):
    out = handler.handle_unclear_voice(
        wb.record(household, "self-reported", "(voice note, not transcribed)",
                  source_kind="voice", audio_object="voice/x.ogg"),
        household,
    )
    assert sorted(out.alerted) == ["Karthik", "Meena"]
    assert len(sent) == 2


def test_the_unclear_alerts_name_no_hospital(household, sent):
    """No triage ran, so no hospital was chosen. Claiming one would be a worse
    failure than the one being reported."""
    handler.handle_unclear_voice(
        wb.record(household, "self-reported", "(voice note, not transcribed)",
                  source_kind="voice", audio_object="voice/x.ogg"),
        household,
    )
    for _, body in sent:
        assert "Sacred Heart" not in body
        assert "km away" not in body
        assert "cashless" not in body.lower()


def test_the_family_is_told_to_listen_and_the_neighbour_is_not(household, sent):
    """The recording is her record. A neighbour is a notified party."""
    handler.handle_unclear_voice(
        wb.record(household, "self-reported", "(voice note, not transcribed)",
                  source_kind="voice", audio_object="voice/x.ogg"),
        household,
    )
    family = next(b for to, b in sent if to == "+16692167706")
    neighbour = next(b for to, b in sent if to == "+919000000101")

    assert "listen to the recording" in family.lower()
    assert "/app" in followed(family)
    assert "108" in family

    assert "listen" not in neighbour.lower()
    assert "/app" not in neighbour
    assert "No medical details are shared here" in neighbour


# ---- transcription failure is an outcome, not a crash -------------------


@pytest.mark.parametrize("audio,why", [
    (b"", "very short"),
    (b"x" * 50, "very short"),
    (b"x" * (9 * 1024 * 1024), "too large"),
])
def test_implausible_audio_is_refused_without_calling_a_model(audio, why):
    result = transcribe.transcribe(audio)
    assert result.ok is False
    assert why in result.detail


def test_transcription_off_reports_itself(monkeypatch):
    monkeypatch.setenv("ANBU_TRANSCRIBE_MODE", "off")
    result = transcribe.transcribe(b"x" * 5000)
    assert result.ok is False
    assert "switched off" in result.detail


def test_a_model_failure_keeps_the_recording(monkeypatch):
    """Never raises. A caller that gets `unclear` tells somebody to listen."""
    monkeypatch.setenv("ANBU_TRANSCRIBE_MODE", "gemini")

    def explode(*a, **k):
        raise RuntimeError("no credentials")

    monkeypatch.setattr("google.genai.Client", explode)
    result = transcribe.transcribe(b"x" * 5000)
    assert result.ok is False
    assert result.unclear is True
    assert "the recording is kept" in result.detail


# ---- what the record says about where the words came from ---------------


def test_a_voice_entry_is_marked_as_transcribed(household):
    entry = wb.record(household, "self-reported", "maarbu vali",
                      source_kind="voice", audio_object="voice/abc.ogg")
    assert entry.source_kind == "voice"
    assert entry.audio_object == "voice/abc.ogg"

    receipt = next(r for r in service.get_chain(household, subject=PARENT_SUBJECT).receipts
                   if r.kind == "wellbeing.recorded")
    assert receipt.payload["source_kind"] == "voice"
    assert receipt.payload["audio_object"] == "voice/abc.ogg"
    # The transcript is hashed, never carried: /verify is public.
    assert "maarbu vali" not in str(receipt.model_dump(mode="json"))


def test_typed_entries_are_still_marked_as_typed(household):
    entry = wb.record(household, "self-reported", "slept well")
    assert entry.source_kind == "text"
    assert entry.audio_object is None


def test_a_transcript_reaching_the_table_behaves_exactly_like_typed_text(household, sent):
    """Transcription is an input, not a decision. Nothing downstream changes
    because the words arrived as audio."""
    out = handler.handle(
        wb.record(household, "self-reported", "crushing chest pain, can't breathe",
                  source_kind="voice", audio_object="voice/y.ogg"),
        household,
    )
    assert out.escalated is True
    assert out.case_id is not None
    assert "triage.decision" in [r.kind for r in service.get_chain(out.case_id).receipts]


def test_a_transcript_is_quoted_as_heard_not_as_said(household, sent):
    """The line everything else in this system rests on. A transcript is what a
    model heard; quoting it as her words puts words in her mouth."""
    handler.handle(
        wb.record(household, "self-reported", "crushing chest pain, can't breathe",
                  source_kind="voice", audio_object="voice/y.ogg"),
        household,
    )
    family = next(b for to, b in sent if to == "+16692167706")
    assert "what Anbu Care heard in her voice note" in family
    assert "It may be imperfect" in family
    assert "Those are her own words" not in family


def test_typed_words_are_still_quoted_as_hers(household, sent):
    handler.handle(
        wb.record(household, "self-reported", "crushing chest pain, can't breathe"),
        household,
    )
    family = next(b for to, b in sent if to == "+16692167706")
    assert "Those are her own words, not a medical assessment" in family
    assert "heard in her voice note" not in family


# ---- one call, and a fallback if it comes back the old shape -------------


def test_a_json_reply_yields_transcript_and_reading_together():
    """The round trip this removes was the difference between a webhook at
    fourteen seconds and one at ten, against a Twilio ceiling of fifteen."""
    text, reading = transcribe._parse(
        '{"transcript": "மார்பு வலிக்கிறது", "symptoms": ["chest pain"],'
        ' "urgent": true, "why": "chest pain with breathlessness"}'
    )
    assert text == "மார்பு வலிக்கிறது"
    assert reading.terms == ["chest pain"]
    assert reading.urgent is True
    assert reading.used is True


def test_a_fenced_json_reply_is_still_parsed():
    """Models wrap JSON in a fence whatever the instruction says."""
    text, reading = transcribe._parse(
        '```json\n{"transcript": "chest pain", "symptoms": ["chest pain"], "urgent": true}\n```'
    )
    assert text == "chest pain"
    assert reading.urgent is True


def test_a_bare_transcript_still_works_without_a_reading():
    """If the model answers with plain text instead of JSON, the recording is
    not lost — the caller simply falls back to asking separately."""
    text, reading = transcribe._parse("மார்பு வலிக்கிறது மூச்சு விட முடியவில்லை")
    assert text == "மார்பு வலிக்கிறது மூச்சு விட முடியவில்லை"
    assert reading is None


def test_a_supplied_reading_means_no_second_model_call(monkeypatch):
    """assess() must use what it was given rather than asking again."""
    from anbu_care.wellbeing import escalation as esc

    def explode(text):
        raise AssertionError("assess made a second model call")

    monkeypatch.setattr(esc, "read", explode)
    verdict = esc.assess(
        "crushing chest pain",
        reading=esc.Reading(terms=["chest pain"], urgent=True, used=True),
    )
    assert verdict.escalate is True
    assert verdict.decided_by == "both"


def test_a_reading_from_the_transcript_call_still_cannot_suppress(monkeypatch):
    """Collapsing the calls must not have weakened the floor."""
    from anbu_care.wellbeing import escalation as esc

    verdict = esc.assess(
        "crushing chest pain, can't breathe",
        reading=esc.Reading(terms=[], urgent=False, used=True, note="model says fine"),
    )
    assert verdict.escalate is True
    assert verdict.decided_by == "rule"
