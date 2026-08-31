#!/usr/bin/env python3
"""Build the architecture PDF the Devpost form asks for.

Colours are the dashboard's own custom properties, so this page, the diagram
on page one and the product a judge opens are the same teal.

The form takes a PNG, and a PNG of a 3,564-pixel-wide diagram is a picture of
some text. A judge opens it, cannot read the guard band, and moves on. PDF keeps
the mermaid output as vectors, so the fifth band is legible at any zoom.

Page two is the part a diagram cannot carry. It says which pillars are Google
managed and the exact command that proves each one, and it lists what the system
refuses to do. Both are claims a judge can check in under a minute, which is the
whole argument this project makes about itself.

    ./.venv/bin/python scripts/make_architecture_pdf.py

Rebuild the diagram first if it changed:

    ./.venv/bin/python scripts/build_architecture_svg.py

Also re-renders docs/architecture.png, the one the README shows, from the same
SVG. One source, so the README and the submission cannot drift apart.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SVG = ROOT / "docs" / "diagram" / "architecture-diagram.svg"
PNG = ROOT / "docs" / "architecture.png"
HTML = ROOT / "docs" / "diagram" / "architecture.html"
PDF = ROOT / "docs" / "diagram" / "AnbuCare-Architecture.pdf"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Each row is a claim and the command that settles it. Nothing here is a
# capability list: a pillar without a check is a pillar somebody has to take on
# trust, which is the opposite of what this project is arguing.
MANAGED = [
    ("Cloud Run", ("Agent API, Twilio webhook, dashboard. A second service runs "
     "the headless browser: Chromium will not fit beside the API."),
     "gcloud run services list --region asia-south1"),
    ("Firestore", ("Case state and the hash-chained receipt ledger, single-table "
     "PK/SK."),
     "curl -s $URL/api/cases/case-da1c2cb6db/verify"),
    ("Cloud Scheduler", ("The recovery tick and the claims SLA tick. Cloud Run "
     "holds no timer, which is what makes the regulatory clocks real."),
     "gcloud scheduler jobs list --location asia-south1"),
    ("Pub/Sub", "Intake, case and claim events across a multi-day admission.",
     "gcloud pubsub topics list"),
    ("Cloud Storage", ("Photographs, booking screenshots, claim forms. Private, "
     "reached only through signed URLs that expire."),
     "curl -o /dev/null -w '%{http_code}' $URL/api/parents/<id>   -> 401"),
    ("Vertex AI", ("Gemini 3.5 Flash for the agent fleet, document vision, Tamil "
     "transcription and translation. Gemini 2.5 Flash Lite for one question with "
     "a two-letter answer."),
     "curl -s $URL/api/healthz"),
    ("Agent Engine Memory Bank", ("The one store that outlives a case. Recall is "
     "an exact scope lookup, never a similarity search."),
     "ANBU_MEMORY_BANK_LIVE=... pytest -m memory_bank"),
    ("Google Places", ("Every hospital and diagnostic centre carries a place_id "
     "and a verification date, so distance is real."),
     "Shown on every triage call, with the seed date"),
]

# The refusals. These are the architecture: a system defined by what it will not
# do is checkable in a way that a system defined by its features is not.
REFUSALS = [
    ("Severity is never argued down",
     ("“She says it’s probably just gas” still returns HIGH. The "
     "table that decides severity is code, and never reads that sentence as "
     "permission.")),
    ("Clinical detail never leaves over WhatsApp",
     ("The gate classifies the content, not the caller’s claim about it. "
     "Bypass the agent and call send() directly: still blocked.")),
    ("It will not attribute a result it cannot place",
     ("Two tests outstanding and one report arriving closes neither. Deciding "
     "which would be a model choosing which clinical order was carried out.")),
    ("It will not pay the insurer’s share",
     ("Under cashless the insurer settles with the hospital, so only the family’s "
     "residual is paid. INR 27,300 on the paper, INR 9,733 owed.")),
    ("It will not claim an action it did not take",
     ("A booking is recorded as requested, never confirmed, because an "
     "unauthenticated callback form cannot truthfully produce more.")),
    ("It will not put a sentence in memory",
     ("Each lesson has its own function composing its own sentence from a "
     "validated value. A caller cannot store a symptom because a caller cannot "
     "store prose.")),
    ("It will not guess on a form somebody signs",
     ("The Part A claim form prints “not on record” rather than a "
     "plausible value, and is left unsigned.")),
    ("It does not watch anyone",
     ("No sensors, no monitoring. An episode begins because a signal arrives, "
     "and the receipt says so. Tests reject the word “detect” in that path.")),
]


def svg_body() -> str:
    raw = SVG.read_text()
    # Drop the max-width the mermaid CLI bakes in, or the diagram refuses to
    # grow to the page and lands as a small square in the corner.
    raw = re.sub(r'style="max-width:[^"]*"', 'style="width:100%;height:auto"', raw, count=1)
    return raw[raw.index("<svg"):]


def rows(items, cols):
    return "\n".join(
        "<tr>" + "".join(f"<td class='c{i}'>{c}</td>" for i, c in enumerate(row)) + "</tr>"
        for row in items
    ) if cols else ""


def build_html() -> str:
    managed = rows(MANAGED, True)
    refusals = rows(REFUSALS, True)
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Anbu Care architecture</title>
<style>
  @page {{ size: 420mm 297mm; margin: 14mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; color: #0b1c30; background: #fff;
    font: 400 10.5pt/1.45 "Helvetica Neue", Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .page {{ page-break-after: always; }}
  .page:last-child {{ page-break-after: auto; }}
  h1 {{ font-size: 21pt; margin: 0 0 2mm; letter-spacing: -.01em; }}
  .thesis {{ font-size: 11.5pt; color: #4d5c6e; margin: 0 0 5mm; max-width: 250mm; }}
  .rule {{ height: 2.4pt; background: #0d7d70; width: 46mm; margin: 0 0 6mm; }}
  h2 {{ font-size: 13pt; margin: 0 0 3mm; letter-spacing: -.005em; }}
  .sub {{ font-size: 9.5pt; color: #4d5c6e; margin: 0 0 4mm; max-width: 175mm; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12mm; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 2.4mm 3mm 2.4mm 0; border-top: .5pt solid #e3e8f0; vertical-align: top;
        font-size: 9pt; line-height: 1.4; }}
  tr:first-child td {{ border-top: .9pt solid #0d7d70; }}
  td.c0 {{ font-weight: 700; width: 33%; color: #065f56; }}
  td.c2 {{ font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 7.6pt;
           color: #4d5c6e; width: 34%; word-break: break-word; }}
  .refusals td.c0 {{ width: 38%; color: #93000a; }}
  .foot {{ margin-top: 7mm; font-size: 8.5pt; color: #4d5c6e; }}
  .foot b {{ color: #0b1c30; }}
  /* Page 1 is the diagram and nothing else: it already carries its own
     title, subtitle and provenance line, and an HTML header on top of it
     pushed the whole thing onto a second page. */
  .page.diagram {{ display: flex; align-items: center; min-height: 262mm; }}
  svg {{ width: 100%; height: auto; display: block; }}
  /* The mermaid frontmatter title repeats the heading directly above it, and
     carries an em dash the rest of these documents no longer use. */
  .flowchartTitleText {{ display: none; }}
</style>

<div class="page diagram">
  {svg_body()}
</div>

<div class="page">
  <h1>What is managed, and what it refuses</h1>
  <div class="rule"></div>
  <div class="cols">
    <div>
      <h2>Google-managed, and the command that proves it</h2>
      <p class="sub">A pillar with no check beside it is a pillar somebody has
      to take on trust. <code>$URL</code> is
      https://anbu-care-37j4eofpwq-el.a.run.app</p>
      <table>{managed}</table>
    </div>
    <div>
      <h2>What this system will not do</h2>
      <p class="sub">Each of these is enforced in code rather than asked for in
      a prompt, and each has a test that fails if the guard is removed.</p>
      <table class="refusals">{refusals}</table>
    </div>
  </div>
  <p class="foot"><b>Verification is public.</b>
  <code>GET /api/cases/&lt;id&gt;/verify</code> needs no credential, because it
  proves the record was not altered without revealing what it says. Everything
  that returns content is credentialed. <b>1,233 tests</b>, none needing GCP or
  a model. <b>Simulated:</b> the insurer’s adjudicator, and payments run on a
  real Razorpay link in test mode. <b>Not simulated:</b> WhatsApp, the clinic
  booking, the regulatory clocks, hospital locations, and the receipt chain.</p>
</div>
"""


