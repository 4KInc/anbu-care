"""Rendering a recorded message in the language of the person reading it.

She is seventy-one, she lives in Thoothukudi, and she speaks Tamil. The system
already meets her in Tamil on the way IN — she sends a voice note and Gemini
transcribes whatever she actually said. Everything on the way OUT was English.
So the son who cannot be there is spoken to in his language, and the mother the
whole product exists for is spoken to in his language too.

This module closes that. The wall it has to hold is narrow and absolute:

    TRANSLATION IS A RENDERING OF A RECORD. IT IS NEVER AUTHORSHIP.

Gemini is handed text that is already ON THE RECORD — the bill summary that was
read off a photograph, the check-in question from a fixed template, the words
the doctor dictated — and asked to say that same thing in Tamil. It is never
asked what to say. The English stays the source of truth; the Tamil is derived
from it and carries a note saying so.

Four properties keep that true rather than aspirational:

1. **No source, no translation.** `render` refuses a string that did not come
   from a recorded field. There is no code path that produces Tamil for text
   nobody wrote down first — the same discipline the voice transcript follows,
   where the audio is the record and the words are derived from it.

2. **The gate rules on the English, before this runs.** `CLINICAL_PATTERNS` is
   a list of English regexes and would not recognise a lab value in Tamil
   script. So the ordering is load-bearing: `gate_message` passes on the exact
   source string, and only then is that string rendered. Translation cannot
   carry anything past a gate that has already seen it. The Tamil is classified
   too, which can only ADD a refusal — a transliterated "troponin" still hits.

3. **The prompt translates and nothing else.** Same posture as the clinician
   dictation prompt: write down what is there, do not interpret, do not answer,
   do not advise. A model invited to be helpful in a medical context is a model
   that will eventually offer a dose.

4. **Failure falls back to the record, never to a guess.** A timeout, an empty
   reply, a switched-off engine — every one of them returns the English source
   with a plain note saying it could not be rendered. A half-translated or
   invented Tamil sentence reaching a frightened seventy-one year old is worse
   than an English one she has to ask her son about.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The languages this system will render into. Anything else is treated as
# English rather than attempted: a language nobody checked the output of is a
# guess with extra steps.
SUPPORTED = {"en", "ta"}

LANGUAGE_NAMES = {"en": "English", "ta": "Tamil"}

# Two ceilings, for the same reason transcription has two, and the split is
# not a tuning detail — it encodes which failure is worse in each lane.
#
# A real EN->Tamil call on this prompt measures 13 to 16 seconds.
#
# URGENT is deliberately BELOW that. The escalation alerts render inside the
# Twilio webhook, which hangs up at roughly fifteen seconds, and they are the
# messages telling a son to ring his mother now. A Tamil alert that arrives
# late — or not at all, because the webhook timed out and Twilio retried and
# double-sent — is strictly worse than an English one that arrives instantly.
# So the emergency lane gives translation a few seconds of grace and then goes
# without it. It will usually go without it, and that is the correct outcome.
URGENT_TIMEOUT_SECONDS = 8

# UNHURRIED is for everything with nothing waiting on it: the recovery tick, and
# the bill and document replies that already run after the response. Here the
# opposite is true — nobody is held up, and an English message to a woman who
# reads Tamil is the failure worth spending twenty extra seconds to avoid.
UNHURRIED_TIMEOUT_SECONDS = 45

# The default is the safe one. A new caller that has not thought about which
# lane it is in gets the behaviour that cannot delay an emergency.
TRANSLATE_TIMEOUT_SECONDS = URGENT_TIMEOUT_SECONDS

_PROMPT = """Translate the message below into Tamil, written in Tamil script.

You are translating a message that has already been written and recorded. Your
job is to say the same thing in Tamil. It is not to improve it, shorten it,
explain it, or answer it.

Rules, all of them absolute:

- Translate ONLY what is in the message. Add nothing.
- Do NOT give advice. Do NOT suggest what the reader should do. Do NOT reassure.
- Do NOT name a condition, a diagnosis, or a cause. If the message does not
  name one, neither do you.
- Keep every number, amount, date, time, medicine name, hospital name and
  person's name EXACTLY as written, in the same digits and spelling. "INR
  48,200" stays "INR 48,200". "108" stays "108".
- Keep URLs exactly as they are. Do not translate or alter a link.
- Keep the line breaks where they are.
- If part of the message is already in Tamil, leave that part as it is.

Output ONLY the Tamil translation. No preamble, no notes, no explanation, no
quotation marks around it, and no English restatement afterwards.

