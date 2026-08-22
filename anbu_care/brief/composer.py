"""The arrival brief — what is waiting when the family lands.

Composed **deterministically** from the signed receipt chain plus the parent's
stored profile. No model is involved in deciding what the brief says; an agent
may only relay what this produced.

That architecture is the whole safety argument. A synthesis over multi-day state
fails gradiently — it can be ninety percent grounded and slip one plausible line
("cardiology follow-up Thursday") that nothing backs. In the one artifact a
family reads at their most frightened moment, that single unbacked line is the
worst failure this system has. So the facts are extracted in code, every line
carries where it came from, and anything the state does not contain comes back
as "not yet known" rather than as a guess.

Read-only by construction: nothing here writes, and composing a brief never
changes the episode it describes.
"""

from __future__ import annotations

from typing import Any

from anbu_care import service
from anbu_care.provenance.chain import Receipt
from anbu_care.schemas import (
    ArrivalBrief,
    ArrivalFact,
    DocumentKind,
    FactSource,
    ParentProfile,
)

UNKNOWN_PHRASE = "not yet known"


def _from_receipt(receipt: Receipt, field: str) -> FactSource:
    return FactSource(
        kind="receipt", receipt_seq=receipt.seq, receipt_kind=receipt.kind, field=field
    )


def _unknown(note: str) -> FactSource:
    return FactSource(kind="unknown", note=note)


def _fact(label: str, value: Any, source: FactSource) -> ArrivalFact:
    """Build a fact. An empty value is an unknown, never a blank line."""
    if value is None or (isinstance(value, str) and not value.strip()):
        note = source.note or "no receipt or stored field carries this yet"
        return ArrivalFact(label=label, value=None, known=False, source=_unknown(note))
    return ArrivalFact(label=label, value=str(value), known=True, source=source)


def _latest_document(parent_id: str, kind: DocumentKind):
    """The most recently parsed document of one kind, or None."""
    matching = [d for d in service.list_documents(parent_id) if d.kind is kind]
    return max(matching, key=lambda d: d.parsed_at) if matching else None


def _unknown_fact(label: str, note: str) -> ArrivalFact:
    return ArrivalFact(label=label, value=None, known=False, source=_unknown(note))


def _latest(receipts: list[Receipt], kind: str) -> Receipt | None:
    matches = [r for r in receipts if r.kind == kind]
    return matches[-1] if matches else None


def _hospital_name(triage: Receipt | None) -> tuple[str | None, FactSource]:
    if triage is None:
        return None, _unknown("no triage decision on this case yet")
    payload = triage.payload
    target = payload.get("recommended_hospital_id")
    for entry in payload.get("ranked", []):
        if entry.get("hospital_id") == target:
            return entry.get("name"), _from_receipt(triage, "ranked[].name")
    return None, _unknown("triage ran but recorded no recommended hospital")


def _what_is_already_known(profile: ParentProfile | None, triage: Receipt | None) -> list[ArrivalFact]:
    """Cover, distance and who is nearby — known long before any claim exists."""
    out: list[ArrivalFact] = []

    policy = getattr(profile, "policy", None) if profile else None
    if policy is not None:
        out.append(_fact(
            "Insurance", f"{policy.insurer}, policy {policy.policy_number}",
            FactSource(kind="profile", field="policy"),
        ))
        out.append(_fact(
            "Cover available", f"INR {policy.sum_insured_inr:,}",
            FactSource(kind="profile", field="sum_insured_inr"),
        ))
        out.append(_fact(
            "Cashless",
            ("Available at network hospitals" if policy.cashless_eligible
             else "Not available; this admission is reimbursement only"),
            FactSource(kind="profile", field="cashless_eligible"),
        ))
    else:
        out.append(_unknown_fact("Insurance", "no policy is recorded for this parent"))

    if triage is not None:
        recommended = triage.payload.get("recommended_hospital_id")
        for entry in triage.payload.get("ranked") or []:
            if entry.get("hospital_id") == recommended:
                out.append(_fact(
                    "Distance", f"{entry.get('distance_km', 0):.1f} km from home",
                    _from_receipt(triage, "ranked"),
                ))
                out.append(_fact(
                    "In her insurer's network",
                    "Yes" if entry.get("network_match") else "No",
                    _from_receipt(triage, "ranked"),
                ))
                break

    if profile is not None and profile.family_contacts:
        names = ", ".join(c.name for c in profile.family_contacts)
        out.append(_fact("People who can be notified", names,
                         FactSource(kind="profile", field="family_contacts")))

    return out


