"""Taking a photographed bill into the record, without taking it on trust.

Four things happen, in this order, and the order is the design:

1. The image is stored **privately**. Not public, not on the chain, not in a
   log. It is the evidence, and it is the only thing here that is not a
   derivation.
2. A model reads it. That reading may be wrong.
3. A receipt is written carrying a **hash** of the reading and a hash of the
   image — never the amounts, never the vendor, never the object URL. Public
   `/verify` can then prove a bill was recorded and has not been altered while
   revealing nothing about what anyone paid or where they were treated.
4. The extraction is saved credentialed, so a family can open the photograph
   next to the numbers and check them.

Step 3 is the one worth dwelling on. A bill is simultaneously financial and
clinical: the line items name the procedures. `pharmacy INR 34,500` beside
`cardiac_icu_room` says a great deal about what happened to someone. So the
same discipline the wellbeing lane uses for her words applies to her bills —
the chain proves the record exists and is unaltered, and reading it requires a
credential.

Nothing in this module decides what is payable. It records what was billed.
"""

from __future__ import annotations

import hashlib
import json

from anbu_care import service
from anbu_care.bills import extract as vision
from anbu_care.provenance.store import get_store
from anbu_care.schemas import BillLineItem, ExtractedBill


class BillRejected(Exception):
    """The bill was not taken in, and the reason is safe to show the sender."""


def _reading_sha256(bill: ExtractedBill) -> str:
    """Hash of the extracted reading — the thing that must not silently change.

    Canonical: sorted keys, no incidental whitespace, so the same reading
    always hashes the same way. Covers the numbers and their labels, which is
    exactly what a later edit would want to alter.
    """
    canonical = json.dumps(
        {
            "line_items": [
                {"item": line.item, "label": line.label, "amount_inr": line.amount_inr}
                for line in bill.line_items
            ],
            "stated_total_inr": bill.stated_total_inr,
            "currency": bill.currency,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ingest_bill_image(case_id: str, parent_id: str, image: bytes,
                      mime_type: str = "image/jpeg") -> ExtractedBill:
    """Store the photograph, read it, and record that it was read.

    Raises BillRejected when the image could not be read. Nothing is written to
    the record in that case: a bill nobody could read is not a bill on file,
    and pretending otherwise would put a phantom in the running total.
    """
    if service.load_case(case_id) is None:
        raise BillRejected("no such case")

    digest = vision.image_sha256(image)

    # The image goes to private storage FIRST. If the reading turns out wrong,
    # the evidence still exists to correct it against — and storing after a
    # successful read would mean an unreadable bill left no trace of having
    # been sent at all.
    from anbu_care.comms import storage

    bill_id = service.new_id("bill")
    stored = storage.store(
        f"bills/{parent_id}/{bill_id}.{mime_type.split('/')[-1]}",
        image, content_type=mime_type,
    )
    if not stored.stored or not stored.object_name:
        # No image, no ingestion. The entire premise of this feature is that a
        # number can be checked against the paper it was read from, so a bill
        # recorded without its photograph is a set of unverifiable figures
        # wearing the authority of a record. Refuse rather than degrade.
        raise BillRejected(
            f"the photograph could not be stored, so the bill was not recorded "
            f"({stored.detail}). Amounts that cannot be checked against the "
            f"image are not worth having."
        )

    reading = vision.extract(image, mime_type)
    if not reading.ok:
        raise BillRejected(
            f"{reading.detail}. The photograph is kept; send a clearer one, or "
            "enter the amounts by hand."
        )

    bill = ExtractedBill(
        bill_id=bill_id,
        case_id=case_id,
        parent_id=parent_id,
        line_items=[BillLineItem(**line) for line in reading.line_items],
        stated_total_inr=reading.stated_total_inr,
        vendor=reading.vendor,
        bill_date=reading.bill_date,
        image_object=stored.object_name,
        image_sha256=digest,
        engine=reading.engine,
        needs_review=reading.needs_review,
        review_reason=reading.review_reason,
    )

    save_bill(bill)

    # Hashes and counts only. No amount, no vendor, no object name — a public
    # verifier learns that a bill was recorded and has not been altered, and
    # nothing whatsoever about what it said.
    service.append_receipt(
        case_id,
        kind="bill.ingested",
        actor="bill_capture",
        payload={
            "bill_id": bill.bill_id,
            "reading_sha256": _reading_sha256(bill),
            "image_sha256": bill.image_sha256,
            "line_count": len(bill.line_items),
            "engine": bill.engine,
            "needs_review": bill.needs_review,
            "note": (
                "A bill was photographed and read. The amounts are NOT on this "
                "chain — only a hash of what was read, so this can be proved "
                "unaltered without revealing what anyone paid. The reading is "
                "a model's, and the image is kept so it can be checked."
            ),
        },
    )
    return bill


def save_bill(bill: ExtractedBill) -> None:
    get_store().put(
        f"CASE#{bill.case_id}", f"BILL#{bill.bill_id}", bill.model_dump(mode="json"))


def list_bills(case_id: str) -> list[ExtractedBill]:
    rows = get_store().query_prefix(f"CASE#{case_id}", "BILL#")
    bills = [ExtractedBill.model_validate(row) for row in rows]
    return sorted(bills, key=lambda b: b.extracted_at)


def reading_sha256(bill: ExtractedBill) -> str:
    """Exposed so a verifier can recompute what the receipt claims."""
    return _reading_sha256(bill)
