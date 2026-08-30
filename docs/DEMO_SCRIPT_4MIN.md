# Demo script — 4:00, unedited, autonomy first

The four-minute cut. It exists because the long script
([`DEMO_SCRIPT.md`](DEMO_SCRIPT.md), ~5m45s) and the take run sheet
([`takes/recovery-run-sheet.md`](takes/recovery-run-sheet.md), thirteen beats)
both open on narrative and reach the machinery later. This one inverts that:
**the first thing on screen is the system doing something nobody asked it to
do**, and the story is told over the top of it.

One handset, one browser tab, one terminal. No cuts.

```
Live service   https://anbu-care-37j4eofpwq-el.a.run.app
Booker         https://anbu-care-booker-37j4eofpwq-el.a.run.app
Project        anbu-care-hack   ·   region asia-south1
```

| | beat | ends |
|---|---|---|
| 0:00 | Cold open | 0:15 |
| 0:15 | **A — one voice note, four actions, a regulatory clock** | 1:25 |
| 1:25 | **B — it booked a real clinic, and photographed itself doing it** | 2:30 |
| 2:30 | **C — edit a receipt, and the chain says so** | 3:25 |
| 3:25 | The honest wall | 4:00 |

---

## The one honest sentence

Say this **once**, during beat A, while the pre-authorisation is on screen:

> The insurer here is a simulated adjudicator, and the payment rail is in test
> mode. Everything else — the phone, the browser, the clinic, the clock, the
> chain — is real.

That is the whole disclosure. Do not tour it, do not repeat it, and do not
apologise for it. The receipts, the API, the trace and the dashboard all carry
`simulated` themselves, so a judge who checks finds the same thing you said.

---

## Pre-roll — finish this ten minutes before you record

**1. Preflight must be green.**

```bash
make preflight            # add FIX=1 if it reports bound handset / open window / live code
```

Ends with `ready`. Two lines in it are the ones that matter here:

```
ok    canonical case-da1c2cb6db   verified=True receipts=8, expected verified=True receipts=8
ok    canonical case-a7cf9fa613   verified=False receipts=2, expected verified=False receipts=2
```

Those two cases are kept alive deliberately, one intact and one broken, so you
know before rolling that beat C still demonstrates something.

**2. Put the booker live and make the booking that beat B shows.**

```bash
make booking-mode MODE=live       # preflight then warns "LIVE" — that is correct
```

Then run the take's first three beats *as a rehearsal* — voice note, scan the
QR, dictate the order — and **stop there**. The booker drives eight real clinic
sites in a real browser and takes about three minutes, which does not fit in a
four-minute film. By the time you roll, the appointment and both screenshots
are on that case.

> **This is the one thing in the film that was not made during it**, and beat B
> says so in a sentence. A real browser filling in a real clinic's form cannot
> be made faster by wanting it to be.
>
> Keep that case id. Call it **`$BOOKED`**.

**A booking reaches DLABS Diagnostics under her name. Cancel it: +91 88707 20883.**

**3. Seed the lapsed insurer clock.**

```bash
FORCE=1 make breach-seed          # FORCE only if you cleared the thread since the last one
```

One command, before you roll, not after. It opens a case and backdates a
cashless clock by 70 minutes so the hour is already up; Cloud Scheduler notices
within a minute and the breach message is waiting in the thread by the time you
reach beat A. Both the request and the breach are fenced on the chain as
`requested_at_source: demonstration_seed`.

**4. Mint the throwaway that beat C breaks.**

```bash
THROW=$(curl -s -X POST https://anbu-care-37j4eofpwq-el.a.run.app/api/intake \
  -H 'content-type: application/json' \
  -d '{"parent_id":"parent-4020cb6672","symptoms":["chest pain"],"reported_by":"tamper-demo"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['case_id'])")
echo $THROW
```

`/api/intake` writes a triage decision and sends no messages, which is why it is
safe to run against the demo family ten minutes before filming. **Tamper this,
never the case you just showed** — a judge who verifies the story's case must
find it intact.

It does leave a broken case on her record, so `make debris` after the take is
not optional.

**5. Terminal ready.** Two tabs, large type, `$THROW` exported in both.

**6. `gcloud auth application-default login`** — beat C writes to Firestore from
the laptop. An expired token hangs for five minutes and then fails.

---

## 0:00 — Cold open (15s)

Dashboard open, nothing else.

**Say** — *"My mother is seventy-one and lives in Thoothukudi. I live nine and a
half time zones away. This is the system that does what I would do, at 2am,
without waking me. Everything you're about to see is running on Cloud Run right
now."*

Do not explain the architecture. It arrives on its own in beat B.

---

## A — 0:15 · One voice note, four actions, a regulatory clock (70s)

*Her handset on camera. She sends a Tamil voice note. Then put the phone down
and do not touch it.*

**Expect**, within about fifteen seconds, four separate messages:

