# Take run sheet — thirteen beats, one continuous take

The running order, what each beat should put on screen, and the specific thing
that silently ruins it. Every trap below was reproduced against the real code,
not inferred from it.

## Four standing constraints

**The window: 22 Aug → 4 Sep 2026.** Day 1 is the discharge date on the paper.
After 4 September beat 8 has nothing to send and closes the window instead.

**A window left open now actually sends.** Before the scheduler existed, a
window abandoned by a half-finished rehearsal sent nothing, because nothing
called the tick. Now it sends: a real check-in to that handset at 09:00 IST,
every day, until it stops or the fortnight runs out. Beat 11 closes it in a
complete run. An abandoned run does not, and `make preflight FIX=1` is what
closes it honestly, with a receipt.

**One image per run.** Document dedupe is keyed on the parent and the image
hash (`docvision/ingest.py:323`), so a fresh case does not reset it. Five
photographs of the same paper are in `~/Desktop/anbu-demo/`:
`discharge_summary.png`, then `_take2` through `_take5`. Bills are case-scoped
(`bills/ingest.py:129`) and replay freely.

**The breach cannot happen on its own.** The simulated adjudicator answers in
about a second, every time, so no pre-authorisation is ever left waiting and the
1-hour clock never lapses naturally on the demo family. The path is real and is
reachable in production, and here if the adjudicator itself fails. It just
cannot be scheduled for a recording, which is why `make breach-seed` exists and
why every clock it starts is fenced on the chain as `demonstration_seed`.

## Who each message is for

One handset stands in for three people, so every message arrives in the same
thread. `ANBU_DEMO_ROLE_TAGS=on` captions each one with its addressee:

```
👵 TO AMMA, ASHANTHI          her own check-in, in Tamil
📱 TO HER SON, ARUN           the family alert and the money
🏠 TO THE NEIGHBOUR, MEENA    a listed contact, no case access
🩺 TO THE TREATING TEAM       the bound handset, notes and orders
```

**Say once, on camera, that these captions are added for the recording.** They
are off in code by default and exist because one phone is playing three parts.
A viewer who assumes the product ships them has been misled by the demo, which
is the one thing this demo cannot afford.

## Before you roll

- [ ] **Preflight is green.** `make preflight`, and `make preflight FIX=1` if the
      handset is still bound as a clinician, a code request is outstanding, or a
      recovery window is already open. All three would be silent on camera.
- [ ] **Pick an unused discharge summary.** One per run-through, rehearsals
      included.
- [ ] **Check the clock in IST.** Beat 8 needs 09:00 or later where she is. IST
      runs 10½ hours ahead of Central, so the only dead zone is roughly
      21:00–22:30 your time.
- [ ] **Confirm the scheduler is enabled.**
      `gcloud scheduler jobs describe anbu-recovery-tick --project anbu-care-hack
      --location asia-south1` should say `ENABLED`. If it is paused or deleted,
      beat 8 silently never happens. `./infra/schedule_recovery_tick.sh`
      recreates it.
- [ ] **Decide the booker's mode, and have the cancel number to hand.**
      `make booking-mode` reports it. LIVE means beat 4 books a real clinic under
      her name: **+91 88707 20883** to cancel. DRY fills every form and never
      submits, and beat 4 then honestly reports that it booked nothing.
- [ ] **`make breach-seed`, and BEFORE beat 1, not after.** One command. It
      opens a case and backdates a cashless clock 70 minutes, so the hour is
      already up and the scheduler catches it within a minute. Beat 12 then
      needs nothing from you.

      **The order is load-bearing.** Photographs attach to the parent's LATEST
      case (`_latest_open_case_for`, `server.py`), and the seeded case becomes
      the latest the moment it is created. Seed first and beat 1's voice note
      opens a newer case, so the bill and the discharge summary land where you
      expect. Seed after beat 1 and both photos attach to the seeded case
      instead, and beats 6 and 7 quietly happen on the wrong chain.

      It runs on the DEMO FAMILY on purpose, and refuses to run without
      `ANBU_BREACH_PARENT` set. The breach message names the parent and goes to
      her primary contact, so seeding it on a throwaway either sends nothing
      (no contact on file) or arrives saying "pre-authorisation for Throwaway"
      to a number nobody is holding. That was a real mistake, caught by the
      message not turning up. It is safe here in a way it was NOT for the
      QUERIED and DENIED checks: those needed a corrupted policy and would have
      damaged her record, whereas this mutates nothing on her profile. It opens
      one case and starts one clock, fenced on the chain as
      `demonstration_seed`.
