"""Re-read documents stored before `details` existed, from the photographs kept.

`ParsedDocument` gained a `details` field so a prescription's medications and a
discharge summary's diagnosis and dates would stop being read and discarded.
Documents ingested before that have a summary sentence and nothing else.

They do not need to be sent again: the photograph is kept, privately, for
exactly this reason — every extracted fact stays checkable against the paper it
came from, and that also means re-checkable. This downloads each stored image,
runs the same reader over it, and fills in what the record could not hold at
the time.

Nothing is invented and nothing already on file is overwritten with less: a
document whose photograph no longer reads is left exactly as it is.

    uv run python scripts/backfill_document_details.py --parent parent-xxxx
    uv run python scripts/backfill_document_details.py --parent parent-xxxx --apply
"""

from __future__ import annotations

import argparse

from anbu_care import service
from anbu_care.docvision import read as vision


def _download(object_name: str) -> bytes | None:
    from anbu_care.comms.storage import _bucket_name

    bucket_name = _bucket_name()
    if not bucket_name:
        return None
    try:
        from google.cloud import storage as gcs

        blob = gcs.Client().bucket(bucket_name).blob(object_name)
        return blob.download_as_bytes()
    except Exception as exc:  # noqa: BLE001 - a missing object is an outcome
        print(f"    could not download {object_name}: {type(exc).__name__}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", required=True)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without it, only report them")
    args = parser.parse_args()

    documents = service.list_documents(args.parent)
    print(f"{len(documents)} document(s) for {args.parent}\n")

    filled = 0
    for doc in documents:
        state = "has details" if doc.details else "EMPTY"
        print(f"  {doc.document_id}  {doc.kind.value:20} {state}")
        if doc.details or not doc.source_filename:
            continue

        image = _download(doc.source_filename)
        if not image:
            continue

        mime = "image/png" if doc.source_filename.endswith(".png") else "image/jpeg"
        reading = vision.read(image, mime)
        if not reading.ok or not reading.payload:
            print(f"    re-read failed: {reading.detail}")
            continue

        keys = ", ".join(sorted(k for k, v in reading.payload.items() if v))
        print(f"    re-read {len(reading.payload)} field(s): {keys[:90]}")
        filled += 1
        if args.apply:
            doc.details = dict(reading.payload)
            service.save_document(doc)
            print("    written")

    print(f"\n{filled} document(s) {'updated' if args.apply else 'would be updated'}")
    if filled and not args.apply:
        print("re-run with --apply to write them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
