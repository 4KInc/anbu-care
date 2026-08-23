"""Bill capture.

The failure this feature can cause is specific and expensive: a model reads
₹96,000 where the paper says ₹9,600, and a wrong number looks exactly as
authoritative as a right one. Nobody double-takes at a figure.

So the tests are about the guards, not the happy path — the image is kept and
private, the chain carries a hash and not the money, the arithmetic is checked
against the bill's own total, and every figure on the coverage surface is
labelled an estimate rather than a settlement.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from anbu_care import service
from anbu_care.bills import coverage, ingest
from anbu_care.bills import extract as vision
from anbu_care.comms.storage import StoredArtifact
from anbu_care.schemas import BillLineItem, ExtractedBill
from anbu_care.tools import onboarding_tools, triage_tools
from anbu_care.webauth import DEMO_TOKEN

IMAGE = b"\xff\xd8\xff" + b"x" * 8000       # plausible-sized JPEG-ish bytes


@pytest.fixture
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


@pytest.fixture
def parent_id() -> str:
    pid = onboarding_tools.create_parent_profile(
        name="Ashanthi M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-1", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True,
    )
    return pid


@pytest.fixture
def case_id(parent_id) -> str:
    return triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="",
    )["case_id"]


def _reads(monkeypatch, lines, stated=None, unreadable=False, reason=None):
    """Pin what the model returns at the single seam, so the guards under test
    are the real parsing, arithmetic and refusal code rather than a mock."""
    payload = {
        "line_items": lines, "stated_total_inr": stated,
        "vendor": "Sacred Heart Hospital", "bill_date": "2026-08-22",
        "unreadable": unreadable, "unreadable_reason": reason,
    }
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model",
                        lambda image, mime_type: json.dumps(payload))
    # The webhook classifies before it routes, so the router is stubbed to say
    # "bill" too. Without this the classifier reaches the real model and the
    # test both slows down and stops testing what it claims to.
    from anbu_care.docvision import read as docvision_read
    monkeypatch.setattr(docvision_read, "read",
                        lambda image, mime_type="image/jpeg": docvision_read.Reading(
                            ok=True, kind="bill", engine="stub", detail="stubbed"))

    # Stand in for GCS. Ingestion refuses without stored evidence, which is the
    # point — but that guard has its own test rather than blocking every other.
    from anbu_care.comms import storage as gcs
    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=True, url="https://signed.example/x", object_name=f"artifacts/{filename}",
        detail="stubbed", expires_in_seconds=900))


ICU = {"label": "Cardiac ICU (3 days)", "item": "cardiac_icu_room",
       "amount_inr": 96_000, "source_hint": "row 1"}
PHARM = {"label": "Pharmacy", "item": "pharmacy", "amount_inr": 34_500, "source_hint": "row 2"}
TOIL = {"label": "Toiletries", "item": "toiletries", "amount_inr": 1_200, "source_hint": "row 3"}


# =========================================================================
# (1) INGESTED, EXTRACTED, AND THE IMAGE IS PRIVATE
# =========================================================================


def test_a_bill_image_becomes_line_items(case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [ICU, PHARM], stated=130_500)

    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    assert [line.item for line in bill.line_items] == ["cardiac_icu_room", "pharmacy"]
    assert bill.computed_total_inr == 130_500
    assert bill.stated_total_inr == 130_500
    assert bill.needs_review is False
    assert bill.image_sha256 == vision.image_sha256(IMAGE)
    assert ingest.list_bills(case_id) == [bill]


def test_the_image_url_is_never_handed_out_directly(client, case_id, parent_id, monkeypatch):
    """The object name stays server-side; only a signed link is ever minted."""
    _reads(monkeypatch, [ICU], stated=96_000)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    body = client.get(f"/api/cases/{case_id}/bills",
                      headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()

    served = json.dumps(body)
    assert "image_object" not in served
    assert "storage.googleapis.com" not in served
    assert body["bills"][0]["image_url"].startswith(f"/api/cases/{case_id}/bills/")


def test_bill_content_is_credentialed(client, case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [ICU], stated=96_000)
    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    assert client.get(f"/api/cases/{case_id}/bills").status_code == 401
    assert client.get(f"/api/cases/{case_id}/bills/{bill.bill_id}/image").status_code == 401
    assert client.get(f"/api/cases/{case_id}/bills",
                      headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).status_code == 200


# =========================================================================
# (3) THE RECEIPT CARRIES A HASH, AND /verify REVEALS NO MONEY
# =========================================================================


def test_the_receipt_carries_hashes_not_amounts(case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [ICU, PHARM], stated=130_500)
    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "bill.ingested")
    blob = json.dumps(receipt.payload)

    assert receipt.payload["reading_sha256"] == ingest.reading_sha256(bill)
    assert receipt.payload["image_sha256"] == vision.image_sha256(IMAGE)
    assert receipt.payload["line_count"] == 2

    for secret in ("96000", "96,000", "34500", "130500", "Sacred Heart", "pharmacy"):
        assert secret not in blob, f"the chain leaked {secret!r}"
    assert service.verify_case(case_id).ok


def test_public_verify_reveals_no_bill_content(client, case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [ICU, PHARM], stated=130_500)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    public = client.get(f"/api/cases/{case_id}/verify")
    assert public.status_code == 200
    for secret in ("96000", "96,000", "34500", "130500", "Sacred Heart", "Ashanthi"):
        assert secret not in public.text


def test_editing_a_stored_amount_breaks_the_reading_hash(case_id, parent_id, monkeypatch):
    """The hash exists to make a silent edit detectable. Prove that it is."""
    _reads(monkeypatch, [ICU], stated=96_000)
    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "bill.ingested")
    recorded = receipt.payload["reading_sha256"]

    tampered = ExtractedBill(**{**bill.model_dump(), "line_items": [
        BillLineItem(label=ICU["label"], item=ICU["item"], amount_inr=9_600)]})
    assert ingest.reading_sha256(tampered) != recorded


# =========================================================================
# (4) A MIS-READ MUST NOT BECOME MONEY-OWED TRUTH
# =========================================================================


def test_lines_that_do_not_match_the_printed_total_are_flagged(case_id, parent_id, monkeypatch):
    """The ₹96,000-vs-₹9,600 case, caught by arithmetic rather than by luck."""
    _reads(monkeypatch, [ICU], stated=9_600)          # bill says 9,600; model read 96,000

    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    assert bill.needs_review is True
    assert "96,000" in bill.review_reason and "9,600" in bill.review_reason
    assert "check the photograph" in bill.review_reason


def test_a_flagged_bill_flags_the_whole_estimate(case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [ICU], stated=9_600)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    assert estimate.needs_review is True


def test_every_line_traces_back_to_its_source(case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [ICU, PHARM], stated=130_500)
    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    for line in bill.line_items:
        assert line.label            # what the paper said, not just our key
        assert line.source_hint      # where on the bill it was read from
    assert bill.image_object         # and the photograph itself is kept


def test_an_unreadable_bill_records_nothing(case_id, parent_id, monkeypatch):
    """A bill nobody could read is not a bill on file."""
    _reads(monkeypatch, [], unreadable=True, reason="the photo is too dark")

    before = len(service.get_chain(case_id).receipts)
    with pytest.raises(ingest.BillRejected) as rejected:
        ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    assert "too dark" in str(rejected.value)
    assert ingest.list_bills(case_id) == []
    assert len(service.get_chain(case_id).receipts) == before


def test_an_amount_that_cannot_be_parsed_is_dropped_not_guessed(monkeypatch):
    _reads(monkeypatch, [ICU, {"label": "Smudged", "item": "x", "amount_inr": "??"}],
           stated=96_000)
    result = vision.extract(IMAGE, "image/jpeg")

    assert result.ok is True
    assert [line["item"] for line in result.line_items] == ["cardiac_icu_room"]
    assert result.needs_review is True
    assert "could not be read" in result.review_reason


@pytest.mark.parametrize("raw,expected", [
    (96000, 96000), ("96000", 96000), ("96,000", 96000),
    ("INR 96,000.00", 96000), ("₹1,23,456", 123456), ("", None), ("abc", None),
])
def test_amount_parsing_accepts_real_formats_and_refuses_junk(raw, expected):
    assert vision._coerce_amount(raw) == expected


# =========================================================================
# (2) THE ITEMIZED VIEW — AN ESTIMATE, NOT A GUARANTEE
# =========================================================================


def test_the_split_reuses_the_adjudicator_rules(case_id, parent_id, monkeypatch):
    """Same sub-limit arithmetic that produces the 66,000 figure elsewhere."""
    _reads(monkeypatch, [ICU, PHARM, TOIL], stated=131_700)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    by_item = {line.item: line for line in estimate.lines}

    # ICU: 2% of 5,00,000 = 10,000/day. No packet, so one day.
    assert by_item["cardiac_icu_room"].estimated_covered_inr == 10_000
    assert by_item["cardiac_icu_room"].estimated_you_pay_inr == 86_000
    assert "2% of sum insured per day" in by_item["cardiac_icu_room"].rule

    # Pharmacy has no sub-limit; toiletries are conventionally excluded.
    assert by_item["pharmacy"].estimated_you_pay_inr == 0
    assert by_item["toiletries"].estimated_covered_inr == 0
    assert "excluded" in by_item["toiletries"].rule

    assert estimate.total_billed_inr == 131_700
    assert estimate.estimated_you_pay_inr == 86_000 + 1_200


def test_the_estimate_never_presents_itself_as_settled(case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [ICU], stated=96_000)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))

    # Nothing has been adjudicated, so settled is None — NOT zero.
    assert estimate.settled_inr is None

    disclaimer = estimate.disclaimer.lower()
    assert "not the insurer's final decision" in disclaimer
    assert "does not decide claims" in disclaimer
    assert "pays first" in disclaimer          # the reimbursement reality

    # Field names carry the claim too, so a careless renderer cannot lose it.
    fields = set(type(estimate).model_fields)
    assert "estimated_covered_inr" in fields and "estimated_you_pay_inr" in fields
    assert "covered_inr" not in fields and "you_pay_inr" not in fields


def test_a_running_total_accumulates_across_bills(case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [ICU], stated=96_000)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    _reads(monkeypatch, [PHARM], stated=34_500)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE + b"2", "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    assert estimate.bills_counted == 2
    assert estimate.total_billed_inr == 130_500


def test_no_policy_means_nothing_is_estimated_as_covered(case_id, monkeypatch):
    """Never optimistic in the absence of information."""
    bare = onboarding_tools.create_parent_profile(
        name="X", age=70, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    bare_case = triage_tools.run_triage(
        parent_id=bare, symptoms=["chest pain"], free_text="",
        reported_by="c", lat=0.0, lon=0.0, case_id="")["case_id"]

    _reads(monkeypatch, [ICU], stated=96_000)
    ingest.ingest_bill_image(bare_case, bare, IMAGE, "image/jpeg")

    estimate = coverage.estimate_for_case(bare_case, ingest.list_bills(bare_case))
    assert estimate.estimated_covered_inr == 0
    assert estimate.estimated_you_pay_inr == 96_000
    assert "no policy" in estimate.lines[0].rule


def test_the_api_labels_the_split_as_an_estimate(client, case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [ICU], stated=96_000)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    body = client.get(f"/api/cases/{case_id}/bills",
                      headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()

    assert body["is_estimate_not_settlement"] is True
    assert "not the insurer's final decision" in body["estimate"]["disclaimer"].lower()
    assert body["estimate"]["settled_inr"] is None


# =========================================================================
# INBOUND — an image is no longer dropped
# =========================================================================


def test_the_webhook_recognises_an_image_as_an_image():
    from anbu_care.comms.inbound import media_from

    assert media_from({"NumMedia": "0"}) is None
    # A kind it does not handle is refused rather than guessed at.
    assert media_from({"NumMedia": "1", "MediaUrl0": "https://x/y",
                       "MediaContentType0": "application/pdf"}) is None


def test_a_bill_whose_photograph_cannot_be_stored_is_refused(case_id, parent_id, monkeypatch):
    """No image, no ingestion.

    The premise of this feature is that a number can be checked against the
    paper it came from. A bill recorded without its photograph is a set of
    unverifiable figures wearing the authority of a record, which is worse than
    no bill at all — so it is refused rather than degraded.
    """
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model",
                        lambda image, mime_type: json.dumps({"line_items": [ICU]}))
    from anbu_care.comms import storage as gcs
    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=False, url=None, detail="ANBU_ARTIFACT_BUCKET is not set"))

    before = len(service.get_chain(case_id).receipts)
    with pytest.raises(ingest.BillRejected) as rejected:
        ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    assert "could not be stored" in str(rejected.value)
    assert ingest.list_bills(case_id) == []
    assert len(service.get_chain(case_id).receipts) == before


def test_a_small_but_legitimate_bill_is_not_refused_for_its_size(monkeypatch):
    """A scan or screenshot of a mostly-white page compresses very small.

    An earlier floor of 4 KB was tuned for phone photographs and would have
    refused a perfectly readable bill for compressing well — a worse failure
    than one wasted call on something that turns out not to be a bill.
    """
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model", lambda image, mime_type: json.dumps(
        {"line_items": [ICU], "stated_total_inr": 96_000, "unreadable": False}))

    small_but_real = b"\x89PNG\r\n\x1a\n" + b"z" * 1_600
    result = vision.extract(small_but_real, "image/png")
    assert result.ok is True

    # A few bytes is still junk and is still refused.
    assert vision.extract(b"tiny", "image/png").ok is False


# =========================================================================
# THE WHATSAPP PATH — the entry point the brief actually asked for
#
# Every piece below this was already tested. The handler joining them was not,
# and unexercised glue is exactly where the last two live-only defects lived.
# =========================================================================


def _twilio_form(parent_number: str, mime: str = "image/jpeg") -> dict:
    return {
        "From": f"whatsapp:{parent_number}", "To": "whatsapp:+14155238886",
        "Body": "", "NumMedia": "1", "MediaUrl0": "https://api.twilio.com/media/1",
        "MediaContentType0": mime,
    }


@pytest.fixture
def registered(parent_id, case_id, monkeypatch):
    """A parent whose WhatsApp number is registered, with an open case."""
    number = "+16692167706"
    onboarding_tools.record_family_contact(
        parent_id, name="Karthik", relationship="son", whatsapp_e164=number,
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=["status_updates", "inbound_wellbeing"],
    )
    return number, parent_id, case_id


def test_a_bill_photo_over_whatsapp_is_ingested(client, registered, monkeypatch):
    """The whole point of the feature, end to end through the webhook."""
    number, _parent_id, case_id = registered
    _reads(monkeypatch, [ICU, PHARM], stated=130_500)
    monkeypatch.setattr("anbu_care.comms.inbound.verify_twilio_signature",
                        lambda *a, **k: None)

    import anbu_care.comms.inbound as inbound_mod
    monkeypatch.setattr(inbound_mod, "media_from", lambda form: inbound_mod.InboundMedia(
        audio=IMAGE, mime_type="image/jpeg", kind="image"))

    response = client.post("/api/wellbeing/inbound", data=_twilio_form(number))

    # The webhook acknowledges immediately and promises the detail separately.
    # Reading a bill takes about fifteen seconds and Twilio abandons a webhook
    # at roughly the same mark, so the work cannot live on this response.
    assert response.status_code == 200
    assert "got that" in response.text.lower()
    assert "nothing is recorded until it has been read" in response.text.lower()
    # And it does NOT pre-announce a total it has not read yet.
    assert "130,500" not in response.text

    # TestClient runs background tasks after the response, so the work still
    # happened — just not on the request path.
    bills = ingest.list_bills(case_id)
    assert len(bills) == 1
    assert bills[0].computed_total_inr == 130_500


def test_a_bill_photo_with_no_case_is_refused_not_filed_against_a_guess(
    client, parent_id, monkeypatch
):
    """A bill on the wrong case silently moves someone else's money."""
    number = "+16692167707"
    onboarding_tools.record_family_contact(
        parent_id, name="K", relationship="son", whatsapp_e164=number,
        timezone_name="UTC", is_primary=True, consent_purposes=["inbound_wellbeing"])
    _reads(monkeypatch, [ICU], stated=96_000)
    monkeypatch.setattr("anbu_care.comms.inbound.verify_twilio_signature",
                        lambda *a, **k: None)
    import anbu_care.comms.inbound as inbound_mod
    monkeypatch.setattr(inbound_mod, "media_from", lambda form: inbound_mod.InboundMedia(
        audio=IMAGE, mime_type="image/jpeg", kind="image"))

    response = client.post("/api/wellbeing/inbound", data=_twilio_form(number))

    assert response.status_code == 200
    assert "no open case" in response.text.lower()
    assert "not been recorded" in response.text.lower()


