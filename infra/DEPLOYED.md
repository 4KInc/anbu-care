# Deployed environment

Current state of the live hackathon environment. Update this when it changes.

## Google Cloud

| Item | Value |
|---|---|
| Project | `anbu-care-hack` (number `473806191488`) |
| Organization | `blockintelai.com` (`227631295422`) |
| Region | `asia-south1` (Mumbai) — same region as Firestore, so reads are local |
| Firestore | Native mode, `(default)` database, `asia-south1` |
| Composite index | `anbu` collection: `pk` ASC, `sk` ASC — **READY** |
| Pub/Sub topics | `anbu-intake-events`, `anbu-case-updates`, `anbu-claim-status` |
| Model | `gemini-3.5-flash` via Vertex AI, location `global` |
| Cloud Run service | `anbu-care`, revision `anbu-care-00001-9s7` |
| Service URL | https://anbu-care-37j4eofpwq-el.a.run.app |

## Verified live

`scripts/verify_stack.py` passes all five checks against the real project:
ADK agents, Vertex AI + Gemini 3.5, Firestore read/write/range-query, Pub/Sub
topics, and a stable signing key.

The full case flow has been run against real Firestore and Pub/Sub — 9 receipts
persisted, reloaded into a fresh chain object, and verified.

A live agent conversation through Vertex produced the intended behaviour: the
coordinator delegated to triage, triage held HIGH severity against a caller
saying "she says it's probably just gas", cited the cashless-network difference
rather than the tied capability score, and surfaced both the seeded-KB caveat
and the unconfirmed-location assumption without being asked.

## Access

Public access (`allUsers`) is **refused** by the organization policy
`constraints/iam.allowedPolicyMemberDomains`, which restricts IAM members to the
`blockintelai.com` Workspace customer. The deploy reports this as a warning, not
an error, so the service silently ends up reachable only from inside the domain.

Current invoker: `user:heartlinmachado@blockintelai.com`.

To reach it:

```bash
gcloud run services proxy anbu-care --region=asia-south1 --port=8080
curl localhost:8080/healthz
```

**Before submission**, judges will need access without a `blockintelai.com`
account. Two options:

1. Add a project-level exception to `iam.allowedPolicyMemberDomains` for
   `anbu-care-hack`, then re-run the `allUsers` invoker grant. This weakens a
   deliberate org-wide control on one project — an owner's decision, not a
   default.
2. Submit a recorded demo plus the locally reproducible `make demo`, and keep
   the deployed service domain-only.

## Setup that had to be done once

Both of these fail in ways that do not name the real cause — see the README's
"First deploy on a fresh project" section.

1. Firestore composite index (`pk` ASC, `sk` ASC on `anbu`). Without it, writes
   succeed and chain reads fail at query time.
2. Build roles on the default compute service account
   (`473806191488-compute@developer.gserviceaccount.com`):
   `storage.objectViewer`, `logging.logWriter`, `artifactregistry.writer`,
   `cloudbuild.builds.builder`.

## Cost note

Billing account `011B69-475206-64389F` ("Default billing") is linked. Running
costs are Cloud Run (scales to zero), Firestore (tiny), Pub/Sub (tiny), and
Vertex AI inference per call.
