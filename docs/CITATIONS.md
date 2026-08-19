# Citation status

**Every market and hospital figure in the Anbu Care project brief is directional
and unverified.** None of them has been sourced to a primary reference. Do not
repeat any of them in a public writeup, a Devpost submission, or a recorded demo
narration until it has been.

Founder credibility and judge trust are the exposure here — an unsourced number
that turns out wrong costs more than the number was ever worth.

## Figures requiring verification

| Claim in the brief | Source to verify against | Status |
|---|---|---|
| NRI remittances to India ~$135–140B (FY26) | RBI remittance data | unverified |
| Health insurance GWP ~₹1.36 lakh crore (~$16B) FY26, ~15% YoY | IRDAI annual report | unverified |
| Retail health ~$15B → ~$22–23B by 2031 | Published market reports | unverified |
| ~3.26 crore claims processed FY24–25 (~89k/day) | IRDAI annual claims data | unverified |
| 1-hour cashless pre-auth decision mandate | IRDAI Master Circular 2024 | unverified — **load-bearing**, see below |
| 30-day reimbursement clock | IRDAI Master Circular 2024 | unverified — **load-bearing**, see below |
| TPA market ~$5.9B (2023) → ~$9.3B (2030) | Published TPA market reports | unverified |
| Competitors are all human-coordinator models | Each company's own product pages | unverified |

The two IRDAI SLA windows are load-bearing: they are implemented as real
deadlines in `anbu_care/service.py` (`SLA_CASHLESS_PREAUTH`,
`SLA_REIMBURSEMENT`) and narrated during the demo. Verify these two first.

## Hospital knowledge base

`anbu_care/kb/data/hospitals_thoothukudi.json` carries its own status field:
**SEEDED SNAPSHOT — NOT A LIVE FEED**. Capability flags (`cardiac_icu`,
`stroke_unit`) and `empanelled_insurers` are seeded for the demo and change over
time. Spot-check against:

- each hospital's own emergency and cardiology service listings;
- the insurer's network-hospital list for the specific policy;
- current TPA empanelment records.

The seeded values are surfaced with their provenance everywhere they are used —
`run_triage` returns the snapshot status in every response, and the triage
agent is instructed to say so whenever a routing decision turns on a capability
or an empanelment. That is honest about the limitation, but it is not a
substitute for checking the values before recording.

## Hackathon facts

Criteria weights (40/30/30), tracks, mandatory stack, prize pools, and bonus
items are aligned to the official All Things Agentic Hackathon rules page.
Re-check them against the live rules page before submission — rules pages get
edited.
