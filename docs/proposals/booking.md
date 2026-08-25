# Proposal — the agent books the appointment

**Status:** proposal, no code written. Gated on approval.
**Scope:** a new `anbu_care/booking/` lane, a new consent purpose, one new
branch in the inbound webhook, and a container change. The referral, payment,
triage, comms and chain cores stay untouched.

---

## The wall this moves, and why it was there

`diagnostics/referral.py` currently ends on a sentence that is load-bearing:

> Nothing here is booked. Anbu Care is not connected to any of these centres
> and has not contacted them. These are places the test could be done, for
> somebody to ring.

That was the right call when it was written, and it is now the thing standing
between this project and its own pitch. **A present son books the appointment.**
He does not send his mother a list of eight labs at 4am and ask her to ring
round. Surfacing options is the part of the job that was easy to make safe, and
stopping there means the system does the easy half and hands her the hard half.

So the wall moves. It does not disappear, and the distinction matters more here
than anywhere else in the system, because this is the first lane where Anbu
Care **acts on her behalf in the physical world against a third party who never
agreed to any of this.** A wrong payment can be refunded. A wrong booking wastes
a real clinic's slot, sends a seventy-one year old across a city for a test she
did not need, and does it under her name.

The move is from *"we never act"* to *"we act inside an authority a human
granted, and every action is bounded, receipted, and reversible."*

That is not a new idea in this codebase. It is exactly what the payment lane
already does, and the payment lane is the proof the shape works: a human grants
a bounded authority ahead of time, deterministic code decides inside it, a
destination can never be set by the counterparty, every decision is receipted,
and one act revokes it. **Booking is the same shape pointed at a different verb.**
Reusing it is not a rhetorical trick — it is the reason this can be built
safely in the time available, because the hard thinking was already done.

---

## 1. The booking mandate

Parallel to `PaymentMandate`, and standing by default for the same reason: a
test gets ordered at 3am while the son is asleep, and an authority that needs
him awake is an authority that fails when it is needed.

```
BookingMandate
  mandate_id, parent_id, case_id ("" when standing), standing_id
  window_opens_at, window_closes_at
  max_distance_km          # never book farther than this
  home_collection_only     # honoured when the clinician said non-ambulatory
  prefer                   # "nearest" | "highest_score" | "soonest"
  may_disclose             # the whitelist, below. Not a free field.
  max_attempts             # how many centres to try before giving up
  requires_cancellable     # default TRUE, see guard 8
  granted_by, revoked_at
```

Granted at `POST /api/parents/{id}/booking-mandate`, adopted per case exactly as
the payment mandate now is, writing `booking.standing_applied` so a case whose
appointments were made under an authority its own record never mentions cannot
exist.

**It never carries money.** There is no cap field, because this lane may not
spend. See guard 9.

---

## 2. The guards, in order

Same discipline as `payments/enforcer.py`: named, ordered, each one a separate
refusal a family can read, and the refusal says which one stopped it.

| # | Guard | Refuses when |
|---|---|---|
| 1 | `order_live` | no clinician ordered this test, or the order was withdrawn |
| 2 | `mandate_live` | no booking authority, or revoked |
| 3 | `within_window` | outside the authorised window |
| 4 | `standing_live` | the standing grant behind an adopted copy is gone |
| 5 | `case_scope` | the order belongs to a different admission |
| 6 | `not_duplicate` | this order already has a live appointment |
| 7 | `centre_from_options` | the centre is not one the search surfaced |
| 8 | `cancellable` | no cancellation path was found before committing |
| 9 | `no_payment` | the flow reached a payment step |
| 10 | `disclosure_minimal` | the outbound payload carries a field not on the whitelist |
| 11 | `mobility_ok` | travel booking when the clinician recorded non-ambulatory |

Four of these deserve their reasoning written down, because they are the ones
that will be argued with.

**7 — `centre_from_options` is `payee_from_mandate` again.** The single most
important guard in the payment lane is that a bill can never set where money
goes. The identical failure here is a **web page** setting where she goes: an
interstitial offering "book at our partner centre instead", a redirect, a
model reading a sponsored result as the answer. The centre is chosen from the
ranked list *this system produced from its own search*, and the page can only
be used to fill in that choice. A centre that appears only on the page and not
in the options is refused, always, however plausible it looks.