def test_an_unreadable_photo_over_whatsapp_says_so_and_files_nothing(
    client, registered, monkeypatch
):
    number, _, case_id = registered
    _reads(monkeypatch, [], unreadable=True, reason="the photo is too dark")
    monkeypatch.setattr("anbu_care.comms.inbound.verify_twilio_signature",
                        lambda *a, **k: None)
    import anbu_care.comms.inbound as inbound_mod
    monkeypatch.setattr(inbound_mod, "media_from", lambda form: inbound_mod.InboundMedia(
        audio=IMAGE, mime_type="image/jpeg", kind="image"))

    response = client.post("/api/wellbeing/inbound", data=_twilio_form(number))

    # Acknowledged immediately; the failure is reported by the follow-up.
    assert response.status_code == 200
    assert "got that" in response.text.lower()
    # Nothing was filed, which is the guarantee that matters.
    assert ingest.list_bills(case_id) == []


# =========================================================================
# THE REVERSE INDEX — and the backfill gap that broke old cases
# =========================================================================


def test_a_case_opened_before_the_index_existed_repairs_itself(parent_id):
    """The bug: the index only existed for cases opened after it was added.

    An old case returned None from latest_case_for_parent, so a bill
    photographed against it was refused as "no open case" — which on camera
    reads as the feature being broken rather than as data being missing.
    """
    from anbu_care.provenance.store import get_store

    case = service.open_case(parent_id)
    # Simulate a case written before the index existed.
    get_store().delete(f"PARENT#{parent_id}", f"CASE#{case.case_id}")
    assert service.latest_case_for_parent(parent_id) is None

    # Anything touching the case repairs it.
    service.update_case(case)
    found = service.latest_case_for_parent(parent_id)
    assert found is not None and found.case_id == case.case_id


