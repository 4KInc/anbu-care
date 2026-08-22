"""Location- and severity-aware hospital routing.

The scoring is deterministic and every term is recorded, because the point of
this layer is not that it picks a hospital — it is that it can say *why* it
picked one, including when it deliberately picks a farther one.
"""

from __future__ import annotations

import math
import uuid

from anbu_care.kb.hospitals import load_hospitals
from anbu_care.schemas import (
    Hospital,
    HospitalScore,
    ParentProfile,
    Severity,
    SymptomReport,
    TriageDecision,
)
from anbu_care.triage.severity import classify_severity

# Specialty -> the hospital capability flags that actually satisfy it.
CAPABILITY_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "cardiology": ("cardiac_icu",),
    "neurology": ("stroke_unit",),
}

# Weights. Capability dominates for HIGH severity — that is the whole point of
# accepting extra travel time. For LOW severity, proximity dominates.
WEIGHTS: dict[Severity, dict[str, float]] = {
    Severity.HIGH: {"capability": 0.60, "distance": 0.20, "network": 0.20},
    Severity.MEDIUM: {"capability": 0.40, "distance": 0.35, "network": 0.25},
    Severity.LOW: {"capability": 0.15, "distance": 0.60, "network": 0.25},
}

EARTH_RADIUS_KM = 6371.0
# Distance beyond which the proximity term is fully spent. Within one city,
# 15 km is effectively "the far side of town".
DISTANCE_HORIZON_KM = 15.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def capability_score(hospital: Hospital, specialties: list[str], severity: Severity) -> tuple[float, list[str]]:
    """How well this hospital's capability matches what the case needs."""
    reasons: list[str] = []
    if not specialties:
        return (1.0 if hospital.has_emergency else 0.0), ["no specialty requirement; emergency cover only"]

    scored: list[float] = []
    for specialty in specialties:
        if specialty in {"emergency", "general"}:
            hit = hospital.has_emergency if specialty == "emergency" else True
            scored.append(1.0 if hit else 0.0)
            reasons.append(
                f"{specialty}: {'24x7 emergency cover' if hit else 'no emergency cover'}"
            )
            continue

        required_flags = CAPABILITY_REQUIREMENTS.get(specialty, ())
        has_flag = any(getattr(hospital, flag, False) for flag in required_flags)
        listed = specialty in hospital.specialties

        if has_flag:
            scored.append(1.0)
            reasons.append(f"{specialty}: dedicated unit present ({', '.join(required_flags)})")
        elif listed:
            # Named as a service but without the dedicated unit. Adequate for a
            # stable case, not for an acute one.
            value = 0.65 if severity is not Severity.HIGH else 0.35
            scored.append(value)
            reasons.append(
                f"{specialty}: listed as a service but no dedicated unit — "
                f"{'acceptable at this severity' if value > 0.5 else 'thin for an acute presentation'}"
            )
        elif "multi_specialty" in hospital.specialties:
            scored.append(0.4)
            reasons.append(f"{specialty}: multi-specialty cover only, no named {specialty} service")
        else:
            scored.append(0.1)
            reasons.append(f"{specialty}: no matching capability")

    return sum(scored) / len(scored), reasons


def rank_hospitals(
    *,
    lat: float,
    lon: float,
    severity: Severity,
    specialties: list[str],
    insurer: str | None = None,
    network_hospitals: list[str] | None = None,
    hospitals: tuple[Hospital, ...] | None = None,
) -> list[HospitalScore]:
    pool = hospitals if hospitals is not None else load_hospitals()
    weights = WEIGHTS[severity]
    network_ids = {h.lower() for h in (network_hospitals or [])}

    scores: list[HospitalScore] = []
    for hospital in pool:
        distance = haversine_km(lat, lon, hospital.lat, hospital.lon)
        proximity = max(0.0, 1.0 - min(distance, DISTANCE_HORIZON_KM) / DISTANCE_HORIZON_KM)

        capability, cap_reasons = capability_score(hospital, specialties, severity)

        in_network = bool(
            (insurer and insurer in hospital.empanelled_insurers)
            or hospital.hospital_id.lower() in network_ids
            or hospital.name.lower() in network_ids
        )
        network_term = 1.0 if in_network else 0.0

        total = (
            weights["capability"] * capability
            + weights["distance"] * proximity
            + weights["network"] * network_term
        )

        reasons = [
            f"{distance:.1f} km away (proximity term {proximity:.2f}, weight {weights['distance']:.2f})",
            f"capability {capability:.2f} (weight {weights['capability']:.2f}): " + "; ".join(cap_reasons),
            (
                f"cashless network: empanelled with {insurer}"
                if in_network and insurer
                else "cashless network: empanelled"
                if in_network
                else f"cashless network: NOT empanelled with {insurer or 'the policy insurer'} — "
                     "admission here likely means reimbursement, not cashless"
            ),
        ]
        if not hospital.has_emergency:
            reasons.append("no emergency department")

        scores.append(
            HospitalScore(
                hospital=hospital,
                distance_km=round(distance, 2),
                capability_score=round(capability, 3),
                network_match=in_network,
                total_score=round(total, 4),
                reasons=reasons,
            )
        )

    return sorted(scores, key=lambda s: (-s.total_score, s.distance_km))


