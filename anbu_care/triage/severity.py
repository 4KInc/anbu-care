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
# Phrases that mean somebody should be woken now.
#
# Sourced, not invented. The cardiac and stroke entries follow NHS heart attack
# and stroke guidance; the undifferentiated emergencies follow the London
# Ambulance Service "when to call 999" list (chest pain, difficulty breathing,
# unconsciousness, severe loss of blood, severe burns, choking, fitting,
# drowning, severe allergic reaction). See docs/CITATIONS.md.
#
# Two things this table is NOT. It is not a clinical protocol: it has not been
# reviewed by a clinician and a real deployment must have that done. And it is
# not a diagnosis — a match means "these words appeared", which is why the
# receipt records the matched phrase and never a conclusion.
#
# Weighted towards the atypical. The classic crushing-chest presentation is the
# one everybody already acts on. Older women, and people with diabetes, more
# often present with jaw or back pain, nausea and sweating, and those are the
# presentations that get missed.
RED_FLAGS: dict[str, tuple[str, str]] = {
    # ---- cardiac -----------------------------------------------------------
    "chest pain": ("cardiology", "chest pain — possible acute coronary syndrome"),
    "chest tightness": ("cardiology", "chest tightness — possible cardiac event"),
    "tight chest": ("cardiology", "chest tightness — possible cardiac event"),
    "crushing chest": ("cardiology", "crushing chest pain — high-probability cardiac event"),
    "chest pressure": ("cardiology", "chest pressure — possible acute coronary syndrome"),
    "chest heavy": ("cardiology", "chest heaviness — possible acute coronary syndrome"),
    "heavy chest": ("cardiology", "chest heaviness — possible acute coronary syndrome"),
    "weight on my chest": ("cardiology", "chest heaviness — possible acute coronary syndrome"),
    "band around my chest": ("cardiology", "constricting chest pain — possible cardiac event"),
    "chest burning": ("cardiology", "burning chest pain — cardiac pain is often mistaken for indigestion"),
    "heartburn": ("cardiology", "burning chest pain — cardiac pain is often mistaken for indigestion"),
    "left arm pain": ("cardiology", "radiating left-arm pain — classic cardiac referral pattern"),
    "arm pain": ("cardiology", "arm pain — cardiac pain radiates to either arm"),
    "jaw pain": ("cardiology", "jaw pain — atypical cardiac presentation, more common in women"),
    "neck pain": ("cardiology", "neck pain — recognised cardiac radiation pattern"),
    "shoulder pain": ("cardiology", "shoulder pain — recognised cardiac radiation pattern"),
    "upper back pain": ("cardiology", "upper back pain — atypical cardiac presentation"),
    "cardiac arrest": ("cardiology", "suspected cardiac arrest"),
    "heart attack": ("cardiology", "reported heart attack"),
    "palpitations": ("cardiology", "palpitations — possible arrhythmia"),
    "racing heart": ("cardiology", "tachycardia — possible arrhythmia"),
    "irregular heartbeat": ("cardiology", "irregular pulse — possible arrhythmia"),

    # ---- breathing ---------------------------------------------------------
    "shortness of breath": ("cardiology", "dyspnoea — cardiac or respiratory compromise"),
    "short of breath": ("cardiology", "dyspnoea — cardiac or respiratory compromise"),
    "breathless": ("cardiology", "breathlessness — cardiac or respiratory compromise"),
    "difficulty breathing": ("emergency", "difficulty breathing — a 999 criterion"),
    "cannot breathe": ("emergency", "difficulty breathing — a 999 criterion"),
    "can't breathe": ("emergency", "difficulty breathing — a 999 criterion"),
    "cant breathe": ("emergency", "difficulty breathing — a 999 criterion"),
    "gasping": ("emergency", "gasping — respiratory distress"),
    "choking": ("emergency", "choking — a 999 criterion"),
    "blue lips": ("emergency", "cyanosis — hypoxia"),

    # ---- stroke ------------------------------------------------------------
    "slurred speech": ("neurology", "slurred speech — stroke red flag"),
    "cannot speak": ("neurology", "speech loss — stroke red flag"),
    "face droop": ("neurology", "facial droop — stroke red flag"),
    "facial droop": ("neurology", "facial droop — stroke red flag"),
    "face has dropped": ("neurology", "facial droop — stroke red flag"),
    "one-sided weakness": ("neurology", "unilateral weakness — stroke red flag"),
    "weakness on one side": ("neurology", "unilateral weakness — stroke red flag"),
    "arm has gone numb": ("neurology", "unilateral numbness — stroke red flag"),
    "cannot lift": ("neurology", "unilateral weakness — stroke red flag"),
    "sudden severe headache": ("neurology", "thunderclap headache — haemorrhage red flag"),
    "worst headache": ("neurology", "thunderclap headache — haemorrhage red flag"),
    "sudden blurred vision": ("neurology", "sudden visual loss — stroke red flag"),
    "sudden vision loss": ("neurology", "sudden visual loss — stroke red flag"),

    # ---- undifferentiated emergency ---------------------------------------
    "collapse": ("emergency", "collapse — undifferentiated emergency"),
    "collapsed": ("emergency", "collapse — undifferentiated emergency"),
    "unconscious": ("emergency", "loss of consciousness — a 999 criterion"),
    "passed out": ("emergency", "loss of consciousness — a 999 criterion"),
    "fainted": ("emergency", "syncope — undifferentiated emergency"),
    "unresponsive": ("emergency", "unresponsive — a 999 criterion"),
    "not waking up": ("emergency", "unresponsive — a 999 criterion"),
    "seizure": ("neurology", "seizure activity — a 999 criterion"),
    "fitting": ("neurology", "seizure activity — a 999 criterion"),
    "convulsion": ("neurology", "seizure activity — a 999 criterion"),
    "heavy bleeding": ("emergency", "uncontrolled bleeding — a 999 criterion"),
    "bleeding a lot": ("emergency", "uncontrolled bleeding — a 999 criterion"),
    "will not stop bleeding": ("emergency", "uncontrolled bleeding — a 999 criterion"),
    "vomiting blood": ("emergency", "haematemesis"),
    "coughing blood": ("emergency", "haemoptysis"),
    "blood in stool": ("emergency", "gastrointestinal bleeding"),
    "severe allergic reaction": ("emergency", "anaphylaxis — a 999 criterion"),
    "anaphylaxis": ("emergency", "anaphylaxis — a 999 criterion"),
    "throat closing": ("emergency", "airway compromise — anaphylaxis red flag"),
    "swollen tongue": ("emergency", "airway compromise — anaphylaxis red flag"),
    "severe burn": ("emergency", "severe burn — a 999 criterion"),
    "poisoning": ("emergency", "poisoning or overdose"),
    "overdose": ("emergency", "poisoning or overdose"),
    "hit her head": ("neurology", "head injury in an elderly patient — bleed risk"),
    "hit his head": ("neurology", "head injury in an elderly patient — bleed risk"),
    "head injury": ("neurology", "head injury in an elderly patient — bleed risk"),
    "stiff neck with fever": ("emergency", "meningism — sepsis or meningitis red flag"),
    "cannot wake": ("emergency", "reduced consciousness — sepsis red flag"),
    "cannot stand": ("emergency", "sudden inability to weight-bear — fracture or collapse"),
    "severe abdominal pain": ("emergency", "acute abdomen"),
    "cannot pass urine": ("emergency", "urinary retention — obstruction risk"),
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
