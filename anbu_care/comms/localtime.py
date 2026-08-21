"""Times, in the timezone of whoever is reading them.

"15:46 UTC" is a number nobody lives in. A son in California and a neighbour
two streets away from the hospital need the same instant expressed two
different ways, and both need it without doing arithmetic at 2am.

The parent's local time is carried alongside because it changes the meaning. A
message sent at 2am reads differently from one sent after lunch, and the reader
cannot infer that from their own clock when they are half a world away.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def in_zone(moment: datetime, tz_name: str) -> str:
    """Format a moment for one reader, e.g. "8:46 AM".

    An unknown zone falls back to UTC and says so, rather than silently
    printing a time that is wrong by hours.
    """
    zone = _zone(tz_name)
    if zone is None:
        return moment.strftime("%-I:%M %p UTC")
    return moment.astimezone(zone).strftime("%-I:%M %p")


def for_reader(moment: datetime, reader_tz: str, parent_tz: str, parent_city: str) -> str:
    """The line that goes in an alert.

    Both clocks when they differ, one when they do not, so a family member in
    the same city is not told the same time twice.
    """
    reader = in_zone(moment, reader_tz)
    theirs = in_zone(moment, parent_tz)
    if reader == theirs:
        return reader
    return f"{reader} your time, {theirs} in {parent_city}"
