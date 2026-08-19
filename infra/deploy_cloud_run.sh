#!/usr/bin/env bash
# Deploy Anbu Care to Cloud Run.
#
#   ./infra/deploy_cloud_run.sh
#
# Requires: gcloud authenticated, ANBU_SIGNING_KEY_B64 set to a stable key
# (an ephemeral key means receipt chains stop verifying across restarts).
set -euo pipefail

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
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 600 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,ANBU_MODEL=${MODEL},ANBU_STORE_BACKEND=firestore,ANBU_TPA_MODE=simulated,ANBU_WHATSAPP_MODE=sandbox,ANBU_PUBSUB_ENABLED=${ANBU_PUBSUB_ENABLED:-false}" \
  --set-env-vars "ANBU_SIGNING_KEY_B64=${ANBU_SIGNING_KEY_B64}"

URL=$(gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')
echo
echo "Deployed: ${URL}"
echo "Health:   curl ${URL}/healthz"
