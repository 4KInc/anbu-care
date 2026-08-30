# dev.to article, ready to publish

**Where:** https://dev.to/new
**Must be PUBLIC, not unlisted.** The bonus requires it.
**Must say it was created for the hackathon.** The line is in the first
paragraph and again at the end.

**Fact-checked:** 30 Aug 2026 against revision `anbu-care-00199-ncz`,
suite at 1,221 tests. Re-check before publishing.

---

## Title  *(paste into the title box)*

```
The guardrails that matter are code, not prompts
```

## Tags  *(max 4)*

```
googlecloud, ai, agents, showdev
```

## Cover image  *(optional)*

`docs/architecture.png`, or a screenshot of the receipt-chain trace.

---

## Body: everything below this line

I built an agent that coordinates eldercare for my mother in India, and the interesting part was not the agents. It was the four times I stopped and decided what the system was **not allowed** to do, and then put those decisions somewhere no prompt can reach.

*I created this post for the purposes of entering the All Things Agentic Hackathon (Google Cloud / Devpost).*

It's live, and you can check its work without asking me: **https://anbu-care-37j4eofpwq-el.a.run.app**

---

## The problem, briefly

My parents live in Thoothukudi. I don't. Every NRI I know has the same 3 a.m. fear: something happens, and by the time you hear about it, the decisions that mattered have already been made by whoever was standing there.

One rule fell out of that and shaped everything: **any step that needs the son to act is a design failure.** He is asleep, nine and a half time zones away. That's the problem, not a detail of it. So every feature got tested with him unreachable.

---

## 1. A guardrail you can argue with is not a guardrail

Five agents on Gemini 3.5 Flash and Google's ADK: onboarding, triage, evidence, insurer liaison, comms, all under a coordinator. Underneath them is a deterministic layer no agent can reach past. **The model proposes; that layer decides, and it is the only thing that can write.**

The demo input for triage is a neighbour calling to say *"she says it's probably just gas."*

Severity comes back HIGH.

The thing that decides severity is a Python dict. It never reads that sentence as permission, because it never reads that sentence.

The second one is the one I'd defend hardest. Clinical detail can't go over WhatsApp. India's DPDP Act and Meta's healthcare policy make that a legal line. So the gate classifies the **content**, not the caller's claim about it. A message declared `logistics` that reads *"just logistics: troponin 0.94 ng/mL"* is blocked anyway.

Then the demo does something that took me embarrassingly long to think of: it bypasses the agent entirely and calls the send function directly.

Still blocked.

That second half is the whole claim. **An agent that is merely *told* not to leak a lab value is not a control.** If your only enforcement is in the system prompt, you have a strong suggestion, and you will find out which it was on the day it matters.

---

## 2. Public where it proves, private where it reveals

This is the design decision I'm most pleased with, and it came from a question I couldn't answer cleanly at first.

If clinical data can't go over WhatsApp because it "lives somewhere protected", what exactly is protecting it?

If the answer had been "a URL nobody guesses", the whole argument would have been hollow, and I'd have published the exact data I claimed to guard.

So the API has two access models, both enforced server-side:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/parents/{id}      # 401
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/cases/{id}/verify # 200
```

Every action appends an Ed25519-signed receipt whose hash covers the previous one. **Verification proves the record wasn't altered without revealing what it says.** It returns hashes, a boolean and a failure mode. That's exactly why it can be open to everyone, and why it *has* to be: a receipt chain only means something if you can check it without my permission.

You can watch it catch an edit. Rewrite a stored receipt straight in Firestore, leaving the hash and signature untouched, exactly as a silent after-the-fact edit would:

```json
{ "field": "payload.severity", "before": "HIGH", "after": "LOW" }
```

Then ask the public endpoint:

```json
{ "verified": false, "receipt_count": 2, "broken_at_seq": 1,
  "reason": "payload does not hash to the recorded hash: content was altered" }
