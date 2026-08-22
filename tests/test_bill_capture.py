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
        name="Rajeswari M.", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
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
    for secret in ("96000", "96,000", "34500", "130500", "Sacred Heart", "Rajeswari"):
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
