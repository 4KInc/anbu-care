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

## Hospital bill structure

`scripts/make_bill_images.py` renders synthetic Indian IPD bills for testing the
bill-capture lane. The structure — hospital header with GSTIN, UHID and IP
number, admission and discharge timestamps, itemised heads by department,
sub-total, discount, GST, advance paid, balance due — follows publicly
documented Indian hospital billing formats.

| Claim | Source | Status |
|---|---|---|
| IPD bill line-item heads and layout | [Uyirly, hospital bill format IPD/OPD](https://www.uyirly.com/guides/hospital-bill-format-ipd-opd-sample) | unverified, structural only |
| General-ward sample figures (bed 1,500/day, nursing 150/shift) | same | unverified, used as synthetic values |
| IRDAI "subsumed" non-payables: PPE kits, gloves, gowns, admission kit | [BillOkay, how to read your hospital bill](https://billokay.com/guides/read-hospital-bill/) | unverified — **load-bearing**, see below |
| Room-rent GST threshold above INR 5,000/day | same | unverified, not implemented |

The subsumed-items list is load-bearing because `NON_COVERED_ITEMS` in
`anbu_care/tpa/adjudicator.py` decides what the coverage estimate treats as not
payable. Gloves and PPE were added to it after a realistic bill layout put them
on their own line and the estimate counted them as fully covered. That list is
**not exhaustive and deliberately so** — IRDAI publishes over two hundred
items, and a list padded to look complete would make an estimate look like an
adjudication. Verify against the current IRDAI list before any public writeup
that describes the split as accurate rather than indicative.

All figures on the generated bills are synthetic and the images say so on their
face. No real patient, no real hospital billing record.

## Medical and insurance document formats

`scripts/make_documents.py` renders synthetic discharge summaries, lab reports,
prescriptions and policy schedules for testing ingestion. Formats follow
publicly documented Indian standards.

| Claim | Source | Status |
|---|---|---|
| Discharge summary sections and field order | [NABH sample format, E-Mitra/FORMAT/04](https://nabh-portal-live.s3.ap-south-1.amazonaws.com/wp-content/uploads/2025/07/18074912/D.-E-Mitra_FORMAT-04_Hospital-Discharge-Summary-Format.pdf) | unverified, structural only |
| Lab report layout: result, unit, biological reference interval, abnormal flag | [Drlogy pathology report formats](https://www.drlogy.com/pathology-lab-software/report-format) | unverified, structural only |
| Room rent sub-limit typically 1% of sum insured per day, ICU 2% | [Onsurity](https://www.onsurity.com/blog/room-rent-icu-limits-in-health-insurance/), [Policybazaar](https://www.policybazaar.com/health-insurance/individual-health-insurance/articles/concept-of-room-rent-limit-in-health-insurance/) | unverified — matches the values already in `SUBLIMIT_RULES` |
| **Proportionate deduction on exceeding room rent** | [Onsurity](https://www.onsurity.com/blog/room-rent-icu-limits-in-health-insurance/), [Acko](https://www.acko.com/health-insurance/room-rent-limit/) | unverified — **load-bearing, and NOT implemented** |

**The proportionate-deduction finding matters more than the documents.** Indian
insurers do not merely deduct excess room rent when the room occupied is above
the eligible category: they reduce the *associated medical expenses* in the
same proportion the eligible rent bears to the rent actually charged, with
medicines, consumables and implants typically excepted.

The coverage estimate does **not** model this. Where a room or ICU line exceeds
its per-day limit the real shortfall is therefore larger — potentially much
larger — than the figure shown. That is the one direction an estimate about
somebody's money must never be quietly wrong in, so `CoverageEstimate` carries
`may_understate` and the dashboard says so on the face of the number rather
than in a footnote.

Implementing it properly needs a decision about which heads are "associated
medical expenses" for a given policy, which is policy wording rather than a
general rule. Until then the warning stands in for the arithmetic.

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

## Triage red-flag table

`anbu_care/triage/severity.py` decides whether a case opens, so its contents
are load-bearing in a way a market figure is not. Sources:

| Entries | Source | Status |
|---|---|---|
| Difficulty breathing, unconsciousness, severe loss of blood, severe burns, choking, fitting, severe allergic reaction | [London Ambulance Service, "When to call 999"](https://www.londonambulance.nhs.uk/calling-us/calling-999/) | cited |
| Chest pain, pain radiating to arms, jaw, neck, back; shortness of breath; nausea | [NHS, "Symptoms of a heart attack"](https://www.nhs.uk/conditions/heart-attack/symptoms/) | cited |
| Face drooping, speech difficulty, sudden confusion | [NHS, "Stroke"](https://www.nhs.uk/conditions/stroke/) | cited |
| Reduced consciousness, meningism with fever | [NHS, "Sepsis"](https://www.nhs.uk/conditions/sepsis/) | cited |

**The table is not a clinical protocol.** It is derived from published public
first-aid guidance and has not been reviewed by a clinician. A real deployment
must have that done. It is stated here, and in the source file, because the
table being a reviewable artefact rather than a prompt is the point — an
unreviewed artefact that looks reviewed would be worse than no artefact.

A match is not a diagnosis. It means the words appeared, which is why the
receipt records the matched phrase and never a conclusion.
