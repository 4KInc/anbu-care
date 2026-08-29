"""Clear what rehearsing left on the demo family's record. Nothing else.

Document dedupe is keyed on the PARENT and the image hash, not the case:

    docvision/ingest.py   for existing in service.list_documents(parent_id)

So a fresh case does not reset it, and a run-through therefore needs a
different photograph of the same paper each time. Five takes leave five
discharge summaries on the record for one admission, and the Record tab lists
every one of them. That stack is rehearsal debris. It is not her history.

The distinction this script draws, and the only one it is entitled to draw:

    debris    several documents that describe the SAME admission, and the
              recovery windows earlier takes left open on closed-over cases
    history   the same document kind describing a DIFFERENT admission, which
              is a second discharge and must survive

Identity comes off the page - admission and discharge dates for a discharge
summary, collection date for a lab report - never off the image hash, because
the whole reason this mess exists is that five photographs of one paper have
five hashes.

What it will not touch, ever: receipts, cases, the profile, the WhatsApp index,
and the surviving document in each group. A receipt naming a deleted document
keeps its hashes and still verifies; the Record tab simply has one row instead
of five.

    python scripts/clear_rehearsal_debris.py                       # the plan
    python scripts/clear_rehearsal_debris.py --backup out.json     # plan + export
    python scripts/clear_rehearsal_debris.py --backup out.json --apply

--backup is required for --apply, for the same reason it is in
collapse_demo_family.py: deleting is not reversible.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from anbu_care import service
from anbu_care.config import settings
from anbu_care.provenance.store import COLLECTION, _doc_id, get_store

BATCH = 400

# What makes two documents the same piece of paper. Read off the page, so a
# retake matches the original and a SECOND admission does not.
IDENTITY: dict[str, tuple[str, ...]] = {
    "discharge_summary": ("admitted_on", "discharged_on", "hospital"),
    "lab_report": ("collected_on",),
    "prescription": ("prescribed_on", "prescriber"),
    "policy_schedule": ("insurer", "policy_number"),
}


def _collection():
    from google.cloud import firestore

    cfg = settings()
    client = firestore.Client(project=cfg.project_id,
                              database=cfg.firestore_database)
    return client, client.collection(COLLECTION)


def _parent(explicit: str | None, family_e164: str) -> str:
    """The parent the family's handset resolves to, or an explicit one.

    Taken from the index rather than by recency, because the index is what
    inbound WhatsApp reads. Cleaning a record nothing routes to would leave the
    clutter exactly where it shows.
    """
    if explicit:
        return explicit
    found = service.lookup_whatsapp_number(family_e164)
    if not found or not found.get("parent_id"):
        sys.exit(f"no parent is registered for {family_e164}; pass --parent explicitly")
    return found["parent_id"]


def _identity(doc) -> tuple:
    """What this document is a photograph OF.

    Falls back to the summary line when a kind has no dated fields, and to the
    document id when even that is empty - which makes it unique, so it is kept.
    A document this cannot identify is never a duplicate.
    """
    kind = doc.kind.value if hasattr(doc.kind, "value") else str(doc.kind)
    fields = IDENTITY.get(kind)
    if fields:
        values = tuple(str(doc.details.get(f) or "").strip().lower() for f in fields)
        if any(values):
            return (kind, *values)
    return (kind, doc.summary.strip().lower() or doc.document_id)


def plan(parent_id: str) -> tuple[list[tuple], list[dict], dict]:
    """What is debris, what survives, and why. Reads only."""
    from anbu_care.recovery import window as recovery

    documents = service.list_documents(parent_id)
    windows = recovery.list_windows(parent_id)

    # The live window is the one on the parent's latest case, because that is
    # the run currently on screen. Not the newest by date: every take reads the
    # same discharge date off the same paper, so the dates are all identical
    # and picking by them would be picking arbitrarily.
    latest_case = service.latest_case_for_parent(parent_id)
    on_latest = [w for w in windows if latest_case and w.case_id == latest_case.case_id]
    live = next((w for w in on_latest if w.open), None) or next(iter(on_latest), None)
    protected = {live.document_id} if live else set()

    groups: dict[tuple, list] = defaultdict(list)
    for doc in documents:
        groups[_identity(doc)].append(doc)

    doomed_docs: list[tuple] = []
    survivors: list[dict] = []
    for identity, docs in sorted(groups.items()):
        # Keep the one the live window points at, so its receipt still resolves
        # to a row. Otherwise keep the newest reading of the paper.
        keeper = next((d for d in docs if d.document_id in protected), None) \
            or max(docs, key=lambda d: d.parsed_at)
        survivors.append({"identity": identity, "keeps": keeper.document_id,
                          "of": len(docs)})
        for doc in docs:
            if doc.document_id != keeper.document_id:
                doomed_docs.append((f"PARENT#{parent_id}", f"DOC#{doc.document_id}",
                                    identity, doc.document_id))

    # A window from an earlier take sits open on a case the demo has moved
    # past, and every one of them is counted by parents_with_open_windows and
    # listed on the recovery view. Its prompt slots go with it, because a slot
    # whose window is gone can never be read again.
    doomed_windows: list[tuple] = []
    for w in windows:
        if live is not None and w.window_id == live.window_id:
            continue
        doomed_windows.append((f"PARENT#{parent_id}", f"RECOVERY#WINDOW#{w.window_id}",
                               ("recovery_window", w.window_id), w.status))
        for row in get_store().query_prefix(f"PARENT#{parent_id}",
                                            f"RECOVERY#PROMPT#{w.window_id}"):
            doomed_windows.append((f"PARENT#{parent_id}", str(row["sk"]),
                                   ("recovery_prompt", w.window_id), ""))

    stats = {"documents": len(documents), "windows": len(windows),
             "keeps_window": live.window_id if live else None}
    return doomed_docs + doomed_windows, survivors, stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parent", help="parent_id to clean (default: whoever the "
                                     "family handset resolves to)")
    ap.add_argument("--family-e164", default="+16692167706",
                    help="the family handset that names the parent")
    ap.add_argument("--backup", help="write every affected row here before deleting")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it nothing is written")
    args = ap.parse_args()

    parent_id = _parent(args.parent, args.family_e164)
    doomed, survivors, stats = plan(parent_id)

    print(f"parent: {parent_id}")
    print(f"  {stats['documents']} document(s), {stats['windows']} recovery window(s) on file\n")
    for s in survivors:
        kind = s["identity"][0]
        detail = " ".join(x for x in s["identity"][1:] if x)
        mark = f"drops {s['of'] - 1}" if s["of"] > 1 else "unique"
        print(f"  {kind:<18} {detail:<46} keeps {s['keeps']}  ({mark})")
    if stats["keeps_window"]:
        print(f"\n  recovery window kept: {stats['keeps_window']} (the live one)")

    if not doomed:
        print("\nnothing to clear. The record is already one row per document.")
        return

    print(f"\n  {len(doomed)} row(s) would be deleted:")
    for _pk, sk, ident, _extra in doomed:
        print(f"    {sk}")

    if args.backup:
        client, col = _collection()
        rows = []
        for pk, sk, _ident, _extra in doomed:
            snap = col.document(_doc_id(pk, sk)).get()
            if snap.exists:
                rows.append({"_id": snap.id, **snap.to_dict()})
        with open(args.backup, "w") as fh:
            json.dump(rows, fh, indent=1, default=str)
        print(f"\nbacked up {len(rows)} row(s) to {args.backup}")

    if not args.apply:
        print("\ndry run. Re-run with --backup FILE --apply to delete.")
        return
    if not args.backup:
        sys.exit("refusing to delete without --backup; this is not reversible")

    client, col = _collection()
    batch = client.batch()
    for i, (pk, sk, _ident, _extra) in enumerate(doomed, 1):
        batch.delete(col.document(_doc_id(pk, sk)))
        if i % BATCH == 0:
            batch.commit()
            batch = client.batch()
    batch.commit()
    print(f"\ndeleted {len(doomed)} row(s). Receipts, cases and the profile are untouched.")


if __name__ == "__main__":
    main()
