"""Clinician notes, typed and spoken.

The guarantee is narrow and absolute: **an unconfirmed transcript writes
nothing**. Not a receipt, not a brief field, not a record. A misheard number
must never land as an attributed clinical fact, and the way that is enforced is
that the transcribe step has no code path to the store at all.

The second guarantee is the no-interpret wall. A doctor saying "her chest pain
is worse" records that a doctor said it. It does not re-triage the case.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from anbu_care import service
from anbu_care.comms import transcribe
from anbu_care.handoff import access, notes
from anbu_care.tools import onboarding_tools, triage_tools


@pytest.fixture(autouse=True)
def link_secret(monkeypatch):
    monkeypatch.setenv("ANBU_LINK_SECRET", "test-note-secret")


@pytest.fixture
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


@pytest.fixture
def case_id() -> str:
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    onboarding_tools.record_emergency_disclosure_consent(pid)
    return triage_tools.run_triage(
        parent_id=pid, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="",
    )["case_id"]


def _heard(monkeypatch, text: str):
    """Pin what the transcriber returns, so the gate is what is under test.

    Patches `transcribe_dictation`, which is the clinician lane's own
    entry point — a prompt that only transcribes, with no symptom reading and
    no urgency judgement to discard.
    """
    monkeypatch.setattr(
        transcribe, "transcribe_dictation",
        lambda audio, mime="audio/ogg", **kw: transcribe.Transcript(
            ok=True, engine="gemini", text=text, detail="stubbed", reading=None),
    )


# =========================================================================
# (1) NOTHING IS WRITTEN UNTIL CONFIRM
# =========================================================================


def test_a_transcript_alone_creates_no_receipt_and_no_record(case_id, monkeypatch):
    """The whole feature, in one assertion."""
    _heard(monkeypatch, "Expected discharge on the twenty second.")
    grant = access.resolve(access.mint(case_id, allow_notes=True))

    before = len(service.get_chain(case_id).receipts)
    draft = notes.draft_from_voice(grant, b"x" * 5000)

    assert draft.text
    assert draft.written_anything is False
    assert len(service.get_chain(case_id).receipts) == before
    assert not [r for r in service.get_chain(case_id).receipts
                if r.kind == "clinician.note"]


def test_the_draft_endpoint_says_plainly_that_nothing_was_written(client, case_id, monkeypatch):
    _heard(monkeypatch, "Patient stable, review in the morning.")
    token = access.mint(case_id, allow_notes=True)

    before = len(service.get_chain(case_id).receipts)
    body = client.post(f"/handoff/{token}/note/draft", content=b"x" * 5000).json()

    assert body["written"] is False
    assert "nothing has been recorded" in body["warning"].lower()
    assert len(service.get_chain(case_id).receipts) == before


def test_confirming_writes_exactly_one_receipt(client, case_id, monkeypatch):
    _heard(monkeypatch, "Patient stable, review in the morning.")
    token = access.mint(case_id, allow_notes=True)

    draft = client.post(f"/handoff/{token}/note/draft", content=b"x" * 5000).json()
    confirmed = client.post(f"/handoff/{token}/note/confirm", json={
        "text": draft["text"], "ticket": draft["ticket"], "recorded_by": "Dr Anand",
    })

    assert confirmed.status_code == 200
    notes_on_chain = [r for r in service.get_chain(case_id).receipts
                      if r.kind == "clinician.note"]
    assert len(notes_on_chain) == 1
    assert service.verify_case(case_id).ok


def test_an_abandoned_draft_leaves_no_trace(case_id, monkeypatch):
    """Three drafts, no confirms. The record is untouched."""
    _heard(monkeypatch, "Something the doctor said and then thought better of.")
    grant = access.resolve(access.mint(case_id, allow_notes=True))

    before = len(service.get_chain(case_id).receipts)
    for _ in range(3):
        notes.draft_from_voice(grant, b"x" * 5000)

    assert len(service.get_chain(case_id).receipts) == before


def test_an_empty_confirm_records_nothing(case_id):
    grant = access.resolve(access.mint(case_id, allow_notes=True))
    before = len(service.get_chain(case_id).receipts)

    with pytest.raises(access.HandoffDenied):
        notes.confirm(grant, "   ")

    assert len(service.get_chain(case_id).receipts) == before


# =========================================================================
# (2) THE NO-INTERPRET WALL
# =========================================================================


def test_a_spoken_triage_phrase_never_reaches_run_triage(client, case_id, monkeypatch):
    """The sentence that would re-triage the case if anything read it."""
    _heard(monkeypatch, "She has crushing chest pain and cannot breathe at all.")

    called = []
    monkeypatch.setattr(triage_tools, "run_triage",
                        lambda *a, **k: called.append(a) or {})

    token = access.mint(case_id, allow_notes=True)
    draft = client.post(f"/handoff/{token}/note/draft", content=b"x" * 5000).json()
    client.post(f"/handoff/{token}/note/confirm",
                json={"text": draft["text"], "ticket": draft["ticket"]})

    assert called == [], "a clinician note reached run_triage"

    kinds = [r.kind for r in service.get_chain(case_id).receipts]
    assert kinds.count("triage.decision") == 1, "the note re-triaged the case"
    assert "clinician.note" in kinds


def test_a_note_does_not_change_severity_or_reopen_the_case(client, case_id, monkeypatch):
    _heard(monkeypatch, "Severity is critical, escalate immediately.")
    chain_before = service.get_chain(case_id)
    triage_before = next(r for r in chain_before.receipts if r.kind == "triage.decision")
    severity_before = triage_before.payload["severity"]
    stage_before = service.load_case(case_id).stage

    token = access.mint(case_id, allow_notes=True)
    draft = client.post(f"/handoff/{token}/note/draft", content=b"x" * 5000).json()
    client.post(f"/handoff/{token}/note/confirm",
                json={"text": draft["text"], "ticket": draft["ticket"]})

    triage_after = [r for r in service.get_chain(case_id).receipts
                    if r.kind == "triage.decision"]
    assert len(triage_after) == 1
    assert triage_after[0].payload["severity"] == severity_before
    assert service.load_case(case_id).stage == stage_before


# =========================================================================
# (3) PROVENANCE OF CAPTURE, AND THE HASH
# =========================================================================


def test_a_confirmed_voice_note_records_how_it_was_captured(client, case_id, monkeypatch):
    _heard(monkeypatch, "Reviewed at the bedside, comfortable.")
    token = access.mint(case_id, allow_notes=True)

    draft = client.post(f"/handoff/{token}/note/draft", content=b"x" * 5000).json()
    body = client.post(f"/handoff/{token}/note/confirm", json={
        "text": draft["text"], "ticket": draft["ticket"], "recorded_by": "Dr Anand",
    }).json()

    assert body["via_voice"] is True
    captured = body["captured"]
    assert "Dr Anand" in captured
    assert "via voice note" in captured
    assert "transcribed by Gemini" in captured
    assert "confirmed" in captured


def test_the_receipt_carries_the_hash_of_the_confirmed_text_not_the_words(
    client, case_id, monkeypatch
):
    """Same discipline as wellbeing text_sha256. /verify must reveal nothing."""
    secret_words = "Discussed prognosis with the family, guarded."
    _heard(monkeypatch, secret_words)
    token = access.mint(case_id, allow_notes=True)

    draft = client.post(f"/handoff/{token}/note/draft", content=b"x" * 5000).json()
    client.post(f"/handoff/{token}/note/confirm",
                json={"text": draft["text"], "ticket": draft["ticket"]})

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "clinician.note")
    assert receipt.payload["text_sha256"] == notes.text_sha256(secret_words)
    assert secret_words not in str(receipt.payload)

    public = client.get(f"/api/cases/{case_id}/verify")
    assert public.status_code == 200
    assert secret_words not in public.text


def test_the_hash_is_of_what_was_confirmed_not_what_was_heard(client, case_id, monkeypatch):
    """A clinician corrects a misheard number. The correction is what counts."""
    _heard(monkeypatch, "Expected discharge on the second.")
    token = access.mint(case_id, allow_notes=True)

    draft = client.post(f"/handoff/{token}/note/draft", content=b"x" * 5000).json()
    corrected = "Expected discharge on the twenty second."
    client.post(f"/handoff/{token}/note/confirm",
                json={"text": corrected, "ticket": draft["ticket"]})

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "clinician.note")
    assert receipt.payload["text_sha256"] == notes.text_sha256(corrected)
    assert receipt.payload["text_sha256"] != notes.text_sha256(draft["text"])
    # Corrected text no longer matches the transcript ticket, so it must not
    # claim to be what Gemini heard.
    assert receipt.payload["via_voice"] is False


def test_typed_text_cannot_claim_to_have_been_transcribed(case_id):
    """Provenance is proven, not asserted by the caller."""
    grant = access.resolve(access.mint(case_id, allow_notes=True))

    result = notes.confirm(grant, "I typed this", ticket="9999999999.forged",
                           recorded_by="Dr Anand")
    assert result["via_voice"] is False
    assert "typed by Dr Anand" in result["captured"]
    assert "Gemini" not in result["captured"]


# =========================================================================
# SCOPE — text stays default, read links stay read-only
# =========================================================================


def test_a_read_only_link_cannot_write_a_note(client, case_id):
    token = access.mint(case_id)  # no allow_notes
    before = len(service.get_chain(case_id).receipts)

    assert client.post(f"/handoff/{token}/note/draft", content=b"x" * 5000).status_code == 403
    assert client.post(f"/handoff/{token}/note/confirm",
                       json={"text": "anything"}).status_code == 403
    assert len(service.get_chain(case_id).receipts) == before


def test_a_read_token_cannot_be_edited_into_a_write_token(case_id):
    """The scope is signed, not a flag in the string."""
    read_token = access.mint(case_id)
    forged = read_token.replace(".read.", ".note.", 1)

    with pytest.raises(access.HandoffDenied):
        access.resolve(forged)


def test_typing_a_note_works_without_any_audio(case_id):
    """Voice is an option, never a requirement."""
    grant = access.resolve(access.mint(case_id, allow_notes=True))
    result = notes.confirm(grant, "Seen and reviewed.", recorded_by="Dr Anand")

    assert result["status"] == "recorded"
    assert result["via_voice"] is False
    assert [r for r in service.get_chain(case_id).receipts if r.kind == "clinician.note"]


def test_an_untranscribable_recording_is_refused_and_writes_nothing(case_id, monkeypatch):
    monkeypatch.setattr(
        transcribe, "transcribe",
        lambda audio, mime="audio/ogg", **kw: transcribe.Transcript(
            ok=False, engine="gemini", detail="could not be made out"),
    )
    grant = access.resolve(access.mint(case_id, allow_notes=True))
    before = len(service.get_chain(case_id).receipts)

    with pytest.raises(access.HandoffDenied) as denied:
        notes.draft_from_voice(grant, b"x" * 5000)

    assert "type the note instead" in str(denied.value)
    assert len(service.get_chain(case_id).receipts) == before


def test_the_clinician_path_is_not_held_to_twilio_s_webhook_ceiling():
    """A cold start failed a transcript that would have worked a second later.

    The 12-second limit exists because the wellbeing lane answers a Twilio
    webhook and Twilio hangs up at about fifteen. The clinician endpoint is a
    direct call from a browser and has no such ceiling — it was inheriting a
    constraint that does not apply to it, and losing real transcripts to it.
    """
    import inspect

    default = inspect.signature(
        transcribe.transcribe_dictation).parameters["timeout_seconds"].default
    assert default == transcribe.CLINICIAN_TIMEOUT_SECONDS
    assert default > transcribe.TRANSCRIBE_TIMEOUT_SECONDS


def test_the_dictation_prompt_asks_only_for_words(case_id, monkeypatch):
    """The no-interpret wall is easier to trust when nothing was interpreted.

    The clinician lane used to reuse the wellbeing prompt, which also extracts
    symptom terms and judges whether someone should check on this person NOW —
    a meaningless question for a doctor at a bedside, computed and discarded on
    every note. Now it is never asked for.
    """
    prompt = transcribe._DICTATION_PROMPT.lower()
    for forbidden in ("symptom", "urgent", "urgency", "diagnos", "assess",
                      "recommend", "impression"):
        assert f"do not {forbidden}" in prompt or forbidden not in prompt.split("do not")[0], (
            f"the dictation prompt still asks the model to {forbidden}"
        )
    assert "do not interpret" in prompt
    assert "do not translate" in prompt
    # Numbers are the thing that must survive verbatim.
    assert "numbers and dates" in prompt

    _heard(monkeypatch, "Expected discharge on the twenty second.")
    grant = access.resolve(access.mint(case_id, allow_notes=True))
    draft = notes.draft_from_voice(grant, b"x" * 5000)
    assert draft.text


def test_the_emergency_lane_keeps_its_short_timeout():
    """The wellbeing lane IS behind Twilio's ceiling and must stay there."""
    assert transcribe.TRANSCRIBE_TIMEOUT_SECONDS == 12


