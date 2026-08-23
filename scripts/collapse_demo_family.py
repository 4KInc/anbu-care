"""Collapse the accumulated demo families down to the one that is live.

Seeding used to mint a new parent every time and repoint the family's WhatsApp
number at it, so each re-seed left the previous record behind with its cases,
receipts and documents stranded under a parent nothing resolved to any more.
Eighty-three profiles accumulated that way, and a demo read its settings off
whichever one happened to be seeded last — which is how a contact recorded as
reading English sent Tamil.

`/api/demo/seed` no longer does that; it reuses the family the handset already
belongs to. This clears up what the old behaviour left behind.

What survives: the parent the family's number currently resolves to, every
case belonging to it, its receipts, documents and wellbeing entries, and the
WhatsApp index entries pointing at it. Everything else is a record nothing can
reach.

Deleting is not reversible, so it does nothing until told to:

    python scripts/collapse_demo_family.py                    # show the plan
    python scripts/collapse_demo_family.py --backup out.json  # plan + full export
    python scripts/collapse_demo_family.py --backup out.json --apply

--backup writes every document in the collection, not only the doomed ones, so
the file restores the whole state rather than half of it. It is required for
--apply for that reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from anbu_care import service
from anbu_care.config import settings
from anbu_care.provenance.store import COLLECTION

BATCH = 400


def _collection():
    from google.cloud import firestore

    cfg = settings()
    client = firestore.Client(project=cfg.project_id,
                              database=cfg.firestore_database)
    return client, client.collection(COLLECTION)


def _survivor(explicit: str | None, family_e164: str) -> str:
    """The parent the family's handset actually resolves to.

    Taken from the index rather than chosen by recency or row count, because
    the index is what inbound WhatsApp reads. Keeping a record that inbound
    would not route to is how this mess started.
    """
    if explicit:
        return explicit
    found = service.lookup_whatsapp_number(family_e164)
    if not found or not found.get("parent_id"):
        sys.exit(f"no parent is registered for {family_e164}; pass --keep explicitly")
    return found["parent_id"]


def _plan(docs: list[tuple[str, dict]], keep: str) -> list[str]:
    """Document ids to delete. Everything not reachable from the survivor."""
    owner = {d.get("pk"): d["parent_id"] for _id, d in docs
             if d.get("pk", "").startswith("CASE#") and d.get("parent_id")}

    def survives(d: dict) -> bool:
        pk = d.get("pk", "")
        if pk.startswith("PARENT#"):
            return pk == f"PARENT#{keep}"
        if pk.startswith("CASE#"):
            # A case partition whose rows never name a parent is a fragment
            # left behind by an earlier deletion. It cannot be the survivor's,
            # because the survivor's cases all carry their parent_id.
            return owner.get(pk) == keep
        if pk.startswith("WANUMBER#"):
            return d.get("parent_id") == keep
        # An unrecognised partition is kept, not guessed at. Better a stray row
        # than a deleted one nobody meant to name.
        return True

    return [doc_id for doc_id, d in docs if not survives(d)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", help="parent_id to keep (default: whoever the "
                                   "family handset resolves to)")
    ap.add_argument("--family-e164", default="+16692167706",
                    help="the family handset that decides the survivor")
    ap.add_argument("--backup", help="write every document here before deleting")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it nothing is written")
    args = ap.parse_args()

    client, col = _collection()
    docs = [(d.id, d.to_dict()) for d in col.stream()]
    keep = _survivor(args.keep, args.family_e164)
    doomed = set(_plan(docs, keep))

    kept = [d for doc_id, d in docs if doc_id not in doomed]
    print(f"survivor: {keep}")
    print(f"keep  {len(kept):4d}  {dict(Counter(d.get('sk','?').split('#', 1)[0] for d in kept))}")
    print(f"drop  {len(doomed):4d}  "
          f"{dict(Counter(d.get('pk','?').split('#', 1)[0] for i, d in docs if i in doomed))}")
    for d in kept:
        if d.get("pk", "").startswith("WANUMBER#"):
            print(f"    keeps {d['pk']} -> {d.get('parent_id')}")

    if args.backup:
        with open(args.backup, "w") as fh:
            json.dump([{"_id": i, **d} for i, d in docs], fh, indent=1, default=str)
        print(f"backed up all {len(docs)} documents to {args.backup}")

    if not args.apply:
        print("\ndry run. Re-run with --backup FILE --apply to delete.")
        return
    if not args.backup:
        sys.exit("refusing to delete without --backup; this is not reversible")

    batch = client.batch()
    for i, doc_id in enumerate(sorted(doomed), 1):
        batch.delete(col.document(doc_id))
        if i % BATCH == 0:
            batch.commit()
            batch = client.batch()
            print(f"  committed {i}/{len(doomed)}")
    batch.commit()
    print(f"deleted {len(doomed)} documents; {len(kept)} remain")


if __name__ == "__main__":
    main()