def _latest_check_in(parent_id: str) -> ArrivalFact:
    """The most recent wellbeing check-in, quoted verbatim.

    Quoted, because paraphrasing a self-report is the first step towards
    turning it into an assessment. If there is none, the brief says so: a
    missing check-in is not evidence that anything is well.
    """
    from anbu_care.wellbeing.store import latest

    if not parent_id:
        return _unknown_fact("Latest check-in", "no parent on this case")

    entry = latest(parent_id)
    if entry is None:
        return _unknown_fact("Latest check-in", "no check-in yet")

    when = entry.received_at.strftime("%H:%M")
    return ArrivalFact(
        label="Latest check-in",
        value=f'"{entry.text}", {when} — {entry.source}',
        known=True,
        source=FactSource(kind="wellbeing", field=entry.entry_id,
                          note="self-reported words, not a clinical assessment"),
    )


def compose_brief(case_id: str) -> ArrivalBrief:
    """Compose the brief for a case. Pure read — writes nothing."""
    chain = service.get_chain(case_id)
    receipts = chain.receipts
    case = service.load_case(case_id)
    parent_id = case.parent_id if case else ""
    profile: ParentProfile | None = service.load_profile(parent_id) if parent_id else None

    brief = ArrivalBrief(
        case_id=case_id,
        parent_id=parent_id,
        as_of=max((r.created_at for r in receipts), default=None),
        chain_receipt_count=len(receipts),
        chain_head_hash=chain.head_hash,
        chain_verified=chain.verify().ok,
    )

    triage = _latest(receipts, "triage.decision")
    adjudication = _latest(receipts, "claim.adjudicated")
    submitted = _latest(receipts, "claim.submitted")
    packet = _latest(receipts, "claim.packet_assembled")

    # ---- where things stand ------------------------------------------------
    if profile is not None:
        brief.facts.append(_fact("Parent", profile.name,
                                 FactSource(kind="profile", field="name")))
    else:
        brief.facts.append(_unknown_fact("Parent", "no stored profile for this case"))

    name, source = _hospital_name(triage)
    brief.facts.append(_fact("Hospital", name, source))

    # What the parent or a caregiver SAID, quoted and labelled. It is not a
    # vital, it is not a triage input, and no part of the brief derives
    # anything from it. Absent reads as absent, never as "fine".
    brief.facts.append(_latest_check_in(parent_id))

    brief.facts.append(
        _fact("Severity assessed", triage.payload.get("severity") if triage else None,
              _from_receipt(triage, "severity") if triage else _unknown("triage has not run yet"))
    )
    brief.facts.append(
        _fact("Why that hospital", triage.payload.get("explanation") if triage else None,
              _from_receipt(triage, "explanation") if triage else _unknown("triage has not run yet"))
    )

    # Facts the system already holds and was simply not showing. Every
    # "not yet known" that could have been answered is a worse unknown than
    # one that genuinely cannot be: it makes the record look emptier than it
    # is, and a family reading it cannot tell which is which.
    brief.facts.extend(_what_is_already_known(profile, triage))

    # Admission and discharge dates come from the claim packet, which is the only
    # place they are recorded as structured fields.
    admitted = discharged = None
    admitted_src = discharged_src = None
    if packet is not None:
        stored_packet = service.load_packet(case_id, packet.payload.get("packet_id", ""))
        if stored_packet is not None:
            admitted, discharged = stored_packet.admitted_on, stored_packet.discharged_on
            admitted_src = _from_receipt(packet, "packet.admitted_on")
            discharged_src = _from_receipt(packet, "packet.discharged_on")

    # A photographed discharge summary carries these dates on its face, and a
    # family reading "no discharge date has been recorded" while holding the
    # discharge summary they just sent is being told the system lost it. The
    # packet still wins where it exists — it is the assembled, submitted
    # version — but a read document beats nothing at all.
    summary_doc = _latest_document(parent_id, DocumentKind.DISCHARGE_SUMMARY)
    # The label follows the source, because they are not the same claim. A
    # packet carries the discharge date the claim was built around; a discharge
    # summary is the hospital saying she went home. Calling the first one
    # "Discharged on" would report a plan as an event.
    discharge_label = "Expected discharge"
    if summary_doc is not None:
        source = FactSource(kind="document", field=summary_doc.document_id,
                            note="read from the discharge summary photograph")
        if not admitted and summary_doc.details.get("admitted_on"):
            admitted, admitted_src = summary_doc.details["admitted_on"], source
        if summary_doc.details.get("discharged_on"):
            discharged, discharged_src = summary_doc.details["discharged_on"], source
            discharge_label = "Discharged on"

    brief.facts.append(_fact(
        "Admitted on", admitted,
        admitted_src or _unknown("no admission date has been recorded on this case"),
    ))
    brief.facts.append(_fact(
        discharge_label, discharged,
        discharged_src or _unknown("no discharge date has been recorded on this case"),
    ))

    # The rest of what that document says, once it exists. Each is shown only
    # when it was actually read — an absent line is absent, not a blank row.
    if summary_doc is not None:
        detail_source = FactSource(kind="document", field=summary_doc.document_id,
                                   note="read from the discharge summary photograph")
        for label, key in (("Diagnosis on discharge", "diagnosis"),
                           ("Condition at discharge", "condition_at_discharge"),
                           ("Follow-up due", "follow_up_on"),
                           ("Treating consultant", "consultant")):
            value = summary_doc.details.get(key)
            if value:
                brief.facts.append(_fact(label, value, detail_source))

    # ---- money -------------------------------------------------------------
    if adjudication is not None:
        payload = adjudication.payload
        outcome = payload.get("outcome")
        brief.facts.append(_fact(
            "Claim outcome so far",
            f"{outcome} (SIMULATED counterparty)",
            _from_receipt(adjudication, "outcome"),
        ))
        # Only a priced outcome carries a real out-of-pocket figure. A QUERY or
        # a DENY leaves total_disallowed_inr at 0 because nothing was priced —
        # reporting that as "INR 0" would be a false reassurance in the one
        # artifact a family reads while frightened, which is worse than an
        # omission.
        disallowed = payload.get("total_disallowed_inr")
        if outcome in {"PARTIAL", "PASS"} and isinstance(disallowed, int):
            brief.facts.append(_fact(
                "Likely out of pocket",
                f"INR {disallowed:,}" if disallowed else "nothing disallowed so far",
                _from_receipt(adjudication, "total_disallowed_inr"),
            ))
        elif outcome == "QUERY":
            brief.facts.append(_unknown_fact(
                "Likely out of pocket",
                "the insurer has raised a query, so no amount has been priced yet",
            ))
        else:
            brief.facts.append(_unknown_fact(
                "Likely out of pocket",
                f"the claim came back {outcome}; no payable amount was calculated",
            ))
    else:
        brief.facts.append(_unknown_fact(
            "Claim outcome so far", "the claim has not been adjudicated yet"))
        brief.facts.append(_unknown_fact(
            "Likely out of pocket",
            "no adjudication yet, so no disallowed amount has been calculated"))

    # ---- what has been done ------------------------------------------------
    for receipt in receipts:
        summary = _describe(receipt)
        if summary:
            brief.actions_taken.append(
                ArrivalFact(label=receipt.created_at.strftime("%d %b %H:%M UTC"),
                            value=summary, known=True,
                            source=_from_receipt(receipt, "payload"))
            )

    # ---- what is still open ------------------------------------------------
    if adjudication is not None and adjudication.payload.get("outcome") == "QUERY":
        for missing in adjudication.payload.get("missing_documents", []):
            label = missing.replace("_", " ")
            brief.pending.append(ArrivalFact(
                label="Insurer query open", value=f"awaiting {label}", known=True,
                source=_from_receipt(adjudication, "missing_documents"),
            ))
            brief.bring_with_you.append(ArrivalFact(
                label=label.title(), value="the insurer has asked for this", known=True,
                source=_from_receipt(adjudication, "missing_documents"),
            ))

    if submitted is not None:
        deadline = submitted.payload.get("sla_deadline")
        brief.pending.append(_fact(
            f"{submitted.payload.get('sla_kind', 'claim')} deadline", deadline,
            _from_receipt(submitted, "sla_deadline"),
        ))

    # "Nothing outstanding" and "not yet known" are different answers, and the
    # brief was giving the second when it had computed the first. But the
    # negative is only meaningful once the thing that produces open items has
    # actually run: before adjudication, an empty pending list means the claim
    # has not been looked at, NOT that the insurer wants nothing. Reporting
    # "nothing outstanding" there would be the omission guarantee inverted —
    # reassurance derived from absence. So the definite answer is gated on the
    # adjudication receipt, and it cites that receipt as its source.
    if not brief.pending:
        if adjudication is not None:
            brief.pending.append(ArrivalFact(
                label="Open items", value="Nothing outstanding", known=True,
                source=_from_receipt(adjudication, "outcome"),
            ))
        else:
            brief.pending.append(_unknown_fact(
                "Open items", "nothing on this case is recorded as pending"))

    if not brief.bring_with_you:
        if adjudication is not None:
            brief.bring_with_you.append(ArrivalFact(
                label="Documents to bring", value="Nothing requested", known=True,
                source=_from_receipt(adjudication, "outcome"),
            ))
        else:
            brief.bring_with_you.append(_unknown_fact(
                "Documents to bring",
                "no outstanding document request is recorded on this case"))

    # ---- who to talk to ----------------------------------------------------
    if profile is not None and profile.family_contacts:
        for contact in profile.family_contacts:
            brief.contacts.append(ArrivalFact(
                label=f"{contact.name} ({contact.relationship})",
                value=contact.whatsapp_e164, known=True,
                source=FactSource(kind="profile", field="family_contacts"),
            ))
    else:
        brief.contacts.append(_unknown_fact(
            "Family contacts", "no family contacts are on the parent's record"))

    if profile is not None and profile.policy is not None:
        brief.contacts.append(ArrivalFact(
            label=f"Insurer ({profile.policy.insurer})",
            value=f"policy {profile.policy.policy_number}", known=True,
            source=FactSource(kind="profile", field="policy"),
        ))
    else:
        brief.contacts.append(_unknown_fact(
            "Insurer", "no policy is on the parent's record"))

    return brief