- [ ] **Send a warm-up voice note.** The only real smoke test. Preflight checks
      state, never whether Gemini and Twilio will answer.
- [ ] **Know that `FIX=1` and `make debris` need ADC.** They reach Firestore from
      the laptop, so an expired token makes them hang for five minutes and then
      fail. Plain `make preflight` is an HTTP call to the deployed service and
      works regardless. `gcloud auth application-default login` when you need
      the other two.

---

## 1. She sends a Tamil voice note

*her handset · four outcomes, none of them typed*

**Expect** — the family alert in English; the care-circle notice to Meena in
Tamil carrying the bedside link and QR; a cashless pre-authorisation filed on
her policy and authorised provisionally by the simulated adjudicator, starting
the 1-hour IRDAI clock; and a claim-update message telling you where that
stands.

**Say** — one voice note, and while the son sleeps four things happen: he is
told, the neighbour gets what the doctor needs, cover is filed so the family
does not pay the hospital out of pocket, and a regulatory clock starts. Nothing
was triggered. That is the whole argument in thirty seconds.

**Say** — the insurer here is a simulated adjudicator. The message body does not
say so any more, deliberately: it is on the receipt, the API, the trace and the
dashboard, and a family reading about their mother is not the audience for a
note about the build's counterparty. Say it once while it is on screen.

**Note** — cashless means the insurer pays the hospital. Authorised is
provisional cover at admission, never a settlement, and Anbu Care never moves
that money or claims the hospital was paid.

**Note** — this opens the case the whole take runs on. Escalation always mints a
new one (`wellbeing/handler.py:192`), so the take never shares a case with a
rehearsal.

**TRAP — one escalation per take.** A second voice note does not continue the
story, it starts a second chain, and everything after it attaches there. A run
that looked empty on the timeline was this: the beats were on the earlier case
and the screen was showing the newer one. Keep beat 1's case id somewhere you
can read it at beat 12.

## 2. Scan the QR, then *Connect this phone*

*the neighbour's handset, at the bedside*

**Expect** — the bedside page opens on a scoped, revocable link, and the handset
is bound as the treating team.

**Note** — from here until beat 5 that phone is wearing the clinician hat, and
everything it sends is read as a note or an order.

## 3. The doctor speaks, in Tamil

*the bound handset*

**Expect** — the sentence reaches the family labelled as rendered, not as the
system's own words.

## 4. He orders a test, in Tamil

*the bound handset · start it, then walk away from it*

**Expect** — an acknowledgement within seconds: *"got it, reading it now. What
was ordered and where it can be done will follow in a moment. Nothing is
recorded until it has been read, and nothing is booked."* The outcome arrives
about **three minutes** later, during beat 6.

**Do NOT wait for it.** The mandate allows eight attempts and the booker drives
each clinic's own site in a real browser: navigate, find the booking form, have
the model map the fields, fill, read back, screenshot. Thirty to sixty seconds
per centre, and a site that will not load burns a timeout before it moves on.
Three minutes of silence on camera is the failure; the beat is the ack plus what
you say over it.

**Say** — it is out arranging this while nobody waits on it, and the answer will
come back into this thread. That is the pitch, said out loud, rather than a
progress bar.

**Note** — the booker is **LIVE**. A booking reaches a real clinic under her
name. Of eight real Thoothukudi centres one currently accepts a submission,
DLABS Diagnostics. **Cancel what you book: +91 88707 20883.** Preflight reports
this as a warn, not a failure: *LIVE - a booking during the take would reach a
real clinic and need cancelling*.

**TRAP** — a full deploy of the booker resets it to dry, because
`infra/deploy_booker.sh` uses `--set-env-vars` and replaces everything. After
any booker deploy, run `make booking-mode MODE=live` again. After, never before.

## 5. STOP hands the handset back — the hinge of the whole take

*the bound handset · say this one out loud, it is not a throwaway*

**Expect** — "This handset is no longer connected as the treating team. Nothing
further from it is recorded against that case."

**TRAP** — skip this and **both** photo beats refuse: *"this handset is connected
as the treating team, so it records notes and orders — not bills. Send STOP to
hand it back, then send the photo again"* (`server.py:2788`). Beats 6 and 7 never
reach the reader at all.

