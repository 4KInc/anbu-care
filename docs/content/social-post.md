# Social post draft

**Status:** draft for the user to publish. Not published by Claude.
**Bonus requirements:** the post must carry **#AllThingsAgenticHackathon** and
must say explicitly that it was created for the hackathon.

> **PUBLISH ONE OF THESE, NOT ALL THREE.**
> Recommended: the **X thread**. It carries the most checkable claims, and the
> hackathon audience is on X. LinkedIn is the better choice if the intended
> reader is a hiring manager rather than a judge. The single post is the
> fallback for when there is no time to thread.

Every post in the thread is **under 280 characters counted raw**. X counts any
URL as 23 characters regardless of length, so the real headroom is larger than
these drafts assume — verify in the composer before posting anyway.

**Last fact-check:** 30 Aug 2026, against revision `anbu-care-00196-wd4`.
Every number below was read off the deployed service or the test suite that day.

---

## ① X / Twitter — thread — RECOMMENDED

**1/**
My parents live in Thoothukudi. I don't.

Every NRI has the same 3am fear: something happens, and by the time you hear, the
decisions that mattered are already made.

I built an agent for that. Live, and auditable by you.

#AllThingsAgenticHackathon

**2/**
The design rule I kept coming back to:

**any step that needs me to act is a design failure.**

I'm asleep, nine and a half time zones away. That IS the problem. So every
feature got tested with me unreachable.

**3/**
She sends one voice note in Tamil. Nobody presses anything after that.

15 seconds later:
· her son is told
· the neighbour has a scoped bedside link
· cashless cover is FILED against her policy
· a one-hour clock has started

**4/**
That clock isn't decorative.

IRDAI's Master Circular (IRDAI/HLT/CIR/PRO/84/5/2024) gives an insurer one hour
to decide a cashless request.

When it lapses, the family is told what they're owed — and told in the same
breath that we've filed nothing and can compel no one.

**5/**
Cloud Run holds no timer, so that hour is a real Cloud Scheduler job ticking
every minute.

That's the difference between an agent and a demo. It keeps running when the
browser is closed and nobody is watching.

**6/**
Then it books a clinic.

A doctor dictates a test in Tamil. Nobody picks a lab. It searches real
Thoothukudi centres and drives each one's booking site in a real browser: read
the form, map the fields, fill, read back, submit, screenshot.

7 of 8 can't take a submission. 1 can.

**7/**
Two details I'd defend in review:

It records `requested`, not `confirmed` — a callback form can't truthfully
produce more.

And it captures the cancellation path BEFORE committing. An agent that can create
an obligation and can't undo it is worse than one that does nothing.

**8/**
The guardrails are code, not prompts.

Input: a neighbour says *"she says it's probably just gas."*
Severity still returns HIGH.

The thing that decides severity is a Python dict. It never reads that sentence
as permission.

**9/**
Clinical detail can't go over WhatsApp — India's DPDP Act.

The gate classifies the *content*, not what the caller claims it is. A message
labelled "just logistics" carrying a troponin value is blocked anyway.

Then I bypass the agent and call send() directly. Still blocked.

**10/**
That second half is the whole claim.

An agent that is merely *told* not to leak a lab value is not a control.

**11/**
My favourite refusal.

A lab report arrives and closes the test it belongs to. But if TWO tests are
outstanding, it closes neither.

Attributing it means reading it to decide which — a model choosing which clinical
order was carried out. So it stops and says so.

**12/**
Two access models, both server-enforced:

  /api/parents/{id}      → 401
  /api/cases/{id}/verify → 200

Verification proves the record wasn't altered *without revealing what it says*.
That's exactly why it's open to everyone.

Public where it proves. Private where it reveals.

**13/**
Watch it catch an edit.

Rewrite a receipt straight in Firestore, leaving hash + signature untouched —
severity HIGH → LOW.

Ask the public endpoint:

```
verified: false
broken_at_seq: 1
reason: payload does not hash to the recorded hash
```

It names the receipt.

**14/**
Caveat I put in my own demo script, because leaving it out would be a lie:

an empty chain is a valid chain. A deleted case returns verified:true,
receipt_count:0.

`verified` alone proves the absence of tampering in nothing at all. The count is
what ties it to the story.

**15/**
Worst bug I shipped and caught:

The agent said *"successfully ingested into her health record."*
Documents actually stored: **zero**.

For a system selling a verifiable record, that's the one bug that discredits
everything else.

**16/**
Second worst, and more instructive:

The reader emits `lab_report`. The record stores `blood_report`. My new guard
compared the stored word.

Nothing raises. The guard just never matches.

18 unit tests passed — they all called the function directly and handed it the
right word.

**17/**
The fix wasn't the one-word change. It was writing two tests that go through the
real path end to end, and *confirming they fail first*.

A test that can't fail for the reason you care about isn't covering that reason.

**18/**
What isn't real, said plainly:

· insurer adjudicator SIMULATED
· payments: real Razorpay, test mode, no real money
· WhatsApp REAL (Twilio), freeform-only
· hospital locations real via Places; capability a dated seed
· all data synthetic

Labelled in the product, not just here.

**19/**
It also doesn't watch anyone. No sensors, no monitoring.

An episode starts because a signal *arrives*. The receipt literally says
"received from an external channel, not detected by Anbu Care."

The tests reject the word "detect" in that path.

**20/**
Gemini 3.5 Flash · Google ADK · Cloud Run · Firestore · Pub/Sub · Vertex AI
Memory Bank
1,203 tests, none needing GCP or a model to run.

Try to break it 👇
anbu-care-37j4eofpwq-el.a.run.app/app

Created for the All Things Agentic Hackathon
#AllThingsAgenticHackathon

---

## ② LinkedIn — alternate

**Guardrails you can't talk your way past**

My parents live in Thoothukudi. I don't. Every NRI I know has the same fear:
something happens, and by the time you hear about it, the decisions that mattered
have already been made.

Today that role is filled by a person — a family friend, a paid proxy, a sibling
WhatsApp thread. Every existing service answers it the same way: a human
coordinator.

I spent the submission window asking whether an agent could take that role, and
what would have to be true before I'd trust it with my mother.

Created for the All Things Agentic Hackathon.

The rule I kept returning to: any step that needs me to act is a design failure.
I am asleep, nine and a half time zones away — that is the problem, not a detail
of it.

So: she sends one voice note in Tamil, and inside fifteen seconds her son is
told, the neighbour has a scoped bedside link, a cashless pre-authorisation is
filed against her policy, and a one-hour regulatory clock starts. IRDAI's Master
Circular gives an insurer one hour to decide a cashless request. When that hour
lapses, the family is told what they are owed — and told in the same breath that
Anbu Care has filed nothing and can compel no one.

Then a doctor dictates a test at the bedside, in Tamil, and the system books it:
it searches real diagnostic centres, ranks them against a mandate, and drives
each centre's own booking site in a real browser. It records the result as
"requested", not "confirmed", because an unauthenticated callback form cannot
truthfully produce anything stronger. And it captures the cancellation path
before it commits — an agent that can create an obligation and cannot undo it is
worse than one that does nothing.

The answer wasn't more agents. It was deciding what they are not allowed to do,
and putting those things in code where no prompt can reach them:

→ A red-flag symptom escalates. The demo input is a neighbour saying "she says
it's probably just gas." Severity still returns HIGH.

→ Clinical detail never leaves over WhatsApp. India's DPDP Act makes that a legal
line. The gate inspects the content, not the caller's claim about it — and I demo
it by bypassing the agent entirely and calling the send function directly. Still
blocked.

→ A decision can't be silently rewritten. Every action appends a signed receipt
whose hash covers the previous one.

The design decision I'm most pleased with came from a question I couldn't answer
cleanly at first. If clinical data can't go over WhatsApp because it "lives
somewhere protected" — what is protecting it? If the answer were "a URL nobody
guesses", the argument would be hollow.

So verification is open to everyone and needs no credential, because it proves
the record wasn't altered without revealing what it says. Everything returning
content is credentialed. Public where it proves; private where it reveals.

The bug worth admitting: on one run the agent told me it had "successfully
ingested" a lab report into the record. Documents actually stored: zero. For a
system whose entire pitch is a verifiable record, that is the failure that
discredits everything else. The fix wasn't better prompt wording — the demo now
prints the stored count read back from the service, right next to what the agent
claimed.

What isn't real is labelled in the product, not buried in a README: the insurer's
adjudicator is simulated, payments run on a real provider in test mode and move
no real money, WhatsApp is real but freeform-only inside the 24-hour window,
hospital locations are verified against Google Places while capability remains a
dated seed, and all demo data is synthetic.

Built on Gemini 3.5 Flash, Google ADK, Cloud Run, Firestore, Pub/Sub and Vertex
AI Memory Bank. 1,203 tests, none of which need cloud access to run.

It's live and you can audit it without asking me:
https://anbu-care-37j4eofpwq-el.a.run.app/app

Anbu (அன்பு) is Tamil for love.

#AllThingsAgenticHackathon

---

## ③ X / Twitter — single post — fallback

One Tamil voice note → her son is told, cashless cover is filed, and a 1-hour
IRDAI clock starts. Nobody pressed anything.

Guardrails in code, not prompts.

Created for the All Things Agentic Hackathon
anbu-care-37j4eofpwq-el.a.run.app/app
#AllThingsAgenticHackathon


---

## Pre-publish checklist

- [ ] **Only one** of the three above is published.
- [ ] It carries **#AllThingsAgenticHackathon**.
- [ ] It carries the line *"Created for the All Things Agentic Hackathon"*.
- [ ] `make test` — confirm the count still reads 1,203 and update if not.
- [ ] `curl -s $URL/api/healthz` — confirm `tpa_mode`, `whatsapp_mode` and
      `memory_bank` still match the "what isn't real" lines.
- [ ] Live URL still serving, and `/app` loads.
- [ ] Seed a fresh case before posting so any linked case ids verify.
- [ ] **No claim about Gemma or a second model.** It is precheck-negative future
      work and appears in the blog post only as a thing that was *not* built.
- [ ] Blog post published first, so the social post can link to it.
