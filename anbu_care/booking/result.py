"""What an arriving lab report is allowed to say about an ordered test.

The booking lane used to stop at the form. A test was ordered, a centre was
found, a request was submitted, the family was told - and then nothing. Nobody
moved `requested` to anything else, so a case sat forever claiming the centre
had not answered, weeks after she had been, had the blood drawn and had the
result in her hand. The record went stale in the one direction that matters:
it under-reported care that actually happened.

The fix does not need a new source of truth, because one already arrives. A
lab report is photographed into this system by the family in the ordinary way,
and a lab report for her can only exist if somebody drew her blood. That is
weaker evidence than a centre confirming, and it is stronger than the silence
it replaces, so it gets its own status rather than borrowing one:

  resulted  a report arrived for her while exactly one ordered test was
            outstanding. The test happened. This is NOT the centre confirming a
            slot, and it is NOT proof that this report is that order's result.

WHAT THIS REFUSES TO DO. It will not guess. Two tests outstanding and one
report arriving is a question this system cannot answer - the report would have
to be read for which test it is, and reading it for that would let a model
decide which clinical order got closed. So two open orders means nothing is
closed and the receipt says why, which leaves a person with an accurate record
and an obvious next step. One outstanding order is the case where there is
nothing to guess about, and it is the only case this acts on.

A report that predates the order is refused for the same reason: an old result
photographed for the insurer is not evidence of a visit that had not been
arranged yet. The date comes off the paper where the reader could read one, and
where it could not the receipt says the arrival time was used instead.

NOTHING HERE IS CLINICAL. No value is read, no analyte is compared, no reading
is interpreted. The only facts consulted are that a document of this kind
arrived, when it was collected, and how many orders were open.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from anbu_care import service

log = logging.getLogger(__name__)

RESULTED = "resulted"

# The one document kind that can close a test. A prescription or a bill can
# arrive for the same admission without anybody having attended anything.
#
# This is the READER's word, not the stored one. The vision reader emits
# "lab_report" and the record stores it as DocumentKind.BLOOD_REPORT, and the
# hook that calls this sits on the reader's side of that mapping. Spelling it
# "blood_report" here is silent: nothing raises, the guard simply never matches
# and the loop never closes anything.
CLOSES_A_TEST = "lab_report"

# Statuses an arriving report may close. `confirmed` is deliberately included:
# a centre confirming a slot and her actually going are different facts, and
# the second one supersedes the first.
OPEN = frozenset({"requested", "confirmed"})


@dataclass(frozen=True)
class Closure:
    """What the arriving report did, and what stopped it where it did nothing."""

    outcome: str
    detail: str = ""
    appointment_id: str = ""
    order_id: str = ""
    guards_passed: list[str] = field(default_factory=list)
    receipt_id: str = ""


def _collected_on(payload: dict) -> date | None:
    """The date on the paper, where the reader could read one."""
    raw = (payload or {}).get("collected_on")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def close_from_document(*, case_id: str, document_id: str, kind: str,
                        payload: dict | None = None,
                        now: datetime | None = None) -> Closure:
    """Close the one outstanding test a newly arrived report can only belong to.

    Never raises. This runs inside a document ingest, and a document that was
    read and stored must stay read and stored whatever this concludes.
    """
    now = now or datetime.now(UTC)
    passed: list[str] = []

    if kind != CLOSES_A_TEST:
        return Closure(outcome="not_a_result",
                       detail=f"a {kind} says nothing about whether she attended")
    passed.append("document_kind")

    if not case_id or service.load_case(case_id) is None:
        return Closure(outcome="no_case", guards_passed=passed,
                       detail="the report is on her record but not on an admission, "
                              "so there is no ordered test for it to close")
    passed.append("case_known")

    open_appointments = [a for a in service.list_appointments(case_id)
                         if a.status in OPEN and a.cancelled_at is None]
    if not open_appointments:
        return Closure(outcome="nothing_open", guards_passed=passed,
                       detail="no test was outstanding on this admission")

    # SIX — one outstanding order, or nothing.
    #
    # The whole safety of this lane is here. With one open order there is
    # nothing to attribute: whatever the report is, it is the only test this
    # system is waiting on. With two, closing either one means deciding which,
    # and the only way to decide is to read the report and match it against a
    # clinician's words. That is a model choosing which medical order to mark
    # done, which is exactly the class of decision this codebase keeps out of a
    # model's hands. So it stops, and says so where somebody will see it.
    if len(open_appointments) > 1:
        detail = (f"{len(open_appointments)} tests are outstanding on this "
                  "admission, so which one this report belongs to is not "
                  "something this system can establish. Nothing was closed.")
        return Closure(
            outcome="ambiguous", guards_passed=passed, detail=detail,
            receipt_id=_receipt(case_id, kind="booking.result_not_attributed",
                                payload={
                                    "document_id": document_id,
                                    "open_appointments": [a.appointment_id for a
                                                          in open_appointments],
                                    "note": detail + " A person can attribute it; "
                                            "this system will not guess, because "
                                            "guessing here means deciding which "
                                            "clinical order was carried out.",
                                }))
    passed.append("single_open_order")

    appointment = open_appointments[0]

    # SEVEN — a result cannot predate the order that asked for it.
    collected = _collected_on(payload or {})
    requested_on = appointment.requested_at.date()
    if collected is not None and collected < requested_on:
        detail = (f"the report was collected on {collected.isoformat()}, before "
                  f"this test was ordered on {requested_on.isoformat()}, so it is "
                  "an earlier result and not evidence of this visit")
        return Closure(outcome="predates_the_order", guards_passed=passed,
                       detail=detail, appointment_id=appointment.appointment_id,
                       order_id=appointment.order_id)
    passed.append("not_older_than_the_order")

    appointment.status = RESULTED
    appointment.resulted_at = now
    appointment.resulted_by_document = document_id
    service.save_appointment(appointment)

    dated = (f"The report is dated {collected.isoformat()}."
             if collected is not None else
             "The report carried no date the reader could make out, so the time "
             "it arrived was used instead.")

    receipt_id = _receipt(
        case_id, kind="booking.resulted",
        payload={
            "appointment_id": appointment.appointment_id,
            "order_id": appointment.order_id,
            "document_id": document_id,
            "from_status": "requested" if appointment.status else "",
            "place_id": appointment.place_id,
            "collected_on": collected.isoformat() if collected else "",
            "guards_passed": passed,
            "note": (
                "A lab report arrived while exactly one ordered test was "
                "outstanding, so that test is recorded as carried out. " + dated +
                " This is not the centre confirming an appointment and it is not "
                "proof that this report is that test's result: it is evidence "
                "that she was seen, which is the fact the record was missing. No "
                "value in the report was read to decide this."
            ),
        })

    return Closure(outcome="closed", guards_passed=passed, receipt_id=receipt_id,
                   appointment_id=appointment.appointment_id,
                   order_id=appointment.order_id,
                   detail=f"{appointment.order_id} is recorded as carried out")


def _receipt(case_id: str, *, kind: str, payload: dict) -> str:
    """Append, or carry on without one. A receipt that failed is not a reason
    to lose a document the family already sent."""
    try:
        written = service.append_receipt(case_id, kind=kind, actor="document_capture",
                                         payload=payload)
        return getattr(written, "receipt_id", "") or ""
    except Exception as e:  # noqa: BLE001 - the ingest must survive this
        log.warning("could not receipt %s on %s: %s", kind, case_id, e)
        return ""
