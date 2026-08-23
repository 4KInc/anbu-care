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


# ---- the preference lives on the person, not the deployment --------------


@pytest.mark.real_translation
def test_two_people_on_one_case_read_it_in_different_languages(monkeypatch, tamil):
    """The claim that makes this per-recipient rather than a global switch.

    Same case, same event, same gated English. The daughter in Thoothukudi
    gets Tamil; the son in California gets exactly the bytes he got before any
    of this existed.
    """
    from anbu_care import service
    from anbu_care.tools import onboarding_tools, whatsapp_tools

    parent_id = onboarding_tools.create_parent_profile(
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Heartlin", relationship="son",
        whatsapp_e164="+14155550142", timezone_name="America/Los_Angeles",
        is_primary=True, consent_purposes=["status_updates"], language="en",
    )
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Priya", relationship="daughter",
        whatsapp_e164="+919000000077", timezone_name="Asia/Kolkata",
        is_primary=False, consent_purposes=["status_updates"], language="ta",
    )
    case = service.open_case(parent_id)

    def send(to):
        return whatsapp_tools.send_family_update(
            case_id=case.case_id, parent_id=parent_id, to_e164=to,
            template_name="status_update",
            template_params={"parent_name": "Ashanthi", "status": "resting",
                             "hospital_name": "Sacred Heart", "timestamp": "4:12 PM"},
            message_class="status",
        )

    son = send("+14155550142")
    daughter = send("+919000000077")

    assert son["rendering"]["translated"] is False
    assert son["rendering"]["rendered_language"] == "en"

    assert daughter["rendering"]["translated"] is True
    assert daughter["rendering"]["rendered_language"] == "ta"
    assert daughter["rendering"]["translated_from"] == "status update"
    # Derived from the same record, and both receipts say so with the same hash.
    assert daughter["rendering"]["source_sha256"] == son["rendering"]["source_sha256"]


# ---- both languages, when that is what the reader asked for ---------------


@pytest.mark.real_translation
def test_a_reader_who_wants_both_gets_english_first_then_tamil(tamil):
    """The son abroad coordinates in English and reads Tamil to his mother.

    Sending him Tamil alone made him ask what his own bill said. Sending him
    English alone throws away the thing he would read aloud. He asked for both,
    English on top, so that is a preference he states rather than one inferred
    from living abroad.
    """
    rendering = translate.render("The bill is on Ashanthi's record. INR 38,450.",
                                 language="en+ta", source_ref="bill summary")

    assert rendering.translated is True
    assert rendering.language == "en+ta"

    english_at = rendering.text.index("The bill is on Ashanthi's record")
    tamil_at = rendering.text.index("அன்பு கேர்")
    assert english_at < tamil_at, "the derived text is above the record it derives from"

    assert translate.BILINGUAL_HEADING in rendering.text
    assert "Translated from the recorded bill summary." in rendering.text


@pytest.mark.real_translation
def test_the_english_half_is_the_record_verbatim(tamil):
    """Not a paraphrase of it, and not the model's idea of it."""
    source = "The bill is on Ashanthi's record. 7 line items, INR 38,450."
    rendering = translate.render(source, language="en+ta", source_ref="bill summary")

    assert rendering.text.startswith(source)
    assert rendering.source_text == source


@pytest.mark.real_translation
def test_wanting_both_does_not_change_what_the_model_is_asked(tamil):
    """The wall this module holds: it renders a record, it never composes one."""
    translate.render("Ashanthi is resting.", language="en+ta",
                     source_ref="status update")

    assert len(tamil) == 1, "one call, for one translation"
    assert "Ashanthi is resting." in tamil[0]


@pytest.mark.real_translation
def test_tamil_alone_stays_tamil_alone(tamil):
    """Priya lives in Thoothukudi. A message twice as long is not a kindness."""
    rendering = translate.render("Ashanthi is resting.", language="ta",
                                 source_ref="status update")

    assert rendering.language == "ta"
    assert "Ashanthi is resting." not in rendering.text
    assert translate.BILINGUAL_HEADING not in rendering.text


def test_english_alone_is_untouched_by_any_of_this(tamil):
    rendering = translate.render("Ashanthi is resting.", language="en",
                                 source_ref="status update")

    assert rendering.translated is False
    assert rendering.text == "Ashanthi is resting."
    assert tamil == [], "English cost a model call"


@pytest.mark.real_translation
def test_a_failed_translation_still_leaves_the_english_readable(monkeypatch):
    """Both halves come from one call, so losing it must not lose the record."""
    monkeypatch.setattr(translate, "_call_model",
                        lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))

    rendering = translate.render("Ashanthi is resting.", language="en+ta",
                                 source_ref="status update")

    assert rendering.translated is False
    assert rendering.text.startswith("Ashanthi is resting.")


