"""The one store that outlives a case, and the walls around what may enter it.

The hermetic tests here run everywhere and never touch the network: they prove
the guards hold and that an unconfigured deployment degrades to silence rather
than to an error. The cross-session test is the only one that needs a real
Memory Bank, and it is skipped unless ANBU_MEMORY_BANK names one, so the suite
stays runnable by anybody who clones this without a Google Cloud project.
"""

from __future__ import annotations

import os
import uuid

import pytest

from anbu_care.config import settings
from anbu_care.memory import lessons

RESOURCE = "projects/p/locations/asia-south1/reasoningEngines/123"


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.delenv("ANBU_MEMORY_BANK", raising=False)
    settings.cache_clear()
    lessons._default = None
    yield
    settings.cache_clear()
    lessons._default = None


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ANBU_MEMORY_BANK", RESOURCE)
    settings.cache_clear()
    lessons._default = None
    yield
    settings.cache_clear()
    lessons._default = None


# --- what /api/healthz is allowed to say ------------------------------------

def test_healthz_says_in_memory_when_no_bank_is_wired(unconfigured):
    assert lessons.describe() == "in-memory (not persistent)"
    assert lessons.configured() is False


def test_healthz_names_the_real_store_and_its_region(configured):
    assert lessons.describe() == "vertex ai agent engine, asia-south1 (persistent)"
    assert lessons.configured() is True


def test_a_resource_name_that_is_not_one_is_called_misconfigured(monkeypatch):
    monkeypatch.setenv("ANBU_MEMORY_BANK", "asia-south1")
    settings.cache_clear()
    lessons._default = None
    try:
        # Never "persistent" on the strength of a string being non-empty.
        assert lessons.describe() == "misconfigured"
        assert lessons.bank() is None
    finally:
        settings.cache_clear()
        lessons._default = None


# --- nothing is written when there is nowhere to write ----------------------

def test_without_a_bank_a_write_is_refused_not_attempted(unconfigured):
    assert lessons.remember_reply_mode("par_x", lessons.VOICE) is False


def test_without_a_bank_recall_is_none_which_is_not_an_error(unconfigured):
    assert lessons.recall_reply_mode("par_x") is None
    assert lessons.recall_language("par_x") is None


def test_the_background_writer_starts_no_thread_without_a_bank(unconfigured):
    called = []
    lessons.remember_in_background(lambda *a: called.append(a), "par_x", "voice")
    assert called == []


# --- the walls: there is no free-text path into this store ------------------

def test_a_reply_mode_outside_the_two_observed_values_is_refused(configured):
    for bogus in ["shouting", "VOICE ", "", "voice; and she reported chest pain"]:
        assert lessons.remember_reply_mode("par_x", bogus) is False


def test_a_language_code_that_is_a_sentence_is_refused(configured):
    for bogus in ["she speaks tamil", "chest pain", "", "en-US-and-a-symptom"]:
        assert lessons.remember_language("par_x", bogus) is False


def test_a_scope_outside_the_closed_set_of_kinds_cannot_be_built():
    with pytest.raises(ValueError):
        lessons._scope("par_x", "diagnosis")
    with pytest.raises(ValueError):
        lessons._scope("", lessons.REPLY_MODE)


def test_every_kind_has_a_writer_that_composes_its_own_sentence():
    # The guarantee is structural: a caller passes a validated value, never a
    # sentence. If a kind ever gains a free-text entry point this fails.
    assert lessons.KINDS == {lessons.LANGUAGE, lessons.REPLY_MODE}
    assert "{mode}" in lessons._REPLY_MODE_FACT
    assert "{code}" in lessons._LANGUAGE_FACT


# --- the store really is one, and it really does outlive a session ----------

@pytest.mark.memory_bank
@pytest.mark.skipif(
    not os.getenv("ANBU_MEMORY_BANK"),
    reason="set ANBU_MEMORY_BANK_LIVE to a real Agent Engine resource to run this",
)
def test_a_lesson_written_in_one_session_is_recalled_in_another():
    """Write through one client, read through a client that shares nothing.

    The second `_Bank` is built from the resource name alone: separate object,
    separate credentials, separate connection, no cache between them. So the
    only way the fact can come back is out of the store. This is the assertion
    that "persistent" rests on, and it is why the test does not reuse
    `lessons.bank()` for both halves.
    """
    resource = os.environ["ANBU_MEMORY_BANK"]
    parent = f"par_test_{uuid.uuid4().hex[:12]}"

    writing_session = lessons._Bank(resource)
    assert writing_session.write(
        parent, lessons.REPLY_MODE,
        lessons._REPLY_MODE_FACT.format(mode=lessons.VOICE),
    ) is True

    reading_session = lessons._Bank(resource)
    assert reading_session is not writing_session
    facts = reading_session.read(parent, lessons.REPLY_MODE)
    assert any("Answers by voice" in f for f in facts), facts

    # And a parent nobody has written about stays empty, so the read above was
    # a lookup rather than everything in the bank coming back for everyone.
    stranger = lessons._Bank(resource)
    assert stranger.read(f"par_test_{uuid.uuid4().hex[:12]}", lessons.REPLY_MODE) == []


