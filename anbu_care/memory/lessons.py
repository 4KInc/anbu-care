"""What Anbu Care is allowed to remember about a parent between cases.

Every other store here is scoped to one case and dies with it. Firestore keeps
the record, the chain keeps the proof, and both are answers to "what happened
on this admission". Neither can answer "what did we learn last time", because
neither is asked until a case already exists.

A lesson is the small class of fact that is true of the person rather than of
the admission: the language she answers in, which contact actually picks up,
which insurer desk asks for the policy number up front. Those are worth
carrying, and carrying them is the difference between a system that coordinates
and a system that starts from zero every time somebody's mother falls ill.

THE BACKING STORE is a Vertex AI Agent Engine Memory Bank, reached over its
REST surface with the credentials the service already holds. It is deliberately
not the ADK memory service: ADK's `memory_service_uri` needs the
google-cloud-aiplatform extra and only serves ADK's own session endpoints,
which nothing in this codebase calls. Wiring that would have made
`/api/healthz` say "configured" while no product path stored anything. This
lane is the honest one, it is the one the recovery check-in actually reads, and
it adds no dependency to the image.

RECALL IS A LOOKUP, NOT A SEARCH. Memory Bank matches scopes exactly, so a
lesson is written under `{parent_id, kind}` and read back under the same two
keys. Nothing is retrieved by similarity, so nothing can be retrieved by
accident: an unrelated lesson cannot surface because it read as close enough.

NOTHING CLINICAL IS WRITTEN HERE, and that is enforced by shape rather than by
review. There is no free-text entry point. Each kind has its own function, each
builds its own sentence from a value this module has already validated, and a
language code that is not a language code is refused before it reaches the
network. A caller cannot pass a symptom through, because a caller cannot pass a
sentence at all.

MEMORY IS NEVER LOAD-BEARING. A write that fails returns False and a recall
that fails returns None, both after a log line. The check-in still goes out in
the language on her profile. No admission may fail because a convenience could
not be stored.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx

from ..config import settings

log = logging.getLogger(__name__)

# The closed set of things worth carrying between admissions. Adding a kind
# means adding a function that composes its sentence, which is the point: the
# set cannot be widened by a caller, only by an edit here.
LANGUAGE = "language"
REPLY_MODE = "reply_mode"

KINDS = frozenset({LANGUAGE, REPLY_MODE})

# How she answers. Two values, both observed rather than inferred: the inbound
# path already knows whether what arrived was audio or typed text, so this
# costs no model call and cannot be wrong about something it did not measure.
VOICE = "voice"
TEXT = "text"
_REPLY_MODES = frozenset({VOICE, TEXT})

# A language code, not a sentence. Two or three letters with an optional
# region, which is the shape of every code the renderer accepts and the shape
# of nothing anybody would call clinical detail.
_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")

_RESOURCE = re.compile(r"^projects/[^/]+/locations/([^/]+)/reasoningEngines/[^/]+$")

_TIMEOUT = httpx.Timeout(10.0)


class _Bank:
    """A thin, synchronous client for one Memory Bank resource.

    Holds no memory of its own on purpose. Two instances share nothing but the
    resource name, which is what makes the cross-session test meaningful: the
    second instance can only answer from the store.
    """

    def __init__(self, resource: str, credentials: Any = None) -> None:
        m = _RESOURCE.match(resource or "")
        if not m:
            raise ValueError(
                "ANBU_MEMORY_BANK must be a full Agent Engine resource name, "
                f"projects/<p>/locations/<l>/reasoningEngines/<id>, got: {resource!r}"
            )
        self.resource = resource
        self.location = m.group(1)
        self.base = f"https://{self.location}-aiplatform.googleapis.com/v1/{resource}"
        self._creds = credentials
        self._lock = threading.Lock()

    def _token(self) -> str:
        with self._lock:
            if self._creds is None:
                self._creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            if not self._creds.valid:
                self._creds.refresh(google.auth.transport.requests.Request())
            return self._creds.token

    def _post(self, path: str, body: dict) -> dict:
        r = httpx.post(
            f"{self.base}{path}",
            json=body,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    def write(self, parent_id: str, kind: str, fact: str) -> bool:
        try:
            self._post("/memories", {"fact": fact, "scope": _scope(parent_id, kind)})
            return True
        except Exception as e:  # noqa: BLE001 - memory is never load-bearing
            log.warning("memory bank write failed (%s/%s): %s", parent_id, kind, e)
            return False

    def read(self, parent_id: str, kind: str) -> list[str]:
        try:
            got = self._post("/memories:retrieve", {"scope": _scope(parent_id, kind)})
        except Exception as e:  # noqa: BLE001 - memory is never load-bearing
            log.warning("memory bank read failed (%s/%s): %s", parent_id, kind, e)
            return []
        out = []
        for item in got.get("retrievedMemories") or []:
            fact = (item.get("memory") or {}).get("fact")
            if fact:
                out.append(fact)
        return out


def _scope(parent_id: str, kind: str) -> dict[str, str]:
    if kind not in KINDS:
        raise ValueError(f"not a lesson kind: {kind!r}")
    if not parent_id:
        raise ValueError("a lesson needs a parent to belong to")
    return {"parent_id": parent_id, "kind": kind}


_default: _Bank | None = None
_default_lock = threading.Lock()


def configured() -> bool:
    """Whether a real Memory Bank is wired. No network call."""
    return bool(settings().memory_bank)


def describe() -> str:
    """The one line /api/healthz reports.

    Says what the store is, not that it is reachable. Reachability is a claim
    only a round trip can make, and a liveness probe has no business making a
    round trip to somebody else's API on every call.
    """
    resource = settings().memory_bank
    if not resource:
        return "in-memory (not persistent)"
    m = _RESOURCE.match(resource)
    return f"vertex ai agent engine, {m.group(1)} (persistent)" if m else "misconfigured"


def bank() -> _Bank | None:
    global _default
    if not configured():
        return None
    with _default_lock:
        if _default is None or _default.resource != settings().memory_bank:
            try:
                _default = _Bank(settings().memory_bank or "")
            except ValueError as e:
                log.warning("memory bank not usable: %s", e)
                return None
    return _default


# ---------------------------------------------------------------------------
# The lessons themselves. One function per kind, each composing its own
# sentence. There is no path here that takes a sentence from a caller.
# ---------------------------------------------------------------------------

_LANGUAGE_FACT = "Answers messages in {code}. Observed from her own replies."


def remember_language(parent_id: str, code: str) -> bool:
    """Record the language she actually answered in.

    Her profile carries a language too, but that one was chosen for her at
    onboarding, usually by the son filling in the form from another country.
    This one she demonstrated. When the two disagree, the demonstration is the
    better evidence, and it is the only one that survives to the next case.
    """
    code = (code or "").strip().lower()
    if not _LANGUAGE_CODE.match(code):
        log.warning("refusing to remember %r as a language code", code)
        return False
    b = bank()
    if b is None:
        return False
    return b.write(parent_id, LANGUAGE, _LANGUAGE_FACT.format(code=code))


def recall_language(parent_id: str) -> str | None:
    """The language she answered in last time, or None if we have not learned one.

    None is the ordinary answer for a parent we have not heard from, and it
    means the caller falls back to the profile. It is not an error.
    """
    b = bank()
    if b is None:
        return None
    for fact in b.read(parent_id, LANGUAGE):
        m = re.match(r"^Answers messages in ([a-z]{2,3}(?:-[A-Za-z]{2,4})?)\.", fact)
        if m:
            return m.group(1)
    return None


_REPLY_MODE_FACT = "Answers by {mode}. Observed from how her messages arrive."


def remember_reply_mode(parent_id: str, mode: str) -> bool:
    """Record whether she answers by voice note or by typing.

    Not a preference she was asked for and not a guess about her. It is the
    shape of the messages that actually arrived, which the inbound path has
    already established by the time this is called: a voice note has audio and
    a typed reply does not. Nothing is transcribed, read, or interpreted to
    learn it.

    It is worth carrying because it is the thing about an eighty-year-old that
    a form never captures and that resets every time a case closes. A woman who
    has never typed a message in her life should not be asked to reply with a
    number, and the system should know that on day one of the next admission
    rather than learning it again from her silence.
    """
    mode = (mode or "").strip().lower()
    if mode not in _REPLY_MODES:
        log.warning("refusing to remember %r as a reply mode", mode)
        return False
    b = bank()
    if b is None:
        return False
    return b.write(parent_id, REPLY_MODE, _REPLY_MODE_FACT.format(mode=mode))


def recall_reply_mode(parent_id: str) -> str | None:
    """How she answered last time, or None if we have never heard from her."""
    b = bank()
    if b is None:
        return None
    for fact in b.read(parent_id, REPLY_MODE):
        m = re.match(r"^Answers by (voice|text)\.", fact)
        if m:
            return m.group(1)
    return None


def remember_in_background(fn, *args) -> None:
    """Write a lesson without making anybody wait for it.

    Every observation this module makes happens on the Twilio webhook, and
    Twilio abandons a webhook at roughly fifteen seconds. That path already
    spends its budget on storage, transcription and a reply she is waiting for.
    Spending more of it on a store that exists to be convenient next month is
    the wrong trade, and a slow quarter from somebody else's API must never be
    the reason her message went unanswered.

    So the write is handed to a daemon thread and forgotten. It cannot raise
    into the request, it cannot delay the reply, and if the process exits first
    the lesson is simply not learned - which is exactly what happens today,
    every time, and is survivable.
    """
    if not configured():
        return
    t = threading.Thread(target=_swallow, args=(fn, *args), daemon=True)
    t.start()


def _swallow(fn, *args) -> None:
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 - a lesson is never load-bearing
        log.warning("background lesson write failed: %s", e)