def test_latest_case_for_parent_returns_the_most_recent(parent_id):
    first = service.open_case(parent_id)
    second = service.open_case(parent_id)

    found = service.latest_case_for_parent(parent_id)
    assert found.case_id == second.case_id != first.case_id


def test_latest_case_for_parent_is_none_for_a_stranger():
    assert service.latest_case_for_parent("parent-nobody") is None


# =========================================================================
# WHAT A REAL BILL LAYOUT TAUGHT US
# =========================================================================


def test_a_discounted_bill_is_not_flagged_as_misread(monkeypatch):
    """Line items add up to the SUB-TOTAL, not the total.

    A real Indian IPD bill prints both, differing by a discount and GST.
    Checking the lines against the total flagged every discounted bill as a
    misread — which the first realistic bill promptly did, by exactly its
    12,000 discount.
    """
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model", lambda image, mime_type: json.dumps({
        "line_items": [ICU, PHARM],                    # 96,000 + 34,500 = 130,500
        "subtotal_inr": 130_500,
        "discount_inr": 12_000,
        "tax_inr": 0,
        "stated_total_inr": 118_500,
        "unreadable": False,
    }))
    result = vision.extract(IMAGE, "image/jpeg")

    assert result.ok is True
    assert result.needs_review is False, result.review_reason
    assert result.subtotal_inr == 130_500
    assert result.discount_inr == 12_000


