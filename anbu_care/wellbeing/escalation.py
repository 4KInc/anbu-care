"""Deciding that a check-in needs a human, without deciding what is wrong.

Two different things get confused here, and only one of them is dangerous.

Inferring a clinical state from a typed sentence — writing "cardiac event" onto
a health record because someone described a feeling — is diagnosis by chatbot.
It stays forbidden. Nothing in this module produces a finding, and the stored
check-in never gains a severity field.

Recognising that a message is a call for help and getting a person involved is
not a clinical judgement at all. It is routing. A neighbour phoning to say
"amma is holding her chest" already reaches the deterministic severity table;
there was never a principled reason the parent's own words should not.

    "maarbu vali, moochu vaanga mudiyala"   (transliterated Tamil)
            |
            v   Gemini: normalise wording into symptom terms  (ADVISORY)
    ["chest pressure", "shortness of breath"]
            |
            v   RED_FLAGS table: deterministic, already the guarantee layer
    HIGH -> open a case, tell a human, say to call 108

Gemini widens what the system RECOGNISES. It never decides what is URGENT. The
severity table does, exactly as it does for every other intake, because a
guarantee that lives in a prompt is not a guarantee.

Language is the widest part of that gap and the least optional. The table is a
list of English phrases. The person using this product is seventy-one and lives
in Thoothukudi, and at 2am she will write in whatever comes first — Tamil,
Tamil in English letters, or half of each. No keyword table can be extended far
enough to cover that. A model can.

Two properties keep that honest rather than decorative:

1. The raw text always goes through the table as a floor. The model can only
   ADD symptom terms, never remove them, so it cannot suppress a red flag. If
   it times out, errors, or returns nonsense, "crushing chest pain" still
   escalates.

2. Its worst failure is therefore a false positive: somebody gets a phone call
   they did not need. The opposite failure — chest pain filed quietly as a
   check-in — is the one this module exists to prevent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from anbu_care.schemas import Severity
from anbu_care.triage.severity import classify_severity

logger = logging.getLogger(__name__)

# India's ambulance line. Configurable because a real deployment elsewhere must
# not inherit a number that does not answer.
EMERGENCY_NUMBER = "108"

# Severities that mean a person should be told now.
ESCALATING = {Severity.HIGH}

_PROMPT = """You normalise informal descriptions of how someone feels into short clinical symptom terms.

The message may be in ANY language, or in a language written with English
letters. Tamil, transliterated Tamil ("maarbu vali", "moochu vaanga
mudiyala"), Hindi, or a mix of a language and English are all expected. Always
return the terms in ENGLISH, whatever the message was written in.

Return ONLY a JSON array of lowercase English symptom phrases, at most six.
Use plain clinical wording such as "chest pain", "chest pressure", "shortness
of breath", "difficulty breathing", "dizziness", "fainting", "confusion",
"slurred speech", "weakness on one side", "severe bleeding", "vomiting",
"fever", "severe abdominal pain", "palpitations", "seizure".

Return [] if the message describes ordinary daily life, mood, sleep, appetite
or general wellbeing with no physical symptom.

Do NOT diagnose. Do NOT infer severity. Do NOT add anything not implied by the
message. Output the JSON array and nothing else.

Message: {text}"""


@dataclass
class Escalation:
    """What was recognised, and what follows from it.

    Deliberately carries no diagnosis. `matched` records the phrases the table
    hit, so the reason is auditable as "these words matched" rather than as a
    clinical opinion.
    """

    escalate: bool
    severity: Severity
    symptoms: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    model_terms: list[str] = field(default_factory=list)
    model_used: bool = False
    model_note: str = ""


def extract_symptoms(text: str) -> tuple[list[str], bool, str]:
    """Ask Gemini to restate informal wording as symptom terms.

    Advisory. The return value is fed to the table as extra symptoms; it can
    only widen the match. Every failure path returns an empty list, because a
    model that cannot answer must not be able to quieten anything.
    """
    try:
        from google import genai

        client = genai.Client()
        from anbu_care.config import settings

        response = client.models.generate_content(
            model=settings().model,
            contents=_PROMPT.format(text=text),
        )
        raw = (response.text or "").strip()
        # Models like to wrap JSON in a fence whatever the instruction says.
        if raw.startswith("```"):
            raw = raw.split("```")[1].removeprefix("json").strip()
        terms = json.loads(raw)
        if not isinstance(terms, list):
            return [], False, "model did not return a list; ignored"
        cleaned = [str(t).strip().lower() for t in terms if str(t).strip()][:6]
        return cleaned, True, f"model suggested {len(cleaned)} term(s)"
    except Exception as exc:  # noqa: BLE001 - advisory input, never load-bearing
        logger.warning("symptom extraction unavailable: %s: %s", type(exc).__name__, exc)
        return [], False, f"model unavailable ({type(exc).__name__}); keyword scan only"


def assess(text: str, chronic_conditions: list[str] | None = None) -> Escalation:
    """Decide whether a human needs to be told. Never what is wrong.

    The raw text goes to the table regardless of what the model said, so the
    model's contribution is strictly additive.
    """
    model_terms, model_used, note = extract_symptoms(text)

    # free_text is passed through untouched: this is the floor that holds when
    # the model is absent, wrong, or slow.
    result = classify_severity(
        symptoms=model_terms,
        free_text=text,
        chronic_conditions=chronic_conditions or [],
    )

    return Escalation(
        escalate=result.severity in ESCALATING,
        severity=result.severity,
        symptoms=model_terms,
        matched=list(result.rationale),
        model_terms=model_terms,
        model_used=model_used,
        model_note=note,
    )


def reply_text(escalation: Escalation, alerted: list[str],
               called: list[str] | None = None) -> str:
    """What to say back, promising only what actually happened.

    The "we have alerted" sentence appears only when a notification was really
    accepted by the transport. This is the one message where claiming an action
    that did not happen could get somebody hurt, so it is conditioned on the
    delivery result rather than on having attempted it.
    """
    if not escalation.escalate:
        return "Thanks, that's noted."

    lines = [
        f"This sounds urgent. If it is an emergency, call {EMERGENCY_NUMBER} now.",
    ]
    if alerted:
        lines.append("We have messaged " + " and ".join(alerted) + ".")
    if called:
        # "Calling", not "spoke to". Twilio returns queued; whether the phone
        # was answered is not known yet, and saying otherwise could stop
        # somebody making the call themselves.
        lines.append("We are also calling " + " and ".join(called) + " now.")
    if not alerted and not called:
        lines.append(
            "We could not reach anyone in your care circle, so please call someone yourself."
        )
    return " ".join(lines)
