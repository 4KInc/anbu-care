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
import logging

from anbu_care import service
from anbu_care.docvision import read as vision
from anbu_care.schemas import (
    DocumentKind,
    InsurancePolicy,
    Medication,
    Observation,
    ParsedDocument,
)

logger = logging.getLogger(__name__)


class DocumentRejected(Exception):
    """Not taken in, and the reason is safe to show the sender.

    `already_recorded` separates the two things a refusal can mean, because
    they read completely differently to whoever sent the photograph. "I could
    not read this" asks them to do something. "This is already on file" tells
    them the job is done. Collapsing both into one unreadable-message shipped,
    and a family who had successfully sent a lab report was told their bill
    could not be read and to send a clearer one.

    `subject` is what to call it in that message. Never "bill" unless it is one.
    """

    def __init__(self, message: str, *, already_recorded: bool = False,
                 subject: str = "document") -> None:
        super().__init__(message)
        self.already_recorded = already_recorded
        self.subject = subject


_DOCUMENT_TO_LABEL = {
    DocumentKind.DISCHARGE_SUMMARY: "discharge summary",
    DocumentKind.BLOOD_REPORT: "lab report",
    DocumentKind.PRESCRIPTION: "prescription",
    DocumentKind.POLICY: "policy schedule",
}


def label_for(kind: DocumentKind) -> str:
    """What to call a document kind in a message to a family."""
    return _DOCUMENT_TO_LABEL.get(kind, str(getattr(kind, "value", kind)).replace("_", " "))


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