def test_a_bill_without_a_subtotal_reconciles_through_the_total(monkeypatch):
    """Not every bill prints a sub-total; the discount still has to be undone."""
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model", lambda image, mime_type: json.dumps({
        "line_items": [ICU, PHARM],
        "subtotal_inr": None, "discount_inr": 12_000, "tax_inr": 0,
        "stated_total_inr": 118_500, "unreadable": False,
    }))
    assert vision.extract(IMAGE, "image/jpeg").needs_review is False


def test_a_genuine_mismatch_is_still_caught(monkeypatch):
    """The check must still do its job once the false positive is gone."""
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model", lambda image, mime_type: json.dumps({
        "line_items": [ICU],                            # 96,000
        "subtotal_inr": 9_600,                          # a real misread
        "discount_inr": None, "tax_inr": None,
        "stated_total_inr": 9_600, "unreadable": False,
    }))
    result = vision.extract(IMAGE, "image/jpeg")
    assert result.needs_review is True
    assert "96,000" in result.review_reason and "9,600" in result.review_reason


def test_subsumed_consumables_are_not_estimated_as_covered(case_id, parent_id, monkeypatch):
    """IRDAI treats PPE and gloves as subsumed into the room charge.

    They appear as their own line on a real bill, and were being counted as
    fully covered — the most optimistic possible reading of the one number a
    family should not be given optimistically.
    """
    ppe = {"label": "Gloves and PPE kit", "item": "gloves_and_ppe_kit",
           "amount_inr": 1_450, "source_hint": "row 14"}
    _reads(monkeypatch, [ICU, ppe], stated=97_450)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    line = next(l for l in estimate.lines if l.item == "gloves_and_ppe_kit")

    assert line.estimated_covered_inr == 0
    assert line.estimated_you_pay_inr == 1_450
    assert "excluded" in line.rule


