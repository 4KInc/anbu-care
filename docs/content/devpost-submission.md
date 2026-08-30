# Devpost submission — every field, ready to paste

**Submission:** https://devpost.com/submit-to/30845-all-things-agentic-hackathon/manage/submissions/1149218-anbucare
**Deadline:** 31 August 2026.
**Fact-checked:** 30 Aug 2026 against revision `anbu-care-00199-ncz`, suite at 1,203 tests.

Fields marked **[YOU]** need a decision or an artefact only you have. Everything
else is filled and checkable.

---

# 1. Project overview

### Project name
```
AnbuCare
```

### Elevator pitch  *(200 char max — this is 191)*
```
My mother is 71 and lives in Thoothukudi. I don't. Anbu Care coordinates her care at 3am — triage, insurance, bookings, payments — with the guardrails in code, where no prompt can reach them.
```

### Thumbnail **[YOU]**
3:2 ratio. Best option: a screenshot of the dashboard trace showing the receipt
chain, or `docs/architecture.png` cropped.

---

# 2. Project details

## About the project

Paste the whole block below into the *About the project* box.

---

## Inspiration

My parents live in Thoothukudi. I don't. Every NRI I know has the same 3 a.m.
fear: something happens, and by the time you hear about it, the decisions that
mattered have already been made by whoever was standing there.

The existing answer is a person — a family friend, a paid proxy, a sibling
WhatsApp thread. Sahaayak, Samarth Care, Care247, Policybazaar's NRI Care
Program: every one of them is a human-coordinator model. I wanted to know
whether an agent could take that role, and what would have to be true before I
would let it near my own mother.

One rule fell out of that and shaped everything else: **any step that needs the
son to act is a design failure.** He is asleep, nine and a half time zones away.
That is the problem, not a detail of it. So every feature got tested with him
unreachable.

## What it does

She sends a voice note in Tamil. Nobody presses anything after that. Within
about fifteen seconds:

- her son gets an alert, in English
- the neighbour gets a notice in Tamil carrying a scoped, revocable bedside link
- **a cashless pre-authorisation is filed against her policy**
- **a one-hour regulatory clock starts**

That clock is not decoration. The IRDAI Master Circular on Health Insurance
Business (IRDAI/HLT/CIR/PRO/84/5/2024, 29 May 2024) gives an insurer one hour to
decide a complete cashless request. When it lapses the family is told what they
are owed — and told in the same breath that Anbu Care has filed nothing, can
compel nobody, and is not claiming this will be won.

From there it keeps going without being asked:

- **It books a real clinic.** A doctor dictates a test at the bedside, in Tamil,
  into a handset bound to the case for sixty minutes. Nobody picks a lab. It
  searches real Thoothukudi diagnostic centres, ranks them against a mandate,
  and drives each centre's own booking site in a real headless browser: read the
  form, map the fields, fill it, read it back, submit, screenshot.
- **It pays the family's share of a hospital bill.** Under a cashless
  authorisation the insurer settles its part with the hospital directly, so
  paying the printed balance would pay the insurer's share out of the family's
  money. On our day-four bill that is ₹27,300 on the paper where ₹9,733 is owed.
- **The claim files itself on discharge.** A photographed discharge summary
  assembles the packet, submits it, starts the real 30-day clock, and renders a
  filled Part A claim form — a PDF a person could sign and send.
- **An arriving lab report closes the test it belongs to** — and closes neither
  when two are outstanding, because attributing it means reading it to decide
  which, and that is a model choosing which clinical order was carried out.
- **It checks on her for a fortnight after discharge**, once a day, in Tamil.

Everything it does leaves an Ed25519-signed receipt whose hash covers the
previous one, and **anyone can verify the chain without a credential.**

## How we built it

Five agents on **Gemini 3.5 Flash** and **Google's Agent Development Kit** —
onboarding, triage, evidence, insurer liaison, WhatsApp comms — under a
coordinator, each with an isolated tool scope. Underneath them is a
deterministic layer no agent can reach past. **The model proposes; that layer
decides, and it is the only thing that can write.**

- **Cloud Run** hosts the agent API, the Twilio webhook and the dashboard. A
  second service runs the headless browser, because Chromium will not fit beside
  the API.
- **Firestore** holds case state and the hash-chained receipt ledger in a
  single-table PK/SK design.
- **Cloud Scheduler** drives the recovery check-ins and the claims SLA tick,
  because Cloud Run holds no timer — that is what makes the regulatory clock
  real rather than a `setTimeout` in a demo.
- **Pub/Sub** carries intake, case and claim events for multi-day tracking.
- **Cloud Storage** keeps every photograph and screenshot, privately.
- **Vertex AI Agent Engine Memory Bank** holds the one class of fact that
  outlives a case.
- **Google Places** verifies that every hospital and diagnostic centre is a real
  place with a `place_id` and a verification date.

## Challenges we ran into

**An agent claiming a write that never happened.** On one run it said "I have
successfully read your mother's lab report and ingested it into her health
record." Documents actually stored: **zero**. For a system whose entire pitch is
a verifiable record, that is the one bug that discredits everything else. The
fix was not better prompt wording — the demo now prints the stored count read
back from the service, next to what the agent claimed, and says `CONTRADICTED`
on screen if they disagree.

