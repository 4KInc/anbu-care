"""Tamil out is a rendering of a record. It is never authorship.

The inbound half of this was already true: she speaks Tamil, a model
transcribes it, and the transcript is treated as what a model HEARD rather than
as what she said. The outbound half is the same claim pointing the other way,
and it is easier to get wrong, because a model that can write fluent Tamil can
just as fluently write Tamil that nobody recorded.

So the tests here are mostly about refusal. What the renderer will not do is
the interesting part.
"""

from __future__ import annotations

import pytest

from anbu_care.comms import translate


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, text: str, sink: list) -> None:
        self._text, self._sink = text, sink

    def generate_content(self, **kwargs):
        self._sink.append(kwargs)
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str, sink: list) -> None:
        self.models = _FakeModels(text, sink)


@pytest.fixture
def tamil(monkeypatch):
    """A stubbed Gemini that returns Tamil. Records what it was asked."""
    sink: list = []

    def fake_call(prompt, timeout_seconds):
        sink.append(prompt)
        return "அன்பு கேர்: அந்த பில் லட்சுமியின் பதிவேட்டில் உள்ளது."

    monkeypatch.setattr(translate, "_call_model", fake_call)
    return sink


# ---- WALL 1: no source record, no translation ----------------------------


@pytest.mark.real_translation
def test_a_translation_with_no_source_is_refused(tamil):
    """The whole wall, in one assertion.

    Every other guarantee in this module is about faithfulness. This one is
    about existence: there is no way to obtain Tamil for text that is not on
    the record, because the only entry point demands the record.
    """
    with pytest.raises(translate.NoSourceRecord):
        translate.render("", language="ta", source_ref="bill")
    with pytest.raises(translate.NoSourceRecord):
        translate.render("   ", language="ta", source_ref="bill")
    assert tamil == [], "the model was called for text that was never recorded"


@pytest.mark.real_translation
def test_a_translation_that_cannot_name_its_source_is_refused(tamil):
    """"Translated from the recorded ___" has to have something in the blank.

    A rendering whose provenance cannot be stated is indistinguishable from one
    the model composed, which is precisely the thing being ruled out.
    """
    with pytest.raises(translate.NoSourceRecord):
        translate.render("Anbu Care: the bill is recorded.", language="ta", source_ref="")
    assert tamil == []


# ---- WALL 2: what it produces is derived, and says so --------------------


@pytest.mark.real_translation
def test_tamil_derives_from_a_real_recorded_field_and_carries_provenance(tamil):
    source = "Anbu Care: that bill is on Lakshmi's record. 4 line items, INR 48,200."
    rendered = translate.render(source, language="ta", source_ref="bill")

    assert rendered.translated is True
    assert rendered.language == "ta"
    # The English record survives alongside, untouched. It is the source of
    # truth and nothing downstream may lose it.
    assert rendered.source_text == source
    assert rendered.source_sha256 == __import__("hashlib").sha256(
        source.encode()).hexdigest()

    # Derived, and it says which record it was derived from — in both scripts,
    # so the son reading over her shoulder can check it too.
    assert "Translated from the recorded bill." in rendered.text
    assert "மொழிபெயர்க்கப்பட்டது" in rendered.text
    assert "அன்பு கேர்" in rendered.text

    # The model was handed the recorded text, and told to translate it.
    assert source in tamil[0]
    assert "Do NOT give advice" in tamil[0]


@pytest.mark.real_translation
def test_the_receipt_payload_proves_derivation_without_carrying_the_source(tamil):
    source = "Anbu Care: that bill is on Lakshmi's record."
    payload = translate.render(source, language="ta", source_ref="bill").as_receipt_payload()

    assert payload["translated"] is True
    assert payload["rendered_language"] == "ta"
    assert payload["translated_from"] == "bill"
    assert payload["source_sha256"]
    # The hash proves it. Repeating the English in the receipt would carry the
    # content twice for no extra proof.
    assert source not in str(payload)


# ---- the son is not switched to Tamil because his mother was --------------


def test_english_readers_are_untouched():
    source = "Anbu Care: that bill is on Lakshmi's record."
    rendered = translate.render(source, language="en", source_ref="bill")
    assert rendered.text == source
    assert rendered.translated is False
    # No note, no marker, nothing appended. An English reader's message must be
    # byte-identical to what it was before any of this existed.
    assert rendered.text == source


def test_an_unsupported_language_falls_back_to_the_record_rather_than_guessing():
    rendered = translate.render("Anbu Care: recorded.", language="fr", source_ref="bill")
    assert rendered.translated is False
    assert "no rendering exists for 'fr'" in rendered.detail


# ---- WALL 3: failure falls back honestly, never to a guess ---------------


