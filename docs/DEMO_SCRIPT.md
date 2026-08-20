# Demo script — ~4 minutes, unedited

One take, one terminal, one browser tab. Every beat below is produced by
`./scripts/demo_run.sh`, which is idempotent: it seeds fresh synthetic cases each
run and never mutates a chain from a previous run.

**Live URL:** https://anbu-care-37j4eofpwq-el.a.run.app

---

## Pre-flight — run this on the actual recording machine

Do the whole thing as a human dry-run, not a skim. Every bug that has bitten this
project late was found by someone actually clicking, not by a test.

### 1. Credentials (they expire fast on this org)

```bash
gcloud auth login
gcloud auth application-default login
make verify-stack          # all five checks must be green
```

`.env` pins `GOOGLE_CLOUD_QUOTA_PROJECT`, so an ADC re-login resetting the quota
project to your personal default no longer breaks anything. Verify anyway.

- [ ] `verify-stack` green, and **signing key reports stable** — an ephemeral key
      makes the whole verify beat meaningless.

### 2. The service is up and public

```bash
URL=https://anbu-care-37j4eofpwq-el.a.run.app
curl -s $URL/api/healthz                     # 200, model gemini-3.5-flash
curl -s -o /dev/null -w '%{http_code}\n' $URL/app   # 200
```

- [ ] Liveness is **`/api/healthz`**. The bare `/healthz` is reserved by Google
      Front End and 404s in Cloud Run — **do not put it on camera.**

### 3. Both auth proofs, from the terminal you will actually use

```bash
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/parents/whatever    # expect 401
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/cases/whatever/verify # expect 200
```

- [ ] 401 and 200. If either is wrong, **stop** — beat 8 is the phase's centrepiece.

### 4. Fresh state, seeded before you record

```bash
./scripts/demo_run.sh --reset      # clear anything a previous run left
./scripts/demo_run.sh              # full 8 beats; must exit 0
```

- [ ] Exit code 0 and all eight beats printed.
- [ ] **Do NOT run `--reset` again before or during the take.** It deletes the
      cases a judge might verify from the video afterwards.
- [ ] Note the two case ids it prints at the end — the valid one and the tampered
      one. Read them out or show them; they stay live-verifiable.
- [ ] The canonical case already staged for this take is **case-da1c2cb6db** (tampered
      twin **case-a7cf9fa613**). The screenshots in docs/design/mobile and the curls in
      docs/takes/backup-take-spine.txt all use that same id. If you re-seed,
      update those three together or they will disagree on camera.
- [ ] On the Claim tab, the line between QUERY and PARTIAL should read
      "discharge summary added to the record at HH:MM, then resubmitted as
      attempt 2". That is what stops Record and Claim looking contradictory.
- [ ] On the Record tab, only **HbA1c** should read "new and abnormal". The
      other analytes moved inside the 10% band and read as variation.

### 5. Walk the dashboard by hand

Open `$URL/app` in the browser you will record, at the window size you will
record.

- [ ] "Seed a synthetic episode" works.
- [ ] Before signing in, a content tab shows **401 THE CASE TRAIL IS CREDENTIALED**.
- [ ] Sign in; Now / Arrival / Routing / Record / Claim / Audit all render.
- [ ] **Scroll a long tab (Record) and confirm the nav is still reachable** — a
      sticky-positioning bug lived exactly there.
- [ ] Audit tab → **"Prove the gate"** prints `401 / 200`.
- [ ] `SYNTHETIC — DEMO DATA` visible on every clinical view.
- [ ] Ingest both lab reports if you want the Record tab populated:
      `uv run python scripts/demo_support.py ingest-doc $URL <parent_id> assets/synthetic/lab_report_mar2026.png`
      (and `…aug2026.png`) — then confirm LDL reads *consistent with baseline*.

### 6. Recording hygiene

- [ ] Terminal font large enough that hashes and HTTP codes are legible on playback.
- [ ] Say **"synthetic"**, **"simulated"** and **"seeded snapshot"** out loud at
      least once each. The honesty framing is part of the architecture case, not a
      disclaimer to rush.
- [ ] Say **"received"**, never "detected", when the intake signal arrives.
- [ ] Never say STEP_UP "prevents denials" — it is pre-submission enrichment.
- [ ] Nothing about Gemma or a second model. It is not running.

### 7. Backup take

Record a **second pass immediately after the first**, before changing anything.

Beats 3, 5, 7, 8, 9 and 10 are deterministic and identical every run — only the
beat-6 agent call goes through a model and can phrase things differently or run
slow. If that beat misbehaves, cut to the backup rather than re-seeding.

`docs/takes/backup-take-spine.txt` is the always-submittable transcript fallback
if recording fails entirely.

---

## Beat sheet

