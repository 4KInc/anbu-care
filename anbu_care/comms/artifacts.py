"""Documents the platform generates, and may therefore attach to a message.

Nothing here fetches a hospital's file. Ingested documents are parsed into
observations and never stored as bytes, so there is no discharge summary lying
around to attach by accident — the only attachable thing is one this module
builds from deterministic case data.

That is the point, not a limitation. Because the text is generated here, it can
be run through the same clinical classifier that gates the message body, so the
gate is never blind to what is riding along with a message. An attachment
fetched from elsewhere could not offer that.

Two rules hold the boundary:

1. Only kinds on ATTACHABLE are ever built. A caller cannot name an arbitrary
   document and have it packaged.
2. The rendered text is classified before the artifact is released. If a claim
   reason ever quotes a lab value, the artifact is refused — the allowlist is
   not trusted to be sufficient on its own.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from anbu_care.schemas import Adjudication

# The only artifact kinds that may be generated. Clinical documents are absent
# by design: Meta's healthcare policy and DPDP put them out of reach of
# WhatsApp regardless of how securely they are transported.
ATTACHABLE = {"claim_summary"}


@dataclass(frozen=True)
class Artifact:
    """A generated document, its text, and proof of what was in it."""

    kind: str
    filename: str
    text: str
    pdf: bytes
    sha256: str

    def as_receipt_payload(self) -> dict[str, str | int]:
        return {
            "kind": self.kind,
            "filename": self.filename,
            "sha256": self.sha256,
            "bytes": len(self.pdf),
        }


class ArtifactRefused(Exception):
    """Raised when an artifact may not be built or released."""


def _inr(amount: int) -> str:
    """Indian digit grouping: 12,34,567 rather than 1,234,567."""
    s = str(abs(amount))
    if len(s) <= 3:
        grouped = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts) + "," + tail
    return ("-" if amount < 0 else "") + grouped


def render_claim_summary(adj: Adjudication) -> str:
    """The claim outcome as plain text, built only from adjudicator output.

    No LLM writes this. Every figure is one the deterministic adjudicator
    already produced and the receipt chain already recorded, so the document
    cannot disagree with the audit trail.
    """
    lines = [
        "ANBU CARE — CLAIM SUMMARY",
        "SIMULATED TPA. Deterministic local rules, not an insurer.",
        "",
        f"Case: {adj.case_id}",
        f"Submission: {adj.submission_id}",
        f"Outcome: {adj.outcome.value}",
        "",
    ]

    if adj.lines:
        lines.append("Assessed lines")
        for line in adj.lines:
            lines.append(
                f"  {line.item}: claimed INR {_inr(line.claimed_inr)}, "
                f"allowed INR {_inr(line.allowed_inr)}"
            )
            lines.append(f"    Rule applied: {line.rule}")
        lines.append("")

    # An unpriced outcome has no payable figure. Printing zero would read as a
    # decision that was never made, so it is named as unknown instead.
    if adj.outcome.value in {"QUERY", "DENY"}:
        lines.append("Payable amount: not yet known at this stage.")
    else:
        lines.append(f"Total claimed: INR {_inr(adj.total_claimed_inr)}")
        lines.append(f"Total allowed: INR {_inr(adj.total_allowed_inr)}")
        lines.append(f"Total disallowed: INR {_inr(adj.total_disallowed_inr)}")
    lines.append("")

    if adj.reasons:
        lines.append("Reasons")
        lines.extend(f"  {r}" for r in adj.reasons)
        lines.append("")

    if adj.missing_documents:
        lines.append("Documents still required")
        lines.extend(f"  {d}" for d in adj.missing_documents)
        lines.append("")

    lines.append(f"Assessed at: {adj.adjudicated_at.isoformat()}")
    lines.append("Clinical detail is not included here. It is in the secure dashboard.")
    return "\n".join(lines)


def _pdf_of(text: str, title: str) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_title(title)
    pdf.set_font("Helvetica", size=10)
    for line in text.split("\n"):
        if not line:
            pdf.ln(4)
            continue
        # Latin-1 is all the core fonts carry. The rupee sign is not in it,
        # which is why every amount is written as "INR" rather than a symbol.
        safe = line.encode("latin-1", "replace").decode("latin-1")
        # Reset to the left margin each time, or fpdf leaves the cursor at the
        # right edge and the next cell has no room for a single character.
        pdf.multi_cell(0, 5, safe, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    out = io.BytesIO()
    pdf.output(out)
    return out.getvalue()


def build(kind: str, adjudication: Adjudication) -> Artifact:
    """Build an attachable artifact, or refuse.

    Refusal is not an error path to be smoothed over: a caller asking for a
    clinical document, or a document whose text trips the classifier, must get
    nothing rather than something.
    """
    if kind not in ATTACHABLE:
        raise ArtifactRefused(
            f"'{kind}' is not an attachable artifact kind. Attachable: {sorted(ATTACHABLE)}. "
            "Clinical documents are excluded by policy, not by omission."
        )

    text = render_claim_summary(adjudication)

    # The allowlist says what may be built. This says what may leave. Both,
    # because a reason string is free text and could one day quote a reading.
    from anbu_care.comms.policy import classify_message
    from anbu_care.schemas import MessageClass

    actual, hits = classify_message(text)
    if actual is MessageClass.CLINICAL:
        raise ArtifactRefused(
            "Artifact refused: its text carries clinical detail (" + ", ".join(hits) + "). "
            "It may not be attached to a WhatsApp message."
        )

    pdf = _pdf_of(text, f"Anbu Care claim summary {adjudication.case_id}")
    return Artifact(
        kind=kind,
        filename=f"anbu-care-claim-{adjudication.case_id}.pdf",
        text=text,
        pdf=pdf,
        sha256=hashlib.sha256(pdf).hexdigest(),
    )