@pytest.mark.real_translation
def test_a_late_failure_sends_the_record_and_says_so(monkeypatch):
    def blow_up(prompt, timeout_seconds):
        raise TimeoutError("vertex did not answer")

    monkeypatch.setattr(translate, "_call_model", blow_up)
    source = "Anbu Care: good morning. How are you feeling today?"
    rendered = translate.render(source, language="ta", source_ref="check-in question")

    assert rendered.translated is False
    assert rendered.text.startswith(source), "the recorded English must survive intact"
    assert "could not be rendered in Tamil" in rendered.text
    assert "தமிழில் தர முடியவில்லை" in rendered.text
    assert "TimeoutError" in rendered.detail


@pytest.mark.real_translation
def test_an_empty_model_reply_is_a_failure_not_an_empty_message(monkeypatch):
    monkeypatch.setattr(translate, "_call_model", lambda prompt, timeout_seconds: "   ")
    rendered = translate.render("Anbu Care: recorded.", language="ta", source_ref="bill")
    assert rendered.translated is False
    assert "Anbu Care: recorded." in rendered.text


def test_switching_translation_off_falls_back_to_the_record(monkeypatch):
    monkeypatch.setenv("ANBU_TRANSLATE_MODE", "off")
    rendered = translate.render("Anbu Care: recorded.", language="ta", source_ref="bill")
    assert rendered.translated is False
    assert rendered.text == "Anbu Care: recorded."


# ---- WALL 4: the gate is not routed around ------------------------------


@pytest.mark.real_translation
def test_a_rendering_that_comes_back_clinical_is_refused(monkeypatch):
    """Belt and braces on top of the ordering.

    The gate has already passed on the English before anything reaches here, so
    this can only ever ADD a refusal. It exists because CLINICAL_PATTERNS are
    English regexes: a term that survives translation unchanged — a lab name, a
    figure with a unit — would otherwise ride out in Tamil script past a
    checker that cannot read it.
    """
    monkeypatch.setattr(
        translate, "_call_model",
        lambda prompt, timeout_seconds: "அன்பு கேர்: troponin 0.9 ng/ml.",
    )
    rendered = translate.render("Anbu Care: results are recorded.",
                                language="ta", source_ref="document")
    assert rendered.translated is False
    assert "classified as carrying clinical detail" in rendered.detail
    assert "troponin" not in rendered.text


@pytest.mark.real_translation
def test_the_model_never_sees_text_the_gate_has_not_seen(monkeypatch, tamil):
    """The ordering, asserted directly.

    `_send` gates the English and renders afterwards. A design that rendered
    first would hand Gemini a string no checker had looked at, and would then
    have to classify Tamil with English regexes.
    """
    from anbu_care.tools import whatsapp_tools

    seen: list[str] = []
    real_gate = whatsapp_tools.gate_message

    def watching_gate(body, declared=None, **kwargs):
        seen.append(body)
        return real_gate(body, declared, **kwargs)

    monkeypatch.setattr(whatsapp_tools, "gate_message", watching_gate)

    from anbu_care import service
    from anbu_care.comms import consent
    from anbu_care.tools import onboarding_tools

    parent_id = onboarding_tools.create_parent_profile(
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_parent_channel(parent_id, "+919000000001", language="ta")
    onboarding_tools.record_recovery_checkin_consent(parent_id)
    case = service.open_case(parent_id)

    whatsapp_tools.send_parent_message(
        parent_id=parent_id, template_name="recovery_check_in",
        template_params={"parent_name": "Ashanthi", "day": "3"},
        message_class="logistics", purpose=consent.RECOVERY_CHECKINS,
        case_id=case.case_id,
    )

    assert seen, "nothing was gated"
    gated = seen[0]
    # Gemini was handed exactly the string the gate ruled on.
    assert gated in tamil[0]
    # And the gate saw English, which is what its patterns are written for.
    assert "good morning" in gated


# ---- the copy rule the rest of the product lives under -------------------


def test_nothing_this_module_adds_to_a_message_uses_an_em_dash():
    """The same rule as the templates and the dashboard.

    An em dash reads as authored voice, and this is the one place in the
    product where a machine appends a sentence to somebody else's message. It
    should sound like a label on a record, not like prose.
    """
    for source_ref in ("bill", "check-in question", "doctor's note"):
        assert "\u2014" not in translate._provenance_note(source_ref)
        assert "\u2014" not in translate._fallback_note(source_ref)


def test_every_template_body_is_free_of_em_dashes():
    """Asserted here because the dashboard test's docstring claims it."""
    from anbu_care.comms.policy import TEMPLATES

    for name, spec in TEMPLATES.items():
        assert "\u2014" not in str(spec["body"]), f"{name} uses an em dash"
