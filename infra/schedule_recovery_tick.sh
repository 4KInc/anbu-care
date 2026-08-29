#!/usr/bin/env bash
# The thing that calls the morning check-in, so that no person has to.
#
#   ./infra/schedule_recovery_tick.sh              # demo cadence, every minute
#   ./infra/schedule_recovery_tick.sh --daily      # 09:00 Asia/Kolkata, once
#   ./infra/schedule_recovery_tick.sh --delete     # stop calling it
#
# Cloud Run has no timer. There is no in-process scheduler in this service and
# no thread waiting for nine o'clock, which is why /api/recovery/tick exists at
# all: something outside has to call it, and this is that something.
#
# WHY EVERY MINUTE IS SAFE, and not a hack.
#
# The tick takes no instruction. It reads stored state and sends what is owed,
# and two guards inside it make the call idempotent:
#
#   the hour gate    nothing is owed before 09:00 in HER timezone
#   the day slot     one prompt per window per local day, forever
#
# So a hundred calls send one message, and the hundred-and-first sends none.
# Polling frequently does not mean messaging frequently; it means the message
# goes out promptly once it is owed, instead of up to a day late.
#
# That promptness is the whole point during a recording: the discharge summary
# opens the window, and the check-in arrives on its own while the presenter is
# still talking. A person typing `curl` on camera is a person doing the job the
# system claims to do.
#
# --daily is what a real deployment wants. Once at nine, in her timezone.
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-anbu-care-hack}"
LOCATION="${ANBU_SCHEDULER_LOCATION:-asia-south1}"
JOB="${ANBU_SCHEDULER_JOB:-anbu-recovery-tick}"
# The second thing outside the service that has to call in. The cashless clock
# breaches by the passage of time and nothing notices unless something asks, for
# exactly the same reason the check-in does: Cloud Run holds no timer.
CLAIMS_JOB="${ANBU_CLAIMS_SCHEDULER_JOB:-anbu-claims-sla-tick}"
BASE="${ANBU_PUBLIC_BASE_URL:-https://anbu-care-37j4eofpwq-el.a.run.app}"
TOKEN="${ANBU_DEMO_TOKEN:-}"
# Her timezone, not the presenter's. The gate inside the tick reads the
# window's own zone, and a job whose clock disagreed with it would be a second
# opinion about what time it is where she lives.
ZONE="${ANBU_RECOVERY_TIMEZONE:-Asia/Kolkata}"

SCHEDULE="* * * * *"
CADENCE="every minute (demo: the check-in lands within a minute of being owed)"

case "${1:-}" in
  --daily)
    SCHEDULE="0 9 * * *"
    CADENCE="once at 09:00 ${ZONE}"
    ;;
  --delete)
    gcloud scheduler jobs delete "$JOB" --project "$PROJECT_ID" \
      --location "$LOCATION" --quiet
    echo "deleted ${JOB}. Nothing calls the tick now, so no check-in will be sent."
    exit 0
    ;;
  "") ;;
  *)
    echo "usage: schedule_recovery_tick.sh [--daily|--delete]" >&2
    exit 2
    ;;
esac

if [[ -z "$TOKEN" ]]; then
  echo "ANBU_DEMO_TOKEN is not set." >&2
  echo "The tick is credentialed on purpose: it is the trigger for the only" >&2
  echo "outbound channel in this system that points at the parent herself." >&2
  echo "Run: set -a; . ./.env; set +a" >&2
  exit 1
fi

gcloud services enable cloudscheduler.googleapis.com --project "$PROJECT_ID" --quiet

# No parent_id: an empty one ticks every parent with an open window, which is
# exactly what the endpoint documents a scheduler should call.
ARGS=(
  --project "$PROJECT_ID"
  --location "$LOCATION"
  --schedule "$SCHEDULE"
  --time-zone "$ZONE"
  --uri "${BASE}/api/recovery/tick"
  --http-method POST
  --attempt-deadline 120s
  --description "Sends any recovery check-in that is due right now. Idempotent: the hour gate and the day slot inside the tick bound it to one message per window per day."
)

if gcloud scheduler jobs describe "$JOB" --project "$PROJECT_ID" \
     --location "$LOCATION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$JOB" "${ARGS[@]}" \
    --update-headers "Authorization=Bearer ${TOKEN}" --quiet
  echo "updated ${JOB}: ${CADENCE}"
else
  gcloud scheduler jobs create http "$JOB" "${ARGS[@]}" \
    --headers "Authorization=Bearer ${TOKEN}" --quiet
  echo "created ${JOB}: ${CADENCE}"
fi

# The claims clock, on the same credential and the same cadence. A breach is
# recorded once per pre-auth, so a frequent tick costs nothing and only means
# the lapse is noticed promptly rather than up to an interval late.
CLAIMS_ARGS=(
  --project "$PROJECT_ID"
  --location "$LOCATION"
  --schedule "$SCHEDULE"
  --time-zone "$ZONE"
  --uri "${BASE}/api/claims/sla-tick"
  --http-method POST
  --attempt-deadline 120s
  --description "Records cashless pre-auth clocks that have actually lapsed. Idempotent: one breach receipt per pre-auth, never re-emitted."
)

if gcloud scheduler jobs describe "$CLAIMS_JOB" --project "$PROJECT_ID" \
     --location "$LOCATION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$CLAIMS_JOB" "${CLAIMS_ARGS[@]}" \
    --update-headers "Authorization=Bearer ${TOKEN}" --quiet
  echo "updated ${CLAIMS_JOB}: ${CADENCE}"
else
  gcloud scheduler jobs create http "$CLAIMS_JOB" "${CLAIMS_ARGS[@]}" \
    --headers "Authorization=Bearer ${TOKEN}" --quiet
  echo "created ${CLAIMS_JOB}: ${CADENCE}"
fi

echo
echo "  target:   ${BASE}/api/recovery/tick"
echo "            ${BASE}/api/claims/sla-tick"
echo "  sends:    at most one check-in per open window per day, at 09:00 ${ZONE} or after"
echo "  proves:   nothing is typed on camera. Beat 7 opens the window; this sends it."
echo
echo "Run it now without waiting:"
echo "  gcloud scheduler jobs run ${JOB} --project ${PROJECT_ID} --location ${LOCATION}"