| Time | Beat | Command | Point at |
|---|---|---|---|
| 0:00–0:20 | **The question** | — | "If something happens to my parent right now, who makes sure the right decisions get made?" Every incumbent answers with a human coordinator. |
| 0:20–0:35 | **Onboarding** | `curl -sX POST $URL/api/demo/seed` | A synthetic Thoothukudi parent. Say **"synthetic"**. |
| 0:35–1:15 | **Multimodal living record** ⭐ | `demo_support.py ingest-doc … mar2026.png` then `…aug2026.png` | LDL unchanged → *consistent with baseline*; HbA1c 7.1→8.4 → *new and abnormal*. Then the **ground-truth stored count**. |
| 1:15–1:35 | **A signal ARRIVES** | `curl -sX POST $URL/api/intake-signal` | `SIMULATED INTAKE SIGNAL — received from an external channel, not detected by Anbu Care`. Say **received**, never *detected*. |
| 1:35–2:15 | **HIGH holds vs "just gas"** ⭐ | (same response) | Severity HIGH; 2.2 km Sacred Heart over 0.8 km Idhayalaya, cited on **cashless network**. `SEEDED SNAPSHOT — NOT A LIVE FEED` on screen. |
| 2:15–2:50 | **WhatsApp boundary** ⭐ | agent `/run`, then `demo_support.py block-receipt` | Agent refuses; then bypass the agent and the **code** blocks it anyway. |
| 2:50–3:25 | **Claim QUERY → resolved → PARTIAL** ⭐ | `demo_support.py claim-flow $CASE $PARENT` | **₹66,000** told now, not at settlement. Say **SIMULATED**. |
| 3:25–3:50 | **Public where it proves, private where it reveals** ⭐ | the two curls | `/api/parents/{id}` → **401** · `/api/cases/{id}/verify` → **200**. |
| 3:50–4:20 | **Tamper** ⭐ | `demo_support.py tamper $THROW` | `verified: false, broken_at_seq: 1`; the judge-facing case stays true. |
| 4:20–4:30 | **Not process memory** | `demo_support.py reload-verify $CASE` | Fresh OS process, straight from Firestore. |
| 4:30–4:40 | **Close** | — | What is real, what is simulated. Say both. |

---

## The beats that carry the score

### Beat 3 (0:35–1:15) — multimodal into a *living* record

Two synthetic lab reports five months apart. Gemini vision extracts every
analyte with its unit and flag — say out loud that nothing is typed in by hand.

The line to land is not "it read the image". It is what the record does with the
second reading:

```
LDL Cholesterol=165 : unchanged from a previous reading — consistent with baseline
HbA1c=8.4           : changed from 7.1 and flagged abnormal — new and abnormal
```

Both are flagged "high" on the page. Only one of them is news. That distinction
is what makes it a health *record* rather than a pile of parsed documents, and
it is the Best Multimodal UX argument.

**Then point at the ground-truth line:**

```
GROUND TRUTH — documents actually stored for this parent: 2
reported status 'ingested' vs stored count 2: consistent
```

That count is read back from the service, not from what the agent said. An
earlier build had an agent announce "successfully ingested into her health
record" while storing nothing — this line is what makes such a claim visibly
false on camera, and it is why the agents are now instructed never to report an
ingest without a `status: "ingested"` tool result.



### Beat 5 (1:35–2:15) — routing that explains itself

The caller says **"she says it's probably just gas."** Severity still returns
`HIGH`, because the rule that escalates a red flag is Python, not a prompt.

Then the routing: it recommends **Sacred Heart at 2.2 km over Idhayalaya at
0.8 km** — and the explanation cites **only the term that actually differed**:

> "The extra distance was accepted because Sacred Heart Hospital is empanelled
> with Star Health and Idhayalaya Heart Centre is not, so this keeps the
> admission cashless."

**Point at what it does not say.** Capability is tied at 1.00 between those two
hospitals, and the explanation does not claim a capability edge. An explanation
that lists a tie as a reason has stopped being an explanation.

Also on screen: `"knowledge_base": "SEEDED SNAPSHOT — NOT A LIVE FEED"`. Say it
out loud. It is returned on every triage call, not added for the demo.

### Beat 6 (2:15–2:50) — the guardrail is code, not a prompt

Two halves, and the second is the one that matters.

**4a.** Ask the deployed agent to relay `"troponin I is 0.94 ng/mL"` framed as
"just logistics". The agent calls `check_message_allowed` and refuses.

**4b.** Now bypass the agent entirely and call the send tool directly. It is
still blocked — the gate classifies the *content*, not the caller's claim about
it — and the blocked attempt is written to the receipt chain as
`comms.blocked`.

**4c — the phone.** With `ANBU_WHATSAPP_MODE=twilio`, send the *logistics*
message and let it land on your handset on camera. Hold the phone up: the
permitted message arrives, and the disguised clinical one from 4b never did.
Real delivery and the boundary, in the same beat.

Say the acceptance honestly too, if asked whether it "delivered": the API
confirms Twilio **accepted** the message; handset confirmation arrives over a
status callback this demo does not run. The receipt says acceptance, not receipt.

Say the reach honestly, once: **"this is the Twilio WhatsApp sandbox — real
delivery to a number that opted in. Reaching any number needs Meta business
verification and template approval, about ten to fifteen business days."** Do not
imply production reach.

Pre-flight for this beat: send `join <your-code>` to **+1 415 523 8886** from the
handset, confirm the reply, and do a throwaway send *before* recording — the
sandbox opt-in expires and the 24-hour freeform window has to be open.

