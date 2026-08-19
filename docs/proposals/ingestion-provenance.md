# Proposal — ingestion provenance receipts

**Status:** proposal, no code written. Gated on approval.
**Scope:** onboarding/document path only. Existing receipts, triage, comms
policy, SLA clocks and the Ed25519 chain core stay untouched.

## The gap this closes

Every consequential action writes a receipt — `triage.decision`, `comms.blocked`,
`claim.submitted`. Ingestion does not. So the one action an agent was recently
caught *lying about* ("successfully ingested it into her health record", zero
documents stored) is the only one with no tamper-evident record.

Phase 2 closed half of it: the demo reads the stored-document count back from
the service, so a false claim is contradicted on screen. That catches
**claimed-but-never-stored**. It does not catch **stored-then-altered** —
today, editing a stored lab value leaves every chain verifying happily.

---

## 1. Verify semantics — record-vs-ledger, stated explicitly

**Current behaviour (must be stated plainly, because it is the whole risk):**
`verify_chain` checks *only chain integrity*. For each receipt it confirms the
sequence, the `prev_hash` link, that the receipt payload hashes to the recorded
hash, and that the signature verifies. **It never reads the subject record.**

That means a naive `document.ingested` receipt would be theatre. The receipt
would hash its own copy of the observations and verify forever, while the
`ParsedDocument` row it describes — a *separate* Firestore document at
`PARENT#<pid> / DOC#<doc_id>` — could be edited freely with nothing detecting it.
The demo claim "alter a stored reading and it breaks" would be **false**.

**Specified behaviour — the receipt is only worth building with this path:**

A new verification step, `verify_record_integrity(parent_id)`, that for every
`document.ingested` receipt:

1. reads the **currently stored** `ParsedDocument` by `document_id`;
2. recomputes `content_hash` over its observations using the canonical form in §2;
3. compares that against the `content_hash` recorded in the receipt;
4. reports divergence as a **fifth, distinct failure mode**.

The four existing modes describe damage *inside* the ledger. This one describes
the ledger and the record disagreeing:

| # | Failure | Meaning |
|---|---|---|
| 1 | sequence gap | a receipt was dropped |
| 2 | `prev_hash` mismatch | the chain was re-linked |
| 3 | hash mismatch | a receipt payload was edited |
| 4 | signature mismatch | a receipt was re-signed with another key |
| **5** | **record diverges from ledger** | **the stored document no longer matches what was signed** |

Mode 5 must also fire when the document is **missing entirely** — a deleted
record is divergence, not absence of evidence. Reported as
`record_missing` vs `record_altered` so a dispute can tell deletion from edit.

`verify_case_chain` keeps its current meaning and cost. Record-vs-ledger is a
separate call because it does N extra Firestore reads, and the unauthenticated
`/api/cases/{id}/verify` endpoint must stay cheap.

---

## 2. Canonicalization — one shared form, reusing Phase 2

An unchanged re-read must not hash to a false tamper. The hash therefore runs
over **validated `Observation` models, not raw tool input**:

```
content_hash = sha256(canonical_json([
    obs.model_dump(mode="json")
    for obs in sorted(observations, key=lambda o: (o.name.lower(), o.observed_on or ""))
]))
```

Three properties, all inherited rather than reinvented:

- **Numeric coercion is Phase 2's, unchanged.** `Observation._coerce_value` runs
  during validation, so `232` and `"232"` are both `"232"` before hashing, and
  `165.0` is `"165"`. A model that emits a number on Monday and a string on
  Tuesday produces the same hash. This is exactly the coercion the ingest path
  already uses — there is no second definition of "same reading".
- **`canonical_json` is the existing provenance encoder** — sorted keys, no
  incidental whitespace, UTC timestamps at microsecond precision. Same function
  the chain already signs with.
- **Order-independence via explicit sort.** Two ingests of the same panel in a
  different row order hash identically.

**Deliberately excluded from the hash:** `document_id`, `parsed_at`,
`source_filename`, and `delta_vs_baseline`. The first two are incidental to
*when* it was stored; `source_filename` is PII (§5); `delta_vs_baseline` is
derived from other documents and would make a document's hash depend on its
neighbours.

---

## 3. Cross-chain link — required field at point of use

The parent chain alone proves a document existed and is unaltered. It does
**not** prove which reading backed a decision. A case that says "we enriched the
claim with prior records" is not evidence unless it names *which* records, in
*which state*.

So the case chain carries the reference **at point of use**, not by inference:

```
evidence_refs: [
  { "document_id":  "doc-8f21ab90cd",
    "content_hash": "sha256:9c1f…",       # pins the exact reading, not just the row
    "ingest_receipt_hash": "a740…" }      # pins the parent-chain link that signed it
]
```

`document_id` alone is insufficient — the row it points at can change. The
`content_hash` is what makes the reference evidential. `ingest_receipt_hash`
lets a verifier walk to the parent chain and confirm the ingest was itself
signed, without needing the whole parent chain in hand.

