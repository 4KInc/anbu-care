"""What the clean-up is allowed to delete, and what it must never touch.

This is a deletion tool pointed at a medical record, so the interesting tests
are the refusals. Five photographs of one discharge summary are rehearsal
debris. A second discharge summary from a second admission is her history, and
it looks almost identical: same kind, same hospital, same patient, different
dates. Getting that distinction wrong deletes a hospital admission.

The rule under test is that identity is read off the PAGE - the dates the
document itself carries - and never off the image hash, because five
photographs of one paper have five hashes and that is the entire reason the
duplicates exist.
"""

from __future__ import annotations

import pytest

from anbu_care import service
from anbu_care.schemas import DocumentKind, ParsedDocument
from anbu_care.tools import onboarding_tools
from scripts.clear_rehearsal_debris import plan

AUGUST = {"admitted_on": "2026-08-19", "discharged_on": "2026-08-22",
          "hospital": "Sacred Heart Hospital"}
MARCH = {"admitted_on": "2026-03-02", "discharged_on": "2026-03-05",
         "hospital": "Sacred Heart Hospital"}


@pytest.fixture
def parent_id() -> str:
    return onboarding_tools.create_parent_profile(
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]


def _discharge(parent_id: str, details: dict) -> str:
    doc = ParsedDocument(
        document_id=service.new_id("doc"), parent_id=parent_id,
        kind=DocumentKind.DISCHARGE_SUMMARY,
        summary=f"Discharge summary ({details['admitted_on']} to "
                f"{details['discharged_on']}).",
        details=dict(details))
    service.save_document(doc)
    return doc.document_id


def _doomed_documents(doomed: list[tuple]) -> set[str]:
    return {sk.split("#", 1)[1] for _pk, sk, _ident, _extra in doomed
            if sk.startswith("DOC#")}


def test_five_photographs_of_one_admission_leave_one(parent_id):
    """The demo's own mess. Five takes, five different photographs of the same
    paper, five rows on the Record tab for one admission."""
    ids = [_discharge(parent_id, AUGUST) for _ in range(5)]

    doomed, survivors, _stats = plan(parent_id)

    assert len(survivors) == 1
    assert survivors[0]["of"] == 5
    assert len(_doomed_documents(doomed)) == 4
    assert survivors[0]["keeps"] in ids


def test_a_second_admission_is_not_debris(parent_id):
    """She was admitted in March and again in August. Two discharges, two
    documents, and deleting either would delete a hospital admission."""
    march = _discharge(parent_id, MARCH)
    august = _discharge(parent_id, AUGUST)

    doomed, survivors, _stats = plan(parent_id)

    assert doomed == []
    assert {s["keeps"] for s in survivors} == {march, august}


def test_the_only_record_of_an_admission_is_never_dropped(parent_id):
    """A lone leftover from an earlier rehearsal is indistinguishable from her
    genuine history. Guessing is not something a record system may do, so the
    single row is kept and the operator is left to run this after the take,
    when there are two and the live window says which is which."""
    _discharge(parent_id, AUGUST)

    doomed, survivors, _stats = plan(parent_id)

    assert doomed == []
    assert survivors[0]["of"] == 1


def test_the_document_the_live_window_names_is_the_one_kept(parent_id):
    """Not simply the newest row.

    A receipt and an open recovery window both name a document id. Keeping some
    other member of the group would leave the live window pointing at a row
    that no longer exists, which is a dangling reference on the one record this
    system exists to keep straight.
    """
    from anbu_care.recovery import window as recovery

    older = _discharge(parent_id, AUGUST)
    _newer = _discharge(parent_id, AUGUST)
    case = service.open_case(parent_id)
    recovery.open_window(parent_id, case.case_id, discharged_on="2026-08-22",
                         document_id=older)

    doomed, survivors, _stats = plan(parent_id)

    assert survivors[0]["keeps"] == older, "the live window's document was dropped"
    assert _doomed_documents(doomed) == {_newer}


def test_windows_from_earlier_takes_go_with_their_prompt_slots(parent_id):
    """An earlier take leaves a window open on a case the demo has moved past.

    Every one of them is counted by parents_with_open_windows and listed on the
    recovery view, and the prompt slot rows underneath them can never be read
    again once their window is gone.
    """
    from anbu_care.recovery import window as recovery

    stale_case = service.open_case(parent_id)
    stale = recovery.open_window(parent_id, stale_case.case_id,
                                 discharged_on="2026-08-22")
    recovery.claim_slot(parent_id, _due(stale), "rp-1", {"delivered": True})

    live_case = service.open_case(parent_id)
    live = recovery.open_window(parent_id, live_case.case_id,
                                discharged_on="2026-08-22")

    doomed, _survivors, stats = plan(parent_id)

    assert stats["keeps_window"] == live.window_id
    sks = {sk for _pk, sk, _i, _x in doomed}
    assert f"RECOVERY#WINDOW#{stale.window_id}" in sks
    assert any(sk.startswith(f"RECOVERY#PROMPT#{stale.window_id}") for sk in sks)
    assert f"RECOVERY#WINDOW#{live.window_id}" not in sks


def _due(window):
    """A due slot for a window, without waiting for nine in the morning."""
    from anbu_care.recovery.window import Due, prompt_sk

    on = window.starts_on
    return Due(window=window, day=1, on=on, slot=prompt_sk(window.window_id, on))
