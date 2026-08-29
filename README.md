# Anbu Care

Autonomous eldercare and insurance coordination for NRI families — a multi-agent
system that coordinates healthcare and insurance for aging parents in India on
behalf of their adult children abroad.

Built for the **All Things Agentic Hackathon** (Google / Devpost), Taskmaster track.

### Architecture at a glance

[![Anbu Care architecture](docs/architecture.png)](docs/architecture.png)

*Agents propose, guards decide, the chain records.* The deterministic guard band
is drawn as its own layer because that separation **is** the architecture:
Gemini contributes terms and transcripts and never a verdict, every action
passes through code the model cannot reach or widen, and one receipt per action
lands on a chain anyone can verify without a credential. Source:
[`docs/architecture.mmd`](docs/architecture.mmd) · rendered
[SVG](docs/architecture.svg) · [PNG](docs/architecture.png).

### 🟢 Live demo — no login required

**https://anbu-care-37j4eofpwq-el.a.run.app**

```bash
URL=https://anbu-care-37j4eofpwq-el.a.run.app
PARENT=$(curl -sX POST $URL/api/demo/seed | jq -r .parent_id)
CASE=$(curl -sX POST $URL/api/intake -H 'content-type: application/json' \
  -d "{\"parent_id\":\"$PARENT\",\"symptoms\":[\"chest pain\"],\"reported_by\":\"judge\"}" | jq -r .case_id)
curl -s $URL/api/cases/$CASE/verify | jq        # verify the signed chain yourself
```

