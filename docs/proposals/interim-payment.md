# Interim bill payment — Phase 0 design

**Status: proposed. No code written. Waiting on the design gate.**

Indian hospitals bill at intervals during a stay. An unpaid interim bill stops
cashless mid-treatment, which is the moment a family eleven time zones away is
least able to act. A present son would read the bill and pay it.

This is the highest-consequence feature in the project. Everywhere else the
worst case is a wrong sentence; here the worst case is money leaving an account
and arriving somewhere it should not.

---

## 1 · What is actually in the repo today

Checked, not assumed.

| | |
|---|---|
| Bill capture | **Exists.** `ExtractedBill` carries `bill_id`, `case_id`, `parent_id`, line items, `stated_total_inr`, `image_sha256`, `needs_review`. |
| A payee | **Does not exist.** Nothing in the codebase mentions UPI, payee or mandate. |
| Interim vs final | **Not modelled.** A bill is a bill. |
| Hospital UPI id | **Not in the KB.** Hospitals carry `place_id`, `verified_name`, `address`, `empanelled_insurers` — no payment destination. |
| `vendor` on a bill | A **string a model read off paper**. Today it is display only. |
| Receipt kinds | 18, none about money movement. |
| `/verify` | Returns `verified`, `receipt_count`, `broken_at_seq`, `reason`, `public_key`. **No payload at all**, so it already leaks nothing. |

**The consequential finding:** `ExtractedBill.vendor` is the only payee-shaped
field that exists, and it is model output. If payment is built carelessly, the
destination of money becomes a string Gemini read off a photograph. That is the
single worst failure mode available in this system and the design exists mostly
to make it structurally impossible.

---

## 2 · The two briefs contradict each other

- **Brief A:** no autonomous payment, ever. The agent prepares; a human approves
  every payment; money moves in the son's own UPI app.
- **Brief B:** bounded autonomy. The agent pays without per-payment approval,
  inside a pre-authorised envelope enforced by deterministic code.

These cannot both be built as stated. But they are not really rivals:

> **Brief A is Brief B's escalation path.**

In Brief B, anything outside the envelope — wrong payee, over cap, duplicate,
anomalous, revoked, out of window — refuses and escalates to the son for
explicit approval. That escalation *is* Brief A's flow.

**Proposal: build the superset, with the mandate optional.**

| Mandate state | Behaviour | Which brief |
|---|---|---|
| None granted | Every payable bill escalates. Son approves each one. | **A**, exactly |
| Granted, bill in envelope | Auto-initiates, no human tap | **B** |
| Granted, bill fails any check | Refuses, escalates, 0 paid | **A** again |

One enforcer, one receipt model, one set of absolute lines. The mode is a
consequence of whether a mandate exists, not a fork in the code. Every line
below holds in **both** modes and none of them is waivable by consent.

---

## 3 · The absolute lines

Each is deterministic, each gets its own test, and none can be relaxed by any
consent the son gives.

1. **The destination never comes from the bill.** A bill may propose an
   *amount*; it can never propose a *payee*. The payee is read from the mandate
   and from nowhere else.
2. **The LLM cannot move money.** Settlement is reachable only through the
   enforcer, and the enforcer is not exposed to any agent or tool.
3. **No banking credential exists anywhere.** No PIN, card number, bank login,
   UPI PIN — not stored, not logged, not in a receipt, not on the chain.
4. **Idempotent by bill id.** A bill is payable at most once, enforced in code.
5. **`payment.confirmed` is never assumed.** Initiated is not settled.
6. **Revocation hard-stops autonomy immediately.**

---

## 4 · The mandate

What the son authorises once, explicitly, in a credentialed session.

```
PaymentMandate
  mandate_id            
  parent_id             who it is for
  case_id               ONE admission. Not a standing arrangement.
  payee_vpa             the ONE destination. Never changes.
  payee_label           what to show a human ("Sacred Heart Hospital")
  per_bill_cap_inr      no single bill above this
  total_cap_inr         no cumulative spend above this
  window_opens_at       
  window_closes_at      wall-clock, not "24 hours" in a prompt
  granted_by            which family contact granted it
  granted_at
  revoked_at            set = dead, instantly and permanently
  method_ref            an opaque reference to a method held by a licensed
                        provider. NOT a credential. Never resolved by us.
```

Stored in Firestore beside the case, under the existing single-table PK/SK.

**Revocation is a hard stop, not a flag consulted politely.** The enforcer reads
the mandate fresh on every single decision and refuses if `revoked_at` is set.
There is no cached copy and no in-flight grace: a payment that has not yet
passed the enforcer when revocation lands does not pass it.

### How the payee is "verified" — and the honest limit

There is **no way for this system to prove a UPI VPA belongs to a hospital.**
VPA→name resolution exists through PSP APIs; we have no PSP. So "verified" here
means something narrower and I will label it that way everywhere:

- The son enters the VPA **once, out of band**, at mandate setup — read off the
  hospital's own billing desk or portal, not off a photograph, not from us.
- It is shown back to him with the hospital name for confirmation before the
  mandate is granted.
- From that moment it is **pinned**. Nothing can change it — not a bill, not an
  extraction, not the agent. Changing the payee means revoking the mandate and
  granting a new one, which is a fresh human act.

The guarantee is not "this is provably the hospital's account". It is **"this is
the account the son typed, and nothing since has been able to alter it."** That
is a real and testable guarantee, and it is the one that stops the failure mode
that matters.

---

## 5 · The enforcer — the choke point

