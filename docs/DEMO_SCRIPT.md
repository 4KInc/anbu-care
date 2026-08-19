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
curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/hospitals | head -c 80
```

- [ ] `verify-stack` green, and **signing key reports stable** — an ephemeral key
      makes the verify beat meaningless.
- [ ] Cloud Run logs open in a second tab, filtered to `anbu-care`.
- [ ] Terminal font large enough that hashes are legible on playback.
- [ ] Say the words "synthetic", "simulated TPA", and "seeded snapshot" out loud at
      least once each — the honesty framing is part of the architecture case.
- [ ] `/healthz` returns 404 on Cloud Run by design (GFE reserves it). Do not
      show it. Use `/api/hospitals` if you need a liveness beat.

**Backup take:** record a second pass immediately after the first, before
changing anything. If the live agent call in Beat 3a is slow or the model
phrases something oddly, the deterministic beats (2, 3b, 4, 5, 6) are identical
every run — you can cut to the backup without re-seeding.

---

## Beat sheet

| Time | Beat | Command | Point at |
|---|---|---|---|
| 0:00–0:25 | **The question** | — | "If something happens to my parent right now, who makes sure the right decisions get made?" Name the competitors: all human-coordinator models. |
| 0:25–0:45 | **Onboarding** | `curl -sX POST $URL/api/demo/seed` | A synthetic Thoothukudi parent with history, policy, and per-purpose consent. Say "synthetic". |
| 0:45–1:35 | **Explainable routing** ⭐ | `curl -sX POST $URL/api/intake …` | **The strongest 40 s of the demo.** See below. |
| 1:35–2:20 | **The WhatsApp boundary** ⭐ | agent `/run`, then `demo_support.py block-receipt` | Agent refuses; then bypass the agent and watch the *code* block it anyway. |
| 2:20–2:50 | **Anyone can verify** | `curl -s $URL/api/cases/$CASE/verify` | Unauthenticated. No login. Judges can run it themselves. |
| 2:50–3:30 | **Tamper** ⭐ | `demo_support.py tamper $THROW` | Silent edit → chain names the exact failure mode and the sequence number. |
| 3:30–3:50 | **Not process memory** | `demo_support.py reload-verify $CASE` | Fresh OS process, straight from Firestore, still verifies. |
| 3:50–4:00 | **Close** | — | What is real, what is simulated. Say both. |

---

## The three beats that carry the score

### Beat 2 (0:45–1:35) — routing that explains itself

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

### Beat 3 (1:35–2:20) — the guardrail is code, not a prompt

Two halves, and the second is the one that matters.

**3a.** Ask the deployed agent to relay `"troponin I is 0.94 ng/mL"` framed as
"just logistics". The agent calls `check_message_allowed` and refuses.

**3b.** Now bypass the agent entirely and call the send tool directly. It is
still blocked — the gate classifies the *content*, not the caller's claim about
it — and the blocked attempt is written to the receipt chain as
`comms.blocked`.

> Line to say: *"An agent that is merely told not to leak a lab value is not a
> control. This holds when the model is not the thing enforcing it."*

### Beat 5 (2:50–3:30) — tamper

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
