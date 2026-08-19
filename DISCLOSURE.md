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

## Third-party dependencies

Standard open-source libraries, unmodified, declared in `pyproject.toml`:
`google-adk`, `google-cloud-firestore`, `google-cloud-pubsub`, `pydantic`,
`cryptography`, `fastapi`, `uvicorn`, `python-dotenv`.

## Provenance of this repository

Every commit in this repository falls inside the Aug 3–31, 2026 submission
window. `git log` is the record.
