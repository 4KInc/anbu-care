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
| Cloud Run service | `anbu-care`, public via disabled invoker IAM check |
| Scaling cap | `--max-instances=5`, containerConcurrency 80 → up to 400 in-flight requests |
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

## Public access — RESOLVED

**Public URL: https://anbu-care-37j4eofpwq-el.a.run.app**

Anonymous, no auth header, no gcloud identity:

```
GET /                    -> 307 -> /dev-ui/  (200)
GET /api/hospitals       -> 200
GET /api/cases/{id}/verify -> 200, chain verified
POST /api/demo/seed      -> 200
POST /api/intake         -> 200
POST /run                -> 200  (full multi-agent flow)
```

### The "404" was a probe-path artefact, not a routing failure

An earlier round of this investigation concluded that Cloud Run default-URL
serving was broken for this project and needed Google Support. **That was
wrong.** Only one path was ever probed — `/healthz` — and that path never
reaches the container.

The tell: with the service still private, `/healthz` returned a 404 whose
headers carried `referrer-policy: no-referrer` and **no** `server: Google
Frontend`, while every other path (`/`, `/api/hospitals`, `/nonexistent-xyz`,
`/dev-ui`) returned a clean `403` with `server: Google Frontend`. A 403 is
Cloud Run correctly rejecting an unauthorized caller — which means the hostname
routed all along.

**`/healthz` is intercepted by Google Front End and never forwarded.** It still
returns 404 today, even with the service fully public and even through an
authenticated proxy, while every sibling route returns 200.

> **Resolved:** the liveness route was moved to **`/api/healthz`**. The bare
> `/healthz` path stays unusable on Cloud Run — that is Google Front End's
> behaviour, not something the app can change — so nothing should probe it.

### The real blocker, and the fix

Only one blocker existed: the organization enforces
`constraints/iam.allowedPolicyMemberDomains` (domain-restricted sharing), which
refuses the `allUsers` invoker grant that `--allow-unauthenticated` tries to
create. `gcloud run deploy` reports that refusal as a *warning*, not an error,
so the deploy "succeeds" and leaves an unreachable service.

Fixed with `--no-invoker-iam-check` on the service. This is Google's documented
alternative for projects under DRS. It is scoped to this one Cloud Run service
and **no organization policy was modified** — DRS remains fully in force
org-wide, which is why this was preferred over a project-level policy override.

```bash
gcloud run services update anbu-care --region=asia-south1 --no-invoker-iam-check
```

**To make the service private again after judging:**

```bash
gcloud run services update anbu-care --project=anbu-care-hack \
  --region=asia-south1 --invoker-iam-check
```

That single command fully reverses public access. Nothing else needs undoing.

### Runtime service account roles

The organization also enforces
`constraints/iam.automaticIamGrantsForDefaultServiceAccounts`, which suppresses
the Editor role normally granted to the default compute service account. The
Cloud Run runtime identity therefore had **no** Firestore, Pub/Sub, or Vertex
access, and every write returned `PermissionDenied: 403` as a 500 from the app.

Granted least-privilege runtime roles to
`473806191488-compute@developer.gserviceaccount.com`:

| Role | For |
|---|---|
| `roles/datastore.user` | Firestore reads and writes |
| `roles/pubsub.publisher` | publishing case events |
| `roles/aiplatform.user` | Vertex AI inference |

IAM changes are not picked up by running instances — a new revision is required
after granting.

### Exposure to weigh before judging

The service is now fully public, including `POST /api/demo/seed`,
`POST /api/intake`, the ADK dev UI, and the `/run` agent API. Anyone who finds
the URL can drive the agents and therefore spend Vertex AI inference against
this project. For a short judging window that is the intended trade.

Mitigation applied: `--max-instances=5`. With containerConcurrency 80 that still
allows ~400 in-flight requests — ample for judging, while capping how fast the
agents can be driven and therefore the rate of Vertex spend. All seeded data is
synthetic.

To revert public access after judging:

```bash
gcloud run services update anbu-care --project=anbu-care-hack \
  --region=asia-south1 --invoker-iam-check
```

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

## Cleanup — done

The two IAM grants added to diagnose the 404 have been removed and the removal
verified: the default compute service account carries no `tokenCreator` binding,
and the Cloud Run service lists only `user:heartlinmachado@blockintelai.com` as
invoker.

The four build roles on that service account (`storage.objectViewer`,
`logging.logWriter`, `artifactregistry.writer`, `cloudbuild.builds.builder`) are
**retained deliberately** — Cloud Build needs them for every future
`make deploy`.
