---
name: clear-rehearsal-debris
description: >-
  Clear what rehearsing left on the Anbu Care demo family's record - several
  photographs of one discharge summary, and the recovery windows earlier takes
  left open. Use when the user asks to clean the record before a take, says the
  Record tab is cluttered or shows duplicate discharge summaries, asks about
  stale or leftover recovery windows, or is about to record a run-through where
  the Record tab is on camera.
---

# Clear rehearsal debris before a take

Document dedupe is keyed on the **parent and the image hash**, not the case:

```
docvision/ingest.py:323   for existing in service.list_documents(parent_id)
```

Bills are the opposite - `bills/ingest.py:129` scans `list_bills(case_id)` - which
is why the same bill photo replays cleanly on every run and the same discharge
summary does not. A fresh case resets the bill check and never resets this one.

So a run-through needs a different photograph of the same paper each time
(`~/Desktop/anbu-demo/discharge_summary.png` plus `_take2` to `_take5`), and five
takes leave five discharge summaries on the record for one admission. The Record
tab lists every one of them (`webui/index.html:1101`).

That stack is rehearsal debris. **It is not her history**, and the difference is
the only judgement this tool makes:

| | |
|---|---|
| **debris** | several documents describing the **same** admission, and recovery windows left open on cases the demo has moved past |
| **history** | the same kind of document describing a **different** admission, which is a second discharge and survives |

Identity is read off the page - admitted and discharged dates plus hospital for
a discharge summary, collection date for a lab report - never off the image
hash, because five photographs of one paper is the whole problem.

## Commands

```bash
# the plan, deletes nothing
.venv/bin/python scripts/clear_rehearsal_debris.py

# plan plus an export of every row it would delete
.venv/bin/python scripts/clear_rehearsal_debris.py --backup /tmp/debris.json

# do it
.venv/bin/python scripts/clear_rehearsal_debris.py --backup /tmp/debris.json --apply
```

`--backup` is required for `--apply`, the same rule `collapse_demo_family.py`
uses, because deleting is not reversible. Default target is whoever
`+16692167706` resolves to; `--parent` overrides.

Read the plan out to the user before applying it. It prints one line per group
saying what it keeps and how many it drops.

## When to run it

**After beat 7 of the take, not before you roll.** This matters and is easy to
get backwards. The tool never deletes the only record of an admission - a lone
leftover from an earlier rehearsal is indistinguishable from her genuine
history, and guessing there is not a thing a record system should do. So before
the take, one stale discharge summary is *kept*, and beat 7 then puts a second
one beside it.

Run it once the take's own discharge summary is on the record. At that moment
the group has two members, the live window names the new one, and the tool keeps
exactly the right row. Anywhere before showing the Record tab is fine.

## Four things that are easy to get wrong

**It is not a reset button.** Deleting the document does let the same photograph
back in past the hash check, and using it that way means deleting a record to
dodge a guard that is working. Use the next take file instead.

**Receipts are untouched, and that is load-bearing for beat 12.** No receipt is
deleted, so `receipt_count` on `/verify` does not move and the chain still
verifies. The cost is honest and worth stating: a trace step from an earlier
take can name a document that is no longer on the record. The receipt still
verifies, because receipts carry hashes rather than the row.

**The photographs stay in the bucket.** Only the record row goes. The image is
deliberately kept, so anything that was read can still be checked against the
paper it was read from.

**The live window is the one on the parent's LATEST case**, not the newest by
date. Every take reads the same discharge date off the same paper, so all the
windows start on 2026-08-22 and picking by date would be picking at random.
The surviving document in a group is the one that live window points at, so its
receipt still resolves to a row.

## Checking it worked

The Record tab should show **one** discharge summary for the August admission,
and any genuinely separate admission still there beside it. `/verify` should
report the same `receipt_count` as before.

`make preflight` covers the other half of this: **no recovery window already
open**. A window left open by an earlier take wins `due_now`'s
`max(starts_on)` and answers the tick instead of the one beat 7 opens, so the
check-in reports the wrong day under the right sentence. `make preflight FIX=1`
closes it, receipted rather than deleted.