```

It names the receipt. Not "something is wrong", but *sequence one, content altered.*

One caveat I put in my own demo script, because leaving it out would be a lie by omission: **an empty chain is a valid chain.** A deleted case answers `verified: true` with `receipt_count: 0`. `verified` on its own proves the absence of tampering in nothing at all. The count is what ties the proof to the story.

---

## 3. The bug that would have discredited everything

On one run, the agent told me:

> "I have successfully read your mother's lab report and ingested it into her health record."

Documents actually stored: **zero**.

For a system whose entire pitch is a verifiable record, an agent asserting a write that never happened is fatal. Not embarrassing. Fatal. Everything else I had built was an argument that you could trust the record, and here was the record's own narrator making things up.

Prompt wording was never going to fix that. So the demo now prints the stored count **read back from the service**, right next to what the agent claimed:

```
GROUND TRUTH. Documents actually stored for this parent: 2
reported status 'ingested' vs stored count 2: consistent
```

If they ever disagree, it says `CONTRADICTED` on screen.

The general lesson I took: anywhere an agent reports an outcome, report the outcome **from the system that would know**, side by side, and make the disagreement loud.

---

## 4. The bug that taught me what my tests were worth

This one is more useful, because it looks like nothing.

The document reader emits the kind `lab_report`. The record stores it as `blood_report`. I added a guard (an arriving lab report should close the outstanding diagnostic order it belongs to) and compared against the *stored* word.

Nothing raises. No error appears anywhere. No log line. The guard simply never matches, and the loop silently closes nothing, forever.

**Eighteen unit tests passed.** They all called the function directly and handed it the right word themselves.

The fix wasn't the one-word change. It was writing two tests that go through the real ingestion path end to end, and **confirming they fail first**, with the message *"the report went in and closed nothing; the hook and the reader disagree about what a lab report is called"*.

A test that cannot fail for the reason you care about is not covering that reason. I now break every new guard on purpose and check that something goes red before I let the change stand.

---

## 5. The refusal I'm proudest of

That same lab-report loop has a case where it does nothing, deliberately.

If **two** tests are outstanding on an admission and one report arrives, it closes neither.

Attributing that report to one of the two orders means reading it to work out which test it is, and that is a model deciding which clinical order was carried out. So it stops, writes a receipt saying exactly why, and leaves a person an accurate record and an obvious next step.

The same instinct shows up in the memory. The system remembers two classes of fact between admissions: whether she answers by voice note or by typing, and which language she actually writes in. Her profile carries a language too, but it was chosen for her at onboarding, usually by a son filling in a form from another country. This one she demonstrated, and where they disagree the demonstration wins. It's in a Vertex AI Agent Engine Memory Bank, and there is **no free-text path into that store.** Each kind of memory has its own function composing its own sentence from a value validated first. A caller can't put a symptom in it because a caller can't put a *sentence* in it. Recall is an exact scope lookup, never a similarity search, so an unrelated memory can't surface because it read as close enough.

That's the whole philosophy in one small module: **decide what may be true, in code, and the interesting failures become impossible rather than unlikely.**

---

## What's actually running

Gemini 3.5 Flash and the Agent Development Kit on Cloud Run, Firestore for state and the receipt ledger, Pub/Sub for multi-day case events, Cloud Storage for photographs, Vertex AI Agent Engine for Memory Bank, Google Places to verify that every hospital is a real place. One thing runs on a smaller model on purpose: detecting which language she writes in is a question with a two-letter answer, and spending a frontier model on that, on a path that owes her a reply in fifteen seconds, is the wrong trade. Gemini 2.5 Flash Lite answers it.

Cloud Scheduler matters more than it sounds. Cloud Run holds no timer, so the regulatory clocks are real scheduler jobs ticking every minute, which is the difference between an agent and a demo. It keeps running when the browser is closed and nobody is watching.

**1,221 tests, none of which need cloud access or a model to run.**

---

## What isn't real, stated plainly

Because a post that spent 2,000 words on honesty should end with some:

- **The insurer's adjudicator is simulated.** Deterministic local rules, labelled as such on every receipt and every API response, not just here.
- **Payments run on a real provider in test mode.** Real Razorpay link, real API call, real webhook, test money. No mode moves real money.
- **WhatsApp is real** (Twilio, actual handsets) but freeform-only inside the 24-hour window, because there are no approved templates.
- **Hospital data is half real.** Locations carry a Places `place_id` and a verification date. Capability and insurer empanelment are a dated seed and say so on every triage call.
- **All demo data is synthetic**, and the clinical views say so on screen, because a screenshot outlives a demo.

It also doesn't watch anyone. No sensors, no passive monitoring. An episode begins because a signal *arrives*, and the receipt says `received from an external channel, not detected by Anbu Care`. The tests reject the words "detect", "notice", "sense" and "monitor" in that path unless they follow a negation.

---

## Try to break it

```bash
URL=https://anbu-care-37j4eofpwq-el.a.run.app
curl -s $URL/api/cases/case-da1c2cb6db/verify | jq   # verified: true, 8 receipts
curl -s $URL/api/cases/case-a7cf9fa613/verify | jq   # verified: false, broken_at_seq: 1
```

Dashboard: `/app`. Health, including which components are simulated: `/api/healthz`.

I created this article for the purposes of entering the **All Things Agentic Hackathon**. If you take one thing from it: put your guarantees in code, then try to break them from *outside* the agent. If it still holds, you have a control. If it doesn't, you have a prompt.

*Anbu (அன்பு) is Tamil for love.*

#AllThingsAgenticHackathon

---

## Pre-publish checklist

- [ ] Published **public**, not unlisted.
- [ ] The line *"created this post for the purposes of entering the All Things
      Agentic Hackathon"* is present near the top.
- [ ] Max 4 tags.
- [ ] `make test`, confirm 1,221 and update if not.
- [ ] `curl -s $URL/api/healthz`, confirm the "what isn't real" list still
      matches `tpa_mode`, `whatsapp_mode`, `memory_bank`.
- [ ] Both canonical case ids still verify as stated.
- [ ] URL pasted into the Devpost "link to a piece of content" field.
