# Demo script — ~5m45s, unedited

One take. **Two phones** on camera (hers, and the nurse's), one terminal, one
browser tab.

**Live URL:** https://anbu-care-37j4eofpwq-el.a.run.app

The arc opens with **a seventy-one year old sending a voice note in Tamil at
2am**, because that is the actual product and the machinery underneath is more
convincing once you have seen why it matters.

**What is new since the last version of this script**, and what it cost:

- **Beat 6 is now a photograph, not a script.** The old claim beat ran
  `demo_support.py claim-flow` and narrated the output. It now starts with a
  hospital bill photographed on WhatsApp, and the money on screen is arithmetic
  run over what Gemini read off that paper. Same length, entirely different
  weight — a script producing a number proves nothing a slide could not.
- **The same beat then sends a discharge summary**, and the arrival brief's
  "not yet known" lines fill in. This is the clearest agentic moment in the
  demo: the system reads a document, works out what it changes, and changes it.
- **Sign-in is real** (Beat 4). A Google account, verified server-side, and
  then a second and separate check that the account is on this family's
  contacts. The 403 is the beat, not the sign-in.
- **The payment beat runs on a real provider** (Beat 7). On
  `ANBU_PAYMENT_MODE=razorpay` the link on screen is a real Razorpay payment
  link in test mode, with a real webhook flipping it to settled. The closing
  narration changed to match; the old "settlement is simulated" line now
  understates what is running, and there is a separate line for the payout
  rail. Read pre-flight §6 before recording — it says which to say.
- **Bills print a UPI ID, and the payee guard checks it.** Which means the
  mandate in pre-flight §6 must name the same address the bill prints, exactly.
  A near-miss escalates the bill you meant to clear, on camera.

Rehearse Beat 6 hardest. It is the newest, it is the most visual, and it is the
one with a state trap in it — see pre-flight §5. **Read §6 as carefully**: the
payment beat has a trap of its own now, and it fails on the good half.

---

## The one thing to say early, or a judge will spot it

The demo handset is registered as **you, the son** — set by
`ANBU_DEMO_FAMILY_NAME`, so the record says your name rather than a placeholder
you would have to explain. When you send a message you are playing the mother,
and the alerts come back to the same thread. Say it out loud once:

> "One phone is doing two jobs here — she sends, and the family contact
> receives. In production those are different handsets."

Trying to hide it looks worse than naming it, and the single thread actually
lets a viewer see the whole loop without cutting away.

---

## Pre-flight — on the actual recording machine

Every bug that bit this project late was found by someone clicking, not by a
test. Do this as a dry run, not a skim.

### 1. Credentials (this org expires them fast)

```bash
gcloud auth login
gcloud auth application-default login
make verify-stack
```

- [ ] **Signing key reports stable, not ephemeral.** An ephemeral key makes the
      whole verify beat meaningless.
- [ ] **Re-run `gcloud auth application-default login` immediately before you
      record.** Beat 3 reads Firestore live, mid-take. This org expires ADC
      aggressively and it has died between one command and the next during this
      build — a stack trace on camera is the worst possible way to find out.

### 2. Service up, both auth proofs

```bash
URL=https://anbu-care-37j4eofpwq-el.a.run.app
curl -s $URL/api/healthz
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/parents/whatever          # 401
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/cases/whatever/verify     # 200
```

- [ ] Liveness is **`/api/healthz`**. Bare `/healthz` is reserved by Google
      Front End and 404s — **do not put it on camera.**
- [ ] 401 and 200. If either is wrong, **stop.**

### 3. WhatsApp is actually live

This is the beat that cannot be faked, so prove it before you roll.

**The sandbox is gone.** Anbu Care has its own WhatsApp sender now —
`+1 239 453 5380`, display name **Anbu Care**, which is what the chat header
reads. No `join school-rate`, no shared Twilio number, no Twilio logo.

```bash
uv run python scripts/verify_whatsapp_sender.py
```

- [ ] That script reports online, named, and pointed at this deployment. It
      checks the three things that fail silently: a sender that is ONLINE with
      no webhook (outbound works, every inbound photograph vanishes), a display
      name that never got set, and a deployment still pointed at a different
      sender.
- [ ] **Message the sender once from the handset before recording.** WhatsApp's
      24-hour window is per business-number-and-handset pair, and this is a new
      number — no window exists until she writes first. Without it the first
      outbound send fails at Twilio rather than in our code, which looks exactly
      like a bug in the demo.
- [ ] Send one throwaway `slept well` and confirm you get
      **"Thanks, that's noted."** back — and that the chat header says
      **Anbu Care**, not Twilio. That single reply proves the window is open,
      the webhook is wired and the sender is branded, in one action.
- [ ] Send one throwaway **voice note** and confirm it comes back transcribed,
      not as "could not make out what she said". Voice takes about ten seconds
      against a Twilio ceiling of roughly fifteen, so a cold start is the thing
      most likely to bite. **Send a warm-up voice note a minute before you
      roll** so the instance is already up.

### 4. Fresh state

```bash
curl -sX POST $URL/api/demo/seed        # note the parent_id it returns
```

- [ ] The canonical judge-facing pair is **case-da1c2cb6db** (valid, 8 receipts)
      and **case-a7cf9fa613** (tampered, `broken_at_seq: 1`). Both stay live
      after the recording so anyone can re-verify from the video. Do not delete
      them — and **check them, do not assume them**:

      ```bash
      curl -s $URL/api/cases/case-da1c2cb6db/verify | jq '{verified, receipt_count}'
      curl -s $URL/api/cases/case-a7cf9fa613/verify | jq '{verified, broken_at_seq}'
      ```

      A deleted case still answers **200 with `verified: true` and
      `receipt_count: 0`** — an empty chain is a valid chain. So the tampered
      one silently stops being tampered, and the beat that proves the system
      detects alteration proves nothing. This has happened once, to a bulk
      cleanup that was careful about the case it was keeping and never looked
      at these two. `receipt_count` is the field that catches it.
      `parent-4787c08b00` owns them both and must survive with them.
- [ ] **Old cases carry old distances, and that is correct.** Hospital
      coordinates were re-verified against Google Places on 21 Aug, which moved
      them by up to 5 km. Receipts are immutable, so a case triaged before that
      keeps the numbers that were true when the decision was made. If anyone
      asks why an old receipt says 2.2 km and a fresh run says 5.6 km, that is
      the answer, and it is the chain working rather than failing. The case you
      create live during the demo will use the corrected coordinates.

### 4b. The handset starts as the son's, not the doctor's

**Read this before every take.** A phone that is still bound as the treating
team from a rehearsal will file your mother's opening voice note as a clinical
note. No alert, no care-circle message, no QR — Beat 2 produces nothing and
gives you no error to notice. It is the quietest way to lose a take, and it has
happened.

```bash
./.venv/bin/python - <<'EOF'
from anbu_care import service
from anbu_care.handoff import channel
key = service.number_key("+16692167706")     # the recording handset
while (b := channel.for_number(key)):
    print("unbinding from", b.case_id); channel.unbind(b)
print("bound as the treating team:", bool(channel.for_number(key)), "<- must be False")
EOF
```

- [ ] **False.** If it prints True, the take will fail silently.
- [ ] Binding lasts **12 hours**, so yesterday's rehearsal is still live today.
- [ ] Sending `STOP` from the handset does the same thing, and is what you do
      on camera in Beat 5e. This is the off-camera version, for rehearsals.

### 5. The paperwork beat — the state trap

**Read this one twice.** The system refuses a photograph it has already seen,
which is correct behaviour and will ruin a take if you meet it live.

- [ ] **Regenerate the images.** This is the reliable reset, and since seeding
      changed it is close to the only one. Documents dedupe per parent by image
      hash; bills dedupe per case by image hash **and by the bill number
      printed on them**, so a re-shot copy of a bill already sent is refused
      too. New files clear both.

      ```bash
      uv run python scripts/make_bill_images.py --out /tmp/demo
      uv run python scripts/make_documents.py  --out /tmp/demo
      ```

- [ ] **Seeding no longer mints a fresh parent.** It used to, and every re-seed
      orphaned the last one — eighty-three profiles accumulated that way, and a
      demo read its settings off whichever was seeded last. `POST
      /api/demo/seed` now reuses the family the handset already belongs to, so
      it is safe to call repeatedly and it will **not** give you an empty
      record. For bills, open a **new case** instead — bill dedupe is scoped to
      the case, so a fresh case is a clean slate even with the same files.

- [ ] **A seed binds your handset and your Google account** — but only if
      `ANBU_DEMO_FAMILY_E164` and `ANBU_DEMO_FAMILY_EMAIL` were set at deploy
      time. Unset, seeding binds a Twilio test number and your photographs land
      somewhere you are not looking. Check before you roll:

      ```bash
      gcloud run services describe anbu-care --project anbu-care-hack \
        --region asia-south1 --format=json \
        | jq -r '.spec.template.spec.containers[0].env[]
                 | select(.name|test("DEMO_FAMILY")) | "\(.name)=\(.value)"'
      ```

- [ ] **Send one bill and one document as a rehearsal, then re-seed.** Do not
      rehearse and record against the same parent.
- [ ] **The bill read takes ~15s.** Know that, and have the line about the
      fifteen-second webhook budget ready to fill it. Rehearse talking over it.
- [ ] Have the four images **already in the WhatsApp thread's gallery** on the
      handset. Hunting through Files on camera is thirty dead seconds.

### 6. The payment beat — two cases, not one

The refusal and the auto-clear cannot share a case. Two bills on one stay put
both outcomes in one money view and the beat stops being legible — and there is
now a mechanical reason too: an approved 2,70,720 plus an automatic 38,450 is
77% of a 4,00,000 authority inside six hours, and the burst signal stops
anything past half. Correct behaviour, wrong moment.

**The payee on the mandate must be the string the bill prints, character for
character.** Bills carry a UPI ID now, and the payee guard compares them. A
mandate for `sacredheart@hdfcbank` against a bill printing
`sacredheart@okhdfcbank` escalates on `payee_mismatch` — a correct refusal, on
the bill you meant to clear, on camera. This is the single most likely way
this beat fails.

```bash
# the destination, exactly as make_bill_images.py prints it on the paper
VPA=sacredheart@okhdfcbank

# one case per outcome, each with the same mandate
for i in 1 2; do
  P=$(curl -sX POST $URL/api/demo/seed | jq -r .parent_id)
  C=$(curl -sX POST $URL/api/intake -H 'content-type: application/json' \
      -d "{\"parent_id\":\"$P\",\"symptoms\":[\"chest pain\"],\"reported_by\":\"caregiver\"}" \
      | jq -r .case_id)
  curl -sX POST $URL/api/cases/$C/payment-mandate -H "Authorization: Bearer $TOKEN" \
    -H 'content-type: application/json' -d "{\"payee_vpa\":\"$VPA\",
      \"payee_label\":\"Sacred Heart Hospital\",\"per_bill_cap_inr\":50000,
      \"total_cap_inr\":400000,\"hours\":48}" >/dev/null
  echo "case $i: $C"
done
```

- [ ] **Confirm the rail before you record.** `ANBU_PAYMENT_MODE=razorpay` gives
      a real Razorpay link in test mode, which is what the narration below
      assumes. `payout` settles with nobody clicking and labels itself
      simulated. They need different closing lines — say the one that matches.
- [ ] **The handset is bound to the LAST parent seeded.** Only one case receives
      photographs. Do the refusal on that one, then re-seed and do the
      auto-clear, or the second bill lands on the first case.
- [ ] **`bill_cardiac_icu.png`** → 2,70,720 due, over the 50,000 cap → refuses.
- [ ] **`bill_interim_day_two.png`** → 38,450 due, Sacred Heart, inside every
      limit → clears with no tap. Check it is under **45,000** as well as under
      the cap: 90% of the cap trips near-cap, which would escalate a bill you
      meant to clear. `day_three` (31,650) and `day_four` (27,300) are
      interchangeable here and leave more headroom.
- [ ] **Do not send `bill_general_ward.png` in this beat.** It is from Sundaram
      Arulrhaj and escalates on vendor mismatch, which is a correct refusal for
      a reason the narration is not set up for.
- [ ] **Every bill is single-use.** Dedupe is on the bill number now, not only
      the image, so a re-take of one already sent will not clear either. Cut a
      fresh file with `make_bill_images.py` if you burn one on a bad take.
- [ ] Approving needs a signed-in session, so **be ready to sign in on camera**.
      That is the beat, not an interruption.

### 7. Sign-in, and a second parent to be refused by

The 403 needs a parent your account is *not* a contact on.

**Seeding will not give you one.** It is idempotent now and returns the family
your handset is already bound to — which is precisely the parent your account
*is* on, so the refusal would not fire.

Use **`parent-4787c08b00`**. It is the parent the two canonical verify cases
belong to, so it stays live permanently, and its only contact carries no email
address at all. Confirmed 403 for this account.

```bash
OTHER_PARENT=parent-4787c08b00
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $GOOGLE_ID_TOKEN" $URL/api/parents/$OTHER_PARENT   # 403
```

- [ ] `curl -s $URL/api/auth-config | jq .google_client_id` is **not null**.
- [ ] Sign in on the deployed dashboard once, before recording. The first
      sign-in on a new client can be slow.
- [ ] Confirm the 403 fires for `$OTHER_PARENT` while signed in.
- [ ] **Audience is Internal**, so only blockintelai.com accounts can sign in
      at all. If a judge asks to try it themselves, hand them the demo
      credential — do not promise them a Google sign-in they cannot have.

### 8. The handoff and the trace (new beats, new ways to fail)

Both of these were added after the last recording, so neither has muscle memory
behind it. Dry-run them fully.

```bash
TOKEN=anbu-demo-family-token
CASE=<a case you just created>
curl -sX POST -H "Authorization: Bearer $TOKEN" $URL/api/cases/$CASE/handoff-link | jq -r .url
curl -s  -H "Authorization: Bearer $TOKEN" $URL/api/cases/$CASE/trace | jq '.query_fork'
```

- [ ] **`ANBU_PUBLIC_BASE_URL` was set on the deployed revision.** If it was
      not, the QR encodes a relative path and scans to nothing. This is silent —
      the dashboard looks perfect and the phone just never resolves it.
- [ ] **Actually scan the QR with the second phone**, from the laptop screen, at
      the angle and brightness you will record at. Glare is the failure mode.
- [ ] The summary shows **Penicillin** at the top. If allergies read "not on
      file", the seed did not run.
- [ ] `query_fork.gathered_at_seqs` is **not empty**. An empty list means the
      case was driven a way that writes no gather receipt — use `claim-flow`.
- [ ] `synthesized` is **0** (`steps` minus `receipt_count`). Say this number
      out loud on camera; it is the point of the beat.
- [ ] Export `$TOKEN` in the shell you will record in. The trace is
      credentialed, and a 401 there reads as broken rather than as a boundary.
- [ ] If you are doing the optional voice-note-on-the-nurse's-phone moment, warm
      the transcriber first — same cold-start risk as Beat 2.

### 9. Walk the dashboard by hand

Open `$URL/app` at the window size you will record.

- [ ] Before signing in, a content tab shows **401 THE CASE TRAIL IS CREDENTIALED**.
- [ ] Sign in with `anbu-demo-family-token`; every tab renders.
- [ ] **Scroll the Record tab and confirm the nav is still reachable** — a
      sticky-positioning bug lived exactly there.
- [ ] Check-ins panel shows your throwaway message, labelled
      **SELF-REPORTED — NOT A MEASURED VITAL**.
- [ ] `SYNTHETIC — DEMO DATA` visible on every clinical view.

### 10. Recording hygiene

- [ ] Say **"synthetic"**, **"simulated"** and **"seeded snapshot"** at least
      once each. The honesty framing is the architecture case, not a disclaimer.
- [ ] Say **"received"**, never "detected".
- [ ] Say **"recognised"** or **"matched"**, never "diagnosed".
- [ ] Do not claim an ambulance is called, or could be.
- [ ] Voice **calls** are built but switched off (`ANBU_VOICE_MODE=off`). The
      number is now purchased, so the reason is a deliberate default rather
      than a missing prerequisite — if you mention the ladder, say it is off.
      Do not confuse this with voice **notes**, which are live and are Beat 2.
- [ ] Say **"the table decides, the model can add"**. Never "the AI decides".
- [ ] Say **"read-only"** and **"not connected to any hospital system"** during
      the handoff beat. Outbound presentation is not integration, and a judge
      who thinks you claimed an EHR connection will discount everything else.
- [ ] Never say the trace shows "what the agent was thinking". It shows what the
      agent **recorded**. That distinction is the beat.
- [ ] A voice note takes ~10s. Do not fill the silence with hedging; say what
      is happening ("it is transcribing, then matching") and let it land.

### 11. Backup take

Record a second pass immediately, before changing anything. Everything except
the Gemini transcription and extraction is deterministic;
`docs/takes/backup-take-spine.txt` is the fallback if recording fails outright.

---

## Beat sheet

| Time | Beat | Point at |
|---|---|---|
| 0:00–0:20 | **The question** | "If something happens to my mother tonight, who makes sure the right decisions get made?" Every incumbent answers: a human coordinator. |
| 0:20–1:10 | **She sends a voice note in Tamil** ⭐⭐ | The hook. Phone on camera. Ends with a QR going out to the care circle unasked. |
| 1:10–1:45 | **What decided that** ⭐ | Gemini translated. A table in code decided. Both are recorded. |
| 1:45–2:20 | **The boundary, and who is allowed to look** ⭐ | Clinical detail refused, family still told. Then a real sign-in, and a 403. |
| 2:20–3:20 | **The treating team, and you never touch it** ⭐⭐⭐ | Scan, connect, a Tamil update, a test ordered, labs found. The son does nothing. |
| 3:20–4:00 | **Photograph the paperwork** ⭐⭐ | A bill becomes an itemised claim. A discharge summary fills in the unknowns. |
| 4:00–4:45 | **It pays one, and refuses another** ⭐⭐ | The refusal first. Then it clears a bill alone, and nobody is woken. Optional +20s: the same bill photographed twice. |
| 4:45–5:25 | **Watch it decide, then check that it did** ⭐⭐ | The trace, then `/verify`. Autonomy and audit on one screen. |
| 5:25–6:00 | **The honest wall** | What it does not do, said out loud. |

Runs ~6:00. The treating-team beat took the extra twenty seconds and is worth
every one of them: it is the only stretch of the demo where a system does the
whole job a son does — get the credential to the right hands, hear the doctor,
record what he said in his words, and find the lab — with the son asleep the
entire time. **The order inside it is load-bearing.** The link goes out before
the doctor speaks, and the doctor speaks before the bill arrives, because each
step is what makes the next possible. On one handset it is also a hard
constraint: the phone must be handed back with `STOP` before it can photograph
a bill.

The paperwork beat replaced the old scripted claim beat at roughly the same
length, and the sign-in moment cost about fifteen seconds inside an existing
beat rather than becoming one of its own — signing in is not interesting, being
refused is.

The payment beat cost forty-five seconds and the tamper beat paid for it: tamper is gone from the running order, deliberately. It was a lovely
thirty seconds, but the trace beat already ends on `/verify`, so verifiability
survives without it — and an agent that declines to spend money is a stronger
thirty seconds than a hash that does not match.

**To hold 5:45:** cut the second half of the payment beat, the auto-clear. Keep
the refusal. A system that pays is ordinary; a system that
refuses to, and says exactly which limit stopped it, is the one nobody else
will have. **Do not cut** the paperwork beat, the handoff, or the trace.

**If you have time to spare**, in order of what buys the most:

1. **The retake**, twenty seconds at the end of the payment beat. The same bill
   photographed twice is the failure nobody watching will have anticipated, and
   the money view staying still is the proof.
2. **The 403 in Beat 4**, ten seconds. The only moment in the demo where the
   system tells *you* no.
3. **The UPI QR** on the payment card, ten seconds, if payments are landing
   well and you want one more concrete Indian detail.

---

## Beat 2 (0:20–1:10) — she sends a voice note ⭐⭐

**On the handset**, record a WhatsApp voice note to the sandbox number saying,
in Tamil:

> மார்பு வலிக்கிறது, மூச்சு விட முடியவில்லை
> *(maarbu vali, moochu vaanga mudiyala — "my chest hurts, I can't catch my breath")*

Hold the phone up. Within seconds, three things come back.

**Say, while they arrive:**

> "She is seventy-one, she is in Thoothukudi, and she cannot breathe. She is not
> going to type. She holds the button and speaks, in Tamil, because that is what
> comes first at two in the morning."

**What lands, and what to point at:**

Three messages arrive in about ten seconds. Do not narrate over the wait — let
the phone buzz.


1. **The alert to the family** — read the first four lines aloud:
   - her voice note, transcribed, *in Tamil script*
   - **"That is what Anbu Care heard in her voice note. It may be imperfect, so
     listen to the recording in the dashboard."**
   - **"Understood as: chest pain, difficulty breathing."**
   - the timestamp in **both clocks**: "11:58 AM your time, 12:28 AM in Thoothukudi"

2. **The routing** — Sacred Heart at **5.6 km**, *1.7 km further than the
   nearest hospital* (Government Medical College, 3.9 km). It went further for
   two reasons, and the explanation names both: cardiac capability scored 1.00
   there against 0.70, **and** it is empanelled with Star Health so the
   admission stays cashless.

   Worth saying out loud: *"the nearest hospital was not the right hospital,
   and the system says why in the same sentence."*

   > **These distances are real.** Hospital identity and coordinates are
   > verified against Google Places, with the place id and verification date on
   > each record. Only empanelment and capability are still seeded — Google can
   > confirm a hospital exists and where; it cannot say who it bills.

3. **The care-circle notice** — the neighbour is asked to call. **No symptoms.
   No link into her record.**

   Then open the **Routing** tab and show the map: her location, all five
   hospitals, the chosen one marked. Say *"this shows where she is being
   directed, not where she is — no location is ever collected from her."*

4. **Tap the link.** It opens straight into the case — no sign-in, no pasting a
   token. The alert carries a signed link scoped to this case and this parent,
   valid for a day. Say: *"that link reads her record and nothing else, and it
   cannot make the system message anyone."*

**The line to land:**

> "Nobody diagnosed anything. It recognised words, applied a rule, and told two
> people who can act — and it told them different things, on purpose."

**If the transcript is imperfect, point at it rather than past it.** On one real
run it heard *மார்பகம்* (breast) for *மார்பு* (chest) and still escalated
correctly, because the terms it extracted were right. That is exactly why the
message says "it may be imperfect, so listen to the recording" — the design
admitted the weakness before the weakness showed up.

**Do not say:** "it detected", "it diagnosed", "it knew she was having a heart
attack".

---

## Beat 3 (1:10–1:45) — what actually decided that ⭐

Terminal. This is where the architecture argument lands, and it now has two
halves: what the model did, and what it was not allowed to do.

```bash
uv run python - <<'PY'
from anbu_care import service
from anbu_care.comms import inbound
from anbu_care.provenance.store import PARENT_SUBJECT
s = inbound.resolve_sender("+16692167706")
chain = service.get_chain(s.parent_id, subject=PARENT_SUBJECT)
r = [x for x in chain.receipts if x.kind == "wellbeing.escalated"][-1]
for k in ("decided_by", "model_terms", "matched_rules", "severity"):
    print(k, "=", r.payload[k])
PY
```

**Say, pointing at `decided_by`:**

> "Gemini did the translation. It turned Tamil into English symptom terms. The
> decision came from a table of seventy-six phrases in the source tree, sourced
> from NHS and ambulance-service guidance, that a clinician could review line by
> line.
>
> And the receipt says which of them decided. `rule` means a named phrase
> matched. `model` means nothing matched and the model alone judged it urgent.
> `both` means they agreed independently."

### The two messages to show side by side

This is the beat. Send these one after the other, from the handset, and read the
receipts:

| she says | `decided_by` | why |
|---|---|---|
| "தலை சுத்துற மாதிரி இருக்குது… ஹாஸ்பிடல் போனும்" *(dizzy, need hospital)* | **both** | table: dizziness is MEDIUM, but her hypertension makes it HIGH for cardiology. model: she asked to go to hospital. |
| "முட்டி ரொம்ப வலிக்குது… ஹாஸ்பிடல் போகணும்" *(knee pain, need hospital)* | **model** | no rule covers knee pain. The model escalated it anyway. |

> "Same speaker, same request, different symptom. One was caught by a rule and
> a model independently. The other by no rule at all — and the receipt says
> **NO RULE MATCHED** in capitals, because a clinician reading this later needs
> to know which presentations the table is missing. Those are the rules to add
> next."

### Then show what the model cannot do

```bash
uv run pytest tests/test_escalation.py -k "junk or quieten or silent" -q
```

> "The model can raise an alarm the table missed. It can never lower one. These
> tests feed it 'patient is fine', 'ignore previous instructions', and an
> explicit 'not urgent' next to a message saying crushing chest pain — every
> one still escalates, because the raw words always reach the table regardless.
>
> So the honest sentence is this: no model can stop someone being woken, and
> when a model alone decides to wake them, the record says so."

**Do not say** "the AI decides" or "the AI knows". Say **the table decides, and
the model can add**.

## Beat 4 (1:45–2:20) — the boundary, and who is allowed to look ⭐

Two halves. The refusal, then the thing most people forget.

**Half one — clinical detail does not travel over WhatsApp.**

From the handset, send:

> `stable, troponin I 0.94 ng/mL, ECG shows ST elevation`

Show the dashboard Audit tab: `comms.blocked`, with the reason naming *why* —
"names a lab or diagnostic result, carries a clinical measurement".

> "The classifier reads the message content, not the caller's claim about it.
> An agent cannot talk its way past this, because it is not asking an agent."

**Half two — and the family is still told.**

This is the half that makes it a design rather than a wall. The chain shows
**two** receipts:

```
comms.blocked   → urgent_family_alert            (quoted version, refused)
comms.sent      → urgent_family_alert_withheld   (no quote, still delivered)
```

> "Her son still gets the alert. It says a medical detail was in the message, so
> it is not repeated here, and here is where to read it. The gate refuses to
> send something and then says where it lives. If it did not, being more
> clinically precise would make her harder to help."

**Half three — "somewhere protected" is a real door.** Fifteen seconds, and it
closes the argument the other two halves opened.

Tap the link in the message. The record opens, because a signed link sent to a
consented family member is a credential. Now open the **avatar menu**, top
right, and sign out. Sign in with Google.

> "That is a real Google account, verified server-side against Google's keys.
> But look at what the menu says: signed in as me, **on the record as her son**
> — and that second line is the one doing the work."

Now the part worth the ten seconds. Paste `$OTHER_PARENT` (pre-flight §6) into
the URL and hit enter.

```
403 — That Google account is signed in, but it is not on this parent's list of
      family contacts, so it cannot read their record.
```

> "Same account. Same session. Different mother. Signing in proves who you are;
> it does not say whose record you may open, and those are two different checks
> in the code. It is a 403 and not a 401 on purpose — telling someone already
> signed in to sign in again sends them round a loop."

Scroll the menu to the consents.

> "And this is what she agreed to, per purpose, with the date each was given.
> DPDP requires that. Every system I looked at records it and never shows it to
> the person who gave it, which makes it a checkbox."

---

## Beat 5 (2:20–3:20) — the treating team, and the son never touches it ⭐⭐⭐

The beat nobody else will have, and the one the whole pitch rests on. **The son
does nothing in it.** Watch for that while you record: if your hand is the one
forwarding the link, the demo is showing the opposite of the product.

Sequence matters here, and it is not the order you would guess. The link goes
out **before** the doctor speaks, and the doctor speaks **before** the bill
arrives — because that is the order it happens in a hospital, and because each
step is what makes the next one possible.

### 5a. The link went out on its own (~10s)

Scroll back to the WhatsApp thread from Beat 2. Under the alert, unprompted,
is a QR and a link addressed to the care circle.

> "Nobody asked for that. She reported chest pain, it decided this was going to
> need a hospital, and it did what a son does next — it got a credential into
> the hands of the person who is actually standing next to her.
>
> That is not me. I am asleep. It went to Meena, the neighbour."

### 5b. The doctor scans it (~15s)

**Pick up the second phone and scan the QR off the laptop screen.** Slowly, and
let the camera see it happen. This is the shot.

> "No login. No account. That doctor has never authenticated with anything."

Let the summary land. **Penicillin** at the top, large and red.

> "Allergies first, because that is the field that kills people when it is
> missed. Conditions, current medication, and her most recent troponin — with
> the date, because a troponin from March means something different from one
> taken this morning.
>
> Nothing on this page is advice. Anbu Care is the record, not the clinician."

Then tap **Connect this phone on WhatsApp**. It opens WhatsApp with a code
already typed. Send it.

> "One tap. That handset is now the treating team's, for this admission only.
> It proved it by holding the credential — not by claiming to be a doctor,
> because anyone can claim that."

The reply names the mode out loud: messages from this handset are recorded as
clinical notes and **not** as her check-ins, and `STOP` hands it back.

### 5c. The doctor speaks, in Tamil (~20s)

Record a voice note on the doctor's phone, in Tamil:

> நெஞ்சு வலி குறைந்துவிட்டது. காலையில் வார்டுக்கு மாற்றுகிறோம்.
> *(The chest pain has settled. We are moving her to the ward in the morning.)*

> "A status update is the commonest thing a treating team says, and it is the
> thing that never reaches the family."

Two things come back, and point at both:

- **On the doctor's phone:** recorded on her record, attributed to the treating
  team, nothing ordered.
- **On the family's:** an update was left, and where to read it — **not what it
  said.** The words are clinical detail and WhatsApp is not where those go.

Open the record behind the credential and show the note: the Tamil exactly as
he said it, and the English underneath, **labelled** as rendered rather than
spoken.

> "It kept his words and it did not put its own in his mouth."

### 5d. He orders a test (~15s)

Second voice note, same phone:

> ரத்த பரிசோதனை செய்ய வேண்டும்.
> *(She needs a blood test.)*

The reply carries the test as recorded and a list of real Thoothukudi centres
with distances.

> "It heard the order in Tamil and wrote it in English, because that is what a
> lab searches on. It did not upgrade "blood test" into "complete blood count"
> — translating what was said and deciding what he meant are different jobs,
> and it only does the first.
>
> Those are live Google Places results, ranked by distance. **Nothing is
> booked. No centre has been contacted.** It found the options. A human rings
> them."

### 5e. Hand the phone back (~5s)

Send **`STOP`**.

> "And the phone goes back to being Meena's."

Say **Meena's**, not yours. The next beat is a bill, and **doctors do not
photograph bills** — the neighbour standing in the corridor holding the paper
does. On three phones this is obvious because the bill arrives from her number
and no handback is needed at all. On one it is a line of narration doing the
work, and getting it wrong quietly puts the son back in the loop the whole
demo exists to get him out of.

**On one handset the `STOP` is not optional.** While it is connected as the
treating team, a photograph from it is refused — it will tell you it records
notes and orders, not bills, and ask for `STOP` first.

**Foot-guns:**
- Set `ANBU_PUBLIC_BASE_URL` before deploying, or the QR encodes a relative path
  and scans to nothing. Test the scan during pre-flight, not on camera.
- Screen glare kills QR scanning. Tilt the laptop back before the take.
- The link expires in **60 minutes**; the binding lasts **12 hours**. Mint the
  link during the take, not before.
- **Start unbound.** Pre-flight checks this. A handset left bound from a
  rehearsal will file your mother's opening voice note as a clinical note, and
  Beat 2 dies silently — no alert, no QR, nothing to cut to.
- The Tamil rendering is a live model call. If it is slow the note still lands;
  the English fills in a moment later.

---

## Beat 6 (3:20–4:00) — photograph the paperwork ⭐⭐

The most visual beat in the demo, and the one that most looks like the actual
job. Everything before this was the night it happened. This is the week
afterwards, which is where families in this situation actually lose money — and
it has to come after the handoff, or you are showing a discharge summary for an
admission the audience has not seen yet.

**Whose hands this is.** The bill comes from **Meena**, the neighbour. She is
the one at the hospital; the son is asleep. Say so, because it is the
difference between a system that replaces him and an app he still has to open.

She holds `inbound_wellbeing`, so she can send paperwork in, and she does
**not** hold `billing_updates` — so the amount, what was claimable and what was
refused go to the son's thread, not hers. She gets an acknowledgement that says
he is being told. That asymmetry is the design, and it is worth ten seconds:

> "She can hand the system a bill. She cannot read the family's money. Helping
> is not the same as being entitled to look."

**Half one — a hospital bill becomes an itemised claim.**

From the handset, photograph `bill_cardiac_icu.png` into the same WhatsApp
thread. The reply comes back immediately:

> "Got that. Reading it now."

Say the next line while the read is running, because it takes about fifteen
seconds and dead air is worse than narration:

> "That acknowledgement is not the answer. Reading a bill takes a Gemini vision
> call and the webhook has a fifteen-second budget, so the read happens after
> the reply. The message says nothing is recorded until it has been read, and it
> means it — if the read fails, nothing lands."

Then the second message lands: line count, the bill total, and the split.

Open the dashboard **Claim** tab.

> "Every line item on that bill, read off the photograph, and every one priced
> against her actual policy — one per cent of sum insured per day for a room, two per cent
> for ICU. The gloves and the admission kit are struck out because IRDAI says
> they are subsumed in the room charge, and this is where a family loses forty
> thousand rupees without ever being told why.
>
> This is where it gets serious. Applying the sub-limit alone is the easy half.
> Then there is **proportionate deduction** — if you take a room above your
> limit, the insurer reduces the associated charges in the same ratio, and
> almost nobody knows that. On this stay it moved what the family owes by
> roughly five times. The family is told that now, not at settlement."

> **Read the figures off the screen. Do not memorise them.** They depend on how
> many bills are on the case, and a fresh seed will not reproduce a rehearsal's
> numbers. For calibration: the current demo case carries **three** bills —
> ₹693,310 billed, ₹363,898 covered, ₹329,412 to pay, against ₹70,120 before
> proportionate deduction was applied. One bill alone gives smaller numbers and
> the same ratio. The *ratio* is the story and it survives whatever the state
> is, so say "roughly five times" and point at what is actually on screen.

Tap **Open the photograph**. The bill fills the screen.

> "Every figure on that screen is one tap from the paper it was read from. A
> number you cannot check against the page it came from is worth very little."

**Half two — a discharge summary changes the record.**

This is the agentic half. Send `doc_discharge_summary.png` to the same thread.

Before it lands, put the **Arrival** tab on screen and point at the unknowns.

> "Discharged on: not yet known. Diagnosis: not yet known. It says so rather
> than guessing, and that has been true for the whole demo."

Now the reply arrives, and refresh.

> "Discharged 22 August. Non-ST elevation acute coronary syndrome. Condition at
> discharge, follow-up on the fifth, treating consultant. Six of those lines
> said *not yet known* thirty seconds ago.
>
> Nobody typed any of it. It classified the document, extracted the fields,
> worked out which of them the record was missing, and filled them in. The
> prescription on it replaced her medication list. Her allergies **merged** —
> that one is deliberate, because a discharge summary lists what that admission
> recorded, and a shorter list is not a retraction of an allergy somebody has
> carried for years."

Open the **Record** tab and show the medication card.

> "Six medications with their dosing schedule. Morning, afternoon, night —
> those three boxes are what the paper prints as 1-0-0, and they are only drawn
> when the paper actually prints it. Where the prescription says 'once daily'
> instead, the words are shown as written. Choosing a slot there would be a
> renderer inventing a dosing time, and an invented one looks exactly as
> authoritative as a read one."

**If a photograph is refused as a duplicate**, that is the system working —
see pre-flight §5. Say so and move on; do not re-send it.

---

## Beat 7 (4:00–4:45) — it pays one, and refuses another ⭐⭐

The highest-consequence thing in the project, and the reason to lead with the
refusal: a system that spends money is believable in proportion to what it
declines to spend.

Set the scene in one line before you send anything.

> "Indian hospitals bill during the stay, not at the end. If an interim bill
> goes unpaid, cashless stops mid-treatment. A present son would read it and
> pay it. He is asleep in California."

**Half one — it refuses, and says which limit stopped it.**

Photograph `bill_cardiac_icu.png`. Balance due 2,70,720, against a per-bill cap
of 50,000. The reply is one message, and the line that matters is:

> INR 2,70,720 of it now … It was NOT paid automatically: INR 2,70,720 is above
> the per-bill cap of INR 50,000. Nothing has moved, and it needs you.

Open the **Claim** tab. The refusal is the first thing on it.

> "It read the bill, checked it against the limits he set, and stopped. The
> reason is on screen: above the per-bill cap. Nothing moved.
>
> This is the part I would want to see if it were my mother's money. Not that
> it pays — that it refuses, and tells me which rule stopped it."

Tap **Approve**. Point at the sign-in panel that appears in place.

> "Approving money needs a signed-in session. The link that opened this page
> can read the record; it cannot authorise a payment. Different acts, different
> credentials."

Sign in with Google, approve, and the card resolves.

**Half two — it clears one on its own.**

Photograph `bill_interim_day_two.png`. Same hospital, 38,450 due, inside every
limit. No tap. The reply says it has been paid.

Open the Claim tab again and point at the line items.

> "The room was capped at 10,000 — two per cent of a five-lakh policy, for one
> day. Then every associated charge came down by the same ratio, because the
> room exceeded its limit. That is proportionate deduction, it is the rule
> almost nobody knows about, and it is why this bill covers 12,875 rather than
> the 16,450 the sub-limit alone would give.
>
> Pharmacy and IV fluids are untouched. A policy cannot proportion down
> consumables, so it does not.
>
> Nobody was woken for that one. It was inside the envelope."

Then the two figures side by side.

> "Settled: zero. Initiated and not confirmed: 38,450. It will not call money
> paid until a settlement confirmation actually arrives — the same distinction
> as a message sent versus a message delivered."

**Optional, ten seconds, only if the pacing allows.** Tap **Pay by UPI** on the
payment card. A QR appears, with the hospital's UPI ID under it.

> "This is the destination on the mandate, not one read off the bill. UPI is
> what the country actually pays with, so there is a version of this that needs
> no provider at all."

**Say this once, plainly, while the numbers are on screen.** Use the line that
matches the rail you configured in pre-flight §6:

> **On `razorpay`:** "That is a real Razorpay payment link, in test mode. Real
> API call, real webhook, test money. What is not automated is the last step:
> a payment link exists to ask a person to pay, so somebody opens it."
>
> **On `payout`:** "Settlement is a simulated payout, and the screen says so.
> Nobody clicked, because a payout pushes money rather than requesting it."

Then, either way:

> "No mode here moves real money. Debiting her account unattended needs UPI
> Autopay, and NPCI caps a mandate debit without re-authentication at fifteen
> thousand rupees — hospital billing is not in the raised category, so a
> thirty-thousand-rupee bill would ask for a PIN every time. The route that
> does work is a payout from a funded account, and that is merchant KYC, not
> code.
>
> The mandate, the caps, the payee lock, the duplicate check and the anomaly
> rules are real, and they are what just ran."

**Optional extension, twenty seconds — the retake.** Strong if you have the
room, because it is the failure nobody in the audience will have thought of.

Photograph the bill you just cleared a second time, from a slightly different
angle. Send `bill_interim_day_three_retaken.jpg`, or make one with
`python scripts/retake_bill.py <bill>.png`.

> "Different photograph. Different file, different hash. The hospital issued
> one bill."

The reply names the bill number and refuses it.

> "It matched on the number the hospital printed, not on the bytes. If it had
> matched on the image, this would be a second bill on the case, twice the
> money owed, and a second payment eligible to go out — for a debt that exists
> once. That is what a family does: the first photo was blurry, so they took
> another."

Point at the money view, unchanged.

---

## Beat 8 (4:45–5:25) — watch it decide, then check that it did ⭐⭐

The agentic beat. Everything so far showed the system *acting*; this shows it
**deciding**, and then lets anyone verify the decisions were real.

```bash
curl -s -H "Authorization: Bearer $TOKEN" $URL/api/cases/$CASE/trace | jq
```

Read the sequence down the screen. Do not paraphrase it — let it speak:

```
 2  A claim packet was assembled          INR 358,500 claimed, 0 document(s) attached
 3  The claim was submitted               cashless_preauth
 4  The counterparty answered             QUERY — asked for discharge summary
 5  The agent gathered what was asked for attached 1 discharge summary
 6  The counterparty answered             PARTIAL — INR 66,000 disallowed
```

> "It submitted. It got asked for a document it had not sent. It went and found
> that document, attached it, and resubmitted — and the answer came back priced.
>
> Nobody scripted that branch. The query is what the adjudicator returned, and
> what happened next depended on it."

Then the line the whole architecture exists for. Point at `synthesized: 0`:

> "Every step you just read is a receipt. Not a log line, not a summary someone
> wrote afterwards — one step per receipt, and the view is **incapable** of
> adding a beat the chain does not contain. If a step is not on the chain, it is
> not on the screen."

Now the pairing — same case id, no credentials, in the same breath:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/cases/$CASE/trace    # 401
curl -s $URL/api/cases/$CASE/verify | jq                                # 200
```

> "You have just watched it decide. Now check that it did — and you do not need
> my permission. That verification is public and it proves the record has not
> been altered **without revealing what it says**. Anyone can check us. Nobody
> can read her.
>
> That is the only reason the privacy argument holds. If the record were
> readable by anyone with the URL, refusing to send it over WhatsApp would be
> theatre."

**Say this once, plainly** — it is the sentence the judges should remember:

> "Autonomy you can verify. Those are usually a trade-off. Here they are the
> same screen."

**Foot-guns:**
- The trace is credentialed. Have `$TOKEN` exported *before* the take — a 401
  here reads as a broken demo rather than a boundary.
- `gathered@[]` with an empty list means the case was driven a way that writes
  no gather receipt. Use `claim-flow`, and check during pre-flight.

---

## Beat 9 — tamper ⭐ (cut from the running order)

```bash
curl -s $URL/api/cases/case-a7cf9fa613/verify
```

```
verified: false
broken_at_seq: 1
reason: payload does not hash to the recorded hash — content was altered
```

> "Same endpoint, a case where one payload was edited after the fact. It names
> the exact link where the chain breaks. And the judge-facing case is still
> true — go and check it yourself after this video."

---

## Beat 10 (5:25–6:00) — the honest wall

Do not skip this. It is the strongest beat in the demo, because everyone else's
demo skips it.

> "What this does not do.
>
> It does not detect anything. Nothing is sensed, nothing is monitored. Every
> episode starts because a person sent something.
>
> It does not diagnose. It recognises phrases from a reviewable table. That
> table is public first-aid guidance and it has **not** been reviewed by a
> clinician — a real deployment needs that done.
>
> It does not call an ambulance, and it says so in the message itself. Twilio's
> emergency calling does not cover India, and a recording cannot answer a
> dispatcher asking whether she is conscious. So it wakes people who can act,
> and tells them to ring 108.
>
> No hospital system is integrated — not one. That summary the nurse read was
> **outbound presentation, not an EHR connection**. Nobody writes back into
> anything, and if she leaves a note it lands on our chain, not in the
> hospital's. The care circle are notified parties, not partners.
>
> It does not move real money. The mandate, the caps, the payee lock, the
> duplicate check and the anomaly rules are real code and they are what you
> just watched run. The settlement is simulated, exactly like the TPA: real
> autonomous UPI debit needs a licensed payment provider and NPCI mandate
> rails, and no US card can fund UPI at all.
>
> And it never chooses where money goes. The destination is typed once by a
> human, out of band, and pinned. A bill can ask for an amount; it can never
> ask for a destination, and a bill that names one is refused for naming one.
>
> It does not read a document it has not been sent. Nothing is fetched from a
> hospital portal or an insurer's system. Somebody photographs a piece of paper,
> and if the photograph is unreadable the record says so rather than guessing at
> what it probably said.
>
> The claim figure is an **estimate from the policy terms, not the insurer's
> decision**. It is labelled that everywhere it appears. Being told the figure a
> week early is worth a great deal and it is still not a settlement.
>
> And the handoff link cannot tell you *who* opened it. It is a link — whoever
> holds it, holds it. So the receipt says a link holder read the summary, and
> it does not put a doctor's name on something we could not verify. That is why
> it dies in an hour and why the family can kill it from their phone.
>
> Every figure on screen is synthetic. The TPA is simulated. The documents are
> generated and say so on their face. And when the transport fails, the record
> says `not_delivered` — it never claims a message it did not send."

**Close:**

> "The thesis is one line: the guarantees are code, not prompts. The severity
> table, the content classifier, the hash chain — a model can widen what the
> system understands, and it never decides what the system promises."

---

## After the take

- [ ] Do not delete **case-da1c2cb6db** or **case-a7cf9fa613**.
- [ ] Note the parent and case you recorded against, and **do not re-send those
      photographs to that parent** — the next attempt will be refused as a
      duplicate, which is right and is not a re-shootable state.
- [ ] If you re-shoot, seed a fresh family first. Regenerating the images is not
      enough on its own for bills, which dedup per case.
- [ ] Revoke the Twilio API key if the demo account is going idle.
- [ ] No sandbox opt-in to renew any more. But the 24-hour window still
      closes: message the sender from the handset before any re-shoot.
