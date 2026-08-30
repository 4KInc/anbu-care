# Proposal — the recording-window plan

**Status:** proposal, no code written. Gated on approval.
**Written:** 2026-08-22, against a clean tree at `7bd1b4f`, 399 tests passing.
*Every figure below is as-of that date and is deliberately not updated: this is
a record of what was proposed, not a description of the repo today.*
**Deadline:** 31 Aug 2026, 5pm PDT. Nine days.
**Scope:** dashboard presentation, hospital-KB provenance strings, arrival-brief
empty-state rendering, and one new inbound field. The guarantee layer — triage,
comms policy, TPA, provenance chain — stays untouched. **Consent is the one
exception, and only in Phase 4b, and only with explicit sign-off.**

## Why this document exists

Four changes were requested off a set of screenshots. Checked against the code
first, two were already shipped, one is a measurement problem rather than a UI
problem, and one cannot be built without opening the consent layer. That gap
between what the screenshots showed and what the repo contains is the reason
nothing has been changed yet.

Two of the four requests were to remove honesty labels. Those are treated here
as the most dangerous items on the list, not the easiest.

---

## 0. Findings — the evidence this plan rests on

Nothing was modified while producing this section.

### 0.1 The maps reference is a simulator

`~/Projects/onetap-take-home-final` — the path originally given — contains zero
map, geo or location code. It is a Next.js scenario editor built on React Flow
and dagre. Its "live" preview is a `useMemo` over Zustand state.

The sibling `~/Projects/onetap-take-home` is the real reference:

| Concern | Implementation |
| --- | --- |
| Rendering | Mapbox GL JS, `components/map/MapTracker.tsx` (213 lines) |
| Transport | `pusher-js`, channel `job-{id}`, event `location.updated` |
| Ingestion | Go, `POST /api/location/update`, persisted via `LocationRepo` |
| Routing | self-hosted OSRM, Tennessee OSM extract, MLD pipeline, port 5001 |
| Smoothing | `hooks/useAnimatedMarker.ts`, RAF lerp over 4800 ms |
| Staleness | `hooks/useTechLocation.ts`, 30 s threshold |
| Reroute | >80 m deviation, 15 s cooldown, fresh OSRM route |
| Hex grid | `h3-js`, used by `DispatchH3Card.tsx` |

**The decisive finding: nothing in that repo produces a real position.** There
is no `geolocation`, no `watchPosition`, no `getCurrentPosition` anywhere in it.
The moving dot comes from `simulation/pusherSim.ts`, which replays a canned
`NASHVILLE_ROUTE` on a 5-second cadence with 1/2/5x speed controls, a `pause()`
whose own comment reads "simulates tunnel / GPS loss", and a road-closure mode
that offsets ~150 m to trigger a reroute on cue. `usePusher` falls back to that
simulator silently whenever `NEXT_PUBLIC_PUSHER_KEY` is unset.

It is a simulator wearing a real transport's shape. The Go endpoint accepts real
pushes; nothing in the repo ever sends one. Copying the dot into Anbu Care would
import fabricated sensing directly.

Reusable here, honestly:

- **Yes** — OSRM/Mapbox route rendering. Requires no position at all.
- **Yes** — the 30-second stale detector. It is an honesty mechanism, the same
  family as `comms.not_delivered`.
- **Yes** — the ingestion endpoint shape, for a genuinely consented push.
- **No** — `useAnimatedMarker`. Its lerp is well built and there is nothing here
  to interpolate between.
- **Never** — `pusherSim`.

### 0.2 Banner inventory

**SYNTHETIC — DEMO DATA.** One definition, one latch, seven call sites.

| Item | Location |
| --- | --- |
| Definition | `webui/index.html:234`, class `.strip demo` |
| Base style | `webui/index.html:57` |
| Demo style | `webui/index.html:59` — red bg/ink, `1px solid #f6d6d6` |
| Latch | `:235`–`:238`, `SYNTH_SHOWN` |
| Reset | `:772`, on every tab click |
| Call sites | `:321` Now, `:415` Arrival, `:442` and `:444` Routing, `:477` Record, `:526` and `:529` Claim, `:604` landing |
| Absent from | the Audit tab |
| Pinned by | `tests/test_auth_boundary.py:172`, and `:280`–`:287` |

Both tests assert **presence and mechanism**, never styling. `:172` checks the
string is in the HTML; `:280`–`:287` check `synthOnce` exists and that
`SYNTH_SHOWN=false` appears. Restyling is therefore free.

**SEEDED.** Four sites, no longer equally true.