def test_the_wellbeing_lane_is_untouched_by_the_dictation_split():
    """The emergency path must keep its prompt, its reading and its ceiling.

    Splitting the prompt was worth doing only if it changed nothing for the
    seventy-one year old sending a voice note at 2am.
    """
    import inspect

    assert transcribe.TRANSCRIBE_TIMEOUT_SECONDS == 12
    assert inspect.signature(
        transcribe.transcribe).parameters["timeout_seconds"].default == 12

    # The wellbeing prompt still does all three jobs.
    wellbeing = transcribe._PROMPT.lower()
    assert "symptom" in wellbeing
    assert "needs someone to check on this person" in wellbeing

    # And the dictation prompt does exactly one.
    assert "symptom" not in transcribe._DICTATION_PROMPT.lower().split("do not")[0]


def test_a_dictation_reply_never_carries_a_reading(monkeypatch):
    """reading is None because it was never asked for, not discarded after.

    Also asserts WHICH prompt was sent — the point of the split is that the
    clinician lane never requests an interpretation at all.
    """
    monkeypatch.setenv("ANBU_TRANSCRIBE_MODE", "gemini")
    sent = {}

    def _seam(audio, mime_type, prompt, timeout_seconds):
        sent["prompt"] = prompt
        sent["timeout"] = timeout_seconds
        return "Reviewed at the bedside."

    monkeypatch.setattr(transcribe, "_call_model", _seam)

    result = transcribe.transcribe_dictation(b"x" * 5000)
    assert result.ok is True
    assert result.text == "Reviewed at the bedside."
    assert result.reading is None
    assert sent["prompt"] is transcribe._DICTATION_PROMPT
    assert sent["prompt"] is not transcribe._PROMPT
    assert sent["timeout"] == transcribe.CLINICIAN_TIMEOUT_SECONDS


