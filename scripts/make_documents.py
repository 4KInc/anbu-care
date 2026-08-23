"""Render realistic Indian medical and insurance documents, for testing ingestion.

Synthetic, and marked synthetic on the face of every page. These exist for the
same reason the bill generator does: the first realistic bill found two defects
in ten minutes that `b"x" * 8000` had never touched, and the documents below
reach parts of the system the bills do not.

What each one is for:

  discharge_summary  The adjudicator REQUIRES one before it will price a claim
                     (REQUIRED_DOCUMENT_KINDS), and admission and discharge
                     dates drive the per-day sub-limit. Structure follows the
                     NABH sample format.
  lab_report         Feeds the Record tab and the emergency clinical summary.
                     Carries reference ranges and abnormal flags, which is what
                     "new and abnormal versus consistent with baseline" needs.
  prescription       Medications for the emergency summary — the field a
                     treating clinician reads before anything except allergies.
  policy_schedule    Sum insured, room-rent and ICU sub-limits, co-pay and
                     exclusions. The coverage estimate is computed FROM these,
                     and until now they have only ever been hardcoded.

Sources for the formats are recorded in docs/CITATIONS.md.

    uv run python scripts/make_documents.py --out ~/Desktop
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W = 1000
INK = (17, 24, 39)
MUTED = (90, 105, 120)
RULE = (200, 210, 220)
FLAG = (176, 42, 34)


# The person who holds the policy on her behalf. Overridable, but the default
# is the real one: this is a real project with a named author, and a placeholder
# on a document that goes on camera is a thing to explain rather than a thing
# that is true.
PROPOSER = os.getenv("ANBU_DEMO_FAMILY_NAME") or "Heartlin Machado"


def _font(size: int, bold: bool = False):
    names = (["Arial Bold.ttf", "Helvetica.ttc"] if bold else ["Arial.ttf", "Helvetica.ttc"])
    for name in names:
        for base in ("/System/Library/Fonts/Supplemental/", "/System/Library/Fonts/"):
            try:
                return ImageFont.truetype(base + name, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default(size)


class Page:
    """A very small layout helper. Grows the canvas rather than clipping."""

    def __init__(self, height: int = 1500) -> None:
        self.img = Image.new("RGB", (W, height), "white")
        self.d = ImageDraw.Draw(self.img)
        self.y = 46

    def text(self, x, s, size=18, bold=False, fill=INK, gap=26):
        self.d.text((x, self.y), str(s), font=_font(size, bold), fill=fill)
        self.y += gap

    def right(self, s, size=18, bold=False, fill=INK, x_right=960):
        f = _font(size, bold)
        self.d.text((x_right - self.d.textlength(str(s), font=f), self.y), str(s), font=f, fill=fill)

    def at(self, x, s, size=18, bold=False, fill=INK):
        self.d.text((x, self.y), str(s), font=_font(size, bold), fill=fill)

    def rule(self, width=1, colour=RULE, gap=16):
        self.d.line([(50, self.y), (W - 50, self.y)], fill=colour, width=width)
        self.y += gap

    def head(self, org, address, extra):
        self.text(50, org, 32, True, gap=42)
        self.text(50, address, 17, fill=MUTED, gap=24)
        self.text(50, extra, 15, fill=MUTED, gap=36)

    def title(self, s, right=""):
        self.at(50, s, 21, True)
        if right:
            self.right(right, 17)
        self.y += 34
        self.rule(2, INK, 20)

    def pairs(self, left, right):
        start = self.y
        for k, v in left:
            self.at(50, k, 15, fill=MUTED); self.at(200, v, 16, True); self.y += 25
        mid, self.y = self.y, start
        for k, v in right:
            self.at(540, k, 15, fill=MUTED); self.at(700, v, 16, True); self.y += 25
        self.y = max(mid, self.y) + 18

    def section(self, s):
        self.text(50, s.upper(), 14, True, fill=(70, 90, 110), gap=24)

    def para(self, s, indent=66, size=17, width=110):
        import textwrap
        for line in textwrap.wrap(str(s), width):
            self.text(indent, line, size, gap=24)
        self.y += 6

    def bullets(self, items, indent=66):
        for i, s in enumerate(items, 1):
            self.text(indent, f"{i}.  {s}", 17, gap=26)
        self.y += 6

    def finish(self, out: Path, note="SYNTHETIC — generated for testing Anbu Care. "
                                    "Not a real patient, not a real record."):
        self.y += 14
        self.text(50, note, 14, fill=(150, 60, 60), gap=20)
        cropped = self.img.crop((0, 0, W, min(self.y + 30, self.img.height)))
        out.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(out, "PNG")
        return out


# --------------------------------------------------------------------------


def discharge_summary(out: Path) -> Path:
    p = Page(1600)
    p.head("Sacred Heart Hospital", "Palayamkottai Road, Thoothukudi, Tamil Nadu 628002",
           "NABH accredited   ·   Reg. No. TN/THO/1187")
    p.title("DISCHARGE SUMMARY", "IP-26-8841")
    p.pairs(
        [("Name", "Ashanthi Machado"), ("Age / Sex", "71 / Female"),
         ("UHID", "SHH-0092841"), ("Consultant", "Dr A. Anand, Cardiology")],
        [("Date of admission", "19 Aug 2026, 02:40"), ("Date of discharge", "22 Aug 2026, 11:15"),
         ("Mode of admission", "Emergency"), ("Ward", "Cardiac ICU")],
    )
    p.rule()
    p.section("Reason for admission")
    p.para("Central chest pain with breathlessness of two hours duration, reported by "
           "the patient at home and brought in by a neighbour.")
    p.section("Clinical summary")
    p.para("Elderly female, known hypertensive, dyslipidaemic and type 2 diabetic on oral "
           "agents. Presented with retrosternal chest discomfort radiating to the left arm "
           "with diaphoresis. ECG on arrival showed ST depression in leads V4 to V6. "
           "Troponin I elevated at 0.94 ng/mL on admission, peaking at 1.32 ng/mL at six "
           "hours. Managed as non-ST elevation acute coronary syndrome.")
    p.section("Course in the hospital")
    p.para("Admitted to cardiac ICU and started on dual antiplatelet therapy, "
           "anticoagulation and statin. Coronary angiography on day two showed 80 percent "
           "stenosis of the proximal left anterior descending artery. Angioplasty with drug "
           "eluting stent performed the same day, without complication. Chest pain settled. "
           "Ambulated on day three. Renal function and electrolytes remained stable "
           "throughout.")
    p.section("Investigations")
    p.para("Troponin I 0.94 rising to 1.32 ng/mL. 2D echocardiography: ejection fraction 48 "
           "percent, mild hypokinesia of the anterior wall. HbA1c 8.4 percent. Creatinine "
           "1.3 mg/dL. Haemoglobin 11.2 g/dL.")
    p.section("Condition at discharge")
    p.para("Haemodynamically stable, chest pain free, ambulant. Discharged to home care.")
    p.section("Prescribed medication at discharge")
    p.bullets([
        "Tab Aspirin 75 mg — once daily, after breakfast",
        "Tab Clopidogrel 75 mg — once daily, after breakfast",
        "Tab Atorvastatin 40 mg — once daily, at night",
        "Tab Telmisartan 40 mg — once daily, morning",
        "Tab Metformin 500 mg — twice daily, after meals",
        "Tab Pantoprazole 40 mg — once daily, before breakfast",
    ])
    p.section("Advice on discharge")
    p.bullets([
        "Review in cardiology OPD on 05 Sep 2026 with a repeat lipid profile.",
        "Salt restricted diabetic diet. No strenuous activity for two weeks.",
        "Report immediately if chest pain, breathlessness or giddiness recurs.",
        "Do not stop antiplatelet medication without consulting the cardiologist.",
    ])
    p.section("Known allergies")
    p.para("PENICILLIN — rash and angioedema documented in 2019.")
    p.rule()
    p.text(50, "Dr A. Anand,  MD DM (Cardiology),  Reg. No. TN/54129", 16, fill=MUTED, gap=24)
    return p.finish(out)


def lab_report(out: Path) -> Path:
    p = Page(1500)
    p.head("Sacred Heart Hospital — Clinical Laboratory",
           "Palayamkottai Road, Thoothukudi, Tamil Nadu 628002",
           "NABL accredited   ·   Lab Reg. No. TN/LAB/2291")
    p.title("LABORATORY REPORT", "LR/2026/118842")
    p.pairs(
        [("Name", "Ashanthi Machado"), ("Age / Sex", "71 / Female"), ("UHID", "SHH-0092841")],
        [("Collected", "19 Aug 2026, 03:10"), ("Reported", "19 Aug 2026, 04:05"),
         ("Referred by", "Dr A. Anand")],
    )
    p.rule()
    p.at(50, "INVESTIGATION", 14, True); p.at(430, "RESULT", 14, True)
    p.at(600, "UNIT", 14, True); p.at(720, "BIOLOGICAL REF. INTERVAL", 14, True)
    p.y += 24
    p.rule(1, INK, 14)

    rows = [
        ("CARDIAC MARKERS", None, None, None, None),
        ("Troponin I", "0.94", "ng/mL", "< 0.04", "HIGH"),
        ("CK-MB", "38", "U/L", "0 - 25", "HIGH"),
        ("HAEMATOLOGY", None, None, None, None),
        ("Haemoglobin", "11.2", "g/dL", "12.0 - 15.0", "LOW"),
        ("Total leucocyte count", "9,400", "/cu.mm", "4,000 - 11,000", None),
        ("Platelet count", "2.640", "lakh/cu.mm", "1.5 - 4.1", None),
        ("BIOCHEMISTRY", None, None, None, None),
        ("Creatinine", "1.3", "mg/dL", "0.6 - 1.1", "HIGH"),
        ("Urea", "34", "mg/dL", "17 - 43", None),
        ("Sodium", "138", "mmol/L", "136 - 145", None),
        ("Potassium", "4.2", "mmol/L", "3.5 - 5.1", None),
        ("HbA1c", "8.4", "%", "< 5.7 (non-diabetic)", "HIGH"),
        ("Total cholesterol", "228", "mg/dL", "< 200", "HIGH"),
    ]
    for name, value, unit, ref, flag in rows:
        if value is None:
            p.y += 6
            p.text(50, name, 14, True, fill=(70, 90, 110), gap=24)
            continue
        p.at(66, name, 17)
        p.at(430, value, 17, bold=bool(flag), fill=FLAG if flag else INK)
        p.at(600, unit, 16, fill=MUTED)
        p.at(720, ref, 16, fill=MUTED)
        if flag:
            p.at(930, flag, 13, True, fill=FLAG)
        p.y += 27
    p.y += 8
    p.rule()
    p.para("Results relate only to the sample tested. Please correlate clinically.", 50, 15, 120)
    p.text(50, "Dr M. Sundari,  MD (Pathology),  Reg. No. TN/38812", 16, fill=MUTED, gap=24)
    return p.finish(out)


def prescription(out: Path) -> Path:
    p = Page(1200)
    p.head("Sacred Heart Hospital — Cardiology OPD",
           "Palayamkottai Road, Thoothukudi, Tamil Nadu 628002",
           "Dr A. Anand,  MD DM (Cardiology)   ·   Reg. No. TN/54129")
    p.title("PRESCRIPTION", "OP/2026/22914")
    p.pairs(
        [("Name", "Ashanthi Machado"), ("Age / Sex", "71 / Female")],
        [("Date", "22 Aug 2026"), ("UHID", "SHH-0092841")],
    )
    p.rule()
    p.section("Diagnosis")
    p.para("Non-ST elevation acute coronary syndrome. Post angioplasty with stent to the "
           "left anterior descending artery. Hypertension. Type 2 diabetes mellitus.")
    p.section("Allergies")
    p.para("PENICILLIN — rash and angioedema. Do not prescribe beta-lactams without review.")
    p.section("Rx")
    for line in [
        "Tab ASPIRIN 75 mg          1 - 0 - 0      after breakfast      30 days",
        "Tab CLOPIDOGREL 75 mg      1 - 0 - 0      after breakfast      30 days",
        "Tab ATORVASTATIN 40 mg     0 - 0 - 1      at night             30 days",
        "Tab TELMISARTAN 40 mg      1 - 0 - 0      morning              30 days",
        "Tab METFORMIN 500 mg       1 - 0 - 1      after meals          30 days",
        "Tab PANTOPRAZOLE 40 mg     1 - 0 - 0      before breakfast     30 days",
    ]:
        p.text(66, line, 17, gap=30)
    p.y += 8
    p.section("Advice")
    p.bullets(["Review on 05 Sep 2026 with repeat lipid profile.",
               "Salt restricted diabetic diet.",
               "Do not stop antiplatelet medication without consulting."])
    return p.finish(out)


def policy_schedule(out: Path) -> Path:
    p = Page(1500)
    p.head("Star Health and Allied Insurance Co. Ltd.",
           "Family Health Optima Insurance Plan — Policy Schedule",
           "IRDAI Reg. No. 129   ·   UIN: SHAHLIP26001V012526")
    p.title("POLICY SCHEDULE", "SH-NRI-4471902")
    p.pairs(
        [("Insured", "Ashanthi Machado"), ("Age", "71"), ("Proposer", PROPOSER)],
        [("Policy period", "01 Apr 2026 to 31 Mar 2027"), ("Sum insured", "INR 5,00,000"),
         ("Cashless", "Yes, at network hospitals")],
    )
    p.rule()
    p.section("Sub-limits per day of hospitalisation")
    p.at(66, "BENEFIT", 14, True); p.at(560, "LIMIT", 14, True); p.y += 24
    for label, limit in [
        ("Room rent (normal ward)", "1% of sum insured per day  =  INR 5,000"),
        ("ICU / intensive care", "2% of sum insured per day  =  INR 10,000"),
        ("Ambulance charges", "INR 2,000 per hospitalisation"),
        ("Cataract, per eye", "INR 40,000"),
    ]:
        p.at(66, label, 17); p.at(560, limit, 17, bold=True); p.y += 28
    p.y += 10

    p.section("Proportionate deduction")
    p.para("If the room category occupied is higher than the eligible limit, all associated "
           "medical expenses — other than the cost of medicines, consumables and implants — "
           "shall be reduced in the same proportion as the eligible room rent bears to the "
           "actual room rent charged.")
    p.section("Co-payment")
    p.para("10 percent of every admissible claim, applicable to insured persons above 60 "
           "years at the time of first enrolment.")
    p.section("Items not payable")
    p.para("Registration and admission kit, gloves, PPE kits, toiletries, attendant charges, "
           "telephone, food for attendants, cosmetics and other items listed as non-payable "
           "under the IRDAI list of expenses generally excluded.")
    p.section("Network hospitals in this district")
    p.para("Sacred Heart Hospital, Thoothukudi.  Sundaram Arulrhaj Hospitals, Thoothukudi.")
    p.rule()
    p.para("This schedule is a summary. The policy wording governs in case of any "
           "difference.", 50, 15, 120)
    return p.finish(out)


DOCS = {
    "discharge_summary": discharge_summary,
    "lab_report": lab_report,
    "prescription": prescription,
    "policy_schedule": policy_schedule,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/docs")
    parser.add_argument("--only", default="", help="one of: " + ", ".join(DOCS))
    args = parser.parse_args()

    out = Path(args.out).expanduser()
    for name, fn in DOCS.items():
        if args.only and args.only != name:
            continue
        path = fn(out / f"doc_{name}.png")
        print(f"  {path}  ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
