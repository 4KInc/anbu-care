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