Written on the case receipts that actually consume documents:
`evidence.enriched` (which already takes `additional_document_ids`),
`claim.packet_assembled` (`attached_document_ids`), and `triage.decision` where
patient history influenced severity.

A dispute then reconstructs: *this decision cited these readings, those readings
hash to what was signed at ingest, and the ingest is in a chain that verifies.*
That is the chain-of-custody claim stated end to end.

---

## 4. Topology cost — which world we are in

**We are in the cheap world, with one sharp edge.**

Audited:

| Component | Subject-agnostic? |
|---|---|
| `verify_chain()` | **Yes.** Takes `Iterable[Receipt]`, checks seq / prev_hash / hash / signature. Zero case knowledge. |
| `ReceiptChain` | **Yes in substance.** Holds one id and a receipt list; `append` is generic. |
| Sequence + `prev_hash` logic | **Yes.** Pure ordering over whatever set it is given. |
| Firestore PK/SK | **Nearly.** `CASE#{id}` is hardcoded in exactly two places — `load_receipts` and `save_receipt`. A parent chain is a new PK prefix, not a new mechanism. |
| `Receipt.case_id` | **This is the edge.** See below. |

**The sharp edge:** `case_id` is a named field *inside `signing_input()`*, so it
is hash-covered. Renaming it to `subject_id` would change the signing bytes and
**invalidate every receipt already written**. That is not acceptable — the whole
point is that old receipts keep verifying.

Two ways through, and the choice is a judgement call rather than a technical one:

- **(a) Reuse `case_id` as an opaque subject id.** Parent receipts carry
  `case_id = "parent-abc123"`. Zero migration, zero risk to existing receipts,
  ~2 lines changed in the store. The cost is a field whose name lies about its
  contents — precisely the kind of quiet misnaming that bites a maintainer in
  six months.
- **(b) Add `subject_type` with a default.** `subject_type: str = "case"`,
  included in `signing_input()`. Old receipts must then hash *as if* the field
  were absent, which means a versioned signing input — real complexity in the
  one component that must never be subtly wrong.

**Recommendation: (a),** with the field documented as an opaque subject id at
its definition. (b) buys naming clarity at the price of versioned canonical
bytes in the security core, which is a bad trade under deadline.

**Lighter alternative if even (a) is unwanted:** skip the parent chain entirely
and record `{document_id, content_hash}` pairs on the **case** receipts only
(§3), with no `document.ingested` receipt at all. This still supports the
record-vs-ledger check in §1 for any document a decision actually cited — which
is the evidential claim that matters — and adds **no new chain topology
whatsoever**. It loses only the ability to prove ingestion of a document that no
decision ever used, which is the least interesting case. If we want one thing
rather than the full design, this is the thing.

---

## 5. PII caveat

`source_filename` is the same PII class as the `exc.errors()` echo noted in
`onboarding_tools.ingest_document`. A real upload is plausibly
`rajeswari_discharge_apollo_2026.pdf` — patient name, facility, and date, written
into a signed, append-only, publicly verifiable ledger that **cannot be
redacted after the fact**.

Therefore: `source_filename` is **excluded from the hash and from the receipt
payload**. The receipt stores `document_id` and `kind` only. Everything here is
synthetic-only until that is settled, exactly as with `exc.errors()`.

This constraint is worth stating loudly: an append-only ledger and a
right-to-erasure regime (DPDP) are in direct tension. Hashes of clinical values
are defensible in a ledger; filenames and raw values are not.

---

## 6. Does this earn a demo beat?

**Honest answer: not as a new beat — but it should be folded into the existing
tamper beat, or skipped.**

The demo is already seven beats in roughly four minutes. Adding an eighth means
cutting one. But the record-vs-ledger check has a natural home: **Beat 6 already
tampers with something and shows the chain naming the failure mode.** Today it
edits a receipt payload (mode 3). Editing a *stored lab value* instead — and
watching mode 5 fire — narrates chain-of-custody at zero extra runtime and lands
a strictly stronger claim: not "our ledger is internally consistent" but "our
ledger and our record cannot silently disagree".

**Decision rule, as agreed:** if it will not be narrated, we skip it. My
recommendation is that it *is* narrated, as a variant inside Beat 6, and that
this is the only justification for building it. If the beat stays as-is, build
the §4 lighter alternative or nothing.

---

## Test plan (if approved)

- `content_hash` is stable across `232` / `"232"` / `165.0` / `"165"` inputs.
- `content_hash` is stable across observation reordering.
- Editing a stored observation → mode 5 `record_altered`, naming the document.
- Deleting a stored document → mode 5 `record_missing`.
- An untouched record verifies clean, and `verify_case_chain` is unaffected.
- `evidence_refs` on a case receipt resolve to a document whose current hash matches.
- A case receipt citing a since-altered document fails, and names it.
- Existing receipts written before this change still verify (regression guard).