1. the family alert, in English, to the son
2. the care-circle notice to Meena the neighbour, in Tamil, carrying the
   bedside link and QR
3. a cashless pre-authorisation filed against her policy, provisionally
   authorised, **starting the 1-hour IRDAI clock**
4. a claim-update message saying where that stands

**Say** — *"One voice note. While the son is asleep: he's told, the neighbour
gets what the doctor needs, cover is filed so the family doesn't pay the
hospital out of pocket, and a regulatory clock starts. I didn't trigger any of
that. Nothing here has a button."*

**Then the honest sentence** (above), while the pre-auth is on screen.

**Then scroll up in the same thread** to the breach message already sitting
there:

**Expect** — the pre-authorisation on the *other* case is still unanswered after
the 1-hour window, followed by what the policyholder is entitled to: the insurer
bears delay-caused cost, delayed settlement carries interest at two percent
above the bank rate, and the grievance and Ombudsman ladder is named. Then:
Anbu Care has not filed anything, cannot compel anyone, and is not claiming this
will be won.

**Say** — *"That one arrived while I was talking. The clock was started early so
the hour could run out on camera — the hour itself is real, and the chain
records that it was seeded. Nothing else about it is staged."*

### Google Cloud proof to capture

Cut to the terminal for six seconds. This is the beat's evidence:

```bash
gcloud scheduler jobs list --project anbu-care-hack --location asia-south1 \
  --format="table(name.basename(),schedule,state,lastAttemptTime)"
```

```
ID                    SCHEDULE   STATE    LAST_ATTEMPT_TIME
anbu-recovery-tick    * * * * *  ENABLED  2026-08-30T01:31:03.817079Z
anbu-claims-sla-tick  * * * * *  ENABLED  2026-08-30T01:31:01.427831Z
```

**Say** — *"Cloud Run holds no timer, so the clock is a real Cloud Scheduler job.
Last attempt, thirty seconds ago. It ticks whether or not anybody is watching —
which is the entire point of a one-hour regulatory deadline."*

**TRAP** — one escalation per take. A second voice note does not continue the
story, it opens a second chain, and everything after it lands there. **Write
beat A's case id down**; you need it at beat C.

**TRAP** — if the breach message is not in the thread, the seed ran against a
parent with no primary contact. The breach is on the chain either way, but
nobody was told, and a `pre_auth.breached` receipt with no `comms.sent` beside
it is the tell.

---

## B — 1:25 · It booked a real clinic, and photographed itself doing it (65s)

*Dashboard, on `$BOOKED`. Scroll to the card headed **Arranged by Anbu Care**.*

**Expect** — DLABS Diagnostics, the distance, `requested, not yet confirmed`,
the cancellation number, and — under *Tried first, and why not* — the centres it
tried before this one and what stopped each.

**Say** — *"A clinician dictated a test in Tamil. Nobody chose a lab. It searched
real Thoothukudi centres, ranked them, and opened each one's own booking site in
a real browser: read the form, mapped the fields, filled it, read it back,
submitted. Seven of the eight it tried can't take a submission. This one can."*

**Then open both screenshots from the card.** They are the beat.

- **before** — the form as it stood, filled in, immediately before submitting
- **after** — the centre's own answer page

**Say** — *"Two screenshots, not one, because a good form clears itself on
success — the answer page alone shows empty boxes and reads as though nothing
was ever typed. This is the only evidence on the record that Anbu Care didn't
write itself."*

**Say the honest sentence about timing** — *"This booking was made a few minutes
before I hit record, because a browser driving a real clinic's website takes
about three minutes. It's the one thing here that isn't live."*

**Say** — *"It says requested, not confirmed. An unauthenticated callback form
cannot truthfully produce anything stronger, so the system doesn't claim it did.
And it captured the cancellation path before it committed to anything — an agent
that can create an obligation and can't undo it is worse than one that does
nothing."*

### Google Cloud proof to capture

```bash
gcloud run services list --project anbu-care-hack --region asia-south1 \
  --format="table(metadata.name,status.latestReadyRevisionName,status.url)"
```

```
NAME              LATEST_READY_REVISION_NAME  URL
anbu-care         anbu-care-00196-wd4         https://anbu-care-37j4eofpwq-el.a.run.app
anbu-care-booker  anbu-care-booker-00044-9md  https://anbu-care-booker-37j4eofpwq-el.a.run.app
```

**Say** — *"Two services, because Chromium will not fit beside the API. The
browser is deployed apart from the agent, and it is the only thing in this
system allowed to touch a page it doesn't control."*

**TRAP** — a full booker deploy resets it to dry, because
`infra/deploy_booker.sh` uses `--set-env-vars` and replaces everything. Re-run
`make booking-mode MODE=live` **after** any booker deploy, never before.

**TRAP** — the screenshot links are case-scoped and signed. Click them from the
dashboard; a bare curl gets a 401, live, on camera.

---

