"""Render realistic Indian hospital bills as images, for testing bill capture.

Synthetic, and marked synthetic on the face of the bill. These exist so the
vision lane is exercised against something shaped like the real thing rather
than against `b"x" * 8000` — which is how the size floor shipped tuned for
phone photos and rejecting legitimate scans.

The structure follows an Indian IPD bill as documented publicly: hospital
header, UHID and IP number, admission and discharge timestamps, itemised heads
by department, subtotal, GST, advance paid, balance due. Sources are listed in
docs/CITATIONS.md.

Two of the bills deliberately include IRDAI "subsumed" items — gloves, PPE kit,
admission kit — because those are what a real claim gets docked for, and a
coverage estimate that never meets one has not been tested against the case it
exists for.

    uv run python scripts/make_bill_images.py --out /tmp/bills
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 1400
INK = (17, 24, 39)
MUTED = (90, 105, 120)
RULE = (200, 210, 220)


def _font(size: int, bold: bool = False):
    """A real font if the system has one, else PIL's default.

    Never fails: a missing font must not stop a test fixture from rendering.
    """
    candidates = (
        ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/System/Library/Fonts/Helvetica.ttc"] if bold else
        ["/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Helvetica.ttc"]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 - fall through to the default
            continue
    return ImageFont.load_default(size)


def render(spec: dict, out: Path) -> Path:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    y = 46

    d.text((50, y), spec["hospital"], font=_font(34, bold=True), fill=INK); y += 44
    d.text((50, y), spec["address"], font=_font(18), fill=MUTED); y += 26
    d.text((50, y), f"GSTIN {spec['gstin']}   ·   {spec['reg']}", font=_font(16), fill=MUTED)
    y += 40

    d.text((50, y), "INPATIENT FINAL BILL", font=_font(22, bold=True), fill=INK)
    d.text((760, y), spec["bill_no"], font=_font(18), fill=INK); y += 36
    d.line([(50, y), (W - 50, y)], fill=INK, width=2); y += 22

    left = [("Patient", spec["patient"]), ("UHID", spec["uhid"]),
            ("Age / Sex", spec["age_sex"]), ("Consultant", spec["consultant"])]
    right = [("IP No.", spec["ip_no"]), ("Admitted", spec["admitted"]),
             ("Discharged", spec["discharged"]), ("Ward", spec["ward"])]
    ry = y
    for label, value in left:
        d.text((50, ry), f"{label}", font=_font(16), fill=MUTED)
        d.text((190, ry), str(value), font=_font(17, bold=True), fill=INK); ry += 26
    ry = y
    for label, value in right:
        d.text((540, ry), f"{label}", font=_font(16), fill=MUTED)
        d.text((680, ry), str(value), font=_font(17, bold=True), fill=INK); ry += 26
    y = ry + 22

    d.line([(50, y), (W - 50, y)], fill=RULE, width=1); y += 14
    d.text((50, y), "PARTICULARS", font=_font(15, bold=True), fill=MUTED)
    d.text((600, y), "QTY", font=_font(15, bold=True), fill=MUTED)
    d.text((700, y), "RATE", font=_font(15, bold=True), fill=MUTED)
    d.text((860, y), "AMOUNT", font=_font(15, bold=True), fill=MUTED)
    y += 24
    d.line([(50, y), (W - 50, y)], fill=INK, width=1); y += 16

    for head, rows in spec["sections"]:
        d.text((50, y), head.upper(), font=_font(15, bold=True), fill=(70, 90, 110)); y += 26
        for label, qty, rate, amount in rows:
            d.text((66, y), label, font=_font(18), fill=INK)
            d.text((600, y), str(qty), font=_font(18), fill=MUTED)
            d.text((700, y), f"{rate:,}" if rate else "", font=_font(18), fill=MUTED)
            text = f"{amount:,}"
            d.text((960 - d.textlength(text, font=_font(18, bold=True)), y),
                   text, font=_font(18, bold=True), fill=INK)
            y += 28
        y += 8

    d.line([(50, y), (W - 50, y)], fill=INK, width=1); y += 18
    for label, amount, bold in spec["totals"]:
        f = _font(21 if bold else 18, bold=bold)
        d.text((600, y), label, font=f, fill=INK)
        text = f"{amount:,}"
        d.text((960 - d.textlength(text, font=f), y), text, font=f, fill=INK)
        y += 32

    y += 20
    d.text((50, y), "SYNTHETIC — generated for testing Anbu Care. Not a real bill, not a real patient.",
           font=_font(15), fill=(150, 60, 60))

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out


# The cardiac ICU stay the demo already tells, itemised the way an Indian
# hospital actually bills it — and carrying the non-payables a real claim gets
# docked for.
CARDIAC = {
    "hospital": "Sacred Heart Hospital",
    "address": "Palayamkottai Road, Thoothukudi, Tamil Nadu 628002",
    "gstin": "33AABCS1429B1ZQ", "reg": "Reg. No. TN/THO/1187",
    "bill_no": "IP/2026/04471", "patient": "Rajeswari Manickam",
    "uhid": "SHH-0092841", "age_sex": "71 / F", "consultant": "Dr A. Anand, Cardiology",
    "ip_no": "IP-26-8841", "admitted": "19 Aug 2026, 02:40",
    "discharged": "22 Aug 2026, 11:15", "ward": "Cardiac ICU",
    "sections": [
        ("Room & nursing", [
            ("Cardiac ICU bed charges", "3 days", 32000, 96000),
            ("Nursing charges", "3 days", 1200, 3600),
        ]),
        ("Professional fees", [
            ("Consultant rounds - Cardiology", "3", 1500, 4500),
            ("Anaesthetist fee", "1", 12000, 12000),
        ]),
        ("Procedures", [
            ("Coronary angiography", "1", 78000, 78000),
            ("Angioplasty with stent", "1", 132000, 132000),
        ]),
        ("Investigations", [
            ("Troponin I (serial)", "4", 1800, 7200),
            ("2D Echocardiography", "1", 4500, 4500),
            ("ECG", "6", 350, 2100),
            ("Complete blood count, LFT, RFT", "1", 4200, 4200),
        ]),
        ("Pharmacy & consumables", [
            ("Ward pharmacy - cardiac drugs", "-", 0, 31200),
            ("IV fluids and injections", "-", 0, 3300),
        ]),
        ("Non-medical items", [
            ("Admission kit", "1", 850, 850),
            ("Gloves and PPE kit", "-", 0, 1450),
            ("Attendant charges", "3 days", 400, 1200),
            ("Toiletries", "-", 0, 620),
        ]),
    ],
    "totals": [
        ("Sub-total", 382720, False),
        ("Discount", -12000, False),
        ("GST", 0, False),
        ("TOTAL", 370720, True),
        ("Advance paid", -100000, False),
        ("BALANCE DUE", 270720, True),
    ],
}

# The smaller general-ward stay from the published sample, for a bill whose
# arithmetic is easy to check by eye.
GENERAL_WARD = {
    "hospital": "Sundaram Arulrhaj Hospitals",
    "address": "Ettayapuram Road, Thoothukudi, Tamil Nadu 628002",
    "gstin": "33AACCS8821K1Z4", "reg": "Reg. No. TN/THO/0904",
    "bill_no": "IP/2026/03318", "patient": "Rajeswari Manickam",
    "uhid": "SAH-114472", "age_sex": "71 / F", "consultant": "Dr S. Meena, General Medicine",
    "ip_no": "IP-26-3318", "admitted": "11 Aug 2026, 09:20",
    "discharged": "14 Aug 2026, 10:05", "ward": "General Ward",
    "sections": [
        ("Room & nursing", [
            ("General ward bed rent", "3 days", 1500, 4500),
            ("Nursing charges", "6 shifts", 150, 900),
        ]),
        ("Professional fees", [
            ("Admission fee", "1", 500, 500),
            ("Doctor rounds", "3 days", 300, 900),
        ]),
        ("Investigations", [
            ("Complete blood count", "1", 350, 350),
            ("Liver function test", "1", 500, 500),
        ]),
        ("Pharmacy", [
            ("Ward medication", "-", 0, 1240),
        ]),
    ],
    "totals": [
        ("Sub-total", 8890, False),
        ("TOTAL", 8890, True),
        ("Advance paid", -5000, False),
        ("BALANCE DUE", 3890, True),
    ],
}


# A LONGER stay at a different hospital, with GST actually charged.
#
# Deliberately unlike the other two: six days rather than three, so the per-bill
# day count has something to differ on, and a non-zero GST line so the subtotal
# reconciliation meets tax outside a test. The ICU sub-limit bites hard here —
# 6 days at 2% of a five-lakh policy caps a 1,68,000 line at 60,000.
ICU_LONG = {
    "hospital": "Idhayalaya Heart Centre",
    "address": "Bryant Nagar, Thoothukudi, Tamil Nadu 628008",
    "gstin": "33AAFCI2298M1ZP", "reg": "Reg. No. TN/THO/1443",
    "bill_no": "IP/2026/05590", "patient": "Rajeswari Manickam",
    "uhid": "IHC-441207", "age_sex": "71 / F", "consultant": "Dr K. Raman, Cardiology",
    "ip_no": "IP-26-5590", "admitted": "02 Aug 2026, 23:15",
    "discharged": "08 Aug 2026, 09:40", "ward": "ICU",
    "sections": [
        ("Room & nursing", [
            ("ICU bed charges", "6 days", 28000, 168000),
            ("Nursing charges", "6 days", 1100, 6600),
        ]),
        ("Professional fees", [
            ("Consultant rounds - Cardiology", "6", 1400, 8400),
            ("Physician fee", "1", 9000, 9000),
        ]),
        ("Procedures", [
            ("Temporary pacemaker insertion", "1", 65000, 65000),
        ]),
        ("Investigations", [
            ("Troponin I (serial)", "6", 1800, 10800),
            ("Chest X-ray", "3", 600, 1800),
            ("Renal function test", "2", 900, 1800),
        ]),
        ("Pharmacy & consumables", [
            ("Ward pharmacy", "-", 0, 22400),
            ("IV fluids and injections", "-", 0, 2900),
        ]),
        ("Non-medical items", [
            ("Admission kit", "1", 900, 900),
            ("Gloves and PPE kit", "-", 0, 1700),
            ("Attendant charges", "6 days", 400, 2400),
        ]),
    ],
    "totals": [
        ("Sub-total", 301700, False),
        ("Discount", -5700, False),
        ("GST", 12000, False),
        ("TOTAL", 308000, True),
        ("Advance paid", -150000, False),
        ("BALANCE DUE", 158000, True),
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/bills")
    args = parser.parse_args()
    out = Path(args.out)

    for name, spec in (("cardiac_icu", CARDIAC), ("general_ward", GENERAL_WARD),
                       ("icu_long_stay", ICU_LONG)):
        path = render(spec, out / f"bill_{name}.png")
        total = next(a for label, a, _ in spec["totals"] if label == "TOTAL")
        print(f"  {path}   TOTAL INR {total:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
