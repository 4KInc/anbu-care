"""The arrival brief.

P6's failure mode was binary — a document is on file or it is not. This one
fails gradiently: a synthesis over multi-day state can be ninety percent
grounded and slip one plausible line that nothing backs. So the test that
matters is not "does the brief match state" but "does it degrade to *not yet
known* in every hole, and never guess."
"""

from __future__ import annotations

import re

import pytest

from anbu_care import service
from anbu_care.brief import compose_brief, render_brief_text
from anbu_care.brief.composer import UNKNOWN_PHRASE
from anbu_care.tools import insurer_tools, onboarding_tools, triage_tools


@pytest.fixture
def bare_parent() -> str:
    """A parent with a profile and nothing else — no policy, no contacts."""
    return onboarding_tools.create_parent_profile(
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=[],
    )["profile"]["parent_id"]


@pytest.fixture
def full_parent(bare_parent) -> str:
    onboarding_tools.record_insurance_policy(
        bare_parent, insurer="Star Health", policy_number="SH-1",
        sum_insured_inr=500_000, network_hospitals=["Sacred Heart Hospital"],
        cashless_eligible=True,
    )
    onboarding_tools.record_family_contact(
        bare_parent, name="Karthik", relationship="son", whatsapp_e164="+14155550142",
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=["status_updates"],
    )
    return bare_parent


def _all_facts(brief):
    return brief.facts + brief.actions_taken + brief.pending + brief.bring_with_you + brief.contacts


# =========================================================================
# THE ADVERSARIAL OMISSION RUN — the number this beat rests on
# =========================================================================


def test_deliberately_holed_state_yields_unknowns_and_zero_fabrications(bare_parent):
    """State is holed in every direction. Every hole must read "not yet known".

    Missing here: any triage, any claim packet, any discharge date, any
    adjudication, any co-pay figure, any policy, any family contact. A brief
    that invents a follow-up appointment, a discharge date, or an out-of-pocket
    number under these conditions is the most dangerous artifact this system
    can produce.
    """
    case = service.open_case(bare_parent)
    brief = compose_brief(case.case_id)

    must_be_unknown = {
        "Hospital",
        "Severity assessed",
        "Why that hospital",
        "Admitted on",
        "Expected discharge",
        "Claim outcome so far",
        "Likely out of pocket",
    }
    by_label = {f.label: f for f in brief.facts}
    for label in must_be_unknown:
        fact = by_label[label]
        assert fact.known is False, f"{label} claimed to be known on holed state"
        assert fact.value is None, f"{label} carried a value: {fact.value!r}"
        assert fact.source.kind == "unknown"
        assert fact.source.note, f"{label} gave no reason for being unknown"

    # No fact anywhere may carry a value without provenance.
    for fact in _all_facts(brief):
        if fact.known:
            assert fact.source.kind in {"receipt", "profile", "derived"}, fact
            assert fact.value

    text = render_brief_text(brief)
    assert text.count(UNKNOWN_PHRASE) >= len(must_be_unknown)


def test_holed_state_brief_contains_no_clinical_or_scheduling_invention(bare_parent):
    """Guard against the specific confabulations this feature invites."""
    case = service.open_case(bare_parent)
    text = render_brief_text(compose_brief(case.case_id)).lower()

    forbidden = [
        r"follow-?up on \w+day",     # "follow-up on Thursday"
        r"discharge (is )?expected",
        r"\bstable\b",
        r"\bimproving\b",
        r"\bdoctor will\b",
        r"\bappointment\b",
        r"\bprescrib",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, text), f"brief invented content matching {pattern!r}"


def test_every_known_fact_traces_to_a_receipt_or_a_stored_field(full_parent):
    """Provenance on every line is what makes this auditable rather than a summary."""
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    brief = compose_brief(triage["case_id"])
    chain_len = len(service.get_chain(triage["case_id"]).receipts)

    for fact in _all_facts(brief):
        if not fact.known:
            continue
        assert fact.source.kind in {"receipt", "profile", "derived"}
        if fact.source.kind == "receipt":
            assert fact.source.receipt_kind
            assert 0 <= fact.source.receipt_seq < chain_len


def test_no_partial_state_leaks_a_guess(full_parent):
    """Triage has run but no claim exists — money facts must stay unknown."""
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    brief = compose_brief(triage["case_id"])
    by_label = {f.label: f for f in brief.facts}

    assert by_label["Hospital"].known is True          # triage ran
    assert by_label["Severity assessed"].known is True
    assert by_label["Likely out of pocket"].known is False
    assert by_label["Expected discharge"].known is False
    assert by_label["Claim outcome so far"].known is False


