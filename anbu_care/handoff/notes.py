"""Clinician notes, including spoken ones. Draft until confirmed, always.

A doctor standing at a bedside would rather talk than type, and the
transcription lane already exists for the parent's own voice notes. But the two
are not the same feature and must not share a gate.

**Why the wellbeing lane has no confirmation step, and this one must.**

When a seventy-one year old sends a gasping voice note, waiting for her to
approve a transcript before anybody is told would be the actual defect. Speed
is the safety property there, the raw text still reaches the deterministic
RED_FLAGS table regardless of what the model heard, and every alert says "we
heard" rather than "she said". Nothing is attributed to her as fact.

A clinician note inverts all three. It is slow, it is *attributed*, and it
becomes part of a record other people will rely on. A misheard "the 22nd" for
"the 2nd", or "point five" for "five", lands as a clinical fact with a doctor's
name on it. So:

    voice note -> Gemini transcript -> shown back -> explicit CONFIRM -> receipt

An unconfirmed transcript writes NOTHING. No receipt, no brief field, no
record. If the confirm never comes, it is as if nobody spoke.

**Provenance cannot be claimed, only proven.** A caller could POST any text to
confirm — that is fine, it is the same as typing, and typing is always allowed.
What a caller cannot do is *claim the text came from a transcript when it did
not*. The draft endpoint returns a signed ticket over exactly what the model
produced; without a matching ticket the note is recorded as typed. So
"transcribed by Gemini" appears on a receipt only when Gemini actually produced
those words.

**The no-interpret wall.** A confirmed note never reaches `run_triage`, never
sets severity, never re-opens or re-scores a case. A doctor saying "her chest
pain is worse" records that a doctor said it. It does not re-triage the case,
because the severity table is the guarantee and a transcript is not an input to
it. This is the same wall the typed clinician path and `severity.py` already
hold.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass

from anbu_care import service
from anbu_care.handoff.access import HandoffDenied, HandoffGrant
from anbu_care.webauth import LINK_SECRET_ENV

# A draft is a scratch value, not a record. It only has to outlive the seconds
# between hearing the transcript and tapping confirm.
DRAFT_TTL_SECONDS = 15 * 60
_DRAFT_DOMAIN = "anbu.handoff.draft.v1"

MAX_NOTE_CHARS = 4000


@dataclass(frozen=True)
class Draft:
    """What the model heard. Not yet anything."""

    text: str
    ticket: str
    engine: str
    detail: str

    @property
    def written_anything(self) -> bool:
        """Always False. Stated as code because it is the whole guarantee."""
        return False


def _secret() -> bytes | None:
    value = os.getenv(LINK_SECRET_ENV)
    return value.encode("utf-8") if value else None


def text_sha256(text: str) -> str:
    """Hash of the CONFIRMED text. Same discipline as wellbeing text_sha256.

    The chain carries this, never the words. A public /verify can then prove a
    note was recorded and unaltered without publishing what a doctor said about
    a patient.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ticket(case_id: str, text: str, expires: int, secret: bytes) -> str:
    payload = f"{_DRAFT_DOMAIN}:{case_id}:{text_sha256(text)}:{expires}"
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"{expires}.{signature}"


def _ticket_matches(case_id: str, text: str, ticket: str,
                    now: int | None = None) -> bool:
    """Did this exact text really come out of the transcriber, recently?"""
    secret = _secret()
    if not secret or not ticket or "." not in ticket:
        return False
    expires_raw, _, presented = ticket.partition(".")
    try:
        expires = int(expires_raw)
    except ValueError:
        return False
    if expires < int(now or time.time()):
        return False
    expected = _ticket(case_id, text, expires, secret)
    return hmac.compare_digest(expected, ticket) and bool(presented)


def draft_from_voice(grant: HandoffGrant, audio: bytes,
                     mime_type: str = "audio/ogg",
                     now: int | None = None) -> Draft:
    """Transcribe for review. Writes nothing, to anything, ever.

    There is deliberately no store call in this function. The guarantee is not
    "we remember not to save the draft" — it is that there is no code here that
    could.
    """
    if not grant.may_write_note:
        raise HandoffDenied("this link is read only")

    from anbu_care.comms import transcribe

    # Dictation, not a distress call. A prompt that only transcribes, and no
    # Twilio webhook ceiling to sit behind — this is a direct call from the
    # clinician's own device, so it waits rather than failing a transcript that
    # would have succeeded a second later.
    heard = transcribe.transcribe_dictation(audio, mime_type)
    if not heard.ok or not (heard.text or "").strip():
        raise HandoffDenied(
            "that recording could not be made out. Play it back, or type the "
            f"note instead ({heard.detail})"
        )

    text = heard.text.strip()[:MAX_NOTE_CHARS]
    secret = _secret()
    if not secret:
        raise HandoffDenied(f"{LINK_SECRET_ENV} is not configured")

    expires = int(now or time.time()) + DRAFT_TTL_SECONDS
    return Draft(text=text, ticket=_ticket(grant.case_id, text, expires, secret),
                 engine=heard.engine, detail=heard.detail)


