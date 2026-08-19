# Proposal — P6: stateful simulated adjudicator + agent reaction

**Status:** proposal. No code written. Gated on approval.
**Untouched:** triage severity/ranking, WhatsApp policy, Ed25519 chain, existing
receipts, and the existing SLA clock math (`service.sla_deadline` is reused
as-is, not modified).

---

## Why this is the #1 value item

The 40% Operational Utility axis is capped today by one sentence in our own
docs: *"only the counterparty's response is mocked."* The mock currently returns
`received: true` and nothing else, so the loop that visibly completes is
`assemble → submit → (agent advances stages by hand)`. The agent is running a
fixed pipeline; nothing comes *back* that it must think about.

The fix is not to fake an insurer. It is to make the simulated counterparty
return something with **structure the agent has to react to** — and to let the
agent visibly do the reacting. That is the difference between a workflow and an
agent, and it is the one thing that raises this axis without dishonesty.

---

## 1. The adjudicator (deterministic, in code — not a prompt)

New module `anbu_care/tpa/adjudicator.py`. Pure functions over the stored packet
and policy. **No model involved**, so it runs identically every time and the
demo is reproducible.

```
adjudicate(packet, policy, attached_kinds) -> Adjudication
```

Four outcomes, decided in this order:

| Outcome | Condition | Cites |
|---|---|---|
| **DENY** | policy lapsed, or every claimed line is non-covered | the clause that excludes it |
| **QUERY** | a required document class is missing | exactly which class, and what it is needed for |
| **PARTIAL** | covered, but sub-limit / sum-insured math reduces the payable amount | line-by-line math, per rule |
| **PASS** | fully payable within limits | the limits it cleared |

`QUERY` is checked before `PARTIAL` deliberately: a real TPA cannot compute a
final payable figure while a required document is missing, and pretending
otherwise would be the dishonest shortcut.

**Sub-limit math (the part that makes PARTIAL real).** Requires seeding
realistic caps, which the demo policy currently lacks (`sub_limits_inr` is `{}`,
so this branch can never fire today):

```
room_rent      : 1% of sum insured per day
cardiac_icu_room: 2% of sum insured per day
```

On the existing demo numbers (sum insured ₹5,00,000; ICU ₹96,000 over 3 days):
cap = ₹10,000/day × 3 = ₹30,000 → **₹66,000 disallowed**, cited as
`"ICU charged 96,000 over 3 days; sub-limit 2% of sum insured per day = 10,000/day = 30,000; disallowed 66,000"`.
That is a number a judge can check on screen against the policy.

**Determinism:** same packet + same policy → same outcome, always. Keyed off
packet content, not randomness, exactly like the existing `_simulated_ack`.

---

## 2. Statefulness

An adjudication is appended to a per-submission history, so a QUERY that is
answered can be re-adjudicated and the *transition* is visible:

```
submit → QUERY (missing discharge summary)
       → agent attaches it, resubmits
       → PARTIAL (₹66,000 disallowed on the ICU sub-limit)
```

Each adjudication writes a `claim.adjudicated` receipt on the existing case
chain — new receipt *kind*, no change to chain mechanics.

New `ClaimStage` members: `QUERIED`, `PARTIALLY_APPROVED`. Existing members
unchanged.

---

## 3. The agent reaction — the actual centerpiece

New tool on the insurer-liaison agent only:

```
respond_to_query(case_id, submission_id, attach_document_ids) -> re-adjudicated result
```

Flow the agent performs itself, unscripted:

1. `submit_claim` returns `outcome: QUERY`, `missing: ["discharge_summary"]`.
2. Agent reads the parent's stored documents, finds the discharge summary.
3. Calls `respond_to_query` with that document id.
4. Gets `PARTIAL` back with the sub-limit math.
5. Reports the disallowed amount to the family **before** it is a surprise.

Step 5 is the product argument: the family learns about ₹66,000 of exposure from
their own coordinator, not from a rejection letter.

**Instruction constraints (added to insurer-liaison):**
- The adjudicator is SIMULATED; say so every time an outcome is reported.
- Never describe a simulated PASS as an insurer having approved anything.
- Only attach documents that exist in the parent's record.
- STEP_UP stays pre-submission enrichment. Responding to a QUERY is **not**
  STEP_UP and must not be described as preventing a denial.

---

## 4. SLA behaviour

`service.sla_deadline()` is reused **unchanged**. A QUERY starts a *second*
tracked window (the response clock) using the same function; the original
cashless/reimbursement deadline keeps running and is still reported. Nothing in
the existing clock math changes — this is an additional field, not a rewrite.

Both clocks appear in the demo, against real wall time.

---

## 5. Diff surface

**New**
```
anbu_care/tpa/__init__.py
anbu_care/tpa/adjudicator.py        deterministic outcome engine + sub-limit math
tests/test_adjudicator.py
```

**Modified**
```
anbu_care/schemas.py                +AdjudicationOutcome, +Adjudication,
                                    +ClaimStage.QUERIED/PARTIALLY_APPROVED,
                                    +InsurancePolicy sub-limit seeding support
anbu_care/tools/insurer_tools.py    submit_claim calls adjudicate();
                                    +respond_to_query tool
anbu_care/agents/insurer_liaison.py +QUERY/PARTIAL reaction instructions
anbu_care/server.py                 seed realistic sub-limits on the demo policy
scripts/demo_run.sh                 new beat
docs/DEMO_SCRIPT.md                 new beat + label placement
DISCLOSURE.md                       framing note (below)
```

**Explicitly untouched:** `triage/`, `comms/`, `provenance/`, `service.sla_deadline`.

---

## 6. Honesty framing

The endpoint stays labelled SIMULATED everywhere: tool results, receipts, agent
speech, demo narration, README.

One framing risk worth naming: a richer adjudicator *looks* more like a real
integration, so the SIMULATED label has to work harder. Mitigation — the
adjudicator names itself in every payload
(`"adjudicator": "SIMULATED — deterministic local rules, not an insurer"`), and
`DISCLOSURE.md` gains a line stating that the decision rules are our own
construction from published IRDAI sub-limit conventions, not any insurer's
actual adjudication logic.

---

## 7. Tests (est. +16, taking the suite to ~108)

Per branch: PASS, PARTIAL, QUERY, DENY — each asserting the cited reason, not
just the enum. Sub-limit math exact on the demo numbers (₹66,000). Determinism
(same packet twice → identical outcome). QUERY→respond→re-adjudicate transition.
QUERY before PARTIAL ordering. Both SLA clocks tracked; existing clock maths
unchanged (regression guard). `claim.adjudicated` receipts chain and verify.
Agent-reaction path at the tool layer with no model in the loop. Attaching a
non-existent document is refused.

---

## 8. Demo beat

Slots into the existing claim section: **submit → QUERY → agent fetches the
discharge summary → resubmit → PARTIAL with ₹66,000 disallowed → family told
before it's a surprise.** Roughly 35 s, and it is the only beat where the system
visibly *reacts* rather than proceeds.

---

## Open question for you

**Should DENY be reachable in the demo path?** It is the most dramatic outcome
and the easiest to stage, but a simulated denial is also the outcome most likely
to be misread by a judge as a real adjudication. My recommendation: implement
and test DENY, but keep the narrated demo on QUERY→PARTIAL, which shows the
reaction *and* the math without inviting that misreading.