def _plural(count: int, noun: str) -> str:
    """"6 medication(s)" is a form field. A record reads as a sentence."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


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
        return f"Lab report, {_plural(len(obs), 'result')}.{tail}"[:400]
    if kind == "prescription":
        meds = payload.get("medications") or []
        return f"Prescription, {_plural(len(meds), 'medication')}."
    if kind == "policy_schedule":
        return (f"Policy schedule, {payload.get('insurer') or 'insurer not read'}, "
                f"sum insured INR {payload.get('sum_insured_inr') or 'not read'}.")
    return "Document."


def message_summary_for(kind: str, payload: dict) -> str:
    """A summary safe to put in a WhatsApp message. Counts, never findings.

    The record summary names which analytes were flagged, because a family
    reading the record behind a credential should see that. Putting the same
    sentence in a message got it BLOCKED as clinical, correctly — "Flagged:
    Troponin I, CK-MB" is exactly the content the gate exists to stop, and the
    family got silence instead of a notification.

    So the message says how many results fell outside the reference range and
    where to read them. The count is the news; the names are the record.
    """
    if kind == "lab_report":
        obs = payload.get("observations") or []
        flagged = sum(1 for o in obs
                      if str(o.get("flag") or "").lower() in {"high", "low", "abnormal"})
        if flagged:
            return (f"{_plural(len(obs), 'result')} recorded, {flagged} outside the "
                    f"reference range.")
        return f"{_plural(len(obs), 'result')} recorded."
    if kind == "discharge_summary":
        span = " to ".join(x for x in (payload.get("admitted_on"),
                                       payload.get("discharged_on")) if x)
        meds = len(payload.get("discharge_medications") or [])
        return (f"Admission {span}." if span else "Discharge summary recorded.") + (
            f" {_plural(meds, 'medication')} listed on discharge." if meds else "")
    if kind == "prescription":
        return f"{_plural(len(payload.get('medications') or []), 'medication')} recorded."
    if kind == "policy_schedule":
        # Money and cover, not clinical detail, so this one can say the figures.
        sum_insured = payload.get("sum_insured_inr")
        bits = [f"Sum insured INR {sum_insured:,}."] if isinstance(sum_insured, int) else []
        if payload.get("copay_percent"):
            bits.append(f"Co-pay {payload['copay_percent']}%.")
        return " ".join(bits) or "Policy schedule recorded."
    return "Recorded."


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


def _apply_discharge_summary(parent_id: str, payload: dict) -> str:
    """Take the discharge summary into the record it describes.

    This document is the one that closes an episode, and its contents were
    being read and then dropped. The dates drive per-day sub-limits, the
    diagnosis is what the adjudicator prices against, and the discharge
    medication is what the parent actually goes home on.

    Allergies MERGE and never remove. A discharge summary lists what that
    admission recorded; a shorter list is not a retraction of an allergy
    somebody has been carrying for years, and dropping one on that reading
    could kill someone.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return "no parent record to update"

    changed: list[str] = []

    meds = _medications(payload.get("discharge_medications"))
    if meds:
        before = len(profile.medications)
        profile.medications = meds
        changed.append(f"medication list updated: {before} on file, "
                       f"{len(meds)} on discharge")

    read = [str(a).strip() for a in (payload.get("allergies") or []) if str(a).strip()]
    known = {a.lower() for a in profile.allergies}
    added = [a for a in read if a.lower() not in known]
    if added:
        profile.allergies = list(profile.allergies) + added
        changed.append(f"allergies added: {', '.join(added)}")

    if changed:
        service.save_profile(profile)

    discharged = str(payload.get("discharged_on") or "").strip()
    if discharged:
        changed.append(f"discharged on {discharged}")
    return "; ".join(changed) or "nothing on file needed changing"


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
            policy.sub_limits_inr[f"{key}_per_day"] = round(sum_insured * pct / 100)

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

    # The same photograph twice is one document, for the same reason a bill sent
    # twice is one bill: a retry must not duplicate the record. Checked FIRST,
    # before anything is stored or read, so a duplicate costs neither a stored
    # object nor a model call — and so the message can name what is already on
    # file rather than describing a reading that never needed to happen.
    for existing in service.list_documents(parent_id):
        if (existing.delta_vs_baseline or "").endswith(digest[:16]):
            existing_label = label_for(existing.kind)
            raise DocumentRejected(
                f"that {existing_label} is already on the record as "
                f"{existing.document_id}. It is the same photograph, so nothing "
                f"was added twice.",
                already_recorded=True, subject=existing_label)

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
            "that is a hospital bill. Send it again and it will be read as one.",
            subject="hospital bill")

    kind = _KIND_TO_DOCUMENT[reading.kind]
    doc = ParsedDocument(
        document_id=doc_id, parent_id=parent_id, kind=kind,
        source_filename=stored.object_name,
        observations=_observations(reading.payload) if reading.kind == "lab_report" else [],
        summary=_summary_for(reading.kind, reading.payload),
        # The rest of the page. A prescription's doses, a discharge summary's
        # diagnosis and follow-up date, a schedule's limits — read off the
        # paper and then dropped, because a one-line summary was the only
        # thing kept. Stored as read, never interpreted here.
        details=dict(reading.payload or {}),
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
    elif reading.kind == "discharge_summary":
        applied = _apply_discharge_summary(parent_id, reading.payload)

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

    # A discharge summary is the one document that says the emergency is over
    # and she has gone home. That is the fact recovery check-ins start from —
    # not a decision anybody here made about her, just a piece of paper she was
    # handed. The date comes off the paper if the reader could read one; if not
    # the window counts from now and the receipt says which.
    recovery_window = None
    claim_filed = None
    if reading.kind == "discharge_summary":
        recovery_window = _open_recovery_window(
            parent_id, case_id, reading.payload, doc_id)
        # The same piece of paper that says she has gone home is the one that
        # makes a reimbursement claim possible. It was the last lane here still
        # waiting to be asked.
        claim_filed = _file_the_claim(case_id, parent_id, reading.payload, doc_id)

    # A lab report is the other document that closes something. The booking
    # lane submits a request and then has no way of ever learning she went, so
    # a case can sit at "the centre has not answered" long after the blood was
    # drawn. The report is the family telling us, without being asked to.
    closed_test = _close_the_ordered_test(case_id, doc_id, reading.kind,
                                          reading.payload)

    return {
        "document_id": doc_id, "kind": reading.kind, "summary": doc.summary,
        # Two summaries on purpose: one for the record, one that can survive the
        # comms gate. Conflating them is what got the first message blocked.
        "message_summary": message_summary_for(reading.kind, reading.payload),
        "observations": len(doc.observations), "applied": applied,
        "payload": reading.payload, "image_object": stored.object_name,
        "recovery_window": recovery_window,
        "closed_test": closed_test,
        "claim_filed": claim_filed,
    }


def _file_the_claim(case_id: str, parent_id: str, payload: dict,
                    doc_id: str) -> dict | None:
    """Start the reimbursement claim off the discharge summary.

    Never raises into an ingest, for the same reason the recovery window does
    not: the photograph is stored and read, the family has been told it
    arrived, and a claim-lane failure must not turn a document safely on the
    record into one that was rejected.
    """
    from anbu_care.tpa import on_discharge

    try:
        filing = on_discharge.file_on_discharge(
            case_id=case_id, parent_id=parent_id, payload=payload,
            document_id=doc_id)
    except Exception:  # the document stays on the record whatever this does
        logger.exception("could not file a claim from %s", doc_id)
        return None
    if filing.outcome in {"no_case", "already_filed"}:
        return None
    return {"outcome": filing.outcome, "detail": filing.detail,
            "packet_id": filing.packet_id,
            "submission_id": filing.submission_id,
            "total_claimed_inr": filing.total_claimed_inr,
            "sla_deadline": filing.sla_deadline,
            "claim_form": filing.form_object}


def _close_the_ordered_test(case_id: str, doc_id: str, kind: str,
                            payload: dict) -> dict | None:
    """Let an arriving result close the test that was waiting for it.

    Never raises into an ingest, for the same reason `_open_recovery_window`
    does not: the photograph is already stored and read, the family has already
    been told it arrived, and a booking-lane failure must not turn a document
    that is safely on the record into one that was rejected.

    Returns None when there was nothing to do, which is the ordinary case for
    every document that is not a lab report.
    """
    from anbu_care.booking import result as booking_result

    if kind != booking_result.CLOSES_A_TEST:
        return None
    try:
        closure = booking_result.close_from_document(
            case_id=case_id, document_id=doc_id, kind=kind, payload=payload)
    except Exception:  # the document stays on the record whatever this does
        logger.exception("could not close an ordered test from %s", doc_id)
        return None
    if closure.outcome in {"not_a_result", "no_case", "nothing_open"}:
        return None
    return {"outcome": closure.outcome, "detail": closure.detail,
            "appointment_id": closure.appointment_id,
            "order_id": closure.order_id}


def _open_recovery_window(parent_id: str, case_id: str, payload: dict,
                          doc_id: str) -> str | None:
    """Start the fortnight of check-ins. Never raises into an ingest.

    A failure here must not lose the document. The discharge summary is on the
    record either way; what is at stake is whether anybody asks her how she is
    afterwards, and that is worth a log line rather than a lost ingest.
    """
    from anbu_care.recovery import window as recovery

    try:
        opened = recovery.open_window(
            parent_id, case_id,
            discharged_on=payload.get("discharged_on"),
            document_id=doc_id,
        )
    except Exception:
        logger.exception("could not open a recovery window for %s", parent_id)
        return None
    if opened is None:
        return None

    # SEND WHAT IS OWED AS SOON AS IT IS OWED, rather than at the next poll.
    #
    # Nothing in this service holds a timer, so a scheduler calls the tick from
    # outside and the first check-in used to wait for whenever that next fired.
    # She came home today; a question that arrives up to a polling interval
    # late for no reason is a worse answer than one that arrives now.
    #
    # This adds no permission and skips no guard. It is the same send_due the
    # scheduler calls, so consent is read live off her profile, nothing goes
    # before nine in the morning where she is, and the day slot is claimed
    # exactly once - which is also what stops the next scheduled tick sending a
    # second copy of this one.
    #
    # It is deliberately inside the same never-raises block. A check-in that
    # could not be sent must not cost her the recovery window it belongs to.
    try:
        from anbu_care.recovery import checkin

        checkin.send_due(parent_id)
    except Exception:
        logger.exception("the first recovery check-in was not sent for %s", parent_id)

    return opened.window_id
