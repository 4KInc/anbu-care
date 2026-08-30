# Build log draft — public blog post

**Status:** draft for the user to publish. Not published by Claude.
**Bonus requirements:** the post must be **public** (not unlisted), must say
explicitly that it was created for the hackathon, and should carry the tag. The
"created for" line is the first line of the post below, and
**#AllThingsAgenticHackathon** closes it.

**Last fact-check:** 30 Aug 2026, against revision `anbu-care-00196-wd4`.
Every figure below was read off the deployed service or the test suite on that
date. Re-run the checklist at the bottom before publishing.

---

## Guardrails you can't talk your way past: building Anbu Care

*Created for the All Things Agentic Hackathon (Google / Devpost).*

My parents live in Thoothukudi. I don't. Every NRI I know has the same 3 a.m.
fear: something happens, and by the time you hear about it, the decisions that
mattered have already been made by whoever was standing there.

The existing answer to that is a person — a family friend, a paid proxy, a
sibling WhatsApp thread. Sahaayak, Samarth Care, Care247, Policybazaar's NRI
Care Program: all of them are human-coordinator models. I wanted to know whether
an agent could take that role, and what would have to be true before I'd let it.

Anbu Care is the answer I built in the submission window. It's live, and you can
check its work without asking me: **https://anbu-care-37j4eofpwq-el.a.run.app**

The design rule I kept coming back to: **any step that needs the son to act is a
design failure.** He is asleep, and nine and a half time zones away, and that is
the entire problem. So the test for every feature was whether it still works
with him unreachable.

---

### 1. The guardrails that matter are code, not prompts

Anbu Care has five agents on Gemini 3.5 Flash and Google's ADK — onboarding,
triage, evidence, insurer liaison, WhatsApp comms — under a coordinator.
Underneath them is a deterministic layer no agent can reach past. The model
proposes; that layer decides, and it is the only thing that can write.

Three things hold on *every* run, including the run where the model is confused
or the caller is reassuring:

**A red-flag symptom escalates.** The severity table is a Python dict. The demo
input is a neighbour calling to say *"she says it's probably just gas."* Severity
comes back HIGH anyway, because the thing that decides severity never reads that
sentence as permission.

**Clinical detail never leaves over WhatsApp.** India's DPDP Act and Meta's
healthcare policy make this a legal line, not a style preference. The gate
classifies the *content*, not the caller's claim about it — a message declared
`logistics` that reads *"just logistics: troponin 0.94 ng/mL"* is blocked anyway,
and the blocked attempt is written to the audit trail, because a block is
evidence the boundary held.

The demo does this twice on purpose. First the agent is asked to relay a lab
value and refuses. Then I bypass the agent entirely and call the send function
directly — and it's *still* blocked. That second half is the whole claim. An
agent that is merely *told* not to leak a lab value is not a control.

**A decision can't be silently rewritten.** Every consequential action appends an
Ed25519-signed receipt whose hash covers the previous one.

---

### 2. The thirty seconds that make it an agent

She sends a voice note in Tamil. Nobody presses anything after that. Inside
about fifteen seconds, four things happen:

- her son gets an alert, in English
- the neighbour gets a notice in Tamil carrying a scoped, revocable bedside link
- **a cashless pre-authorisation is filed against her policy**, and
- a one-hour clock starts

The clock is not decorative. The IRDAI Master Circular on Health Insurance
Business (IRDAI/HLT/CIR/PRO/84/5/2024, 29 May 2024) gives an insurer **one hour**
to decide a complete cashless request, and three hours for discharge
authorisation. Where the delay causes extra cost — an additional room day — the
circular puts that cost on the insurer, and delayed settlement carries interest
at two percent above the bank rate.

So when the hour lapses, the family is told what they are entitled to, and told
it in the same breath that **Anbu Care has not filed anything, cannot compel
anyone, and is not claiming this will be won.** Stating a right is not the same
as winning it, and a system that blurs those two is worse than one that says
nothing.

Cloud Run holds no timer, so the clock is a real Cloud Scheduler job ticking
every minute. That is the difference between an agent and a demo: it keeps
running when the browser is closed and nobody is watching.

**Then it books a clinic.** A doctor at the bedside dictates a test, in Tamil,
into a handset bound to the case for sixty minutes. Nobody chooses a lab. The
system searches real Thoothukudi diagnostic centres, ranks them against a
mandate — fifteen kilometres, eight attempts, must be cancellable — and then
opens each centre's own booking site in a real headless browser: read the form,
map the fields, fill it, read it back, submit, screenshot.

Seven of the eight real centres can't take a submission. One can, and the
booking reaches it under her name.

Two details I'd defend in a code review:

- It records the appointment as **`requested`, not `confirmed`.** An
  unauthenticated callback form cannot truthfully produce anything stronger, so
  the record doesn't claim it did.
- It captures the **cancellation path before it commits to anything.** An agent
  that can create an obligation and can't undo it is worse than one that does
  nothing.