**A guard that could never fire.** The document reader emits the kind
`lab_report`; the record stores it as `blood_report`. A new guard compared
against the stored word. Nothing raises. No error appears anywhere. The guard
simply never matches and the loop silently closes nothing, forever — and
eighteen unit tests passed, because they all called the function directly and
handed it the right word themselves. The fix was not the one-word change; it was
writing two tests that go through the real ingestion path and **confirming they
fail first.**

**A race I caused, on the demo handset.** Recovery check-ins read
the day's slot, then translated, then sent, then wrote the slot. Between the
read and the write sit a model call and a provider call, and a second caller
reading that same empty slot sends the same message. She was asked how she was
feeling twice in one minute. The slot is now claimed atomically *before* the
send.

**Paying the insurer's share out of the family's money.** The payment lane
handed the enforcer the balance printed on the bill, and nine guards checked the
destination and the caps without one of them asking whether the insurer was
already paying the hospital directly. The system knew better *in the same
function, three lines apart* — one line paid the balance, the next rendered the
coverage split saying most of it was covered.

**Gemma, which is not in the build.** I wanted a small model normalising messy
intake text. It is not available as a managed endpoint on Vertex for this
project — all three variants 404 on `generateContent` — and serving it would
have meant a GPU-backed deployment billed by the hour for a component that by
design could never change a decision. It is future work, the precheck evidence
is in the repo, and it is not claimed anywhere as built.

## Accomplishments that we're proud of

**The guardrails are code, not prompts, and I can show it rather than say it.**

- Tell it *"she says it's probably just gas"* and severity still returns HIGH.
  The thing that decides severity is a Python dict; it never reads that sentence
  as permission.
- Clinical detail cannot leave over WhatsApp. The gate classifies the *content*,
  not the caller's claim about it. Then I bypass the agent entirely and call the
  send function directly — **still blocked.** That second half is the whole
  claim: an agent that is merely *told* not to leak a lab value is not a control.
- **Public where it proves, private where it reveals.** Verification is open to
  everyone and needs no credential, because it proves the record was not altered
  *without revealing what it says*. Everything returning content is
  credentialed. Rewrite a receipt in Firestore leaving hash and signature
  untouched, and the public endpoint names the sequence number: `verified:
  false, broken_at_seq: 1, "payload does not hash to the recorded hash"`.

**The refusals I am proudest of.** Two outstanding tests and one arriving report
closes neither. A Memory Bank with no free-text path into it, so a caller cannot
put a symptom in it because a caller cannot put a *sentence* in it. A claim form
that prints `not on record` rather than guessing, because it is signed by a
person and a confident wrong field is worse than a blank one.

**1,203 tests, none of which need cloud access or a model to run.**

## What we learned

That the interesting work in an agent is deciding what it is **not** allowed to
do, and then putting those decisions somewhere a prompt cannot reach.

And that a test which cannot fail for the reason you care about is not covering
that reason. The `lab_report` bug passed eighteen tests. Now, when I add a
guard, I break it on purpose first and check that something goes red.

## What's next for AnbuCare

The staleness nudge — nothing yet reminds anybody that a booking request has
gone unanswered. Real cancellation through a centre's own system, so `cancel`
stops being a record-only act. Per-analyte reference change values instead of a
flat 10% band. And Gemma as an intake normaliser, if it ever ships as a managed
endpoint.

---

### Built with  *(tags — paste comma-separated)*
```
python, google-adk, gemini, gemini-3.5-flash, vertex-ai, agent-engine, memory-bank, cloud-run, firestore, pub-sub, cloud-storage, cloud-scheduler, google-places, fastapi, pydantic, uvicorn, docker, twilio, whatsapp, playwright, razorpay, ed25519, cryptography, fpdf2, segno
```

### "Try it out" links
```
https://anbu-care-37j4eofpwq-el.a.run.app/app
https://github.com/4KInc/anbu-care
https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/case-da1c2cb6db/verify
```
The third needs no login, and is the point: a judge can check the chain without
asking me. Pair it with `case-a7cf9fa613`, which is kept deliberately tampered.

