#!/usr/bin/env bash
# Deploy Anbu Care to Cloud Run.
#
#   ./infra/deploy_cloud_run.sh
#
# Requires: gcloud authenticated, ANBU_SIGNING_KEY_B64 set to a stable key
# (an ephemeral key means receipt chains stop verifying across restarts).
#
# Public access uses --no-invoker-iam-check rather than --allow-unauthenticated.
# The latter grants roles/run.invoker to allUsers, which this organization's
# domain-restricted-sharing policy (constraints/iam.allowedPolicyMemberDomains)
# refuses — and the deploy reports that refusal as a warning, not an error, so
# the service silently ends up unreachable. Disabling the invoker check is
# Google's documented alternative for projects under DRS, and it is scoped to
# this one service instead of weakening an org-wide control.
#
# To make the service private again after judging:
#   gcloud run services update anbu-care --region "$REGION" --invoker-iam-check
set -euo pipefail

# NOTE: --set-env-vars must appear ONCE. gcloud replaces rather than merges on
# a repeat, so a second flag silently discards everything the first one set.
# The "^@^" prefix changes the delimiter to @ so values may contain commas.

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-anbu-care-hack}"
REGION="${ANBU_REGION:-asia-south1}"
SERVICE="${ANBU_SERVICE:-anbu-care}"
MODEL="${ANBU_MODEL:-gemini-3.5-flash}"

if [[ -z "${ANBU_SIGNING_KEY_B64:-}" ]]; then
  echo "ANBU_SIGNING_KEY_B64 is not set." >&2
  echo "Mint one with: uv run python -m anbu_care.provenance.keygen" >&2
  echo "Deploying without it would give every revision a fresh signing key," >&2
  echo "so receipts written before a restart would stop verifying." >&2
  exit 1
fi

echo "Deploying ${SERVICE} to ${REGION} in ${PROJECT_ID} (model ${MODEL})"

gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --no-invoker-iam-check \
  --ingress all \
  --memory 1Gi \
  --cpu 1 \
  --timeout 600 \
  --set-env-vars "^@^GOOGLE_GENAI_USE_VERTEXAI=TRUE@GOOGLE_CLOUD_PROJECT=${PROJECT_ID}@GOOGLE_CLOUD_LOCATION=global@ANBU_MODEL=${MODEL}@ANBU_STORE_BACKEND=firestore@ANBU_TPA_MODE=simulated@ANBU_WHATSAPP_MODE=${ANBU_WHATSAPP_MODE:-off}@ANBU_PUBSUB_ENABLED=${ANBU_PUBSUB_ENABLED:-false}@ANBU_DEMO_TOKEN=${ANBU_DEMO_TOKEN:-anbu-demo-family-token}@ANBU_SIGNING_KEY_B64=${ANBU_SIGNING_KEY_B64}@ANBU_ARTIFACT_BUCKET=${ANBU_ARTIFACT_BUCKET:-}@TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID:-}@TWILIO_API_KEY_SID=${TWILIO_API_KEY_SID:-}@TWILIO_WHATSAPP_FROM=${TWILIO_WHATSAPP_FROM:-}" \
  --set-secrets "TWILIO_API_KEY_SECRET=twilio-api-key-secret:latest"

URL=$(gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')
echo
echo "Deployed: ${URL}"
echo "Health:   curl ${URL}/api/healthz"
