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

The agent UI is at [`/dev-ui/`](https://anbu-care-37j4eofpwq-el.a.run.app/dev-ui/).
Access is open because the receipt chain is meant to be independently checkable —
see [Public access](#public-access-under-domain-restricted-sharing). All demo data
is synthetic. Liveness probe is `/api/healthz`.

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
- SLA tracking against the IRDAI 2024 Master Circular's 1-hour cashless and
  30-day reimbursement clocks, against real wall time.
- The signed, tamper-evident receipt chain, and the verification that detects
  a silent edit.

**Simulated, and labelled as such everywhere it appears:**

- **The insurer/TPA response.** No production insurer or TPA API exists to
  integrate against in this window. The liaison agent submits to a simulated
  endpoint. Packet assembly and SLA tracking are real; only the counterparty's
  answer is mocked.
- **The hospital knowledge base.** `anbu_care/kb/data/hospitals_thoothukudi.json`
  is a dated seeded snapshot, not a live capability feed. Capability and
  empanelment values must be spot-checked against current listings before any
  public writeup or recorded narration.
- **WhatsApp delivery.** Production template approval takes roughly 10–15
  business days and will not clear inside the hackathon window, so sends go to
  the WhatsApp Business API sandbox.

Every market figure quoted in the project brief is directional and unverified.
See [`docs/CITATIONS.md`](docs/CITATIONS.md) before repeating any of them.

---

## Quick start

```bash
make install          # uv sync --extra dev
make test             # 73 tests, no GCP or model access needed
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
| **Gemini 3.5 (Vertex AI)** | Multimodal reasoning over discharge summaries, lab reports, ECG images, prescriptions, and bills; policy-clause matching. Configurable via `ANBU_MODEL` — any Gemini 3.5+ model satisfies the mandate. |
| **Google ADK** | Five sub-agents with isolated tool scopes under one coordinator. |
| **Cloud Run** | Hosts the agent API and the dashboard endpoints. |
| **Firestore** | Case state and the hash-chained receipt ledger, single-table PK/SK. |
| **Pub/Sub** | Async multi-day case tracking — intake events, case updates, claim status. |
| **Memory Bank** | Persistent cross-session context across weeks of a case. |

---

## Architecture

```
              NRI Family (mobile/web app + WhatsApp)
                              |
                 +------------+------------+
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
 +----+-----+ +-----+------+   +--------+-------+  +----------------+
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
tests/                  73 tests, no GCP or model access needed
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
| `GET /api/cases/{id}/verify` | Independent chain verification — deliberately unauthenticated |

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

- **Ingestion provenance receipts.** Documents are ground-truthed by a stored
  count today; a `document.ingested` receipt with a content hash would also make
  *stored-then-altered* detectable. The topology audit in
  [`docs/proposals/ingestion-provenance.md`](docs/proposals/ingestion-provenance.md)
  found the chain core is **already subject-agnostic** — `verify_chain`,
  sequence and `prev_hash` logic carry no case knowledge, so this is a new PK
  prefix rather than a refactor. Deliberately deferred, not unexamined: the
  case-chain tamper beat already carries the verifiability story, so the hour
  went to the adjudicator instead.

## Disclosure

Anbu Care reimplements two patterns from our own prior work, written fresh
during the submission window. See [`DISCLOSURE.md`](DISCLOSURE.md).

## Licence

MIT — see [`LICENSE`](LICENSE).
