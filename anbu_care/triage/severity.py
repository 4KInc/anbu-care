"""Deterministic severity classification.

The LLM writes the family-facing explanation; it does not decide severity.
A red-flag symptom must escalate on every run, so the rule that escalates it is
code, not a prompt. This is the deterministic fallback the architecture promises
for each agent.
"""

from __future__ import annotations

import re

from anbu_care.schemas import Severity

# Red flags force HIGH regardless of anything else, and carry the specialty
# the case must be routed to.
RED_FLAGS: dict[str, tuple[str, str]] = {
    "chest pain": ("cardiology", "chest pain — possible acute coronary syndrome"),
    "chest tightness": ("cardiology", "chest tightness — possible cardiac event"),
    "crushing chest": ("cardiology", "crushing chest pain — high-probability cardiac event"),
    "left arm pain": ("cardiology", "radiating left-arm pain — classic cardiac referral pattern"),
    "jaw pain": ("cardiology", "jaw pain with cardiac context — atypical presentation"),
    "shortness of breath": ("cardiology", "dyspnoea — cardiac or respiratory compromise"),
    "breathless": ("cardiology", "breathlessness — cardiac or respiratory compromise"),
    "collapse": ("emergency", "collapse — undifferentiated emergency"),
    "unconscious": ("emergency", "loss of consciousness"),
    "cardiac arrest": ("cardiology", "suspected cardiac arrest"),
    "palpitations": ("cardiology", "palpitations — possible arrhythmia"),
    "slurred speech": ("neurology", "slurred speech — stroke red flag"),
    "face droop": ("neurology", "facial droop — stroke red flag"),
    "facial droop": ("neurology", "facial droop — stroke red flag"),
    "one-sided weakness": ("neurology", "unilateral weakness — stroke red flag"),
    "weakness on one side": ("neurology", "unilateral weakness — stroke red flag"),
    "sudden severe headache": ("neurology", "thunderclap headache — haemorrhage red flag"),
    "seizure": ("neurology", "seizure activity"),
    "heavy bleeding": ("emergency", "uncontrolled bleeding"),
    "vomiting blood": ("emergency", "haematemesis"),
}

# (specialty, why, specialty this symptom becomes high-risk for given a matching
# history). Dizziness in a patient with a prior MI is not the same complaint as
# dizziness in a patient without one.
MEDIUM_FLAGS: dict[str, tuple[str, str, str | None]] = {
    "persistent fever": ("internal_medicine", "fever persisting beyond 48h", None),
    "high fever": ("internal_medicine", "high-grade fever", None),
    "dehydration": ("internal_medicine", "dehydration risk", None),
    "fall": ("orthopaedics", "fall — fracture and head-injury risk in an elderly patient", "neurology"),
    "severe pain": ("internal_medicine", "severe uncontrolled pain", None),
    "dizziness": ("internal_medicine", "dizziness — fall and perfusion risk", "cardiology"),
    "confusion": ("internal_medicine", "new confusion — delirium or infection", "neurology"),
    "swelling": ("internal_medicine", "new swelling — fluid overload risk", "cardiology"),
    "nausea": ("internal_medicine", "nausea — nonspecific but a known atypical cardiac presentation", "cardiology"),
    "sweating": ("internal_medicine", "diaphoresis — nonspecific but a known cardiac accompaniment", "cardiology"),
    "fatigue": ("internal_medicine", "new fatigue — deconditioning or cardiac output drop", "cardiology"),
}

LOW_FLAGS: dict[str, tuple[str, str, str | None]] = {
    "fever": ("general", "fever without red flags", None),
    "cough": ("general", "cough without respiratory distress", None),
    "cold": ("general", "upper respiratory symptoms", None),
    "minor injury": ("general", "minor injury", None),
    "rash": ("general", "skin complaint", None),
    "minor fall": ("general", "minor fall, no head strike reported", "neurology"),
    "back pain": ("general", "musculoskeletal pain", None),
}

# A known cardiac or cerebrovascular history lowers the bar for escalation:
# the same symptom means more in a patient who already has the disease.
ESCALATING_CONDITIONS = {
    "cardiology": {"hypertension", "coronary artery disease", "cad", "prior mi",
                   "myocardial infarction", "angina", "arrhythmia", "heart failure",
                   "high cholesterol", "hyperlipidemia", "hyperlipidaemia"},
    "neurology": {"stroke", "tia", "transient ischemic attack", "atrial fibrillation"},
}


class SeverityResult:
    def __init__(self, severity: Severity, rationale: list[str], specialties: list[str]):
        self.severity = severity
        self.rationale = rationale
        self.specialties = specialties


def _normalise(symptoms: list[str], free_text: str) -> str:
    joined = " ; ".join(symptoms) + " ; " + free_text
    return re.sub(r"\s+", " ", joined.lower())


def classify_severity(
    symptoms: list[str],
    free_text: str = "",
    chronic_conditions: list[str] | None = None,
) -> SeverityResult:
    haystack = _normalise(symptoms, free_text)
    conditions = {c.strip().lower() for c in (chronic_conditions or [])}

    rationale: list[str] = []
    specialties: list[str] = []
    # Specialties this presentation would become high-risk for, if the patient
    # has the matching history.
    history_sensitive: set[str] = set()

    def add_specialty(name: str) -> None:
        if name not in specialties:
            specialties.append(name)

    def scan_red() -> bool:
        hit = False
        for phrase, (specialty, why) in RED_FLAGS.items():
            if phrase in haystack:
                hit = True
                rationale.append(f"matched '{phrase}': {why}")
                add_specialty(specialty)
        return hit

    def scan(table: dict[str, tuple[str, str, str | None]]) -> bool:
        hit = False
        for phrase, (specialty, why, sensitive_to) in table.items():
            if phrase in haystack:
                hit = True
                rationale.append(f"matched '{phrase}': {why}")
                add_specialty(specialty)
                if sensitive_to:
                    history_sensitive.add(sensitive_to)
        return hit

    if scan_red():
        severity = Severity.HIGH
    elif scan(MEDIUM_FLAGS):
        severity = Severity.MEDIUM
    elif scan(LOW_FLAGS):
        severity = Severity.LOW
    else:
        severity = Severity.MEDIUM
        rationale.append(
            "no rule matched — defaulting to MEDIUM rather than LOW, because an "
            "unrecognised complaint in an elderly patient is not evidence of a mild one"
        )
        add_specialty("emergency")

    # History-based escalation: a matching history turns a history-sensitive
    # presentation into a HIGH-severity one, and pulls in that specialty so the
    # routing step filters for the right capability.
    if severity is not Severity.HIGH:
        for specialty in sorted(history_sensitive | set(specialties)):
            overlap = conditions & ESCALATING_CONDITIONS.get(specialty, set())
            if overlap:
                severity = Severity.HIGH
                add_specialty(specialty)
                rationale.append(
                    f"escalated to HIGH: known history of {', '.join(sorted(overlap))} "
                    f"makes this presentation materially higher-risk for {specialty}"
                )

    if severity is Severity.HIGH and "emergency" not in specialties:
        add_specialty("emergency")

    return SeverityResult(severity, rationale, specialties)
