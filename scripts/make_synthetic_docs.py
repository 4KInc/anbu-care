"""Generate the synthetic medical documents used in the multimodal demo beat.

Run once; the PNGs are committed. Pillow is a dev-only dependency — the service
never imports it.

Everything here is invented. There is no real patient, no real lab, no real
doctor. The header on every image says so, so a frame grab from the demo can
never be mistaken for a real record.

    uv run python scripts/make_synthetic_docs.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("assets/synthetic")
W, H = 1000, 1300
INK = (24, 32, 44)
MUTED = (110, 122, 138)
RULE = (206, 214, 224)
FLAG = (176, 42, 42)
BANNER_BG = (255, 244, 214)
BANNER_INK = (140, 94, 12)


def _font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _banner(d: ImageDraw.ImageDraw) -> int:
    d.rectangle([0, 0, W, 54], fill=BANNER_BG)
    d.text((30, 17), "SYNTHETIC SAMPLE — NOT A REAL MEDICAL RECORD — GENERATED FOR DEMO",
           font=_font(19, True), fill=BANNER_INK)
    return 92


def _header(d: ImageDraw.ImageDraw, y: int, title: str, subtitle: str) -> int:
    d.text((60, y), title, font=_font(34, True), fill=INK)
    d.text((60, y + 46), subtitle, font=_font(19), fill=MUTED)
    y += 88
    d.line([60, y, W - 60, y], fill=RULE, width=2)
    return y + 28


def _patient_block(d: ImageDraw.ImageDraw, y: int, rows: list[tuple[str, str]]) -> int:
    for label, value in rows:
        d.text((60, y), label, font=_font(18), fill=MUTED)
        d.text((300, y), value, font=_font(18, True), fill=INK)
        y += 30
    y += 14
    d.line([60, y, W - 60, y], fill=RULE, width=2)
    return y + 26


def lab_report(filename: str, collected: str, rows: list[tuple[str, str, str, str, str]]) -> None:
    """rows: (analyte, result, unit, reference range, flag)"""
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    y = _banner(d)
    y = _header(d, y, "Meenakshi Diagnostics (Sample)", "Thoothukudi, Tamil Nadu · NABL-equivalent sample layout")
    y = _patient_block(d, y, [
        ("Patient", "Rajeswari Manickam"),
        ("Age / Sex", "71 Y / F"),
        ("Referring physician", "Dr. A. Ravi (sample)"),
        ("Sample collected", collected),
        ("Report", "LIPID PROFILE + HbA1c"),
    ])

    cols = [60, 380, 540, 690, 890]
    for label, x in zip(["ANALYTE", "RESULT", "UNIT", "REFERENCE", "FLAG"], cols):
        d.text((x, y), label, font=_font(16, True), fill=MUTED)
    y += 30
    d.line([60, y, W - 60, y], fill=RULE, width=2)
    y += 18

    for analyte, result, unit, ref, flag in rows:
        colour = FLAG if flag.strip() else INK
        d.text((cols[0], y), analyte, font=_font(20), fill=INK)
        d.text((cols[1], y), result, font=_font(20, True), fill=colour)
        d.text((cols[2], y), unit, font=_font(19), fill=MUTED)
        d.text((cols[3], y), ref, font=_font(19), fill=MUTED)
        d.text((cols[4], y), flag, font=_font(19, True), fill=colour)
        y += 40

    y += 30
    d.line([60, y, W - 60, y], fill=RULE, width=2)
    d.text((60, y + 20), "Flags: H = above reference, L = below reference.", font=_font(17), fill=MUTED)
    d.text((60, y + 48), "Synthetic document. Values invented for a hackathon demo.", font=_font(17), fill=MUTED)
    img.save(OUT / filename)
    print("wrote", OUT / filename)


def prescription(filename: str) -> None:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    y = _banner(d)
    y = _header(d, y, "Dr. A. Ravi, MD (Sample)", "Consultant Physician · Thoothukudi · Reg. SAMPLE-0000")
    y = _patient_block(d, y, [
        ("Patient", "Rajeswari Manickam"),
        ("Age / Sex", "71 Y / F"),
        ("Date", "02 Aug 2026"),
        ("Known conditions", "Hypertension, Type 2 diabetes, Dyslipidaemia"),
    ])
    d.text((60, y), "Rx", font=_font(46, True), fill=INK)
    y += 70
    for name, dose, freq, note in [
        ("Telmisartan", "40 mg", "1-0-0", "after breakfast"),
        ("Atorvastatin", "20 mg", "0-0-1", "at bedtime"),
        ("Metformin", "500 mg", "1-0-1", "with meals"),
        ("Aspirin", "75 mg", "0-1-0", "after lunch"),
    ]:
        d.text((80, y), f"{name}  {dose}", font=_font(23, True), fill=INK)
        d.text((560, y), freq, font=_font(23), fill=INK)
        d.text((700, y), note, font=_font(19), fill=MUTED)
        y += 44
    y += 30
    d.line([60, y, W - 60, y], fill=RULE, width=2)
    d.text((60, y + 22), "Review in 4 weeks with fasting lipid profile and HbA1c.", font=_font(20), fill=INK)
    d.text((60, y + 56), "Synthetic document. Not a real prescription.", font=_font(17), fill=MUTED)
    img.save(OUT / filename)
    print("wrote", OUT / filename)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    lab_report("lab_report_mar2026.png", "14 Mar 2026", [
        ("Total Cholesterol", "232", "mg/dL", "< 200", "H"),
        ("LDL Cholesterol", "165", "mg/dL", "< 100", "H"),
        ("HDL Cholesterol", "38", "mg/dL", "> 40", "L"),
        ("Triglycerides", "180", "mg/dL", "< 150", "H"),
        ("HbA1c", "7.1", "%", "< 7.0", "H"),
    ])
    lab_report("lab_report_aug2026.png", "02 Aug 2026", [
        ("Total Cholesterol", "236", "mg/dL", "< 200", "H"),
        ("LDL Cholesterol", "165", "mg/dL", "< 100", "H"),
        ("HDL Cholesterol", "37", "mg/dL", "> 40", "L"),
        ("Triglycerides", "186", "mg/dL", "< 150", "H"),
        ("HbA1c", "8.4", "%", "< 7.0", "H"),
    ])
    prescription("prescription_aug2026.png")
