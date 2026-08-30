#!/usr/bin/env python3
"""Hand-author the architecture diagram, rather than letting a layout engine.

The mermaid version is a correct picture of the system and a poor argument for
it. Auto-layout put the request spine on a diagonal, left a dead zone through
the middle, and drew every edge identically, so a refusal, an autonomous tick
and a human decision all look like the same arrow. On a diagram whose whole
claim is "these three are different", that is the wrong thing to be careless
about.

So the coordinates are chosen here. The spine runs straight down the middle,
band 5 is the widest thing on the page because it is the argument, and edges
carry meaning: orange refuses, teal runs unwatched, dashed needs a person.

    ./.venv/bin/python scripts/build_architecture_svg.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "diagram" / "architecture-diagram.svg"

W, H = 2040, 1245

# THE DASHBOARD'S PALETTE, not a near miss of it. These are lifted from the
# custom properties in anbu_care/webui/index.html, so the diagram, the icon and
# the product a judge opens are the same teal rather than three adjacent ones.
# The first draft of this file used #0e4f52 and a warm ivory, which nobody
# notices alone and everybody notices side by side.
INK = "#0b1c30"          # --ink
MUTE = "#4d5c6e"         # --ink-soft
LINE = "#7d8b9c"         # --ink-mute
TEAL = "#0d7d70"         # --teal, the permitted / verified colour
TEAL_INK = "#065f56"     # --teal-ink
RUST = "#c2313b"         # --red, and it is only ever a refusal here
FILL = "#f6f7fb"         # --surface
EDGE = "#e3e8f0"         # --line
GUARD_FILL = "#fdecec"   # --red-bg
GUARD_EDGE = "#c2313b"   # --red
PROOF_FILL = "#e2f6f2"   # --teal-bg


def esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def box(x, y, w, h, *, num="", title="", lines=(), fill=FILL, edge=EDGE,
        weight=1.0, title_size=15, line_size=13, radius=3, title_top=False):
    """One node. `lines` are already-wrapped strings; nothing wraps itself.

    `title_top` is for the three bands that hold child boxes. Their titles are
    set near the top edge rather than centred, because a centred title lands
    underneath its own children, which is how the first render came out.
    """
    out = [(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
           f'fill="{fill}" stroke="{edge}" stroke-width="{weight}"/>')]
    cx = x + w / 2
    ty = (y + title_size + 9) if title_top else (
        y + (h - (len(lines) * (line_size + 4) + title_size)) / 2 + title_size)
    head = (f'<tspan font-weight="700">{esc(num)}</tspan>  ' if num else "") + esc(title)
    out.append(f'<text x="{cx}" y="{ty}" text-anchor="middle" font-size="{title_size}" '
               f'font-weight="700" fill="{INK}">{head}</text>')
    for i, ln in enumerate(lines):
        italic = ln.startswith("*")
        text = esc(ln.lstrip("*"))
        out.append(
            f'<text x="{cx}" y="{ty + (i + 1) * (line_size + 4)}" text-anchor="middle" '
            f'font-size="{line_size}" fill="{MUTE}"'
            + (' font-style="italic"' if italic else "") + f'>{text}</text>')
    return "\n".join(out)


def label(x, y, text, *, size=12, fill=MUTE, anchor="middle", weight="400", italic=False):
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}"'
            + (' font-style="italic"' if italic else "") + f'>{esc(text)}</text>')


def path(d, *, stroke=LINE, width=1.6, dash="", arrow="arrow"):
    dd = f' stroke-dasharray="{dash}"' if dash else ""
    mk = f' marker-end="url(#{arrow})"' if arrow else ""
    return (f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{width}"'
            f'{dd}{mk} stroke-linecap="round"/>')


CX = 1020            # the spine

def build() -> str:
    p = []

    # ---- header ---------------------------------------------------------
    p.append(label(60, 62, "Anbu Care", size=34, fill=INK, anchor="start", weight="700"))
    p.append(label(60, 96, "Taskmaster  ·  eldercare coordination for families "
                   "who live somewhere else", size=16, fill=TEAL, anchor="start",
                   weight="700"))
    p.append(label(60, 124, "One Tamil voice note at 3am. Her son is told, the "
                   "neighbour gets a bedside link, cashless cover is filed against "
                   "her policy and a 1-hour regulatory clock starts. Nobody pressed "
                   "anything.", size=14, fill=MUTE, anchor="start"))
    p.append(label(60, 148, "5 agents on Google ADK  ·  Gemini 3.5 Flash and 2.5 "
                   "Flash Lite on Vertex AI  ·  Cloud Run, asia-south1  ·  1,221 "
                   "tests  ·  verified live 30 Aug 2026",
                   size=12.5, fill=LINE, anchor="start"))
    p.append(f'<line x1="60" y1="172" x2="{W-60}" y2="172" stroke="{EDGE}" stroke-width="1"/>')
    p.append(label(60, 196, "FULL SYSTEM", size=12, fill=MUTE, anchor="start",
                   weight="700"))

    # ---- 1 inbound ------------------------------------------------------
    p.append(box(CX - 330, 216, 660, 92, num="1 ·", title="INBOUND",
                 lines=[("her Tamil voice note  ·  a photographed bill, report or "
                        "discharge summary"),
                        "*the bedside handset, bound to one case for 60 minutes"]))

    # ---- Cloud Scheduler, right ------------------------------------------
    p.append(box(1640, 216, 340, 92, num="", title="Cloud Scheduler",
                 lines=["recovery tick  ·  claims SLA tick",
                        "*Cloud Run holds no timer"]))
    p.append(path("M 1640 262 C 1520 262 1480 300 1400 336"
                  , stroke=TEAL, width=2.2))
    p.append(label(1560, 316, "unprompted, every minute", size=12, fill=TEAL))

    # ---- 2 cloud run -----------------------------------------------------
    p.append(path(f"M {CX} 308 L {CX} 336"))
    p.append(box(CX - 290, 336, 580, 76, num="2 ·", title="Cloud Run  ·  anbu-care",
                 lines=["ADK API  ·  Twilio webhook  ·  family dashboard"]))

    # ---- Gemini, left ----------------------------------------------------
    p.append(box(60, 452, 330, 108, num="", title="Gemini on Vertex AI",
                 lines=["3.5 Flash  ·  vision, Tamil, translation",
                        "2.5 Flash Lite  ·  which language she writes in",
                        "*proposes, never decides"]))
    p.append(path("M 390 490 C 500 490 560 470 640 458", dash="5 5"))
    p.append(label(520, 478, "asks", size=12))
    p.append(path("M 640 528 C 560 540 500 545 390 545", dash="5 5"))
    p.append(label(530, 566, "terms and transcripts,", size=11.5))
    p.append(label(530, 581, "never verdicts", size=11.5))

    # ---- 3 coordinator ---------------------------------------------------
    p.append(path(f"M {CX} 412 L {CX} 440"))
    p.append(box(CX - 240, 440, 480, 68, num="3 ·", title="Coordinator  ·  root_agent"))

    # ---- 4 agents --------------------------------------------------------
    p.append(path(f"M {CX} 508 L {CX} 552"))
    p.append(label(CX + 62, 536, "delegate", size=12))
    p.append(box(640, 552, 760, 104, num="4 ·", title="Five sub-agents, isolated tool scopes",
                 title_top=True))
    names = ["Onboarding / KB", "Triage", "Evidence / STEP_UP", "Insurer liaison",
             "WhatsApp comms"]
    bw, gap = 138, 8
    x0 = 640 + (760 - (len(names) * bw + (len(names) - 1) * gap)) / 2
    for i, n in enumerate(names):
        bx = x0 + i * (bw + gap)
        p.append(f'<rect x="{bx}" y="604" width="{bw}" height="34" rx="3" '
                 f'fill="#ffffff" stroke="{EDGE}"/>')
        p.append(label(bx + bw / 2, 626, n, size=11.5, fill=INK))

    # ---- 5 the guard layer, the hero -------------------------------------
    p.append(path(f"M {CX} 656 L {CX} 700", width=2.6, stroke=TEAL))
    p.append(label(CX + 168, 684, "every action, without exception", size=12.5,
                   fill=TEAL, weight="700"))
    p.append(box(150, 700, 1740, 168, num="5 ·",
                 title="DETERMINISTIC GUARD LAYER  ·  code, not prompts",
                 lines=["*the one layer no agent can reach past, argue with, or widen"],
                 fill=GUARD_FILL, edge=GUARD_EDGE, weight=2.2, title_size=16,
                 title_top=True))
    guards = [("RED_FLAGS table", "severity decided here"),
              ("Comms gate", "clinical content, 24h window"),
              ("Consent, per purpose", "read live, every send"),
              ("Payment enforcer", "9 guards  ·  pays her share only"),
              ("Booking enforcer", "12 guards  ·  must be cancellable"),
              ("Claim and result guards", "one claim, one order, or neither")]
    gw, ggap = 268, 12
    gx0 = 150 + (1740 - (len(guards) * gw + (len(guards) - 1) * ggap)) / 2
    for i, (t, sub) in enumerate(guards):
        bx = gx0 + i * (gw + ggap)
        p.append(f'<rect x="{bx}" y="782" width="{gw}" height="62" rx="3" '
                 f'fill="#ffffff" stroke="{GUARD_EDGE}" stroke-width="1.1"/>')
        p.append(label(bx + gw / 2, 806, t, size=12.5, fill=INK, weight="700"))
        p.append(label(bx + gw / 2, 826, sub, size=11.5, fill=MUTE, italic=True))

    # refusal, out to the left
    p.append(path("M 150 812 C 90 812 74 856 74 900", stroke=RUST, width=2, dash="4 4"))
    p.append(box(60, 900, 300, 86, num="", title="refused, and receipted",
                 lines=["*a block is evidence the",
                        "*boundary held"],
                 fill=GUARD_FILL, edge=GUARD_EDGE))

    # ---- 6 state ---------------------------------------------------------
    p.append(path(f"M {CX} 868 L {CX} 906", width=2.6, stroke=TEAL))
    p.append(label(CX + 132, 892, "one receipt per action", size=12.5, fill=TEAL))
    p.append(box(430, 906, 1180, 130, num="6 ·", title="State and evidence",
                 title_top=True))
    stores = [("Firestore", "single-table PK/SK", "hash-chained receipts"),
              ("Cloud Storage", "photographs, screenshots", "private, signed URLs"),
              ("Pub/Sub", "intake · case · claim", "multi-day admission"),
              ("Memory Bank", "Vertex Agent Engine", "lessons that outlive a case")]
    sw, sgap = 268, 14
    sx0 = 430 + (1180 - (len(stores) * sw + (len(stores) - 1) * sgap)) / 2
    for i, (t, a, b) in enumerate(stores):
        bx = sx0 + i * (sw + sgap)
        p.append(f'<rect x="{bx}" y="956" width="{sw}" height="66" rx="3" '
                 f'fill="#ffffff" stroke="{EDGE}"/>')
        p.append(label(bx + sw / 2, 978, t, size=12.5, fill=INK, weight="700"))
        p.append(label(bx + sw / 2, 995, a, size=11, fill=MUTE, italic=True))
        p.append(label(bx + sw / 2, 1011, b, size=11, fill=MUTE, italic=True))

    # ---- 7 outbound ------------------------------------------------------
    p.append(path(f"M {CX} 1036 L {CX} 1074"))
    p.append(box(CX - 430, 1074, 860, 96, num="7 ·", title="OUTBOUND  ·  only what the gate permitted",
                 lines=[("the family alert  ·  the care-circle notice  ·  her daily "
                        "check-in, in Tamil"),
                        ("*the treating team's scoped link  ·  a filled claim form, "
                        "unsigned")]))

    # booker, bottom left
    p.append(box(60, 1074, 330, 96, num="", title="anbu-care-booker",
                 lines=["a real headless browser on",
                        "a real clinic's booking form",
                        "*deployed apart: Chromium will not fit"]))
    p.append(path("M 490 1122 L 585 1122", arrow="arrow"))
    p.append(label(538, 1112, "submits", size=11.5))

    # verify, bottom right
    p.append(box(1690, 1074, 290, 96, num="8 ·", title="GET /verify",
                 lines=["public, no credential",
                        "*anyone can check the chain,",
                        "*including you"],
                 fill=PROOF_FILL, edge=TEAL_INK, weight=2))
    p.append(path("M 1610 971 C 1700 971 1836 1000 1836 1074", stroke=TEAL, width=2))

    # human decision
    p.append(path(f"M {CX + 250} 1170 C {CX + 250} 1200 {CX + 120} 1206 {CX + 40} 1206",
                  dash="6 5", stroke=MUTE, arrow=""))
    p.append(label(CX + 300, 1196, "a person opens the payment link, and signs the claim form",
                   size=11.5, anchor="start", italic=True))

    # ---- legend ----------------------------------------------------------
    ly = 1225
    items = [(RUST, "4 4", "Refusal path", "blocked, refused, or withheld"),
             (TEAL, "", "Autonomous loop", "runs unwatched, nobody asked"),
             (MUTE, "6 5", "Human decision required", "the two things it will not do alone")]
    lx = 60
    for colour, dash, name, sub in items:
        p.append(f'<line x1="{lx}" y1="{ly-4}" x2="{lx+34}" y2="{ly-4}" stroke="{colour}" '
                 f'stroke-width="2.4"' + (f' stroke-dasharray="{dash}"' if dash else "") + '/>')
        p.append(label(lx + 44, ly, name, size=12, fill=INK, anchor="start", weight="700"))
        p.append(label(lx + 44 + len(name) * 6.9 + 10, ly, sub, size=12, fill=MUTE,
                       anchor="start"))
        lx += 44 + len(name) * 6.9 + 12 + len(sub) * 6.1 + 46

    defs = (f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{LINE}"/></marker></defs>')

    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'font-family="Helvetica Neue, Helvetica, Arial, sans-serif">'
            f'{defs}<rect width="{W}" height="{H}" fill="#ffffff"/>'
            + "\n".join(p) + "</svg>")


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build())
    print(f"  {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
