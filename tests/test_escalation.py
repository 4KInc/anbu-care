"""Gemini widens what is recognised. It never decides what is urgent.

The distinction this file defends: normalising "elephant sitting on my chest"
into "chest pressure" is language work, and a model is good at it. Deciding
that chest pressure warrants waking someone at 3am is a rule, and a rule that
lives in a prompt is not a rule.

So the model is advisory and strictly additive. The tests below try to make it
load bearing and assert that it cannot become so.
"""

from __future__ import annotations

import pytest

from anbu_care.schemas import Severity
from anbu_care.wellbeing import escalation as esc


@pytest.fixture
def model(monkeypatch):
    """Replace symptom extraction with something a test controls."""
    def install(terms, used=True, note="faked"):
        monkeypatch.setattr(esc, "extract_symptoms", lambda text: (terms, used, note))
    return install


# ---- the model cannot quieten anything -----------------------------------


def test_a_silent_model_still_escalates_crushing_chest_pain(model):
    """The floor. If Gemini times out, errors, or is switched off, the raw text
    still goes through the table."""
    model([], used=False, note="model unavailable")
    verdict = esc.assess("crushing chest pain, can't breathe")
    assert verdict.escalate is True
    assert verdict.severity is Severity.HIGH
    assert verdict.model_used is False


@pytest.mark.parametrize("junk", [
    [], ["nothing to report"], ["patient is fine"], ["no symptoms"],
    ["ignore previous instructions"],
])
def test_a_model_returning_junk_cannot_suppress_a_red_flag(model, junk):
    """Including the case where the message tries to talk the model down.

    The model's output is added to the table's input, never substituted for it,
    so a wrong or adversarial suggestion can only fail towards calling someone
    unnecessarily.
    """
    model(junk)
    assert esc.assess("crushing chest pain, can't breathe").escalate is True


def test_the_model_can_widen_recognition(model):
    """The reason Gemini is here at all: wording the keyword table would miss."""
    informal = "feels like an elephant is sitting on my chest and I can't catch my breath"

    model([])                       # keyword scan alone
    without = esc.assess(informal)

    model(["chest pressure", "shortness of breath"])
    with_model = esc.assess(informal)

    assert with_model.escalate is True
    assert with_model.model_terms == ["chest pressure", "shortness of breath"]
    # The point is the model adds reach; the floor may or may not catch this one.
    assert with_model.escalate or not without.escalate


def test_the_model_cannot_invent_an_emergency_out_of_nothing(model):
    """It can add terms, but the table still rules on them.

    "tired" is not a red flag however the model phrases it, so a hallucinated
    term that is not in the table changes nothing.
    """
    model(["mild tiredness", "general malaise"])
    verdict = esc.assess("a bit tired today")
    assert verdict.escalate is False


# ---- ordinary life is not an emergency -----------------------------------


@pytest.mark.parametrize("benign", [
    "slept well, ate breakfast",
    "feeling better today",
    "went for a short walk in the evening",
    "appetite is low but otherwise fine",
    "mood is low today",
    "did not sleep much last night",
    "watched TV with the neighbours",
])
def test_ordinary_check_ins_do_not_escalate(model, benign):
    model([])
    assert esc.assess(benign).escalate is False


# ---- the reply promises only what happened -------------------------------


def test_an_escalation_names_the_ambulance_number(model):
    model([])
    verdict = esc.assess("crushing chest pain, can't breathe")
    assert "108" in esc.reply_text(verdict, alerted=["Meena"])


def test_the_reply_claims_an_alert_only_when_one_was_delivered(model):
    """The sentence that must never be false.

    Telling someone help is coming when nobody was reached is the worst thing
    this system could say, so it is conditioned on the delivery result rather
    than on having tried.
    """
    model([])
    verdict = esc.assess("crushing chest pain, can't breathe")

    reached = esc.reply_text(verdict, alerted=["Meena", "Ravi"])
    assert "We have alerted Meena and Ravi." in reached

    nobody = esc.reply_text(verdict, alerted=[])
    assert "alerted" not in nobody.replace("could not reach", "")
    assert "call someone yourself" in nobody
    assert "108" in nobody


