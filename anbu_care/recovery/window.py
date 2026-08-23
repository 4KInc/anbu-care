"""The recovery window: when check-ins start, and every way they stop.

A window is opened by a fact, not by a judgement. The fact is a discharge
summary arriving on the record — a photograph of a piece of paper somebody was
handed on the way out of a hospital. Nothing here decides she has been
discharged; the paper says so, and this reads the paper.

The two dates are kept apart on purpose:

    discharged_on      what the document said, if the reader could read it
    starts_on          the day the counting actually starts

When the document carried a date, they are the same. When it did not, the
window starts from the moment the document was recorded and the receipt says
`starts_on_source: "recorded_at"` in as many words. Inventing a discharge date
to make day numbering tidy would put a fabricated clinical date on a health
record, which is a strange thing to do for the sake of an ordinal.

The window ends by the calendar. Fourteen days, then it closes. It does not end
because anything decided she was better — nothing in this system is entitled to
that opinion — and it does not extend because anything decided she was not.

Everything that stops it early is read LIVE, at tick time, from the profile:

    consent withdrawn   -> stops on the very next tick, no cached grace
    STOP replied        -> stops immediately, on the message
    window expired      -> closes itself

The consent read is the parent-facing equivalent of revoking a payment mandate.
A family that wants to stop being messaged wants to stop being messaged now,
not at the end of whatever period something last cached.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from anbu_care import service
from anbu_care.comms import consent as consent_purposes
from anbu_care.provenance.store import PARENT_SUBJECT, get_store

logger = logging.getLogger(__name__)

# Defaults, both overridable, because neither is a clinical constant. A
# deployment sets them from its own policy — not from anybody's opinion about
# how long recovery takes.
DEFAULT_WINDOW_DAYS = 14
DEFAULT_HOUR_LOCAL = 9

# How long after a prompt a reply is still read as answering it. A day, because
# she may see the message at nine and answer after lunch, and because a second
# prompt has gone out by the time this expires.
REPLY_WINDOW = timedelta(hours=24)

# Whole-message opt-outs. Deliberately exact matches on the entire trimmed
# body: "stop" alone is an instruction, but "stop the pain" is a symptom and
# must reach the escalation table like any other sentence.
STOP_WORDS = {"stop", "stopp", "unsubscribe", "cancel", "end", "நிறுத்து", "வேண்டாம்"}

OPEN = "open"
STOPPED = "stopped"


def window_days() -> int:
    try:
        return max(1, int(os.getenv("ANBU_RECOVERY_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)))
    except ValueError:
        return DEFAULT_WINDOW_DAYS


def hour_local() -> int:
    try:
        return min(23, max(0, int(os.getenv("ANBU_RECOVERY_HOUR", DEFAULT_HOUR_LOCAL))))
    except ValueError:
        return DEFAULT_HOUR_LOCAL


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _window_sk(window_id: str) -> str:
    return f"RECOVERY#WINDOW#{window_id}"


def prompt_sk(window_id: str, on: date) -> str:
    """The due-slot key. One prompt per window per local day, forever.

    This is the whole idempotency story. A tick that runs twice, a Cloud
    Scheduler that retries, two instances waking at once — all of them compute
    the same key, and the second one finds it already there.
    """
    return f"RECOVERY#PROMPT#{window_id}#{on.isoformat()}"


@dataclass(frozen=True)
class Window:
    window_id: str
    parent_id: str
    case_id: str
    starts_on: date
    days: int
    hour: int
    timezone: str
    status: str
    discharged_on: str | None = None
    starts_on_source: str = "document"
    document_id: str | None = None
    stopped_reason: str = ""

    @property
    def open(self) -> bool:
        return self.status == OPEN

    def last_day(self) -> date:
        return self.starts_on + timedelta(days=self.days - 1)

    def day_number(self, on: date) -> int:
        """1 on the first day. Zero or negative means it has not started."""
        return (on - self.starts_on).days + 1

    def as_row(self) -> dict:
        return {
            "window_id": self.window_id, "parent_id": self.parent_id,
            "case_id": self.case_id, "starts_on": self.starts_on.isoformat(),
            "days": self.days, "hour": self.hour, "timezone": self.timezone,
            "status": self.status, "discharged_on": self.discharged_on,
            "starts_on_source": self.starts_on_source,
            "document_id": self.document_id, "stopped_reason": self.stopped_reason,
        }


def _from_row(row: dict) -> Window:
    return Window(
        window_id=row["window_id"], parent_id=row["parent_id"],
        case_id=row.get("case_id", ""),
        starts_on=date.fromisoformat(row["starts_on"]),
        days=int(row.get("days", DEFAULT_WINDOW_DAYS)),
        hour=int(row.get("hour", DEFAULT_HOUR_LOCAL)),
        timezone=row.get("timezone", "Asia/Kolkata"),
        status=row.get("status", OPEN),
        discharged_on=row.get("discharged_on"),
        starts_on_source=row.get("starts_on_source", "document"),
        document_id=row.get("document_id"),
        stopped_reason=row.get("stopped_reason", ""),
    )


def _parse_date(value: object) -> date | None:
    """A date off a photographed document, or None. Never a guess."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError):
        return None