### Image gallery **[YOU]**
Suggested, in order: the dashboard trace with the receipt chain; the two booking
screenshots (form filled / centre's answer); the filled claim form PDF; the
WhatsApp thread showing the four messages from one voice note;
`docs/architecture.png`.

### Video demo link **[YOU]**
Unlisted-or-public YouTube. Script: `docs/DEMO_SCRIPT_4MIN.md`.

---

# 3. Additional info

### Sponsor / Special Prizes
Leave blank unless you are entering **Startup Excellence**, which needs an
incorporated organisation and a corporate email. **[YOU]**

### Submitter Type **[YOU]**
`Individuals` — unless you are submitting on behalf of BlockIntel AI, in which
case `Organization`, and that is also what the Startup Prize requires.

### Submitter country of residence **[YOU]**
`United States`

### Which Category are you submitting to?
```
Taskmaster
```

### If submitting on behalf of an Organization, what is the Organization name? **[YOU]**
`N/A` if you selected Individuals. Otherwise your incorporated name.

### What date did you start this project?  *(MM-DD-YY)*
```
08-19-26
```
First commit: *"Deterministic core: schemas, signed hash-chain provenance,
Thoothukudi KB, triage engine"*, 19 Aug 2026. 243 commits through 30 Aug. Inside
the submission period.

### URL to your public or private code repo
```
https://github.com/4KInc/anbu-care
```
Public — verified returning 200 on 30 Aug 2026, so no need to share it with
testing@devpost.com.

### Did you add Reproducible Testing instructions to your README?
```
Yes
```
`make install` / `make test` / `make preflight` / `make demo`, plus a Deploying
section that mints the signing key and a Memory Bank. `make test` runs 1,203
tests with **no GCP or model access needed.**

### Hosted project URL
```
https://anbu-care-37j4eofpwq-el.a.run.app
```

### Testing instructions *(judges only — paste this)*
```
No credentials needed for the two things worth checking.

1. The chain is public. Anyone can verify it:
   curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/case-da1c2cb6db/verify
   -> verified: true, receipt_count: 8

   And a case we tampered with on purpose, kept broken so the check has
   something to catch:
   curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/case-a7cf9fa613/verify
   -> verified: false, broken_at_seq: 1,
      "payload does not hash to the recorded hash - content was altered"

   Note an empty chain is a valid chain: a deleted case returns verified:true
   with receipt_count:0. The COUNT is what ties the proof to the story.

2. Content is refused without a credential, and that boundary is the design:
   curl -o /dev/null -w '%{http_code}' <URL>/api/parents/<any-id>   -> 401
   curl -o /dev/null -w '%{http_code}' <URL>/api/cases/<any-id>/verify -> 200
   Public where it proves; private where it reveals.

3. Health, including which components are simulated:
   curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/healthz

4. Locally, with no Google Cloud project and no model access:
   make install && make test     # 1,203 tests
   make demo                     # the full spine, no LLM in the loop,
                                 # ending with a tamper the chain catches

WHAT IS SIMULATED, stated plainly and labelled in the product itself rather
than only here: the insurer's adjudicator is deterministic local rules;
payments run on a real Razorpay link in TEST mode and move no real money.
What is NOT simulated: WhatsApp (real Twilio to real handsets, freeform-only
inside the 24h window), the clinic booking (a real headless browser on a real
centre's site), the regulatory clocks (real Cloud Scheduler, real wall time),
hospital locations (Google Places place_id + verification date), and the
receipt chain. All demo data is synthetic.
```

### Which Google SDK did you use?
```
Agent Development Kit (ADK)
Google GenAI SDK (google-genai)
```

### Which Google Cloud Service(s) did you use?
Select every one of these that appears in the list:
```
Cloud Run          two services: the agent API, and the headless browser
Firestore          case state + the hash-chained receipt ledger
Pub/Sub            intake, case and claim events
Cloud Storage      photographs, booking screenshots, claim forms
Cloud Scheduler    the recovery tick and the claims SLA tick
Vertex AI          Gemini 3.5 Flash, and Agent Engine Memory Bank
Google Places      hospital and diagnostic-centre verification
Cloud Build        deploys from source
Artifact Registry  container images
```

### Architecture diagram
```
docs/architecture.png
```
Rendered from `docs/architecture.mmd`. Five numbered bands, with the
deterministic guard layer drawn as its own layer and the request spine running
straight through it — that separation *is* the architecture.

### Which Google AI Models did you use?
```
Gemini 3.5 Flash (gemini-3.5-flash) via Vertex AI — document vision over
discharge summaries, lab reports, ECGs, prescriptions and bills; single-call
transcription of Tamil voice notes; translation; policy-clause matching.
```
No additional Google AI model is used. Gemma was scoped and is **not** in the
build: it is unavailable as a managed endpoint on this project's Vertex AI
(`gemma-3-27b-it`, `gemma-3-12b-it`, `gemma-2-9b-it` all 404 on
`generateContent`). Precheck evidence is in the repo. Claiming it would be the
one thing this project cannot afford.

### OPTIONAL for Bonus Points — link to a piece of content **[YOU]**
Publish `docs/content/devto-article.md` on dev.to, **public not unlisted**, then
paste the URL. It carries the required "created for the All Things Agentic
Hackathon" line.

### OPTIONAL for Bonus Points — link to a social media post **[YOU]**
Publish **one** draft from `docs/content/social-post.md` (the X thread is
recommended) and paste the URL. Carries `#AllThingsAgenticHackathon`.

---

# Before you hit submit

- [ ] Video uploaded and the link works in an incognito window.
- [ ] Architecture diagram attached — the form rejects a blank file.
- [ ] dev.to article published **public**, URL pasted.
- [ ] Social post published, URL pasted.
- [ ] `curl -s $URL/api/healthz` still returns ok.
- [ ] Both canonical cases still verify as stated in the testing instructions.
- [ ] `make test` still reads 1,203, or update every number in this file.
