"""Turning the links Anbu Care sends into something a person can read.

Every URL this system puts in a WhatsApp message carries a signed credential,
because that is what makes a link openable without an account. The cost is that
they look like this:

    …/app?case=case-354f6a7a59&t=1787698021.OyY_sgxdH8Z-PYn2v3MbsCFJT8qHRvWr81…

which wraps to four lines on a phone, and reads to a frightened person at 4am
as exactly the sort of thing you are told never to tap. The credential has to
be there. It does not have to be on screen.

So a short alias stands in front of it. The alias is not a second security
model - it is a bearer token like the URL it hides, and everything about it is
sized for that:

  UNGUESSABLE   12 characters from a 32-symbol alphabet, which is 60 bits. Not
                a counter, not a hash of the target, not sequential - anything
                enumerable would turn one leaked link into all of them.
  SHORTER-LIVED an alias inherits the expiry of what it points at and may not
                outlive it. A short link that still worked after the token
                inside it died would be a credential nobody knew they still
                held.
  OURS ONLY     it only ever wraps this deployment's own base URL. A shortener
                that will redirect anywhere is an open redirect, and an open
                redirect on a domain people have been told to trust is worth
                more to an attacker than anything else here.
  NO CONTENT    the stored row is a URL and an expiry. Nothing about her.

The alphabet omits 0/O/1/l/I. These get read aloud down a phone line between
Thoothukudi and Nashville, and a character somebody has to ask about twice is a
character that should not be in it.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import time

from anbu_care.provenance.store import get_store

logger = logging.getLogger(__name__)

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LENGTH = 12

# Anything shorter than this is already readable and wrapping it would only add
# a redirect nobody needed.
WORTH_SHORTENING = 60

_PK = "SHORTLINK"

# Trailing punctuation belongs to the sentence, not the URL.
_URL = re.compile(r"https?://[^\s<>\"]+")


def _base() -> str:
    return os.getenv("ANBU_PUBLIC_BASE_URL", "").rstrip("/")


def _new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def shorten(url: str, expires_at: int | None = None) -> str:
    """An alias for one of our URLs, or the URL unchanged.

    Never raises. A shortener that could break a message is worse than a long
    link: the family would get nothing rather than something ugly.
    """
    base = _base()
    if not base or not url.startswith(base + "/"):
        return url
    if len(url) < WORTH_SHORTENING:
        return url

    try:
        code = _new_code()
        get_store().put(f"{_PK}#{code}", "URL#", {
            "code": code,
            "target": url,
            "expires_at": int(expires_at) if expires_at else _default_expiry(url),
            "created_at": int(time.time()),
        })
        return f"{base}/s/{code}"
    except Exception:  # noqa: BLE001 - a long link still works
        logger.warning("could not shorten a link; sending it long")
        return url


def _default_expiry(url: str) -> int:
    """When this alias dies, if the target did not say.

    Read off the target where it carries its own deadline, so the alias cannot
    outlive the credential inside it. A dashboard link token is `<epoch>.<sig>`,
    and that epoch is the answer.
    """
    match = re.search(r"[?&]t=(\d{9,12})\.", url)
    if match:
        return int(match.group(1))
    # A handoff token carries its own expiry too: case.scope.epoch.expires.sig
    match = re.search(r"/handoff/[^/?#]*?\.(\d{9,12})\.", url)
    if match:
        return int(match.group(1))
    return int(time.time()) + 30 * 24 * 3600


def resolve(code: str) -> str | None:
    """Where this alias points, or None if it is unknown or dead."""
    row = get_store().get(f"{_PK}#{(code or '').strip()}", "URL#")
    if not row:
        return None
    expires = int(row.get("expires_at") or 0)
    if expires and expires < int(time.time()):
        return None
    return str(row.get("target") or "") or None


def shorten_links_in(text: str) -> str:
    """Replace every one of our long URLs in a message with an alias.

    Applied at the point of sending rather than in each template, because a
    rule about what leaves the platform belongs in one place, and because every
    template that ever adds a link gets this without being changed.
    """
    if not text or not _base():
        return text

    def swap(match: re.Match) -> str:
        raw = match.group(0)
        trailing = ""
        while raw and raw[-1] in ".,;:!?)":
            trailing, raw = raw[-1] + trailing, raw[:-1]
        return shorten(raw) + trailing

    return _URL.sub(swap, text)