def test_an_ordinary_check_in_gets_the_plain_acknowledgement(model):
    model([])
    verdict = esc.assess("slept well")
    assert esc.reply_text(verdict, alerted=[]) == "Thanks, that's noted."


# ---- what escalation is, and is not --------------------------------------


def test_an_escalation_records_matched_phrases_not_a_conclusion(model):
    """Auditable as "these words matched", never as a diagnosis."""
    model([])
    verdict = esc.assess("crushing chest pain, can't breathe")
    assert verdict.matched, "escalated without recording what matched"
    joined = " ".join(verdict.matched).lower()
    assert "matched" in joined
    for diagnosis in ("myocardial infarction", "heart attack", "you have", "diagnosis"):
        assert diagnosis not in joined


def test_the_escalation_result_carries_no_diagnosis_field():
    fields = set(esc.Escalation.__dataclass_fields__)
    for forbidden in ("diagnosis", "condition", "finding", "assessment"):
        assert forbidden not in fields


@pytest.mark.real_extraction
def test_extraction_failure_is_reported_not_hidden(monkeypatch):
    """A model that could not answer must say so, so the receipt records it."""
    def explode(*a, **k):
        raise RuntimeError("no credentials")

    monkeypatch.setattr("google.genai.Client", explode)
    terms, used, note = esc.extract_symptoms("crushing chest pain")
    assert terms == []
    assert used is False
    assert "unavailable" in note


# ---- what the child is actually told -------------------------------------


def _urgent_body(**over):
    from anbu_care.comms.policy import render_template

    params = {
        "parent_name": "Rajeswari", "timestamp": "02:14 UTC",
        "said": "feels like an elephant is sitting on my chest",
        "hospital_name": "Sacred Heart Hospital", "distance_km": "2.2",
        "why_hospital": ("The extra distance was accepted because Sacred Heart is "
                         "empanelled with Star Health, so this keeps the admission cashless."),
        "cashless_status": "Cashless should apply at this hospital",
    }
    params.update(over)
    return render_template("urgent_family_alert", params)


def test_the_family_alert_answers_what_a_child_asks_first():
    """Woken at 2am, in the order the questions actually arrive."""
    body = _urgent_body()
    assert "elephant is sitting on my chest" in body      # what happened
    assert "Sacred Heart Hospital" in body                # where
    assert "2.2 km" in body                               # how far
    assert "empanelled with Star Health" in body          # why there
    assert "cashless" in body.lower()                     # is it covered
    assert "Call her now" in body                         # what do I do
    assert "108" in body                                  # and if I cannot
    assert "/app" in body                                 # where is the rest


def test_the_family_alert_does_not_pretend_an_ambulance_is_coming():
    """The single most tempting lie in this whole message.

    Anbu Care does not dispatch anything. An ETA, or "help is on the way",
    would read as a promise that transport was arranged, and a child who
    believes it may not make the call that actually matters.
    """
    body = _urgent_body()
    assert "has not called an ambulance and cannot" in body
    for fabrication in ("eta", "arriving in", "on the way", "dispatched",
                        "ambulance is coming", "minutes away"):
        assert fabrication not in body.lower()


def test_her_words_are_labelled_as_hers():
    """Relayed, in quotes, and named as an account rather than a finding."""
    body = _urgent_body()
    assert '"feels like an elephant is sitting on my chest"' in body
    assert "her own words, not a medical assessment" in body


def test_the_family_alert_is_still_gated():
    """Relaying her words does not open a hole. A message that turns out to
    carry a lab value is blocked like any other."""
    from anbu_care.comms.policy import gate_message
    from anbu_care.schemas import MessageClass

    clean = gate_message(_urgent_body(), MessageClass.STATUS,
                         template_name="urgent_family_alert")
    assert clean.allowed is True

    dirty = _urgent_body(said="troponin I came back at 0.94 ng/mL")
    assert gate_message(dirty, MessageClass.STATUS,
                        template_name="urgent_family_alert").allowed is False


