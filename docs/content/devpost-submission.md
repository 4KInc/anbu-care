# Devpost submission: answers

Copy-paste answers for
`devpost.com/submit-to/30845-all-things-agentic-hackathon`, submission
`1149218-anbucare`.

Everything factual here was checked against the code or the deployed service on
30 August 2026, revision `anbu-care-00200-n6l`. Fields marked **DECIDE** need
something only you can supply.

---

## Project overview

**Project name** (50 char limit)

```
AnbuCare
```

**Elevator pitch** (200 char limit; this is 188)

```
My mother is 71 and lives in Thoothukudi. I don't. Anbu Care coordinates her care at 3am: triage, insurance, bookings, payments, with the guardrails in code where no prompt can reach them.
```

> Two other framings, if you want a different emphasis. Both fit the limit.
>
> **Leads with the autonomy** (186). The strongest single differentiator, and
> the one the four-minute video opens on:
> `One Tamil voice note at 3am. Her son is told, the neighbour gets a bedside link, cashless cover is filed against her policy and a 1-hour regulatory clock starts. Nobody pressed anything.`
>
> **Leads with the proof** (190), which is the axis judges can check themselves:
> `Eldercare coordination for families abroad. It books real clinics, files real claims, pays only what insurance doesn't, and signs every action so you can verify the record without asking me.`

---

## Project story: "About the project"

> Paste the whole block below into the *About the project* box. It uses the
> headings Devpost pre-fills.