# --- which language she actually writes in ----------------------------------
#
# The detector is a second, smaller model: Gemini 2.5 Flash Lite rather than the
# 3.5 Flash everything else runs on, because this asks one question with a
# two-letter answer. None of these tests spends a call. `_ask` is patched, so
# what is under test is the constraint on the answer rather than the model.

def _answers(monkeypatch, reply):
    from anbu_care.comms import detect_language

    monkeypatch.setenv("ANBU_DETECT_LANGUAGE_MODE", "gemini")
    monkeypatch.setattr(detect_language, "_ask", lambda sample: reply)
    return detect_language


SHE_WROTE = "I am feeling alright today, took my morning tablets."


def test_a_code_on_the_list_is_accepted(monkeypatch):
    d = _answers(monkeypatch, "ta")
    assert d.detect(SHE_WROTE) == "ta"


def test_the_answer_is_normalised_before_it_is_trusted(monkeypatch):
    d = _answers(monkeypatch, "  TA\n")
    assert d.detect(SHE_WROTE) == "ta"


@pytest.mark.parametrize("reply", [
    "fr",                                    # real code, not on our list
    "unknown",                               # the model doing as it was told
    "Tamil",                                 # the name rather than the code
    "The message appears to be in Tamil.",   # a sentence
    "",                                      # nothing
    "ta ta",                                 # two answers
    "t",                                     # too short to be a code
])
def test_anything_off_the_list_teaches_us_nothing(monkeypatch, reply):
    # The worst a confused model can do here is fail to teach us something,
    # which is the state this replaces.
    d = _answers(monkeypatch, reply)
    assert d.detect(SHE_WROTE) is None


def test_a_message_too_short_to_judge_is_not_sent_to_a_model(monkeypatch):
    from anbu_care.comms import detect_language

    monkeypatch.setenv("ANBU_DETECT_LANGUAGE_MODE", "gemini")
    called = []
    monkeypatch.setattr(detect_language, "_ask",
                        lambda sample: called.append(sample) or "ta")
    assert detect_language.detect("ok") is None
    assert called == [], "a two-character message bought a model call"


def test_switched_off_means_no_call_at_all(monkeypatch):
    from anbu_care.comms import detect_language

    monkeypatch.setenv("ANBU_DETECT_LANGUAGE_MODE", "off")
    called = []
    monkeypatch.setattr(detect_language, "_ask",
                        lambda sample: called.append(sample) or "ta")
    assert detect_language.detect(SHE_WROTE) is None
    assert called == []


def test_only_a_sample_is_sent_never_the_whole_message(monkeypatch):
    from anbu_care.comms import detect_language

    monkeypatch.setenv("ANBU_DETECT_LANGUAGE_MODE", "gemini")
    seen = []
    monkeypatch.setattr(detect_language, "_ask",
                        lambda sample: seen.append(sample) or "en")
    detect_language.detect("x" * 5000)
    assert len(seen[0]) == detect_language.SAMPLE_LIMIT


def test_a_model_that_raises_is_not_an_error_for_the_caller(monkeypatch):
    from anbu_care.comms import detect_language

    monkeypatch.setenv("ANBU_DETECT_LANGUAGE_MODE", "gemini")
    monkeypatch.setattr(detect_language, "_ask",
                        lambda sample: (_ for _ in ()).throw(RuntimeError("503")))
    assert detect_language.detect(SHE_WROTE) is None


def test_it_does_not_use_the_frontier_model(monkeypatch):
    # The whole reason this is a separate module. If it ever silently falls back
    # to settings().model the architecture claim on the submission stops being
    # true, and nothing else would notice.
    from anbu_care.comms import detect_language
    from anbu_care.config import settings

    assert detect_language.MODEL == "gemini-2.5-flash-lite"
    assert detect_language.MODEL != settings().model


def test_nothing_is_learned_when_the_language_is_undetectable(monkeypatch, configured):
    d = _answers(monkeypatch, "unknown")
    assert d.detect(SHE_WROTE) is None
    assert lessons.learn_language_from("par_x", SHE_WROTE) is False


# --- what the next message to her is written in ------------------------------

class _Profile:
    def __init__(self, language):
        self.language = language


def test_what_she_demonstrated_beats_what_the_form_said(unconfigured, monkeypatch):
    # Her profile says English because a son in another country filled it in.
    # She has been answering in Tamil.
    monkeypatch.setattr(lessons, "recall_language", lambda parent_id: "ta")
    assert lessons.language_for("par_x", _Profile("en")) == "ta"


def test_with_nothing_learned_the_profile_still_decides(unconfigured):
    assert lessons.language_for("par_x", _Profile("ta")) == "ta"


def test_a_parent_with_no_language_anywhere_gets_english(unconfigured):
    assert lessons.language_for("par_x", _Profile("")) == "en"
    assert lessons.language_for("par_x", None) == "en"
