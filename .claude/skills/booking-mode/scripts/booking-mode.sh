#!/usr/bin/env bash
# Flip the Anbu Care booker between DRY and LIVE, and prove which it is.
#
# Live means a booking reaches a real clinic in Thoothukudi and somebody has to
# ring them to cancel it. That is not a state to be in by accident, and it is
# not a state to guess at either - so this always reads the answer back off the
# deployed service rather than assuming the write took.
set -euo pipefail

PROJECT="${PROJECT:-anbu-care-hack}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-anbu-care-booker}"
MODE="${1:-status}"

G="\033[32m"; R="\033[31m"; A="\033[33m"; D="\033[2m"; O="\033[0m"

# gcloud narrates onto stderr, so a quiet flag is not enough to keep this
# readable. Errors are still shown - they are just shown only when there are
# some, instead of buried under a progress bar.
quietly() {
  local log; log="$(mktemp)"
  if ! "$@" >/dev/null 2>"$log"; then
    printf "${R}failed${O}\n" >&2
    cat "$log" >&2
    rm -f "$log"
    return 1
  fi
  rm -f "$log"
}

reading() {
  gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
    --format='value(spec.template.spec.containers[0].env)' 2>/dev/null \
    | tr ';' '\n' | sed -n "s/.*'name': 'ANBU_BOOKING_DRYRUN', 'value': '\([01]\)'.*/\1/p"
}

report() {
  local v; v="$(reading)"
  # ABSENT MEANS DRY. The driver treats an unset variable as on, and every full
  # deploy of this service drops the variable - so "no line" is the commonest
  # reading here and it means safe, not unknown.
  if [ -z "$v" ] || [ "$v" = "1" ]; then
    printf "  ${G}DRY${O}   forms are filled and never submitted"
    [ -z "$v" ] && printf "  ${D}(unset, which the driver reads as dry)${O}"
    printf "\n"
  else
    printf "  ${R}LIVE${O}  a booking reaches a real clinic and needs cancelling\n"
  fi
}

case "$MODE" in
  status)
    echo; report; echo
    ;;
  dry|off|safe)
    quietly gcloud run services update "$SERVICE" --region "$REGION" \
      --project "$PROJECT" --update-env-vars ANBU_BOOKING_DRYRUN=1 --quiet
    echo; report; echo
    ;;
  live|real|on)
    quietly gcloud run services update "$SERVICE" --region "$REGION" \
      --project "$PROJECT" --update-env-vars ANBU_BOOKING_DRYRUN=0 --quiet
    echo; report
    printf "  ${A}every arrange from here books a real clinic. cancel what you book.${O}\n\n"
    ;;
  *)
    echo "usage: booking-mode.sh [status|dry|live]" >&2
    exit 2
    ;;
esac