def open_window(parent_id: str, case_id: str, *, discharged_on: object = None,
                document_id: str | None = None,
                now: datetime | None = None) -> Window | None:
    """Start a recovery window from a recorded discharge. Idempotent per case.

    Returns None when one is already open for this case — a family sending the
    discharge summary twice must not get two streams of morning messages, and
    the duplicate-photograph check upstream does not cover a genuinely
    different photograph of the same discharge.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return None

    for existing in list_windows(parent_id):
        if existing.open and existing.case_id == case_id:
            return None

    moment = now or datetime.now(UTC)
    tz = getattr(profile, "timezone", "Asia/Kolkata")

    read_date = _parse_date(discharged_on)
    starts_on = read_date or moment.astimezone(_zone(tz)).date()

    window = Window(
        window_id=service.new_id("rw"),
        parent_id=parent_id, case_id=case_id,
        starts_on=starts_on, days=window_days(), hour=hour_local(),
        timezone=tz, status=OPEN,
        discharged_on=read_date.isoformat() if read_date else None,
        # Named, not implied. A trace reader must be able to tell a day counted
        # from the paper from a day counted from when we saw the paper.
        starts_on_source="document" if read_date else "recorded_at",
        document_id=document_id,
    )
    get_store().put(f"PARENT#{parent_id}", _window_sk(window.window_id), window.as_row())

    service.append_receipt(
        case_id or parent_id,
        kind="recovery.window_opened",
        actor="recovery",
        payload={
            "window_id": window.window_id,
            "parent_id": parent_id,
            "starts_on": window.starts_on.isoformat(),
            "starts_on_source": window.starts_on_source,
            "discharged_on": window.discharged_on,
            "document_id": document_id,
            "days": window.days,
            "hour_local": window.hour,
            "timezone": window.timezone,
            "note": (
                ("The discharge summary gave a discharge date and the window counts "
                 "from it."
                 if read_date else
                 "The discharge summary carried no readable discharge date, so the "
                 "window counts from when the document was recorded. No discharge "
                 "date has been inferred.")
                + f" Check-ins run for {window.days} days at {window.hour:02d}:00 "
                  f"{window.timezone} and end by the calendar, not because anyone "
                  "decided she is better. They ask and record; they never advise."
            ),
        },
        **({} if case_id else {"subject": PARENT_SUBJECT}),
    )
    return window


def list_windows(parent_id: str) -> list[Window]:
    rows = get_store().query_prefix(f"PARENT#{parent_id}", "RECOVERY#WINDOW#")
    return [_from_row({k: v for k, v in r.items() if k not in {"pk", "sk"}}) for r in rows]


def parents_with_open_windows() -> list[str]:
    """Every parent a tick has to consider.

    A cross-partition scan, which is honest about what it costs: at demo scale
    it is nothing, and at real scale it wants an index rather than a cleverer
    scan. The alternative — a list of "active recovery parents" maintained
    alongside the windows — is a second source of truth that can disagree with
    the first, and the disagreement would show up as a mother who quietly stops
    being asked how she is.
    """
    rows = get_store().query_sk_prefix_across("RECOVERY#WINDOW#")
    return sorted({
        str(r["parent_id"]) for r in rows
        if r.get("status") == OPEN and r.get("parent_id")
    })


def open_window_for(parent_id: str) -> Window | None:
    """The one open window, or None. Most recent if somehow there are several."""
    windows = [w for w in list_windows(parent_id) if w.open]
    if not windows:
        return None
    return max(windows, key=lambda w: w.starts_on)


def _save(window: Window) -> None:
    get_store().put(f"PARENT#{window.parent_id}", _window_sk(window.window_id),
                    window.as_row())


def stop(parent_id: str, reason: str, *, detail: str = "") -> list[Window]:
    """Close every open window for a parent, now. Returns what was closed.

    Called from three places — a withdrawn consent, a STOP reply, an expired
    window — and it writes the same receipt for all of them, because from the
    outside they are one fact: the messages have ended, and here is why.
    """
    closed: list[Window] = []
    for window in list_windows(parent_id):
        if not window.open:
            continue
        ended = Window(**{**window.__dict__, "status": STOPPED, "stopped_reason": reason})
        _save(ended)
        closed.append(ended)
        service.append_receipt(
            window.case_id or parent_id,
            kind="recovery.stopped",
            actor="recovery",
            payload={
                "window_id": window.window_id,
                "parent_id": parent_id,
                "reason": reason,
                "detail": detail,
                "stopped_at": datetime.now(UTC).isoformat(),
                "note": ("No further recovery check-ins will be sent. This was "
                         "read from the live record at the moment it was checked, "
                         "not from anything cached."),
            },
            **({} if window.case_id else {"subject": PARENT_SUBJECT}),
        )
    return closed


def consent_held(parent_id: str) -> bool:
    """Read live off the profile, never cached, every single tick."""
    profile = service.load_profile(parent_id)
    if profile is None:
        return False
    return consent_purposes.RECOVERY_CHECKINS in getattr(profile, "contact_consents", {})


def is_stop_word(body: str) -> bool:
    """Is this whole message an opt-out?

    The entire trimmed body must be one of the words. "stop" is an
    instruction; "stop the pain" is a symptom and has to reach the
    deterministic table like anything else she might say.
    """
    return (body or "").strip().strip(".!").lower() in STOP_WORDS


@dataclass(frozen=True)
class Due:
    """A prompt that should go out now, and the slot it will occupy."""

    window: Window
    day: int
    on: date
    slot: str


def due_now(parent_id: str, now: datetime | None = None) -> Due | None:
    """What is owed to this parent right now, or nothing.

    Every stop condition is evaluated here, in order, against live state. A
    window that should have closed closes on the way past rather than being
    left open and skipped — a window that stays open forever while sending
    nothing is a lie on the record.
    """
    window = open_window_for(parent_id)
    if window is None:
        return None

    if not consent_held(parent_id):
        stop(parent_id, "consent withdrawn",
             detail=(f"'{consent_purposes.RECOVERY_CHECKINS}' is no longer held on "
                     "the profile. Nothing was sent."))
        return None

    moment = (now or datetime.now(UTC)).astimezone(_zone(window.timezone))
    today = moment.date()

    if today > window.last_day():
        stop(parent_id, "window ended",
             detail=(f"The {window.days}-day window that began on "
                     f"{window.starts_on.isoformat()} has run out. It ended by the "
                     "calendar; nobody assessed her as recovered."))
        return None

    day = window.day_number(today)
    if day < 1:
        return None                      # discharge date is in the future
    if moment.hour < window.hour:
        return None                      # too early in her day

    slot = prompt_sk(window.window_id, today)
    if get_store().get(f"PARENT#{parent_id}", slot) is not None:
        return None                      # already sent today

    return Due(window=window, day=day, on=today, slot=slot)


def claim_slot(parent_id: str, due: Due, prompt_id: str, sent: dict) -> None:
    """Mark the slot used, whatever the transport said.

    Written on a failed delivery too. A prompt that was attempted and not
    delivered is receipted as not delivered, and re-attempting it on the next
    tick would turn one missed morning into a burst of catch-up messages at
    whatever hour the tick happened to run.
    """
    get_store().put(f"PARENT#{parent_id}", due.slot, {
        "prompt_id": prompt_id,
        "window_id": due.window.window_id,
        "day": due.day,
        "on": due.on.isoformat(),
        "attempted_at": datetime.now(UTC).isoformat(),
        "delivered": bool(sent.get("delivered")),
    })


def recent_prompt(parent_id: str, now: datetime | None = None) -> dict | None:
    """The most recent prompt still inside its reply window, or None.

    This is the entire basis for labelling a reply as recovery-phase. It reads
    two stored facts — a prompt went out, it went out recently — and nothing
    about what she wrote.
    """
    moment = now or datetime.now(UTC)
    rows = get_store().query_prefix(f"PARENT#{parent_id}", "RECOVERY#PROMPT#")
    best: dict | None = None
    for row in rows:
        try:
            at = datetime.fromisoformat(str(row.get("attempted_at")))
        except (TypeError, ValueError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if moment - at > REPLY_WINDOW or at > moment:
            continue
        if best is None or at > datetime.fromisoformat(str(best["attempted_at"])):
            best = row
    return best
