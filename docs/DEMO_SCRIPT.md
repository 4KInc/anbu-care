# Demo script — ~4 minutes, unedited

One take, one terminal, one browser tab. Every beat below is produced by
`./scripts/demo_run.sh`, which is idempotent: it seeds fresh synthetic cases each
run and never mutates a chain from a previous run.

**Live URL:** https://anbu-care-37j4eofpwq-el.a.run.app

---

## Pre-flight (do this before recording)

```bash
gcloud auth application-default login        # ADC expires fast on this org
make verify-stack                            # all five checks must pass
./scripts/demo_run.sh --reset                # clear any prior demo state
curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/healthz
```

- [ ] `verify-stack` green, and **signing key reports stable** — an ephemeral key
      makes the verify beat meaningless.
- [ ] Cloud Run logs open in a second tab, filtered to `anbu-care`.
- [ ] Terminal font large enough that hashes are legible on playback.
- [ ] Say the words "synthetic", "simulated TPA", and "seeded snapshot" out loud at
      least once each — the honesty framing is part of the architecture case.
- [ ] Liveness is `/api/healthz`. The bare `/healthz` path is reserved by
      Google Front End and 404s in Cloud Run — do not use it on camera.

**Backup take:** record a second pass immediately after the first, before
changing anything. If the live agent call in Beat 3a is slow or the model
phrases something oddly, the deterministic beats (2, 3b, 4, 5, 6) are identical
every run — you can cut to the backup without re-seeding.

---

## Beat sheet

| Time | Beat | Command | Point at |
|---|---|---|---|
| 0:00–0:20 | **The question** | — | "If something happens to my parent right now, who makes sure the right decisions get made?" Competitors are all human-coordinator models. |
| 0:20–0:35 | **Onboarding** | `curl -sX POST $URL/api/demo/seed` | A synthetic Thoothukudi parent with history, policy, per-purpose consent. Say "synthetic". |
| 0:35–1:20 | **Multimodal living record** ⭐ | `demo_support.py ingest-doc … lab_report_mar2026.png` then `…aug2026.png` | Gemini reads two lab reports; LDL unchanged reads as baseline, HbA1c 7.1→8.4 reads as new and abnormal. Then the **ground-truth document count**. |
| 1:20–2:05 | **Explainable routing** ⭐ | `curl -sX POST $URL/api/intake …` | Holds HIGH against "probably just gas"; cites only the term that differed. |
| 2:05–2:45 | **The WhatsApp boundary** ⭐ | agent `/run`, then `demo_support.py block-receipt` | Agent refuses; then bypass the agent and watch the *code* block it anyway. |
| 2:45–3:20 | **Claim queried → agent reacts** ⭐ | `demo_support.py claim-flow $CASE $PARENT` | The one beat where the system *reacts* rather than proceeds. See below. |
| 3:20–3:35 | **Anyone can verify** | `curl -s $URL/api/cases/$CASE/verify` | Unauthenticated. Judges can run it themselves. |
| 3:35–4:05 | **Tamper** ⭐ | `demo_support.py tamper $THROW` | Silent edit → names the exact failure mode and sequence number. |
| 4:05–4:15 | **Not process memory** | `demo_support.py reload-verify $CASE` | Fresh OS process, straight from Firestore, still verifies. |
| 4:15–4:25 | **Close** | — | What is real, what is simulated. Say both. |

---

## The four beats that carry the score

### Beat 2 (0:35–1:20) — multimodal into a *living* record

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



### Beat 3 (1:20–2:05) — routing that explains itself

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

### Beat 4 (2:05–2:45) — the guardrail is code, not a prompt

Two halves, and the second is the one that matters.

**4a.** Ask the deployed agent to relay `"troponin I is 0.94 ng/mL"` framed as
"just logistics". The agent calls `check_message_allowed` and refuses.

**4b.** Now bypass the agent entirely and call the send tool directly. It is
still blocked — the gate classifies the *content*, not the caller's claim about
it — and the blocked attempt is written to the receipt chain as
`comms.blocked`.

> Line to say: *"An agent that is merely told not to leak a lab value is not a
> control. This holds when the model is not the thing enforcing it."*

### Beat 5 (2:45–3:20) — the claim comes back queried, and the agent reacts

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

Two things worth pointing at if a judge is engaged:

- `./scripts/demo_run.sh --branches` shows **all four outcomes live** — PASS,
  PARTIAL, QUERY, DENY — not three plus a unit test.
- The agent will not invent the missing document. If the discharge summary is
  genuinely not on file, it says the query cannot be resolved. That is the same
  guarantee as the ingestion ground-truth count, on a different path.

### Beat 7 (3:35–4:05) — tamper

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
