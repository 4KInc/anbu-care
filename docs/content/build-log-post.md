# Build log draft — public blog post

**Status:** draft for the user to publish. Not published by Claude.
**Required for the bonus:** the post must be public (not unlisted) and must say
explicitly that it was built for the hackathon. That line is the first line below.

---

## Guardrails you can't talk your way past: building Anbu Care

*Built for the All Things Agentic Hackathon (Google / Devpost).*

My parents live in Thoothukudi. I don't. Every NRI I know has the same 3 a.m.
fear: something happens, and by the time you hear about it, the decisions that
mattered have already been made by whoever was standing there.

The existing answer to that is a person — a family friend, a paid proxy, a
sibling WhatsApp thread. Sahaayak, Samarth Care, Care247, Policybazaar's NRI
Care Program: all of them are human-coordinator models. I wanted to know whether
an agent could take that role, and what would have to be true before I'd let it.

Anbu Care is the answer I built in the submission window. It's live, and you can
check its work without asking me: **https://anbu-care-37j4eofpwq-el.a.run.app**

The interesting parts weren't the agents. They were the four times I had to stop
and decide what the system was *not allowed* to do.

---

### 1. The guardrails that matter are code, not prompts

Anbu Care has five agents on Gemini 3.5 Flash and Google's ADK: onboarding,
triage, evidence, insurer liaison, and WhatsApp comms, under a coordinator.
Underneath them is a layer that no agent can reach past.

Three things have to hold on *every* run — including the run where the model is
confused, or the caller is reassuring, or someone is actively trying to talk
around them:

**A red-flag symptom escalates.** The severity table is a Python dict. The demo
input is a neighbour calling to say *"she says it's probably just gas."* Severity
comes back HIGH anyway, because the thing that decides severity never reads that
sentence as permission.

**Clinical detail never leaves over WhatsApp.** India's DPDP Act and Meta's
healthcare policy make this a legal line, not a style preference. So the gate
classifies the *content*, not the caller's claim about it — a message declared
`logistics` that reads *"just logistics: troponin 0.94 ng/mL"* is blocked anyway.
And the blocked attempt is written to the audit trail, because a block is
evidence the boundary held.

The demo does this twice on purpose. First the agent is asked to relay a lab
value and refuses. Then I bypass the agent entirely and call the send function
directly — and it's *still* blocked. That second half is the whole claim. An
agent that is merely *told* not to leak a lab value is not a control.

**A decision can't be silently rewritten.** Every consequential action appends an
Ed25519-signed receipt whose hash covers the previous one.

---

### 2. Public where it proves, private where it reveals

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
says*.** It returns hashes, a boolean, and a failure mode. That's exactly why it
can be open to everyone — and it has to be, because a receipt chain only means
something if you can check it without my permission.

Everything that returns content — the parsed record, the case trail, the arrival
brief — is credentialed.

The demo credential is published in the README, deliberately. Secrecy isn't what
I'm demonstrating. Take the token out of the page; the 401 still happens.

---

### 3. Arithmetic you can check in your head

There's no production TPA API to integrate against in a hackathon window, so the
adjudicator is simulated — and labelled `SIMULATED — deterministic local rules,
not an insurer` in every payload, every receipt, and every sentence the agent
says about it.

But the *math* is real, and I made a rule for myself: if a number goes on screen
next to a visible policy, a judge has to be able to do it in their head and have
it tie out.

The conventional Indian ICU sub-limit is 2% of sum insured per day. On the
synthetic ₹5,00,000 policy that's ₹10,000/day. The stay is 19–22 August, three
days, so ₹30,000 of a ₹96,000 ICU bill is payable and **₹66,000 is not.**

I didn't reverse-engineer that number. The ₹5,00,000 and the ₹96,000 both existed
in the demo seed before this feature did; the percentage is a real convention;
₹66,000 is just where the arithmetic landed. There's a test that recomputes every
step from first principles and asserts the figure *displayed* equals the figure
*computed*, so the narration and the code can't drift apart.

---

### 4. The agent had to do something, not just proceed

Everything above is the system *proceeding*. The moment it became an agent rather
than a pipeline was when something came back that it had to think about.

