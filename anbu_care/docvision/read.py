"""Reading a photographed medical or insurance document.

The bill lane proved the shape: one Gemini call, honest failure as a first-class
outcome, the image kept privately so every extracted figure stays checkable
against the paper it came from. This generalises it to the other four documents
a family actually photographs.

**Classification and extraction happen in one call.** Two calls would double the
latency and give the second one no way to disagree with the first — a model
that has decided "this is a lab report" will find lab results in a discharge
summary. Asking for both together lets the same reading produce both answers.

What each type is read into, and why it matters that they differ:

  discharge_summary  admission and discharge dates, diagnosis, discharge
                     medication. The adjudicator REQUIRES this document before
                     it prices anything, and the dates drive per-day sub-limits.
  lab_report         observations with units, reference intervals and abnormal
                     flags. The emergency summary surfaces these, and "new and
                     abnormal" is meaningless without the interval.
  prescription       medications, which is what a treating clinician reads
                     immediately after allergies.
  policy_schedule    sum insured, sub-limits, co-pay. The coverage estimate is
                     computed from these, so reading them wrong moves money.
  bill               handled by `anbu_care.bills`, which already exists.

Nothing here decides anything clinical. It reads what is printed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

MIN_IMAGE_BYTES = 800
MAX_IMAGE_BYTES = 12 * 1024 * 1024
SUPPORTED_MIME = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")

KINDS = ("bill", "discharge_summary", "lab_report", "prescription",
         "policy_schedule", "other")

_PROMPT = """You are reading a photograph of an Indian medical or insurance document.

FIRST decide which kind of document it is, then extract only the fields for that
kind. Return ONLY a JSON object, no prose and no code fence.

{
  "kind": "bill" | "discharge_summary" | "lab_report" | "prescription"
          | "policy_schedule" | "other",
  "confidence": <0.0 to 1.0>,
  "patient_name": "<as printed, or null>",
  "unreadable": <true if you cannot read this reliably>,
  "unreadable_reason": "<short reason, or null>",

  "discharge_summary": {
    "admitted_on": "<YYYY-MM-DD or null>",
    "discharged_on": "<YYYY-MM-DD or null>",
    "hospital": "<or null>",
    "consultant": "<or null>",
    "diagnosis": "<the stated diagnosis, one or two sentences, or null>",
    "condition_at_discharge": "<or null>",
    "allergies": ["<as printed>"],
    "discharge_medications": [
      {"name": "<drug name>", "dose": "<e.g. 75 mg>", "frequency": "<e.g. once daily>"}
    ],
    "follow_up_on": "<YYYY-MM-DD or null>"
  },

  "lab_report": {
    "collected_on": "<YYYY-MM-DD or null>",
    "observations": [
      {"name": "<test name as printed>", "value": "<result exactly as printed>",
       "unit": "<or null>", "reference_range": "<as printed, or null>",
       "flag": "high" | "low" | "normal" | "abnormal" | null}
    ]
  },

  "prescription": {
    "prescribed_on": "<YYYY-MM-DD or null>",
    "prescriber": "<or null>",
    "allergies": ["<as printed>"],
    "medications": [
      {"name": "<drug name>", "dose": "<e.g. 40 mg>", "frequency": "<e.g. 1-0-0 after food>"}
    ]
  },

  "policy_schedule": {
    "insurer": "<or null>",
    "policy_number": "<or null>",
    "sum_insured_inr": <integer rupees, or null>,
    "room_rent_percent_per_day": <number, e.g. 1 for 1% of sum insured, or null>,
    "icu_percent_per_day": <number, e.g. 2, or null>,
    "room_rent_inr_per_day": <integer, if stated as a rupee amount, or null>,
    "icu_inr_per_day": <integer, if stated as a rupee amount, or null>,
    "copay_percent": <integer, or null>,
    "proportionate_deduction": <true if the schedule says associated charges are
                                reduced proportionately when the room exceeds
                                the limit, else false>,
    "network_hospitals": ["<as printed>"],
    "valid_until": "<YYYY-MM-DD or null>"
  }
}

