"""Taking a photographed document into the record, without taking it on trust.

Same discipline as the bill lane, and for the same reason: the image is the
record and everything downstream is a reading of it.

  1. The image is stored PRIVATELY first. No image, no ingestion — a fact that
     cannot be checked against the paper it came from is not worth having.
  2. A model reads it. That reading may be wrong.
  3. A receipt carries HASHES, never the content. A discharge summary names a
     diagnosis and a lab report carries results; both are exactly what the
     comms gate refuses to put on WhatsApp, so neither goes on a public chain.
  4. The extraction is saved credentialed, next to the photograph.

What differs from bills is where each kind LANDS, and the care that needs.

A prescription updates the medication list a clinician reads in an emergency.
A policy schedule updates the numbers the coverage estimate is computed from.
Both overwrite something a person relied on, so both refuse to overwrite with
less than they found: a reading that produced no medications does not empty the
medication list, and a policy reading missing a sum insured does not zero the
cover. A model that half-read a photograph must not be able to delete a record.
"""

from __future__ import annotations

import hashlib
import json

from anbu_care import service
from anbu_care.docvision import read as vision
from anbu_care.schemas import (
    DocumentKind,
    InsurancePolicy,
    Medication,
    Observation,
    ParsedDocument,
)


class DocumentRejected(Exception):
    """Not taken in, and the reason is safe to show the sender."""


_KIND_TO_DOCUMENT = {
    "discharge_summary": DocumentKind.DISCHARGE_SUMMARY,
    "lab_report": DocumentKind.BLOOD_REPORT,
    "prescription": DocumentKind.PRESCRIPTION,
    "policy_schedule": DocumentKind.POLICY,
}


