"""Bill capture: a photograph becomes line items, and stays checkable.

The image is the record. Everything downstream is a reading of it.

Note the function is exported as `extract_bill`, not `extract`: a re-export
named `extract` shadows the `anbu_care.bills.extract` MODULE, so
`from anbu_care.bills import extract` silently hands you a function where a
caller reasonably expects a module. Cheap to avoid, confusing to debug.
"""

from anbu_care.bills.coverage import estimate_for_case
from anbu_care.bills.extract import extract as extract_bill
from anbu_care.bills.extract import image_sha256
from anbu_care.bills.ingest import (
    BillRejected,
    ingest_bill_image,
    list_bills,
    reading_sha256,
)

__all__ = [
    "BillRejected",
    "estimate_for_case",
    "extract_bill",
    "image_sha256",
    "ingest_bill_image",
    "list_bills",
    "reading_sha256",
]
