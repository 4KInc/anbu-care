"""Diagnostic referral: what a present son does, and what he refuses to do.

The doctor orders a test. A son in Nashville looks up where it can be done,
checks what he honestly can about coverage, and sends the list. He does not
diagnose her, does not decide from another continent whether she can travel,
does not book anything, and does not tell her the insurer will pay when he has
not asked them.

These are the walls, one test each, plus the paths that must keep working.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from anbu_care import service
from anbu_care.diagnostics import places as places_api
from anbu_care.diagnostics import referral
from anbu_care.schemas import FamilyContact, InsurancePolicy, ParentProfile

HOSPITAL_LAT, HOSPITAL_LON = 8.8140478, 78.1468969


# ---- the real shape Places returns, captured from a live call -------------
LIVE_SAMPLE = {
    "places": [
        {"id": "ChIJpS-F-MPvAzsRAYdm2Uf2K3k",
         "displayName": {"text": "AARTHI SCANS & LABS | TUTICORIN"},
         "formattedAddress": "Tuticorin, Tamil Nadu",
         "location": {"latitude": 8.7992, "longitude": 78.1332},
         "primaryType": "medical_clinic"},
        {"id": "ChIJ3TVikMXvAzsR6XXy_sr_RKU",
         "displayName": {"text": "DLABS Diagnostics and DG Medical Centre"},
         "formattedAddress": "Thoothukudi, Tamil Nadu",
         "location": {"latitude": 8.8204, "longitude": 78.1319},
         "primaryType": "medical_lab"},
        {"id": "ChIJclosed", "displayName": {"text": "Shut Down Labs"},
         "formattedAddress": "Thoothukudi", "businessStatus": "CLOSED_PERMANENTLY",
         "location": {"latitude": 8.80, "longitude": 78.14},
         "primaryType": "medical_lab"},
    ]
}


@pytest.fixture
def live_places(monkeypatch):
    """Stand in for Places at the one network seam, with its real response shape."""
    monkeypatch.setenv("ANBU_DIAGNOSTICS_MODE", "places")
    monkeypatch.setenv("ANBU_PLACES_API_KEY", "test-key")

    calls = []

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(LIVE_SAMPLE).encode()

    def fake_urlopen(request, timeout=0):
        calls.append(json.loads(request.data.decode()))
        return _Response()

    monkeypatch.setattr(places_api.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(places_api.json, "load", lambda r: json.loads(r.read()))
    return calls


@pytest.fixture
def case(monkeypatch):
    pid = service.new_id("parent")
    service.save_profile(ParentProfile(
        parent_id=pid, name="Ashanthi Machado", age=71, city="Thoothukudi",
        lat=8.7642, lon=78.1400,
        policy=InsurancePolicy(insurer="Star Health", policy_number="SH-1",
                               sum_insured_inr=500_000),
        family_contacts=[FamilyContact(
            name="Heartlin Machado", relationship="son",
            whatsapp_e164="+16692167706", is_primary=True,
            consent_purposes=["status_updates"])],
    ))
    # Her own agreement that a treating team may read her record. Without it a
    # handoff link cannot be minted at all, which is correct and is its own
    # test elsewhere.
    from anbu_care.tools import onboarding_tools

    onboarding_tools.record_emergency_disclosure_consent(pid)
    return pid, service.open_case(pid).case_id


# =========================================================================
# WALL 1 — THE ORDER COMES FROM THE CLINICIAN
# =========================================================================


def test_the_order_is_recorded_by_the_clinician_not_generated(case, live_places):
    """Anbu Care never originates an order. It carries one."""
    from anbu_care.handoff.access import HandoffGrant

    parent_id, case_id = case
    grant = HandoffGrant(case_id=case_id, parent_id=parent_id,
                         expires_at=2 ** 31, may_write_note=True)

    from anbu_care.handoff import notes

    result = notes.confirm(grant, "Needs a troponin repeat in the morning.",
                           recorded_by="Dr A. Anand",
                           orders_test="Troponin I (repeat)")

    order = service.load_diagnostic_order(case_id, result["order_id"])
    assert order.test_label == "Troponin I (repeat)"
    assert order.ordered_by == "Dr A. Anand"
    assert order.note_receipt_id == result["receipt_id"]


def test_an_ordinary_note_orders_nothing(case):
    """The note path is unchanged when no test is named."""
    from anbu_care.handoff import notes
    from anbu_care.handoff.access import HandoffGrant

    parent_id, case_id = case
    grant = HandoffGrant(case_id=case_id, parent_id=parent_id,
                         expires_at=2 ** 31, may_write_note=True)

    result = notes.confirm(grant, "Resting comfortably overnight.",
                           recorded_by="Dr A. Anand")

    assert result["order_id"] == ""
    assert service.list_diagnostic_orders(case_id) == []


def test_options_are_refused_without_an_order(case, live_places):
    """A referral with no clinician behind it does not exist."""
    from fastapi.testclient import TestClient

    from anbu_care.server import app

    _parent_id, case_id = case
    with TestClient(app) as client:
        r = client.post(f"/api/cases/{case_id}/diagnostics/dxorder-nope/options",
                        headers={"Authorization": "Bearer anbu-demo-family-token"})
    assert r.status_code in (401, 403, 409)
    if r.status_code == 409:
        assert "does not order tests" in r.json()["detail"]


# =========================================================================
# WALL 2 — NO COVERAGE PROMISE
# =========================================================================


def test_nothing_in_a_referral_says_covered(case, live_places):
    """The word, and every near neighbour of it, in the whole payload.

    A wrong promise here sends a seventy-one year old to pay for a scan she was
    told was free, which is the exact opposite of what the son wanted.
    """
    surfaced = referral.options_for(test_label="Troponin I", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, insurer="Star Health")
    blob = json.dumps(surfaced).lower()

    for forbidden in ("covered", "is covered", "your insurance covers",
                      "will be paid", "guaranteed", "cashless is available"):
        assert forbidden not in blob, f"a referral claimed coverage: {forbidden!r}"

    for option in surfaced["options"]:
        assert "covered" not in option, "an invented coverage field exists"
        assert "confirm coverage with your insurer" in option["network_note"].lower()


def test_network_status_uses_the_routing_modules_exact_words(case, live_places):
    """"listed as empanelled", never "is empanelled". Same source, same phrasing."""
    from anbu_care.kb.hospitals import load_hospitals

    known = load_hospitals()[0]
    matched = {"places": [{"id": "p1", "displayName": {"text": known.name},
                           "formattedAddress": "Thoothukudi",
                           "location": {"latitude": known.lat, "longitude": known.lon},
                           "primaryType": "medical_lab"}]}
    LIVE_SAMPLE_BACKUP = dict(LIVE_SAMPLE)
    try:
        LIVE_SAMPLE.clear()
        LIVE_SAMPLE.update(matched)
        surfaced = referral.options_for(test_label="ECG", lat=known.lat,
                                        lon=known.lon,
                                        insurer=known.empanelled_insurers[0])
    finally:
        LIVE_SAMPLE.clear()
        LIVE_SAMPLE.update(LIVE_SAMPLE_BACKUP)

    note = surfaced["options"][0]["network_note"]
    assert "is listed as empanelled with" in note
    assert "is empanelled with" not in note.replace("is listed as empanelled with", "")


def test_a_centre_we_know_nothing_about_says_so(case, live_places):
    """Most diagnostic centres are not in a five-hospital knowledge base."""
    surfaced = referral.options_for(test_label="MRI", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, insurer="Star Health")
    note = surfaced["options"][0]["network_note"]
    assert "no network information" in note.lower()


def test_the_source_of_the_list_is_named(case, live_places):
    """A live search and a seeded snapshot are different claims."""
    surfaced = referral.options_for(test_label="MRI", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, insurer=None)
    assert surfaced["source"] == "google_places_live"
    assert "live Google Places search" in surfaced["source_label"]
    assert "SEEDED SNAPSHOT" in surfaced["kb_provenance"]


# =========================================================================
# WALL 3 — NO MOBILITY VERDICT, EVER
# =========================================================================


def test_anbu_care_sets_no_ambulatory_verdict(case, live_places):
    """Whether she can travel is a fact about a person in a room.

    Unstated stays unstated. It is not inferred from severity, not defaulted to
    ambulatory because most people are, and not filled in later.
    """
    from anbu_care.handoff import notes
    from anbu_care.handoff.access import HandoffGrant

    parent_id, case_id = case
    grant = HandoffGrant(case_id=case_id, parent_id=parent_id,
                         expires_at=2 ** 31, may_write_note=True)

    result = notes.confirm(grant, "Please arrange bloods.", recorded_by="Dr A. Anand",
                           orders_test="Full blood count")
    order = service.load_diagnostic_order(case_id, result["order_id"])
    assert order.mobility == "unknown"

    grouped = referral.group_by_mobility([], order.mobility)
    assert grouped["mobility_as_stated"] == "unknown"
    assert "not Anbu Care" in grouped["mobility_note"]


def test_an_unrecognised_mobility_value_is_not_guessed_at(case):
    from anbu_care.handoff import notes
    from anbu_care.handoff.access import HandoffGrant

    parent_id, case_id = case
    grant = HandoffGrant(case_id=case_id, parent_id=parent_id,
                         expires_at=2 ** 31, may_write_note=True)

    result = notes.confirm(grant, "Bloods please.", recorded_by="Dr A. Anand",
                           orders_test="Full blood count", mobility="probably fine")
    order = service.load_diagnostic_order(case_id, result["order_id"])
    assert order.mobility == "unknown", "a guess was written into the record"


def test_both_paths_are_shown_when_the_clinician_did_not_say(case, live_places):
    """Options, not a decision. The people in the room choose."""
    surfaced = referral.options_for(test_label="MRI", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, insurer=None)
    grouped = referral.group_by_mobility(surfaced["options"], "unknown")

    assert "travel" in grouped and "home_collection" in grouped
    assert "depends on whether she can travel" in grouped["mobility_note"]
    # An empty home-collection group is an absence of information, not a no.
    assert "not a confirmed no" in grouped["home_collection_note"]


def test_the_clinicians_own_words_are_carried_when_they_did_say(case):
    from anbu_care.handoff import notes
    from anbu_care.handoff.access import HandoffGrant

    parent_id, case_id = case
    grant = HandoffGrant(case_id=case_id, parent_id=parent_id,
                         expires_at=2 ** 31, may_write_note=True)

    result = notes.confirm(grant, "Cannot get to a lab.", recorded_by="Dr A. Anand",
                           orders_test="Full blood count", mobility="non_ambulatory")
    order = service.load_diagnostic_order(case_id, result["order_id"])
    assert order.mobility == "non_ambulatory"

    grouped = referral.group_by_mobility([], "non_ambulatory")
    assert "The clinician recorded" in grouped["mobility_note"]


# =========================================================================
# NOTHING IS ARRANGED
# =========================================================================


def test_nothing_claims_a_centre_was_contacted(case, live_places):
    surfaced = referral.options_for(test_label="MRI", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, insurer=None)
    blob = json.dumps(surfaced).lower()
    # The denial says "nothing here is booked", so the check is for an
    # AFFIRMATIVE claim rather than the word itself.
    for forbidden in ("has been booked", "we booked", "appointment at",
                      "scheduled for", "reserved for", "we have contacted",
                      "lab was engaged"):
        assert forbidden not in blob, f"a referral claimed to arrange: {forbidden!r}"
    assert "not connected to any of these centres" in surfaced["not_arranged"]
    assert "Nothing here is booked" in surfaced["not_arranged"]


def test_a_permanently_closed_centre_is_not_an_option(case, live_places):
    """Places says so. Passing it on sends somebody to a locked door."""
    surfaced = referral.options_for(test_label="MRI", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, insurer=None)
    assert all(o["name"] != "Shut Down Labs" for o in surfaced["options"])


def test_the_search_uses_the_clinicians_words_unrewritten(case, live_places):
    referral.options_for(test_label="Troponin I (repeat)", lat=HOSPITAL_LAT,
                         lon=HOSPITAL_LON, insurer=None)
    assert "Troponin I (repeat)" in live_places[0]["textQuery"]


def test_a_failed_search_does_not_become_a_seeded_list(monkeypatch, case):
    """A live label over seeded data is the lie this guards against."""
    monkeypatch.setenv("ANBU_DIAGNOSTICS_MODE", "places")
    monkeypatch.setenv("ANBU_PLACES_API_KEY", "test-key")

    def boom(request, timeout=0):
        raise TimeoutError()

    monkeypatch.setattr(places_api.urllib.request, "urlopen", boom)

    with pytest.raises(referral.ReferralRefused):
        referral.options_for(test_label="MRI", lat=HOSPITAL_LAT, lon=HOSPITAL_LON)


# =========================================================================
# THE RECEIPT
# =========================================================================


def test_the_receipt_leaks_no_clinical_content(case, live_places):
    """/verify is public. "She was sent for a troponin test" must not be on it."""
    _parent_id, case_id = case
    surfaced = referral.options_for(test_label="Troponin I", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, insurer="Star Health")
    grouped = referral.group_by_mobility(surfaced["options"], "unknown")
    referral.record(case_id=case_id, order_id="dxorder-1",
                    test_label="Troponin I", surfaced=surfaced, grouped=grouped)

    receipt = service.get_chain(case_id).receipts[-1]
    assert receipt.kind == "diagnostic.referral"
    blob = json.dumps(receipt.payload).lower()
    assert "troponin" not in blob, "the test name reached the public chain"
    assert receipt.payload["option_count"] == len(surfaced["options"])
    assert receipt.payload["option_place_ids"]


def test_the_referral_is_additive_and_the_chain_still_verifies(case, live_places):
    _parent_id, case_id = case
    before = len(service.get_chain(case_id).receipts)

    surfaced = referral.options_for(test_label="MRI", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, insurer=None)
    grouped = referral.group_by_mobility(surfaced["options"], "unknown")
    referral.record(case_id=case_id, order_id="dxorder-1", test_label="MRI",
                    surfaced=surfaced, grouped=grouped)

    assert len(service.get_chain(case_id).receipts) == before + 1
    assert service.verify_case(case_id).ok is True


def test_the_referral_renders_on_the_trace(case, live_places):
    """A real agent action, visible — like the claim gather that was invisible."""
    from anbu_care.trace import compose_trace

    _parent_id, case_id = case
    surfaced = referral.options_for(test_label="MRI", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, insurer=None)
    grouped = referral.group_by_mobility(surfaced["options"], "unknown")
    referral.record(case_id=case_id, order_id="dxorder-1", test_label="MRI",
                    surfaced=surfaced, grouped=grouped)

    trace = compose_trace(case_id)
    step = next(s for s in trace.steps if s.kind == "diagnostic.referral")
    assert "clinician-ordered test" in step.what
    assert "Nothing was booked" in step.detail
    assert len(trace.steps) == trace.receipt_count, "the trace invented a beat"


# =========================================================================
# THE NOTIFY IS LOGISTICS, AND DOES NOT NAME THE TEST
# =========================================================================


def test_the_notify_never_names_the_test(case):
    """Run against the real classifier, "ECG" and "troponin I" are refused as
    clinical detail — correctly. So the message does not carry them."""
    from anbu_care.comms.policy import TEMPLATES, classify_message
    from anbu_care.schemas import MessageClass

    spec = TEMPLATES["diagnostic_options_ready"]
    assert spec["message_class"] is MessageClass.LOGISTICS
    assert "{test" not in spec["body"], "the template names the test"

    body = spec["body"].format(clinician="Dr A. Anand", parent_name="Ashanthi",
                               option_count="6", dashboard_url="https://x/app")
    actual, hits = classify_message(body, MessageClass.LOGISTICS)
    assert actual is not MessageClass.CLINICAL, hits
    assert "has not booked" in body


def test_a_far_away_centre_is_never_offered_as_nearby(case, monkeypatch):
    """A live search once put a lab in Noida at the top of a "nearby" list.

    2,205 km, because a location bias is a bias and a national brand matched
    the assay name. Anchoring the query to the city fixed it — but a query
    heuristic is not a guarantee, and this is the guarantee.
    """
    monkeypatch.setenv("ANBU_DIAGNOSTICS_MODE", "places")
    monkeypatch.setenv("ANBU_PLACES_API_KEY", "test-key")

    far = {"places": [
        {"id": "ChIJnoida", "displayName": {"text": "Redcliffe Labs - Noida"},
         "formattedAddress": "Noida, Uttar Pradesh",
         "location": {"latitude": 28.5355, "longitude": 77.3910},
         "primaryType": "medical_lab"},
        {"id": "ChIJlocal", "displayName": {"text": "Bethesda Lab"},
         "formattedAddress": "Thoothukudi",
         "location": {"latitude": 8.8070, "longitude": 78.1487},
         "primaryType": "medical_lab"},
    ]}
    monkeypatch.setattr(places_api, "_search_places",
                        lambda **kw: places_api.SearchResult(
                            ok=True, source="google_places_live",
                            source_label=places_api.LIVE_LABEL,
                            centres=[c for c in
                                     (places_api._centre_from(p) for p in far["places"])
                                     if c]))

    surfaced = referral.options_for(test_label="Troponin I", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, city="Thoothukudi")

    names = [o["name"] for o in surfaced["options"]]
    assert "Redcliffe Labs - Noida" not in names, "a lab 2,205 km away was offered"
    assert names == ["Bethesda Lab"]
    assert surfaced["dropped_as_too_far"] == 1


def test_the_city_anchors_the_query(case, live_places):
    """Without it the bias is not enough and Places goes national."""
    referral.options_for(test_label="Troponin I (repeat)", lat=HOSPITAL_LAT,
                         lon=HOSPITAL_LON, city="Thoothukudi")
    query = live_places[0]["textQuery"]
    assert "Troponin I (repeat)" in query, "the clinician's words were rewritten"
    assert "Thoothukudi" in query, "the query was not anchored to the city"


def test_nothing_local_is_refused_rather_than_widened(case, monkeypatch):
    """Better no list than a list of places nobody can get her to."""
    monkeypatch.setenv("ANBU_DIAGNOSTICS_MODE", "places")
    monkeypatch.setenv("ANBU_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(places_api, "_search_places",
                        lambda **kw: places_api.SearchResult(
                            ok=True, source="google_places_live",
                            source_label=places_api.LIVE_LABEL,
                            centres=[places_api.Centre(
                                place_id="p", name="Far Lab", address="Delhi",
                                lat=28.6, lon=77.2, primary_type="medical_lab")]))

    with pytest.raises(referral.ReferralRefused) as refused:
        referral.options_for(test_label="MRI", lat=HOSPITAL_LAT, lon=HOSPITAL_LON,
                             city="Thoothukudi")
    assert "farther away and were not offered" in str(refused.value)


# =========================================================================
# THE CLINICIAN CAN ACTUALLY REACH IT
# =========================================================================


def _client_page():
    """The dashboard as it is served."""
    return pathlib.Path("anbu_care/webui/index.html").read_text()


def _summary_stub():
    """The shape `_handoff_html` reads, with everything absent.

    The form is what is under test, not the summary, and "not on file"
    everywhere is a real state the page has to render anyway.
    """
    class _Fact:
        known = False
        label = "x"
        value = ""

        class source:
            note = "not on file"

    class _Summary:
        def __init__(self):
            self.allergies = [_Fact()]
            self.identity = [_Fact()]
            self.conditions = [_Fact()]
            self.medications = [_Fact()]
            self.recent_labs = [_Fact()]
            self.disclaimer = "not a hospital system"

    return _Summary()


def test_a_write_scoped_link_offers_the_order_form(case):
    """The whole feature hung off an endpoint with no way to reach it.

    The note endpoints existed, the page rendered a summary and nothing else,
    so an order could only be placed with curl. A clinician does not have curl.
    """
    from anbu_care.server import _handoff_html

    class _Grant:
        may_write_note = True


    page = _handoff_html(_summary_stub(), _Grant(), token="tok123")
    assert 'id=dxtest' in page, "no field to name the test"
    assert "/handoff/tok123/note/confirm" in page
    assert "Anbu Care does not order tests" in page


def test_a_read_only_link_offers_no_order_form(case):
    """A read link cannot be edited into one that orders tests."""
    from anbu_care.server import _handoff_html

    class _Grant:
        may_write_note = False


    page = _handoff_html(_summary_stub(), _Grant(), token="tok123")
    assert "id=dxtest" not in page
    assert "note/confirm" not in page


def test_the_form_defaults_to_saying_nothing_about_mobility():
    """"I am not saying" is the default, and it is the honest one."""
    from anbu_care.server import _order_form_html

    form = _order_form_html("tok")
    assert "<option value=unknown selected>" in form
    assert "I am not saying" in form


def test_the_record_shows_what_was_surfaced_not_a_fresh_search(case, live_places):
    """Re-searching on page load would show a list no receipt covers.

    It would also spend a paid API call on every render, which is its own
    reason, but the first one is why this is a correctness test.
    """
    _parent_id, case_id = case
    surfaced = referral.options_for(test_label="MRI", lat=HOSPITAL_LAT,
                                    lon=HOSPITAL_LON, city="Thoothukudi")

    from anbu_care.schemas import DiagnosticOrder

    order = DiagnosticOrder(order_id="dxorder-x", case_id=case_id, parent_id="p",
                            test_label="MRI", options=surfaced["options"],
                            options_source=surfaced["source"])
    service.save_diagnostic_order(order)

    stored = service.load_diagnostic_order(case_id, "dxorder-x")
    assert [o["place_id"] for o in stored.options] == \
           [o["place_id"] for o in surfaced["options"]]


# =========================================================================
# THE AGENT DOES IT, NOT THE FAMILY
# =========================================================================


def test_recording_an_order_surfaces_and_tells_without_being_asked(case, monkeypatch):
    """The point of the whole feature.

    A present son does not wait to be asked to look up where the test can be
    done. Splitting this into endpoints somebody had to invoke meant the family
    was doing the work on the agent's behalf, which is the opposite of what
    this is for — the bill lane has always read, priced, decided and told from
    one photograph, and this now matches it.
    """
    from anbu_care import server

    _parent_id, case_id = case
    ran = []
    monkeypatch.setattr(server, "_surface_options",
                        lambda c, o: ran.append(("surface", c, o)) or {"options": [1, 2]})
    monkeypatch.setattr(server, "_tell_about_order",
                        lambda c, o, option_count: ran.append(("tell", option_count)))

    server._refer_and_tell(case_id, "dxorder-1")

    assert [r[0] for r in ran] == ["surface", "tell"]
    assert ran[1][1] == 2, "the family was not told how many options there were"


def test_a_failed_search_still_tells_the_family(case, monkeypatch):
    """Silence is indistinguishable from nothing having happened."""
    from anbu_care import server

    _parent_id, case_id = case
    told = []
    monkeypatch.setattr(server, "_surface_options",
                        lambda c, o: (_ for _ in ()).throw(RuntimeError("places down")))
    monkeypatch.setattr(server, "_tell_about_order",
                        lambda c, o, option_count: told.append(option_count))

    server._refer_and_tell(case_id, "dxorder-1")

    assert told == [None], "a failed search told nobody"


def test_the_empty_result_message_asks_for_a_phone_call():
    from anbu_care.comms.policy import TEMPLATES
    from anbu_care.schemas import MessageClass

    spec = TEMPLATES["diagnostic_options_none"]
    assert spec["message_class"] is MessageClass.LOGISTICS
    assert "could not find anywhere nearby" in spec["body"]
    assert "has not booked anything" in spec["body"]
    assert "{test" not in spec["body"], "the empty message names the test"


def test_an_ordinary_note_starts_no_referral(case, monkeypatch):
    """Only an order triggers it. A note about how she slept does not."""
    from fastapi.testclient import TestClient

    from anbu_care import server

    started = []
    monkeypatch.setattr(server, "_refer_and_tell",
                        lambda c, o: started.append(o))

    _parent_id, case_id = case
    # Set explicitly. Reading it from the ambient environment passed locally,
    # where .env is loaded, and failed in CI where it is not — which is the
    # whole class of bug this project keeps finding the slow way.
    monkeypatch.setenv("ANBU_LINK_SECRET", "test-referral-secret")

    from anbu_care.handoff import access

    token = access.mint(case_id, allow_notes=True)
    with TestClient(server.app) as client:
        r = client.post(f"/handoff/{token}/note/confirm",
                        json={"text": "Slept well overnight.",
                              "recorded_by": "Dr A. Anand"})
    assert r.status_code == 200
    assert r.json()["order_id"] == ""
    assert started == [], "a note with no order started a referral"


def _centre(primary_type):
    return places_api.Centre(place_id="p", name="Nowhere Lab", address="x",
                             lat=8.8, lon=78.1, primary_type=primary_type)


def test_no_em_dashes_in_anything_a_family_reads():
    """A standing rule on this project, broken twice in this feature alone.

    Docstrings and comments are for us. Every string that renders on a surface
    a family sees is not, and an em dash has been quietly reappearing in them.
    """
    from anbu_care.comms.policy import TEMPLATES
    from anbu_care.diagnostics import referral as ref

    for name in ("MOBILITY_UNSTATED", "MOBILITY_NON_AMBULATORY", "NOT_ARRANGED",
                 "CONFIRM_WITH_INSURER"):
        assert "—" not in getattr(ref, name), f"{name} carries an em dash"

    for key in ("diagnostic_options_ready", "diagnostic_options_none"):
        assert "—" not in TEMPLATES[key]["body"], f"{key} carries an em dash"

    # And every note the module actually composes, checked as output rather
    # than scraped from source: a regex over the file matches docstrings too,
    # which are ours to write however we like.
    for mobility in ("unknown", "ambulatory", "non_ambulatory"):
        grouped = ref.group_by_mobility([], mobility)
        assert "—" not in grouped["mobility_note"]
        assert "—" not in grouped["home_collection_note"]

    for note in (ref._network_note("Nowhere Lab", "Star Health")[1],
                 ref._network_note("Nowhere Lab", None)[1],
                 ref._offers_test(_centre("medical_lab"))[1],
                 ref._offers_test(_centre("medical_clinic"))[1],
                 ref._offers_test(_centre(""))[1]):
        assert "—" not in note, f"a composed note carries an em dash: {note[:60]}"


# =========================================================================
# DICTATION: GEMINI PROPOSES, THE CLINICIAN ORDERS
# =========================================================================


def _hears(monkeypatch, payload):
    from anbu_care.diagnostics import dictation

    monkeypatch.setenv("ANBU_DICTATION_MODE", "gemini")
    monkeypatch.setattr(dictation, "_call_model", lambda t: json.dumps(payload))


def test_a_dictated_order_is_read_as_the_clinician_said_it(monkeypatch):
    """"Repeat troponin" stays "repeat troponin".

    Expanding it into a formal name is a second decision nobody asked for, and
    the search wants the clinician's words anyway.
    """
    from anbu_care.diagnostics import dictation

    _hears(monkeypatch, {"tests": ["repeat troponin"], "unclear": False})
    p = dictation.propose_tests("She'll need a repeat troponin in the morning.")

    assert p.ok is True
    assert p.first == "repeat troponin"


def test_more_than_one_dictated_test_is_not_silently_reduced(monkeypatch):
    """Picking one for them is exactly the quiet choice this must not make."""
    from anbu_care.diagnostics import dictation

    _hears(monkeypatch, {"tests": ["repeat troponin", "echo"], "unclear": False})
    p = dictation.propose_tests("Repeat troponin, and an echo before discharge.")

    assert p.tests == ["repeat troponin", "echo"]


def test_an_unclear_dictation_proposes_nothing(monkeypatch):
    """A confident wrong answer costs her a day and a bill. Empty costs typing."""
    from anbu_care.diagnostics import dictation

    _hears(monkeypatch, {"tests": [], "unclear": True})
    p = dictation.propose_tests("Mumble mumble.")

    assert p.ok is False
    assert p.tests == []
    assert "not clear" in p.detail


def test_a_note_that_orders_nothing_proposes_nothing(monkeypatch):
    from anbu_care.diagnostics import dictation

    _hears(monkeypatch, {"tests": [], "unclear": False})
    p = dictation.propose_tests("She slept well and is comfortable.")

    assert p.ok is True
    assert p.tests == []


def test_a_failed_reading_proposes_nothing_rather_than_guessing(monkeypatch):
    from anbu_care.diagnostics import dictation

    monkeypatch.setenv("ANBU_DICTATION_MODE", "gemini")
    monkeypatch.setattr(dictation, "_call_model",
                        lambda t: (_ for _ in ()).throw(TimeoutError()))
    p = dictation.propose_tests("Repeat troponin please.")

    assert p.ok is False
    assert p.tests == []


def test_the_draft_endpoint_writes_nothing_and_records_no_order(case, monkeypatch):
    """The whole wall. A proposal is not an order.

    Nothing here may reach the record: no receipt, no DiagnosticOrder, nothing.
    Only what the clinician submits from the field counts.
    """
    from fastapi.testclient import TestClient

    from anbu_care import server
    from anbu_care.comms import transcribe
    from anbu_care.diagnostics import dictation
    from anbu_care.handoff import access

    _parent_id, case_id = case
    monkeypatch.setenv("ANBU_LINK_SECRET", "test-dictation-secret")
    monkeypatch.setenv("ANBU_DICTATION_MODE", "gemini")
    monkeypatch.setattr(transcribe, "transcribe_dictation",
                        lambda audio, mime: transcribe.Transcript(
                            ok=True, text="Repeat troponin in the morning.",
                            engine="stub", detail="stubbed"))
    monkeypatch.setattr(dictation, "_call_model",
                        lambda t: json.dumps({"tests": ["repeat troponin"],
                                              "unclear": False}))

    before = len(service.get_chain(case_id).receipts)
    token = access.mint(case_id, allow_notes=True)
    with TestClient(server.app) as client:
        r = client.post(f"/handoff/{token}/note/draft", content=b"x" * 5000,
                        headers={"content-type": "audio/webm"})

    body = r.json()
    assert body["written"] is False
    assert body["proposed_tests"] == ["repeat troponin"]
    assert "Nothing has been recorded" in body["warning"]

    assert service.list_diagnostic_orders(case_id) == [], "a draft created an order"
    assert len(service.get_chain(case_id).receipts) == before, "a draft wrote a receipt"


def test_the_page_puts_the_proposal_in_a_field_the_clinician_can_edit():
    """It fills the input. It does not submit."""
    from anbu_care.server import _order_form_html

    form = _order_form_html("tok")
    assert "note/draft" in form, "no way to send a recording"
    assert "getElementById('dxtest').value=t" in form, "the proposal is not editable"
    # The recorder must not call confirm. Only the Record button does.
    recorder = form[form.index("mic.addEventListener"):form.index("b.addEventListener")] \
        if "b.addEventListener" in form and \
           form.index("mic.addEventListener") < form.index("b.addEventListener") \
        else form[form.index("mic.addEventListener"):]
    assert "note/confirm" not in recorder, "speaking an order recorded it"


def test_the_page_says_plainly_when_it_could_not_tell():
    from anbu_care.server import _order_form_html

    form = _order_form_html("tok")
    assert "could not tell which test that was" in form
    assert "Check the test above is right" in form


def test_an_order_dictated_in_tamil_is_read_and_kept_in_tamil(monkeypatch):
    """She is in Thoothukudi and so is her doctor.

    Verified against the real model: a Tamil dictation comes back in Tamil
    script, code-mixed Tanglish comes back as the English clinical terms the
    clinician actually used, and both search Places usefully. The words are
    kept as spoken either way, which is the same rule that applies in English.
    """
    from anbu_care.diagnostics import dictation

    _hears(monkeypatch, {"tests": ["மீண்டும் ட்ரோபோனின் பரிசோதனை"], "unclear": False})
    p = dictation.propose_tests("காலையில் மீண்டும் ட்ரோபோனின் பரிசோதனை செய்ய வேண்டும்.")
    assert p.first == "மீண்டும் ட்ரோபோனின் பரிசோதனை"


def test_a_code_mixed_dictation_keeps_the_clinical_terms_as_said(monkeypatch):
    """Indian clinicians speak Tanglish. "Oru repeat troponin pannunga" is two
    languages and one order, and the clinical term stays the English one."""
    from anbu_care.diagnostics import dictation

    _hears(monkeypatch, {"tests": ["repeat troponin", "echo"], "unclear": False})
    p = dictation.propose_tests(
        "Oru repeat troponin pannunga, appuram echo-um venum discharge-ku munnadi.")
    assert p.tests == ["repeat troponin", "echo"]


def test_a_tamil_label_reaches_the_search_unrewritten(case, live_places):
    """Translating it before searching would be deciding what was ordered."""
    referral.options_for(test_label="ரத்த பரிசோதனை", lat=HOSPITAL_LAT,
                         lon=HOSPITAL_LON, city="Thoothukudi")
    assert "ரத்த பரிசோதனை" in live_places[0]["textQuery"]


# =========================================================================
# A TAMIL ORDER, FOR A READER IN ENGLISH
# =========================================================================


def test_a_tamil_order_is_rendered_into_english_for_the_family(monkeypatch):
    """The other direction. Outbound Tamil renders an English record for her;
    this renders her doctor's Tamil for the son reading in Nashville."""
    from anbu_care.comms import translate

    monkeypatch.setenv("ANBU_TRANSLATE_MODE", "gemini")
    monkeypatch.setattr(translate, "_call_model", lambda p, t: "blood test")

    r = translate.render_into_english("ரத்த பரிசோதனை", source_ref="clinician's order")
    assert r.translated is True
    assert r.text == "blood test"
    # The dictated words remain the record.
    assert r.source_text == "ரத்த பரிசோதனை"
    assert "recorded clinician's order" in r.detail