# =========================================================================
# grounding
# =========================================================================


def test_brief_reflects_real_state_once_it_exists(full_parent):
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    case_id = triage["case_id"]
    doc = onboarding_tools.ingest_document(
        full_parent, kind="discharge_summary", source_filename="d.pdf",
        summary="Discharged 22 Aug.", observations=[],
    )["document"]["document_id"]
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=full_parent, admission_summary="ICU",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[doc],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, pkt["packet"]["packet_id"], "reimbursement")

    brief = compose_brief(case_id)
    by_label = {f.label: f for f in brief.facts}

    assert by_label["Hospital"].value == "Sacred Heart Hospital"
    assert by_label["Severity assessed"].value == "HIGH"
    assert by_label["Admitted on"].value == "2026-08-19"
    assert by_label["Expected discharge"].value == "2026-08-22"
    assert "66,000" in by_label["Likely out of pocket"].value
    assert "PARTIAL" in by_label["Claim outcome so far"].value
    assert "SIMULATED" in by_label["Claim outcome so far"].value


def test_open_insurer_query_becomes_a_bring_with_you_item(full_parent):
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    case_id = triage["case_id"]
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=full_parent, admission_summary="ICU",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, pkt["packet"]["packet_id"], "reimbursement")

    brief = compose_brief(case_id)
    assert any("discharge summary" in (f.value or "") for f in brief.pending)
    assert any(f.known and "Discharge Summary" in f.label for f in brief.bring_with_you)


def test_actions_taken_never_exceed_the_receipts(full_parent):
    """Every line of "what has been done" is a receipt that exists."""
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    brief = compose_brief(triage["case_id"])
    receipts = service.get_chain(triage["case_id"]).receipts
    assert len(brief.actions_taken) <= len(receipts)
    seqs = [f.source.receipt_seq for f in brief.actions_taken]
    assert seqs == sorted(seqs)
    assert all(s is not None for s in seqs)


# =========================================================================
# staleness
# =========================================================================


def test_brief_states_as_of_and_disclaims_live_monitoring(full_parent):
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    brief = compose_brief(triage["case_id"])
    text = render_brief_text(brief)

    assert "State as of" in text
    assert "Compiled" in text
    assert "snapshot, not a live view" in text
    assert "does not monitor" in text
    # as_of tracks the newest receipt, not the moment of compilation.
    newest = max(r.created_at for r in service.get_chain(triage["case_id"]).receipts)
    assert brief.as_of == newest


def test_as_of_moves_when_state_moves(full_parent):
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    first = compose_brief(triage["case_id"]).as_of
    service.append_receipt(triage["case_id"], kind="comms.sent", actor="whatsapp_agent",
                           payload={"body": "Admitted."})
    assert compose_brief(triage["case_id"]).as_of > first


def test_empty_case_reports_no_activity_rather_than_a_timestamp(bare_parent):
    """A case with no receipts at all must not imply anything happened."""
    brief_text = render_brief_text(compose_brief("case-does-not-exist"))
    assert "no activity recorded" in brief_text


# =========================================================================
# read-only
# =========================================================================


def test_composing_a_brief_does_not_change_the_case(full_parent):
    """Reading must never advance the episode it describes."""
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    case_id = triage["case_id"]
    before = service.get_chain(case_id)
    before_head, before_len = before.head_hash, len(before.receipts)
    before_stage = service.load_case(case_id).stage

    for _ in range(3):
        compose_brief(case_id)

    after = service.get_chain(case_id)
    assert after.head_hash == before_head
    assert len(after.receipts) == before_len
    assert service.load_case(case_id).stage == before_stage
    assert after.verify().ok


def test_brief_is_stable_across_repeated_reads(full_parent):
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    runs = [compose_brief(triage["case_id"]) for _ in range(3)]
    signatures = [[(f.label, f.value, f.known) for f in _all_facts(b)] for b in runs]
    assert all(sig == signatures[0] for sig in signatures)


def test_a_query_does_not_report_zero_out_of_pocket(full_parent):
    """An unpriced claim must not read as "you owe nothing".

    QUERY leaves total_disallowed_inr at 0 because nothing was priced. Printing
    that as INR 0 would be a false reassurance traced to a real field — the
    subtlest version of the fabrication this feature invites.
    """
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    case_id = triage["case_id"]
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=full_parent, admission_summary="ICU",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[],  # no discharge summary -> QUERY
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, pkt["packet"]["packet_id"], "reimbursement")

    by_label = {f.label: f for f in compose_brief(case_id).facts}
    out_of_pocket = by_label["Likely out of pocket"]
    assert out_of_pocket.known is False
    assert out_of_pocket.value is None
    assert "query" in out_of_pocket.source.note.lower()


