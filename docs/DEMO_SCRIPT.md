# Demo script — ~5 minutes, unedited

One take. A phone on camera, one terminal, one browser tab.

**Live URL:** https://anbu-care-37j4eofpwq-el.a.run.app

The arc changed with W1/W2. It used to open with a simulated intake signal
arriving from outside. It now opens with **a seventy-one year old sending a
voice note in Tamil at 2am**, because that is the actual product and the
machinery underneath is more convincing once you have seen why it matters.

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

### 5. Walk the dashboard by hand

Open `$URL/app` at the window size you will record.

- [ ] Before signing in, a content tab shows **401 THE CASE TRAIL IS CREDENTIALED**.
- [ ] Sign in with `anbu-demo-family-token`; every tab renders.
- [ ] **Scroll the Record tab and confirm the nav is still reachable** — a
      sticky-positioning bug lived exactly there.
- [ ] Check-ins panel shows your throwaway message, labelled
      **SELF-REPORTED — NOT A MEASURED VITAL**.
- [ ] `SYNTHETIC — DEMO DATA` visible on every clinical view.

### 6. Recording hygiene

- [ ] Say **"synthetic"**, **"simulated"** and **"seeded snapshot"** at least
      once each. The honesty framing is the architecture case, not a disclaimer.
- [ ] Say **"received"**, never "detected".
- [ ] Say **"recognised"** or **"matched"**, never "diagnosed".
- [ ] Do not claim an ambulance is called, or could be.
- [ ] Voice calls are **built but switched off** (no Twilio number purchased).
      If you mention the ladder, say it is off.
- [ ] Say **"the table decides, the model can add"**. Never "the AI decides".
- [ ] A voice note takes ~10s. Do not fill the silence with hedging; say what
      is happening ("it is transcribing, then matching") and let it land.

### 7. Backup take

Record a second pass immediately, before changing anything. Everything except
the Gemini transcription and extraction is deterministic;
`docs/takes/backup-take-spine.txt` is the fallback if recording fails outright.

---

## Beat sheet

| Time | Beat | Point at |
|---|---|---|
| 0:00–0:20 | **The question** | "If something happens to my mother tonight, who makes sure the right decisions get made?" Every incumbent answers: a human coordinator. |
| 0:20–1:20 | **She sends a voice note in Tamil** ⭐⭐ | The hook. Phone on camera. |
| 1:20–2:10 | **What decided that** ⭐ | Gemini translated. A table in code decided. Both are recorded. |
| 2:10–2:50 | **The boundary** ⭐ | Clinical detail refused — and the family still told. |
| 2:50–3:20 | **The claim** ⭐ | QUERY → resolved → PARTIAL. ₹66,000, told now. |
| 3:20–3:50 | **Public where it proves, private where it reveals** ⭐ | 401 and 200, same case. |
| 3:50–4:20 | **Tamper** ⭐ | `verified: false, broken_at_seq: 1`. |
| 4:20–4:50 | **The honest wall** | What it does not do, said out loud. |

**If you must cut:** drop the claim beat (2:40–3:20). It is the strongest
*business* beat but the weakest *architecture* beat, and the PDF can be shown as
a still in the writeup.

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

2. **The routing** — Sacred Heart, 2.2 km, *1.4 km further than the nearest
   hospital*, chosen because it is empanelled with Star Health so the admission
   stays cashless.

3. **The care-circle notice** — the neighbour is asked to call. **No symptoms.
   No link into her record.**

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

## Beat 3 (1:20–2:10) — what actually decided that ⭐

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

## Beat 4 (2:10–2:50) — the boundary ⭐

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

## Beat 5 (2:50–3:20) — the claim ⭐

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

## Beat 6 (3:20–3:50) — public where it proves, private where it reveals ⭐

Same case id, two URLs, no credentials:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/cases/case-da1c2cb6db          # 401
curl -s $URL/api/cases/case-da1c2cb6db/verify                                    # 200
```

> "Anyone can prove this case has not been altered. Nobody can read it. The
> verification endpoint proves integrity without revealing content — which is
> the only reason the DPDP argument holds. If the record were readable by anyone
> with the URL, refusing to send it over WhatsApp would be theatre."

---

## Beat 7 (3:50–4:20) — tamper ⭐

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

## Beat 8 (4:20–4:50) — the honest wall

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
> No hospital is integrated. No doctor is in the loop. The care circle are
> notified parties, not partners.
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