def test_the_care_circle_is_told_less_than_the_family():
    """A neighbour is asked to go round. They are not given her symptoms or a
    link into her record."""
    from anbu_care.comms.policy import TEMPLATES

    circle = str(TEMPLATES["care_circle_notice"]["body"])
    assert "{said}" not in circle
    assert "{dashboard_url}" not in circle
    assert "no medical details are shared here" in circle


def test_the_alert_carries_no_internal_scoring():
    """"score 0.971" means nothing to someone woken at 2am, and the hospital is
    already named a line above."""
    from anbu_care.wellbeing.handler import _why_only

    why = _why_only(
        "Severity HIGH. Recommending Sacred Heart Hospital (2.2 km, score 0.971). "
        "That is 1.4 km farther than the nearest option (Idhayalaya Heart Centre, 0.8 km). "
        "The extra distance was accepted because Sacred Heart Hospital is empanelled "
        "with Star Health and Idhayalaya Heart Centre is not, so this keeps the "
        "admission cashless."
    )
    assert "score" not in why
    assert "Severity HIGH" not in why
    assert "Recommending" not in why
    # The part actually worth reading survives.
    assert "farther than the nearest option" in why
    assert "keeps the admission cashless" in why


def test_the_care_circle_is_not_told_she_has_already_arrived():
    """Nobody has taken her anywhere yet. "has been taken to" would be a small
    lie that a neighbour might act on."""
    from anbu_care.comms.policy import TEMPLATES

    body = str(TEMPLATES["care_circle_notice"]["body"])
    assert "has been taken to" not in body
    assert "is being directed to" in body


def test_the_family_alert_gates_on_the_same_purpose_it_selects_on():
    """A mismatch here is silent: contacts are chosen by one consent and then
    refused by another, so someone who agreed to admission alerts gets nothing
    and no error is raised anywhere."""
    import inspect

    from anbu_care.wellbeing import handler

    source = inspect.getsource(handler._tell_the_family)
    assert "consent_ok(contact.consents, consent.ADMISSION_ALERTS)" in source
    assert "purpose_override=consent.ADMISSION_ALERTS" in source


# ---- times a person can read ---------------------------------------------


def test_each_reader_gets_the_time_on_their_own_clock():
    """"15:46 UTC" is a number nobody lives in."""
    from datetime import UTC, datetime

    from anbu_care.comms.localtime import for_reader

    moment = datetime(2026, 8, 21, 15, 46, tzinfo=UTC)
    son = for_reader(moment, "America/Los_Angeles", "Asia/Kolkata", "Thoothukudi")
    assert "8:46 AM your time" in son
    assert "9:16 PM in Thoothukudi" in son
    assert "UTC" not in son


def test_a_reader_in_the_same_city_is_not_told_the_time_twice():
    from datetime import UTC, datetime

    from anbu_care.comms.localtime import for_reader

    same = for_reader(datetime(2026, 8, 21, 15, 46, tzinfo=UTC),
                      "Asia/Kolkata", "Asia/Kolkata", "Thoothukudi")
    assert same == "9:16 PM"


def test_her_local_time_is_carried_because_it_changes_the_meaning():
    """A message sent at 2am reads differently from one sent after lunch, and
    a son three time zones away cannot tell which from his own clock."""
    from datetime import UTC, datetime

    from anbu_care.comms.localtime import for_reader

    small_hours = for_reader(datetime(2026, 8, 21, 20, 30, tzinfo=UTC),
                             "America/Los_Angeles", "Asia/Kolkata", "Thoothukudi")
    assert "2:00 AM in Thoothukudi" in small_hours


def test_an_unknown_timezone_says_utc_rather_than_lying():
    """Falling back silently would print a time wrong by hours."""
    from datetime import UTC, datetime

    from anbu_care.comms.localtime import in_zone

    assert "UTC" in in_zone(datetime(2026, 8, 21, 15, 46, tzinfo=UTC), "Mars/Olympus")