**Family dashboard:** [`/app`](https://anbu-care-37j4eofpwq-el.a.run.app/app) ·
**agent UI:** [`/dev-ui/`](https://anbu-care-37j4eofpwq-el.a.run.app/dev-ui/)

Two access models, both enforced server-side — this contrast is deliberate:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/parents/{parent_id}     # 401 — clinical content
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/cases/{case_id}/verify  # 200 — open to everyone
```

Verification proves the record was not altered *without revealing what it says*,
which is why it needs no credential. Anything returning case or patient content
requires a credential. Two are accepted:

- **The demo credential**, `anbu-demo-family-token`, published deliberately:
  secrecy is not what is being demonstrated, server-side enforcement is. Take
  the token out of the page and the 401 still happens.
- **A Google account**, verified against Google's keys — and then checked
  against this parent's family contacts, which is the half that matters. A
  verified stranger gets **403**, not a record.

A signed link sent to a consented family member is a third, narrower one: it
opens the case it was minted for and cannot mint further links for anyone else.

All demo data is synthetic. Liveness probe is `/api/healthz`.

> *"If something happens to my parent right now, who makes sure the right decisions
> get made, fast, and that I actually know what's happening?"*

Today that role is a family friend, a paid proxy, or a frantic sibling WhatsApp
thread. Every existing service — Sahaayak, Samarth Care, Care247, Policybazaar's
NRI Care Program — answers it with a human coordinator. Anbu Care answers it with
an agentic decision layer that knows the parent's history, the local hospital
landscape, and the insurance policy, and acts within minutes.

---

## What is real, and what is not

This matters more than any feature list, so it comes first.

**Real and demonstrable:**

- Multimodal document parsing into structured, queryable observations, with
  "new and abnormal" distinguished from "consistent with known baseline".
- Severity classification and hospital routing, including the explanation of
  what was traded for extra travel distance.
- The WhatsApp compliance boundary — enforced in code, before send.
- Claim packet assembly, coverage and sub-limit checks, and STEP_UP evidence
  scoring.
- SLA tracking against the regulatory cashless and reimbursement windows, run
  against real wall time. The window lengths are implemented as real deadlines
  in `anbu_care/service.py` and are now **verified** against the source:
  IRDAI/HLT/CIR/PRO/84/5/2024, 29 May 2024, the Master Circular on Health
  Insurance Business — one hour for cashless authorisation, three hours for
  final discharge authorisation, thirty days for reimbursement settlement, with
  interest at two percent above the bank rate on delay. See
  [`docs/CITATIONS.md`](docs/CITATIONS.md).
- **Cashless pre-authorisation at admission, filed without being asked.** The
  escalation that opens a case also files it, the same simulated adjudicator
  answers, the one-hour clock starts on real wall time, and a scheduler records
  the breach if the hour lapses without a decision. Requested is not
  authorised, and authorised is not settled: cashless means the insurer pays
  the hospital, which Anbu Care never does and never claims. On a breach it
  states what the policyholder is entitled to and nothing more — it has not
  filed a grievance, cannot compel anyone, and does not claim it will be won.
- The signed, tamper-evident receipt chain, and the verification that detects
  a silent edit.
- **Wellbeing check-in over inbound WhatsApp**, including voice notes. A voice
  note is stored, transcribed by Gemini in a single call, and read for symptom
  terms — and the audio, not the transcript, is treated as the record. Alerts
  say "we heard", never "she said". Multilingual and verified from a real
  handset in Tamil script, transliterated Tamil, Hindi and code-mixed English.
- **Escalation and care-circle notification.** A recognised red flag opens a
  case, runs triage, alerts the family, and can ring a care-circle contact. It
  also **hands the treating team a link without anybody being asked**: a
  write-scoped emergency-access link is minted at escalation and sent to the
  people who are physically with her, so the neighbour who reaches the hospital
  has something to show a doctor. Before this the link only existed if the son
  minted one from the dashboard, which meant the person the whole system stands
  in for had to wake up and copy a URL. The son is told; he is not the courier,
  and on an escalation he is skipped for the link entirely because he was
  already alerted as family.

  The seeded family now includes **the neighbour**, Meena, who holds
  `outbound_notify` and nothing else. Without her the care circle was the son,
  so every workflow reaching for "whoever is with her" reached for a man in
  Nashville: the mechanism was right and the data made it a no-op, which is the
  worst kind of wrong because nothing looks broken. She can be told to go round
  and handed a link to show a doctor. She cannot read the record, cannot
  authorise a payment, and receives no clinical detail.
- **Per-purpose consent, read live.** Seven purposes across four directions —
  what a family member may be SENT, what they may SEND IN, what may be SHOWN to
  a third party, and what may be sent TO THE PARENT herself — deliberately
  disjoint. Conflating two of them was a real shipped defect, since fixed and
  regression-tested; the fourth direction exists so recovery check-ins could
  not repeat it by borrowing an agreement somebody else made.
- **Outbound Tamil, as translation and never as authorship.** Messages to a
  recipient whose language preference is Tamil are rendered from the recorded
  English by Gemini, which stays the source of truth, and carry the line
  "translated from the recorded <bill / check-in question / status update>".
  Per-recipient, so the mother reads Tamil while her son keeps English — and a
  preference may name both, `en+ta`, which sends the English record with the
  Tamil beneath it for a reader who has both. The preference lives on the
  person, not the role: a daughter in Thoothukudi is a family contact too, and
  a message twice as long is not a kindness to her. A
  translation with no source record is **refused** — there is no code path that
  produces Tamil for text nobody wrote down first. The gate rules on the
  English *before* anything is rendered, because `CLINICAL_PATTERNS` cannot read
  Tamil script, and any failure falls back to the recorded English with a note
  saying plainly that it could not be rendered. It never guesses.
- **Recovery check-ins — the son who keeps caring after the emergency.** A
  recorded discharge summary opens a fourteen-day window, and one question a
  day goes to her at 09:00 her time: how are you feeling, did you take today's
  medicines, any new discomfort. In Tamil, through the same gated path as every
  other message. Her reply comes back through the **unmodified** W1 inbound
  path — same signature check, same transcription, same store — labelled
  `phase=recovery` from stored state alone, never from reading her words. The
  window ends by the calendar, not because anything decided she was better, and
  consent is read live at every tick so a withdrawal or a STOP stops it on the
  next message. It asks and records; it never advises. A concerning reply gets
  the **full existing escalation** — case opened, the same deterministic table,
  the family told — with an alert that says "we heard X" and names no cause.
- **Google Places verification of the hospital knowledge base**, and a real map
  of the routing decision. Verification caught a genuine bug: the previously
  seeded coordinates were out by 1.4 to 5.0 km, which changed which hospital was
  nearest and therefore what the routing explanation claimed.
- **An emergency clinical summary for the treating team**, reachable by a
  clinician with no login through a scoped link the family mints and can revoke.
  Allergies first, every line traced to the stored field it came from, and no
  field anywhere an instruction could live in — this is the record, not the
  doctor.
- **A clinician's update, not just their orders.** A treating team says "she is
  stable, chest pain has settled" far more often than they order anything, and
  that used to be answered with "could not tell which test was being ordered"
  and thrown away. A clinician message is a **note first** and an order second,
  and a test rides on the same note when one is actually heard.

  The words are kept **behind the case credential** now. They never were: the
  chain carried a hash and nothing kept the text, so a note was write-only and
  the family could see one had been left without ever reading it. The chain is
  unchanged and still carries only the hash, because `/verify` is public.

  And the family reads it in their own language. A Tamil note shows the English
  above and the clinician's own words underneath, labelled as translated from
  them, the same way a Tamil test label does.

  **The message now carries the sentence.** It used to say only that an update
  existed, on the reasoning that clinical detail has no business on WhatsApp.
  The reasoning was sound and the result was not: a son eleven time zones away
  got "an update was left, go and read it" at 4am, which is a notification
  about a notification — and the sentence is the only part that tells him
  whether to get on a plane.

  So the words go, under an exception the gate knows **by name** rather than by
  weakening the rule. `CLINICAL_EXCEPTIONS` is one template wide, and every
  part of it is load-bearing: the treating team's own words, one direction,
  about the reader's own parent, on a case they hold a credential for, to a
  contact who consented to status updates — and **recorded as an exception** on
  the receipt, so a message carrying clinical detail is never indistinguishable
  from one that did not. Any other message with the same words is still
  refused, whatever it declares. The English rendering is what is sent, labelled
  as rendered rather than spoken.

  This is a real trade and it is worth stating plainly: Meta's healthcare policy
  and DPDP both restrict health data over WhatsApp, and this crosses that line
  deliberately, narrowly, and on the record.

- **Clinician notes, typed or spoken.** A voice note is transcribed by Gemini
  and shown back for confirmation; **an unconfirmed transcript writes nothing**.
  The receipt carries the hash of the confirmed text, never the words.
- **Bill capture.** A family member photographs a hospital bill over the same
  WhatsApp thread, Gemini reads the line items, and the existing sub-limit rules
  produce an itemised covered / not-covered / you-pay split with a running total
  across the stay. The photograph is kept privately so every number stays
  checkable against the paper it came from.

  Reading takes longer than the webhook window, so the reply is acknowledged
  first and the read runs after it. That cost durability once: a deploy
  replaced the instance nine seconds into a read and the family got "reading it
  now" and then silence, with nothing in Firestore to show it had arrived. The
  photograph is now written down **before** the acknowledgement and the row
  stays open until a message has actually been sent about it, so the next
  instance to start finishes what the last one dropped.
- **Document capture, four kinds beyond bills.** The same WhatsApp thread takes a
  discharge summary, a lab report, a prescription or a policy schedule. One
  Gemini call classifies and extracts together — two calls would let the second
  never disagree with the first — and each kind lands somewhere different: a
  prescription replaces the medication list, a policy schedule sets the
  sub-limits the coverage estimate is computed from, a discharge summary fills
  in the dates, diagnosis, condition and follow-up the arrival brief was
  reporting as "not yet known". The refusals are the interesting part. A
  reading that finds no medication does not empty the list; a policy read with
  no sum insured does not zero the cover; a discharge summary **merges**
  allergies and never removes one, because a shorter list is not a retraction
  of something someone has carried for years.
- **Interim bill payment, bounded by a mandate the family grants.** A hospital
  wants money on day two, before any insurer has decided anything. The family
  authorises a destination, a per-bill cap, a total cap and a window; after that
  a photographed bill is paid without waking anyone, or it is refused and it
  says which check stopped it. Nine deterministic guards run in order —
  `mandate_live`, `within_window`, `case_scope`, `not_duplicate`,
  `per_bill_cap`, `standing_live`, `total_cap`, `payee_from_mandate`,
  `no_anomaly` — and the last is a set of named signals rather than a
  judgement: an amount spike against the running mean, a payee or vendor
  disagreeing with the mandate, spend velocity, sitting just under a cap, or
  arriving in the last tenth of the window.

  **The authority is granted before the admission exists.** Scoped to one
  admission it put the son back in the loop at the exact moment he cannot be in
  it: a case opens at 3am in Thoothukudi while he is asleep in Nashville, and
  until he wakes and grants something a bill cannot be paid — the system meant
  to stand in for him waiting on him. Deciding how much may be spent is his
  job. Being conscious when an ambulance arrives is not.

  So a **standing** mandate lives on the parent and each case *adopts* it as it
  opens, writing a `mandate.standing_applied` receipt that says in as many
  words that nobody authorised anything for this admission. A case whose bills
  were paid under an authority its own record never mentions would be
  unauditable.

  One rule makes that safe, and it is the whole of the design: **the total cap
  is a ceiling across every case the grant covers, not a fresh allowance for
  each.** Copying the cap onto each new admission turns one signature into as
  many as there are admissions — INR 400,000 authorised, INR 1,200,000 gone
  across three, every individual decision looking correct. The enforcer counts
  spend across all adopting cases, and the burst window is widened the same way
  for the same reason.

  Two further guards, both about revocation meaning revocation. An adopted copy
  can outlive the grant it came from, so `standing_live` re-checks the grant on
  **every** decision — revoke once and admissions already carrying it stop too.
  And declining it on one admission is recorded, because otherwise the next
  question re-adopts the grant it was just told to stop. An explicit per-case
  grant still wins: a narrower, later human act, and the way to cap a single
  admission without withdrawing the arrangement.

  **The family is told what happened to the money.** The bill message says the
  payment was sent and "is not confirmed as settled yet" — a promise of a
  second message, and for a long time there was none: the rail confirmed, a
  receipt was written, and the person whose money it was found out by opening a
  dashboard if they thought to. Settled, failed and wrong-amount now each send,
  from the webhook handler where the news actually arrives. The failure matters
  most: nobody has to act on money that arrived.

  **A bill can never set where money goes.** The destination comes from the
  mandate, always. A UPI ID printed on a bill is read as *evidence* — a bill
  claiming a different one is refused, never followed. That guard was inert
  until bills started carrying an address to check, which is why the synthetic
  bills now print one.

  **A photograph is not an identity.** The same bill photographed twice — a
  retake after a blurry one — is one debt, matched on the bill number the
  hospital printed rather than on the image bytes. Before that it was two
  bills, twice the money owed, and a second payment eligible to go out.

- **Diagnostic referral — what a present son does when a test is ordered.** The
  doctor says she needs a repeat troponin. A son in Nashville would open his
  phone, find the nearest labs, check what he honestly could about coverage,
  and send the list. Anbu Care does that: a clinician records the order through
  the existing confirmed-note path, and a **live Google Places search** near
  the admitting hospital returns real Thoothukudi centres, ranked by the same
  transparent scoring hospital routing uses.

  Three walls, one test each. **The order comes from the clinician** — nothing
  here originates one, and a referral with no order behind it is refused.
  **No coverage promise:** network status is read from the same seeded KB
  routing uses, in that module's own words, "is listed as empanelled with X",
  never "is covered" — there is no coverage field in the model and a test
  asserts the word never appears. **No mobility verdict:** if the clinician did
  not say whether she can travel, both paths are shown and the message says the
  people with her decide. Surfacing options **arranges nothing on its own** —
  booking is a separate lane with its own authority, below.

  The clinician can **speak the order** rather than type it. The dictation is
  transcribed by Gemini and read for which test is being ordered, and that
  reading is a **proposal**: it lands in the field, editable, and only what the
  clinician submits is recorded. Same rule the note path already held, and for
  a sharper reason here — a misheard test written down unchecked sends her for
  the wrong scan, with a receipt saying a clinician ordered it. An unclear
  dictation proposes nothing and says so; two tests in one sentence are
  reported as two rather than silently reduced to the first.

  **It works in Tamil**, which matters because she is in Thoothukudi and so is
  her doctor. Verified end to end against the real model and real Places: a
  Tamil dictation is transcribed in Tamil script, the order is read out of it
  in Tamil, and a Tamil query returns seven local centres. Code-mixed Tanglish
  keeps the English clinical terms the clinician actually used. The words are
  never translated before the search, because translating them would be
  deciding what was ordered.

  The family reads it in theirs. A Tamil order shows the English on top and the
  clinician's own words underneath, labelled as translated from them, because a
  son in Nashville seeing only "ரத்த பரிசோதனை" on his mother's record learns
  nothing. Same wall as outbound Tamil, pointed the other way: the dictation is
  the record, the English is derived from it, a failure shows the dictation
  rather than a guess, and a label already in English costs no model call.

  The `diagnostic.referral` receipt carries counts, place ids and the source
  label, never the test name, so `/verify` stays public and leaks nothing.

- **It books the appointment.** Surfacing options was the half of the job that
  was easy to make safe, and stopping there means the system does the easy half
  and hands a seventy-one year old the hard half at 4am. A present son books
  the appointment. **A real browser drives the centre's own form**, and a real
  booking has been made at a real Thoothukudi clinic.

  This is the first lane where being wrong reaches a **third party who never
  agreed to any of this**. A wrong payment can be refunded; a wrong booking
  wastes a real clinic's slot and sends her across a city under her own name.
  So the wall in the referral module moved rather than disappearing: from "we
  never act" to "we act inside an authority a human granted, bounded, receipted
  and reversible" — which is the payment lane's shape pointed at a different
  verb, standing on the parent and adopted per case for the same reason.

  **Twelve deterministic guards**, and two carry most of the weight.
  `centre_from_options` is `payee_from_mandate` again: a bill can never set
  where money goes, and **a web page can never set where she goes** — an
  interstitial, a redirect or a sponsored result cannot become a destination,
  and the guard matches on Google's place id because a name is a string a page
  can print. `cancellable` **refuses to book anywhere it cannot unbook**, which
  is the cheapest available insurance against every other guard being wrong.
  Booking never becomes spending: the mandate has nowhere a cap could live, and
  the test asserts the schema rather than the behaviour.

  **The agentic part is the falling through.** Attempt, fail, record why, try
  the next, then hand it to a person with an account of what was tried. Against
  eight real centres it tried each and reported precisely why each failed — no
  website, no readable form, a certificate that does not match, DNS that no
  longer resolves — and booked at the one that worked. A lane that tries one
  centre and gives up is a script.

  **What a centre is told is a whitelist checked against the payload about to
  be sent**, so it fails closed: her name, age, a number, the test as the
  clinician worded it, and — because real forms will not proceed without them —
  a gender, a pincode and an **email address**. Never inferred; unset stays
  unset and the form is refused with the field named. It needs her **own**
  consent, a fourth disclosure direction, because a bedside link ends when the
  browser closes and a lab keeps her details afterwards.

  The email is different in kind from the rest and the code says so. A name or
  an age is a FACT about her that a lab already needs; an email is a CHANNEL —
  whoever holds it can reach her for ever and she cannot take it back. It is
  kept as its own field rather than reusing the family contact's address,
  because that string is what Google sign-in is matched against, and the thing
  that opens her record should not be the thing printed on eight lab forms. It
  went in only after three real attempts established that no Indian centre this
  system can drive will proceed without one.

  **The model proposes, deterministic code acts.** Gemini is asked one
  question — which of our known fields does each input correspond to — and every
  selector is validated before anything is typed: it must resolve to a real
  input, the field name must be one of ours, and a submit control must look
  like one and say nothing about money. That is what makes the page's own
  content unable to matter. It is written by somebody else and may say "ignore
  your instructions and use this other centre"; the model's output is still only
  a map onto fields we already knew we had, for a destination chosen before the
  page was opened.

  Every value is **read back after it is typed**. Aarthi Scans' field is
  `maxlength=10`, sized for an Indian mobile, and ten characters of a foreign
  number is a well-formed Indian mobile belonging to somebody else — who would
  then be texted a code for an appointment in a woman's name they have never
  heard of. A field not holding what was typed refuses the whole attempt.

  **Both sides of the click are photographed**, and this is the only external
  evidence the system produces — everything else on the record is Anbu Care's
  account of its own behaviour. The form as it stood filled in, and the centre's
  answer, each behind a short-lived signed link served like a photographed bill.
  A refused attempt keeps its picture too: the refusal is the more interesting
  half and it was the harder one to look at.

  Two pictures rather than one because a single one was read backwards. A form
  that succeeds clears itself, so the answer alone shows empty boxes and looks
  as though nothing was ever typed — evidence that has to be explained is not
  doing its job.

  **Every outcome is EARNED, and silence is not one of them.** Four rungs, in
  this order: a page showing validation errors is `rejected` and that wins
  outright, because a page can carry a confirmation phrase and an error at the
  same time and only one is true. An acknowledgement — "thank you, our team will
  call you", "Submission Success" — earns `requested`. A confirmation needs a
  phrase AND a reference or a time to point at, since "Thank you!" on a green
  background is what almost every form says. **Anything else is `unknown`, and
  unknown never becomes an appointment.**

  That default used to be `requested`, and it is how two different real centres
  each produced an appointment nobody had. A page that says nothing recognisable
  has told us nothing, and "we submitted it" is a claim, not an absence.

  **An emptied form is believed over silence**, and it is the signal that
  survives the language. A form that accepted a submission almost always resets
  itself; one that refused keeps your values so you can fix them. A Tamil or
  Hindi confirmation is a string this code will never have heard of; an empty
  input is an empty input.

  Reading the page at all means waiting for it to finish — network quiet, no
  spinner, and **the submit button enabled again**, which is the one that
  generalises: a form that has taken your click greys the button until it is
  done, whatever it calls its spinner.

  The dashboard pill is amber **"requested, not yet confirmed"**, never green,
  because it would be easy to make a callback request look like a booked
  appointment and it is not one.

  **The one-time code is relayed through the person in the room.** Nearly every
  real slot-booking flow in India sends an OTP, and defeating it is out of scope
  permanently — it is an identity control doing its job. But the code goes to a
  phone a human is holding, so Anbu Care asks the **neighbour** for it: a person
  supplying their own one-time code, which is what the control requires, and the
  care circle rather than the son because he is asleep eleven time zones away.
  The session must stay alive to receive it — an OTP is bound to the session
  that asked, so finishing and resuming later would trigger a fresh code — which
  is why the browser service runs one instance with concurrency above one.

  The inbound branch is the narrowest in the webhook. Digits are a shape
  ordinary messages have — "6" is an answer to how she slept, "104" is a
  temperature — so it fires only while a request is actually outstanding for
  that parent, within minutes, from a resolved sender, on a message that is
  digits and nothing else. Verified live: the same "123456" is a wellbeing
  check-in with no request open and a code with one. The code is never stored,
  logged or receipted; the chain carries that one was asked for and that the
  request was closed, and nothing that would let anybody replay it.

- **The treating team gets a WhatsApp channel, bound by the credential they
  already hold.** "How does Anbu Care know that is the doctor" has two obvious
  answers and both are wrong. You cannot register them in advance, because a
  treating clinician is whoever is on shift when your mother is admitted. And
  you cannot read it off the message, because "Dr Kumar here, she needs an MRI"
  is a sentence anybody can say — if that grants ordering rights on a woman's
  record it grants them to everyone.

  So the answer is the one the handoff link already uses: a **capability**. The
  care circle shows the doctor a QR at the bedside; he opens it, taps once, and
  sends a WhatsApp message carrying a one-time code only that link could have
  produced. The handset is bound by the credential, not by a claim. It is
  scoped to one case, dies with the grant that made it, is revoked in the same
  act as the family's links, and the binding is on the chain. What it
  deliberately does **not** do is identify a human — an order carries "as
  recorded by", never "verified as".

  After that the doctor just talks. A Tamil voice note becomes an attributed
  note on the record with the English derived from it, a test order fans out
  into a live Places search, and `STOP` hands the handset back. On one phone
  that handback is load-bearing: while it is connected as the treating team a
  photograph from it is refused with the reason and the way out, because a
  photo from a handset holding a clinical grant could as easily be a lab report
  as an invoice, and choosing between those is a guess this system does not
  make.

- **The neighbour can hand the system a bill; she cannot read the family's
  money.** Doctors do not photograph bills — the person standing in the
  corridor holding the paper does. Meena held only `outbound_notify`, so she
  could be told things and send nothing: her photograph was dropped with a 204
  and no error anywhere. On a shared handset that was invisible, because the
  index gets to the parent and `resolve_sender` finds the son on the same
  number and uses **his** consent. Give her her own phone and the bill lane was
  dead.

  She holds `inbound_wellbeing` now and deliberately not `billing_updates`. The
  amount, what was claimable and what was refused go to the son's thread, not
  hers. Helping is not the same as being entitled to look — and because the
  sender of a bill is now routinely not the person its outcome is for, the
  acknowledgement stopped promising everybody that the answer would follow in a
  moment. She is told he is being told.

- **Links a frightened person will actually tap.** Every URL carries a signed
  credential, which is what makes it openable without an account and also what
  made it wrap to four lines on a phone — reading at 4am as exactly the sort of
  thing you are told never to tap. The credential has to be there; it does not
  have to be on screen. A twelve-character alias stands in front, minted at the
  point of sending so every template gets it without being changed, and *after*
  rendering, because a translator handed a URL will happily rewrite the
  characters inside a signature.

  It is a bearer token like the URL it hides and is sized for that: sixty bits
  of randomness and never sequential, since anything enumerable turns one
  leaked link into all of them; it **inherits the expiry of the token inside
  it** and may not outlive it; and it only ever wraps this deployment's own base
  URL, because a shortener that redirects anywhere is an open redirect, and one
  on a domain people have been told to trust is worth more to an attacker than
  anything else here. The alphabet drops `0 O 1 l I` — these get read aloud down
  a phone line between Thoothukudi and Nashville.

- **Sign in with Google, with identity kept apart from permission.** The ID
  token is verified server-side against Google's published keys with the client
  id pinned as the audience — a token minted for another application is a valid
  Google token and still not a credential for this one. Authorisation is a
  separate check: the verified address must already be a family contact on the
  parent being read, or the answer is 403. Being a real person is not permission
  to read somebody's mother's lab results. The demo credential still works
  alongside it, so the boundary can be shown without an account.
- **A decision trace**: the chain rendered as the sequence a person can follow —
  the counterparty raising a query, the agent gathering what was asked for, the
  re-adjudication that followed. One step per receipt, so the view cannot
  invent a beat the chain does not contain.

**Simulated, and labelled as such everywhere it appears:**

- **The insurer/TPA response.** No production insurer or TPA API exists to
  integrate against in this window. The liaison agent submits to a simulated
  endpoint. Packet assembly and SLA tracking are real; only the counterparty's
  answer is mocked.
- **Hospital capability and insurer empanelment.**
  `anbu_care/kb/data/hospitals_thoothukudi.json` is split provenance, and only
  half of it is seeded. **Locations are real:** all five hospitals carry a
  Google Places `place_id` and a `location_verified_on` date, so identity,
  coordinates and therefore distance are verified rather than guessed.
  **Capability and empanelment are seeded** and must be spot-checked against
  current listings before any public writeup or recorded narration. Google can
  say where a hospital is; it cannot say who bills which insurer, or which
  centre can run a cath lab at 2am.
- **Booking is DRY BY DEFAULT, and that is a deployment switch.** The browser
  service treats an absent `ANBU_BOOKING_DRYRUN` as on: it navigates, finds the
  form, fills every field, captures the cancellation path and reads the page,
  and does not click submit. These are real clinics, and a deploy that books by
  accident wastes a real appointment for a patient who does not exist. Every
  deploy of that service resets the flag, which turns out to be a good safety
  property. `robots.txt` is consulted, and a site that has said not to has said
  not to.

  **A real booking has been made and photographed**: DLABS Diagnostics in
  Thoothukudi, whose page answered "Submission Success, Thanks for getting in
  touch!" with every field cleared. Both sides of the click are kept — the form
  filled in, and the centre's answer — so the claim rests on that clinic's own
  page rather than on this system's account of itself.

  It took five attempts, and every failed one was this lane claiming something
  it had not verified: a form that rejected the submission on a missing email,
  a page still spinning when it was read, a spinner it did not recognise, and
  an acknowledgement whose wording it had never heard. Each was found by the
  screenshot and not by the code, which is the entire argument for keeping one.

- **No centre found so far can CONFIRM online.** A booking lands as
  `requested` — the centre has the request and has agreed nothing. The
  `confirmed` path is implemented and tested and **has never fired against a
  real centre**, because every centre this system can drive takes a callback and
  nothing more, so the agreement happens in a phone call Anbu Care is not on.
  The card's pill stays amber for exactly that reason.

  Of eight real Thoothukudi centres, **one** can be booked. One has no website,
  three are React applications the driver cannot read, two have hostnames that
  no longer resolve, and one never answers within the wait. Chennai, scouted for
  comparison, is better built and wants MORE — Anderson requires a package
  chosen from its own catalogue, which would be this system deciding which test
  she has. The realistic Indian path is the call bridge, not the form.
  The OTP relay is complete and verified in every part — the parked session, the
  delivery, the narrow inbound branch, one-shot closure — and **the full loop
  has never run end to end**, for the same reason: nothing we can drive has
  asked for a code.

- **WhatsApp delivery.** The gate decision is real and always has been; the
  transport behind it is real code with real credentials. Whether a message
  actually leaves depends on the configured provider, and the system never
  claims a send that did not happen — a permitted-but-undelivered message is
  recorded as `comms.not_delivered`, with `sent_at` left unset.

  Two providers are wired, and **Twilio is the working path.** The account is
  upgraded (`type: Full`), and a freeform `Body` send with no `ContentSid`
  reaches a real handset — verified end to end on 2026-08-22, `status:
  delivered`. This reverses an earlier note in this README: on a Twilio *trial*
  account freeform sends are rejected with `21654 ContentSid Required` while the
  Content API that would satisfy it returns `20003 not available on a Trial
  account`. That catch-22 is real, and upgrading is what resolves it.

  **Meta's Cloud API is implemented but is a dead end on this project.** The
  test sender registers and connects, the token is valid, and sends still fail
  `131030 Recipient phone number not in allowed list` — the only UI for that
  allow-list permanently reports "No phone numbers available for this app",
  reproduced across two apps in two separate business portfolios. There is no
  public Graph API for the list. The code stays because it works given a
  populated allow-list; it is not the path this demo uses.

  One live constraint remains on the working path: the account has **no Content
  templates**, so freeform sends only succeed inside WhatsApp's 24-hour customer
  service window. A cold, business-initiated send still fails, and fails
  honestly as `comms.not_delivered`.

  Neither is general production reach. Sending to an arbitrary number needs Meta
  business verification and template approval, roughly 10–15 business days.
  Whatever the transport, **clinical detail never traverses WhatsApp** — that is
  the classifier's job, and it runs before the transport is reachable at all.

- **Settlement.** The deciding is autonomous; the money is not. Three rails,
  and the receipt records which one carried each payment, because "settled" on
  one is a different claim from "settled" on another.

  | `ANBU_PAYMENT_MODE` | What actually happens |
  |---|---|
  | `razorpay` | A real Razorpay API call in **test mode**. Real payment link, real webhook, test money. A person opens the link — a Payment Link is a *collection* instrument, so a lane ending in one ends with somebody clicking. |
  | `razorpayx` | A real RazorpayX **payout** — money pushed to a beneficiary, nobody present. Needs a provisioned RazorpayX account; the Payouts endpoint 404s without one. Falls back and says so when unconfigured. |
  | `payout` | The payout shape with no provider behind it. Settles without a human and labels itself simulated on every surface. |

  **No mode moves real money.** Autonomous debit needs UPI Autopay or an NPCI
  e-mandate, and NPCI caps AFA-free mandate debits at ₹15,000 — the raised
  ₹1,00,000 tier is restricted to merchant categories hospital billing is not
  in, so a ₹31,650 interim bill would prompt for a UPI PIN every time. A payout
  carries no such step, which is why the payout rail is the one with a route to
  being real, and why the blocker is merchant KYC rather than code.

- **Diagnostic centre coverage.** The *centres* are real and live — Google
  Places, queried at the moment of the referral. What is **not** live is
  whether any of them is in a family's insurance network: the KB holds five
  hospitals, so for almost every centre the honest answer is "Anbu Care has no
  network information for this centre", and that is what it says. Nothing is
  connected to any lab, in either direction.

**Real, but narrower than it looks:**

- **Wellbeing check-ins are self-reported, never a measured vital.** The
  dashboard labels them so. An absent entry reads "no check-in yet" — a missing
  check-in is not evidence that anything is well.
- **A transcript is not what she said.** It is what a model heard. That is why
  the audio is retained and playable, and why nothing in the system attributes
  the transcript to her as speech.
- **The care circle is a set of notified parties, not integrated providers.**
  Nobody in it can reply into the system, and no hospital or clinician system is
  connected in either direction.
- **A placed call is not an answered call.** The telephony reports that a call
  was placed; whether anyone picked up is outside what this system can know, and
  it never claims otherwise.
- **Anbu Care does not sense anything.** There is no location tracking, no
  monitoring, and no autonomous detection. Every input is something a person
  deliberately sent.
- **The handoff link cannot say who opened it.** A link is a bearer credential,
  so the access receipt records that the summary was read and when, and states
  in its own payload that this was a link holder rather than an identified
  clinician. Naming a doctor it cannot verify would be the same false claim as
  reporting a message delivered when the provider only accepted it.
- **A clinician note is attributed to whoever held the link**, and the system
  cannot check that either. What it can prove is that the text was confirmed
  before it was written, and that nobody has altered it since.
- **The trace shows the deciding; it is not the deciding.** It renders receipts
  that already exist. Where a step left no receipt it shows nothing — which is
  how the gather in the claim loop was found to be invisible, and then fixed by
  recording it rather than by inferring it.
- **Nothing here is a hospital integration.** No EHR is connected, nobody writes
  back, and the clinician view says so on its own face.
- **A bill split is an estimate, not a settlement.** It comes from the policy
  rules, not from the insurer, who has not been asked and does not know this
  system exists. Every field is named `estimated_`, and `settled_inr` stays
  None until a real adjudication says otherwise — because nobody having decided
  is a different thing from nothing being owed. On a reimbursement claim the
  family pays first and is repaid later, so even a correct estimate is not
  money they hold.
- **"Autonomous payment" is autonomous deciding.** Nothing here moves money by
  itself in any mode, and the surfaces say which rail carried each payment
  rather than leaving "settled" to be read as more than it is. The interesting
  claim is the refusal: an over-cap bill, a bill from another hospital, a bill
  naming a different UPI ID, and a second photograph of a bill already paid all
  stop without a human, and each names the check that stopped it.
- **Initiated is not paid.** A payment leaves the enforcer unconfirmed and stays
  that way until a settlement confirmation arrives, written by a different
  actor — `payment_rail`, never `payment_enforcer`. The money view counts the
  two separately, and a rail that reported its own success would be the failure
  this lane is built to avoid.
- **A destination leaves the store by exactly one door.** Every response
  carries `payee_ref`, a hash prefix that proves which destination without
  being one. One named, authenticated endpoint serves the real address with a
  UPI QR, because nobody can pay an account they have not been given — and the
  page renders it only there, read-only, on request.
- **An extracted amount is a reading, not a fact.** A model that reads ₹96,000
  where the paper says ₹9,600 produces a number that looks exactly as
  authoritative as a correct one. So the lines are checked against the bill's
  own printed total, a mismatch is flagged rather than reconciled, and the
  image is retained — credentialed — so the figure can be checked. A bill whose
  photograph could not be stored is refused outright rather than recorded as
  unverifiable numbers.

Every market figure quoted in the project brief is directional and unverified.
See [`docs/CITATIONS.md`](docs/CITATIONS.md) before repeating any of them.

---

## Quick start

```bash
make install          # uv sync --extra dev
make test             # 1070 tests, no GCP or model access needed
make preflight        # the state that silently ruins a recording, in ~2s
make demo             # the full spine, end to end, with no model in the loop
```

`./booker/                 the browser service, deployed apart from the API
  Dockerfile            Playwright image; Chromium will not fit beside the API
  driver.py             navigate, read the form, validate, fill, submit
  app.py                two endpoints and a session parked on a one-time code
scripts/demo_run.sh` drives the **deployed** service through the full demo
narrative — fresh synthetic cases each run, a separate throwaway case for the
tamper beat, and `--reset` to clean up. The beat sheet is
[`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

`make demo` runs `scripts/demo_spine.py`: onboarding, document ingestion, the
triage decision, the WhatsApp gate, packet assembly, the STEP_UP gate,
submission with a live SLA clock, and then it tampers with a receipt to show the
chain catching it. Nothing in it depends on what an LLM does on the day.

To talk to the agents:

```bash
gcloud auth application-default login
cp .env.example .env          # then set GOOGLE_CLOUD_PROJECT
make keygen                   # mint a stable signing key, put it in .env
make verify-stack             # confirm Vertex, Firestore, Pub/Sub are reachable
make chat                     # terminal conversation with the coordinator
make serve                    # agent API + ADK dev UI on :8080
```

Note that `gcloud auth application-default login` resets ADC's quota project to
your gcloud default every time you run it, which silently bills and meters every
Vertex and Firestore call against the wrong project. `.env` pins
`GOOGLE_CLOUD_QUOTA_PROJECT` so that reset cannot take effect — verified by
pointing ADC at the wrong project and confirming Vertex still resolves correctly.

---

## The stack

Every mandatory requirement, and where it is actually load-bearing.

| Requirement | Used for |
|---|---|
| **Gemini 3.5 (Vertex AI)** | Multimodal reasoning over discharge summaries, lab reports, ECG images and prescriptions; policy-clause matching; and single-call transcription of inbound WhatsApp voice notes. Deployed default is `gemini-3.5-flash`, configurable via `ANBU_MODEL` — any Gemini 3.5+ model satisfies the mandate. |
| **Google ADK** | Five sub-agents with isolated tool scopes under one coordinator. |
| **Cloud Run** | Hosts the agent API, the Twilio webhook, and the dashboard. |
| **Firestore** | Case state and the hash-chained receipt ledger, single-table PK/SK. |
| **Pub/Sub** | Async multi-day case tracking — intake events, case updates, claim status. |
| **Memory Bank** | Wired via `ANBU_MEMORY_SERVICE_URI`, but **the deployed revision runs the in-memory fallback** — `/api/healthz` reports `"memory_bank": "in-memory (not persistent)"`. Cross-session persistence is therefore not live. |

---

## Architecture

```
   Parent in India                    NRI Family (dashboard + WhatsApp)
   (WhatsApp voice note / text)                     |
              |                                     |
   +----------v-----------+                         |
   | Inbound wellbeing    |  Gemini transcribes     |
   | (Twilio webhook)     |  audio in ONE call;     |
   | audio is the record, |  RED_FLAGS table (code) |
   | transcript derived   |  decides, not the model |
   +----------+-----------+                         |
              |                                     |
              |  red flag -> open case, triage      |
              +------------------+------------------+
                                 |
                 +------------+--+---------+
                 |  Onboarding / KB Agent  |
                 |  (medical hx, docs,     |
                 |   insurance baseline)   |
                 +------------+------------+
                              |
                 +------------+------------+
                 |   Coordinator (root)    |  (ADK)
                 +------------+------------+
                              |
      +-------------+---------+---------+-------------------+
      |             |                   |                   |
 +----v-----+ +-----v------+   +--------v-------+  +--------v-------+
 | Triage   | | Evidence / |   | Insurer/TPA    |  | WhatsApp Comms |
 | Agent    | | STEP_UP    |   | Liaison Agent  |  | Agent          |
 | severity+| | (pre-submit|   | (submits pre-  |  | (template-     |
 | location+| |  evidence  |   |  validated     |  |  compliant     |
 | hospital | |  enrich)   |   |  packet;       |  |  status to     |
 | matching | |            |   |  *SIMULATED*   |  |  parent + NRI) |
 +----+-----+ +-----+------+   +--------+-------+  +-------+--------+
      |             |                   |                  |
      |             |                   |          +-------v--------+
      |             |                   |          | Care circle    |
      |             |                   |          | NOTIFIED only, |
      |             |                   |          | never          |
      |             |                   |          | integrated     |
      |             |                   |          +----------------+
      |             |                   |
      +-------------+---------+---------+
                              |
                 +------------v------------+
                 | Signed Receipt /        |
                 | Provenance Chain        |
                 | (Firestore, hash-chained|
                 +------------+------------+
                              |
                 +------------v------------+
                 | Memory Bank             |
                 | (cross-session, multi-  |
                 |  month family + policy) |
                 +-------------------------+
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the design decisions and
why each one was made.

---

## The one idea worth stealing

**Guardrails that matter are code, not prompts.**

Four things in this system must hold on every single run, including the run
where the model is confused, the caller is reassuring, or someone is trying to
talk their way past the boundary:

1. **A red-flag symptom escalates.** `anbu_care/triage/severity.py` decides
   severity. The agent relays it and is instructed never to soften it. "She says
   it's probably just gas" does not downgrade chest pain.

2. **Clinical detail leaves over WhatsApp only where a named exception says
   so.** `anbu_care/comms/policy.py` classifies the *content*, not the caller's
   claim about it. A message declared as `logistics` that carries a troponin
   value is blocked anyway — and the block is written to the receipt chain as
   evidence the boundary held.

   There is exactly one exception and it is a **frozen set of template names**,
   not a flag a caller can pass: the treating team's own update, to the family,
   about their own parent. It is allowed by name, its detection is not
   suppressed, and the receipt records that an exception was applied and what
   it found. The distinction worth stealing is that the exception is as
   auditable as the rule — a message carrying clinical detail is never
   indistinguishable from one that did not.

3. **A counterparty never chooses the destination.** A bill may propose an
   amount and can never propose a payee; a web page may hold a booking form and
   can never choose which centre she goes to. Both are the same guard pointed
   at different nouns, both assign from the mandate rather than comparing
   against it, and the booking one matches on Google's place id because a name
   is a string a page can print.

4. **A decision cannot be silently rewritten.** Every consequential action
   appends a signed receipt whose hash covers the previous one. Editing an
   earlier entry breaks every hash after it, and `verify_case_chain` reports
   exactly where.

An agent that is merely *told* not to leak a lab value is not a control.

---

## Repository layout

```
anbu_care/
  agent.py              root coordinator — ADK entrypoint (`root_agent`)
  agents/               five sub-agents, one file each
  tools/                one tool module per agent — isolated scopes
  triage/               severity rules + hospital ranking (deterministic)
  comms/                WhatsApp message policy (deterministic), outbound translation, link aliasing
  recovery/             the fortnight after discharge: window, cadence, stop conditions
  preauth/              cashless pre-authorisation at admission, and the insurer's 1-hour clock
  bills/                bill vision, line items, sub-limit and co-pay arithmetic
  diagnostics/          live Places search, ranking, and reading an order from dictation
  payments/             standing and per-case mandates, the nine guards, the settlement rails
  booking/              holding an appointment: the mandate, twelve guards, the OTP relay
  intake.py             photographs kept until they have actually been read
  docvision/            the other four document kinds: classify, extract, apply
  brief/                the arrival brief, composed from receipts and the record
  handoff/              scoped clinician links, and binding a handset to one case
  webauth.py            credentials: demo token, signed link, Google identity
  provenance/           hash chain, Ed25519 signing, Firestore/memory store
  kb/                   seeded Thoothukudi hospital knowledge base
  service.py            case state transitions, SLA clocks, Pub/Sub
  server.py             Cloud Run entrypoint (ADK API + dashboard routes)
scripts/
  demo_spine.py         end-to-end run, no model, no transport, no GCP
  verify_stack.py       confirms Vertex / Firestore / Pub/Sub are reachable
  make_bill_images.py   synthetic Indian hospital bills, for the vision lane
  make_documents.py     synthetic discharge summary, lab report, prescription, policy
  link_google_account.py  link a Google address to a family contact
  backfill_document_details.py  re-read stored photographs into `details`
  retake_bill.py        the same bill photographed a second time, for the dedupe
  collapse_demo_family.py  fold accumulated demo families back to the live one
  preflight.py          the state that silently ruins a take (`make preflight`)
  clear_rehearsal_debris.py  fold repeated photographs of one admission back to one
  seed_breach.sh        an already-lapsed cashless clock, for demonstrating the breach
tests/                  1144 tests, no GCP or model access needed
infra/deploy_cloud_run.sh
infra/deploy_booker.sh
infra/schedule_recovery_tick.sh  the two ticks Cloud Run cannot hold itself
```

---

## HTTP endpoints

Beyond ADK's own agent API:

| Route | Purpose |
|---|---|
| `GET /api/healthz` | Liveness, plus whether the signing key is stable and Memory Bank is wired |
| `GET /api/hospitals` | The seeded KB, served with its provenance attached |
| `POST /api/demo/seed` | Create the Thoothukudi demo family, returns its `parent_id` |
| `POST /api/intake` | Direct triage — how an automated intake signal enters the system |
| `GET /api/parents/{id}` | Baseline record and every ingested document |
| `GET /api/cases/{id}` | Case metadata and current chain head |
| `GET /api/cases/{id}/trail` | Every decision on the case, in order, with hash links |
| `GET /api/cases/{id}/brief` | The arrival brief, degrading to "not yet known" in every hole |
| `GET /api/cases/{id}/verify` | Independent chain verification — deliberately unauthenticated |
| `POST /api/wellbeing/inbound` | Twilio webhook — inbound check-ins, text and voice notes |
| `GET /api/parents/{id}/wellbeing` | Check-in history for a parent |
| `GET /api/parents/{id}/care-circle` | Care-circle contacts and their consent purposes |
| `POST /api/recovery/tick` | Send any recovery check-in that is due. **Credentialed** — it is the trigger for the only outbound channel pointed at the parent herself |
| `POST /api/cases/{id}/preauth` | Ask the simulated adjudicator whether cover exists for this admission. Idempotent: one admission is one pre-authorisation |
| `GET /api/cases/{id}/preauth` | Every pre-authorisation on the case and how its clock stands right now |
| `POST /api/claims/sla-tick` | Record every cashless clock that has actually lapsed. **Credentialed** — an open version would let anybody put a regulatory breach on somebody else's record |
| `POST /api/cases/{id}/preauth/backdate` | Move a pending clock's start into the past, **for demonstration**. The deadline stays start plus one hour and the breach still has to be genuinely past it; the request and the breach both carry `requested_at_source: demonstration_seed` so a seeded clock can never read as an hour that elapsed on its own |
| `GET /api/parents/{id}/recovery` | The recovery window, whether consent is held, and the answers recorded |
| `POST /api/cases/{id}/notify-claim` | Claim-status message to the family, through the comms gate |
| `POST /api/cases/{id}/notify-care-circle` | Care-circle notice, through the comms gate |
| `POST /api/intake-signal` | Structured intake event |
| `GET /api/intake-channels` | Which intake channels are configured |
| `GET /api/map-config` | Maps browser key and the hospital-provenance source note |
| `GET /api/cases/{id}/trace` | The decision sequence — one step per receipt, nothing synthesised |
| `POST /api/cases/{id}/handoff-link` | Mint an emergency-access link for a treating clinician |
| `POST /api/cases/{id}/handoff-link/revoke` | Kill every outstanding link for the case |
| `GET /api/cases/{id}/diagnostics` | Clinician-ordered tests on the case. **Credentialed** — the test a doctor ordered is clinical detail |
| `POST /api/cases/{id}/diagnostics/{order_id}/options` | Live Places search near the hospital. Refuses if no clinician ordered anything |
| `POST /api/cases/{id}/diagnostics/{order_id}/notify` | Tell the family, logistics only, without naming the test |
| `POST /api/parents/{id}/booking-mandate` | Authorise Anbu Care to hold an appointment. Standing, and carrying **no** authority to spend |
| `DELETE /api/parents/{id}/booking-mandate` | Withdraw it, including from admissions already carrying it |
| `POST /api/cases/{id}/diagnostics/{order_id}/arrange` | Choose a centre and try to hold the slot, falling through on failure |
| `GET /api/cases/{id}/appointments` | What was arranged, what was only requested, and every centre tried on the way. **Credentialed** — where she will be on Thursday is a fact about her |
| `POST /api/cases/{id}/appointments/{id}/cancel` | Withdraw one, and hand back the path for telling the centre |
| `GET /api/cases/{id}/appointments/{id}/evidence` | A signed link to the centre's own page. `?stage=sent` for the form as it was filled. **Credentialed** — the page carries her name and a number |
| `GET /api/cases/{id}/appointments/{id}/evidence/view` | The same, as a redirect, so a WhatsApp message can carry the proof beside the claim |
| `GET /api/cases/{id}/attempts/{n}/evidence/view` | The page of a centre that REFUSED, by its position in this case's own escalation receipt. The object is never named by the caller |
| `GET /api/preflight` | The state that silently ruins a recording, in one round trip. **Credentialed** |
| `GET /api/cases/{id}/bills` | Photographed bills and the estimated policy split |
| `POST /api/parents/{id}/payment-mandate` | Authorise **standing**, ahead of any admission. Every case opened while it is live adopts it and they share the total cap |
| `DELETE /api/parents/{id}/payment-mandate` | Withdraw it, including from admissions already carrying it |
| `POST /api/cases/{id}/payment-mandate` | Authorise for one admission: destination, per-bill cap, total cap, window. Wins over the standing grant |
| `DELETE /api/cases/{id}/payment-mandate` | Revoke it. Every further bill needs approval |
| `GET /api/cases/{id}/payments` | What is paid, what is merely initiated, what authority remains, and every refusal with the check that caused it |
| `POST /api/cases/{id}/bills/{bill_id}/consider` | Put a bill already on file to the guards |
| `POST /api/cases/{id}/payments/approve` | A human approving what the enforcer refused. Authorises an amount, never a destination |
| `POST /api/cases/{id}/payments/{payment_id}/confirm` | Record that a settlement confirmation arrived |
| `GET /api/cases/{id}/payments/{payment_id}/upi` | The UPI intent and QR for one payment. **The only response that carries a raw destination** |
| `POST /api/payments/razorpay` | Provider webhook — collections and payouts. HMAC-verified, deliberately session-less |
| `GET /api/cases/{id}/bills/{bill_id}/image` | Short-lived signed URL for the source photograph |
| `GET /api/parents/{id}/documents/{doc_id}/image` | The same, for a photographed document |
| `GET /api/auth-config` | Which sign-in methods this deployment offers |
| `GET /api/whoami` | Who the presented credential says you are. Never 401s |
| `GET /s/{code}` | Follow a short alias to the link it stands for. Unauthenticated, like the long URL it hides; unknown and expired answer the same **410** |
| `GET /handoff/{token}` | The clinician's read-only summary. No login, by design |
| `POST /handoff/{token}/note/draft` | Transcribe a spoken note for review. Writes nothing |
| `POST /handoff/{token}/note/confirm` | Record a confirmed note |
| `GET /app` | The dashboard |

Everything returning case or patient content sits behind
`require_case_access` / `require_family_session` and answers **401** without a
session. Two deliberate exceptions, and they are different in kind:

- `/api/cases/{id}/verify` proves integrity **without revealing content** — no
  name, no allergy, no hospital, no rupee figure — so it needs no credential.
- `/handoff/{token}` shows clinical content to someone who has never
  authenticated, because a nurse holding an unconscious patient will not create
  an account. It is not a second way in: only a caller who already holds a
  family session can mint the token, so it delegates a subset of that access
  rather than bypassing it. One case, sixty minutes, revocable, and every open
  writes a receipt the family can see.

Smoke-testing a fresh deploy is three calls:

```bash
PARENT=$(curl -sX POST $URL/api/demo/seed | jq -r .parent_id)
CASE=$(curl -sX POST $URL/api/intake -H 'content-type: application/json' \
  -d "{\"parent_id\":\"$PARENT\",\"symptoms\":[\"chest pain\"],\"reported_by\":\"neighbour\"}" \
  | jq -r .case_id)
curl -s $URL/api/cases/$CASE/verify | jq
```

The verify route is unauthenticated on purpose. The point of a receipt chain is
that a family or an insurer can check it without trusting us to run the check
for them.

---

## Deploying

```bash
make keygen                                  # mint a stable Ed25519 key
export ANBU_SIGNING_KEY_B64=...              # deploy refuses without it
make deploy                                  # gcloud run deploy --source .
```

The deploy script refuses to run without a stable signing key. An ephemeral key
means each revision signs with a different one, and receipts written before a
restart stop verifying — which defeats the entire point of the chain.

### First deploy on a fresh project

Two things bite once, and neither is obvious from the error message.

**1. Firestore needs a composite index.** The single-table layout queries
pk-equality + sk-range + order-by, which Firestore refuses without one — and it
fails at *query* time, so writes succeed and reads blow up. Create it before the
first real run:

```bash
gcloud firestore indexes composite create --collection-group=anbu \
  --field-config=field-path=pk,order=ascending \
  --field-config=field-path=sk,order=ascending
```

The definition is recorded in [`infra/firestore.indexes.json`](infra/firestore.indexes.json).

**2. The default compute service account needs build roles.** A fresh project's
`PROJECT_NUMBER-compute@developer.gserviceaccount.com` cannot read the source
bucket Cloud Build uploads to, and the deploy fails with a `storage.objects.get`
403:

```bash
SA=PROJECT_NUMBER-compute@developer.gserviceaccount.com
for role in roles/storage.objectViewer roles/logging.logWriter \
            roles/artifactregistry.writer roles/cloudbuild.builds.builder; do
  gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:$SA" --role="$role" --condition=None
done
```

### Public access under domain-restricted sharing

`--allow-unauthenticated` grants `roles/run.invoker` to `allUsers`. If your
organization enforces `constraints/iam.allowedPolicyMemberDomains`, that grant is
**refused and the deploy still reports success** — you get a service nobody can
reach, with only a warning in the output.

`infra/deploy_cloud_run.sh` therefore uses `--no-invoker-iam-check` instead,
which is Google's documented alternative for projects under DRS. It is scoped to
the one service and modifies no organization policy.

The **booker is the opposite** and deliberately so. `infra/deploy_booker.sh`
deploys it with `--no-allow-unauthenticated` and grants `roles/run.invoker` to
the API's service account and nothing else: a browser that will type a
stranger's name into a form on request is not left open to the internet. Cloud
Run answers an unauthorised caller with **404, not 403** — refusing without
admitting the service exists — so a "not found" from that service usually means
the caller is not an invoker rather than that the route is missing.

```bash
# make public
gcloud run services update anbu-care --region=asia-south1 --no-invoker-iam-check
# make private again
gcloud run services update anbu-care --region=asia-south1 --invoker-iam-check
```

### Google Sign-In

Optional. Unset, `/api/auth-config` reports `null`, the button says it is not
configured, and the demo credential still works — nothing else changes.

The client is created in the console; there is no `gcloud` path for a Web OAuth
client (`gcloud alpha iap oauth-clients` is IAP brands, a different thing).

1. **Google Auth Platform → Get started.** Audience **Internal** if the project
   sits in a Workspace organization: no test-user list, and no "Google hasn't
   verified this app" interstitial, which External-in-Testing shows even to
   test users.
2. **Clients → Create client → Web application.** Authorized JavaScript origin
   is the deployed base URL, scheme and host only — no trailing slash, no path.
   **Leave redirect URIs empty**: Google Identity Services returns the ID token
   to the page, so there is no redirect leg to authorise.
3. `ANBU_GOOGLE_CLIENT_ID=...apps.googleusercontent.com` and redeploy.

The client ID is public by design — it identifies the app to Google and ships in
every page offering the button. The **client secret** in the downloaded JSON is
not used by this flow and is stored nowhere.

Signing in is not permission. The verified address must already be on the
parent's family contacts:

```bash
uv run python scripts/link_google_account.py --parent parent-xxxx \
    --contact Karthik --email you@example.com --apply
```

Without that, a perfectly valid Google account gets a 403, which is the system
working rather than failing.

### Liveness is `/api/healthz`, not `/healthz`

Google Front End reserves the bare `/healthz` path and never forwards it to the
container, so a route defined there works locally and returns 404 in Cloud Run —
with no `server: Google Frontend` header, which is the tell. The liveness route
is served under `/api/` for that reason. See `infra/DEPLOYED.md`.

### WhatsApp delivery credentials

Secrets are read from the environment only — never committed, never logged,
never baked into an image layer. Locally they go in `.env` (gitignored); on
Cloud Run, inject them as secrets rather than plain env vars.

**Meta Cloud API** (implemented, but a dead end on this project — see the honesty note above):

```bash
# From the Meta app dashboard: WhatsApp -> API Setup
ANBU_WHATSAPP_MODE=meta
WHATSAPP_ACCESS_TOKEN=...        # "Generate access token" — temporary, expires
WHATSAPP_PHONE_NUMBER_ID=...     # the numeric ID, not the phone number
WHATSAPP_API_VERSION=v25.0
```

No conversation window exists until the recipient has written to you, so the
first contact must be a pre-approved template. `transport.open_session(to)`
sends `hello_world` for exactly that purpose. It is an operational handshake,
not a family update: it carries no case content and does not go through the
gate, because there is nothing to classify.

**Twilio** (the working path; needs a non-trial account, see the honesty note above):

```bash
ANBU_WHATSAPP_MODE=twilio
TWILIO_ACCOUNT_SID=AC...
TWILIO_API_KEY_SID=SK...         # preferred: revocable without rotating the
TWILIO_API_KEY_SECRET=...        # account auth token
TWILIO_WHATSAPP_FROM=whatsapp:+1...   # your sandbox sender, NOT the docs default
```

On Cloud Run:

```bash
printf %s "$WHATSAPP_ACCESS_TOKEN" | gcloud secrets create whatsapp-access-token --data-file=-

gcloud run services update anbu-care --region=asia-south1 \
  --update-secrets=WHATSAPP_ACCESS_TOKEN=whatsapp-access-token:latest \
  --update-env-vars=ANBU_WHATSAPP_MODE=meta,WHATSAPP_PHONE_NUMBER_ID=...
```

The runtime service account needs `roles/secretmanager.secretAccessor` on those
secrets. `ANBU_WHATSAPP_MODE=off` (the default) records gate decisions and sends
nothing.

### Runtime service account needs explicit roles

If your organization enforces
`constraints/iam.automaticIamGrantsForDefaultServiceAccounts`, the Cloud Run
runtime identity gets no Editor role and every Firestore write fails with a 500.
Grant it explicitly:

```bash
SA=PROJECT_NUMBER-compute@developer.gserviceaccount.com
for role in roles/datastore.user roles/pubsub.publisher roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:$SA" --role="$role" --condition=None
done
```

Then deploy a new revision — running instances do not pick up IAM changes.

## Future work

- **Gemma as an intake normalizer — DEFERRED, precheck negative (not pending).** The plan was a small model converting messy
  free-text intake ("amma fell, chest heavy, sweating, BP 160") into the
  structured fields the deterministic triage table consumes — advisory input
  only, never able to set severity or write a receipt. **Not built:** Gemma is
  not available as a managed endpoint on this project's Vertex AI (absent from
  the publisher listing; `gemma-3-27b-it`, `gemma-3-12b-it` and `gemma-2-9b-it`
  all return 404 on `generateContent`). Serving it would mean deploying from
  Model Garden to a dedicated GPU-backed endpoint, billed per hour rather than
  per token. Deferred as infra cost out of proportion to a component that by
  design cannot change any decision.
- **Nothing closes the loop after a booking.** An appointment is made, the
  family is told, and then the lane stops: nothing moves `requested` to
  `confirmed` for a callback-only centre, nothing reminds anybody, nothing
  checks she went, and a lab report photographed later is filed as a document
  with no link to the order that caused it. Worse, `cancel` marks our record and
  says plainly it has not contacted the centre — so the moment somebody rings to
  cancel, the record is wrong and nothing reconciles it. The honest fixes are a
  staleness nudge, and letting the arriving result close the loop, since a
  report can only exist if she went.
- **Voice calls are decided but not placed.** An escalation rings the care
  circle as well as messaging them, and with no voice transport configured the
  call is recorded as `voice.not_placed` with the reason and the words it would
  have spoken. That is the system declining to claim something it did not do,
  which is the right failure — but it is still a lane that has never carried a
  call. Wiring a provider is configuration, not design work.
- **The dashboard grants per-case, the API grants standing.** The mandate form
  in `webui/index.html` posts to `/api/cases/{id}/payment-mandate`, so granting
  from the UI narrows authority to that one admission — correct behaviour for
  that route, and the wrong default now that standing grants exist. The parent
  route has no control in front of it yet.
- **Per-analyte reference change values.** A repeat lab reading drifts for
  reasons that are not clinical, so changes inside a flat 10% band are narrated
  as "within normal variation" rather than "new and abnormal" — which is what
  stops a 1.7% cholesterol drift burying an HbA1c move from 7.1 to 8.4. A flat
  percentage is the crude version: real practice derives a reference change
  value per analyte from assay and biological variation. The band is narrative
  only — the high/low flag against the reference range is untouched, and no
  triage or adjudication decision reads it.
- **Ingestion provenance receipts — PARTLY SHIPPED.** A photographed document
  now writes a `document.ingested` receipt carrying both a content hash and an
  image hash, so a stored-then-altered reading is detectable and a duplicate
  photograph is refused rather than double-counted. The remaining gap is the
  **tool path**: `onboarding_tools.ingest_document`, used by the agent and the
  seed script, still writes no receipt, so a document that never came through a
  photograph is ground-truthed only by a stored count. The topology audit in
  [`docs/proposals/ingestion-provenance.md`](docs/proposals/ingestion-provenance.md)
  found the chain core is **already subject-agnostic** — `verify_chain`,
  sequence and `prev_hash` logic carry no case knowledge, so this is a new PK
  prefix rather than a refactor. Deliberately deferred, not unexamined: the
  case-chain tamper beat already carries the verifiability story, so the hour
  went to the adjudicator instead.

## Writeup material

- [`docs/content/build-log-post.md`](docs/content/build-log-post.md) — build-log post draft
- [`docs/content/social-post.md`](docs/content/social-post.md) — social drafts
- [`DISCLOSURE.md`](DISCLOSURE.md) — prior-work and simulated-adjudicator disclosure
- [`docs/CITATIONS.md`](docs/CITATIONS.md) — every market figure, and its verification status
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — the beat sheet
- [`infra/DEPLOYED.md`](infra/DEPLOYED.md) — the live environment

## Disclosure

Anbu Care reimplements two patterns from our own prior work, written fresh
during the submission window. See [`DISCLOSURE.md`](DISCLOSURE.md).

## Licence

MIT — see [`LICENSE`](LICENSE).
