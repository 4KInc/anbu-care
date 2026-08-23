"""A photographed document that arrived, kept open until it has been read.

Reading a bill takes about fifteen seconds and Twilio abandons a webhook at
roughly the same mark, so the read runs after the response. That trade bought a
reply the family actually receives, and it cost durability: the photograph
lived only in the instance's memory. When Cloud Run replaced the container
mid-read — a deployment, a scale-down, an eviction — the image, the reading and
the promised follow-up went with it. The family had been told "reading it now"
and then heard nothing, which is the single outcome that acknowledgement exists
to rule out.

So the photograph is written down BEFORE the acknowledgement goes out, and the
row recording it stays open until a message has actually been sent about it. An
instance that dies mid-read now loses the attempt, not the document: the next
instance to start sweeps the open rows and reads them again.

Re-reading is safe. Both ingestion lanes reject a repeat of the same image by
its SHA-256, so a retry that races a slow first attempt records one document
rather than two. The worst case is the family being told the bill is already on
file, which is true, and which is a great deal better than silence.

Giving up is also an outcome. After MAX_ATTEMPTS the row is closed as abandoned
and the family is told the photograph could not be read, because a document
that quietly stopped being worked on is indistinguishable from one that was
never received.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from anbu_care import service
from anbu_care.provenance.store import get_store

logger = logging.getLogger(__name__)

# How long a read may run before another instance is entitled to assume the one
# that started it is gone. Comfortably longer than two Gemini calls, so a slow
# read is never stolen from itself.
LEASE = timedelta(seconds=90)

# How long an open row waits before a sweep will touch it. Long enough that the
# ordinary in-process read almost always wins the race and the sweeper finds
# nothing to do.
STALE_AFTER = timedelta(seconds=60)

MAX_ATTEMPTS = 3

OPEN = "open"
DONE = "done"
ABANDONED = "abandoned"

_PREFIX = "INTAKE#"


@dataclass
class Intake:
    """One photograph, and how far it has got."""

    intake_id: str
    parent_id: str
    case_id: str
    mime_type: str
    sha256: str
    object_name: str
    created_at: str
    status: str = OPEN
    attempts: int = 0
    lease_until: str = ""
    detail: str = ""

    @property
    def sk(self) -> str:
        # Timestamp first so a prefix query returns them oldest-first.
        return f"{_PREFIX}{self.created_at}#{self.intake_id}"

    @property
    def pk(self) -> str:
        return f"PARENT#{self.parent_id}"

    @property
    def exhausted(self) -> bool:
        return self.attempts >= MAX_ATTEMPTS


def _now() -> datetime:
    return datetime.now(UTC)


def _save(intake: Intake) -> None:
    get_store().put(intake.pk, intake.sk, asdict(intake))


def record(parent_id: str, case_id: str, image: bytes, mime_type: str) -> Intake | None:
    """Put the photograph somewhere it survives this instance, and open a row.

    Returns None when the image could not be stored, which is the caller's
    signal that the read is best-effort again: durability that silently is not
    there is worse than none, because the acknowledgement would be promising a
    follow-up nothing is holding the document for.
    """
    from anbu_care.comms import storage
    from anbu_care.docvision import read as docvision_read

    digest = docvision_read.image_sha256(image)
    intake_id = service.new_id("intake")
    extension = (mime_type.split("/")[-1] or "jpeg").lower()

    stored = storage.store(f"intake/{parent_id}/{intake_id}.{extension}",
                           image, content_type=mime_type)
    if not stored.stored or not stored.object_name:
        logger.warning("intake not durable for %s: %s", parent_id, stored.detail)
        return None

    intake = Intake(
        intake_id=intake_id, parent_id=parent_id, case_id=case_id,
        mime_type=mime_type, sha256=digest, object_name=stored.object_name,
        created_at=_now().isoformat(),
    )
    _save(intake)
    logger.info("intake %s opened for %s", intake_id, parent_id)
    return intake


def image_for(intake: Intake) -> bytes | None:
    """The photograph back out of the bucket, or None if it is not there."""
    from anbu_care.comms import storage

    return storage.fetch(intake.object_name)


def claim(intake: Intake, owner: str, now: datetime | None = None) -> bool:
    """Take the work, if nobody else holds it.

    Read, check, write, read back. Firestore gives this layer no
    compare-and-set, so two instances sweeping the same second can both believe
    they won. That is survivable rather than fixed here: the SHA-256 dedupe in
    both ingestion lanes means the loser records nothing, and the cost of the
    race is a redundant message, not a duplicated document.
    """
    moment = now or _now()
    current = _load(intake.pk, intake.sk)
    if current is None or current.status != OPEN:
        return False
    if current.lease_until and current.lease_until > moment.isoformat():
        return False

    current.attempts += 1
    current.lease_until = (moment + LEASE).isoformat()
    current.detail = f"claimed by {owner}"
    _save(current)

    confirmed = _load(intake.pk, intake.sk)
    return confirmed is not None and confirmed.detail == current.detail


def finish(intake: Intake, status: str = DONE, detail: str = "") -> None:
    """Close the row. Called once a message about this document has been sent."""
    current = _load(intake.pk, intake.sk) or intake
    current.status = status
    current.lease_until = ""
    current.detail = detail[:200]
    _save(current)
    logger.info("intake %s closed as %s", current.intake_id, status)


def release(intake: Intake, detail: str = "") -> None:
    """Put the row back for another attempt, without spending the lease."""
    current = _load(intake.pk, intake.sk) or intake
    current.status = OPEN
    current.lease_until = ""
    current.detail = detail[:200]
    _save(current)


def stale(now: datetime | None = None) -> list[Intake]:
    """Open rows old enough that whoever was reading them is presumed gone.

    Oldest first, so a backlog is worked in the order the family sent it.
    """
    moment = now or _now()
    cutoff = (moment - STALE_AFTER).isoformat()
    rows = get_store().query_sk_prefix_across(_PREFIX)

    out: list[Intake] = []
    for row in rows:
        intake = _from_row(row)
        if intake is None or intake.status != OPEN:
            continue
        if intake.created_at > cutoff:
            continue
        if intake.lease_until and intake.lease_until > moment.isoformat():
            continue
        out.append(intake)

    out.sort(key=lambda i: i.created_at)
    return out


def _load(pk: str, sk: str) -> Intake | None:
    return _from_row(get_store().get(pk, sk))


def _from_row(row: dict | None) -> Intake | None:
    if not row:
        return None
    fields = {k: v for k, v in row.items() if k not in {"pk", "sk"}}
    try:
        return Intake(**fields)
    except TypeError:
        logger.warning("unreadable intake row: %s", sorted(fields))
        return None