@pytest.mark.real_translation
def test_the_preference_is_read_off_the_contact_not_their_role(tamil, monkeypatch):
    """Per-recipient, end to end: same case, same event, three readers."""
    from anbu_care import service
    from anbu_care.tools import onboarding_tools, whatsapp_tools

    parent_id = onboarding_tools.create_parent_profile(
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    for name, number, language in (("Heartlin", "+14155550142", "en+ta"),
                                   ("Priya", "+919000000077", "ta"),
                                   ("Anil", "+14155550143", "en")):
        onboarding_tools.record_family_contact(
            parent_id=parent_id, name=name, relationship="family",
            whatsapp_e164=number, timezone_name="Asia/Kolkata",
            is_primary=(name == "Heartlin"), consent_purposes=["status_updates"],
            language=language,
        )
    case = service.open_case(parent_id)

    def send(to):
        return whatsapp_tools.send_family_update(
            case_id=case.case_id, parent_id=parent_id, to_e164=to,
            template_name="status_update",
            template_params={"parent_name": "Ashanthi", "status": "resting",
                             "hospital_name": "Sacred Heart", "timestamp": "4:12 PM"},
            message_class="status",
        )

    assert send("+14155550142")["rendering"]["rendered_language"] == "en+ta"
    assert send("+919000000077")["rendering"]["rendered_language"] == "ta"
    assert send("+14155550143")["rendering"]["rendered_language"] == "en"


# ---- the language belongs to the reader, on every path -------------------


@pytest.mark.real_translation
def test_one_event_reaches_mother_and_son_each_in_their_own_language(tamil):
    """The son prefers English and reads Tamil; his mother reads only Tamil.

    Same case, same moment. Nothing about the event decides the language, and
    nothing about one reader's preference reaches the other's message.
    """
    from anbu_care import service
    from anbu_care.tools import onboarding_tools, whatsapp_tools

    parent_id = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Heartlin Machado", relationship="son",
        whatsapp_e164="+16692167706", timezone_name="America/Chicago",
        is_primary=True, language="en+ta",
        consent_purposes=["status_updates", "outbound_notify"],
    )
    onboarding_tools.record_parent_channel(
        parent_id, whatsapp_e164="+919000000055", language="ta")
    onboarding_tools.record_recovery_checkin_consent(parent_id)
    case = service.open_case(parent_id)

    to_son = whatsapp_tools.send_family_update(
        case_id=case.case_id, parent_id=parent_id, to_e164="+16692167706",
        template_name="status_update",
        template_params={"parent_name": "Ashanthi", "status": "resting",
                         "hospital_name": "Sacred Heart", "timestamp": "4:12 PM"},
        message_class="status",
    )
    from anbu_care.comms import consent as consent_purposes
    from anbu_care.recovery.checkin import TEMPLATE as CHECKIN_TEMPLATE

    to_mother = whatsapp_tools.send_parent_message(
        parent_id=parent_id, template_name=CHECKIN_TEMPLATE,
        template_params={"parent_name": "Ashanthi", "day": "2"},
        message_class="logistics",
        purpose=consent_purposes.RECOVERY_CHECKINS,
        case_id=case.case_id,
    )

    assert to_son["rendering"]["rendered_language"] == "en+ta"
    assert to_mother["rendering"]["rendered_language"] == "ta"

    # She gets one language. A message twice as long is not a kindness to
    # somebody who does not read the half on top.
    assert translate.BILINGUAL_HEADING not in to_mother["message"]["body"]


def test_no_sender_chooses_a_language_for_somebody_else():
    """The two entry points look the preference up; callers cannot pass one.

    This is what makes it per-recipient rather than per-deployment. A caller
    able to name a language is a caller able to send a frightened seventy-one
    year old a message in one she does not read, and every send in this system
    goes through one of these two.
    """
    import inspect

    from anbu_care.tools import whatsapp_tools

    for entry in (whatsapp_tools.send_family_update,
                  whatsapp_tools.send_parent_message):
        signature = inspect.signature(entry)
        assert "language" not in signature.parameters, (
            f"{entry.__name__} lets its caller choose the reader's language")

    # And each derives it from the person being written to, not from a default.
    assert 'language=getattr(contact, "language", "en")' in inspect.getsource(
        whatsapp_tools.send_family_update)
    assert 'language=getattr(profile, "language", "en")' in inspect.getsource(
        whatsapp_tools.send_parent_message)
