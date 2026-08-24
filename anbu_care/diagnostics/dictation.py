"""Reading an ordered test out of what a clinician actually said.

A doctor at a bedside says "she'll need a repeat troponin in the morning and an
echo before discharge". That is prose. An order is a field. Something has to
cross the gap, and that something is a model, which is uncomfortably close to
the wall this whole feature stands on: THE ORDER COMES FROM THE CLINICIAN,
NEVER FROM ANBU CARE.

So this proposes and never records. The value it returns is put in front of the
clinician, in the field, where they correct it or replace it, and what reaches
the record is what they confirmed. Same rule the note path already holds: an
unconfirmed transcript writes nothing. Gemini suggests; the clinician orders.

The failure this guards against is specific and bad. A misheard test recorded
without anyone checking sends a seventy-one year old for the wrong scan, and
the receipt says a clinician ordered it. Every design choice below is that
sentence:

- An empty answer is a fine answer. "I could not tell what test that was" costs
  the clinician a moment of typing. A confident wrong answer costs her a day
  and a bill.
- The test comes back in ENGLISH, and the transcript stays in whatever language
  was spoken. Those are different things and getting them backwards was the
  first version of this: a doctor who said the English words "blood test" got
  "பிளட் டெஸ்ட்" in the field, a transliteration of English into Tamil script
  that nobody had ordered and no search would find.
- English AT THIS POINT rather than later, because this is the moment a human
  checks it. A translation applied after the order was recorded is one nobody
  confirmed; a proposal in the field is one the clinician either accepts or
  corrects, which makes the English theirs.
- It is still not normalised into a catalogue. "Blood test" stays "blood test",
  never "complete blood count". Translating what was said and deciding what it
  should have been are different acts, and only the first one is allowed here.
- More than one test is reported as more than one, never silently reduced to
  the first. Picking one for them is exactly the kind of quiet choice this
  system does not get to make.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 25
MAX_LABEL_CHARS = 120

_PROMPT = """A clinician has dictated a note about a patient. Read it and report
ONLY which diagnostic tests, scans or investigations they are ORDERING.

Return ONLY a JSON object, no prose and no code fence:

{
  "tests": ["<each ordered test, in English>"],
  "unclear": <true if you cannot tell what was ordered>
}

Rules you must not break:
- Report each test IN ENGLISH, whatever language the note is in. A clinician in
  Thoothukudi dictating in Tamil is ordering a blood test, not a Tamil test,
  and the family reading this and the search looking for it both work in
  English. "ரத்த பரிசோதனை" is "blood test". "பிளட் டெஸ்ட்" is "blood test" —
  that is an English term written in Tamil script, not a Tamil term.
- Do NOT expand, formalise or tidy. "blood test" stays "blood test", never
  "complete blood count". "repeat troponin" stays "repeat troponin", never
  "Troponin I, serial". Translating what was said is not the same as deciding
  what it should have been.
- Only tests being ORDERED. A test already done, or a result being discussed, is
  not an order. "Her troponin was 0.9, get an echo" orders an echo and nothing
  else.
- If more than one test is ordered, list them all, in the order they were said.
- If the note orders nothing, return an empty list. That is a correct answer.
- If you cannot tell, set "unclear" to true and return an empty list. Guessing
  is worse than saying you could not tell.
- Never invent a test that was not mentioned. Never infer one from a diagnosis
  or a symptom.
"""


@dataclass(frozen=True)
class Proposal:
    """What a model thinks was ordered. Never what was ordered."""

    ok: bool
    tests: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def first(self) -> str:
        return self.tests[0] if self.tests else ""


def _off() -> bool:
    return os.getenv("ANBU_DICTATION_MODE", "gemini").strip().lower() in {
        "off", "none", "false",
    }


def _call_model(transcript: str) -> str:
    """The one place this module talks to Gemini."""
    from google import genai

    from anbu_care.config import settings

    client = genai.Client()
    response = client.models.generate_content(
        model=settings().model,
        contents=[f"{_PROMPT}\n\nThe dictated note:\n{transcript}"],
    )
    return (response.text or "").strip()


def propose_tests(transcript: str) -> Proposal:
    """Which tests this note appears to order. Never raises, never records.

    A failure and "nothing was ordered" both come back as an empty list, which
    is correct: the clinician sees an empty field and types what they meant.
    """
    text = (transcript or "").strip()
    if not text:
        return Proposal(ok=False, detail="there is nothing to read")
    if _off():
        return Proposal(ok=False, detail="reading orders from dictation is switched off")

    try:
        raw = _call_model(text)
    except Exception as exc:  # noqa: BLE001 - a failed read is an outcome
        logger.warning("could not read an order from dictation: %s", type(exc).__name__)
        return Proposal(ok=False,
                        detail=f"the order could not be read from that recording "
                               f"({type(exc).__name__})")

    parsed = _parse(raw)
    if parsed is None:
        return Proposal(ok=False, detail="the reply could not be used")
    if parsed.get("unclear"):
        return Proposal(ok=False, detail="it was not clear which test was ordered")

    tests = [str(t).strip()[:MAX_LABEL_CHARS]
             for t in (parsed.get("tests") or []) if str(t).strip()]
    if not tests:
        return Proposal(ok=True, tests=[], detail="that note does not order a test")

    return Proposal(ok=True, tests=tests,
                    detail=(f"{len(tests)} test(s) heard in the dictation, to be "
                            f"confirmed by the clinician"))


def _parse(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
