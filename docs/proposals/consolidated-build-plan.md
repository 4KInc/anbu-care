# Proposal — consolidated build plan (clinician handoff, voice, bills, docs, agency)

**Status:** proposal, no code written. Every stream below is gated.
**Written:** 2026-08-22, against a clean tree at `7bd1b4f`.
**Deadline:** 31 Aug 2026, 5pm PDT. **Nine days, no demo video recorded yet.**
**Scope:** five requested streams, reconciled into one sequence.
**Guarantee layer** — `triage/severity.py`, `comms/policy.py`, `tpa/`,
`provenance/`, `comms/consent.py` — unchanged, with two named exceptions that
require sign-off (Stream A Phase 2, Stream B Phase 1).

---

## Read this before the plan

Five briefs arrived, each asking for a Phase 0 check before building. Those
checks are done, and **five premises did not survive them.** They are listed
first because three of them change the size of the work substantially.

The second thing to say plainly: **this is more than nine days of work**, and
the video — still the single hardest deliverable — is not started. The plan
below is therefore ordered by recordable value per hour, with an explicit line
marking the minimum set worth shooting. Everything past that line is honestly
labelled as beyond the deadline unless something is cut.

---

## Phase 0 — consolidated ground truth

### 0.1 Premise corrections

| Briefs said | Reality | Impact |
| --- | --- | --- |
| "279 tests" | **399 passing** | the number for every doc |
| Docs claim "5 agents" is stale | **Accurate.** 1 root + 5 sub-agents | no edit needed |
| Existing Gemini-**vision** document parsing | **Does not exist.** `ingest_document` is an ADK tool the multimodal agent calls with values it already read | bill capture is *not* assembly |
| Inbound WhatsApp handles images | **Audio only.** `inbound.py:141` returns `InboundMedia(audio=…)`, `media_from` defaults `audio/ogg` | webhook must be extended |
| WhatsApp voice "already implemented" | **True, and genuinely end to end** | check-then-extend confirmed |

### 0.2 Test count, model, agents

- **399 passing**, 7 warnings, ~1.5 s.
- `ANBU_MODEL` default is **`gemini-3.5-flash`** in all three places:
  `config.py:27`, `infra/deploy_cloud_run.sh:28`, `.env:8`. Gemini 3.5, not a
  2.5 fallback. Eligibility is fine.
- Agents: root `anbu_care` coordinator (`agent.py:88`) plus **five** sub-agents —
  onboarding, triage, evidence, insurer_liaison, whatsapp_comms. Every one is an
  ADK `LlmAgent` on `settings().model`.

### 0.3 Endpoints and the credential boundary

Eighteen routes in `server.py`. The boundary is enforced by
`Depends(require_case_access)` / `require_family_session` from `webauth.py`, and
is **proven live on the deployed revision**:

```
/api/cases/{id}/verify   → 200   public, integrity only
/api/cases/{id}/trail    → 401   content, credentialed
/api/healthz             → 200
/api/hospitals           → 200
/api/intake-channels     → 200
/api/map-config          → 200
```

Credentialed today: `/api/parents/{id}`, `…/wellbeing`, `…/brief`, `…/trail`,
`…/care-circle`, `/api/cases/{id}`, plus the two notify POSTs.

### 0.4 Receipt kinds and consent purposes

Receipt kinds emitted: `case.opened`, `intake.signal_received`,
`triage.decision`, `evidence.assessed`, `evidence.enriched`,
`claim.packet_assembled`, `claim.submitted`, `claim.adjudicated`,
`claim.stage_changed`, `wellbeing.recorded`, `wellbeing.escalated`,
`wellbeing.unclear`, `voice.placed` / `voice.not_placed`, plus the `comms.*`
family built conditionally.

Consent purposes (`comms/consent.py:22`–`:42`): four outbound
(`admission_alerts`, `status_updates`, `billing_updates`, `claim_updates`),
plus `outbound_notify` (care circle) and `inbound_wellbeing`. Outbound and
inbound sets are deliberately disjoint — conflating them was a real shipped
defect, fixed, regression-tested. **Every new purpose below must respect that
separation.**