```markdown
## Inspiration

My parents live in Thoothukudi. I don't. Every NRI I know has the same 3 a.m. fear: something happens, and by the time you hear about it, the decisions that mattered have already been made by whoever was standing there.

The existing answer is a person: a family friend, a paid proxy, a sibling WhatsApp thread. Sahaayak, Samarth Care, Care247, Policybazaar's NRI Care Program are all human-coordinator models. I wanted to know whether an agent could take that role, and what would have to be true before I let it near my own mother.

One rule fell out of that and shaped everything else: **any step that needs the son to act is a design failure.** He is asleep, nine and a half time zones away. That is the problem, not a detail of it. So every feature got tested with him unreachable.

## What it does

She sends a voice note in Tamil. Nobody presses anything after that. Within about fifteen seconds her son gets an alert in English, the neighbour gets a notice in Tamil carrying a scoped revocable bedside link, **a cashless pre-authorisation is filed against her policy**, and **a one-hour regulatory clock starts.**

That clock is not decoration. The IRDAI Master Circular on Health Insurance Business (IRDAI/HLT/CIR/PRO/84/5/2024, 29 May 2024) gives an insurer one hour to decide a complete cashless request. When it lapses the family is told what they are owed, and told in the same breath that Anbu Care has filed nothing, can compel nobody, and is not claiming this will be won.

From there it keeps going without being asked:

- **It books a real clinic.** A doctor dictates a test at the bedside, in Tamil, into a handset bound to the case for sixty minutes. Nobody picks a lab. It searches real Thoothukudi diagnostic centres, ranks them against a mandate, then drives each centre's own booking site in a real headless browser: read the form, map the fields, fill it, read it back, submit, screenshot. Seven of the eight real centres cannot take a submission. One can.
- **It pays the family's share of a hospital bill, not the hospital's whole balance.** Under a cashless authorisation the insurer settles its part with the hospital directly, so paying the printed balance would pay the insurer's share out of the family's money. On our day-four bill that is INR 27,300 on the paper where INR 9,733 is owed.
- **The claim files itself on discharge.** A photographed discharge summary assembles the packet, submits it, starts the real 30-day clock, and renders a filled Part A claim form: a PDF a person could sign and send, filled from the record, with any field the system does not hold printed as `not on record` rather than guessed.
- **An arriving lab report closes the test it belongs to**, and closes neither when two are outstanding, because attributing it means reading it to decide which, and that is a model choosing which clinical order was carried out.
- **It checks on her for a fortnight after discharge**, once a day, in Tamil.

Every action leaves an Ed25519-signed receipt whose hash covers the previous one, and **anyone can verify the chain with no credential.**

## How we built it

Five agents on **Google ADK**, a coordinator delegating to onboarding, triage, evidence, insurer liaison and WhatsApp comms, each with an isolated tool scope, running **Gemini 3.5 Flash** on Vertex AI for document vision, Tamil transcription, translation and policy-clause matching. A second, smaller model, **Gemini 2.5 Flash Lite**, decides which language she actually writes in, because asking a frontier model a question with a two-letter answer is the wrong trade on a path that owes her a reply in fifteen seconds.

Underneath them is a deterministic layer no agent can reach past. **The model proposes; that layer decides, and it is the only thing that can write.**

Google-managed services doing real work:

- **Cloud Run** for the agent API, the Twilio webhook and the dashboard, plus a second service for the headless browser, because Chromium will not fit beside the API
- **Firestore** for case state and the hash-chained receipt ledger, single-table PK/SK
- **Cloud Scheduler** for the recovery tick and the claims SLA tick. Cloud Run holds no timer, which is what makes the regulatory clock real rather than a `setTimeout` in a demo
- **Pub/Sub** for intake, case and claim events across a multi-day admission
- **Cloud Storage** for photographs, booking screenshots and claim forms
- **Vertex AI Agent Engine Memory Bank** for the one class of fact that outlives a case
- **Google Places** so every hospital and diagnostic centre carries a `place_id` and a verification date

The discipline that mattered: guarantees live in code, and the tests assert claims about the world rather than return values. There are 1,221 of them, none needing GCP credentials or a model.

## Challenges we ran into

**An agent claiming a write that never happened.** On one run it said "I have successfully read your mother's lab report and ingested it into her health record." Documents actually stored: **zero**. For a system whose entire pitch is a verifiable record, that is the one bug that discredits everything else. Prompt wording was never going to fix it. The demo now prints the stored count read back from the service next to what the agent claimed, and says `CONTRADICTED` on screen if they disagree.

**A guard that could never fire.** The document reader emits the kind `lab_report`; the record stores it as `blood_report`. A new guard compared against the stored word. Nothing raises. No error appears anywhere. The guard simply never matches and the loop silently closes nothing, forever. Eighteen unit tests passed anyway, because they all called the function directly and handed it the right word themselves.

**A race I caused.** Recovery check-ins read the day's slot, then translated, then sent, then wrote the slot. Between the read and the write sit a model call and a provider call, and a second caller reading that same empty slot sends the same message. The demo handset was asked how she was feeling twice in one minute. The slot is now claimed atomically before the send.

**Paying the insurer's share out of the family's money.** The payment lane handed the enforcer the balance printed on the bill, and nine guards checked the destination, the caps and the anomalies without one of them asking whether the insurer was already paying the hospital directly. The system knew better in the same function, three lines apart: one line paid the balance, the next rendered the coverage split saying most of it was covered.

**Gemma, which is not in the build.** I wanted a small model normalising messy intake text. It is not available as a managed endpoint on this project's Vertex AI (`gemma-3-27b-it`, `gemma-3-12b-it` and `gemma-2-9b-it` all return 404 on `generateContent`), and serving it would have meant a GPU-backed deployment billed by the hour for a component that by design could never change a decision. The precheck evidence is in the repo and it is claimed nowhere as built.

## Accomplishments that we're proud of

**The guardrails are code, not prompts, and I can show that rather than say it.**

Tell it *"she says it's probably just gas"* and severity still returns HIGH. The thing that decides severity is a Python dict; it never reads that sentence as permission.

Clinical detail cannot leave over WhatsApp. India's DPDP Act and Meta's healthcare policy make that a legal line, so the gate classifies the *content*, not the caller's claim about it. Then the demo bypasses the agent entirely and calls the send function directly. **Still blocked.** That second half is the whole claim: an agent merely *told* not to leak a lab value is not a control.

**Public where it proves, private where it reveals.** Verification is open to everyone and needs no credential, because it proves the record was not altered without revealing what it says. Everything returning content is credentialed. Rewrite a receipt in Firestore leaving hash and signature untouched, and the public endpoint names the sequence number rather than saying something is wrong.

**The refusals.** Two outstanding tests and one arriving report closes neither. A Memory Bank with no free-text path into it, so a caller cannot put a symptom in it because a caller cannot put a sentence in it. A claim form that prints `not on record` rather than guessing, because it is signed by a person and a confident wrong field is worse than a blank one.

## What we learned

**A test that cannot fail for the reason you care about is not covering that reason.** The `lab_report` bug passed eighteen tests, because every one of them called the function directly and supplied the right word itself. The fix was not the one-word change; it was writing two tests through the real ingestion path and confirming they failed first. Every new guard now gets broken on purpose before the change is allowed to stand.

**Managed services break assumptions that mocks cannot.** Vertex Memory Bank matches scopes exactly on the whole map rather than a subset, which is why recall here is a keyed lookup rather than a similarity search. One live call established that; no amount of local testing would have.

**Silence is a design decision.** The lanes that felt most staged were the ones that waited to be asked. Each was fixed by finding the document that already arrives and making it the trigger: an escalation files the pre-authorisation, a discharge summary files the claim, a lab report closes the test.

## What's next for AnbuCare

The staleness nudge: nothing yet reminds anybody that a booking request has gone unanswered, and callback-only centres never reach `confirmed`. Real cancellation through a centre's own system, so `cancel` stops being a record-only act. Per-analyte reference change values instead of a flat 10% band. And Gemma as an intake normaliser, if it ever ships as a managed endpoint.
```

