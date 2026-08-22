"""Backfill the parent -> case reverse index for cases opened before it existed.

`open_case` writes the index now, and `update_case` repairs it for any case
that gets touched. But a case opened earlier and never updated since has no row,
and until it does `latest_case_for_parent` returns None — which is what a bill
photographed against an old case would hit.

Read-modify-write, idempotent, and it prints what it changed. Run it once
against the deployed store:

    set -a && . ./.env && set +a
    uv run python scripts/backfill_case_index.py            # dry run
    uv run python scripts/backfill_case_index.py --apply
"""

from __future__ import annotations

import sys

from anbu_care import service
from anbu_care.provenance.store import get_store
from anbu_care.schemas import Case


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    store = get_store()

    rows = store.query_by_sk("META")
    cases = [Case.model_validate(row) for row in rows if row.get("case_id")]
    print(f"found {len(cases)} case(s)")

    missing = []
    for case in cases:
        indexed = store.query_prefix(f"PARENT#{case.parent_id}", f"CASE#{case.case_id}")
        if not indexed:
            missing.append(case)

    if not missing:
        print("every case already has an index row; nothing to do")
        return 0

    print(f"{len(missing)} case(s) missing an index row:")
    for case in missing:
        print(f"  {case.case_id}  parent={case.parent_id}  opened={case.opened_at:%Y-%m-%d}")

    if not apply:
        print("\ndry run. re-run with --apply to write them.")
        return 0

    for case in missing:
        service.index_case(case, store=store)
    print(f"\nwrote {len(missing)} index row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