def test_the_wellbeing_lane_still_sends_its_own_prompt(monkeypatch):
    """The split must not have redirected the emergency lane."""
    monkeypatch.setenv("ANBU_TRANSCRIBE_MODE", "gemini")
    sent = {}

    def _seam(audio, mime_type, prompt, timeout_seconds):
        sent["prompt"] = prompt
        sent["timeout"] = timeout_seconds
        return '{"transcript": "maarbu vali", "symptoms": ["chest pain"], "urgent": true}'

    monkeypatch.setattr(transcribe, "_call_model", _seam)

    result = transcribe.transcribe(b"x" * 5000)
    assert result.ok is True
    assert sent["prompt"] is transcribe._PROMPT
    assert sent["timeout"] == transcribe.TRANSCRIBE_TIMEOUT_SECONDS
    # And it still gets its reading in the same call.
    assert result.reading is not None


def test_a_dictation_reply_that_arrives_as_json_does_not_leak_braces(monkeypatch):
    """Belt and braces: a model that ignores the format must not put {} in a note."""
    monkeypatch.setenv("ANBU_TRANSCRIBE_MODE", "gemini")
    monkeypatch.setattr(
        transcribe, "_call_model",
        lambda audio, mime_type, prompt, timeout_seconds:
            '{"transcript": "Seen and reviewed.", "symptoms": []}')

    result = transcribe.transcribe_dictation(b"x" * 5000)
    assert result.text == "Seen and reviewed."
    assert "{" not in result.text
