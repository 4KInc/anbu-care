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

# --no-cpu-throttling is load-bearing, not a performance tweak. Reading a
# photographed bill takes about fifteen seconds and Twilio abandons a webhook at
# roughly the same mark, so the webhook acknowledges immediately and finishes in
# a background task. Cloud Run throttles CPU once the response returns, which
# would leave that task suspended and the family holding an acknowledgement that
# never became an answer.
#
# Do NOT put comments between the continued lines of the gcloud command below.
# A backslash-newline joins the lines, so a `#` there comments out every flag
# that follows it — silently, and `bash -n` will not tell you.

# NOTE: the separator is ^|^ and NOT the more usual ^@^. An email address
# contains @, so ANBU_DEMO_FAMILY_EMAIL split mid-value and gcloud rejected the
# whole flag with "Bad syntax for dict arg: [blockintelai.com]". Any separator
# here has to be a character that appears in NO value: not @ (emails), not + or
# / or = (base64 keys, phone numbers), not : or . or - (URLs). Pipe is safe.
#
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
  --no-cpu-throttling \
  --set-env-vars "^|^GOOGLE_GENAI_USE_VERTEXAI=TRUE|GOOGLE_CLOUD_PROJECT=${PROJECT_ID}|GOOGLE_CLOUD_LOCATION=global|ANBU_MODEL=${MODEL}|ANBU_STORE_BACKEND=firestore|ANBU_TPA_MODE=simulated|ANBU_WHATSAPP_MODE=${ANBU_WHATSAPP_MODE:-off}|ANBU_PUBSUB_ENABLED=${ANBU_PUBSUB_ENABLED:-false}|ANBU_DEMO_TOKEN=${ANBU_DEMO_TOKEN:-anbu-demo-family-token}|ANBU_SIGNING_KEY_B64=${ANBU_SIGNING_KEY_B64}|ANBU_ARTIFACT_BUCKET=${ANBU_ARTIFACT_BUCKET:-}|ANBU_DEMO_FAMILY_E164=${ANBU_DEMO_FAMILY_E164:-+14155550142}|ANBU_MAPS_API_KEY=${ANBU_MAPS_API_KEY:-}|ANBU_PUBLIC_BASE_URL=${ANBU_PUBLIC_BASE_URL:-}|ANBU_VOICE_MODE=${ANBU_VOICE_MODE:-off}|TWILIO_VOICE_FROM=${TWILIO_VOICE_FROM:-}|TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID:-}|TWILIO_API_KEY_SID=${TWILIO_API_KEY_SID:-}|TWILIO_WHATSAPP_FROM=${TWILIO_WHATSAPP_FROM:-}|ANBU_GOOGLE_CLIENT_ID=${ANBU_GOOGLE_CLIENT_ID:-}|ANBU_DEMO_FAMILY_EMAIL=${ANBU_DEMO_FAMILY_EMAIL:-}|ANBU_DEMO_FAMILY_NAME=${ANBU_DEMO_FAMILY_NAME:-}|ANBU_TRANSLATE_MODE=${ANBU_TRANSLATE_MODE:-gemini}|ANBU_RECOVERY_WINDOW_DAYS=${ANBU_RECOVERY_WINDOW_DAYS:-14}|ANBU_RECOVERY_HOUR=${ANBU_RECOVERY_HOUR:-9}|ANBU_DEMO_PARENT_E164=${ANBU_DEMO_PARENT_E164:-}|ANBU_DEMO_PARENT_LANGUAGE=${ANBU_DEMO_PARENT_LANGUAGE:-ta}|ANBU_DEMO_FAMILY_LANGUAGE=${ANBU_DEMO_FAMILY_LANGUAGE:-en}|ANBU_DEMO_FAMILY_TZ=${ANBU_DEMO_FAMILY_TZ:-America/Los_Angeles}|ANBU_PAYMENT_MODE=${ANBU_PAYMENT_MODE:-simulated}|RAZORPAY_KEY_ID=${RAZORPAY_KEY_ID:-}|RAZORPAY_KEY_SECRET=${RAZORPAY_KEY_SECRET:-}|RAZORPAY_WEBHOOK_SECRET=${RAZORPAY_WEBHOOK_SECRET:-}|RAZORPAYX_KEY_ID=${RAZORPAYX_KEY_ID:-}|RAZORPAYX_KEY_SECRET=${RAZORPAYX_KEY_SECRET:-}|RAZORPAYX_ACCOUNT_NUMBER=${RAZORPAYX_ACCOUNT_NUMBER:-}" \
  --set-secrets "TWILIO_API_KEY_SECRET=twilio-api-key-secret:latest,TWILIO_AUTH_TOKEN=twilio-auth-token:latest,ANBU_LINK_SECRET=anbu-link-secret:latest"

URL=$(gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')
echo
echo "Deployed: ${URL}"
echo "Health:   curl ${URL}/api/healthz"