---

## Built with

> Up to 25 tags. Paste one at a time.

```
python · google-adk · gemini · gemini-3.5-flash · vertex-ai · agent-engine ·
memory-bank · cloud-run · firestore · pub-sub · cloud-storage ·
cloud-scheduler · google-places · fastapi · pydantic · uvicorn · docker ·
twilio · whatsapp · playwright · razorpay · ed25519 · fpdf2 · segno · pytest
```

---

## "Try it out" links

```
https://anbu-care-37j4eofpwq-el.a.run.app/app
https://github.com/4KInc/anbu-care
https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/case-da1c2cb6db/verify
```

> The third needs no login and is the point: a judge can check the chain without
> asking you. Its pair, `case-a7cf9fa613`, is kept deliberately tampered.

---

## Project media

**Video demo link: DECIDE.** Record from `docs/DEMO_SCRIPT_4MIN.md`, which
carries the beat sheet, the Google Cloud proof to capture at each beat, and a
mid-take failure table. Must be public, not unlisted.

**Image gallery**, suggested in order:

1. The WhatsApp thread showing four messages from one voice note. The only frame
   where the system is visibly acting on its own.
2. The two booking screenshots side by side: the form filled in, and the
   centre's answer page.
3. The filled Part A claim form.
4. The dashboard trace with the receipt chain, and the verify result beside it.
5. The architecture diagram.

**Thumbnail: DECIDE.** 3:2. The trace with the receipt chain reads best at
gallery size.

---

## Additional info

| Field | Answer |
|---|---|
| **Submitter Type** | **DECIDE.** *Individuals* unless you are entering as Blockintel Inc |
| **Submitter country of residence** | United States |
| **Which Category are you submitting to?** | **Taskmaster** |
| **Organization name** (required field) | **DECIDE.** `Blockintel Inc`, or `N/A` if submitting as an individual |
| **What date did you start this project?** | `08-19-26`, the first commit: *Deterministic core: schemas, signed hash-chain provenance, Thoothukudi KB, triage engine*. 243 commits through 30 Aug |
| **URL to your public or private code repo** | `https://github.com/4KInc/anbu-care` |
| **Did you add Reproducible Testing instructions to your README?** | **Yes** |
| **Hosted project URL** | `https://anbu-care-37j4eofpwq-el.a.run.app` |

> The repo was confirmed public (HTTP 200) on 30 Aug, so there is no need to
> share it with `testing@devpost.com` and `cloudhackathons@google.com`.

**Which Google SDK did you use?** (select all that apply)

- Agent Development Kit (ADK)
- Google GenAI SDK (google-genai)

**Which Google Cloud Service(s) did you use?** (select all that apply)

- Cloud Run
- Firestore
- Pub/Sub
- Plus any of these the list also offers: Vertex AI, Cloud Storage, Cloud
  Scheduler, Cloud Build, Artifact Registry

> The dropdown showed five options before scrolling. Select Vertex AI, Cloud
> Storage and Cloud Scheduler if they appear. All three are genuinely used and
> Cloud Scheduler is load-bearing for the Architecture axis: it is what makes
> the one-hour and thirty-day regulatory clocks real rather than a timer inside
> a request.

**Which Google AI Models did you use?**

**255 characters maximum, single line**, confirmed by the form rejecting a
longer paste. This is 249:

```
Gemini 3.5 Flash (five-agent ADK fleet on Vertex AI: document vision over bills, lab reports and discharge summaries; Tamil voice-note transcription; translation; policy-clause matching). Gemini 2.5 Flash Lite (detecting the language she writes in).
```

> **Two models, five call sites, nothing else.** 3.5 Flash runs the agent fleet,
> document vision, Tamil transcription and translation. 2.5 Flash Lite does one
> job: deciding which language she actually writes in.
>
> **Why the second one is smaller.** That question has a two-letter answer, on a
> path that already owes her a reply inside fifteen seconds, and spending a
> frontier model on a one-token classification is the wrong trade. It is an
> architecture reason rather than a model count, and a test asserts the detector
> cannot silently fall back to `settings().model`. The full reasoning is in the
> *How we built it* story, so it is not lost by shortening this field.
>
> **What is deliberately NOT claimed.** Agent Engine Memory Bank can do semantic
> recall, which would put an embedding model on this list. Ours does not: recall
> is an exact scope lookup, because Memory Bank matches scopes exactly and a
> keyed lookup fits the rule that guards are code rather than similarity. So
> `text-embedding-005` would be a plausible third entry that we never invoke,
> and a judge who greps the repo for it finds nothing.
>
> **Gemma is not in the build** and is claimed nowhere. Re-probed on 30 Aug
> through the client the app uses: `gemini-3.5-flash` and
> `gemini-2.5-flash-lite` both answer, `gemma-3-27b-it` and `gemma-3-12b-it`
> both return 404.