A single deterministic function in the guarantee layer. **This is the one
approved addition to that layer.**

```
enforcer.decide(bill, mandate, history) -> Decision(pay | refuse, reasons)
```

Structurally:

- Lives in `anbu_care/payments/enforcer.py`, importing nothing from `agents/`
  or `tools/`.
- The settlement call is **private to the payments package** and called from
  exactly one place: inside the enforcer, after every check has passed.
- No agent, no tool and no model output can reach settlement. A test asserts
  this by import graph, not by convention.

### Check order — all must pass

1. mandate exists, not revoked, `now` inside the window
2. bill's `case_id` == mandate's `case_id`
3. bill id not already paid *(idempotency)*
4. amount > 0 and amount ≤ `per_bill_cap_inr`
5. running total + amount ≤ `total_cap_inr`
6. **payee := mandate.payee_vpa** — note this is an assignment, not a
   comparison. If the extraction proposed a payee at all, that is recorded as an
   anomaly signal and the extraction's value is discarded.
7. anomaly checks (§6) all clear
8. → initiate. Anything else → refuse with the failing check named.

Ordering matters: revocation and case scope are cheapest and most absolute, so
they run first. The failing check is recorded, because "it did not pay" is far
less useful to a family than "it did not pay **because the amount was above the
cap you set**".

---

## 6 · Anomaly signals — code, not prompt

Deterministic, thresholded, individually testable. Any one firing forces
refusal + escalation **even when the bill is within every cap**.

| Signal | Threshold | Why |
|---|---|---|
| Amount spike | > 3× the running mean of prior bills on this case (needs ≥2 priors) | An interim bill 5× the last one is either an error or an emergency; both want a human |
| Unseen pattern | The extraction proposed a payee that differs from the mandate | Not used as a destination — used as *evidence something is wrong with this bill* |
| Burst | A second bill within 6 hours of the last paid one | Hospitals do not bill hourly. Duplicates and fraud do |
| Near-cap | Amount ≥ 90% of `per_bill_cap_inr` | Sitting just under a cap is a signature, not a coincidence |
| Late-window | Bill arrives in the final 10% of the mandate window | The son is least likely to be watching |

Thresholds live in one module-level table, not scattered, so they can be read
and argued with.

---

## 7 · Receipts

Hash-chained on the existing chain. Amount, payee **reference** (never the raw
VPA — a stable hash prefix), case, and which guards passed or failed.

| Kind | Written when |
|---|---|
| `mandate.granted` | Son grants. Carries caps, window, payee ref, who granted |
| `mandate.revoked` | Son revokes. Hard stop from this instant |
| `payment.escalated` | Enforcer refused. **Names the failing check** |
| `payment.auto_initiated` | Enforcer passed everything. Lists the guards passed |
| `payment.approved` | A human explicitly approved an escalated bill |
| `payment.confirmed` | A settlement confirmation actually arrived |
| `payment.failed` | Settlement reported failure |

`payment.confirmed` is **never** written by the code path that initiates. It is
written only on receipt of a confirmation signal, exactly as `comms.sent` is
distinct from `comms.not_delivered`. An initiated-but-unconfirmed payment shows
as *initiated*, never as paid, in the money view.

**No receipt carries a credential**, and no schema in the payment path has a
field one could live in. Tested by schema assertion and by grep.

---

## 8 · Settlement: simulated and labelled

Real autonomous UPI debit needs a licensed PSP plus UPI Autopay / e-mandate
rails under NPCI. **Out of scope in this window**, and pretending otherwise
would be the one dishonesty this project has avoided everywhere else.

What is **real code and is the demo**: the mandate, the envelope enforcer, the
payee lock, idempotency, the anomaly step-up, the receipts, the trace.

What is **simulated and labelled**: the settlement itself, exactly like the TPA.
A generated UPI intent (`upi://pay?pa=…&am=…&tn=…&cu=INR`) is genuinely valid
and will open a UPI app — but the demo does not execute a transfer.

### NRI reality, stated plainly

UPI requires an **Indian bank account (NRO/NRE) linked to an Indian mobile
number**. A US card cannot fund UPI. An NRI son typically *can* do this through
his NRO account — but the demo must never imply US-card→UPI, because that is
not a thing.

### USDC — future vision, not built

Recorded as a line, not a plan. An Indian hospital terminal takes rupees, so a
stablecoin rail needs a USDC→INR→UPI off-ramp: **more** integration that still
does not reach the real payee. A future agent-held treasury settling to local
rails is interesting; it is not this.

---

## 9 · What the demo shows

The refusals are the strongest beat, not the payment.

1. An in-envelope interim bill **auto-clears with no human tap** (simulated settle)
2. A bill whose extraction proposes a different payee → **refused**, escalated, ₹0 moved
3. An over-cap bill → **refused**, escalated
4. The same bill twice → **paid once**
5. An anomalous but in-cap bill → **escalated**
6. Revocation → autonomy dead immediately

---

## 10 · Tests this adds

- payee lock: extraction payee ≠ mandate payee → refused, and the initiated
  payment would still only ever target the mandate payee
- import-graph: settlement unreachable from `agents/` and `tools/`
- no model output can reach settlement
- caps, window, case scope, revocation — one test each
- idempotency: same bill twice → exactly one payment, one receipt
- each anomaly signal fires independently
- `payment.confirmed` absent until a confirmation signal arrives
- no credential field in any payment schema (schema walk + grep)
- `/verify` exposes no amount, payee or credential
- the money view never counts an unconfirmed payment as paid