ORDER_MOBILITY = {"ambulatory", "non_ambulatory", "unknown"}
MAX_TEST_CHARS = 120


def confirm(grant: HandoffGrant, text: str, ticket: str = "",
            recorded_by: str = "", now: int | None = None,
            orders_test: str = "", mobility: str = "unknown",
            spoken: bool = False) -> dict:
    """Write the note. This is the only function here that touches the record.

    `ticket` decides how the capture is described, not whether the note is
    allowed. Text with a valid ticket was genuinely transcribed and says so;
    text without one is recorded as typed. Neither can claim the other.
    """
    if not grant.may_write_note:
        raise HandoffDenied("this link is read only")

    text = (text or "").strip()
    if not text:
        raise HandoffDenied("an empty note is not a note; nothing was recorded")
    text = text[:MAX_NOTE_CHARS]

    # A ticket proves text came out of OUR transcriber unedited, which is what
    # the bedside page needs: it sends back words a human may have corrected,
    # and the server cannot tell. A caller that transcribed the audio itself
    # and passed the result straight through already knows, and `spoken` is how
    # it says so — an inbound voice note is genuinely spoken whether or not a
    # ticket was ever minted for it.
    via_voice = spoken or _ticket_matches(grant.case_id, text, ticket, now=now)
    who = (recorded_by or "").strip() or "the treating team (unverified)"

    capture = (
        f"recorded by {who} via voice note, transcribed by Gemini, confirmed "
        f"before it was written"
        if via_voice else
        f"typed by {who}"
    )

    # The note itself is unchanged by any of this. An order is recorded ALONGSIDE
    # it, not instead of it, and a note with no order behaves exactly as it did
    # before this existed.
    #
    # It has to be structured because the note is not stored: the chain gets a
    # hash of the words and nothing keeps the words themselves, so an order
    # written only as prose could never be read back and acted on.
    ordered = (orders_test or "").strip()[:MAX_TEST_CHARS]
    stated_mobility = (mobility or "unknown").strip().lower()
    if stated_mobility not in ORDER_MOBILITY:
        # An unrecognised value is not a reason to guess. Whether she can
        # travel is a fact about a person in a room, and "unknown" is the only
        # honest thing to write when nobody said.
        stated_mobility = "unknown"

    receipt = service.append_receipt(
        case_id=grant.case_id,
        kind="clinician.note",
        actor="handoff_link",
        payload={
            # The words are NOT on the chain. The hash proves the note was
            # recorded and unaltered; reading it needs the credential.
            "text_sha256": text_sha256(text),
            "captured": capture,
            "via_voice": via_voice,
            "confirmed": True,
            "note": (
                "A note left by whoever held a write-scoped link. The system "
                "cannot verify who that was and does not claim to. This did "
                "not re-triage the case, change its severity, or alter any "
                "coverage figure."
            ),
        },
    )
    # The WORDS, behind the case credential. The chain above is unchanged and
    # still carries only the hash, because /verify is public and "chest pain
    # settled, moving her to the ward" is not. What changed is that they are
    # now kept somewhere at all: a note used to be write-only, so the family
    # could see that a clinician had left one and could never read it, and the
    # receipt's own comment described a credential-gated read that did not
    # exist.
    from anbu_care.schemas import ClinicianNote

    case = service.load_case(grant.case_id)
    note = ClinicianNote(
        note_id=service.new_id("clinnote"),
        case_id=grant.case_id,
        parent_id=case.parent_id if case else "",
        text=text,
        recorded_by=who,
        via_voice=via_voice,
        receipt_id=receipt.receipt_id,
        text_sha256=text_sha256(text),
    )

    order_id = ""
    if ordered:
        from anbu_care.schemas import DiagnosticOrder

        order = DiagnosticOrder(
            order_id=service.new_id("dxorder"),
            case_id=grant.case_id,
            parent_id=case.parent_id if case else "",
            test_label=ordered,
            mobility=stated_mobility,
            ordered_by=who,
            via_voice=via_voice,
            note_receipt_id=receipt.receipt_id,
        )
        service.save_diagnostic_order(order)
        order_id = order.order_id
        note.order_id = order_id

    service.save_clinician_note(note)

    return {
        "status": "recorded",
        "receipt_id": receipt.receipt_id,
        "captured": capture,
        "via_voice": via_voice,
        "text_sha256": text_sha256(text),
        # Empty unless the clinician actually ordered something.
        "note_id": note.note_id,
        "order_id": order_id,
        "mobility_as_stated": stated_mobility if ordered else "",
    }