> Line to say: *"An agent that is merely told not to leak a lab value is not a
> control. This holds when the model is not the thing enforcing it."*

### Beat 7 (2:50–3:25) — the claim comes back queried, and the query gets resolved

Everything before this is the system *proceeding*. This is the only beat where
something comes back that the agent has to think about.

```
[1] SUBMITTED   -> QUERY    (SIMULATED — deterministic local rules, not an insurer)
    · required document not attached: discharge summary
    original SLA: 59 min remaining; query response clock started, both now running
[2] The agent reacts: looking for the queried document on file…
    found and attached: doc-33f3678e8b (kind=discharge_summary)
[3] RESUBMITTED -> PARTIAL
    · cardiac_icu_room: claimed INR 96,000, allowed INR 30,000, disallowed INR 66,000
      (sub-limit: 2% of sum insured per day = INR 10,000/day x 3 day(s) = INR 30,000)

    DISALLOWED INR 66,000   <- family told now, not at settlement
```

**Say the arithmetic out loud and invite the check.** Sum insured ₹5,00,000; the
conventional ICU cap is 2% per day = ₹10,000/day; the stay is 19–22 Aug = 3 days;
so ₹30,000 of a ₹96,000 ICU bill is payable and **₹66,000 is not**. Nothing is
seeded to produce that number — it falls out of the convention applied to the
policy on screen. A judge can do it in their head, and it ties out.

**The product line:** the family hears about ₹66,000 of exposure from their own
coordinator, now — not from a settlement letter later.

**Say SIMULATED.** The counterparty is not real and the payload says so on every
line. What *is* real: the packet, the policy arithmetic, the SLA clocks, and the
receipts.

**One thing to be precise about on camera.** This beat runs at the tool layer so
every take is identical. The insurer-liaison agent makes exactly these calls
itself — say "this is the same sequence the agent performs, replayed so the take
is reproducible", not "watch the agent react". If a judge wants to see the agent
do it live:

```bash
# coordinator → insurer_liaison → evidence → submit(QUERY)
#   → onboarding (finds the document) → respond_to_query(PARTIAL) → SLA
curl -s -X POST $URL/apps/anbu_care/users/demo/sessions -H 'content-type: application/json' -d '{}'
# then POST /run with the discharge instruction — see scripts/ for the shape
```

Two things worth pointing at if a judge is engaged:

- `./scripts/demo_run.sh --branches` shows **all four outcomes live** — PASS,
  PARTIAL, QUERY, DENY — not three plus a unit test.
- The agent will not invent the missing document. If the discharge summary is
  genuinely not on file, it says the query cannot be resolved. That is the same
  guarantee as the ingestion ground-truth count, on a different path.

### Beat 8 (3:25–3:50) — public where it proves, private where it reveals

The line that makes the WhatsApp gate credible. If clinical data "lives somewhere
protected", that place has to actually refuse people.

```
/api/parents/{id}        -> HTTP 401   (denied — this is where lab values live)
/api/cases/{id}/verify   -> HTTP 200   (open by design)
```

**Say why the second one is open**, because it is the non-obvious half:
verification proves the record was not altered *without revealing what it says*.
It returns hashes, a boolean and a failure mode. A receipt chain only means
something if someone can check it without asking our permission.

The dashboard has a **"Prove the gate"** button on the Audit tab that runs both
calls in-page and prints `401 / 200`. Use that if you are on screen rather than in
a terminal.

If a judge asks about the credential: it is published in the README on purpose.
Secrecy is not the claim — server-side enforcement is. They can lift the token out
of the page and removing it still produces a 401.

### Beat 9 (3:50–4:20) — tamper

Uses a **separate throwaway case**, so the case you just showed a judge stays
valid on `/verify` — check both on screen.

The edit rewrites `payload.severity` from `HIGH` to `LOW` and leaves the hash
and signature untouched, which is what a silent after-the-fact edit looks like.
The unauthenticated endpoint reports:

```json
{ "verified": false, "broken_at_seq": 1,
  "reason": "payload does not hash to the recorded hash — content was altered" }
```

The verifier distinguishes **four** failure modes, because in a dispute they
mean different things: sequence gap (a receipt was dropped), `prev_hash`
mismatch (the chain was re-linked), hash mismatch (a payload was edited),
signature mismatch (re-signed with another key). This beat shows the third.

---

## Closing lines (say both)

**Real and demonstrable:** multimodal document parsing, severity classification
and hospital routing, the WhatsApp compliance boundary, claim packet assembly
with coverage and sub-limit checks, SLA tracking against the IRDAI 2024 Master
Circular's 1-hour cashless and 30-day reimbursement clocks, and the signed
tamper-evident receipt chain.

**Simulated, and labelled everywhere it appears:** the insurer/TPA response
(no production API exists in this window), the hospital knowledge base (a dated
seeded snapshot, not a live capability feed), and WhatsApp delivery (sandbox —
production template approval takes 10–15 business days).

---

## After the take

```bash
./scripts/demo_run.sh --reset     # leaves no half-seeded state
```

Judges can still reproduce everything themselves from the README quick-start.
