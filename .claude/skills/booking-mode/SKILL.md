---
name: booking-mode
description: >-
  Flip the Anbu Care booker between DRY (fills forms, never submits) and LIVE
  (books real diagnostic centres), or report which it is. Use when the user asks
  to turn booking on or off, go live, set dry run, book for real, stop booking
  real clinics, or asks whether the booker is live before a recording.
---

# Booking mode — dry or live

The booker drives real diagnostic centres' own booking forms with a real
browser. **Live means a real clinic in Thoothukudi receives a real enquiry under
a real person's name, and somebody has to ring them to cancel it.**

That is why this exists as one command instead of a remembered flag.

## Commands

```bash
# which is it right now
bash .claude/skills/booking-mode/scripts/booking-mode.sh

# stop booking real clinics
bash .claude/skills/booking-mode/scripts/booking-mode.sh dry

# book for real - warn the user first
bash .claude/skills/booking-mode/scripts/booking-mode.sh live
```

## Three things that are easy to get wrong

**An absent variable means DRY.** The driver treats an unset
`ANBU_BOOKING_DRYRUN` as on. So "no line in the env" is the commonest reading
and it means *safe*, not *unknown* — do not report it as "unset, state unclear".

**A full deploy of the booker resets it to dry.** `infra/deploy_booker.sh` uses
`--set-env-vars`, which replaces every variable, so anything set out of band is
dropped. This is a good safety property and a reliable trap: **after any booker
deploy, set the mode again if you wanted live.** Set it after, never before.

**Read it back; do not assume the write took.** This script always re-reads the
deployed service. An earlier session reported "dry run is off" from an absent
grep line that actually meant the opposite, and cost a wasted run.

## Before switching to live

Tell the user plainly, and do not switch without them asking:

- every `arrange` from that moment books a real clinic
- of eight real Thoothukudi centres, **one** currently accepts a booking —
  DLABS Diagnostics, whose form answers `Submission Success`
- each successful booking is a phone call to cancel: **+91 88707 20883**

## Checking it stuck

`make preflight` reports the same thing among its other checks, asking the
booker itself over `/state` rather than reading a deploy flag — the only truth
is what that container is running. Run it after any deploy.

## What dry actually does

Not nothing. It navigates, follows the site to its booking page, opens a modal
if the form is behind one, has the model map the fields, validates every
selector, fills each value and reads it back, captures the cancellation path,
and photographs the filled form. It stops at the click and says so:

> `DRY RUN: the form was filled with name, phone, email and NOT submitted.`

So a rehearsal exercises everything except the one irreversible step.
