# Anbu Care

Autonomous eldercare and insurance coordination for NRI families — a multi-agent
system that coordinates healthcare and insurance for aging parents in India on
behalf of their adult children abroad.

Built for the **All Things Agentic Hackathon** (Google / Devpost), Taskmaster track.

### 🟢 Live demo — no login required

**https://anbu-care-37j4eofpwq-el.a.run.app**

```bash
URL=https://anbu-care-37j4eofpwq-el.a.run.app
PARENT=$(curl -sX POST $URL/api/demo/seed | jq -r .parent_id)
CASE=$(curl -sX POST $URL/api/intake -H 'content-type: application/json' \
  -d "{\"parent_id\":\"$PARENT\",\"symptoms\":[\"chest pain\"],\"reported_by\":\"judge\"}" | jq -r .case_id)
curl -s $URL/api/cases/$CASE/verify | jq        # verify the signed chain yourself
```

**Family dashboard:** [`/app`](https://anbu-care-37j4eofpwq-el.a.run.app/app) ·
**agent UI:** [`/dev-ui/`](https://anbu-care-37j4eofpwq-el.a.run.app/dev-ui/)

Two access models, both enforced server-side — this contrast is deliberate:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/parents/{parent_id}     # 401 — clinical content
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/cases/{case_id}/verify  # 200 — open to everyone
```

Verification proves the record was not altered *without revealing what it says*,
which is why it needs no credential. Anything returning case or patient content
requires a family session. The demo credential is
`anbu-demo-family-token` — published deliberately: secrecy is not what is being
demonstrated, server-side enforcement is. Take the token out of the page and the
401 still happens.

All demo data is synthetic. Liveness probe is `/api/healthz`.

> *"If something happens to my parent right now, who makes sure the right decisions
> get made, fast, and that I actually know what's happening?"*

Today that role is a family friend, a paid proxy, or a frantic sibling WhatsApp
thread. Every existing service — Sahaayak, Samarth Care, Care247, Policybazaar's
NRI Care Program — answers it with a human coordinator. Anbu Care answers it with
an agentic decision layer that knows the parent's history, the local hospital
landscape, and the insurance policy, and acts within minutes.

---

## What is real, and what is not

This matters more than any feature list, so it comes first.

**Real and demonstrable:**

- Multimodal document parsing into structured, queryable observations, with
  "new and abnormal" distinguished from "consistent with known baseline".
- Severity classification and hospital routing, including the explanation of
  what was traded for extra travel distance.
- The WhatsApp compliance boundary — enforced in code, before send.
- Claim packet assembly, coverage and sub-limit checks, and STEP_UP evidence
  scoring.
- SLA tracking against the regulatory cashless and reimbursement windows, run
  against real wall time. The two window lengths are implemented as real
  deadlines in `anbu_care/service.py` and are quoted from the IRDAI 2024 Master
  Circular, but that citation is **not yet verified against the source
  document** — see [`docs/CITATIONS.md`](docs/CITATIONS.md), where both are
  flagged load-bearing. The clock machinery is real either way; the specific
  window lengths should be checked before anyone narrates them as fact.
- The signed, tamper-evident receipt chain, and the verification that detects
  a silent edit.
- **Wellbeing check-in over inbound WhatsApp**, including voice notes. A voice
  note is stored, transcribed by Gemini in a single call, and read for symptom
  terms — and the audio, not the transcript, is treated as the record. Alerts
  say "we heard", never "she said". Multilingual and verified from a real
  handset in Tamil script, transliterated Tamil, Hindi and code-mixed English.
- **Escalation and care-circle notification.** A recognised red flag opens a
  case, runs triage, alerts the family, and can ring a care-circle contact.
- **Per-purpose consent, read live.** Six purposes, with the inbound and
  outbound sets deliberately disjoint. Conflating them was a real shipped
  defect, since fixed and regression-tested.
- **Google Places verification of the hospital knowledge base**, and a real map
  of the routing decision. Verification caught a genuine bug: the previously
  seeded coordinates were out by 1.4 to 5.0 km, which changed which hospital was
  nearest and therefore what the routing explanation claimed.
- **An emergency clinical summary for the treating team**, reachable by a
  clinician with no login through a scoped link the family mints and can revoke.
  Allergies first, every line traced to the stored field it came from, and no
  field anywhere an instruction could live in — this is the record, not the
  doctor.
- **Clinician notes, typed or spoken.** A voice note is transcribed by Gemini
  and shown back for confirmation; **an unconfirmed transcript writes nothing**.
  The receipt carries the hash of the confirmed text, never the words.
- **Bill capture.** A family member photographs a hospital bill over the same
  WhatsApp thread, Gemini reads the line items, and the existing sub-limit rules
  produce an itemised covered / not-covered / you-pay split with a running total
  across the stay. The photograph is kept privately so every number stays
  checkable against the paper it came from.
- **A decision trace**: the chain rendered as the sequence a person can follow —
  the counterparty raising a query, the agent gathering what was asked for, the
  re-adjudication that followed. One step per receipt, so the view cannot
  invent a beat the chain does not contain.

**Simulated, and labelled as such everywhere it appears:**

- **The insurer/TPA response.** No production insurer or TPA API exists to
  integrate against in this window. The liaison agent submits to a simulated
  endpoint. Packet assembly and SLA tracking are real; only the counterparty's
  answer is mocked.
- **Hospital capability and insurer empanelment.**
  `anbu_care/kb/data/hospitals_thoothukudi.json` is split provenance, and only
  half of it is seeded. **Locations are real:** all five hospitals carry a
  Google Places `place_id` and a `location_verified_on` date, so identity,
  coordinates and therefore distance are verified rather than guessed.
  **Capability and empanelment are seeded** and must be spot-checked against
  current listings before any public writeup or recorded narration. Google can
  say where a hospital is; it cannot say who bills which insurer, or which
  centre can run a cath lab at 2am.
- **WhatsApp delivery.** The gate decision is real and always has been; the
  transport behind it is real code with real credentials. Whether a message
  actually leaves depends on the configured provider, and the system never
  claims a send that did not happen — a permitted-but-undelivered message is
  recorded as `comms.not_delivered`, with `sent_at` left unset.

  Two providers are wired, and **Twilio is the working path.** The account is
  upgraded (`type: Full`), and a freeform `Body` send with no `ContentSid`
  reaches a real handset — verified end to end on 2026-08-22, `status:
  delivered`. This reverses an earlier note in this README: on a Twilio *trial*
  account freeform sends are rejected with `21654 ContentSid Required` while the
  Content API that would satisfy it returns `20003 not available on a Trial
  account`. That catch-22 is real, and upgrading is what resolves it.

  **Meta's Cloud API is implemented but is a dead end on this project.** The
  test sender registers and connects, the token is valid, and sends still fail
  `131030 Recipient phone number not in allowed list` — the only UI for that
  allow-list permanently reports "No phone numbers available for this app",
  reproduced across two apps in two separate business portfolios. There is no
  public Graph API for the list. The code stays because it works given a
  populated allow-list; it is not the path this demo uses.

  One live constraint remains on the working path: the account has **no Content
  templates**, so freeform sends only succeed inside WhatsApp's 24-hour customer
  service window. A cold, business-initiated send still fails, and fails
  honestly as `comms.not_delivered`.

  Neither is general production reach. Sending to an arbitrary number needs Meta
  business verification and template approval, roughly 10–15 business days.
  Whatever the transport, **clinical detail never traverses WhatsApp** — that is
  the classifier's job, and it runs before the transport is reachable at all.

**Real, but narrower than it looks:**

- **Wellbeing check-ins are self-reported, never a measured vital.** The
  dashboard labels them so. An absent entry reads "no check-in yet" — a missing
  check-in is not evidence that anything is well.
- **A transcript is not what she said.** It is what a model heard. That is why
  the audio is retained and playable, and why nothing in the system attributes
  the transcript to her as speech.
- **The care circle is a set of notified parties, not integrated providers.**
  Nobody in it can reply into the system, and no hospital or clinician system is
  connected in either direction.
- **A placed call is not an answered call.** The telephony reports that a call
  was placed; whether anyone picked up is outside what this system can know, and
  it never claims otherwise.
- **Anbu Care does not sense anything.** There is no location tracking, no
  monitoring, and no autonomous detection. Every input is something a person
  deliberately sent.
- **The handoff link cannot say who opened it.** A link is a bearer credential,
  so the access receipt records that the summary was read and when, and states
  in its own payload that this was a link holder rather than an identified
  clinician. Naming a doctor it cannot verify would be the same false claim as
  reporting a message delivered when the provider only accepted it.
- **A clinician note is attributed to whoever held the link**, and the system
  cannot check that either. What it can prove is that the text was confirmed
  before it was written, and that nobody has altered it since.
- **The trace shows the deciding; it is not the deciding.** It renders receipts
  that already exist. Where a step left no receipt it shows nothing — which is
  how the gather in the claim loop was found to be invisible, and then fixed by
  recording it rather than by inferring it.
- **Nothing here is a hospital integration.** No EHR is connected, nobody writes
  back, and the clinician view says so on its own face.
- **A bill split is an estimate, not a settlement.** It comes from the policy
  rules, not from the insurer, who has not been asked and does not know this
  system exists. Every field is named `estimated_`, and `settled_inr` stays
  None until a real adjudication says otherwise — because nobody having decided
  is a different thing from nothing being owed. On a reimbursement claim the
  family pays first and is repaid later, so even a correct estimate is not
  money they hold.
- **An extracted amount is a reading, not a fact.** A model that reads ₹96,000
  where the paper says ₹9,600 produces a number that looks exactly as
  authoritative as a correct one. So the lines are checked against the bill's
  own printed total, a mismatch is flagged rather than reconciled, and the
  image is retained — credentialed — so the figure can be checked. A bill whose
  photograph could not be stored is refused outright rather than recorded as
  unverifiable numbers.

Every market figure quoted in the project brief is directional and unverified.
See [`docs/CITATIONS.md`](docs/CITATIONS.md) before repeating any of them.

---

## Quick start

```bash
make install          # uv sync --extra dev
make test             # 499 tests, no GCP or model access needed
make demo             # the full spine, end to end, with no model in the loop
```

`./scripts/demo_run.sh` drives the **deployed** service through the full demo
narrative — fresh synthetic cases each run, a separate throwaway case for the
tamper beat, and `--reset` to clean up. The beat sheet is
[`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

`make demo` runs `scripts/demo_spine.py`: onboarding, document ingestion, the
triage decision, the WhatsApp gate, packet assembly, the STEP_UP gate,
submission with a live SLA clock, and then it tampers with a receipt to show the
chain catching it. Nothing in it depends on what an LLM does on the day.

To talk to the agents:

```bash
gcloud auth application-default login
cp .env.example .env          # then set GOOGLE_CLOUD_PROJECT
make keygen                   # mint a stable signing key, put it in .env
make verify-stack             # confirm Vertex, Firestore, Pub/Sub are reachable
make chat                     # terminal conversation with the coordinator
make serve                    # agent API + ADK dev UI on :8080
```

Note that `gcloud auth application-default login` resets ADC's quota project to
your gcloud default every time you run it, which silently bills and meters every
Vertex and Firestore call against the wrong project. `.env` pins
`GOOGLE_CLOUD_QUOTA_PROJECT` so that reset cannot take effect — verified by
pointing ADC at the wrong project and confirming Vertex still resolves correctly.

---

## The stack

Every mandatory requirement, and where it is actually load-bearing.

| Requirement | Used for |
|---|---|
| **Gemini 3.5 (Vertex AI)** | Multimodal reasoning over discharge summaries, lab reports, ECG images and prescriptions; policy-clause matching; and single-call transcription of inbound WhatsApp voice notes. Deployed default is `gemini-3.5-flash`, configurable via `ANBU_MODEL` — any Gemini 3.5+ model satisfies the mandate. |
| **Google ADK** | Five sub-agents with isolated tool scopes under one coordinator. |
| **Cloud Run** | Hosts the agent API, the Twilio webhook, and the dashboard. |
| **Firestore** | Case state and the hash-chained receipt ledger, single-table PK/SK. |
| **Pub/Sub** | Async multi-day case tracking — intake events, case updates, claim status. |
| **Memory Bank** | Wired via `ANBU_MEMORY_SERVICE_URI`, but **the deployed revision runs the in-memory fallback** — `/api/healthz` reports `"memory_bank": "in-memory (not persistent)"`. Cross-session persistence is therefore not live. |

---

## Architecture

```
   Parent in India                    NRI Family (dashboard + WhatsApp)
   (WhatsApp voice note / text)                     |
              |                                     |
   +----------v-----------+                         |
   | Inbound wellbeing    |  Gemini transcribes     |
   | (Twilio webhook)     |  audio in ONE call;     |
   | audio is the record, |  RED_FLAGS table (code) |
   | transcript derived   |  decides, not the model |
   +----------+-----------+                         |
              |                                     |
              |  red flag -> open case, triage      |
              +------------------+------------------+
                                 |
                 +------------+--+---------+
                 |  Onboarding / KB Agent  |
                 |  (medical hx, docs,     |
                 |   insurance baseline)   |
                 +------------+------------+
                              |
                 +------------+------------+
                 |   Coordinator (root)    |  (ADK)
                 +------------+------------+
                              |
      +-------------+---------+---------+-------------------+
      |             |                   |                   |
 +----v-----+ +-----v------+   +--------v-------+  +--------v-------+
 | Triage   | | Evidence / |   | Insurer/TPA    |  | WhatsApp Comms |
 | Agent    | | STEP_UP    |   | Liaison Agent  |  | Agent          |
 | severity+| | (pre-submit|   | (submits pre-  |  | (template-     |
 | location+| |  evidence  |   |  validated     |  |  compliant     |
 | hospital | |  enrich)   |   |  packet;       |  |  status to     |
 | matching | |            |   |  *SIMULATED*   |  |  parent + NRI) |
 +----+-----+ +-----+------+   +--------+-------+  +-------+--------+
      |             |                   |                  |
      |             |                   |          +-------v--------+
      |             |                   |          | Care circle    |
      |             |                   |          | NOTIFIED only, |
      |             |                   |          | never          |
      |             |                   |          | integrated     |
      |             |                   |          +----------------+
      |             |                   |
      +-------------+---------+---------+
                              |
                 +------------v------------+
                 | Signed Receipt /        |
                 | Provenance Chain        |
                 | (Firestore, hash-chained|
                 +------------+------------+
                              |
                 +------------v------------+
                 | Memory Bank             |
                 | (cross-session, multi-  |
                 |  month family + policy) |
                 +-------------------------+
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the design decisions and
why each one was made.

---

## The one idea worth stealing

**Guardrails that matter are code, not prompts.**

Three things in this system must hold on every single run, including the run
where the model is confused, the caller is reassuring, or someone is trying to
talk their way past the boundary:

1. **A red-flag symptom escalates.** `anbu_care/triage/severity.py` decides
   severity. The agent relays it and is instructed never to soften it. "She says
   it's probably just gas" does not downgrade chest pain.

2. **Clinical detail never leaves over WhatsApp.** `anbu_care/comms/policy.py`
   classifies the *content*, not the caller's claim about it. A message declared
   as `logistics` that carries a troponin value is blocked anyway — and the block
   is written to the receipt chain as evidence the boundary held.

3. **A decision cannot be silently rewritten.** Every consequential action
   appends a signed receipt whose hash covers the previous one. Editing an
   earlier entry breaks every hash after it, and `verify_case_chain` reports
   exactly where.

An agent that is merely *told* not to leak a lab value is not a control.

---

## Repository layout

```
anbu_care/
  agent.py              root coordinator — ADK entrypoint (`root_agent`)
  agents/               five sub-agents, one file each
  tools/                one tool module per agent — isolated scopes
  triage/               severity rules + hospital ranking (deterministic)
  comms/                WhatsApp message policy (deterministic)
  provenance/           hash chain, Ed25519 signing, Firestore/memory store
  kb/                   seeded Thoothukudi hospital knowledge base
  service.py            case state transitions, SLA clocks, Pub/Sub
  server.py             Cloud Run entrypoint (ADK API + dashboard routes)
scripts/
  demo_spine.py         end-to-end run, no model in the loop
  verify_stack.py       confirms Vertex / Firestore / Pub/Sub are reachable
tests/                  499 tests, no GCP or model access needed
infra/deploy_cloud_run.sh
```

---

## HTTP endpoints

Beyond ADK's own agent API:

| Route | Purpose |
|---|---|
| `GET /api/healthz` | Liveness, plus whether the signing key is stable and Memory Bank is wired |
| `GET /api/hospitals` | The seeded KB, served with its provenance attached |
| `POST /api/demo/seed` | Create the Thoothukudi demo family, returns its `parent_id` |
| `POST /api/intake` | Direct triage — how an automated intake signal enters the system |
| `GET /api/parents/{id}` | Baseline record and every ingested document |
| `GET /api/cases/{id}` | Case metadata and current chain head |
| `GET /api/cases/{id}/trail` | Every decision on the case, in order, with hash links |
| `GET /api/cases/{id}/brief` | The arrival brief, degrading to "not yet known" in every hole |
| `GET /api/cases/{id}/verify` | Independent chain verification — deliberately unauthenticated |
| `POST /api/wellbeing/inbound` | Twilio webhook — inbound check-ins, text and voice notes |
| `GET /api/parents/{id}/wellbeing` | Check-in history for a parent |
| `GET /api/parents/{id}/care-circle` | Care-circle contacts and their consent purposes |
| `POST /api/cases/{id}/notify-claim` | Claim-status message to the family, through the comms gate |
| `POST /api/cases/{id}/notify-care-circle` | Care-circle notice, through the comms gate |
| `POST /api/intake-signal` | Structured intake event |
| `GET /api/intake-channels` | Which intake channels are configured |
| `GET /api/map-config` | Maps browser key and the hospital-provenance source note |
| `GET /api/cases/{id}/trace` | The decision sequence — one step per receipt, nothing synthesised |
| `POST /api/cases/{id}/handoff-link` | Mint an emergency-access link for a treating clinician |
| `POST /api/cases/{id}/handoff-link/revoke` | Kill every outstanding link for the case |
| `GET /api/cases/{id}/bills` | Photographed bills and the estimated policy split |
| `GET /api/cases/{id}/bills/{bill_id}/image` | Short-lived signed URL for the source photograph |
| `GET /handoff/{token}` | The clinician's read-only summary. No login, by design |
| `POST /handoff/{token}/note/draft` | Transcribe a spoken note for review. Writes nothing |
| `POST /handoff/{token}/note/confirm` | Record a confirmed note |
| `GET /app` | The dashboard |

Everything returning case or patient content sits behind
`require_case_access` / `require_family_session` and answers **401** without a
session. Two deliberate exceptions, and they are different in kind:

- `/api/cases/{id}/verify` proves integrity **without revealing content** — no
  name, no allergy, no hospital, no rupee figure — so it needs no credential.
- `/handoff/{token}` shows clinical content to someone who has never
  authenticated, because a nurse holding an unconscious patient will not create
  an account. It is not a second way in: only a caller who already holds a
  family session can mint the token, so it delegates a subset of that access
  rather than bypassing it. One case, sixty minutes, revocable, and every open
  writes a receipt the family can see.

Smoke-testing a fresh deploy is three calls:

```bash
PARENT=$(curl -sX POST $URL/api/demo/seed | jq -r .parent_id)
CASE=$(curl -sX POST $URL/api/intake -H 'content-type: application/json' \
  -d "{\"parent_id\":\"$PARENT\",\"symptoms\":[\"chest pain\"],\"reported_by\":\"neighbour\"}" \
  | jq -r .case_id)
curl -s $URL/api/cases/$CASE/verify | jq
```

The verify route is unauthenticated on purpose. The point of a receipt chain is
that a family or an insurer can check it without trusting us to run the check
for them.

---

## Deploying

```bash
make keygen                                  # mint a stable Ed25519 key
export ANBU_SIGNING_KEY_B64=...              # deploy refuses without it
make deploy                                  # gcloud run deploy --source .
```

The deploy script refuses to run without a stable signing key. An ephemeral key
means each revision signs with a different one, and receipts written before a
restart stop verifying — which defeats the entire point of the chain.

### First deploy on a fresh project

Two things bite once, and neither is obvious from the error message.

**1. Firestore needs a composite index.** The single-table layout queries
pk-equality + sk-range + order-by, which Firestore refuses without one — and it
fails at *query* time, so writes succeed and reads blow up. Create it before the
first real run:

```bash
gcloud firestore indexes composite create --collection-group=anbu \
  --field-config=field-path=pk,order=ascending \
  --field-config=field-path=sk,order=ascending
```

The definition is recorded in [`infra/firestore.indexes.json`](infra/firestore.indexes.json).

**2. The default compute service account needs build roles.** A fresh project's
`PROJECT_NUMBER-compute@developer.gserviceaccount.com` cannot read the source
bucket Cloud Build uploads to, and the deploy fails with a `storage.objects.get`
403:

```bash
SA=PROJECT_NUMBER-compute@developer.gserviceaccount.com
for role in roles/storage.objectViewer roles/logging.logWriter \
            roles/artifactregistry.writer roles/cloudbuild.builds.builder; do
  gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:$SA" --role="$role" --condition=None
done
```

### Public access under domain-restricted sharing

`--allow-unauthenticated` grants `roles/run.invoker` to `allUsers`. If your
organization enforces `constraints/iam.allowedPolicyMemberDomains`, that grant is
**refused and the deploy still reports success** — you get a service nobody can
reach, with only a warning in the output.

`infra/deploy_cloud_run.sh` therefore uses `--no-invoker-iam-check` instead,
which is Google's documented alternative for projects under DRS. It is scoped to
the one service and modifies no organization policy.

```bash
# make public
gcloud run services update anbu-care --region=asia-south1 --no-invoker-iam-check
# make private again
gcloud run services update anbu-care --region=asia-south1 --invoker-iam-check
```

### Liveness is `/api/healthz`, not `/healthz`

Google Front End reserves the bare `/healthz` path and never forwards it to the
container, so a route defined there works locally and returns 404 in Cloud Run —
with no `server: Google Frontend` header, which is the tell. The liveness route
is served under `/api/` for that reason. See `infra/DEPLOYED.md`.

### WhatsApp delivery credentials

Secrets are read from the environment only — never committed, never logged,
never baked into an image layer. Locally they go in `.env` (gitignored); on
Cloud Run, inject them as secrets rather than plain env vars.

**Meta Cloud API** (implemented, but a dead end on this project — see the honesty note above):

```bash
# From the Meta app dashboard: WhatsApp -> API Setup
ANBU_WHATSAPP_MODE=meta
WHATSAPP_ACCESS_TOKEN=...        # "Generate access token" — temporary, expires
WHATSAPP_PHONE_NUMBER_ID=...     # the numeric ID, not the phone number
WHATSAPP_API_VERSION=v25.0
```

No conversation window exists until the recipient has written to you, so the
first contact must be a pre-approved template. `transport.open_session(to)`
sends `hello_world` for exactly that purpose. It is an operational handshake,
not a family update: it carries no case content and does not go through the
gate, because there is nothing to classify.

**Twilio** (the working path; needs a non-trial account, see the honesty note above):

```bash
ANBU_WHATSAPP_MODE=twilio
TWILIO_ACCOUNT_SID=AC...
TWILIO_API_KEY_SID=SK...         # preferred: revocable without rotating the
TWILIO_API_KEY_SECRET=...        # account auth token
TWILIO_WHATSAPP_FROM=whatsapp:+1...   # your sandbox sender, NOT the docs default
```

On Cloud Run:

```bash
printf %s "$WHATSAPP_ACCESS_TOKEN" | gcloud secrets create whatsapp-access-token --data-file=-

gcloud run services update anbu-care --region=asia-south1 \
  --update-secrets=WHATSAPP_ACCESS_TOKEN=whatsapp-access-token:latest \
  --update-env-vars=ANBU_WHATSAPP_MODE=meta,WHATSAPP_PHONE_NUMBER_ID=...
```

The runtime service account needs `roles/secretmanager.secretAccessor` on those
secrets. `ANBU_WHATSAPP_MODE=off` (the default) records gate decisions and sends
nothing.

### Runtime service account needs explicit roles

If your organization enforces
`constraints/iam.automaticIamGrantsForDefaultServiceAccounts`, the Cloud Run
runtime identity gets no Editor role and every Firestore write fails with a 500.
Grant it explicitly:

```bash
SA=PROJECT_NUMBER-compute@developer.gserviceaccount.com
for role in roles/datastore.user roles/pubsub.publisher roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:$SA" --role="$role" --condition=None
done
```

Then deploy a new revision — running instances do not pick up IAM changes.

## Future work

- **Gemma as an intake normalizer — DEFERRED, precheck negative (not pending).** The plan was a small model converting messy
  free-text intake ("amma fell, chest heavy, sweating, BP 160") into the
  structured fields the deterministic triage table consumes — advisory input
  only, never able to set severity or write a receipt. **Not built:** Gemma is
  not available as a managed endpoint on this project's Vertex AI (absent from
  the publisher listing; `gemma-3-27b-it`, `gemma-3-12b-it` and `gemma-2-9b-it`
  all return 404 on `generateContent`). Serving it would mean deploying from
  Model Garden to a dedicated GPU-backed endpoint, billed per hour rather than
  per token. Deferred as infra cost out of proportion to a component that by
  design cannot change any decision.
- **Per-analyte reference change values.** A repeat lab reading drifts for
  reasons that are not clinical, so changes inside a flat 10% band are narrated
  as "within normal variation" rather than "new and abnormal" — which is what
  stops a 1.7% cholesterol drift burying an HbA1c move from 7.1 to 8.4. A flat
  percentage is the crude version: real practice derives a reference change
  value per analyte from assay and biological variation. The band is narrative
  only — the high/low flag against the reference range is untouched, and no
  triage or adjudication decision reads it.
- **Ingestion provenance receipts.** Documents are ground-truthed by a stored
  count today; a `document.ingested` receipt with a content hash would also make
  *stored-then-altered* detectable. The topology audit in
  [`docs/proposals/ingestion-provenance.md`](docs/proposals/ingestion-provenance.md)
  found the chain core is **already subject-agnostic** — `verify_chain`,
  sequence and `prev_hash` logic carry no case knowledge, so this is a new PK
  prefix rather than a refactor. Deliberately deferred, not unexamined: the
  case-chain tamper beat already carries the verifiability story, so the hour
  went to the adjudicator instead.

## Writeup material

- [`docs/content/build-log-post.md`](docs/content/build-log-post.md) — build-log post draft
- [`docs/content/social-post.md`](docs/content/social-post.md) — social drafts
- [`DISCLOSURE.md`](DISCLOSURE.md) — prior-work and simulated-adjudicator disclosure
- [`docs/CITATIONS.md`](docs/CITATIONS.md) — every market figure, and its verification status
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — the beat sheet
- [`infra/DEPLOYED.md`](infra/DEPLOYED.md) — the live environment

## Disclosure

Anbu Care reimplements two patterns from our own prior work, written fresh
during the submission window. See [`DISCLOSURE.md`](DISCLOSURE.md).

## Licence

MIT — see [`LICENSE`](LICENSE).