### 0.5 The voice pipeline — honest state: **FULLY WORKING**

Not a placeholder.

| Question | Answer |
| --- | --- |
| End-to-end inbound voice? | **Yes.** `server.py:326`–`:352` stores the audio, transcribes, records, escalates |
| Real Gemini audio call? | **Yes.** `transcribe.py:130`–`:134`, `genai.Client().models.generate_content` with `types.Part.from_bytes(data=audio, mime_type=…)` |
| One call or two? | One — transcript **and** symptom reading in a single request, deliberately, to keep the webhook under Twilio's timeout |
| Where does the transcript go? | `wellbeing_store.record(…, source_kind="voice", audio_object=…)`, then `escalation.handle(entry, …, reading=heard.reading)` |
| Confirmation step? | **None** |
| Audio retention? | Stored via `comms/storage.py` — bucket stays closed, V4 signed URL with TTL. Never public |
| Failure handling? | First-class: `handle_unclear_voice` opens a case *because* unintelligible speech is itself a red flag |

**`voice.not_placed` — your suspicion was right.** It is `wellbeing/handler.py:267`,
emitted from `voice.place_call`, and describes an **outbound phone call**, not
inbound transcription. Its receipt note reads *"A placed call is not an answered
call."* It has nothing to do with the voice-note lane.

**On the missing confirmation step — this is not a defect, and should not be
"fixed".** For an emergency wellbeing check-in, requiring a gasping
seventy-one-year-old to confirm a transcript before anyone is alerted would be
the actual defect. The existing design compensates correctly: the audio is the
record and the transcript is derived, alerts say *"we heard"* not *"she said"*,
the recording stays playable, and the deterministic `RED_FLAGS` table reads the
raw text regardless.

**For a clinician note the calculus inverts** — an attributed clinical fact is
permanent, and a misheard number must never land. Confirmation is required there
and must be built, but as a **new gate on a new path**, not as a retrofit that
would slow the emergency lane.

### 0.6 Existing agency — the BUILD vs REVEAL answer

**It is mostly REVEAL, with one genuine BUILD.**

*Real agency, already present:* `agent.py:88` builds a root `LlmAgent` with five
`LlmAgent` sub-agents and its own tool list. ADK does model-driven tool
selection and sub-agent transfer. In the conversational lane, the model already
chooses what to call and in what order. **Nothing renders this.**

*Fixed pipelines, by design:* every webhook path in `server.py` runs a
deterministic sequence. `/api/wellbeing/inbound` is transcribe → record →
escalate, with no model choosing the order. That is correct and must stay.

*Care-circle escalation (brief question a):* `wellbeing/handler.py:258`–`:278`
iterates `profile.family_contacts` in **stored order**, places a call, and
breaks on `result.placed`. So: hardcoded order, with a runtime break on a real
outcome. Critically, **it cannot implement "if no answer → neighbour"**, because
Twilio reports *placed*, never *answered* — which is exactly what the receipt
note says. Any plan describing a no-answer fallback is describing something the
telephony cannot support.

*QUERY / STEP_UP (brief question b):* the adjudicator produces per-line verdicts
(`tpa/adjudicator.py:90`–`:227`). The reaction is code, not planning.

**Conclusion:** the flagship agentic beat should be **Stream E Phase 2 (the trace
view) first** — it makes existing agency visible for a fraction of the cost — and
a bounded planner (Phase 1) only if time survives.

### 0.7 Reusable for bill capture

- ✅ **Coverage math is real and per-line.** `adjudicator.py` emits `item`,
  `claimed_inr`, `allowed_inr`, `disallowed_inr`, `rule`, with sub-limit caps at
  `:77`–`:194`. Directly reusable. **Simulated**, as labelled.
- ✅ **Private GCS with V4 signed URLs** (`comms/storage.py:97`–`:118`). Bucket
  closed, short TTL. Directly reusable for bill images.
- ❌ **No vision lane.** Must be built.
- ❌ **No image intake.** Webhook must be extended.

