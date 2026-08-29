#!/usr/bin/env bash
# One already-lapsed cashless clock, for the breach beat. Run right before a take.
#
#   make breach-seed
#
# WHY THIS EXISTS. The simulated adjudicator answers in about a second, every
# time, so no pre-authorisation is ever left waiting and the breach path cannot
# occur naturally on the demo family. It is reachable in production - a
# counterparty that does not answer is the ordinary case there, and an
# adjudicator failure reaches it here too - but it cannot be scheduled for a
# recording. This makes a real path observable.
#
# WHAT IT DOES NOT DO. It does not fake a lapse. It moves ONE thing: the start
# of the clock. The deadline is still that start plus one hour, the hour has to
# have genuinely passed against wall time, the scheduler still has to notice,
# and the receipt still keeps the three timestamps apart. The request and the
# breach both carry `requested_at_source: demonstration_seed`, so the chain
# says which clocks were started early. Say that out loud on camera.
#
# The case is opened through /api/intake-signal rather than by a voice note,
# because an escalation now files a pre-authorisation itself and gets it
# answered on the spot - and a decided request has no clock left to breach.
set -euo pipefail

URL="${ANBU_URL:-https://anbu-care-37j4eofpwq-el.a.run.app}"
TOKEN="${ANBU_DEMO_TOKEN:-}"
PARENT="${ANBU_BREACH_PARENT:-}"
MINUTES="${MINUTES:-70}"
# FORCE=1 says "I cleared the thread". The guard stops a second identical
# breach message landing beside the first, and only the person who deleted the
# first can know it is gone.
FORCE="${FORCE:-}"

G="\033[32m"; A="\033[33m"; D="\033[2m"; O="\033[0m"

if [[ -z "$TOKEN" ]]; then
  echo "ANBU_DEMO_TOKEN is not set. Run: set -a; . ./.env; set +a" >&2
  exit 1
fi
if [[ -z "$PARENT" ]]; then
  echo "ANBU_BREACH_PARENT is not set." >&2
  echo "Point it at a THROWAWAY parent, never the demo family: this opens a" >&2
  echo "case and puts a regulatory breach on it." >&2
  exit 1
fi

CASE=$(curl -s --max-time 60 -X POST -H "content-type: application/json" \
  -d "{\"parent_id\":\"${PARENT}\",\"channel\":\"er_desk_webhook\",\"raw_text\":\"admitted\",\"reported_by\":\"caregiver\",\"triage_now\":true,\"symptoms\":[\"chest pain\"],\"lat\":8.7642,\"lon\":78.1400}" \
  "${URL}/api/intake-signal" | python3 -c "import json,sys; print(json.load(sys.stdin).get('triage',{}).get('case_id',''))")

if [[ -z "$CASE" ]]; then
  echo "no case was opened; nothing seeded" >&2
  exit 1
fi

curl -s --max-time 60 -X POST -H "Authorization: Bearer ${TOKEN}" \
  "${URL}/api/cases/${CASE}/preauth/backdate?minutes=${MINUTES}${FORCE:+&force=true}" \
  | python3 -c "
import json, sys, textwrap
d = json.load(sys.stdin)
if d.get('status') == 'already_seeded':
    print('  ALREADY SEEDED')
    print(textwrap.fill(d['note'], 74, initial_indent='  ', subsequent_indent='  '))
    print()
    print('  If you cleared the thread, that message is gone and this guard is')
    print('  protecting nothing:  FORCE=1 make breach-seed')
    raise SystemExit(2)
if d.get('status') != 'ok':
    print('  seed refused:', d.get('error') or d.get('note')); raise SystemExit(1)
print('  case          ', d['preauth']['case_id'])
print('  requested at  ', d['requested_at'])
print('  deadline was  ', d['decision_due_at'])
print('  past it by    ', d['seconds_past_deadline'], 'seconds')
print('  provenance    ', d['requested_at_source'])
"

echo
printf "  ${G}seeded${O}  the scheduler ticks every minute, so the breach lands within one\n"
printf "  ${D}and the family message follows it. Nothing to type on camera.${O}\n\n"
printf "  ${A}say it out loud:${O} the clock was started early so the hour could be watched\n"
printf "  ${D}running out. The hour itself is real, and the chain records the seed.${O}\n"
