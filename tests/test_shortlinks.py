"""Short links, and the doctor's words reaching the family.

Both are about the same failure: a message that is technically correct and
useless to the person holding the phone. A four-line URL at 4am reads as the
thing you are told never to tap, and "an update was left, go and read it" is a
notification about a notification.
"""

from __future__ import annotations

import time

import pytest

from anbu_care.comms import shortlinks


@pytest.fixture(autouse=True)
def public_base(monkeypatch):
    monkeypatch.setenv("ANBU_PUBLIC_BASE_URL", "https://anbu.example.run.app")


LONG = ("https://anbu.example.run.app/app?case=case-354f6a7a59"
        "&t=1999999999.OyY_sgxdH8Z-PYn2v3MbsCFJT8qHRvWr81FrTmijqdI&view=claim")


def test_an_alias_leads_where_the_long_link_did():
    short = shortlinks.shorten(LONG)
    assert short != LONG and len(short) < len(LONG)
    assert shortlinks.resolve(short.rsplit("/", 1)[-1]) == LONG


def test_it_only_ever_wraps_our_own_links():
    """A shortener that redirects anywhere is an open redirect, and one on a
    domain people have been told to trust is worth more than anything here."""
    for hostile in ("https://evil.example.com/steal?next=" + "x" * 80,
                    "http://anbu.example.run.app.evil.com/" + "y" * 80):
        assert shortlinks.shorten(hostile) == hostile


def test_an_alias_cannot_outlive_the_credential_inside_it():
    """A short link still working after its token died would be a credential
    nobody knew they still held."""
    dead = ("https://anbu.example.run.app/app?case=case-x&t=1000000000."
            + "s" * 44)
    short = shortlinks.shorten(dead)
    assert shortlinks.resolve(short.rsplit("/", 1)[-1]) is None


def test_a_handoff_link_expiry_is_read_off_the_token_too():
    soon = int(time.time()) + 600
    url = f"https://anbu.example.run.app/handoff/case-x.note.0.{soon}." + "g" * 43
    short = shortlinks.shorten(url)
    assert shortlinks.resolve(short.rsplit("/", 1)[-1]) == url


def test_codes_are_not_guessable_or_sequential():
    """Anything enumerable turns one leaked link into all of them."""
    codes = {shortlinks.shorten(LONG).rsplit("/", 1)[-1] for _ in range(50)}
    assert len(codes) == 50
    assert all(len(c) == shortlinks.CODE_LENGTH for c in codes)
    assert all(ch in shortlinks.ALPHABET for c in codes for ch in c)


def test_the_alphabet_has_nothing_anyone_has_to_ask_about_twice():
    """These get read down a phone line between Thoothukudi and Nashville."""
    for confusable in "01lIO":
        assert confusable not in shortlinks.ALPHABET


def test_a_short_link_is_left_alone():
    already = "https://anbu.example.run.app/app"
    assert shortlinks.shorten(already) == already


def test_punctuation_after_a_link_is_not_swallowed_into_it():
    text = f"Read it: {LONG}. Nothing else."
    out = shortlinks.shorten_links_in(text)
    assert out.endswith(". Nothing else.")
    code = out.split("/s/")[1].split(".")[0]
    assert shortlinks.resolve(code) == LONG


def test_a_message_with_no_links_is_unchanged():
    text = "Anbu Care: nothing here is a link."
    assert shortlinks.shorten_links_in(text) == text


def test_an_unknown_code_resolves_to_nothing():
    assert shortlinks.resolve("nosuchcodeee") is None
    assert shortlinks.resolve("") is None