def test_the_acknowledgement_does_not_claim_the_bill_is_recorded(client, registered, monkeypatch):
    """Two messages, two different claims, and neither overstates.

    The webhook cannot know what is in the bill yet — it has fifteen seconds of
    work to do and about fifteen seconds before Twilio hangs up. So the
    acknowledgement says only that the photo arrived, and the second message
    says what it contained. An acknowledgement that said "bill recorded" would
    be claiming an outcome that had not happened yet, which is the same class
    of false claim as reporting a message delivered when it was only accepted.
    """
    number, _, _ = registered
    _reads(monkeypatch, [ICU], stated=96_000)
    monkeypatch.setattr("anbu_care.comms.inbound.verify_twilio_signature",
                        lambda *a, **k: None)
    import anbu_care.comms.inbound as inbound_mod
    monkeypatch.setattr(inbound_mod, "media_from", lambda form: inbound_mod.InboundMedia(
        audio=IMAGE, mime_type="image/jpeg", kind="image"))

    text = client.post("/api/wellbeing/inbound", data=_twilio_form(number)).text.lower()

    assert "recorded" not in text.split("nothing is recorded")[0]
    for premature in ("96,000", "covered", "you pay", "estimate"):
        assert premature not in text.split("nothing is recorded")[0]


def test_the_follow_up_reports_the_split_and_calls_it_an_estimate(client, registered, monkeypatch):
    """The second message is where the numbers live, and where the caveat does."""
    from anbu_care.comms.policy import TEMPLATES

    body = str(TEMPLATES["bill_recorded"]["body"]).lower()
    assert "estimate" in body
    assert "not the insurer's decision" in body
    assert "{dashboard_url}" in body
    # The photo it was read from is reachable, because a number you cannot
    # check against the paper is not worth much.
    assert "photo it was read from" in body


def test_the_same_photograph_twice_is_one_bill(case_id, parent_id, monkeypatch):
    """A retry must not double the money.

    This shipped: a bill sent at 2:44 got no reply because the webhook timed
    out, the same bill was sent again at 2:55, and the running total reported
    INR 765,440 for a INR 382,720 bill. Photographing the same paper twice is
    one bill, and the image hash already knew that.
    """
    _reads(monkeypatch, [ICU, PHARM], stated=130_500)

    first = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    before = len(service.get_chain(case_id).receipts)

    with pytest.raises(ingest.BillRejected) as rejected:
        ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    assert first.bill_id in str(rejected.value)
    assert "not been added again" in str(rejected.value)
    assert len(ingest.list_bills(case_id)) == 1
    assert len(service.get_chain(case_id).receipts) == before

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    assert estimate.total_billed_inr == 130_500      # not 261,000
    assert estimate.bills_counted == 1


def test_a_genuinely_different_bill_still_adds(case_id, parent_id, monkeypatch):
    """Deduplication must not swallow the second bill of a real stay."""
    _reads(monkeypatch, [ICU], stated=96_000)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    _reads(monkeypatch, [PHARM], stated=34_500)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE + b"different", "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    assert estimate.bills_counted == 2
    assert estimate.total_billed_inr == 130_500


def test_the_message_does_not_mix_this_bill_with_every_bill():
    """"16 line items, INR 765,440 billed" was two different scopes in one
    sentence: this bill's line count beside every bill's total. It read as a
    single wrong number, and the number it looked wrong about was money."""
    from anbu_care.comms.policy import TEMPLATES

    body = str(TEMPLATES["bill_recorded"]["body"])
    assert "{this_bill}" in body and "on this bill" in body
    assert "{total_billed}" not in body, "the ambiguous total is back"

    # The stay-wide figures live in their own block, which names its own scope.
    # They used to be a bare running total sitting beside this bill's line
    # count, and later a case-wide split labelled as being of one bill.
    assert "{settlement_lines}" in body
    import inspect

    from anbu_care import server

    block = inspect.getsource(server._settlement_lines)
    assert "across the {bills} bills on this stay" in block
    assert "else \"on this bill\"" in block


