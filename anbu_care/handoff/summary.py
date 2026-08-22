"""The emergency clinical summary.

A nurse is holding a seventy-one year old who cannot tell them what she takes,
and the family is eleven time zones away. This is the page that answers "what
do we already know about her", and it answers only that.

Three rules, and the second is the one that makes this defensible:

1. Every line is a stored field read back, with the source it came from. No
   model composes this. The composer is deterministic, exactly like the arrival
   brief, because a synthesis over a clinical record is the last place a
   plausible sentence should be able to appear.

2. **Facts, never advice.** No drug recommendation, no "avoid X", no suggested
   treatment, no severity opinion. A record that starts advising is a record
   pretending to be a clinician, and the moment it does that its own accuracy
   stops being the only thing at stake. `EmergencySummary` has no field an
   instruction could live in, and a test enforces that.

3. Missing is stated, never inferred. A field with nothing behind it reads
   "not on file", and — for allergies specifically — says what that does and
   does not mean. "No allergies recorded" and "no known allergies" are
   different sentences, and only one of them is true here.
"""

from __future__ import annotations

from datetime import datetime

from anbu_care import service
from anbu_care.schemas import (
    ArrivalFact,
    EmergencySummary,
    FactSource,
    ParentProfile,
    ParsedDocument,
)

NOT_ON_FILE = "not on file"

# Observation names worth pulling forward in an emergency. Matched loosely
# because documents name the same analyte several ways. This ONLY decides what
# is surfaced first; nothing is hidden, and the full document list is carried
# alongside so a clinician can see there is more.
_URGENT_ANALYTES = (
    "troponin", "creatinine", "egfr", "potassium", "sodium", "haemoglobin",
    "hemoglobin", "hba1c", "inr", "platelet", "wbc", "crp", "d-dimer",
    "urea", "bilirubin", "glucose",
)


def _stored(field: str) -> FactSource:
    return FactSource(kind="profile", field=field)


def _missing(note: str) -> FactSource:
    return FactSource(kind="unknown", note=note)


def _fact(label: str, value: str | None, source: FactSource) -> ArrivalFact:
    if value is None or not str(value).strip():
        return ArrivalFact(label=label, value=None, known=False, source=source)
    return ArrivalFact(label=label, value=str(value), known=True, source=source)


def _allergy_facts(profile: ParentProfile | None) -> list[ArrivalFact]:
    """Allergies, kept separate because this is the life-safety field.

    An empty list is the dangerous case. "No allergies recorded" can be read
    across a resus bay as "no known allergies", and those are not the same
    claim: one describes our file, the other describes the patient. This says
    which one it means, every time, in the value itself rather than in small
    print underneath that a hurried reader will skip.
    """
    if profile is None:
        return [ArrivalFact(
            label="Allergies", value=None, known=False,
            source=_missing("no parent record was found for this case"),
        )]

    if not profile.allergies:
        return [ArrivalFact(
            label="Allergies", value=None, known=False,
            source=_missing(
                "none are recorded on this file. That is not the same as no "
                "known allergies — nobody has confirmed the absence, only the "
                "silence. Ask."
            ),
        )]

    return [
        ArrivalFact(label="Allergy", value=allergy, known=True,
                    source=_stored("allergies"))
        for allergy in profile.allergies
    ]


def _medication_facts(profile: ParentProfile | None) -> list[ArrivalFact]:
    if profile is None or not profile.medications:
        return [ArrivalFact(
            label="Current medication", value=None, known=False,
            source=_missing("no medication is recorded on this file"),
        )]

    facts: list[ArrivalFact] = []
    for med in profile.medications:
        detail = ", ".join(p for p in (med.dose, med.frequency) if p)
        facts.append(ArrivalFact(
            label=med.name,
            value=detail or "recorded, without a dose or frequency on file",
            known=True,
            source=_stored("medications"),
        ))
    return facts


def _condition_facts(profile: ParentProfile | None) -> list[ArrivalFact]:
    if profile is None or not profile.chronic_conditions:
        return [ArrivalFact(
            label="Chronic conditions", value=None, known=False,
            source=_missing("no chronic condition is recorded on this file"),
        )]
    return [
        ArrivalFact(label="Condition", value=condition, known=True,
                    source=_stored("chronic_conditions"))
        for condition in profile.chronic_conditions
    ]


def _is_urgent(name: str) -> bool:
    lowered = name.strip().lower()
    return any(analyte in lowered for analyte in _URGENT_ANALYTES)