The adjudicator returns PASS, PARTIAL, QUERY or DENY. QUERY is evaluated *before*
PARTIAL, deliberately — a real adjudicator can't compute a payable figure while a
required document is missing. It asks first and prices second.

Live, unscripted, on the deployed service:

```
insurer_liaison_agent  submit_claim        -> QUERY (missing discharge summary)
                     → onboarding_agent    ← goes and finds it on the record
insurer_liaison_agent  respond_to_query    -> PARTIAL, INR 66,000 disallowed
insurer_liaison_agent  advance_claim_stage, check_claim_sla
```

The family hears about ₹66,000 of exposure from their own coordinator, now —
not from a settlement letter later.

And the negative case matters more than the positive one. I told the agent a
discharge summary was on file when it wasn't. It looked, found nothing, and said
so — instead of inventing a document id. That's the same failure class as the
worst bug I shipped and caught during the build.

---

### The bug worth writing about

Gemini reads a lab report and returns `232` — the JSON *number*. My schema said
the value was a string. Pydantic rejected it, the tool raised, and the request
500'd. Three out of three deployed attempts.

That was the loud half. The quiet half was worse.

On a run where the tool was never called at all, the agent told the user:
*"I have successfully read your mother's lab report and ingested it into her
health record."* Documents actually stored: **zero**.

For a system whose entire pitch is a verifiable record, an agent asserting a
write that never happened is the one bug that discredits everything else. Prompt
wording alone wasn't going to fix it — so now the demo prints the stored-document
count *read back from the service*, next to what the agent claimed:

```
GROUND TRUTH — documents actually stored for this parent: 2
reported status 'ingested' vs stored count 2: consistent
```

If those ever disagree, it says `CONTRADICTED` on screen.

I applied the same discipline to the arrival brief — the one artifact a family
reads at their most frightened moment, and the place a synthesis is most tempted
to be helpful. It's composed in code from the signed chain, every line carries
the receipt it came from, and anything the state doesn't contain comes back as
*"not yet known"* with the reason. Asked point-blank *"when will she be
discharged and what will this cost me?"*, with no discharge date and no
adjudication on file, it answers "not yet known" to both.

While testing it I found a subtler version of the same bug: a queried claim has
`total_disallowed_inr: 0` — because nothing has been *priced* yet. The brief was
rendering that as "₹0 so far". Traced to a real field, and still a lie: false
reassurance about money. Now unknown until something is actually priced.

---

### What isn't real, stated plainly

The insurer response is simulated. The hospital knowledge base is a dated seeded
snapshot, not a live capability feed — and that label is returned on *every*
triage call, not added for the demo. WhatsApp sends go to the sandbox. Every
figure in the pitch deck is flagged unverified until sourced. All demo data is
synthetic and the clinical views say so on screen, because a screenshot outlives
a demo.

Anbu Care also doesn't watch anyone. It has no sensors and no passive monitoring.
An episode begins because a signal *arrives* — a hospital intake desk, a family
form, a neighbour. The receipt says `received from an external channel, not
detected by Anbu Care`, and the tests reject the words "detect", "notice",
"sense" and "monitor" anywhere in that path unless they follow a negation.

I wanted to add Gemma as a second model to normalise messy intake text. It isn't
available as a managed endpoint on Vertex for this project — all three variants
404 — and serving it would have meant a GPU-backed deployment billed by the hour
for a component that, by design, could never change a decision. So it's future
work, and the precheck evidence is in the repo.

---

### Try to break it

167 tests, all green, none of them needing GCP or a model to run.

```bash
URL=https://anbu-care-37j4eofpwq-el.a.run.app
PARENT=$(curl -sX POST $URL/api/demo/seed | jq -r .parent_id)
CASE=$(curl -sX POST $URL/api/intake -H 'content-type: application/json' \
  -d "{\"parent_id\":\"$PARENT\",\"symptoms\":[\"chest pain\"],\"reported_by\":\"you\"}" | jq -r .case_id)
curl -s $URL/api/cases/$CASE/verify | jq
```

Dashboard: `/app`. Repo and disclosure: (link).

*Anbu (அன்பு) is Tamil for love.*