def main() -> int:
    if not SVG.exists():
        print(f"missing {SVG}; run build_architecture_svg.py first", file=sys.stderr)
        return 1
    if not pathlib.Path(CHROME).exists():
        print(f"no Chrome at {CHROME}", file=sys.stderr)
        return 1

    HTML.write_text(build_html())
    PDF.unlink(missing_ok=True)
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={PDF}", HTML.as_uri()],
        check=True, capture_output=True, timeout=120,
    )
    if not PDF.exists():
        print("Chrome produced no file", file=sys.stderr)
        return 1
    print(f"  {PDF.relative_to(ROOT)}  {PDF.stat().st_size / 1024:.0f} KB")

    # The README's image, from the same SVG, so the two cannot disagree.
    shot = ROOT / "docs" / "diagram" / "_png.html"
    shot.write_text(f'<!doctype html><meta charset=utf-8>'
                    f'<style>body{{margin:0}}img{{width:2040px;display:block}}</style>'
                    f'<img src="{SVG.as_uri()}">')
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
         f"--screenshot={PNG}", "--window-size=2040,1245",
         "--force-device-scale-factor=2", shot.as_uri()],
        check=True, capture_output=True, timeout=120)
    shot.unlink(missing_ok=True)
    print(f"  {PNG.relative_to(ROOT)}  {PNG.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