def test_the_stay_is_read_off_the_bill_when_no_packet_exists(case_id, parent_id, monkeypatch):
    """A per-day sub-limit is multiplied by the length of stay.

    The bill prints "Cardiac ICU bed charges, 3 days" and an admission and
    discharge date, and we were assuming one day — covering INR 10,000 of a
    INR 96,000 ICU line instead of INR 30,000, and telling the family they owed
    20,000 more than the policy says. Understating coverage is not the safe
    direction; it is just a different wrong number about money.
    """
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model", lambda image, mime_type: json.dumps({
        "line_items": [ICU], "subtotal_inr": 96_000, "stated_total_inr": 96_000,
        "admitted_on": "2026-08-19", "discharged_on": "2026-08-22",
        "unreadable": False,
    }))
    from anbu_care.comms import storage as gcs
    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=True, url="x", object_name=f"artifacts/{filename}", detail="stub"))

    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    assert bill.admitted_on == "2026-08-19"

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    icu = next(l for l in estimate.lines if l.item == "cardiac_icu_room")

    assert icu.estimated_covered_inr == 30_000        # 3 days, not 1
    assert icu.estimated_you_pay_inr == 66_000
    assert "3 day(s) as printed on the bill" in estimate.basis


def test_a_bill_without_dates_still_says_it_assumed_one_day(case_id, parent_id, monkeypatch):
    """The assumption stays visible rather than becoming an invisible default."""
    _reads(monkeypatch, [ICU], stated=96_000)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    assert "one day assumed" in estimate.basis


def test_each_bill_uses_its_own_length_of_stay(case_id, parent_id, monkeypatch):
    """One case can hold bills from two different admissions.

    A general ward stay in one week and a cardiac ICU stay in another are two
    stays with two date ranges. Computing one day count per case and applying
    it to every line was right only by coincidence while both happened to be
    three days, and wrong the moment they differ — which is exactly when a
    per-day sub-limit starts producing the wrong number.
    """
    from anbu_care.comms import storage as gcs

    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=True, url="x", object_name=f"artifacts/{filename}", detail="stub"))
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")

    def reading(lines, admitted, discharged, total):
        return lambda image, mime_type: json.dumps({
            "line_items": lines, "subtotal_inr": total, "stated_total_inr": total,
            "admitted_on": admitted, "discharged_on": discharged, "unreadable": False})

    # A ONE-day ICU stay: cap is 10,000, so 96,000 leaves 86,000 to pay.
    monkeypatch.setattr(vision, "_call_model",
                        reading([ICU], "2026-08-01", "2026-08-02", 96_000))
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    # A FIVE-day ICU stay on the same case: cap is 50,000.
    monkeypatch.setattr(vision, "_call_model",
                        reading([ICU], "2026-08-10", "2026-08-15", 96_000))
    ingest.ingest_bill_image(case_id, parent_id, IMAGE + b"second", "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    covered = sorted(l.estimated_covered_inr for l in estimate.lines)

    assert covered == [10_000, 50_000], covered
    assert "1 day(s)" in estimate.basis and "5 day(s)" in estimate.basis


def test_a_claim_packet_still_overrides_every_bill(case_id, parent_id, monkeypatch):
    """Packet dates are entered fields, not a model's reading of a photograph."""
    from anbu_care.comms import storage as gcs
    from anbu_care.tools import insurer_tools

    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=True, url="x", object_name=f"artifacts/{filename}", detail="stub"))
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model", lambda image, mime_type: json.dumps({
        "line_items": [ICU], "subtotal_inr": 96_000, "stated_total_inr": 96_000,
        "admitted_on": "2026-08-01", "discharged_on": "2026-08-02",  # one day
        "unreadable": False}))
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=parent_id, admission_summary="ICU",
        itemized_bills_inr={"cardiac_icu_room": 96_000}, diagnostics=[],
        attached_document_ids=[], admitted_on="2026-08-19", discharged_on="2026-08-22")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    assert "from the claim packet" in estimate.basis
    assert estimate.lines[0].estimated_covered_inr == 30_000     # 3 days, not 1


def test_the_bill_image_route_is_credentialed_and_the_link_token_works(
    client, case_id, parent_id, monkeypatch
):
    """The photograph link 401'd because an anchor carries no credential.

    The endpoint was right to refuse. The dashboard was wrong to point a plain
    href at it: an <a> sends no bearer token and no signed link parameters, so
    "open the photograph" reliably produced a 401 page. It is fetched through
    the same authenticated path as every other credentialed read now, and this
    pins that BOTH ways of holding a credential reach it.
    """
    from anbu_care.comms import storage as gcs
    from anbu_care.webauth import DEMO_TOKEN, make_link_token

    _reads(monkeypatch, [ICU], stated=96_000)
    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    monkeypatch.setattr(gcs, "signed_url", lambda obj: StoredArtifact(
        stored=True, url="https://signed.example/photo", object_name=obj,
        detail="stub", expires_in_seconds=900))

    path = f"/api/cases/{case_id}/bills/{bill.bill_id}/image"

    # No credential at all: refused, and the refusal explains itself.
    denied = client.get(path)
    assert denied.status_code == 401
    assert "family session" in denied.json()["detail"]

    # A family session reaches it.
    with_session = client.get(path, headers={"Authorization": f"Bearer {DEMO_TOKEN}"})
    assert with_session.status_code == 200
    assert with_session.json()["url"] == "https://signed.example/photo"

    # And so does the signed link a family member was sent at 2am, which is the
    # credential they will actually be holding when they tap through.
    monkeypatch.setenv("ANBU_LINK_SECRET", "test-link-secret")
    token = make_link_token(parent_id, case_id)
    if token:
        via_link = client.get(f"{path}?t={token}&case={case_id}")
        assert via_link.status_code == 200