**6 — `not_duplicate` is where the real third-party harm lives.** Double-booking
is this lane's version of paying the same bill twice, except the injured party
is a clinic that never agreed to any of this. Keyed on the order id, not on the
attempt, so a retry after a timeout cannot become a second slot.

**8 — do not book somewhere you cannot unbook.** Before committing, the driver
must have captured either a cancellation URL or a phone number. If it has
neither, it refuses. An agent that can create an obligation and cannot undo it
is worse than one that does nothing, and this is the cheapest possible
insurance against every other guard being wrong.

**9 — booking never becomes spending.** Prepaid slots and "pay to confirm" flows
stop the lane and escalate to the family. The payment lane exists, has its own
mandate, its own nine guards, and its own destination lock; a browser session
filling in a card field would route around all of it. These two authorities are
deliberately not fungible.

---

## 3. What may be disclosed, and nothing else

The centre needs enough to hold a slot and no more. `may_disclose` is a
whitelist, checked against the actual outbound payload rather than trusted:

**Permitted:** her name, her age, a contact number, the ordered test as the
clinician worded it, and home collection yes/no.

**Refused, structurally:** allergies, conditions, medications, the policy
number, the insurer, the case id, the son's number, the hospital she was
admitted to, anything from the clinician's note beyond the test label.

This mirrors the rule the referral receipt already holds — *"the test label is
NOT on the receipt, `/verify` is public"* — pointed outward instead of inward.
The test asserting it should be the same shape as
`test_nothing_in_a_referral_says_covered`: read the payload, assert the absent
fields are absent, in a test that fails loudly if someone widens the dict.

The contact number is a real disclosure to a third party, so it needs its own
DPDP purpose — `booking_disclosure` — joining the existing seven. Consent for
being *told* things is not consent to be *given out*.

---

## 4. Choosing, which is the part he actually asked for

Today `options_for` ranks and `group_by_mobility` presents. Nothing decides.

The decision is deliberately dull: filter the ranked options by the mandate
(distance, mobility, home collection), order by `prefer`, take the first, and
record **why that one** — the same explanation discipline hospital routing
already holds when it says what was traded for extra distance.

The agentic part is not the choosing. It is the **falling through**: attempt,
fail, record the failure with its reason, try the next, up to `max_attempts`,
then tell the family what was tried and what happened. A system that tries one
centre and gives up is a script. A system that tries three, tells you it tried
three, and says the fourth needs a phone call, is doing the job.

---

## 5. The three channels

### Tier A — web form, no identity check (`booking.requested`)

Many Indian diagnostic centres run a plain "request a callback" or "book a home
collection" form: name, phone, test, submit. No OTP, no account. This is
genuinely automatable end to end and is a **real booking request** — which is
what the receipt will say. Not "confirmed". The centre calls back and confirms,
and that confirmation arrives as a phone call to the care circle, not to us.

Driver: Playwright, with Gemini reading a screenshot plus the accessibility tree
to locate fields. The model proposes a field map; deterministic code fills and
submits. Same split as the dictation lane — **the model proposes, code acts** —
because a model that can both decide and click is a model that can do anything
the page suggests.

### Tier B — OTP flows (`booking.confirmed`)

Nearly every real slot-booking flow in India sends an OTP to the phone number.
That is an identity control and a bot control, and **defeating it is out of
scope, permanently.** It is also not the obstacle it first looks like.

The OTP goes to a phone a human is holding. So:

1. The driver reaches the OTP step and pauses, holding the browser session.
2. Anbu Care WhatsApps the care circle: *"Booking a blood test at X. A code has
   just been sent to your phone — send it here."*
3. She replies with six digits.
4. The inbound webhook recognises a pending OTP for that parent, feeds it in,
   and the driver completes.

This is a human providing **their own** one-time code, which is what the
control is for. Nothing is bypassed. And it is the care circle, not the son —
the neighbour is in the room, he is asleep, and this is precisely the human
bridge the whole design says to reach for first.

Mechanically it reuses the clinician-channel pattern: a short-lived pending
state keyed to the case, a narrow inbound branch, one shot, time-boxed to a few
minutes. The branch must be scoped tightly — six digits is a shape lots of
messages have, so it may only match while an OTP is genuinely outstanding.

**Session lifetime is the honest problem here.** Cloud Run will not hold a
browser open across an arbitrary human response time. Either the driver runs in
a Cloud Run **job** with a longer budget, or the flow is restarted with the OTP
in hand. This needs a spike before it is promised.

