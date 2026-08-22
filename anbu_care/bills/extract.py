"""Reading a photographed bill, and being honest about what that is.

This is the vision counterpart to `comms/transcribe.py`, and it inherits that
module's central discipline: **the image is the record and the reading is
derived from it.** A transcript is what a model heard; an extraction is what a
model saw. Neither is the thing itself.

The stakes are different here, and worse. A misheard symptom produces a wrong
word that a human immediately notices. A misread bill produces a *number* —
₹96,000 where the paper said ₹9,600 — and a wrong number looks exactly as
authoritative as a right one. Nobody double-takes at a figure. So:

  - the image is stored, privately, and every line stays traceable back to it
  - the arithmetic is checked against the bill's own stated total, and a
    mismatch sets `needs_review` rather than being quietly reconciled
  - failure is a first-class outcome, not an exception: a photograph too dark
    or too skewed to read comes back as "could not read this", which is a
    useful answer, where an invented total is a harmful one

Nothing here decides coverage. Extraction produces claimed amounts; what is
payable is the deterministic rules in `tpa/adjudicator.py`, and this module
never touches them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

# Loose bounds, only to reject obvious junk before spending a call.
#
# The floor is deliberately low. An earlier 4 KB was tuned for phone photos and
# would have refused a legitimate bill: a scan, a screenshot or a fax of a
# mostly-white page compresses far below that, and refusing a readable bill
# because it compressed well is a worse failure than spending one wasted call
# on something that turns out not to be a bill.
MIN_IMAGE_BYTES = 800
MAX_IMAGE_BYTES = 12 * 1024 * 1024

SUPPORTED_MIME = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")

# Tolerance on the stated-total check. Bills round, and a rupee or two of
# rounding is not a misread; anything larger is flagged rather than absorbed.
TOTAL_TOLERANCE_INR = 2

_PROMPT = """You are reading a photograph of a medical or hospital bill from India.

Return ONLY a JSON object, no prose and no code fence:

{
  "line_items": [
    {"label": "<exactly as printed on the bill>",
     "item": "<normalised key, lowercase, underscores>",
     "amount_inr": <integer rupees>,
     "source_hint": "<where on the bill, e.g. 'row 3' or 'ICU charges section'>"}
  ],
  "subtotal_inr": <integer rupees, the SUB-TOTAL before discount and tax, or null>,
  "discount_inr": <integer rupees deducted as a discount, or null>,
  "tax_inr": <integer rupees of GST or tax added, or null>,
  "stated_total_inr": <integer rupees, the final TOTAL printed, or null>,
  "vendor": "<hospital or clinic name, or null>",
  "bill_date": "<YYYY-MM-DD, or null>",
  "admitted_on": "<YYYY-MM-DD admission date printed on the bill, or null>",
  "discharged_on": "<YYYY-MM-DD discharge date printed on the bill, or null>",
  "unreadable": <true if you cannot read this reliably>,
  "unreadable_reason": "<short reason, or null>"
}

Rules you must not break:
- Transcribe amounts EXACTLY as printed. Never estimate, never round, never
  infer a figure that is obscured. If a digit is unclear, set "unreadable".
- Rupees only, as integers. Drop paise. "1,23,456.00" is 123456.
- Do NOT compute any total yourself. Every total field is only what is PRINTED
  on the bill. If a figure is not printed, use null.
- An Indian hospital bill usually prints BOTH a sub-total and a total, and they
  differ by a discount, by GST, or by both. The line items add up to the
  SUB-TOTAL. Read them as separate figures and do not reconcile them for us.
- "Advance paid" and "balance due" are payment lines, not charges. Do NOT
  include them as line items.
- Do NOT include a line you cannot read. A missing line is recoverable; an
  invented one is not.
- Use these normalised keys where they apply, because the coverage rules look
  them up: room_rent, room, ward, icu, icu_room, cardiac_icu_room, procedures,
  pharmacy, diagnostics, consultation, toiletries, attendant_charges,
  admission_kit, telephone, food_for_attendant, cosmetics.
  Anything else: lowercase the printed label and replace spaces with
  underscores.
- Admission and discharge dates are usually printed in the patient block near
  the top. They matter: a per-day sub-limit is multiplied by the length of
  stay, so a stay read as one day instead of three understates what an insurer
  covers. Read them if they are printed, and use null if they are not.
- If the image is not a bill at all, set "unreadable" with that as the reason.
"""


@dataclass
class Extraction:
    """The outcome of looking at one image. `ok=False` is a normal result."""

    ok: bool
    engine: str
    detail: str
    line_items: list[dict] = field(default_factory=list)
    stated_total_inr: int | None = None
    subtotal_inr: int | None = None
    discount_inr: int | None = None
    tax_inr: int | None = None
    vendor: str | None = None
    bill_date: str | None = None
    admitted_on: str | None = None
    discharged_on: str | None = None
    needs_review: bool = False
    review_reason: str | None = None


def image_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _size_problem(data: bytes) -> str | None:
    if len(data) < MIN_IMAGE_BYTES:
        return f"that image is very small ({len(data)} bytes) and was not read"
    if len(data) > MAX_IMAGE_BYTES:
        return f"that image is too large ({len(data)} bytes) and was not read"
    return None


def _coerce_amount(value: object) -> int | None:
    """Rupees as an integer, or None. Never a guess.

    Models emit 96000, "96000", "96,000" and "₹96,000.00" for the same figure.
    All are the same reading. Anything that is not a clean number is dropped,
    because a line that cannot be parsed is better missing than approximated.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(round(value)) if value >= 0 else None
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.]", "", value)
        if not cleaned or cleaned.count(".") > 1:
            return None
        try:
            number = float(cleaned)
        except ValueError:
            return None
        return int(round(number)) if number >= 0 else None
    return None