def _describe(receipt: Receipt) -> str | None:
    """One line for a receipt, using only what the receipt actually carries."""
    payload = receipt.payload
    match receipt.kind:
        case "case.opened":
            return "Case opened."
        case "triage.decision":
            return f"Triage assessed severity {payload.get('severity')}."
        case "comms.sent":
            return "Family notified over WhatsApp (sandbox delivery)."
        case "comms.not_delivered":
            return "A message was permitted but not delivered — no transport was configured."
        case "comms.blocked":
            return "A message was blocked before sending (clinical detail is not permitted over WhatsApp)."
        case "claim.packet_assembled":
            total = payload.get("total_claimed_inr")
            return f"Claim packet assembled, INR {total:,} claimed." if isinstance(total, int) else "Claim packet assembled."
        case "evidence.assessed":
            return f"Claim evidence assessed: {payload.get('gate')}."
        case "evidence.enriched":
            return "Claim evidence enriched before submission."
        case "claim.submitted":
            return "Claim submitted to the SIMULATED insurer endpoint."
        case "claim.adjudicated":
            return f"SIMULATED adjudication returned {payload.get('outcome')}."
        case "claim.stage_changed":
            return f"Claim stage moved {payload.get('from')} to {payload.get('to')}."
        case _:
            return None


def render_brief_text(brief: ArrivalBrief) -> str:
    """Plain-text rendering. Unknowns are printed, not hidden.

    The staleness line is first on purpose: this is read hours later, mid-flight,
    and a brief that implies real-time truth is worse than one that omits.
    """
    as_of = brief.as_of.strftime("%d %b %Y %H:%M UTC") if brief.as_of else "no activity recorded"
    generated = brief.generated_at.strftime("%d %b %Y %H:%M UTC")

    lines = [
        "ARRIVAL BRIEF",
        f"State as of : {as_of}   (last recorded activity on this case)",
        f"Compiled    : {generated}",
        (
            "This is a snapshot, not a live view. Anbu Care does not monitor "
            "continuously — it records what has happened. Re-open the brief for "
            "anything newer."
        ),
        (
            f"Chain       : {brief.chain_receipt_count} receipts, "
            f"verified={brief.chain_verified}, head {brief.chain_head_hash[:12]}"
        ),
        "",
        "WHERE THINGS STAND",
    ]
    for fact in brief.facts:
        lines.append(f"  {fact.label:<22} {fact.value if fact.known else UNKNOWN_PHRASE}")
        if not fact.known:
            lines.append(f"  {'':<22}   ({fact.source.note})")

    for title, entries in (
        ("WHAT HAS BEEN DONE", brief.actions_taken),
        ("STILL OPEN", brief.pending),
        ("BRING WITH YOU", brief.bring_with_you),
        ("WHO TO CONTACT", brief.contacts),
    ):
        lines += ["", title]
        if not entries:
            lines.append(f"  {UNKNOWN_PHRASE}")
        for fact in entries:
            lines.append(f"  {fact.label:<22} {fact.value if fact.known else UNKNOWN_PHRASE}")
            if not fact.known:
                lines.append(f"  {'':<22}   ({fact.source.note})")

    unknowns = brief.unknown_count
    lines += ["", f"{unknowns} item(s) not yet known — shown above rather than guessed."]
    return "\n".join(lines)
