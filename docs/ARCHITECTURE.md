# Architecture

Design decisions, and why each was made.

## The shape

A coordinator (`anbu_care/agent.py`) holding five ADK sub-agents, each with its
own tool module and no access to any other agent's tools. Underneath them sits a
deterministic layer that the agents call but cannot overrule.

```
  agents/         conversation, delegation, explanation      ← LLM
  tools/          the actions each agent may take            ← LLM calls these
  ────────────────────────────────────────────────────────
  triage/         severity rules, hospital ranking           ← plain Python
  comms/          WhatsApp message policy                    ← plain Python
  service.py      case state, SLA clocks                     ← plain Python
  provenance/     hash chain, signing, storage               ← plain Python
```

The line matters. Everything below it is testable without a model, runs the same
way every time, and is where the guarantees live.

## Why severity is not a prompt

A red-flag symptom must escalate on *every* run. Not most runs, and not the runs
where the caller sounds worried.

`anbu_care/triage/severity.py` is a rule table. Chest pain escalates whether or
not the caller adds "she says it's probably just gas". The triage agent is told
in its instructions never to soften what the engine returned, and
`test_red_flag_wins_even_when_wrapped_in_reassuring_words` holds that line in CI
rather than in a prompt.

The one genuinely interesting rule is history-sensitivity. Dizziness in a patient
with a prior MI is not the same complaint as dizziness in a patient without one,
so certain MEDIUM symptoms carry a "becomes high-risk given this history" marker
and escalate when the parent's record matches.

## Why the routing explanation only cites what differed

The explainable routing decision is the strongest "reasoning, not lookup" moment
in the demo, and a bad explanation would undo it.

An earlier version said *"capability scored 1.00 here versus 1.00 there, and it
is in-network"* — citing a tied term as though it were a reason. `_explain` in
`anbu_care/triage/routing.py` now names only the terms that genuinely differ,
and when nothing differs it says so and reports the margin instead. An
explanation that lists a tie as a reason has stopped being an explanation.

## Why the WhatsApp gate classifies content, not intent

Meta's healthcare policy and India's DPDP Act make this a legal boundary, not a
style preference. `anbu_care/comms/policy.py` runs regex classifiers over the
message body and blocks anything carrying a diagnosis, a lab value, a clinical
measurement, or prescription specifics — regardless of what the caller declared
the message class to be.

A message declared `logistics` that reads "just logistics: troponin 0.94 ng/mL"
is blocked. An agent that is merely *instructed* not to leak a lab value is not
a control; a function that inspects the bytes before they leave is.

Two consequences follow, and both are deliberate:

- **Blocked sends are written to the chain.** A block is evidence the boundary
  held, so `comms.blocked` receipts sit alongside `comms.sent` ones.
- **The demo climax is not a clinical message.** Because clinical data cannot go
  over WhatsApp, the WhatsApp beats are logistics and billing. The ECG reading
  lives in the dashboard. The demo climax is the explainable routing decision
  and the signed receipt — not a message the design correctly prohibits.

Consent is checked per purpose, not per contact. DPDP requires purpose-specific,
timestamped opt-in; a blanket checkbox is not sufficient, so
`FamilyContact.consents` is a map of purpose to timestamp.

### Three directions, not two

Purposes carry their direction in the name, because a purpose that did not say
which way it pointed is what caused a real defect here once: inbound wellbeing
originally reused `status_updates`, so agreeing to *receive* updates about your
parent silently made you eligible to *file reports* about her.

| Direction | Purposes | Whose agreement |
|---|---|---|
| outbound — things we may send you | `admission_alerts`, `status_updates`, `billing_updates`, `claim_updates`, `outbound_notify` | the contact's |
| inbound — things you may send us | `inbound_wellbeing` | the contact's |
| **disclosure — showing the record itself** | `emergency_clinical_share` | **the parent's** |

The third direction is not a fifth outbound purpose. The first two govern
*messages* to or from a person and are held by that person about their own
traffic. Disclosure governs handing someone the record, and its subject is
different: a son agreeing to receive claim updates has not agreed that a
stranger in a corridor may read his mother's allergies. So it lives on
`ParentProfile.disclosure_consents`, and a test asserts it is unreachable from
either of the other two sets.

## Why the receipt chain is signed as well as hashed

A hash chain alone proves internal consistency: nobody edited entry 3 and left
entries 4–10 intact. It does not prove who wrote entry 3.

Each receipt carries an Ed25519 signature over the same canonical bytes the hash
covers, plus the public key it was signed with. `verify_chain` distinguishes
four failure modes, because in a dispute they mean different things:

| Failure | What it means |
|---|---|
| sequence gap | a receipt was dropped |
| `prev_hash` mismatch | the chain was re-linked around something |
| hash mismatch | a payload was edited in place |
| signature mismatch | a receipt was re-signed with a different key |

`canonical_json` — sorted keys, no incidental whitespace, UTC timestamps at
microsecond precision — exists because two processes must produce byte-identical
input for the same logical payload, or verification means nothing.

