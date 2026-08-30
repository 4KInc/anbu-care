"""Which language she actually writes in, decided by the cheapest model that can.

Her profile carries a language, but it was chosen for her at onboarding, usually
by a son filling in a form from another country. It is a guess about somebody
else's mother tongue made by somebody who moved away. The Memory Bank has held
a `language` lesson since it was built, with a writer and a reader and nothing
in between, because nothing on the inbound path ever established what she
actually used. This is that missing half.

WHY A SECOND, SMALLER MODEL. Everything else here runs on Gemini 3.5 Flash,
which reads discharge summaries and hears Tamil out of a voice note. This asks
one question with a two-letter answer, and spending a frontier model on a
one-token classification is the wrong trade on a path that already owes her a
reply inside fifteen seconds. Gemini 2.5 Flash Lite answers it and costs less.
That is the entire reason, and it is an architecture reason rather than a model
count.

WHAT IT IS NOT ALLOWED TO DECIDE. Nothing clinical, and nothing consequential.
A language selects which pre-translated sentence she is sent and which language
the renderer targets. It never touches severity, consent, money, or who is
told. If it is wrong she gets a check-in in the language her profile already
said, which is exactly what happens today, every time.

THE ANSWER IS CONSTRAINED, not trusted. The model is given a closed list and
its reply is checked against that list. A code outside it, a sentence, an empty
string, an apology - all return None. So the worst a confused model can do here
is fail to teach us something, which is the state this replaces.

IT SEES A SAMPLE, NOT A RECORD. At most 240 characters, and only from a message
she wrote herself. It is the same Vertex project the transcription already sends
her whole voice note to, so this adds no new place her words travel to, and it
sends materially less of them.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger(__name__)

# The model, named here rather than in config, because this is the one call in
# the system that deliberately does NOT use `settings().model`.
MODEL = os.getenv("ANBU_DETECT_MODEL", "gemini-2.5-flash-lite")

# The closed list. Every language a family here plausibly writes in, and
# nothing else. Widening it is an edit, not a thing the model can do.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "ta": "Tamil",
    "hi": "Hindi",
    "ml": "Malayalam",
    "te": "Telugu",
    "kn": "Kannada",
    "mr": "Marathi",
    "bn": "Bengali",
}

SAMPLE_LIMIT = 240
TIMEOUT_SECONDS = 8

_CODE = re.compile(r"^[a-z]{2}$")

_PROMPT = (
    "Reply with exactly one language code from this list and nothing else: "
    + ", ".join(sorted(LANGUAGES))
    + ". It is the language the following message is written in. If it is not "
    "one of those, or you cannot tell, reply with: unknown\n\nMessage:\n"
)


def enabled() -> bool:
    return os.getenv("ANBU_DETECT_LANGUAGE_MODE", "gemini").strip().lower() not in {
        "off", "none", "false", ""
    }


def detect(text: str) -> str | None:
    """Her language as a two-letter code, or None.

    None is the ordinary answer for a message too short to judge, a model that
    was unsure, and a model that was unreachable. All three mean the same thing
    to the caller - we did not learn anything - so they are not distinguished.
    Never raises.
    """
    text = (text or "").strip()
    if not enabled() or len(text) < 8:
        return None
    try:
        answer = _ask(text[:SAMPLE_LIMIT])
    except Exception as e:  # noqa: BLE001 - never load-bearing
        log.warning("language detection failed: %s", e)
        return None

    answer = (answer or "").strip().lower()
    if not _CODE.match(answer) or answer not in LANGUAGES:
        # Includes the literal "unknown", which is the model doing as it was
        # told rather than failing.
        if answer and answer != "unknown":
            log.info("language detection returned %r, which is not on the list", answer)
        return None
    return answer


def _ask(sample: str) -> str:
    """The one model call. Isolated so every path above can be tested without
    spending one, and so the model choice is visible in a single place."""
    from google import genai

    client = genai.Client()
    response = client.models.generate_content(
        model=MODEL,
        contents=_PROMPT + sample,
        config={
            # A two-letter answer needs no room to reason itself into a
            # sentence, and the cap is cheaper than parsing one.
            "max_output_tokens": 8,
            "temperature": 0.0,
            "http_options": {"timeout": TIMEOUT_SECONDS * 1000},
        },
    )
    return response.text or ""