**Architecture diagram: ready.** Upload `docs/architecture.png`, rendered from
`docs/architecture.mmd`. Five numbered bands, with the deterministic guard layer
drawn as its own layer and the request spine running straight through it. That
separation is the architecture: a diagram that mixed the two would describe a
different system. Regenerate with
`npx -y @mermaid-js/mermaid-cli -i docs/architecture.mmd -o docs/architecture.png -b white --scale 2`.

**Startup Prize fields: DECIDE.** Only if you are entering as Blockintel Inc,
which requires the incorporated organisation name and a corporate email
(`heartlinmachado@blockintelai.com`). Leave both blank otherwise.

---

## Testing instructions (optional field, seen by judges, not public)

**255 characters maximum, single line.** Everything that does not fit lives in
the README, which is what the *Reproducible Testing instructions* field points
at anyway. This is 245:

```
No GCP creds: make install && make test = 1,221 offline tests. The chain is public: /api/cases/case-da1c2cb6db/verify -> verified true, 8 receipts; case-a7cf9fa613 -> verified false, broken_at_seq 1. /api/healthz names every simulated component.
```

> Chosen for what a judge cannot get anywhere else on the form: that the suite
> needs no credentials, that the integrity claim is checkable from outside the
> process with no login, and that there is a deliberately broken case so the
> check has something to catch. The hosted URL is omitted because it has its own
> field.

### The longer version, for the README rather than this box

```
No GCP credentials needed for the test suite:

  git clone https://github.com/4KInc/anbu-care && cd anbu-care
  make install
  make test                 # 1,221 tests, all offline, no model access
  make demo                 # the full spine with no LLM in the loop,
                            # ending with a tamper the chain catches

The chain is public. Anyone can verify it, with no login:

  curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/case-da1c2cb6db/verify
  -> verified: true, receipt_count: 8

  And a case tampered with on purpose, kept broken so the check has something
  to catch:

  curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/cases/case-a7cf9fa613/verify
  -> verified: false, broken_at_seq: 1,
     "payload does not hash to the recorded hash: content was altered"

  Note that an empty chain is a valid chain: a deleted case returns
  verified:true with receipt_count:0. The COUNT is what ties the proof to the
  story a judge just watched.

Content is refused without a credential, and that boundary is the design:

  curl -o /dev/null -w '%{http_code}' <URL>/api/parents/<any-id>      -> 401
  curl -o /dev/null -w '%{http_code}' <URL>/api/cases/<any-id>/verify -> 200

  Public where it proves; private where it reveals.

Health, naming which components are simulated:

  curl -s https://anbu-care-37j4eofpwq-el.a.run.app/api/healthz

WHAT IS SIMULATED, labelled in the product rather than only here: the insurer's
adjudicator is deterministic local rules; payments run on a real Razorpay link
in TEST mode and move no real money. What is NOT simulated: WhatsApp (real
Twilio to real handsets, freeform-only inside the 24h window), the clinic
booking (a real headless browser on a real centre's site), the regulatory clocks
(real Cloud Scheduler, real wall time), hospital locations (Places place_id plus
a verification date), and the receipt chain. All demo data is synthetic.
```

---

## Bonus-points fields

**Link to a piece of content: DECIDE.** The article is written and ready to
paste at `docs/content/devto-article.md`, with its title, four tags and body. It
carries the required "created for the purposes of entering this hackathon" line
in the first paragraph and again at the end. Publish it at `dev.to/new`, public
rather than unlisted, and paste the URL here. `docs/content/build-log-post.md`
is the longer version it was adapted from; you do not need both.

**Link to a social media post: DECIDE.** `docs/content/social-post.md` has three
drafts, all tagged `#AllThingsAgenticHackathon` with the attribution line. The X
thread is recommended. Post **one** and paste the URL.

---

## Before you hit submit

- [ ] Architecture diagram uploaded from `docs/architecture.png`. The form
      rejects a blank file.
- [ ] Video recorded from `DEMO_SCRIPT_4MIN.md`, public rather than unlisted
- [ ] `make booking-mode MODE=live` before recording; it is currently dry
- [ ] `/api/healthz` returns ok, and its labels still match the "what is
      simulated" wording above
- [ ] Both canonical cases still verify as stated
- [ ] `make test` still reads 1,221, or every number here is updated
- [ ] dev.to article and social post live, URLs pasted
- [ ] Category is **Taskmaster**