| Site | Text | Still true |
| --- | --- | --- |
| `webui/index.html:449` | `EMPANELMENT AND CAPABILITY: SEEDED, NOT A LIVE FEED` | yes |
| `webui/index.html:330` | pill `empanelment: seeded` | yes |
| `kb/data/hospitals_thoothukudi.json:7` | `SEEDED SNAPSHOT — NOT A LIVE FEED` | **overbroad** |
| `agent.py:70`, `agents/triage.py:34` | prompt: "dated seeded snapshot" | **overbroad** |

The JSON `_meta.status` propagates to `tools/triage_tools.py:86` and `:125`,
`scripts/demo_spine.py:120`, and `server.py:218`.

Note the direction of the error. These labels are currently **too pessimistic** —
hospital locations really are Google-verified now. Correcting them makes the
system more accurate, which is the only defensible reason to touch an honesty
label at all.

### 0.3 Every unknown, classified and measured

Measured by building a throwaway harness, running one case at two stages, then
deleting it. Tree confirmed clean afterwards at 399 passing.

```
EARLY STAGE (triage only — the state in the screenshots)
  12 known / 7 unknown        unknown_count (facts only) = 5

FULLY RUN (document ingested + packet assembled + claim submitted)
  17 known / 2 unknown        unknown_count (facts only) = 1
```

The screenshotted case reported **4**, not 5, because it had check-ins and the
harness case did not. With the real demo case, which has eight check-ins, a
fully-run case reaches **zero unknown facts**.

| Field | Why unknown | Category | Resolves when |
| --- | --- | --- | --- |
| Admitted on | no claim packet — `composer.py:202` | no data yet | claim beat |
| Expected discharge | no packet — `composer.py:207` | no data yet | claim beat |
| Claim outcome so far | not adjudicated — `composer.py:243` | no data yet | claim beat |
| Likely out of pocket | no adjudication — `composer.py:244` | no data yet | claim beat † |
| Lab results / documents | nothing ingested | no data yet | document beat |
| Latest check-in | no check-in — `composer.py:128` | no data yet | any check-in |
| Open items | **knows** there are none — `composer.py:279` | affirmative negative | presentation fix |
| Documents to bring | **knows** there are none — `composer.py:283` | affirmative negative | presentation fix |

† Even after adjudication, a `QUERY` or `DENY` outcome deliberately keeps this
unknown rather than printing `INR 0` (`composer.py:225`–`:241`). A false zero in
the one artifact a frightened family reads is worse than an omission. Running
the claim beat will not zero this field, and must not be made to.

**Category (b) from the original brief — fields answerable from data already
held — is empty.** Commit `9ab22d6` swept it when it wired Insurance, Cover
available, Cashless, Distance, In her insurer's network and People who can be
notified to their real sources at `composer.py:75`–`:110`. There is nothing left
to reclaim there.

---

## 1. Banners — quieter, never absent

**Status:** ready to implement on approval. No guarantee-layer contact.

### 1.1 Synthetic banner: restyle, keep the words

The request was to remove it. The recommendation is to make it visually lighter
and keep it, for one reason: it is the label that stops a screenshot of
"Ashanthi Machado, 71, hypertension, high cholesterol, type 2 diabetes,
penicillin allergy" — on a **public** URL, from a **public** repo — from reading
as published patient data. The repo being public is what makes this load-bearing
rather than decorative.

Change `webui/index.html:59` only:

```
.strip.demo{background:var(--red-bg);color:var(--red-ink);border:1px solid #f6d6d6}
```

becomes a muted treatment: slate ink on a near-neutral ground, a single thin
left rule in the warning hue rather than a full pink fill, `font-weight` 700 to
600, `font-size` 12.5px to 11.5px, and the `.strip` padding at `:57` from
`9px 12px` to `6px 12px`.

Net effect: it reads as a footnote rather than an alarm, and still says exactly
what it said. `SYNTH_BANNER` at `:234`, the latch, and all seven call sites are
untouched.

**Tests:** both existing assertions continue to pass unchanged, because neither
inspects styling. No new test needed; the existing pair already prevents
deletion, which is the failure mode worth guarding.

### 1.2 Seeded labels: correct the three that overstate

Leave `:449` and `:330` exactly as they are. Both are still true.

Rewrite `kb/data/hospitals_thoothukudi.json` `_meta` so status is split rather
than blanket:

- `location_status` — verified against Google Places, carrying
  `location_verified_on` (already present per hospital as of `9ab22d6`)
- `capability_status` — seeded, with the existing warning text narrowed to name
  capability and empanelment specifically