Message:
{text}"""


# The provenance line. Both scripts, because the son may read over her shoulder
# and because a note that only she can read cannot be checked by anyone else.
def _provenance_note(source_ref: str) -> str:
    return (f"\n\nபதிவு செய்யப்பட்ட {source_ref} இலிருந்து மொழிபெயர்க்கப்பட்டது.\n"
            f"Translated from the recorded {source_ref}.")


def _fallback_note(source_ref: str) -> str:
    return (f"\n\nஇதை தமிழில் தர முடியவில்லை. பதிவு செய்யப்பட்ட {source_ref} "
            f"ஆங்கிலத்தில் மேலே உள்ளது.\n"
            f"This could not be rendered in Tamil, so the recorded {source_ref} "
            f"is shown in English.")


class NoSourceRecord(Exception):
    """Asked to translate something that is not on the record.

    Raised, not returned, because it is a programming error rather than a
    runtime outcome: every caller has a recorded field to hand, and one that
    does not has composed the text itself, which is the thing this module
    exists to prevent.
    """


@dataclass(frozen=True)
class Rendering:
    """What will actually be sent, and what it was derived from.

    `source_text` is always present, whatever happened. The English record is
    the truth; `text` is the version of it this recipient can read.
    """

    text: str
    language: str
    translated: bool
    source_text: str
    source_ref: str
    source_sha256: str
    detail: str = ""

    def as_receipt_payload(self) -> dict[str, object]:
        """What goes on the chain.

        The hash, not the source text, is what proves the rendering was derived
        from the record — the rendered body is already stored beside it by the
        caller, and repeating the English in full would double every receipt
        for no extra proof.
        """
        return {
            "rendered_language": self.language,
            "translated": self.translated,
            "translated_from": self.source_ref,
            "source_sha256": self.source_sha256,
            "translation_detail": self.detail,
        }


def _off() -> bool:
    return os.getenv("ANBU_TRANSLATE_MODE", "gemini").strip().lower() in {
        "off", "none", "false",
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _passthrough(source_text: str, source_ref: str, detail: str = "") -> Rendering:
    """The English record, unchanged, labelled as what it is."""
    return Rendering(
        text=source_text, language="en", translated=False,
        source_text=source_text, source_ref=source_ref,
        source_sha256=_sha(source_text), detail=detail,
    )


def _call_model(prompt: str, timeout_seconds: int) -> str:
    """The one place this module talks to Gemini.

    Isolated exactly as `transcribe._call_model` is, so the refusal paths and
    the parsing can be tested against real code without a test spending a real
    call.
    """
    from google import genai

    from anbu_care.config import settings

    client = genai.Client()
    response = client.models.generate_content(
        model=settings().model,
        contents=prompt,
        config={"http_options": {"timeout": timeout_seconds * 1000}},
    )
    return (response.text or "").strip()


def render(source_text: str, *, language: str, source_ref: str,
           timeout_seconds: int = TRANSLATE_TIMEOUT_SECONDS) -> Rendering:
    """Render a recorded message for one reader. Never composes one.

    Args:
        source_text: The text as it is on the record. Not a prompt, not a
            request, not a question for the model to answer — the actual
            recorded words that would have been sent in English.
        language: The reader's preference. "en" returns the source untouched.
        source_ref: What record this came from, named the way it will appear in
            the provenance note: "bill summary", "doctor's note", "check-in
            question", "status update".

    Raises:
        NoSourceRecord: if there is no recorded text, or nothing to name it by.
            A translation whose source cannot be pointed at is authorship.
    """
    if not (source_text or "").strip():
        raise NoSourceRecord(
            "there is no recorded text to translate. Translation renders a record; "
            "it does not compose one."
        )
    if not (source_ref or "").strip():
        raise NoSourceRecord(
            "a translation must name the record it came from, so the message can "
            "say what it was derived from. Nothing was named."
        )

    wanted = (language or "en").strip().lower()
    if wanted == "en" or wanted not in SUPPORTED:
        # Not a failure and not worth a note in the message. English is the
        # record, and an unsupported language falls back to it rather than
        # being attempted blind.
        return _passthrough(
            source_text, source_ref,
            detail=("" if wanted == "en" else
                    f"no rendering exists for '{wanted}'; the recorded English was used"),
        )

    if _off():
        return _passthrough(source_text, source_ref,
                            detail="translation is switched off; the recorded English was used")

    try:
        raw = _call_model(_PROMPT.format(text=source_text), timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - failure is an outcome, not a crash
        logger.warning("translation unavailable: %s: %s", type(exc).__name__, exc)
        return _rendered_fallback(
            source_text, source_ref,
            f"translation failed ({type(exc).__name__}); the recorded English was sent"[:200],
        )

    text = _clean(raw)
    if not text:
        return _rendered_fallback(source_text, source_ref,
                                  "the model returned nothing; the recorded English was sent")

    # Belt and braces. The gate has already passed on the English source, so
    # this can only ADD a refusal — a transliterated lab name or a figure with
    # a unit survives translation and is caught here rather than travelling.
    from anbu_care.comms.policy import classify_message
    from anbu_care.schemas import MessageClass

    actual, hits = classify_message(text, MessageClass.LOGISTICS)
    if actual is MessageClass.CLINICAL:
        logger.warning("translation classified clinical (%s); sending the source instead", hits)
        return _rendered_fallback(
            source_text, source_ref,
            "the Tamil rendering was classified as carrying clinical detail "
            f"({', '.join(hits)}); the recorded English was sent instead",
        )

    return Rendering(
        text=text + _provenance_note(source_ref),
        language="ta", translated=True,
        source_text=source_text, source_ref=source_ref,
        source_sha256=_sha(source_text),
        detail=f"translated {len(source_text)} characters of the recorded {source_ref}",
    )


def _rendered_fallback(source_text: str, source_ref: str, detail: str) -> Rendering:
    """English, plus a line saying plainly that Tamil was not available.

    The note matters. Without it she gets an English message with no
    explanation and no way to tell a system that chose English from one that
    broke — and the next honest thing she could do, ask her son, is exactly
    what the note tells her the message is.
    """
    return Rendering(
        text=source_text + _fallback_note(source_ref),
        language="en", translated=False,
        source_text=source_text, source_ref=source_ref,
        source_sha256=_sha(source_text), detail=detail,
    )


def _clean(raw: str) -> str:
    """Strip the wrappers models add whatever the instruction said."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1].removeprefix("json").strip()
    # A model that answers "Here is the translation:" has added something, and
    # the rule is that it adds nothing. Drop a leading label, keep the rest.
    for lead in ("Tamil translation:", "Translation:", "Here is the translation:"):
        if text.lower().startswith(lead.lower()):
            text = text[len(lead):].strip()
    return text.strip('"').strip()