### Tier C — the call (stretch)

`comms/voice.py` already places calls and gates spoken content; it needs a
purchased Twilio number, which is configuration.

**The version worth building first is a bridge, not a robot.** Anbu Care dials
the centre, waits through the IVR and the hold music, and when a human answers,
connects the care circle. The agent does the tedious part; the person does the
twenty seconds of talking. That is exactly what a son does, it is achievable,
and it does not require an agent to survive an accented turn-taking phone
conversation about a lab test.

Fully autonomous voice booking — speak the request, parse the reply, confirm a
slot — is the far stretch. Worth naming as such rather than half-building.

---

## 6. Receipts

Public chain, so the same rule as `diagnostic.referral`: place ids and counts,
**never the test name**.

| Receipt | Written when |
|---|---|
| `booking.standing_applied` | a case adopts the standing authority |
| `booking.attempted` | a centre was tried, with which guard set passed |
| `booking.requested` | a request was submitted, awaiting the centre |
| `booking.confirmed` | the centre returned a slot or reference |
| `booking.refused` | a guard stopped it, naming the guard |
| `booking.escalated` | every attempt failed; a human is needed |
| `booking.cancelled` | the appointment was withdrawn |

`booking.attempted` before the attempt, not after. Same reasoning as writing the
photograph down before acknowledging it: an instance that dies mid-booking must
leave evidence that something was tried, or a retry double-books.

---

## 7. Infrastructure

- **Playwright will not fit the current container.** Cloud Run is at 1 CPU /
  1 GiB; Chromium wants ~2 GiB and adds ~400 MB to the image. Options: raise
  the service, or — better — run the driver as a separate **Cloud Run job**, so
  a browser cannot slow the webhook path that answers Twilio inside its
  timeout. The job is the right shape anyway for Tier B's longer session.
- One new consent purpose, `booking_disclosure`.
- One new inbound branch, tightly scoped.
- Twilio voice number for Tier C.

---

## 8. The demo honesty problem, which is not a small one

**These are real clinics.** Booking a slot at a real Thoothukudi lab for a
patient who does not exist creates a real no-show, wastes a real appointment,
and may breach that site's terms. Doing it on camera does not make it better.

Three options, in order of preference:

1. **A mock centre site**, obviously labelled, self-hosted, with the driver code
   byte-identical to the real path — only the URL differs. This is exactly the
   discipline the project already applies to the insurer/TPA, which is
   simulated and says so in the README's first section. It is honest and it is
   defensible.
2. **One real booking, genuinely cancelled**, on a centre with a free callback
   form, done off-camera as an end-to-end proof and written up. This is what
   makes the claim "real" in the README rather than "simulated".
3. Real bookings left standing — **no.**

Recommendation: 1 for the recording, 2 once, for the README line. That keeps
the "what is real and what is not" table truthful, which is the section this
project's credibility actually rests on.

---

## 9. Phasing

**Phase 0 — decide, without booking.** The mandate, the guards, the disclosure
whitelist, the choosing, the fall-through, and every receipt including
`booking.refused`. No browser. Fully testable with no network. This alone turns
"posts findings" into "decides and records what it decided", and it is the half
that carries all the safety.

**Phase 1 — Tier A booking** against a mock, then once against a real callback
form. `booking.requested` is real.

**Phase 2 — OTP relay through the care circle.** Unlocks `booking.confirmed`.
Spike the session-lifetime question first.

**Phase 3 — the call bridge.** Then, far later, autonomous voice.

Phase 0 is where the value and the risk both are. Phases 1 onward are
plumbing against a design that has already decided what it is allowed to do.

---

## 10. What I expect to go wrong

- **Session lifetime across the OTP wait.** The most likely thing to force a
  redesign. Spike it before promising Tier B.
- **A page that says "book at our partner instead".** Guard 7 exists for this
  and it will be load-bearing sooner than expected.
- **Cancellation paths that are a phone number only.** Guard 8 will refuse more
  centres than expected, which is correct and will feel wrong.
- **Bot detection.** Some sites will block automation outright. That is a
  refusal, not a puzzle to solve — `booking.escalated`, tell the family, move
  on.
- **The model clicking something that costs money.** Guard 9 is the backstop;
  the field map being proposed-but-not-executed is the actual defence.