Then update the two prompt strings so the model stops narrating the wider claim:

- `agent.py:70`
- `agents/triage.py:34`

And follow the propagation: `tools/triage_tools.py:86` and `:125`,
`scripts/demo_spine.py:120`, `server.py:218`.

**Tests:** add one asserting the KB meta distinguishes location provenance from
capability provenance, so a future re-seed cannot silently re-broaden the claim.

---

## 2. Maps — already real, one open risk

**Status:** shipped in `9ab22d6`, verified live. No further feature work.

All five hospitals carry a Google `place_id` and `location_verified_on:
2026-08-21`. The Routing tab renders a real map (`webui/index.html:665`–`:731`,
loaded at `:685`). `/api/map-config` (`server.py:133`) serves the key and an
accurate source note at `:135`.

That verification caught a real bug: seeded coordinates were out by 1.4 to
5.0 km, which changed which hospital was nearest and therefore what the routing
explanation asserted. The old line — "Sacred Heart, 2.2 km, 1.4 km farther than
Idhayalaya" — was wrong. Truth: 5.6 km, nearest is Government Medical College at
3.9 km. The narrative survives and improves: the extra 1.7 km now buys
capability 1.00 against 0.70 **and** empanelment.

### 2.1 API key handling — action required, and it is not mine

`/api/map-config` serves the Maps key to the browser. That is correct and
unavoidable for Maps JS — browser keys are public by design — **but only safe if
the key is referrer-restricted.** Unrestricted, anyone can lift it from the
deployed public app and spend against the billing account.

Required, in Cloud Console:

1. Application restriction: HTTP referrers, `anbu-care-37j4eofpwq-el.a.run.app/*`
2. API restriction: Maps JavaScript API and Places API only
3. Confirm no quota alarm is unset on the project

The key is delivered by env var (`ANBU_MAPS_API_KEY`, `infra/deploy_cloud_run.sh:49`)
and is not committed. Restriction is the remaining control.

### 2.2 What stays seeded, deliberately

Capability and insurer empanelment cannot come from Places. Google publishes
where a hospital is. It does not publish who bills which insurer, or which
centre can run a cath lab at 2am. A Places-derived proxy for either would be
fabrication with a citation attached, which is strictly worse than a seeded
value that says it is seeded.

**The deterministic score stays in code.** Maps supplies distance and location as
inputs to `triage/routing.py`; it does not replace the capability × distance ×
network calculation.

---

## 3. Unknowns — run the case, do not fill the fields

**Status:** one small code change plus a demo-seed change. No fabrication.

### 3.1 The bulk of it requires no code

Five of the eight unknowns resolve by running the case further, which the demo
script already does at `docs/DEMO_SCRIPT.md:319`
(`scripts/demo_support.py claim-flow $CASE $PARENT`). The screenshotted case had
simply stopped after triage with four receipts.

The action is to **record the demo against a fully-run case**, and to let the
claim beat resolve the unknowns on camera. Four unknowns turning into signed
receipts while a judge watches is a stronger beat than never having shown them,
and it is the honest version of "fewer unknowns".

### 3.2 The one honest code change: affirmative negatives

Three fields print `not yet known` when the system knows the answer and the
answer is "none":

- `composer.py:279` — Open items, "nothing on this case is recorded as pending"
- `composer.py:283` — Documents to bring, "no outstanding document request"
- Care circle — "not notified on this case"

Each reaches `_unknown_fact()` through a guard of the form `if not brief.pending:`
— the code examined the receipts, found zero, and then reported that as an
absence of knowledge. That is an honesty error pointing the other way: it
**understates** what the system knows and pads the unknown count.

Fix: introduce a third fact state alongside known and unknown — a definite
negative — rendering as `None recorded` and carrying the same source. This
fabricates nothing; it reports a zero the system actually computed.

**Blast radius:** small. No test asserts a specific `unknown_count`
(`schemas.py:471`); `tests/test_arrival_brief.py:181`–`:197` covers only the
populated case. The adversarial-omission run at `:55` must keep passing
unchanged — a holed state must still produce unknowns, not definite negatives,
and a new test should pin exactly that distinction.

### 3.3 Acceptance

- Fully-run case: **0 unknown facts**, all values traceable to a receipt
- Holed case: unchanged behaviour, every hole still `not yet known`
- No field anywhere derives a value it was not given

---

## 4. The live map

### 4a. Route rendering — recommended regardless

**Status:** ready on approval. No consent contact, no new data.