**The signing key must be stable.** An ephemeral key means receipts written
before a restart stop verifying. `load_signer` mints an ephemeral dev key when
`ANBU_SIGNING_KEY_B64` is unset and flags it in `/api/healthz`, in
`verify_case_chain`, and in the demo output. `infra/deploy_cloud_run.sh` refuses
to deploy without one.

## Why Firestore is single-table PK/SK

One case's entire chain is a single range read: `pk = CASE#<id>`,
`sk >= RECEIPT#` — no joins, no fan-out, no ordering ambiguity. Receipt sequence
numbers are zero-padded (`RECEIPT#000007`) so lexicographic order is numeric
order.

```
PK                 SK                  entity
PARENT#<pid>       PROFILE             ParentProfile
PARENT#<pid>       DOC#<doc_id>        ParsedDocument
CASE#<cid>         META                Case
CASE#<cid>         PACKET#<pkt_id>     ClaimPacket
CASE#<cid>         SUBMISSION#<sub_id> ClaimSubmission
CASE#<cid>         RECEIPT#000000      Receipt
CASE#<cid>         RECEIPT#000001      Receipt
```

`append_receipt` reads the stored chain before appending, so `seq` and
`prev_hash` come from what is actually persisted rather than an in-process
counter. Two workers appending concurrently would collide on `seq` — acceptable
at hackathon scale, and the fix (a Firestore transaction on the case document)
is a known next step rather than an oversight.

A `MemoryStore` implements the same interface, which is why 1258 tests run with no
GCP access at all.

## Why the TPA is simulated, and says so

There is no production insurer or TPA API to integrate against inside this
window. Pretending otherwise would be the single most damaging thing in a live
demo.

So: `submit_claim` returns `simulated: true`, a `SIMULATED TPA` notice, and a
`tpa_reference` derived deterministically from the packet id (a random reference
would make demo runs unreproducible). The liaison agent's instructions require it
to say the counterparty response is simulated every time it reports one.

What is *not* simulated: packet assembly from stored evidence, the coverage and
sub-limit checks against the real policy record, and the SLA deadlines, which are
real timestamps checked against real wall time.

## Why the trace can only render receipts

The agency was always real — the adjudicator raises a query, the agent gathers
what was asked for, the packet is re-checked, and the loop stops when the gate
clears. What was missing was any way to *watch* it, and `anbu_care/trace/` is
that view.

It is deliberately the dullest module in the repository, because the
interesting property is what it refuses to do: **one step per receipt, never
more and never fewer**. No summarising two receipts into a tidier beat, no
inferring an unrecorded step because the story reads better with it, no
reordering into the sequence a narrator would prefer. `len(steps)` equals
`receipt_count` or the test fails, and every step carries the `seq` and hash of
the receipt it came from.

A trace that could invent a step would be a story about an agent rather than a
record of one, and the difference is the whole argument.

Two failure directions are held, not one. The obvious one is inventing detail.
The quieter one is hiding it: an unrecognised receipt kind still renders rather
than silently vanishing, and a tampered chain reports `chain_verified: false`
while still showing every step it has.

This constraint earned its keep immediately. On the deployed demo path the
trace showed the query and the answer with nothing in between, because
`respond_to_query` attached the document in place and wrote no receipt. The
agent's actual decision was real and invisible. The fix was a
`claim.query_answered` receipt in the claim lane — the step became visible
because it started being **recorded**, not because the renderer started
inferring it.

## Agent boundaries

| Agent | Tools | Cannot |
|---|---|---|
| coordinator | `verify_case_chain`, `get_case_trail` | triage, message, or file a claim |
| onboarding | profile, medications, policy, contacts, document ingestion | send messages, file claims |
| triage | `run_triage`, `list_known_hospitals` | send messages, touch claims |
| evidence | `assess_claim_packet`, `enrich_claim_packet` | submit anything |
| insurer liaison | assemble, submit, advance stage, check SLA | send messages, triage |
| whatsapp | list templates, send update, check allowed | read clinical data, file claims |

Five agents is a lot to keep non-brittle in a live run — that is the honest
exposure on the Architecture criterion. The mitigation is that each agent's
consequential work is a deterministic function underneath it, so an agent that
gets confused produces a worse *explanation*, not a worse *decision*.

## Known gaps

- **Concurrent receipt appends** can collide on `seq`. Needs a Firestore
  transaction on the case document.
- **Memory Bank recall is load-bearing for language and not for reply mode.**
  `language` is observed on her own inbound messages, stored, and read back to
  decide what the next admission's first check-in is written in. `reply_mode` is
  observed and stored but nothing branches on it yet: the check-in records what
  it knew on the receipt and no decision reads it.
- **No live hospital-capability feed.** Hospital *locations* are now verified
  against Google Places, with a `place_id` and verification date on every
  record, so distance is real. Capability and insurer empanelment remain a dated
  seed and say so — no feed publishes them.
- **No content templates on the WhatsApp sender.** Freeform sends succeed only
  inside WhatsApp's 24-hour customer service window; a cold business-initiated
  send fails, and is recorded as `comms.not_delivered` rather than claimed.
- **A placed call is not an answered call.** Escalation walks the contacts in
  stored order and stops at the first call the carrier accepts. Whether anyone
  picked up is outside what the telephony reports, so no no-answer fallback is
  possible and none is claimed.