**Note** — exact whole-message match. "Stop the bleeding" is a clinical note and
stays one.

## 6. Photograph the bill

*the neighbour's handset, now unbound*

**Expect** — acknowledged, read, priced, paid, and a settlement confirmation to
the contact who consented to hear about money.

**Note** — bill dedupe scans the case, so the same bill photograph replays
cleanly on every run.

## 7. Photograph the discharge summary

*the same handset · a file you have not used before*

**Expect** — an acknowledgement first, then the outcome as its own message:

```
that discharge summary is on Ashanthi's record.
Admission 2026-08-19 to 2026-08-22. 6 medications listed on discharge.
medication list updated: 3 on file, 6 on discharge;
allergies added: Sulfa drugs; discharged on 2026-08-22.
```

and on the trace:

> Recovery check-ins began. 14 days from 2026-08-22, counted from the discharge
> date on the document.

**Say** — the summary lists two allergies. One was already on file, and the
record now holds both: a discharge summary adds what that admission recorded and
never retracts what she has carried for years.

**TRAP** — reuse a file and you get *"already on the record"* instead: nothing
ingested, no window opened, and the beat says the opposite of what you just
claimed.

## 8. The check-in arrives on its own, in Tamil

*her handset. Nothing is typed.*

**Do** — nothing. Keep talking. The check-in goes out the moment beat 7 opens
the window, so it lands about **twenty seconds** after you send the photo, while
you are still describing what the summary changed. A Cloud Scheduler job calls
`/api/recovery/tick` every minute and owns every morning after this one.

**Say** — Cloud Run has no timer, so nothing in the service waits for nine
o'clock. Something outside calls it, and it sends only what is owed. Polling
every minute is safe because the tick takes no instruction: it reads stored
state, and two guards inside it bound the result to one message per window per
local day. The opening send claims that day's slot, which is also what stops the
next scheduled tick asking her the same question twice.

**Expect** — the day counted from 22 August, of 14 (day 7 on the 28th). The
template names no medicine, gives no advice, and carries no assessment.

**TRAP** — one prompt per window per Indian day, whoever triggers it. A
rehearsal that let a tick through burns that day's slot and the take gets
nothing. The Indian date rolls over at 13:30 Central, so a morning rehearsal and
an afternoon take are different days.

**TRAP** — the hour gate is 09:00 in HER timezone. Between roughly 21:00 and
22:30 Central it is before nine in Thoothukudi and nothing will be sent, however
long you wait.

## 9. She answers that she is fine

*her handset*

**Say** — நல்லா இருக்கேன், மருந்து எடுத்துக்கிட்டேன்

**Expect** — recorded, `phase=recovery`.

**Say** — that label is computed from two stored facts (a window is open, a
prompt went out recently) and from nothing she wrote. The system is allowed to
ask. It is not allowed to interpret the answer.

## 10. She answers again, and it is chest pain

*her handset*

**Say** — நெஞ்சு வலி திரும்ப வந்திருச்சு

**Expect** — the family is told what was heard, in her own words, with no advice
and no diagnosis, and asked to call her now.

**Say** — Gemini normalises the wording into symptom terms; a deterministic table
decides. The raw text reaches that table regardless, so a silent model can widen
a match but never suppress one.

## 11. STOP ends the check-ins

*her handset*

**Expect** — stopped on the message itself, before anything is stored as a report
of how she is.

**Say** — whole-message match again. "Stop the pain" is a symptom and goes to the
red-flag table like any other sentence.

## 12. The insurer's hour ran out while you were working

*scroll back in the thread. Nothing to trigger.*

**Do** — nothing. `make breach-seed` before you rolled put an already-lapsed
clock on a new case for the same parent. The scheduler ticks every minute, so the breach was
recorded and the message sent during beats 1 to 11, and it has been sitting in
the thread since.

**Expect** — a claim-update message: the pre-authorisation is *still unanswered
after the 1-hour window*, followed by what the policyholder is entitled to. The
insurer bears delay-caused cost such as an extra room day, delayed settlement
carries interest at two percent above the bank rate, and the grievance and
Ombudsman ladder is named. Then: **Anbu Care has not filed anything, cannot
compel anyone, and is not claiming this will be won.**