### 0.8 Docs staleness

| Claim | Where | Status |
| --- | --- | --- |
| "73 tests" | `README.md:107`, `:243`, `docs/ARCHITECTURE.md:126` | stale → **399** |
| "Five sub-agents" | `README.md:147`, `:232`, `ARCHITECTURE.md:155` | **correct, leave alone** |
| IRDAI 1-hour / 30-day | `README.md:64`–`:65` states as fact | **contradicts CITATIONS** |

`docs/CITATIONS.md:19`–`:20` marks both IRDAI SLA windows **"unverified —
load-bearing"**, and `:24`–`:26` notes they are implemented as real deadlines in
`service.py` and narrated in the demo. The README asserts them flatly. The two
docs disagree and must be reconciled.

---

## Stream D — documentation reconciliation *(do first: cheapest, no code)*

**Gate:** none. No code changes. **Size:** small.

1. Replace `73` with `399` at `README.md:107`, `:243`, `ARCHITECTURE.md:126`.
   Re-grep for any digit-count of tests afterwards.
2. **Leave the agent count alone** — it is correct.
3. Add the real features to the architecture and endpoint tables: wellbeing
   check-in, care-circle notify, the consent split, real WhatsApp delivery,
   Google Places verification, the Routing map. Mark each **in code / routed /
   tested / live**.
4. Extend the "what is real / what is not" section — it stays leading the README
   — with the honest caveat for each new feature: sandbox WhatsApp sender,
   self-reported wellbeing (not a measured vital), notified-not-integrated care
   circle, seeded empanelment, simulated TPA.
5. **IRDAI reconciliation.** Until the Master Circular is verified, soften
   `README.md:64`–`:65` to name "the regulatory SLA windows Anbu Care tracks
   against real wall time" without asserting the two figures as established. The
   alternative — verifying them and marking CITATIONS verified — is a research
   task, not a code task, and is yours to decide.
6. Preserve verbatim: the code-not-prompts thesis, the two-access curl block
   (401 content / 200 verify), the deploy foot-gun notes, the Twilio catch-22.

**Proof required:** a repo-wide grep showing no stale count and no claim of a
capability Phase 0 marked not-live.

---

## Stream E — make the agency visible

### E1. Trace view *(the primary 10/10 deliverable)*

**Gate:** none. **Size:** medium. **Depends on:** nothing.

Render the decision sequence already recorded in the chain: which agent was
delegated to, which tool it chose, what came back, what happened next. The
receipts exist; nothing renders them as *reasoning*.

This is the difference between the agent deciding — which already happens in the
ADK lane — and a judge **watching** it decide. Paired with the existing
`/verify`, it is the strongest pitch available: autonomy you can check.

### E2. Bounded planner *(only if time survives)*

**Gate:** design review before code. **Size:** large.

The QUERY-with-missing-document fork. LLM-driven planning over **existing safe
tools only**, writing a `plan.step` receipt per decision.

**Hard guards, tested:** the planner cannot set severity, cannot compute a
coverage number, cannot fabricate a document, cannot write a receipt directly,
and terminates within a bounded iteration count. On exhaustion it reports the
real gap and never claims a submission.

**Honest note:** a planner that only reorders calls the code would have made
anyway is theatre. If E2 is built, the demo must show a fork where the ordering
genuinely differs — otherwise E1 alone is the better use of the remaining days.

---

## Stream A — clinician handoff

### A1. Emergency summary composer

**Gate:** none beyond this document. **Size:** medium.

Reuses stored fields only — no new parsing, confirmed in 0.7. Name, age,
**allergies most prominent**, chronic conditions, current medications, most
recent labs with date. Per-line provenance, same pattern as
`brief/composer.py`. Unknown fields render **"not on file"**, never guessed.

**Hard line:** facts, never advice. No drug recommendations, no "avoid X", no
treatment suggestions. A test asserts the summary schema has no directive field
and the rendered text contains no advisory construction.

### A2. Scoped emergency access *** GATE — credential boundary ***

**Size:** medium-large. **Requires explicit approval.**

