# Social post drafts

**Status:** drafts for the user to publish. Not published by Claude.
**Required for the bonus:** must carry **#AllThingsAgenticHackathon**.

Every claim below is checkable against the deployed service. Nothing here
implies a capability that isn't live.

---

## X / Twitter — thread (primary)

**1/**
My parents live in Thoothukudi. I don't.

Every NRI has the same 3am fear: something happens, and by the time you hear,
the decisions that mattered are already made.

I built an agent for that. It's live, and you can audit it without asking me.

#AllThingsAgenticHackathon

**2/**
The interesting part wasn't the agents.

It was the three things I decided they're *not allowed* to do — and enforcing
those in code rather than in a prompt.

**3/**
Demo input: a neighbour calls and says *"she says it's probably just gas."*

Severity still comes back HIGH.

The thing that decides severity is a Python dict. It never reads that sentence
as permission.

**4/**
Clinical detail can't go over WhatsApp (DPDP + Meta health policy).

So the gate classifies the *content*, not what the caller claims it is. A message
labelled "just logistics" carrying a troponin value is blocked anyway.

Then I bypass the agent entirely and call send() directly. Still blocked.

**5/**
That second half is the whole claim.

An agent that is merely *told* not to leak a lab value is not a control.

**6/**
Two access models, both server-enforced:

  /api/parents/{id}      → 401
  /api/cases/{id}/verify → 200

Verification proves the record wasn't altered *without revealing what it says*.
That's exactly why it's open to everyone.

Public where it proves. Private where it reveals.

**7/**
The claim comes back QUERIED — missing discharge summary.

The agent goes and finds it on the record, resubmits, gets PARTIAL back, and
tells the family ₹66,000 won't be covered.

Now. Not in a settlement letter three weeks later.

**8/**
That ₹66,000 isn't staged.

ICU sub-limit is 2%/day of sum insured. ₹5,00,000 policy → ₹10,000/day. 3-day
stay → ₹30,000 payable on a ₹96,000 bill.

A test recomputes it from first principles and asserts the number *shown* equals
the number *computed*.

**9/**
Worst bug I shipped and caught:

The agent said *"successfully ingested into her health record."*
Documents actually stored: **zero**.

For a system selling a verifiable record, that's the one bug that discredits
everything else.

**10/**
The fix isn't better prompt wording.

The demo now prints the stored count read back from the service, next to what the
agent claimed. If they disagree it says CONTRADICTED, on screen.

**11/**
What isn't real, said plainly:

· insurer response is SIMULATED
· hospital KB is a dated seeded snapshot
· WhatsApp is sandbox
· all data synthetic

Every one of those is labelled in the product, not just the README.

**12/**
It also doesn't watch anyone. No sensors, no monitoring.

An episode starts because a signal *arrives*. The receipt literally says
"received from an external channel, not detected by Anbu Care."

The tests reject the word "detect" in that path.

**13/**
Gemini 3.5 Flash · Google ADK · Cloud Run · Firestore · Pub/Sub
1144 tests, none needing GCP or a model to run.

Try to break it 👇
anbu-care-37j4eofpwq-el.a.run.app/app

#AllThingsAgenticHackathon

---

## X / Twitter — single post (fallback)

Built an agent that coordinates eldercare for my parents in India.

Told it "she says it's probably just gas" → still returns HIGH.
Told it to WhatsApp a troponin value → blocked, even with the agent bypassed.

Guardrails that matter are code, not prompts.

Live + auditable, no login:
anbu-care-37j4eofpwq-el.a.run.app/app

#AllThingsAgenticHackathon

---

## LinkedIn

**Guardrails you can't talk your way past**

My parents live in Thoothukudi. I don't. Every NRI I know has the same fear:
something happens, and by the time you hear about it, the decisions that mattered
have already been made.

Today that role is filled by a person — a family friend, a paid proxy, a sibling
WhatsApp thread. Every existing service answers it the same way: a human
coordinator.

I spent the submission window asking whether an agent could take that role, and
what would have to be true before I'd trust it with my mother.

The answer wasn't more agents. It was deciding what they're not allowed to do,
and putting those three things in code where no prompt can reach them:

→ A red-flag symptom escalates. The demo input is a neighbour saying "she says
it's probably just gas." Severity still returns HIGH.

→ Clinical detail never leaves over WhatsApp. India's DPDP Act makes that a legal
line. The gate inspects the content, not the caller's claim about it — and I
demo it by bypassing the agent entirely and calling the send function directly.
Still blocked.

→ A decision can't be silently rewritten. Every action appends a signed receipt
whose hash covers the previous one.

The design decision I'm most pleased with came from a question I couldn't answer
cleanly at first. If clinical data can't go over WhatsApp because it "lives
somewhere protected" — what is protecting it? If the answer were "a URL nobody
guesses", the argument would be hollow.

So: verification is open to everyone and requires no credential, because it
proves the record wasn't altered *without revealing what it says*. Everything
that returns content is credentialed. Public where it proves; private where it
reveals.

The bug worth admitting: on one run the agent told me it had "successfully
ingested" a lab report into the record. Documents actually stored: zero. For a
system whose entire pitch is a verifiable record, that is the one failure that
discredits everything else. The fix wasn't better prompt wording — the demo now
prints the stored count read back from the service, right next to what the agent
claimed.

What isn't real is labelled in the product, not buried in a README: the insurer
response is simulated, the hospital knowledge base is a dated snapshot, WhatsApp
is sandboxed, and every figure is flagged until sourced.

Built on Gemini 3.5 Flash, Google ADK, Cloud Run, Firestore and Pub/Sub.
1144 tests, none of which need cloud access to run.

It's live and you can audit it without asking me:
https://anbu-care-37j4eofpwq-el.a.run.app/app

Anbu (அன்பு) is Tamil for love.

#AllThingsAgenticHackathon

---

## Pre-publish checklist

- [ ] Blog post is **public**, not unlisted (bonus requires public).
- [ ] The literal line "Built for the All Things Agentic Hackathon" appears.
- [ ] Social post carries **#AllThingsAgenticHackathon**.
- [ ] Repo link resolves and DISCLOSURE.md is reachable from it.
- [ ] Live URL still serving — `curl -s $URL/api/healthz`.
- [ ] Seed a fresh case before posting so the linked case ids verify.
- [ ] **No claim about Gemma or a second model** — it is precheck-negative
      future work. Bonus target is **+0.4 from the two content items**.