def route(report: SymptomReport, profile: ParentProfile, case_id: str | None = None) -> TriageDecision:
    """Full triage pass: classify, rank, and record why."""
    severity = classify_severity(
        report.symptoms, report.free_text, profile.chronic_conditions
    )

    lat = report.lat if report.lat is not None else profile.lat
    lon = report.lon if report.lon is not None else profile.lon
    insurer = profile.policy.insurer if profile.policy else None
    network = profile.policy.network_hospitals if profile.policy else []

    ranked = rank_hospitals(
        lat=lat,
        lon=lon,
        severity=severity.severity,
        specialties=severity.specialties,
        insurer=insurer,
        network_hospitals=network,
    )

    best = ranked[0] if ranked else None
    nearest = min(ranked, key=lambda s: s.distance_km) if ranked else None

    return TriageDecision(
        case_id=case_id or f"case-{uuid.uuid4().hex[:10]}",
        parent_id=profile.parent_id,
        severity=severity.severity,
        severity_rationale=severity.rationale,
        matched_specialties=severity.specialties,
        ranked_hospitals=ranked,
        recommended_hospital_id=best.hospital.hospital_id if best else None,
        explanation=_explain(severity.severity, best, nearest, insurer),
    )


def _explain(
    severity: Severity,
    best: HospitalScore | None,
    nearest: HospitalScore | None,
    insurer: str | None,
) -> str:
    """A plain-language account of the trade-off actually made.

    Only the terms that genuinely differ are cited. Listing a tied term as a
    reason is how an explanation stops being one.
    """
    if best is None:
        return "No hospital in the knowledge base could be scored for this case."

    lines = [
        (
            f"Severity {severity.value}. Recommending {best.hospital.name} "
            f"({best.distance_km:.1f} km, score {best.total_score:.3f})."
        )
    ]

    if nearest is None or nearest.hospital.hospital_id == best.hospital.hospital_id:
        lines.append("This is also the nearest option, so no travel-time trade-off was needed.")
    else:
        extra = best.distance_km - nearest.distance_km
        deltas: list[str] = []
        if best.capability_score > nearest.capability_score + 1e-9:
            deltas.append(
                f"capability scored {best.capability_score:.2f} there versus "
                f"{nearest.capability_score:.2f} at {nearest.hospital.name}"
            )
        if best.network_match and not nearest.network_match:
            # "listed as", not "is". These are real, named hospitals and this
            # sentence is about whether a real insurer pays at them, from
            # network data that is seeded and unverified. Reporting what the
            # record lists is true; asserting the empanelment as fact is not.
            # The provenance itself lives in the KB _meta and /api/hospitals.
            deltas.append(
                f"{best.hospital.name} is listed as empanelled with "
                f"{insurer or 'the policy insurer'} and {nearest.hospital.name} is not, "
                f"so this keeps the admission cashless"
            )

        if deltas:
            lines.append(
                f"That is {extra:.1f} km farther than the nearest option "
                f"({nearest.hospital.name}, {nearest.distance_km:.1f} km). "
                f"The extra distance was accepted because " + "; and ".join(deltas) + "."
            )
        else:
            lines.append(
                f"That is {extra:.1f} km farther than {nearest.hospital.name}, which "
                f"scored equally on capability and network — the two are close, and the "
                f"margin ({best.total_score - nearest.total_score:.3f}) is small enough "
                f"that either is defensible."
            )

    if not best.network_match:
        lines.append(
            f"Note: {best.hospital.name} is not listed as empanelled with "
            f"{insurer or 'the policy insurer'} — expect a reimbursement claim rather "
            "than cashless pre-auth."
        )

    return " ".join(lines)