def test_the_bills_api_exposes_the_bill_id_the_link_needs(client, case_id, parent_id, monkeypatch):
    """The client builds the image URL from bill_id, so it has to be served."""
    from anbu_care.webauth import DEMO_TOKEN

    _reads(monkeypatch, [ICU], stated=96_000)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    body = client.get(f"/api/cases/{case_id}/bills",
                      headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()
    assert body["bills"][0]["bill_id"]


def test_an_over_limit_room_reduces_the_associated_charges_too(
    case_id, parent_id, monkeypatch
):
    """Proportionate deduction, which is what actually decides the shortfall.

    An insurer does not merely refuse the excess room rent. Where the room is
    above the eligible category, the ASSOCIATED medical expenses are reduced by
    the same ratio the eligible rent bears to the rent charged. Leaving that out
    made every estimate optimistic in the one direction that hurts a family.

    Here the ICU is billed 96,000 against a 10,000/day cap over one day, so the
    ratio is 10,000/96,000. Procedures are reduced by it; pharmacy is not,
    because medicines are the standard carve-out.
    """
    proc = {"label": "Angioplasty", "item": "procedures",
            "amount_inr": 100_000, "source_hint": "row 2"}
    _reads(monkeypatch, [ICU, proc, PHARM], stated=230_500)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    by = {l.item: l for l in estimate.lines}

    ratio = by["cardiac_icu_room"].estimated_covered_inr / 96_000
    assert 0 < ratio < 1

    # Procedures reduced in the same proportion.
    assert by["procedures"].estimated_covered_inr == round(100_000 * ratio)
    assert "proportionate deduction" in by["procedures"].rule

    # Medicines exempt: still fully covered.
    assert by["pharmacy"].estimated_covered_inr == PHARM["amount_inr"]
    assert "proportionate deduction" not in by["pharmacy"].rule

    # And the estimate says which line caused it rather than hedging silently.
    assert estimate.may_understate is True
    assert "policy wording" in estimate.may_understate_note


def test_a_copay_is_taken_off_what_is_left_as_covered(case_id, parent_id, monkeypatch):
    """A co-pay is a share of the admissible amount, not of the bill."""
    profile = service.load_profile(parent_id)
    profile.policy.copay_percent = 10
    profile.policy.proportionate_deduction = False
    service.save_profile(profile)

    _reads(monkeypatch, [PHARM], stated=34_500)          # no sub-limit applies
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    line = coverage.estimate_for_case(case_id, ingest.list_bills(case_id)).lines[0]
    assert line.estimated_covered_inr == round(34_500 * 0.9)
    assert line.estimated_you_pay_inr == 34_500 - round(34_500 * 0.9)
    assert "10% co-pay" in line.rule


def test_a_policy_stated_limit_beats_the_conventional_percentage(
    case_id, parent_id, monkeypatch
):
    """A photographed schedule is this family's actual terms."""
    profile = service.load_profile(parent_id)
    profile.policy.sub_limits_inr = {"icu_per_day": 25_000}
    profile.policy.proportionate_deduction = False
    service.save_profile(profile)

    _reads(monkeypatch, [ICU], stated=96_000)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    line = coverage.estimate_for_case(case_id, ingest.list_bills(case_id)).lines[0]
    assert line.estimated_covered_inr == 25_000        # not the 10,000 default
    assert "policy limit" in line.rule


def test_an_estimate_with_no_capped_line_makes_no_such_claim(case_id, parent_id, monkeypatch):
    """The caveat has to mean something, so it cannot be always-on."""
    small = {"label": "Ward medication", "item": "pharmacy",
             "amount_inr": 1_240, "source_hint": "row 1"}
    _reads(monkeypatch, [small], stated=1_240)
    ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    estimate = coverage.estimate_for_case(case_id, ingest.list_bills(case_id))
    assert estimate.may_understate is False
    assert estimate.may_understate_note == ""


def test_the_docvision_package_does_not_shadow_its_own_module():
    """`from pkg import read` must give the module, not a re-exported function.

    This exact bug shipped in the bills package, was fixed there, and was
    reintroduced in docvision within the hour. A one-line test is cheaper than
    finding it a third time from an AttributeError at runtime.
    """
    import types

    from anbu_care.bills import extract as bills_extract
    from anbu_care.docvision import read as docvision_read

    assert isinstance(bills_extract, types.ModuleType)
    assert isinstance(docvision_read, types.ModuleType)


# =========================================================================
# WHAT A BILL SAYS IT COMES TO
# =========================================================================


def test_the_printed_total_is_what_gets_quoted(case_id, parent_id, monkeypatch):
    """Found on the first real discounted bill. The line items added up to
    3,82,720 and the bill's own TOTAL was 3,70,720 after a 12,000 discount, and
    the family was told the larger number as "on this bill".

    A person holding the paper reads TOTAL. Quoting anything else is telling
    them their own bill is wrong.
    """
    _reads(monkeypatch, [
        {"label": "ICU bed", "item": "icu", "amount_inr": 300_000},
        {"label": "Pharmacy", "item": "pharmacy", "amount_inr": 82_720},
    ], stated=370_720)
    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")

    assert bill.computed_total_inr == 382_720      # the line items
    assert bill.stated_total_inr == 370_720        # what the bill prints
    assert bill.payable_total_inr == 370_720       # what a person is told


def test_the_adjustments_survive_ingestion(case_id, parent_id, monkeypatch):
    """The extractor read sub-total, discount and GST and ingestion dropped
    all three, which is how the discount went missing."""
    import json

    payload = {
        "line_items": [{"label": "ICU bed", "item": "icu", "amount_inr": 382_720}],
        "subtotal_inr": 382_720, "discount_inr": 12_000, "tax_inr": 0,
        "stated_total_inr": 370_720, "balance_due_inr": 270_720,
        "vendor": "Sacred Heart Hospital", "bill_date": "2026-08-22",
        "unreadable": False, "unreadable_reason": None,
    }
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model", lambda image, mime_type: json.dumps(payload))
    from anbu_care.comms import storage as gcs
    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=True, url="https://s/x", object_name=f"a/{filename}", detail="",
        expires_in_seconds=900))
    from anbu_care.docvision import read as docvision_read
    monkeypatch.setattr(docvision_read, "read",
                        lambda image, mime_type="image/jpeg": docvision_read.Reading(
                            ok=True, kind="bill", engine="stub", detail="stubbed"))

    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    assert bill.subtotal_inr == 382_720
    assert bill.discount_inr == 12_000
    assert bill.payable_total_inr == 370_720