def _call_model(image: bytes, mime_type: str) -> str | None:
    """The one place this module talks to Gemini.

    Isolated so the parsing, the arithmetic check and the refusal paths can all
    be tested against real code rather than against a mock of themselves.
    """
    from google import genai
    from google.genai import types

    from anbu_care.config import settings

    client = genai.Client()
    response = client.models.generate_content(
        model=settings().model,
        contents=[types.Part.from_bytes(data=image, mime_type=mime_type), _PROMPT],
    )
    return response.text


def _parse(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract(image: bytes, mime_type: str = "image/jpeg") -> Extraction:
    """Read a bill image. Never raises; a failure is an answer."""
    if os.getenv("ANBU_BILL_VISION_MODE", "gemini").strip().lower() in {"off", "none", "false"}:
        return Extraction(ok=False, engine="off",
                          detail="bill reading is switched off; nothing was read")

    problem = _size_problem(image)
    if problem:
        return Extraction(ok=False, engine="none", detail=problem)

    if mime_type not in SUPPORTED_MIME:
        return Extraction(ok=False, engine="none",
                          detail=f"{mime_type} is not an image format this build reads")

    try:
        raw = _call_model(image, mime_type)
        parsed = _parse(raw or "")
    except Exception as exc:  # noqa: BLE001 - unreadable is a handled outcome
        return Extraction(ok=False, engine="gemini",
                          detail=f"the bill could not be read: {type(exc).__name__}")

    if parsed is None:
        return Extraction(ok=False, engine="gemini",
                          detail="the bill could not be read: the reply was not usable")

    if parsed.get("unreadable"):
        reason = parsed.get("unreadable_reason") or "the image could not be read reliably"
        return Extraction(ok=False, engine="gemini",
                          detail=f"the bill could not be read: {reason}")

    lines: list[dict] = []
    dropped = 0
    for entry in parsed.get("line_items") or []:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        amount = _coerce_amount(entry.get("amount_inr"))
        label = str(entry.get("label") or "").strip()
        if amount is None or not label:
            dropped += 1
            continue
        item = str(entry.get("item") or label).strip().lower().replace(" ", "_")
        hint = entry.get("source_hint")
        lines.append({
            "label": label, "item": item, "amount_inr": amount,
            "source_hint": str(hint).strip() if hint else None,
        })

    if not lines:
        return Extraction(ok=False, engine="gemini",
                          detail="no line item on that bill could be read")

    stated = _coerce_amount(parsed.get("stated_total_inr"))
    subtotal = _coerce_amount(parsed.get("subtotal_inr"))
    discount = _coerce_amount(parsed.get("discount_inr"))
    tax = _coerce_amount(parsed.get("tax_inr"))
    computed = sum(line["amount_inr"] for line in lines)

    # The arithmetic check, against the right number.
    #
    # Line items add up to the SUB-TOTAL. The total is the sub-total less any
    # discount plus any tax, and comparing the lines to it flagged every
    # discounted bill as misread — which a real bill with a 12,000 discount
    # promptly did. So check against the sub-total where one is printed, and
    # fall back to reconciling the total through discount and tax.
    #
    # Which line is wrong is never knowable from here, so a genuine mismatch
    # flags the whole extraction rather than being reconciled to whichever
    # number looks nicer.
    needs_review = False
    reason = None
    against, label = ((subtotal, "sub-total") if subtotal is not None
                      else (None, None))
    if against is None and stated is not None:
        against = stated + (discount or 0) - (tax or 0)
        label = "total after adding back the discount and removing tax"

    if against is not None and abs(against - computed) > TOTAL_TOLERANCE_INR:
        needs_review = True
        reason = (f"the lines add up to INR {computed:,} but the bill's {label} "
                  f"is INR {against:,} — check the photograph")
    elif dropped:
        needs_review = True
        reason = f"{dropped} line(s) on this bill could not be read and are not included"

    return Extraction(
        ok=True, engine="gemini",
        detail=f"read {len(lines)} line(s) from {len(image)} bytes of {mime_type}",
        line_items=lines, stated_total_inr=stated, subtotal_inr=subtotal,
        discount_inr=discount, tax_inr=tax,
        vendor=(str(parsed["vendor"]).strip() if parsed.get("vendor") else None),
        bill_date=(str(parsed["bill_date"]).strip() if parsed.get("bill_date") else None),
        admitted_on=(str(parsed["admitted_on"]).strip() if parsed.get("admitted_on") else None),
        discharged_on=(str(parsed["discharged_on"]).strip() if parsed.get("discharged_on") else None),
        needs_review=needs_review, review_reason=reason,
    )
