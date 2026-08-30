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
from anbu_care.memory import lessons
from anbu_care.provenance.store import PARENT_SUBJECT, get_store
from anbu_care.schemas import WellbeingEntry

SELF_REPORTED = "self-reported"


def entry_sk(received_at_iso: str, entry_id: str) -> str:
    # Timestamp first so a prefix query returns them in order.
    return f"WELLBEING#{received_at_iso}#{entry_id}"


def record(parent_id: str, source: str, text: str, channel: str = "whatsapp",
           source_kind: str = "text", audio_object: str | None = None,
           phase: str = "acute", prompt_id: str | None = None) -> WellbeingEntry:
    """Store a check-in and receipt it.

    The receipt carries a hash of the text, never the text. Chain verification
    is public — it proves integrity without revealing content — so a receipt
    holding the words would leak "chest hurts, dizzy" to anyone who can reach
    the verify endpoint. The hash still proves the stored entry was not altered
    afterwards, which is the property the chain is there for.
    """
    # WHAT SURVIVES THE CASE. The entry and its receipt belong to this
    # admission and end with it. That she answers by voice rather than by
    # typing is true of her, not of the admission, so it is the one thing here
    # worth carrying to the next one. Only her own messages teach it: a son
    # typing on her behalf says nothing about what she can do.
    if source == SELF_REPORTED:
        lessons.remember_in_background(
            lessons.remember_reply_mode, parent_id,
            lessons.VOICE if source_kind == "voice" else lessons.TEXT,
        )
        # And which language she used, which her profile only ever guessed at.
        # Off the request path for the same reason: this costs a model call,
        # and Twilio abandons a webhook that is still owed a reply.
        lessons.remember_in_background(lessons.learn_language_from, parent_id, text)

    entry = WellbeingEntry(
        entry_id=service.new_id("wb"),
        parent_id=parent_id,
        source=source,
        text=text,
        channel=channel,
        source_kind=source_kind,
        audio_object=audio_object,
        phase=phase,
        prompt_id=prompt_id,
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
            "source_kind": source_kind,
            # Named, never the URL: a signed link expires and a receipt holding
            # a dead link reads as proof of something it cannot support.
            "audio_object": audio_object,
            # Which part of the story, and which question it answers. Both come
            # from stored state — an open window, a prompt receipt — never from
            # reading the words that were just hashed above.
            "phase": phase,
            "prompt_id": prompt_id,
            # Said explicitly so nothing downstream mistakes this for a finding.
            # The recovery wording is the same promise in the same shape: a
            # recovery check-in is still her words and still not an assessment.
            "note": ("recovery check-in — self-reported, not a clinical assessment"
                     if phase == "recovery" else
                     "self-reported words, not a clinical assessment"),
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