And it keeps **two** screenshots, not one — the form as it stood filled in, and
the centre's answer page. A good form clears itself on success, so the answer
alone shows empty boxes and reads as though nothing was ever typed. I only
learned that by looking at the evidence and finding it unconvincing.

---

### 3. Public where it proves, private where it reveals

This is the design decision I'm most pleased with, and it came from a question I
couldn't answer cleanly at first: if clinical data can't go over WhatsApp because
it "lives somewhere protected" — what exactly is protecting it?

If the answer had been "a URL nobody guesses", the whole DPDP argument would have
been hollow, and I'd have published the exact data I claimed to guard.

So the API has two access models, both enforced server-side:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/parents/{id}      # 401
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/cases/{id}/verify # 200
```

**Verification proves the record wasn't altered *without revealing what it
says*.** It returns hashes, a boolean and a failure mode. That's exactly why it
can be open to everyone — and it has to be, because a receipt chain only means
something if you can check it without my permission.

You can watch it catch an edit. Rewrite a stored receipt directly in Firestore,
leaving the hash and signature untouched, exactly as a silent after-the-fact edit
would:

```json
{ "field": "payload.severity", "before": "HIGH", "after": "LOW" }
```

Then ask the public endpoint:

```json
{ "verified": false, "receipt_count": 2, "broken_at_seq": 1,
  "reason": "payload does not hash to the recorded hash — content was altered" }
