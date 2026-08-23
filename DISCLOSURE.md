# Prior-work disclosure

The hackathon rules require that projects be newly created during the Submission
Period (Aug 3–31, 2026), and that any pre-existing code or work incorporated be
disclosed. This document is that disclosure.

## Summary

**No code was lifted from any prior repository.** Every file in this repository
was written during the submission window. Two *design patterns* from our own
earlier projects were reimplemented from scratch here, and they are named below.

## Pattern 1 — confidence-gated evidence decisioning (STEP_UP)

**Prior work:** Verigate (github.com/4KInc/verigate) — deterministic AI agent
payment authorization, where an action below a confidence threshold triggers a
step-up rather than proceeding or failing outright.

**What was reused:** the idea. A three-way gate (`PASS` / `STEP_UP` / `BLOCK`)
driven by a weighted completeness score, where the middle state means "gather
more before proceeding" rather than "reject".

**What is new here:** the entire implementation. The scoring dimensions are
claim-packet-specific (policy number, admission summary, itemised bills,
diagnostics, attached documents, matched policy clauses), the thresholds are
tuned to first-pass claim approval, and the enrichment actions are drawn from
the parent's health knowledge base. See `anbu_care/tools/evidence_tools.py`.

**Important scoping note:** in Anbu Care, STEP_UP operates on the packet *we*
submit. It is not inside the insurer's adjudication loop and it does not
pre-empt a denial. Its job is pre-submission enrichment to raise first-pass
approval odds. Describing it as "prevents denials" would overstate what the
agent controls, and the agent's own instructions forbid that phrasing.

## Pattern 2 — signed hash-chain provenance

**Prior work:** GenuProof (github.com/4KInc/genuproof) — anti-counterfeiting and
product authentication, using a hash-chained ledger of signed events in a
single-table PK/SK Firestore layout.

**What was reused:** the idea. Each record's hash covers the previous record's
hash, so a silent edit to an earlier entry invalidates every entry after it; and
the single-table PK/SK layout that makes one entity's whole chain a single range
read.

**What is new here:** the entire implementation. `anbu_care/provenance/chain.py`
is written from scratch — Ed25519 signatures over a canonical JSON encoding, a
receipt schema built around agent decisions (`triage.decision`, `evidence.assessed`,
`claim.submitted`, `comms.blocked`), and a verifier that distinguishes an altered
payload from a dropped receipt from a bad signature.

## The simulated adjudicator's rules are our construction

`anbu_care/tpa/adjudicator.py` decides PASS / PARTIAL / QUERY / DENY using
deterministic local rules. Those rules are **our own construction from published
convention**, not any insurer's or TPA's actual adjudication logic, and no
insurer was consulted.

Specifically, the per-day sub-limit percentages (room rent 1% of sum insured per
day, ICU 2% per day) are the conventional caps widely used across Indian health
policies. They are applied here as a plausible stand-in so the claim path
produces defensible arithmetic. **No real policy schedule was copied**, and the
demo seeds no bespoke sub-limit values — the caps derive from the convention
applied to the sum insured already on the synthetic policy, so the numbers land
wherever the arithmetic puts them.

A richer adjudicator looks more like a real integration than the earlier stub
did, so the label works harder: every adjudication payload, receipt, and agent
report carries `SIMULATED — deterministic local rules, not an insurer`.

## Gemini is used for translation, and only as a rendering of a record

Outbound Tamil is a Gemini call, and that is worth stating precisely because
the failure mode is subtle. The model is never asked what to say. It is handed
text that is already on the record — a bill summary read off a photograph, a
fixed check-in template, a status line — and asked to say that same thing in
Tamil.

Three properties are enforced in code rather than in the prompt:

- `translate.render` **refuses** a call with no source text or no named source
  record. There is no path that produces Tamil for text nobody wrote down.
- The compliance gate rules on the **English** before anything is rendered.
  `CLINICAL_PATTERNS` are English regexes and would not recognise a lab value
  in Tamil script, so gating after translation would have been a hole the width
  of the alphabet.
- Every failure — timeout, empty reply, engine switched off — returns the
  recorded English with a line saying it could not be rendered. There is no
  mode that produces a partial or guessed translation.

The English record remains the source of truth. The Tamil is derived from it,
says so in the message, and the receipt carries the SHA-256 of the English the
gate ruled on.

## Recovery check-ins present and record. They never advise or diagnose

The recovery feature asks a fixed question once a day and stores the answer. It
holds no opinion about the answer, and the check-in record has no field an
opinion could be written into — a test asserts the exact field set, and fails
the day somebody adds `severity`.

One thing deliberately **is** allowed to happen to a concerning answer: the
deterministic severity table already used by every other intake sees it, and if
a red flag matches, a case opens and the family is told. That is not the
recovery feature deciding anything — it is the recovery feature declining to
make an exception. The alert it produces reports what was heard and names no
cause, no condition and no course of action beyond "call her, and if you cannot,
call 108".

The phase label on a check-in (`acute` / `recovery`) is computed from stored
state — an open window, a prompt sent within 24 hours — and never from reading
her words.

## Third-party dependencies

Standard open-source libraries, unmodified, declared in `pyproject.toml`:
`google-adk`, `google-cloud-firestore`, `google-cloud-pubsub`, `pydantic`,
`cryptography`, `fastapi`, `uvicorn`, `python-dotenv`.

## Provenance of this repository

Every commit in this repository falls inside the Aug 3–31, 2026 submission
window. `git log` is the record.
