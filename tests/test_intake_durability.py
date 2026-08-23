"""A photograph must outlive the instance that was reading it.

The acknowledgement promises a second message. Before this, the only thing
holding the system to that promise was a BackgroundTask and the image bytes in
one container's memory — so a deploy six seconds before a bill arrived killed
the read nine seconds in, and the family got "reading it now" and then nothing.
Firestore had no document, no bill and no case for it: the photograph was gone.

These cover the properties that make that recoverable, and the one that keeps
it honest when it is not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anbu_care import intake as intake_ledger
from anbu_care import service
from anbu_care.provenance.store import get_store
from anbu_care.schemas import FamilyContact, ParentProfile

PNG = (b"\x89PNG\r\n\x1a\n" + b"x" * 2000)


@pytest.fixture
def parent_id() -> str:
    pid = service.new_id("parent")
    service.save_profile(ParentProfile(
        parent_id=pid, name="Ashanthi Machado", age=71, city="Thoothukudi",
        lat=8.7642, lon=78.1348,
        family_contacts=[FamilyContact(
            name="Heartlin Machado", relationship="son",
            whatsapp_e164="+16692167706", timezone="America/Chicago",
            is_primary=True, consent_purposes=["status_updates", "billing_updates"],
        )],
    ))
    return pid


@pytest.fixture
def bucket(monkeypatch):
    """A stand-in for the artifact bucket, so nothing here touches GCS."""
    objects: dict[str, bytes] = {}

    from anbu_care.comms import storage

    def fake_store(filename, data, content_type="application/pdf"):
        name = f"artifacts/{filename}"
        objects[name] = data
        return storage.StoredArtifact(stored=True, url="https://example/x",
                                      object_name=name, detail="stored")

    monkeypatch.setattr(storage, "store", fake_store)
    monkeypatch.setattr(storage, "fetch", lambda name: objects.get(name))
    return objects


def test_the_photograph_is_kept_before_the_family_is_told_it_is_being_read(
        parent_id, bucket):
    """The row and the image both exist the moment the acknowledgement goes."""
    held = intake_ledger.record(parent_id, "case-x", PNG, "image/png")

    assert held is not None
    assert bucket[held.object_name] == PNG, "the image is not recoverable"
    assert held.status == intake_ledger.OPEN
    assert intake_ledger.image_for(held) == PNG


def test_a_read_that_never_finished_is_swept_up_again(parent_id, bucket):
    """The exact incident: an instance dies mid-read and leaves the row open."""
    held = intake_ledger.record(parent_id, "case-x", PNG, "image/png")
    assert intake_ledger.claim(held, "revision-A") is True

    # Revision A is killed here. Nothing closes the row, and the lease it took
    # expires unrenewed.
    later = datetime.now(UTC) + intake_ledger.LEASE + timedelta(seconds=10)

    waiting = intake_ledger.stale(now=later)
    assert [i.intake_id for i in waiting] == [held.intake_id]
    assert intake_ledger.claim(waiting[0], "revision-B", now=later) is True


def test_a_read_still_running_is_not_taken_from_itself(parent_id, bucket):
    """A slow read is slow, not dead. Sweeping it would double the model calls."""
    held = intake_ledger.record(parent_id, "case-x", PNG, "image/png")
    intake_ledger.claim(held, "revision-A")

    # Thirty seconds in: past the staleness threshold, inside the lease.
    mid_read = datetime.now(UTC) + timedelta(seconds=70)
    assert intake_ledger.stale(now=mid_read) == []


def test_a_finished_read_is_never_swept(parent_id, bucket):
    held = intake_ledger.record(parent_id, "case-x", PNG, "image/png")
    intake_ledger.claim(held, "revision-A")
    intake_ledger.finish(held)

    long_after = datetime.now(UTC) + timedelta(hours=2)
    assert intake_ledger.stale(now=long_after) == []


def test_two_instances_cannot_both_hold_the_same_photograph(parent_id, bucket):
    held = intake_ledger.record(parent_id, "case-x", PNG, "image/png")

    assert intake_ledger.claim(held, "revision-A") is True
    assert intake_ledger.claim(held, "revision-B") is False


def test_it_gives_up_out_loud_rather_than_retrying_forever(parent_id, bucket):
    """A row that stopped being worked on looks exactly like one never received."""
    intake_ledger.record(parent_id, "case-x", PNG, "image/png")

    for attempt in range(intake_ledger.MAX_ATTEMPTS):
        moment = datetime.now(UTC) + timedelta(hours=attempt + 1)
        current = intake_ledger.stale(now=moment)[0]
        intake_ledger.claim(current, "revision", now=moment)

    exhausted = intake_ledger.stale(now=datetime.now(UTC) + timedelta(hours=9))
    assert exhausted[0].exhausted is True, "it would retry forever"


def test_no_bucket_means_no_false_promise_of_durability(parent_id, monkeypatch):
    """Silently non-durable is worse than plainly non-durable."""
    from anbu_care.comms import storage

    monkeypatch.setattr(storage, "store", lambda *a, **k: storage.StoredArtifact(
        stored=False, url=None, detail="ANBU_ARTIFACT_BUCKET is not set"))

    assert intake_ledger.record(parent_id, "case-x", PNG, "image/png") is None


def test_the_row_stays_open_when_the_read_raises(parent_id, bucket, monkeypatch):
    """A failed attempt is not a finished one."""
    from anbu_care import server

    held = intake_ledger.record(parent_id, "case-x", PNG, "image/png")

    def boom(*args, **kwargs):
        raise RuntimeError("model call died")

    monkeypatch.setattr(server, "_read_bill_and_report", boom)

    with pytest.raises(RuntimeError):
        server._read_intake("case-x", parent_id, PNG, "image/png", held)

    row = get_store().get(held.pk, held.sk)
    assert row["status"] == intake_ledger.OPEN, "a failure closed the row"


def test_the_row_closes_once_a_message_has_gone_out(parent_id, bucket, monkeypatch):
    from anbu_care import server

    held = intake_ledger.record(parent_id, "case-x", PNG, "image/png")
    monkeypatch.setattr(server, "_read_bill_and_report", lambda *a, **k: None)

    server._read_intake("case-x", parent_id, PNG, "image/png", held)

    row = get_store().get(held.pk, held.sk)
    assert row["status"] == intake_ledger.DONE


def test_the_photograph_is_stored_before_the_acknowledgement_is_returned():
    """Order is the whole fix. Acknowledging first re-opens the original hole."""
    import inspect

    from anbu_care import server

    source = inspect.getsource(server._handle_bill_photo)
    stored_at = source.index("intake_ledger.record")
    acknowledged_at = source.index("Got that. Reading it now")
    assert stored_at < acknowledged_at, "the promise is made before it is held"


def test_the_family_clock_is_configured_not_hardcoded(monkeypatch):
    """"1:17 PM your time" against a phone reading 3:18 PM reads as a stale alert.

    Every alert quotes both ends of the clock — the reader's afternoon and
    Thoothukudi's small hours — because one without the other means nothing.
    Pinned to Pacific in the source, it misstated the reader's own end by two
    hours for a message ninety seconds old.
    """
    from anbu_care import server

    monkeypatch.setenv("ANBU_DEMO_FAMILY_TZ", "America/Chicago")
    assert server._demo_family_timezone() == "America/Chicago"

    monkeypatch.setenv("ANBU_DEMO_FAMILY_TZ", "Asia/Kolkata")
    assert server._demo_family_timezone() == "Asia/Kolkata"


def test_an_unusable_zone_degrades_to_utc_rather_than_another_wrong_one(monkeypatch):
    from anbu_care import server

    monkeypatch.setenv("ANBU_DEMO_FAMILY_TZ", "Pacific/Nowhere")
    assert server._demo_family_timezone() == "UTC"


def test_the_zone_is_no_longer_written_into_the_source():
    import inspect

    from anbu_care import server

    source = inspect.getsource(server)
    assert 'timezone_name="America/Los_Angeles"' not in source, "still pinned"


def test_the_sweeper_actually_runs_under_the_real_lifespan(monkeypatch):
    """Registered is not running.

    The first version of this used @app.on_event("startup"). It registered
    cleanly, ruff passed, the suite passed — and it never fired once, because
    ADK supplies its own lifespan and Starlette ignores on_startup handlers
    when one is set. A recovery mechanism that is never invoked is worse than
    none, because it reads like the problem is handled.
    """
    import time
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from anbu_care import server

    fired: list[int] = []
    monkeypatch.setattr(server, "_sweep_intakes", lambda *a, **k: fired.append(1))
    monkeypatch.setattr(server.intake_ledger, "LEASE", timedelta(seconds=0))

    with TestClient(server.app) as client:
        assert client.get("/api/healthz").status_code == 200
        time.sleep(7)

    assert fired, "the startup sweep never ran"


# ---- seeding twice is seeding once ---------------------------------------


def test_seeding_twice_does_not_mint_a_second_family(monkeypatch):
    """Eighty-three parent profiles accumulated one re-seed at a time.

    Each seed created a new parent and repointed the family's WhatsApp number
    at it, so the previous record kept its cases, receipts and documents behind
    a parent nothing resolved to any more. A demo then read its settings off
    whichever record happened to be seeded last, which is how a contact set to
    English sent Tamil.
    """
    from fastapi.testclient import TestClient

    from anbu_care import server, service

    monkeypatch.setenv("ANBU_DEMO_FAMILY_E164", "+16692167706")

    with TestClient(server.app) as client:
        first = client.post("/api/demo/seed").json()["parent_id"]
        second = client.post("/api/demo/seed").json()["parent_id"]

    assert first == second, "a re-seed orphaned the first family"
    assert service.lookup_whatsapp_number("+16692167706")["parent_id"] == first


def test_a_re_seed_keeps_the_history_already_on_the_record(monkeypatch):
    """The point of not minting a new one: the old case is still reachable."""
    from fastapi.testclient import TestClient

    from anbu_care import server, service

    monkeypatch.setenv("ANBU_DEMO_FAMILY_E164", "+16692167706")

    with TestClient(server.app) as client:
        parent_id = client.post("/api/demo/seed").json()["parent_id"]
        case = service.open_case(parent_id)
        client.post("/api/demo/seed")

    assert service.load_case(case.case_id) is not None
    assert service.latest_case_for_parent(parent_id).case_id == case.case_id


def test_recording_the_same_number_twice_corrects_it_rather_than_duplicating(monkeypatch):
    """Three identical sons is three copies of every care-circle message.

    Appearing twice in a roster is not two people. The number is the identity,
    because that is what inbound WhatsApp resolves and what outbound sends to.
    """
    from anbu_care import service
    from anbu_care.tools import onboarding_tools

    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]

    for language in ("en", "ta", "en+ta"):
        onboarding_tools.record_family_contact(
            parent_id=pid, name="Heartlin Machado", relationship="son",
            whatsapp_e164="+16692167706", timezone_name="America/Chicago",
            is_primary=True, consent_purposes=["status_updates"], language=language,
        )

    contacts = service.load_profile(pid).family_contacts
    assert len(contacts) == 1, f"{len(contacts)} entries for one person"
    assert contacts[0].language == "en+ta", "the correction did not take"


def test_a_second_person_is_still_a_second_contact(monkeypatch):
    """The de-duplication must not collapse a family into one member."""
    from anbu_care import service
    from anbu_care.tools import onboarding_tools

    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7, lon=78.1,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]

    for name, number in (("Heartlin", "+16692167706"), ("Priya", "+919000000077")):
        onboarding_tools.record_family_contact(
            parent_id=pid, name=name, relationship="family",
            whatsapp_e164=number, timezone_name="Asia/Kolkata",
            is_primary=False, consent_purposes=["status_updates"], language="en",
        )

    assert len(service.load_profile(pid).family_contacts) == 2