```

It names the sequence number. Not "something is wrong" — *this* receipt, content
altered. If a system's account of what it did to somebody's mother ever ends up
disputed, that is the difference between a log and evidence.

One caveat I put in the demo script because it would otherwise be a lie by
omission: **an empty chain is a valid chain.** A deleted case answers
`verified: true` with `receipt_count: 0`. `verified` on its own proves the
absence of tampering in nothing at all. The count is what ties the proof to the
story.

---

### 4. The refusal I'm most pleased with

Late in the build I closed a loop that had been open the whole time. The booking
lane could submit a request to a clinic and then never learn anything again, so
a case would sit at "the centre has not answered" for weeks after she had been,
had the blood drawn, and had the result in her hand. The record was going stale
in the one direction that matters: it was under-reporting care that actually
happened.

The fix needed no new data source, because one already arrives. The family
photographs the lab report into the system in the ordinary way, and a lab report
for her can only exist if somebody drew her blood.

So an arriving report closes the outstanding test — and the interesting part is
the case where it doesn't. **If two tests are outstanding, it closes neither.**
Attributing the report to one of them means reading it to work out which test it
is, and that is a model deciding which clinical order was carried out. So it
stops, writes a receipt saying exactly why, and leaves a person an accurate
record and an obvious next step.

It also refuses a report dated *before* the order, because an old result
photographed for the insurer is not evidence of a visit that hadn't been
arranged yet.

The status it writes is its own word, `resulted`, rather than a reuse of
`confirmed`. A centre confirming a slot and her actually attending are different
facts. The second is weaker evidence than a centre's confirmation and stronger
than the silence it replaced, and giving it a borrowed name would have thrown
that distinction away.

---

### 5. Arithmetic you can check in your head

There's no production TPA API to integrate against in a hackathon window, so the
adjudicator is simulated — and labelled `SIMULATED — deterministic local rules,
not an insurer` in every payload, every receipt, and every sentence the agent
says about it.

But the *math* is real, and I made a rule: if a number goes on screen next to a
visible policy, a judge has to be able to do it in their head and have it tie
out.

The conventional Indian ICU sub-limit is 2% of sum insured per day. On the
synthetic ₹5,00,000 policy that's ₹10,000/day. The stay is 19–22 August, three
days, so ₹30,000 of a ₹96,000 ICU bill is payable and **₹66,000 is not.**

I didn't reverse-engineer that number. The ₹5,00,000 and the ₹96,000 both existed
in the demo seed before this feature did; the percentage is a real convention;
₹66,000 is just where the arithmetic landed. A test recomputes every step from
first principles and asserts the figure *displayed* equals the figure *computed*,
so the narration and the code can't drift apart.

---

### The bugs worth writing about

**The one that discredits everything else.** On a run where the ingestion tool
was never called, the agent told me: *"I have successfully read your mother's lab
report and ingested it into her health record."* Documents actually stored:
**zero**.

For a system whose entire pitch is a verifiable record, an agent asserting a
write that never happened is fatal. Prompt wording wasn't going to fix it, so the
demo now prints the stored count *read back from the service*, next to what the
agent claimed, and says `CONTRADICTED` on screen if they disagree.

I applied the same discipline to the arrival brief — the artifact a family reads
at their most frightened moment, and the place a synthesis is most tempted to be
helpful. It's composed in code from the signed chain, every line carries the
receipt it came from, and anything the state doesn't contain comes back as *"not
yet known"* with the reason.

While testing it I found a subtler version: a queried claim has
`total_disallowed_inr: 0`, because nothing has been *priced* yet. The brief was
rendering that as "₹0 so far". Traced to a real field, and still a lie — false
reassurance about money.

**The one that taught me what my tests were worth.** The document reader emits
the kind `lab_report`; the record stores it as `blood_report`. The first version
of the new closing guard compared against the *stored* word. Nothing raises. No
error appears anywhere. The guard simply never matches, and the loop silently
closes nothing, forever.

Eighteen unit tests passed, because they all called the function directly and
handed it the right word themselves.

The fix wasn't the one-word change. It was writing two tests that go through the
real ingestion path end to end, and confirming they fail — with the message
*"the report went in and closed nothing; the hook and the reader disagree about
what a lab report is called"* — before letting the change stand.

**The one about a race I caused.** Recovery check-ins are sent by a scheduled
tick. The code read the day's slot, then translated, then sent, then wrote the
slot. Between the read and the write sit a model call and a provider call — and a
second caller reading that same empty slot sends the same message. It happened
on the demo handset: asked how she was feeling, twice, in one minute.

The slot is now claimed atomically *before* the send, so of two callers exactly
one proceeds. The general shape — check, do slow thing, write — is one I'll be
looking for everywhere now.

---

### What isn't real, stated plainly

- **The insurer's adjudicator is simulated.** Deterministic local rules, labelled
  as such on every receipt and every API response, not just in this post.
- **Payments run on a real provider in test mode.** A real Razorpay link, a real
  API call, a real webhook, test money. No mode here moves real money, and the
  last step is deliberately not automated: a payment link exists to ask a person
  to pay, so somebody opens it. Debiting her account unattended would need UPI
  Autopay, and NPCI caps a mandate debit without re-authentication anyway.
- **WhatsApp is real, and limited.** Messages go over Twilio to actual handsets.
  There are no approved message templates, so it can only send freeform inside
  the 24-hour customer service window — a real constraint, not a simulation.
- **Hospital data is half real.** Locations are verified against Google Places
  with a `place_id` and a verification date on every record, so distances are
  genuine. Capability and insurer empanelment are a dated seed and say so on
  every triage call — no feed publishes them.
- **All demo data is synthetic**, and the clinical views say so on screen,
  because a screenshot outlives a demo.

Anbu Care also **doesn't watch anyone.** No sensors, no passive monitoring. An
episode begins because a signal *arrives* — a hospital intake desk, a family
form, a neighbour. The receipt says `received from an external channel, not
detected by Anbu Care`, and the tests reject the words "detect", "notice",
"sense" and "monitor" anywhere in that path unless they follow a negation.

I wanted to add Gemma as a second model to normalise messy intake text. It isn't
available as a managed endpoint on Vertex for this project — all three variants
return 404 on `generateContent` — and serving it would have meant a GPU-backed
deployment billed by the hour for a component that, by design, could never change
a decision. So it is future work, not a feature, and the precheck evidence is in
the repo.

---

### The thing I'd defend hardest

The system remembers one class of fact between admissions: whether she answers by
voice note or by typing. It's in a Vertex AI Agent Engine Memory Bank, and it's
the kind of thing a form never captures and that resets every time a case closes.
A woman who has never typed a message in her life should not be asked to reply
with a number, and the system should know that on day one of the next admission
rather than learning it again from her silence.

There is **no free-text path into that store.** Each kind of memory has its own
function, composing its own sentence from a value validated first. A caller can't
put a symptom in it because a caller can't put a *sentence* in it. Recall is an
exact scope lookup, never a similarity search, so an unrelated memory can't
surface because it read as close enough.

That is the whole philosophy in one small module: decide what may be true, in
code, and then the interesting failures become impossible rather than unlikely.

---

### Try to break it

**1,174 tests**, all green, none needing GCP or a model to run.

```bash
URL=https://anbu-care-37j4eofpwq-el.a.run.app
PARENT=$(curl -sX POST $URL/api/demo/seed | jq -r .parent_id)
CASE=$(curl -sX POST $URL/api/intake -H 'content-type: application/json' \
  -d "{\"parent_id\":\"$PARENT\",\"symptoms\":[\"chest pain\"],\"reported_by\":\"you\"}" | jq -r .case_id)
curl -s $URL/api/cases/$CASE/verify | jq
```

Dashboard: `/app`. Health, including which of the above is running:
`/api/healthz`. Repo and disclosure: (link).

*Anbu (அன்பு) is Tamil for love.*

#AllThingsAgenticHackathon

---

## Pre-publish checklist

- [ ] Post is **public**, not unlisted.
- [ ] The line *"Created for the All Things Agentic Hackathon"* is present, near
      the top.
- [ ] **#AllThingsAgenticHackathon** is on the post.
- [ ] `make test` — confirm the count still reads 1,174 and update if not.
- [ ] `curl -s $URL/api/healthz` — confirm `tpa_mode`, `whatsapp_mode` and
      `memory_bank` still match the "what isn't real" section.
- [ ] Repo link resolves and `DISCLOSURE.md` is reachable from it.
- [ ] Seed a fresh case before posting so any linked case ids verify.
- [ ] **No claim that Gemma is integrated.** The paragraph above says plainly
      that it is not, which is the only safe way to mention it.
