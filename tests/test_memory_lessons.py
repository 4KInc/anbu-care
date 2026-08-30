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