def _lab_facts(documents: list[ParsedDocument]) -> list[ArrivalFact]:
    """The most recent reading for each urgent analyte, with its date.

    A lab value without a date is not a lab value — a troponin from March means
    something entirely different from one taken this morning, and a clinician
    who cannot see which one they are looking at has been handed a hazard. So
    the date rides on the value, and a document that lost its parse date is
    skipped rather than shown undated.
    """
    if not documents:
        return [ArrivalFact(
            label="Recent labs", value=None, known=False,
            source=_missing("no documents have been ingested for this parent"),
        )]

    # analyte key -> (parsed_at, display name, rendered value)
    newest: dict[str, tuple[datetime, str, str]] = {}
    for doc in documents:
        if doc.parsed_at is None:
            continue
        for obs in doc.observations:
            if not _is_urgent(obs.name):
                continue
            key = obs.name.strip().lower()
            if key in newest and doc.parsed_at <= newest[key][0]:
                continue

            # A bare number is not a result. "Troponin 0.94" means nothing
            # without ng/mL, and a clinician reading it in a corridor should
            # not have to remember which assay this hospital runs. The
            # reference interval rides along for the same reason: "high"
            # without "against what" is an adjective, not a finding.
            shown = f"{obs.value} {obs.unit}".strip() if obs.unit else str(obs.value)
            if obs.flag and obs.flag.lower() in {"high", "low", "abnormal"}:
                shown = f"{shown}  [{obs.flag.upper()}]"
            if obs.reference_range:
                shown = f"{shown}  ref {obs.reference_range}"
            newest[key] = (doc.parsed_at, obs.name.strip(), shown)

    if not newest:
        return [ArrivalFact(
            label="Recent labs", value=None, known=False,
            source=_missing(
                "documents are on file but none carry a result this summary "
                "surfaces — the full documents are listed below"
            ),
        )]

    return [
        ArrivalFact(
            label=name,
            value=f"{value} ({parsed_at:%d %b %Y})",
            known=True,
            source=FactSource(kind="document", field="observations"),
        )
        for parsed_at, name, value in sorted(
            newest.values(), key=lambda entry: entry[0], reverse=True
        )
    ]


def compose_emergency_summary(parent_id: str) -> EmergencySummary:
    """Read the stored record back for a treating team. Nothing more."""
    profile = service.load_profile(parent_id)
    documents = service.list_documents(parent_id)

    identity: list[ArrivalFact] = []
    if profile is None:
        identity.append(ArrivalFact(
            label="Patient", value=None, known=False,
            source=_missing("no parent record was found"),
        ))
    else:
        identity.append(_fact("Name", profile.name, _stored("name")))
        identity.append(_fact("Age", str(profile.age), _stored("age")))

    return EmergencySummary(
        parent_id=parent_id,
        allergies=_allergy_facts(profile),
        identity=identity,
        conditions=_condition_facts(profile),
        medications=_medication_facts(profile),
        recent_labs=_lab_facts(documents),
        source_documents=[
            ArrivalFact(
                label=doc.kind.value if hasattr(doc.kind, "value") else str(doc.kind),
                value=f"{doc.source_filename or 'document'} ({doc.parsed_at:%d %b %Y})",
                known=True,
                source=FactSource(kind="document", field="document_id"),
            )
            for doc in documents
        ],
    )


def render_summary_text(summary: EmergencySummary) -> str:
    """Plain text, for a phone held sideways in a corridor."""
    lines = [
        "EMERGENCY CLINICAL SUMMARY",
        "Read-only. Not connected to any hospital system.",
        "",
        "ALLERGIES",
    ]
    for fact in summary.allergies:
        lines.append(f"  {fact.value}" if fact.known
                     else f"  {NOT_ON_FILE} — {fact.source.note}")

    for title, facts in (
        ("PATIENT", summary.identity),
        ("CONDITIONS", summary.conditions),
        ("MEDICATION", summary.medications),
        ("RECENT LABS", summary.recent_labs),
    ):
        lines += ["", title]
        for fact in facts:
            if fact.known:
                label = "" if fact.label in {"Condition", "Allergy", "lab"} else f"{fact.label}: "
                lines.append(f"  {label}{fact.value}")
            else:
                lines.append(f"  {fact.label}: {NOT_ON_FILE} — {fact.source.note}")

    lines += ["", "This is a record, not advice. It does not recommend treatment."]
    return "\n".join(lines)