Draw the route from her home to the chosen hospital alongside the five existing
markers, using the same map already on the Routing tab. This makes the routing
decision visual — the 5.6 km against 3.9 km trade becomes something you can see
rather than read.

No position is involved. The existing caption stays accurate.

### 4b. Location she chooses to share — needs sign-off

**Status:** blocked on an explicit consent-layer exception.

Twilio's inbound WhatsApp webhook delivers `Latitude`, `Longitude`, `Label` and
`Address` when a user taps *share location*. That is a deliberate human act,
consented per message, requiring no GPS permission and no background tracking —
the same shape as the voice notes she already sends. It is a real source, not a
sensor this system invented.

The seam is open: `comms/inbound.py:109`–`:121` parses `NumMedia`,
`MediaUrl0` and `MediaContentType0`, and never reads the location fields.

Work required:

1. Read the four fields alongside `media_from()` in `comms/inbound.py`
2. **A new consent purpose.** Location is not covered by `inbound_wellbeing`.
   Reusing that purpose to carry location would repeat exactly the conflation
   that was already a shipped defect once, fixed, and regression-tested.
3. Persist on the check-in, receipted like every other consequential action
4. Render as: *"last reported location, 14:37 — she shared this. Not live
   tracking."*
5. If no location was ever shared, the map shows hospital and route only

**Guardrail conflict.** An existing test greps the dashboard for `geolocation`,
`watchPosition` and the string `"live location"`, and fails if any appears. A
last-reported pin is not live location, so the copy must stay clear of that
phrase. Write around the test; do not loosen it.

**No animation, explicitly.** One pin, one timestamp. Interpolating between two
shared points would draw a path nobody reported — fabricated sensing with extra
steps, and precisely what `pusherSim` does.

**The conflict to resolve first.** The scope line of this document says the
consent layer is untouched. 4b cannot be built under that constraint. It is
either an explicit, scoped exception with sign-off, or it does not ship. No
existing purpose gets quietly widened to avoid the question.

---

## 5. Order of work

Cheapest and least risky first, so the video stays reachable even if 4b slips.

| # | Work | Guarantee layer | Size |
| --- | --- | --- | --- |
| 1 | Phase 1 — banner restyle, three label corrections | no | small |
| 2 | Phase 3.2 — affirmative negatives | no | small |
| 3 | Restrict the Maps API key *(owner action)* | no | minutes |
| 4 | Phase 4a — route on the map | no | medium |
| 5 | Redeploy, live-verify, refresh screenshots | no | small |
| 6 | Phase 3.1 — run the full case for the recording | no | small |
| 7 | Phase 4b — WhatsApp location share | **yes, consent** | large |
| 8 | **Record the video** | — | the long pole |

Steps 1–6 leave a visibly better dashboard and a case that resolves its own
unknowns on camera. That is already a strong five minutes.

Step 7 is the one that can consume two days and return nothing recordable if the
consent work goes sideways. **Recommendation: timebox it, and record the video
before starting it.** A shot demo with four beats beats an unshot demo with five.

---

## 6. Test plan

Every phase ships green or does not ship.

| Phase | New tests |
| --- | --- |
| 1.1 | none — existing pair at `test_auth_boundary.py:172`, `:280` already prevents deletion |
| 1.2 | KB meta distinguishes location provenance from capability provenance |
| 3.2 | definite-negative renders as such; **holed state still yields unknowns** |
| 4a | route layer renders without a position; no new geolocation strings |
| 4b | consent purpose is separate and read live; a withheld location does not block the brief; dashboard still free of `geolocation` / `watchPosition` / `"live location"` |

Baseline is 399. The count must grow, never shrink.

## 7. What could go wrong

- **Restyling reads as removal.** Mitigated by keeping the words byte-identical
  and the tests that assert them.
- **The KB re-seed re-broadens the claim.** Mitigated by the 1.2 test.
- **The definite-negative change leaks into the holed case**, weakening the
  adversarial-omission guarantee. This is the highest-risk item in the plan and
  is why 3.2 ships with a test pinning both directions.
- **4b overruns.** Mitigated by recording first.
- **The Maps key is already being abused.** Unknown until the console is checked.
  Check before the repo draws attention.

## 8. Open decisions

1. Approve Phase 1 restyle-not-remove, or overrule and remove outright.
2. Approve or refuse the **consent-layer exception** for 4b.
3. Confirm the video is recorded before 4b, or accept the schedule risk.

Related: [`DEMO_SCRIPT.md`](../DEMO_SCRIPT.md), [`ARCHITECTURE.md`](../ARCHITECTURE.md),
[`CITATIONS.md`](../CITATIONS.md).