Proposed token model, for review before any code:

| Property | Value |
| --- | --- |
| Scope | exactly one case, summary read only |
| Lifetime | 60 minutes from issue, absolute, no refresh |
| Revocation | family can revoke instantly from the dashboard |
| Storage | signed, server-verified, opaque to the holder |
| Grants | `GET …/emergency-summary` **only** |
| Never grants | `/trail`, `/api/parents/*`, any other case, any write |
| On open | writes an `emergency.access` receipt every time |
| Consent | new purpose `emergency_clinical_share`, read **live** |

The receipt records that an access occurred and when. It does **not** claim to
identify the clinician, because a link-holder is not an authenticated identity —
asserting otherwise would be the same class of false claim as "we alerted X"
when delivery failed.

**Consent conflict to resolve:** this adds a purpose to a layer marked
unchanged. It is a scoped exception or it does not ship.

### A3. Honest labelling

"Emergency clinical summary for the treating team — read-only, not connected to
any hospital system." Same wall as notified-parties-not-integrated-providers.
Nobody writes back; no EHR is connected.

---

## Stream B — clinician notes with voice input

**Gate:** depends on Stream A. **Size:** medium.

Voice is an **input channel** to the clinician-note path, inheriting every guard
of the typed path.

1. **Draft until confirmed.** Voice note → Gemini transcription → transcript
   shown back → explicit confirm → **only then** the `clinician.note` receipt.
   An unconfirmed transcript writes nothing and populates no brief field.
2. **No-interpret wall.** Transcribed text must never reach `run_triage`, set
   severity, or re-score a case — identical to `severity.py:94`.
3. **Capture provenance.** "recorded by *clinician* via voice note, transcribed
   by Gemini, confirmed *time*". Receipt carries the hash of the **confirmed**
   text, never raw audio, never raw text on the public chain.
4. **Audio retention** follows the existing lane: private bucket, signed URL,
   never public, never logged.
5. **Text stays default.** Voice is optional; typing always works.

Reuses `transcribe.transcribe()` unchanged. The confirmation gate is new code on
a new path and does not touch the emergency wellbeing lane.

### B2. Fields a clinician note must be able to update

Raised 2026-08-22: a discharge date is unknown at admission, and once the doctor
says it, the brief should show it. Correct, and there is no path today.

`discharged_on` lives only on `ClaimPacket` (`schemas.py:226`) and is written
only by `assemble_claim_packet` (`insurer_tools.py:37`, `:74`). The composer
reads it from the stored packet and nowhere else (`composer.py:198`). So the
field cannot move until a claim packet is assembled, which in a real stay
happens long after the doctor has said when she is going home.

**The constraint that decides the design.** `discharged_on` is not display-only.
`adjudicator.py:156` feeds it to `stay_days()`, which multiplies the per-day
sub-limit in `_cap_for()`, which sets `disallowed_inr` — **the out-of-pocket
figure the family is shown**. A date that reaches the packet changes what the
family is told they owe.

So a spoken discharge date must be **two different facts, kept apart**:

| | Expected discharge | Recorded discharge |
| --- | --- | --- |
| Source | confirmed clinician note | claim packet |
| Provenance shown | "told by Dr X, 14:37, via voice note" | "packet.discharged_on" |
| Reaches the adjudicator | **never** | yes |
| Affects money | no | yes |

A clinician's expectation is information for the family and must never silently
become an input to a coverage calculation. If a misheard "the 22nd" for "the
2nd" changed the sub-limit day count, a transcription error would become a rupee
figure — the exact failure the confirmation gate exists to prevent, arriving
through a side door.

Implementation, when Stream B is approved: the composer prefers a confirmed
clinician note for **Expected discharge**, falls back to the packet, and labels
which one it used. `assemble_claim_packet` keeps taking its date from the
document record, never from a note.

---

## Stream C — bill capture *(largest; premises corrected)*

**Gate:** design review. **Size:** large — two of three assumed foundations do
not exist.

1. **Extend the webhook to images.** `inbound.py` is audio-only today.
   `media_from` must branch on `MediaContentType0` and carry image bytes.