Rules you must not break:
- Include ONLY the object for the kind you chose. Set the others to null.
- Transcribe values EXACTLY as printed. Never estimate, never round, never infer
  a figure that is obscured. If a digit is unclear, set "unreadable".
- Dates as YYYY-MM-DD. "19 Aug 2026" is "2026-08-19".
- Rupees as integers, no separators. "INR 5,00,000" is 500000.
- A lab result keeps its printed form: "0.94", "<0.01", "Positive", "2.640".
- Do NOT interpret results, do NOT diagnose, do NOT recommend treatment. Copy
  the diagnosis the document states; do not form one.
- If it is a hospital BILL with line items and amounts, the kind is "bill" and
  every other object is null. A different tool reads those.
"""


@dataclass
class Reading:
    """What one document turned out to be, and what was on it."""

    ok: bool
    kind: str = "other"
    engine: str = ""
    detail: str = ""
    confidence: float = 0.0
    patient_name: str | None = None
    payload: dict = field(default_factory=dict)

    @property
    def is_bill(self) -> bool:
        return self.kind == "bill"


def image_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _size_problem(data: bytes) -> str | None:
    if len(data) < MIN_IMAGE_BYTES:
        return f"that image is very small ({len(data)} bytes) and was not read"
    if len(data) > MAX_IMAGE_BYTES:
        return f"that image is too large ({len(data)} bytes) and was not read"
    return None


def _call_model(image: bytes, mime_type: str) -> str:
    """The one place this module talks to Gemini."""
    from google import genai
    from google.genai import types

    from anbu_care.config import settings

    client = genai.Client()
    response = client.models.generate_content(
        model=settings().model,
        contents=[types.Part.from_bytes(data=image, mime_type=mime_type), _PROMPT],
    )
    return (response.text or "").strip()


def _parse(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def read(image: bytes, mime_type: str = "image/jpeg") -> Reading:
    """Classify and extract in one call. Never raises; a failure is an answer."""
    if os.getenv("ANBU_DOC_VISION_MODE", "gemini").strip().lower() in {"off", "none", "false"}:
        return Reading(ok=False, engine="off",
                       detail="document reading is switched off; nothing was read")

    problem = _size_problem(image)
    if problem:
        return Reading(ok=False, engine="none", detail=problem)
    if mime_type not in SUPPORTED_MIME:
        return Reading(ok=False, engine="none",
                       detail=f"{mime_type} is not an image format this build reads")

    try:
        parsed = _parse(_call_model(image, mime_type))
    except Exception as exc:  # noqa: BLE001 - unreadable is a handled outcome
        return Reading(ok=False, engine="gemini",
                       detail=f"the document could not be read: {type(exc).__name__}")

    if parsed is None:
        return Reading(ok=False, engine="gemini",
                       detail="the document could not be read: the reply was not usable")
    if parsed.get("unreadable"):
        reason = parsed.get("unreadable_reason") or "the image could not be read reliably"
        return Reading(ok=False, engine="gemini",
                       detail=f"the document could not be read: {reason}")

    kind = str(parsed.get("kind") or "other").strip().lower()
    if kind not in KINDS:
        kind = "other"

    body = parsed.get(kind) if isinstance(parsed.get(kind), dict) else {}

    # An unrecognised document is a real outcome, not a failure to force into a
    # category. Filing a wedding invitation as a discharge summary would put a
    # fabricated admission date on a claim.
    if kind == "other":
        return Reading(ok=False, engine="gemini", kind="other",
                       detail="that does not look like a bill, discharge summary, "
                              "lab report, prescription or policy schedule")

    if kind != "bill" and not body:
        return Reading(ok=False, engine="gemini", kind=kind,
                       detail=f"it looks like a {kind.replace('_', ' ')} but nothing "
                              f"could be read off it")

    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    name = parsed.get("patient_name")
    return Reading(
        ok=True, kind=kind, engine="gemini", confidence=confidence,
        patient_name=(str(name).strip() if name else None),
        payload=body or {},
        detail=f"read a {kind.replace('_', ' ')} from {len(image)} bytes of {mime_type}",
    )
