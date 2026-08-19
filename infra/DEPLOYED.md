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

## Open issue: the Cloud Run URL returns 404 — UNRESOLVED

The service deploys and the container starts cleanly, but **no request has ever
reached it**. Every call to either default URL returns a Google Frontend 404
(the generic `Error 404 (Not Found)!!1` page), not a response from the app.

What was ruled out:

| Hypothesis | Test | Result |
|---|---|---|
| App is broken | same image serves `/healthz` locally | app is fine |
| Container failed to start | Cloud Run logs | `Application startup complete`, `Ready=True` |
| Routing not provisioned | `status.conditions` | `RoutesReady=True` |
| DNS not propagated | `dig` on both hostnames | resolve to Google IPs |
| Propagation lag | polled ~20 min, forced a new revision | still 404 |
| This machine or its network | probed from a Cloud Build job inside GCP | still 404 |
| Region-specific | second service deployed to `us-central1` | still 404 (since deleted) |
| Ingress restriction | `run.googleapis.com/ingress=all`, `run.allowedIngress` = ALLOW | not it |
| VPC Service Controls | no perimeter found | not it |
| Org policy blocking Cloud Run URLs | listed every org policy on `227631295422` | none relevant |

Not yet ruled out: whether the 404 is Cloud Run's answer to an *unauthorized*
caller. Cloud Run can return 404 rather than 403 to avoid disclosing that a
service exists. Confirming it needs an identity token whose audience is the
service URL, which user credentials cannot mint — it requires impersonating a
service account, and that attempt stalled on IAM propagation.

**Next step:** mint a properly-audienced token and retry. A 200 means the 404
was purely an access-control artefact and the fix is the access decision below.
Still 404 means this is a Google-side problem for Cloud Support.

```bash
SA=473806191488-compute@developer.gserviceaccount.com
URL=https://anbu-care-37j4eofpwq-el.a.run.app
gcloud iam service-accounts add-iam-policy-binding $SA \
  --member="user:YOU@blockintelai.com" --role="roles/iam.serviceAccountTokenCreator"
gcloud run services add-iam-policy-binding anbu-care --region=asia-south1 \
  --member="serviceAccount:$SA" --role="roles/run.invoker"
# wait for IAM to propagate, then:
TOK=$(gcloud auth print-identity-token --impersonate-service-account=$SA \
      --audiences=$URL --include-email)
curl -H "Authorization: Bearer $TOK" $URL/healthz
```

None of this blocks the demo: `make demo` and `make chat` run the full system
locally, and `scripts/verify_stack.py` confirms every cloud dependency is live.

## Access

Public access (`allUsers`) is **refused** by the organization policy
`constraints/iam.allowedPolicyMemberDomains`, which restricts IAM members to the
`blockintelai.com` Workspace customer. The deploy reports this as a warning
rather than an error, so the service silently ends up domain-only.

Current invoker: `user:heartlinmachado@blockintelai.com`.

**Before submission**, judges will need access without a `blockintelai.com`
account. Two options:

1. Add a project-level exception to `iam.allowedPolicyMemberDomains` for
   `anbu-care-hack`, then re-run the `allUsers` invoker grant. That weakens a
   deliberate org-wide control on one project — an owner's call, not a default,
   so it is left undone here.
2. Submit a recorded demo plus the locally reproducible `make demo`, and leave
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