2. **Build the vision lane.** No server-side image→structured-data path exists.
   A bill parser is new code, mirroring `transcribe.py`'s shape: one Gemini call,
   `Part.from_bytes`, honest failure as a first-class outcome.
3. **Private storage.** Reuse `comms/storage.py` unchanged.
4. **Receipt carries a hash of extracted data — not amounts, not the image URL** —
   so public `/verify` proves a bill was recorded and unaltered while revealing
   no cost or PII.
5. **Traceability.** Every extracted amount links to the source image behind the
   credential, so a misread ₹96,000-vs-₹9,600 can be checked and corrected. A
   misread number must never silently become money-owed truth.
6. **Coverage split reuses `adjudicator.py`** and is labelled **"estimated split
   based on your policy — not the insurer's final decision."** Estimated-eligible
   is distinguished from actually-settled, and the cashless-vs-reimbursement
   distinction is reflected.

---

## Unified sequence

| # | Work | Stream | Gate | Size |
| --- | --- | --- | --- | --- |
| 1 | Docs reconciliation | D | none | small |
| 2 | Banner restyle + label corrections | *(prior plan)* | none | small |
| 3 | Affirmative negatives; run the full case | *(prior plan)* | none | small |
| 4 | Restrict the Maps API key *(owner action)* | — | none | minutes |
| 5 | **Trace view** | E1 | none | medium |
| 6 | Redeploy, live-verify, refresh screenshots | — | none | small |
| **—** | **← MINIMUM RECORDABLE SET. SHOOT THE VIDEO HERE. →** | | | |
| 7 | Emergency summary composer | A1 | none | medium |
| 8 | Scoped emergency access | A2 | **consent** | med-large |
| 9 | Clinician notes + voice confirmation | B | **consent** | medium |
| 10 | Bounded planner | E2 | design | large |
| 11 | Bill capture | C | design | large |

**Steps 1–6 are achievable in the time remaining and produce a materially
stronger demo than exists today.** Steps 7–11 are not all achievable by 31 Aug
alongside recording, editing and a Devpost writeup. That is a scheduling fact,
not pessimism.

If the clinician handoff is the beat you most want on camera, promote 7 and 8
above the line and drop 10 and 11 entirely — but make that trade deliberately.

---

## Test plan

Baseline **399**. The count grows; it never shrinks.

| Stream | Tests to add |
| --- | --- |
| D | none (docs only) |
| E1 | trace renders only receipts that exist; no synthesised step |
| E2 | cannot fabricate a document; cannot set severity or coverage; terminates within bounds; exhaustion reports the real gap |
| A1 | no directive/advice field; missing field renders "not on file"; every line traces to a stored source |
| A2 | token opens only its own case; expired → denied, nothing rendered; forged → denied; every open writes a receipt; no consent → no token; token cannot reach `/trail` or `/api/parents` |
| B | transcription alone creates no receipt and populates no brief field; spoken triage phrase never calls `run_triage` and sets no severity; confirmed note carries via-voice provenance and the confirmed-text hash |
| C | image ingested → line items extracted; image non-public; receipt carries a hash and `/verify` reveals no amounts; extracted amount traces to the source image |

---

## Open decisions

1. **Consent exceptions.** Streams A2 and B each add a purpose to a layer marked
   unchanged. Approve as scoped exceptions, or those streams do not ship.
2. **Where the recording line falls.** Steps 1–6 as written, or promote the
   clinician handoff above it and cut the planner and bill capture.
3. **IRDAI.** Soften the README, or verify the Master Circular and mark
   CITATIONS verified. The two docs cannot keep disagreeing.
4. **E2 at all.** If the planner would only reorder calls the code already makes
   in that order, E1 alone is the stronger deliverable.

Related: [`recording-window-plan.md`](recording-window-plan.md),
[`DEMO_SCRIPT.md`](../DEMO_SCRIPT.md), [`ARCHITECTURE.md`](../ARCHITECTURE.md),
[`CITATIONS.md`](../CITATIONS.md).
