#!/usr/bin/env bash
# The browser service. Deployed separately from the API on purpose.
#
#   ISOLATED    Chromium wants several times the memory the API needs, and a
#               browser in that container competes with the lane answering a
#               voice note inside Twilio's fifteen-second webhook window.
#   PRIVATE     no public invoker. A browser that will type a stranger's name
#               into a form on request is not left open to the internet; the
#               API's service account is granted run.invoker and nothing else
#               can call it.
#   ONE INSTANCE  a browser session waiting for a one-time code lives in that
#               process's memory. A code delivered to a different instance is a
#               code delivered to nobody, so there is exactly one instance and
#               no session affinity to get wrong.
#   FEW AT ONCE  concurrency above one, because /otp has to be served WHILE
#               /commit is parked waiting for it. Kept small: a browser is
#               heavy and two real ones at a time is the honest ceiling here.
#   DRY BY DEFAULT  ANBU_BOOKING_DRYRUN is not set here, and the driver treats
#               absent as ON. Booking for real is a deliberate act.
set -euo pipefail

PROJECT="${PROJECT:-anbu-care-hack}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-anbu-care-booker}"
API_SA="${API_SA:-$(gcloud run services describe anbu-care --region "$REGION" \
  --project "$PROJECT" --format='value(spec.template.spec.serviceAccountName)')}"

cd "$(dirname "$0")/../booker"

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --project "$PROJECT" \
  --no-allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --concurrency 4 \
  --max-instances 1 \
  --timeout 600 \
  --set-env-vars "ANBU_ARTIFACT_BUCKET=${ANBU_ARTIFACT_BUCKET:-},GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_GENAI_USE_VERTEXAI=True,GOOGLE_CLOUD_LOCATION=${REGION}" \
  --quiet

BOOKER_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" \
  --project "$PROJECT" --format='value(status.url)')"

if [ -n "${API_SA:-}" ]; then
  gcloud run services add-iam-policy-binding "$SERVICE" \
    --region "$REGION" --project "$PROJECT" \
    --member "serviceAccount:${API_SA}" --role roles/run.invoker --quiet >/dev/null
  echo "granted run.invoker to ${API_SA}"
fi

echo
echo "booker: ${BOOKER_URL}"
echo
echo "Point the API at it, still dry:"
echo "  gcloud run services update anbu-care --region ${REGION} --project ${PROJECT} \\"
echo "    --update-env-vars ANBU_BOOKER_URL=${BOOKER_URL},ANBU_BOOKING_CHANNELS=web"
echo
echo "When you actually want it to book, on the BOOKER:"
echo "  gcloud run services update ${SERVICE} --region ${REGION} --project ${PROJECT} \\"
echo "    --update-env-vars ANBU_BOOKING_DRYRUN=0"