**Say** — the clock was started early so the hour could run out on camera. The
hour itself is real, the scheduler noticed on its own, and the chain records
the seed: both the request and the breach carry
`requested_at_source: demonstration_seed`. Say it before a judge finds it.

**Note** — this is a separate case on purpose. A case opened by the voice note
already has an answered pre-authorisation, and the seed refuses to undo a
recorded decision, so there is no clock left on it to breach. It is the same
PARENT though, which is what makes the message name Ashanthi and arrive on the
handset you are filming.

**TRAP** — if the message is not in the thread, the seed ran against a parent
with no primary contact. The breach is still recorded on the chain and the tick
still reports it, but `_tell_the_family` had nobody to send to and there is no
`comms.*` receipt on the case. Check the case's receipts: a breach with no
`comms.sent` beside it means nobody was told.

**TRAP** — the breach can never happen naturally on the demo family. The
simulated adjudicator answers in about a second, every time, so nothing is ever
left waiting. It is reachable in production, and reachable here if the
adjudicator itself fails, but it cannot be scheduled for a take. That is what
the seed is for, and why it is fenced.

## 13. Verify, and read the receipt count

*terminal, on camera. The endpoint is PUBLIC, which is the point.*

**Use the case id from BEAT 1**, not whatever the dashboard is showing. Every
escalating message calls `service.open_case` unconditionally, so beat 10 opened
a second case, and `make breach-seed` opened a third on a throwaway parent: the chest-pain answer is a new episode with its own chain, and
the system does not fold a new emergency into an old one. Beat 1's case has the
whole arc. Beat 10's has a fragment. Say which you are showing and why.

**Do** — take the case id from the beat 1 link (`/app?case=case-...`), then:

```bash
CASE=case-xxxxxxxxxx
curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/$CASE/verify \
  | python3 -m json.tool
```

**Expect**

```json
{
    "verified": true,
    "receipt_count": 8,
    "broken_at_seq": null,
    "reason": null,
    "public_key": "v/JLEciPv3U4DrYgvOUl8E0CM601GVFCFolLkFgHMZQ="
}
```

**Stage it against the trace.** The trace tab renders one step per receipt. Open
it, count the steps, then run verify and show the same number coming back from
an endpoint that needs no login. "Twelve steps on screen, twelve receipts in the
chain, and you can check it yourself."

**TRAP** — a deleted or empty case also answers `verified: true`, with
`receipt_count: 0`, because an empty chain is a valid chain. `verified` alone
proves the absence of tampering in nothing at all. **The count is what ties the
proof to the story they just watched.** Read it out loud.

**Then the contrast, which is what makes this a demonstration rather than a
checkmark:**

```bash
curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/case-a7cf9fa613/verify \
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

That is the canonical tampered case, kept deliberately broken. Preflight asserts
both of them every run - `verified=True receipts=8` and `verified=False
receipts=2` - so you know before you roll that the demonstration still
demonstrates something. Fifteen seconds for the pair.

---

## After the take

Beat 7 leaves a second discharge summary beside the one an earlier take put on
the record, and the Record tab lists every one. Collapse them before that tab is
on camera again:

```bash
make debris                                   # the plan, deletes nothing
make debris BACKUP=/tmp/debris.json APPLY=1   # collapse them
```

It keeps the one the live window names, never deletes the only record of an
admission, and never touches a receipt, so the count beat 12 just read does not
move.

---

## Still open, both decisions rather than tasks

**The demo token is the repo's published default.** `Bearer
anbu-demo-family-token` is in `infra/deploy_cloud_run.sh` on a public repo, and
it now also sits in the scheduler header. It opens the tick and the
family-session endpoints. The blast radius is bounded (one message per window
per day, and only while a window is open) but anyone reading the repo holds it.
Fix is three steps that already have wiring: a random value in `.env`, redeploy,
re-run `./infra/schedule_recovery_tick.sh` so the job picks up the new header.

**The captions have not met the real transport.** Deployed and unit tested, but
no captioned message has yet arrived on a handset. The warm-up voice note in the
pre-roll settles this and the inbound half at the same time.

---

*Every claim on this sheet was reproduced against the deployed code path: the
reader, the WhatsApp document lane, the comms gate, the dedupe rules, and the
scheduler calling the tick (200s from `Google-Cloud-Scheduler` in the Cloud Run
request log).*
