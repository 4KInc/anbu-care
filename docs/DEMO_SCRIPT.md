# Demo script — ~5m30s, unedited

One take. **Two phones** on camera (hers, and the nurse's), one terminal, one
browser tab.

**Live URL:** https://anbu-care-37j4eofpwq-el.a.run.app

The arc changed with W1/W2. It used to open with a simulated intake signal
arriving from outside. It now opens with **a seventy-one year old sending a
voice note in Tamil at 2am**, because that is the actual product and the
machinery underneath is more convincing once you have seen why it matters.

Two beats are new since the last recording and neither has been performed
before: **the clinician handoff** (Beat 5), where a nurse scans a QR and reads
her allergies without logging into anything, and **the trace** (Beat 7), where
the agent's decision sequence is read off the chain and then verified by anyone
watching. Rehearse both. Pre-flight §5 exists specifically for them.

---

## The one thing to say early, or a judge will spot it

The demo handset is registered as **Karthik, the son**. So when you send a
message, you are playing the mother, and the alerts come back to the same
thread. Say it out loud once:

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

- [ ] Twilio sandbox webhook points at
      `$URL/api/wellbeing/inbound`, method POST
      (Console → Messaging → Try it out → Send a WhatsApp message → Sandbox settings)
- [ ] Re-send **`join school-rate`** to **+1 415 523 8886** from the handset.
      The sandbox session expires after 3 days and the 24-hour freeform window
      has to be open. Do this even if you joined yesterday.
- [ ] Send one throwaway `slept well` and confirm you get
      **"Thanks, that's noted."** back. If you get a Twilio demo echo instead,
      the webhook URL did not save.
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
      them.
- [ ] **Old cases carry old distances, and that is correct.** Hospital
      coordinates were re-verified against Google Places on 21 Aug, which moved
      them by up to 5 km. Receipts are immutable, so a case triaged before that
      keeps the numbers that were true when the decision was made. If anyone
      asks why an old receipt says 2.2 km and a fresh run says 5.6 km, that is
      the answer, and it is the chain working rather than failing. The case you
      create live during the demo will use the corrected coordinates.

### 5. The handoff and the trace (new beats, new ways to fail)

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

### 6. Walk the dashboard by hand

Open `$URL/app` at the window size you will record.

- [ ] Before signing in, a content tab shows **401 THE CASE TRAIL IS CREDENTIALED**.
- [ ] Sign in with `anbu-demo-family-token`; every tab renders.
- [ ] **Scroll the Record tab and confirm the nav is still reachable** — a
      sticky-positioning bug lived exactly there.
- [ ] Check-ins panel shows your throwaway message, labelled
      **SELF-REPORTED — NOT A MEASURED VITAL**.
- [ ] `SYNTHETIC — DEMO DATA` visible on every clinical view.

### 7. Recording hygiene

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

### 8. Backup take

Record a second pass immediately, before changing anything. Everything except
the Gemini transcription and extraction is deterministic;
`docs/takes/backup-take-spine.txt` is the fallback if recording fails outright.

---

## Beat sheet

| Time | Beat | Point at |
|---|---|---|
| 0:00–0:20 | **The question** | "If something happens to my mother tonight, who makes sure the right decisions get made?" Every incumbent answers: a human coordinator. |
| 0:20–1:20 | **She sends a voice note in Tamil** ⭐⭐ | The hook. Phone on camera. |
| 1:20–2:00 | **What decided that** ⭐ | Gemini translated. A table in code decided. Both are recorded. |
| 2:00–2:35 | **The boundary** ⭐ | Clinical detail refused — and the family still told. |
| 2:35–3:15 | **She arrives, and you are not there** ⭐⭐ | Scan the QR. A nurse reads her allergies with no login. |
| 3:15–3:45 | **The claim** ⭐ | QUERY → resolved → PARTIAL. ₹66,000, told now. |
| 3:45–4:30 | **Watch it decide, then check that it did** ⭐⭐ | The trace, then `/verify`. Autonomy and audit on one screen. |
| 4:30–5:00 | **Tamper** ⭐ | `verified: false, broken_at_seq: 1`. |
| 5:00–5:30 | **The honest wall** | What it does not do, said out loud. |

Runs ~5:30, up from ~5:00. Two beats were added and only one costs time: the
handoff is new (~40s), while the trace beat **absorbs** the old
public-vs-private beat rather than sitting beside it — showing the credentialed
trace and then the open `/verify` makes the same point in one movement instead
of two. The rest was tightened by five to ten seconds a beat.

**If you must cut:** drop the claim beat (3:15–3:45), as before. It is the
strongest single beat but the trace beat now carries the QUERY→gather→resolve
arc anyway, so cutting it costs less than it used to. Do **not** cut the
handoff or the trace — they are the two beats no other entry will have.

---

## Beat 2 (0:20–1:20) — she sends a voice note ⭐⭐

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

## Beat 3 (1:20–2:00) — what actually decided that ⭐

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

## Beat 4 (2:00–2:35) — the boundary ⭐

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

---

## Beat 5 (2:35–3:15) — she arrives, and you are not there ⭐⭐

The beat nobody else will have. It needs **two devices on camera**: the laptop
showing the family dashboard, and a second phone standing in for the nurse's.

On the dashboard, **Record** tab, scroll to *Share with the treating team* and
tap **Create a link**. A QR appears.

> "She has been routed to Sacred Heart. She arrives, and her son is eleven time
> zones away and asleep. The nurse receiving her has no idea what she is
> allergic to."

Now **pick up the second phone and scan the QR off the laptop screen.** Do it
slowly and let the camera see it happen — this is the shot.

> "No login. No account. That nurse has never authenticated with anything."

Let the summary land. **Penicillin** is at the top, large and red.

> "Allergies first, because that is the field that kills people when it is
> missed. Conditions, current medication, and her most recent troponin — with
> the date, because a troponin from March means something different from one
> taken this morning.
>
> Nothing on this page is advice. It does not tell the doctor what to do. Anbu
> Care is the record, not the clinician — and the summary has no field a
> recommendation could even be written into."

Then the part that makes it defensible rather than reckless:

> "That link is scoped to this one case, it dies in an hour, her son can revoke
> it from here, and **every time it is opened a receipt is written to the chain
> he can read.** It cannot reach the audit trail, her full record, or any other
> case.
>
> And it does not claim to know who opened it, because a link cannot. The
> receipt says a link holder read the summary. Not a name we could not verify."

**Optional, if the take is going well** (~15s): record a voice note on the
nurse's phone and show the transcript come back for confirmation.

> "A doctor would rather talk than type. Gemini transcribes it — and **nothing
> is written until they confirm it.** A misheard number must never land as a
> clinical fact with someone's name on it."

Tap confirm. The note appears on the chain in the next beat.

**Foot-guns:**
- Set `ANBU_PUBLIC_BASE_URL` before deploying, or the QR encodes a relative path
  and scans to nothing. Test the scan during pre-flight, not on camera.
- Screen glare kills QR scanning. Tilt the laptop back before the take.
- The link expires in **60 minutes**. Mint it during the take, not before.

---

## Beat 6 (3:15–3:45) — the claim ⭐

```bash
uv run python scripts/demo_support.py claim-flow $CASE $PARENT
```

QUERY first — a required document is missing, so nothing is priced. Then the
document arrives, resubmission, **PARTIAL**.

> "₹66,000 is the shortfall: ICU at 2% of a five-lakh policy per day, over three
> days. That number is derived from the policy, not typed into a slide. And the
> family is told **now**, not at settlement — that is the whole point.
>
> This is a **simulated** TPA. Deterministic local rules. No insurer is
> contacted."

If you want the attachment on screen, trigger the notify endpoint and show the
PDF landing on the phone. Its `sha256` matches the receipt chain — you can
download it and hash it.

---

## Beat 7 (3:45–4:30) — watch it decide, then check that it did ⭐⭐

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

## Beat 8 (4:30–5:00) — tamper ⭐

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

## Beat 9 (5:00–5:30) — the honest wall

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
> And the handoff link cannot tell you *who* opened it. It is a link — whoever
> holds it, holds it. So the receipt says a link holder read the summary, and
> it does not put a doctor's name on something we could not verify. That is why
> it dies in an hour and why the family can kill it from their phone.
>
> Every figure on screen is synthetic. The TPA is simulated. And when the
> transport fails, the record says `not_delivered` — it never claims a message
> it did not send."

**Close:**

> "The thesis is one line: the guarantees are code, not prompts. The severity
> table, the content classifier, the hash chain — a model can widen what the
> system understands, and it never decides what the system promises."

---

## After the take

- [ ] Do not delete **case-da1c2cb6db** or **case-a7cf9fa613**.
- [ ] Revoke the Twilio API key if the demo account is going idle.
- [ ] The sandbox opt-in expires in 3 days — re-join before any re-shoot.