def test_an_english_order_costs_no_model_call(monkeypatch):
    """A label already readable does not need confirming, and every call is
    another chance to alter a record that did not need altering."""
    from anbu_care.comms import translate

    called = []
    monkeypatch.setenv("ANBU_TRANSLATE_MODE", "gemini")
    monkeypatch.setattr(translate, "_call_model",
                        lambda p, t: called.append(1) or "x")

    r = translate.render_into_english("repeat troponin", source_ref="clinician's order")
    assert r.translated is False
    assert r.text == "repeat troponin"
    assert called == [], "an English label was sent to the model"


def test_a_failed_rendering_shows_the_dictation_rather_than_a_guess(monkeypatch):
    """An unreadable label a family can ask about beats a confident English one
    that renames the test."""
    from anbu_care.comms import translate

    monkeypatch.setenv("ANBU_TRANSLATE_MODE", "gemini")
    monkeypatch.setattr(translate, "_call_model",
                        lambda p, t: (_ for _ in ()).throw(TimeoutError()))

    r = translate.render_into_english("ரத்த பரிசோதனை", source_ref="clinician's order")
    assert r.translated is False
    assert r.text == "ரத்த பரிசோதனை"
    assert "shown as recorded" in r.detail


def test_rendering_into_english_still_refuses_text_with_no_record():
    """The wall the whole module stands on, in the new direction too."""
    from anbu_care.comms import translate

    with pytest.raises(translate.NoSourceRecord):
        translate.render_into_english("", source_ref="clinician's order")
    with pytest.raises(translate.NoSourceRecord):
        translate.render_into_english("ரத்த பரிசோதனை", source_ref="")


def test_the_record_shows_both_the_english_and_what_was_said():
    """The dictation does not disappear behind its translation."""
    page = _client_page()
    card = page[page.index("function dxCard("):]
    card = card[:card.index("\n}")]

    assert "o.test_label_en" in card, "the English rendering is not shown"
    assert "esc(o.test_label)" in card, "the dictated words vanish"
    assert "translated from it" in card, "the translation is not labelled as one"
