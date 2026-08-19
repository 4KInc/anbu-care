"""Triage agent tools.

Severity and routing are computed in code, not by the model. The agent's job is
to gather the report accurately and relay the explanation the engine produced —
not to second-guess a red flag.
"""

from __future__ import annotations

from typing import Any

from anbu_care import service
from anbu_care.kb.hospitals import KB_META, get_hospital, load_hospitals
from anbu_care.schemas import SymptomReport
from anbu_care.triage.routing import route


def run_triage(
    parent_id: str,
    symptoms: list[str],
    free_text: str,
    reported_by: str,
    lat: float,
    lon: float,
    case_id: str,
) -> dict[str, Any]:
    """Classify severity and pick the right hospital, with the reasoning.

    Severity is decided by deterministic rules over the reported symptoms and
    the parent's known history. Hospital ranking weighs capability match,
    distance, and cashless-network status, and records every term.

    Args:
        parent_id: Whose case this is.
        symptoms: Short symptom phrases, e.g. ["chest pain", "sweating"].
        free_text: The caller's own words, verbatim where possible.
        reported_by: Who reported it, e.g. "neighbour", "parent", "daughter".
        lat: Latitude the parent is at now. Pass 0 to use their home location.
        lon: Longitude the parent is at now. Pass 0 to use their home location.
        case_id: The case to record this against. Pass "" to open a new case.

    Returns:
        Severity, matched specialties, the ranked hospitals with per-term
        reasons, the recommended hospital, and a plain-language explanation.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return {"status": "error", "error": f"no profile for parent_id {parent_id}"}

    if not case_id:
        case_id = service.open_case(parent_id).case_id

    report = SymptomReport(
        parent_id=parent_id,
        reported_by=reported_by,
        symptoms=symptoms,
        free_text=free_text,
        lat=lat or None,
        lon=lon or None,
    )
    decision = route(report, profile, case_id=case_id)

    receipt = service.append_receipt(
        case_id,
        kind="triage.decision",
        actor="triage_agent",
        payload={
            "report": report.model_dump(mode="json"),
            "severity": decision.severity.value,
            "severity_rationale": decision.severity_rationale,
            "matched_specialties": decision.matched_specialties,
            "recommended_hospital_id": decision.recommended_hospital_id,
            "ranked": [
                {
                    "hospital_id": s.hospital.hospital_id,
                    "name": s.hospital.name,
                    "total_score": s.total_score,
                    "distance_km": s.distance_km,
                    "capability_score": s.capability_score,
                    "network_match": s.network_match,
                    "reasons": s.reasons,
                }
                for s in decision.ranked_hospitals
            ],
            "explanation": decision.explanation,
            "kb_snapshot": KB_META()["seeded_on"],
        },
    )

    case = service.load_case(case_id)
    if case is not None:
        case.stage = "triaged"
        case.triage_decision_id = receipt.receipt_id
        service.update_case(case)

    service.publish_event(
        service.settings().topic_intake,
        {"case_id": case_id, "parent_id": parent_id, "severity": decision.severity.value},
    )

    recommended = get_hospital(decision.recommended_hospital_id or "")
    return {
        "status": "ok",
        "case_id": case_id,
        "severity": decision.severity.value,
        "severity_rationale": decision.severity_rationale,
        "matched_specialties": decision.matched_specialties,
        "recommended_hospital": recommended.model_dump(mode="json") if recommended else None,
        "ranked_hospitals": [
            {
                "name": s.hospital.name,
                "hospital_id": s.hospital.hospital_id,
                "total_score": s.total_score,
                "distance_km": s.distance_km,
                "capability_score": s.capability_score,
                "network_match": s.network_match,
                "reasons": s.reasons,
            }
            for s in decision.ranked_hospitals
        ],
        "explanation": decision.explanation,
        "receipt_id": receipt.receipt_id,
        "knowledge_base": {
            "status": KB_META()["status"],
            "seeded_on": KB_META()["seeded_on"],
            "warning": KB_META()["warning"],
        },
    }


def list_known_hospitals() -> dict[str, Any]:
    """List the hospitals in the knowledge base and their capabilities.

    Returns:
        Every hospital with its specialties, units, and empanelled insurers,
        plus the snapshot metadata. The data is a dated seed, not a live feed.
    """
    return {
        "status": "ok",
        "meta": KB_META(),
        "hospitals": [h.model_dump(mode="json") for h in load_hospitals()],
    }