def test_a_denied_claim_does_not_report_zero_out_of_pocket(full_parent):
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    case_id = triage["case_id"]
    doc = onboarding_tools.ingest_document(
        full_parent, kind="discharge_summary", source_filename="d.pdf",
        summary="", observations=[],
    )["document"]["document_id"]
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=full_parent, admission_summary="x",
        itemized_bills_inr={"toiletries": 900}, diagnostics=[],
        attached_document_ids=[doc],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, pkt["packet"]["packet_id"], "reimbursement")

    by_label = {f.label: f for f in compose_brief(case_id).facts}
    assert by_label["Likely out of pocket"].known is False


def test_a_priced_partial_does_report_the_figure(full_parent):
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    case_id = triage["case_id"]
    doc = onboarding_tools.ingest_document(
        full_parent, kind="discharge_summary", source_filename="d.pdf",
        summary="", observations=[],
    )["document"]["document_id"]
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=full_parent, admission_summary="ICU",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[doc],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, pkt["packet"]["packet_id"], "reimbursement")

    by_label = {f.label: f for f in compose_brief(case_id).facts}
    assert by_label["Likely out of pocket"].known is True
    assert "66,000" in by_label["Likely out of pocket"].value


# =========================================================================
# DEFINITE NEGATIVES — the opposite failure from fabrication
# =========================================================================
#
# "Nothing outstanding" and "not yet known" are different claims. Saying the
# second when you computed the first understates what the record holds. Saying
# the first when you computed the second is reassurance derived from absence,
# which is the omission guarantee running backwards and is far worse. Both
# directions are pinned here.


def test_unadjudicated_case_says_not_yet_known_never_nothing_outstanding(full_parent):
    """Before adjudication, an empty pending list means NOT LOOKED AT.

    This is the inversion that matters. A family reading "nothing outstanding"
    on a claim the insurer has not opened would be reassured by the absence of
    a check, not by its result.
    """
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    brief = compose_brief(triage["case_id"])

    pending = {f.label: f for f in brief.pending}
    bring = {f.label: f for f in brief.bring_with_you}

    assert pending["Open items"].known is False
    assert pending["Open items"].value is None
    assert bring["Documents to bring"].known is False
    assert bring["Documents to bring"].value is None

    rendered = render_brief_text(brief)
    assert "Nothing outstanding" not in rendered
    assert "Nothing requested" not in rendered


def test_holed_state_still_degrades_to_unknown_everywhere(bare_parent):
    """The adversarial-omission run must be untouched by definite negatives."""
    case_id = service.open_case(bare_parent).case_id
    brief = compose_brief(case_id)

    assert all(not f.known for f in brief.pending)
    assert all(not f.known for f in brief.bring_with_you)
    assert "Nothing outstanding" not in render_brief_text(brief)


def test_adjudicated_clean_claim_reports_the_negative_it_computed(full_parent):
    """Once adjudication has run and raised no query, the answer is known."""
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    case_id = triage["case_id"]
    doc = onboarding_tools.ingest_document(
        full_parent, kind="discharge_summary", source_filename="d.pdf",
        summary="Discharged 22 Aug.", observations=[],
    )["document"]["document_id"]
    pkt = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=full_parent, admission_summary="ICU",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=["ECG"],
        attached_document_ids=[doc],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    insurer_tools.submit_claim(case_id, pkt["packet"]["packet_id"], "reimbursement")

    brief = compose_brief(case_id)
    bring = {f.label: f for f in brief.bring_with_you}

    # The insurer raised no document request, and that is a computed answer.
    assert bring["Documents to bring"].known is True
    assert bring["Documents to bring"].value == "Nothing requested"
    # And it cites the receipt that establishes it, not thin air.
    assert bring["Documents to bring"].source.kind == "receipt"
    assert bring["Documents to bring"].source.receipt_kind == "claim.adjudicated"


def test_a_definite_negative_never_counts_as_an_unknown(full_parent):
    """The count must drop because state exists, not because wording changed."""
    triage = triage_tools.run_triage(
        parent_id=full_parent, symptoms=["chest pain"], free_text="",
        reported_by="neighbour", lat=0.0, lon=0.0, case_id="",
    )
    before = compose_brief(triage["case_id"])
    unknown_labels = {f.label for f in _all_facts(before) if not f.known}
    assert "Open items" in unknown_labels
    assert "Documents to bring" in unknown_labels