## C — 2:30 · Edit a receipt, and the chain says so (55s)

*Terminal, full screen. The endpoint is **public** — that is the beat.*

**First, the case they just watched.** Take the case id off the dashboard's own
URL — `/app?case=case-xxxxxxxxxx` — which is beat A's, not whatever the tab
drifted to.

```bash
CASE=case-xxxxxxxxxx
curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/$CASE/verify \
  | python3 -m json.tool
```

```json
{
    "verified": true,
    "receipt_count": 6,
    "broken_at_seq": null,
    "reason": null,
    "public_key": "v/JLEciPv3U4DrYgvOUl8E0CM601GVFCFolLkFgHMZQ="
}
```

The key is fixed and is the one above; **the count is not** — beat A's case has
whatever it earned on the day. Read the number off the screen, not off this
page.

**Say the count out loud, against the trace tab**, which renders one step per
receipt. *"Six steps on screen, six receipts in the chain, and no login to check
it."*

> **An empty chain is a valid chain.** A deleted case answers `verified: true`
> with `receipt_count: 0`. `verified` on its own proves the absence of tampering
> in nothing at all — **the count is what ties the proof to the story.**

**Now break one.** On the throwaway from pre-roll:

```bash
uv run python scripts/demo_support.py tamper $THROW
```

```json
{
  "tampered_case": "case-xxxxxxxxxx",
  "receipt_seq": 1,
  "field": "payload.severity",
  "before": "HIGH",
  "after": "LOW",
  "note": "hash and signature left untouched, as a silent edit would be"
}
```

**Say** — *"That writes straight to Firestore, under the same key, and leaves the
hash and the signature alone — which is exactly what an after-the-fact edit
looks like. It now says her chest pain was low severity."*

**Then ask the deployed service.**

```bash
curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/$THROW/verify \
  | python3 -m json.tool
```

```json
{
    "verified": false,
    "receipt_count": 2,
    "broken_at_seq": 1,
    "reason": "payload does not hash to the recorded hash: content was altered"
}
```

**Say** — *"It names the receipt. Not 'something is wrong' — sequence one,
content altered. If this ever ends up in a dispute about what a system did to
somebody's mother, that is the difference between a log and evidence."*

**Say** — *"And the case you just watched is still intact. I broke a separate
one on purpose, so you can go and check that one yourself."*

### Google Cloud proof to capture

The proof here is what the request did **not** carry. Point at the curl: no
`Authorization` header, no cookie, no key. The same endpoint refuses every
content route without a credential — preflight asserts that on every run
(`content is refused without a credential → HTTP 401`).

**TRAP** — tamper writes to Firestore from the laptop and needs ADC. Without it
it hangs for about five minutes and then fails, in silence, on camera.

**TRAP** — never tamper beat A's case, and never `case-da1c2cb6db` or
`case-a7cf9fa613`. Those two are the permanent pair preflight checks.

---

## 3:25 — The honest wall (35s)

Dashboard on screen, still.

**Say** — *"What's real: the voice note, the Tamil, the document reading, the
clinic, the browser, the clock, the chain, and the fact that nobody pressed
anything. What's simulated: the insurer's adjudicator, and the payment rail is
in test mode. The system says so itself, on every receipt and every API
response, whether or not I mention it."*

**Say** — *"Guarantees here are code, not prompts. The model proposes; a
deterministic layer decides and is the only thing that can write. It never sets
severity, never authorises a payment, never picks a clinic, and never decides
who may read what. That's the architecture, and it's the reason I'm willing to
point this at my own mother."*

**Close** — *"Every claim I just made has a receipt, and the endpoint that checks
them needs no login. Check it."*

Leave the verify URL on screen for the last three seconds.

---

## If something dies mid-take

| what died | what to do |
|---|---|
| Voice note gets no answer in 20s | Keep talking. Gemini transcription occasionally takes 15s. If nothing by 30s, stop — a warm-up note before rolling is the only real smoke test. |
| Breach message not in the thread | Skip it. Beat A's live pre-auth carries the clock on its own. Do not run `breach-seed` on camera. |
| Screenshot link 404s | The appointment stands; say so and move on. "No page was kept for this one" is the honest fallback the endpoint itself returns. |
| `tamper` hangs | ADC expired. Fall back to `case-a7cf9fa613`, the permanently broken one, and say it was tampered earlier. |
| Dashboard blank | Wrong case id. Beat A's escalation minted a new one; the seed minted another. |

## After the take

- **Cancel the DLABS booking: +91 88707 20883.**
- `make booking-mode MODE=dry`
- `make preflight FIX=1` — closes the recovery window the take opened, with a
  receipt. A window left open really does send her a check-in at 09:00 IST every
  day until it is closed.
- `make debris` — the rehearsal booking's case and the tampered throwaway are
  both still on her record.
- Do not delete `case-da1c2cb6db` or `case-a7cf9fa613`.
