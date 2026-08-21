"""Wellbeing check-ins: stored, receipted, and never interpreted.

A check-in is what a parent or a caregiver said. It is kept as the words they
used, with a source label and a timestamp, and that is the whole record. There
is no mood field to fill in, no score to compute, and no health state to
derive, because a sentence typed on a phone is not a measurement and the moment
it is treated as one the system is inventing clinical findings.

Entries live under the parent, not a case. Most check-ins arrive when nothing
is wrong, which is exactly when there is no case to attach them to, so the
receipt goes on a parent-scoped chain.
"""

from __future__ import annotations

import hashlib

from anbu_care import service
from anbu_care.provenance.store import PARENT_SUBJECT, get_store
from anbu_care.schemas import WellbeingEntry

SELF_REPORTED = "self-reported"


def entry_sk(received_at_iso: str, entry_id: str) -> str:
    # Timestamp first so a prefix query returns them in order.
    return f"WELLBEING#{received_at_iso}#{entry_id}"


def record(parent_id: str, source: str, text: str, channel: str = "whatsapp") -> WellbeingEntry:
    """Store a check-in and receipt it.

    The receipt carries a hash of the text, never the text. Chain verification
    is public — it proves integrity without revealing content — so a receipt
    holding the words would leak "chest hurts, dizzy" to anyone who can reach
    the verify endpoint. The hash still proves the stored entry was not altered
    afterwards, which is the property the chain is there for.
    """
    entry = WellbeingEntry(
        entry_id=service.new_id("wb"),
        parent_id=parent_id,
        source=source,
        text=text,
        channel=channel,
    )
    get_store().put(
        f"PARENT#{parent_id}",
        entry_sk(entry.received_at.isoformat(), entry.entry_id),
        entry.model_dump(mode="json"),
    )
    service.append_receipt(
        parent_id,
        kind="wellbeing.recorded",
        actor="wellbeing_intake",
        payload={
            "entry_id": entry.entry_id,
            "source": entry.source,
            "channel": entry.channel,
            "received_at": entry.received_at.isoformat(),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text_length": len(text),
            # Said explicitly so nothing downstream mistakes this for a finding.
            "note": "self-reported words, not a clinical assessment",
        },
        subject=PARENT_SUBJECT,
    )
    return entry


def list_entries(parent_id: str, limit: int = 20) -> list[WellbeingEntry]:
    """Most recent first."""
    rows = get_store().query_prefix(f"PARENT#{parent_id}", "WELLBEING#")
    entries = [WellbeingEntry.model_validate(_clean(r)) for r in rows]
    entries.sort(key=lambda e: e.received_at, reverse=True)
    return entries[:limit]


def latest(parent_id: str) -> WellbeingEntry | None:
    entries = list_entries(parent_id, limit=1)
    return entries[0] if entries else None


def _clean(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in {"pk", "sk"}}
