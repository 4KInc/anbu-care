"""The claim form a person could actually put in front of an insurer.

Everything else this system produces about a claim is for the family to read:
a WhatsApp paragraph, a dashboard card, a receipt. None of it is the thing an
insurer asks for. An Indian reimbursement claim starts with a filled Part A -
the insured's own declaration of who was admitted, where, when, under which
policy, and for how much - and until this existed Anbu Care could describe a
claim without ever producing one.

So this renders the form. The layout is a form, the fields are the fields, and
every value in it is read off the record rather than composed: the policy from
onboarding, the dates and the hospital from the discharge summary the family
photographed, the amounts from the bills they photographed. Nothing here asks a
model for anything.

WHAT IT WILL NOT DO IS GUESS. A field this system does not hold is printed as
"not on record" rather than filled with something plausible. A claim form is
signed by a human being and submitted under their name, and a confident wrong
address on it is worse than a blank one - the blank gets asked about, the wrong
one gets relied on. Bank details are absent entirely, and not because they were
hard: this system has never held them and a claim form is not the place to
start.

IT IS NOT ATTACHABLE. `comms/artifacts.py` builds documents that may ride on a
WhatsApp message, and refuses anything carrying clinical detail. This form
states a diagnosis, so it is deliberately not built through that path and never
goes near the comms gate. It is stored like any other document and reached
through the same case-scoped credential the record is, which is the whole
public-where-it-proves, private-where-it-reveals line applied to the one
document that would otherwise be an obvious exception.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

from anbu_care.money import group

UNKNOWN = "not on record"

# The declaration is the insured's, not Anbu Care's, and it is printed unsigned
# on purpose. A form this system had already "signed" would be a system making
# a legal declaration on somebody's behalf.
DECLARATION = (
    "I declare that the statements made in this form are true to the best of "
    "my knowledge and belief, and that the treatment described was received by "
    "the insured person named above. I understand that this form must be "
    "signed before it is submitted."
)

PREPARED_BY = (
    "Prepared by Anbu Care from documents the family photographed. The policy "
    "details come from onboarding, the dates and hospital from the discharge "
    "summary, and the amounts from the itemised bills. Fields marked "
    f"'{UNKNOWN}' were not held and have not been guessed. This form is "
    "unsigned and has not been sent to any insurer by Anbu Care."
)


def _text(value, fallback: str = UNKNOWN) -> str:
    value = ("" if value is None else str(value)).strip()
    return value or fallback


def _line_label(key: str) -> str:
    """A bill line's normalised key, printed the way the ward writes it.

    `capitalize()` alone turns "icu" into "Icu", which on a form somebody signs
    reads as a machine that has not seen a hospital bill.
    """
    words = []
    for word in str(key).replace("_", " ").split():
        words.append(word.upper() if len(word) <= 3 else word.capitalize())
    return " ".join(words) or "Other"


def _money(amount) -> str:
    try:
        amount = int(amount or 0)
    except (TypeError, ValueError):
        return UNKNOWN
    return f"INR {group(amount)}" if amount else UNKNOWN


def fields_for(*, profile, packet, discharge: dict | None = None,
               bills: list | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    """Every printed field, as sections. Separated from the drawing so the
    values can be asserted without rendering a PDF."""
    discharge = discharge or {}
    policy = getattr(profile, "policy", None)

    insured = [
        ("Name of insured person", _text(getattr(profile, "name", ""))),
        ("Age", _text(getattr(profile, "age", "") or "")),
        ("Gender", _text(getattr(profile, "gender", ""), "not stated")),
        ("City", _text(getattr(profile, "city", ""))),
        ("PIN code", _text(getattr(profile, "pincode", ""))),
        ("Contact number", _text(getattr(profile, "whatsapp_e164", ""))),
    ]
    cover = [
        ("Insurer", _text(getattr(policy, "insurer", ""))),
        ("Policy number", _text(getattr(policy, "policy_number", ""))),
        ("Sum insured", _money(getattr(policy, "sum_insured_inr", 0))),
        ("Cashless eligible", "Yes" if getattr(policy, "cashless_eligible", False)
         else "No / not on record"),
    ]
    admission = [
        ("Hospital", _text(discharge.get("hospital")
                           or getattr(packet, "hospital_name", ""))),
        ("Treating consultant", _text(discharge.get("consultant"))),
        ("Date of admission", _text(getattr(packet, "admitted_on", "")
                                    or discharge.get("admitted_on"))),
        ("Date of discharge", _text(getattr(packet, "discharged_on", "")
                                    or discharge.get("discharged_on"))),
        ("Nature of illness as stated on the discharge summary",
         _text(discharge.get("diagnosis"))),
        ("Condition at discharge", _text(discharge.get("condition_at_discharge"))),
    ]

    claimed = []
    for label, amount in sorted((getattr(packet, "itemized_bills_inr", None) or {}).items()):
        claimed.append((_line_label(label), _money(amount)))
    total = getattr(packet, "total_claimed_inr", 0)
    if not claimed:
        claimed.append(("Itemised amounts", UNKNOWN))
    claimed.append(("TOTAL CLAIMED", _money(total)))

    evidence = [
        ("Bills attached", str(len(bills or []))),
        ("Documents on the claim",
         str(len(getattr(packet, "attached_document_ids", None) or []))),
        ("Anbu Care case reference", _text(getattr(packet, "case_id", ""))),
        ("Anbu Care packet reference", _text(getattr(packet, "packet_id", ""))),
    ]

    return [
        ("A. Insured person", insured),
        ("B. Policy", cover),
        ("C. Hospitalisation", admission),
        ("D. Amount claimed", claimed),
        ("E. Supporting evidence held", evidence),
    ]


def render(*, profile, packet, discharge: dict | None = None,
           bills: list | None = None, now: datetime | None = None) -> bytes:
    """The filled form, as PDF bytes."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    now = now or datetime.now(UTC)
    sections = fields_for(profile=profile, packet=packet, discharge=discharge,
                          bills=bills)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_title(f"Health insurance claim form - {getattr(packet, 'case_id', '')}")

    def line(text: str, *, size=10, style="", height=5.5):
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(0, height,
                       str(text).encode("latin-1", "replace").decode("latin-1"),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    line("HEALTH INSURANCE CLAIM FORM", size=15, style="B", height=8)
    line("Part A - to be filled by the insured", size=10, style="I")
    line(f"Prepared {now.strftime('%d %b %Y, %H:%M UTC')}", size=8)
    pdf.ln(3)

    for title, rows in sections:
        pdf.set_fill_color(235, 238, 242)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        pdf.ln(1)
        for label, value in rows:
            y = pdf.get_y()
            pdf.set_font("Helvetica", "", 9)
            pdf.set_xy(12, y)
            pdf.multi_cell(78, 5.5,
                           label.encode("latin-1", "replace").decode("latin-1"),
                           new_x=XPos.RIGHT, new_y=YPos.TOP)
            after_label = pdf.get_y()
            pdf.set_xy(92, y)
            bold = "B" if label.isupper() else ""
            pdf.set_font("Helvetica", bold, 9)
            pdf.multi_cell(106, 5.5,
                           str(value).encode("latin-1", "replace").decode("latin-1"),
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_y(max(after_label, pdf.get_y()))
        pdf.ln(2)

    pdf.ln(2)
    line("Declaration", size=10, style="B")
    line(DECLARATION, size=8.5)
    pdf.ln(6)
    line("Signature of insured / claimant: ______________________     "
         "Date: ____________", size=9)
    pdf.ln(4)
    pdf.set_draw_color(190, 190, 190)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)
    line(PREPARED_BY, size=7.5, style="I")

    out = io.BytesIO()
    pdf.output(out)
    return out.getvalue()