def test_a_bill_with_no_printed_total_falls_back_to_the_lines(case_id, parent_id,
                                                              monkeypatch):
    """A bill whose TOTAL could not be read still has to say something, and the
    line items are the honest fallback."""
    _reads(monkeypatch, [{"label": "Ward", "item": "room_rent", "amount_inr": 4_500}])
    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    assert bill.stated_total_inr is None
    assert bill.payable_total_inr == 4_500


def test_the_message_names_the_discount_rather_than_hiding_it():
    import inspect

    from anbu_care import server

    source = inspect.getsource(server._read_bill_and_report)
    assert "bill.payable_total_inr" in source
    assert "a discount of INR" in source
    assert "bill.computed_total_inr:,}\"," not in source, "still quoting the line-item sum"


def test_the_split_reconciles_with_the_printed_total(case_id, parent_id, monkeypatch):
    """covered + you_pay must equal what the bill says it comes to.

    It equalled the SUB-total, so a family reading "1,42,030 covered, 2,40,690
    to pay" against a bill printing TOTAL 3,70,720 found the two sides 12,000
    apart with nothing explaining it.
    """
    import json

    payload = {
        "line_items": [{"label": "ICU bed", "item": "icu", "amount_inr": 200_000},
                       {"label": "Pharmacy", "item": "pharmacy", "amount_inr": 182_720}],
        "subtotal_inr": 382_720, "discount_inr": 12_000, "tax_inr": 0,
        "stated_total_inr": 370_720, "vendor": "Sacred Heart Hospital",
        "bill_date": "2026-08-22", "unreadable": False, "unreadable_reason": None,
    }
    monkeypatch.setenv("ANBU_BILL_VISION_MODE", "gemini")
    monkeypatch.setattr(vision, "_call_model", lambda i, m: json.dumps(payload))
    from anbu_care.comms import storage as gcs
    monkeypatch.setattr(gcs, "store", lambda filename, data, content_type="": StoredArtifact(
        stored=True, url="https://s/x", object_name=f"a/{filename}", detail="",
        expires_in_seconds=900))
    from anbu_care.docvision import read as docvision_read
    monkeypatch.setattr(docvision_read, "read",
                        lambda image, mime_type="image/jpeg": docvision_read.Reading(
                            ok=True, kind="bill", engine="stub", detail="stubbed"))

    bill = ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")
    est = coverage.estimate_for_case(case_id, [bill])

    assert est.total_discount_inr == 12_000
    assert est.estimated_covered_inr + est.estimated_you_pay_inr == bill.payable_total_inr
    assert est.total_billed_inr == 382_720          # the charges, unchanged


def test_a_discount_reduces_the_family_share_not_the_insurers(case_id, parent_id,
                                                              monkeypatch):
    """The insurer's share is capped by sub-limits on the line items, and a
    concession by the hospital does not raise that cap. So it comes off the
    residual."""
    _reads(monkeypatch, [{"label": "Ward", "item": "room_rent", "amount_inr": 100_000}])
    plain = coverage.estimate_for_case(
        case_id, [ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")])
    covered_before = plain.estimated_covered_inr
    pay_before = plain.estimated_you_pay_inr

    bills = ingest.list_bills(case_id)
    bills[0].discount_inr = 5_000
    discounted = coverage.estimate_for_case(case_id, bills)

    assert discounted.estimated_covered_inr == covered_before
    assert discounted.estimated_you_pay_inr == max(0, pay_before - 5_000)


def test_a_discount_larger_than_the_residual_does_not_owe_money_back(
        case_id, parent_id, monkeypatch):
    _reads(monkeypatch, [{"label": "Ward", "item": "room_rent", "amount_inr": 4_000}])
    bills = [ingest.ingest_bill_image(case_id, parent_id, IMAGE, "image/jpeg")]
    bills[0].discount_inr = 9_999_999

    assert coverage.estimate_for_case(case_id, bills).estimated_you_pay_inr == 0