def _content_sha256(payload: dict) -> str:
    """Hash of the reading. Canonical, so the same reading always hashes alike."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _summary_for(kind: str, payload: dict) -> str:
    """One line describing the document, for the record listing.

    Deliberately factual. It repeats what the document said and never
    characterises it.
    """
    if kind == "discharge_summary":
        span = " to ".join(x for x in (payload.get("admitted_on"),
                                       payload.get("discharged_on")) if x)
        return (f"Discharge summary{f' ({span})' if span else ''}. "
                f"{payload.get('diagnosis') or 'No diagnosis text was read.'}")[:400]
    if kind == "lab_report":
        obs = payload.get("observations") or []
        flagged = [o.get("name") for o in obs if o.get("flag") in {"high", "low", "abnormal"}]
        tail = f" Flagged: {', '.join(str(f) for f in flagged if f)}." if flagged else ""
        return f"Lab report, {len(obs)} result(s).{tail}"[:400]
    if kind == "prescription":
        meds = payload.get("medications") or []
        return f"Prescription, {len(meds)} medication(s)."
    if kind == "policy_schedule":
        return (f"Policy schedule, {payload.get('insurer') or 'insurer not read'}, "
                f"sum insured INR {payload.get('sum_insured_inr') or 'not read'}.")
    return "Document."


def _observations(payload: dict) -> list[Observation]:
    out: list[Observation] = []
    for entry in payload.get("observations") or []:
        if not isinstance(entry, dict):
            continue
        name, value = entry.get("name"), entry.get("value")
        if not name or value in (None, ""):
            continue
        flag = entry.get("flag")
        out.append(Observation(
            name=str(name).strip(), value=value,
            unit=(str(entry["unit"]).strip() if entry.get("unit") else None),
            reference_range=(str(entry["reference_range"]).strip()
                             if entry.get("reference_range") else None),
            flag=(str(flag).strip().lower() if flag else None),
            observed_on=payload.get("collected_on"),
        ))
    return out


def _medications(entries: list) -> list[Medication]:
    out: list[Medication] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        out.append(Medication(
            name=str(entry["name"]).strip(),
            dose=(str(entry["dose"]).strip() if entry.get("dose") else None),
            frequency=(str(entry["frequency"]).strip() if entry.get("frequency") else None),
        ))
    return out


def _apply_prescription(parent_id: str, payload: dict) -> str:
    """Replace the medication list, but never with an empty one.

    A photograph read badly must not be able to delete the list a clinician
    reads in an emergency. Fewer medications than are on file is a plausible
    reading of a real prescription, so it replaces; none at all is a failed
    reading, so it does not.
    """
    meds = _medications(payload.get("medications"))
    if not meds:
        return "no medication could be read, so the existing list is unchanged"

    profile = service.load_profile(parent_id)
    if profile is None:
        return "no parent record to update"
    before = len(profile.medications)
    profile.medications = meds
    service.save_profile(profile)
    return f"medication list updated: {before} on file, {len(meds)} read from this prescription"


def _apply_policy(parent_id: str, payload: dict) -> str:
    """Update the policy, refusing to lose cover to a partial reading."""
    profile = service.load_profile(parent_id)
    if profile is None:
        return "no parent record to update"

    sum_insured = payload.get("sum_insured_inr")
    if not isinstance(sum_insured, int) or sum_insured <= 0:
        return ("no sum insured could be read, so the policy on file is unchanged")

    existing = profile.policy
    policy = InsurancePolicy(
        insurer=str(payload.get("insurer") or (existing.insurer if existing else "not read")),
        policy_number=str(payload.get("policy_number")
                          or (existing.policy_number if existing else "not read")),
        sum_insured_inr=sum_insured,
        cashless_eligible=existing.cashless_eligible if existing else True,
        network_hospitals=[str(h).strip() for h in (payload.get("network_hospitals") or [])
                           if str(h).strip()] or (existing.network_hospitals if existing else []),
        sub_limits_inr=dict(existing.sub_limits_inr) if existing else {},
        valid_until=payload.get("valid_until") or (existing.valid_until if existing else None),
    )

    # Percentages the schedule states are stored as rupee-per-day sub-limits, so
    # the coverage rules read one shape regardless of how a policy expressed it.
    for key, pct_field, inr_field in (
        ("room_rent", "room_rent_percent_per_day", "room_rent_inr_per_day"),
        ("icu", "icu_percent_per_day", "icu_inr_per_day"),
    ):
        rupees = payload.get(inr_field)
        pct = payload.get(pct_field)
        if isinstance(rupees, int) and rupees > 0:
            policy.sub_limits_inr[f"{key}_per_day"] = rupees
        elif isinstance(pct, (int, float)) and pct > 0:
            policy.sub_limits_inr[f"{key}_per_day"] = int(round(sum_insured * pct / 100))

    copay = payload.get("copay_percent")
    if isinstance(copay, (int, float)) and 0 <= copay <= 100:
        policy.copay_percent = int(copay)
    elif existing is not None:
        policy.copay_percent = existing.copay_percent

    stated = payload.get("proportionate_deduction")
    policy.proportionate_deduction = (
        bool(stated) if stated is not None
        else (existing.proportionate_deduction if existing else True))

    profile.policy = policy
    service.save_profile(profile)
    return (f"policy updated: sum insured INR {sum_insured:,}, "
            f"sub-limits {policy.sub_limits_inr or 'none read'}, "
            f"co-pay {policy.copay_percent}%")


def ingest_document_image(parent_id: str, image: bytes, mime_type: str = "image/jpeg",
                          case_id: str = "") -> dict:
    """Store the photograph, read it, record it, and apply it where it belongs.

    Raises DocumentRejected when it cannot be read or stored. Nothing is written
    in that case: a document nobody could read is not a document on file.
    """
    if service.load_profile(parent_id) is None:
        raise DocumentRejected("no parent record for that number")

    digest = vision.image_sha256(image)

    from anbu_care.comms import storage

    doc_id = service.new_id("doc")
    stored = storage.store(f"documents/{parent_id}/{doc_id}.{mime_type.split('/')[-1]}",
                           image, content_type=mime_type)
    if not stored.stored or not stored.object_name:
        raise DocumentRejected(
            f"the photograph could not be stored, so nothing was recorded "
            f"({stored.detail}). A fact that cannot be checked against the paper "
            f"is not worth keeping."
        )

    reading = vision.read(image, mime_type)
    if not reading.ok:
        raise DocumentRejected(
            f"{reading.detail}. The photograph is kept; send a clearer one."
        )
    if reading.is_bill:
        raise DocumentRejected(
            "that is a hospital bill. Send it again and it will be read as one."
        )

    # The same photograph twice is one document, for the same reason a bill
    # sent twice is one bill: a retry must not duplicate the record.
    for existing in service.list_documents(parent_id):
        if (existing.delta_vs_baseline or "").endswith(digest[:16]):
            raise DocumentRejected(
                f"that is the same photograph as {existing.document_id}, which is "
                f"already on the record. It has not been added again.")

    kind = _KIND_TO_DOCUMENT[reading.kind]
    doc = ParsedDocument(
        document_id=doc_id, parent_id=parent_id, kind=kind,
        source_filename=stored.object_name,
        observations=_observations(reading.payload) if reading.kind == "lab_report" else [],
        summary=_summary_for(reading.kind, reading.payload),
        # The image hash rides here so a duplicate can be spotted without a
        # schema change; the prefix is enough to identify and too short to be
        # mistaken for the full digest.
        delta_vs_baseline=f"read from a photograph, image {digest[:16]}",
    )
    service.save_document(doc)

    applied = ""
    if reading.kind == "prescription":
        applied = _apply_prescription(parent_id, reading.payload)
    elif reading.kind == "policy_schedule":
        applied = _apply_policy(parent_id, reading.payload)

    if case_id and service.load_case(case_id) is not None:
        service.append_receipt(
            case_id,
            kind="document.ingested",
            actor="document_capture",
            payload={
                "document_id": doc_id,
                "document_kind": kind.value,
                "content_sha256": _content_sha256(reading.payload),
                "image_sha256": digest,
                "observation_count": len(doc.observations),
                "engine": reading.engine,
                "applied": applied,
                "note": (
                    "A document was photographed and read. Its CONTENTS are not "
                    "on this chain — only hashes — so this can be proved "
                    "unaltered without publishing a diagnosis or a lab value. "
                    "The reading is a model's, and the image is kept so it can "
                    "be checked."
                ),
            },
        )

    return {
        "document_id": doc_id, "kind": reading.kind, "summary": doc.summary,
        "observations": len(doc.observations), "applied": applied,
        "payload": reading.payload, "image_object": stored.object_name,
    }
