"""Onboarding / knowledge-base agent tools.

The agent does the multimodal reading (Gemini parses the photo or PDF in the
conversation); these tools persist what it read as structured, queryable data
and tell it how the new reading compares with the baseline.
"""

from __future__ import annotations

from typing import Any

from anbu_care import service
from anbu_care.schemas import (
    DocumentKind,
    FamilyContact,
    InsurancePolicy,
    Medication,
    Observation,
    ParentProfile,
    ParsedDocument,
    utcnow,
)


def create_parent_profile(
    name: str,
    age: int,
    city: str,
    lat: float,
    lon: float,
    chronic_conditions: list[str],
    allergies: list[str],
) -> dict[str, Any]:
    """Create the baseline record for a parent.

    Args:
        name: The parent's full name.
        age: Age in years.
        city: City of residence, e.g. "Thoothukudi".
        lat: Home latitude, used as the default location for triage.
        lon: Home longitude.
        chronic_conditions: Known ongoing conditions, e.g. ["Hypertension"].
        allergies: Known allergies, e.g. ["Penicillin"].

    Returns:
        The created profile, including the parent_id every later call needs.
    """
    profile = ParentProfile(
        parent_id=service.new_id("parent"),
        name=name,
        age=age,
        city=city,
        lat=lat,
        lon=lon,
        chronic_conditions=chronic_conditions,
        allergies=allergies,
    )
    service.save_profile(profile)
    return {"status": "created", "profile": profile.model_dump(mode="json")}


def record_medications(parent_id: str, medications: list[dict]) -> dict[str, Any]:
    """Attach the parent's current medications to their baseline record.

    Args:
        parent_id: The parent whose record to update.
        medications: One entry per drug, each with keys: name, dose, frequency.

    Returns:
        The medication list now on record.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}
    profile.medications = [Medication.model_validate(m) for m in medications]
    service.save_profile(profile)
    return {"status": "updated", "medications": [m.model_dump() for m in profile.medications]}


def record_insurance_policy(
    parent_id: str,
    insurer: str,
    policy_number: str,
    sum_insured_inr: int,
    network_hospitals: list[str],
    cashless_eligible: bool,
) -> dict[str, Any]:
    """Capture insurance upfront so coverage is known at emergency time.

    Args:
        parent_id: The parent this policy covers.
        insurer: Insurer name exactly as it appears on the policy, e.g. "Star Health".
        policy_number: The policy number.
        sum_insured_inr: Sum insured in rupees.
        network_hospitals: Network hospital names or ids listed on the policy.
        cashless_eligible: Whether the policy supports cashless admission.

    Returns:
        The stored policy.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}
    profile.policy = InsurancePolicy(
        insurer=insurer,
        policy_number=policy_number,
        sum_insured_inr=sum_insured_inr,
        network_hospitals=network_hospitals,
        cashless_eligible=cashless_eligible,
    )
    service.save_profile(profile)
    return {"status": "updated", "policy": profile.policy.model_dump(mode="json")}


def record_family_contact(
    parent_id: str,
    name: str,
    relationship: str,
    whatsapp_e164: str,
    timezone_name: str,
    is_primary: bool,
    consent_purposes: list[str],
) -> dict[str, Any]:
    """Register a family member and their purpose-specific consent.

    DPDP requires purpose-specific, timestamped opt-in — a blanket checkbox is
    not sufficient — so each purpose is recorded separately with its own
    timestamp.

    Args:
        parent_id: The parent this contact is family to.
        name: Contact's name.
        relationship: e.g. "son", "daughter", "sibling".
        whatsapp_e164: WhatsApp number in E.164 form, e.g. "+14155550100".
        timezone_name: IANA timezone, e.g. "America/Los_Angeles".
        is_primary: Whether this is the primary decision-maker.
        consent_purposes: Purposes consented to, from:
            "admission_alerts", "status_updates", "billing_updates", "claim_updates".

    Returns:
        The stored contact with consent timestamps.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}
    now = utcnow()
    contact = FamilyContact(
        name=name,
        relationship=relationship,
        whatsapp_e164=whatsapp_e164,
        timezone=timezone_name,
        is_primary=is_primary,
        consents={purpose: now for purpose in consent_purposes},
    )
    profile.family_contacts.append(contact)
    service.save_profile(profile)
    return {"status": "recorded", "contact": contact.model_dump(mode="json")}


def ingest_document(
    parent_id: str,
    kind: str,
    source_filename: str,
    summary: str,
    observations: list[dict],
) -> dict[str, Any]:
    """Store a document you have just read, as structured, queryable data.

    Read the attached image or PDF yourself and pass the values you extracted.
    Do not guess values that are not legible — omit them instead.

    Args:
        parent_id: Whose record this belongs to.
        kind: One of: blood_report, ecg, discharge_summary, prescription, bill, policy, other.
        source_filename: Original filename, for traceability.
        summary: One or two sentences on what the document says.
        observations: One entry per measurement, each with keys:
            name, value, unit, reference_range, flag ("high"/"low"/"normal"/"abnormal"),
            observed_on (ISO date).

    Returns:
        The stored document plus how it compares with the parent's baseline.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}

    try:
        doc_kind = DocumentKind(kind)
    except ValueError:
        doc_kind = DocumentKind.OTHER

    doc = ParsedDocument(
        document_id=service.new_id("doc"),
        parent_id=parent_id,
        kind=doc_kind,
        source_filename=source_filename,
        summary=summary,
        observations=[Observation.model_validate(o) for o in observations],
    )
    doc.delta_vs_baseline = _delta_vs_baseline(parent_id, doc)
    service.save_document(doc)
    return {
        "status": "ingested",
        "document": doc.model_dump(mode="json"),
        "delta_vs_baseline": doc.delta_vs_baseline,
    }


def _delta_vs_baseline(parent_id: str, doc: ParsedDocument) -> str:
    """Distinguish "new and abnormal" from "consistent with known baseline".

    An LDL of 165 means one thing in a patient whose last three readings were
    normal, and another in one who has been at 160 for two years.
    """
    prior = [d for d in service.list_documents(parent_id) if d.document_id != doc.document_id]
    history: dict[str, list[str]] = {}
    for old in prior:
        for obs in old.observations:
            history.setdefault(obs.name.lower(), []).append(obs.value)

    notes: list[str] = []
    for obs in doc.observations:
        seen = history.get(obs.name.lower())
        abnormal = (obs.flag or "").lower() in {"high", "low", "abnormal"}
        if seen is None:
            notes.append(
                f"{obs.name}={obs.value}{' ' + obs.unit if obs.unit else ''}: first recorded value"
                + (" and flagged abnormal — genuinely new" if abnormal else "")
            )
        elif obs.value in seen:
            notes.append(f"{obs.name}={obs.value}: unchanged from a previous reading — consistent with baseline")
        else:
            notes.append(
                f"{obs.name}={obs.value}: changed from {seen[-1]}"
                + (" and flagged abnormal — new and abnormal" if abnormal else "")
            )
    return "; ".join(notes) if notes else "no measurements to compare"


def get_parent_profile(parent_id: str) -> dict[str, Any]:
    """Read back the full baseline record, including policy and contacts.

    Args:
        parent_id: The parent to look up.

    Returns:
        The profile, plus every document ingested for them so far.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}
    docs = service.list_documents(parent_id)
    return {
        "status": "ok",
        "profile": profile.model_dump(mode="json"),
        "documents": [d.model_dump(mode="json") for d in docs],
    }
