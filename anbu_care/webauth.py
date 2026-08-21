"""Server-side access control for the dashboard.

Two access models, deliberately different, both enforced here rather than in the
browser:

**Open, with no credential at all** — `/api/cases/{id}/verify`, the hospital
knowledge base, and liveness. Verification proves the record was not altered
*without revealing what the record says*: it returns hashes, a boolean and a
failure mode. That is why it can be public, and it is the point of the whole
provenance design — a family or an insurer must be able to check us without
asking our permission.

**Credentialed** — anything that returns case or patient content: the parsed
health record, the case trail, the arrival brief. This is the other half of the
DPDP argument the WhatsApp gate makes. That gate blocks clinical detail from
leaving over WhatsApp *because* it lives somewhere protected; if "somewhere
protected" were a URL anyone could read, the argument would be hollow and we
would have published the exact data we claim to guard.

The demo credential is not a secret and is printed in the README. Secrecy is not
what is being demonstrated — server-side enforcement is. A judge should be able
to take the token out of the page, and still see that removing it produces a 401.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from fastapi import Header, HTTPException, Request

# A stable demo credential. Deliberately not generated per-boot: the dashboard
# auto-signs-in with it during the demo, and a rotating value would make the
# published README instructions wrong.
DEMO_TOKEN = os.getenv("ANBU_DEMO_TOKEN", "anbu-demo-family-token")

UNAUTHENTICATED_DETAIL = (
    "This endpoint returns case or patient content and requires a family "
    "session. Verification is deliberately open and needs no credential: try "
    "/api/cases/{case_id}/verify instead."
)


# ---- links a family member can actually tap ------------------------------
#
# A son woken at 2am taps the link his mother's emergency generated. Asking him
# to find and paste a shared token first is friction at the worst possible
# moment, and a token shared by everyone is a worse credential than one issued
# per alert anyway.
#
# So an alert carries a signed link. It is NOT a general credential: it names
# the parent and the case it was issued for, it expires, and it is signed with
# a secret the caller never sees. It grants exactly what the message promised —
# this episode and this parent's record — and nothing else.
LINK_TTL_SECONDS = 24 * 60 * 60      # long enough to cross a timezone and sleep
LINK_SECRET_ENV = "ANBU_LINK_SECRET"


def _link_secret() -> bytes | None:
    """No secret, no signed links. Fails closed rather than to a default.

    A hardcoded fallback would mean every deployment shared a signing key, so
    anyone who read the source could mint a link into any record.
    """
    value = os.getenv(LINK_SECRET_ENV)
    return value.encode("utf-8") if value else None


def make_link_token(parent_id: str, case_id: str, now: int | None = None) -> str | None:
    """Mint a link credential for one parent and one case. None if unconfigured."""
    secret = _link_secret()
    if not secret:
        return None
    expires = int(now or time.time()) + LINK_TTL_SECONDS
    payload = f"{parent_id}:{case_id}:{expires}"
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"{expires}.{signature}"


def link_token_grants(token: str, *, parent_id: str = "", case_id: str = "",
                      now: int | None = None) -> bool:
    """Does this token authorise this exact parent and case, right now?

    Every failure — malformed, expired, or signed for something else — returns
    False. A caller learns only that it was refused.
    """
    secret = _link_secret()
    if not secret or not token or "." not in token:
        return False

    expires_raw, _, presented = token.partition(".")
    try:
        expires = int(expires_raw)
    except ValueError:
        return False
    if expires < int(now or time.time()):
        return False

    payload = f"{parent_id}:{case_id}:{expires}"
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return hmac.compare_digest(expected, presented)


def require_family_session(authorization: str | None = Header(default=None)) -> str:
    """Reject anything without a valid family bearer token.

    Constant-time compare — the token is not a secret here, but a credential
    check that leaks timing is a bad pattern to ship even in a demo.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail=UNAUTHENTICATED_DETAIL)

    presented = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(presented, DEMO_TOKEN):
        raise HTTPException(status_code=401, detail=UNAUTHENTICATED_DETAIL)
    return presented


def require_case_access(request: Request,
                        authorization: str | None = Header(default=None)) -> str:
    """A family session, OR a signed link issued for exactly this case.

    The link names one parent and one case. Both are checked against what the
    request is actually asking for, so a link minted for one case cannot read
    another, and a link for one parent cannot read a different parent's record.

    The case id travels in the query string because the link carries it there
    already; a parent-scoped path has no case in its path to check against.
    Nothing is trusted from that parameter on its own — it only selects which
    signature to verify, and a wrong value simply fails to match.
    """
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization.split(" ", 1)[1].strip()
        if hmac.compare_digest(presented, DEMO_TOKEN):
            return presented

    token = request.query_params.get("t", "")
    if token:
        case_id = request.path_params.get("case_id") or request.query_params.get("case", "")
        parent_id = request.path_params.get("parent_id", "")
        if not parent_id and case_id:
            from anbu_care import service

            case = service.load_case(case_id)
            parent_id = case.parent_id if case else ""
        if parent_id and case_id and link_token_grants(
            token, parent_id=parent_id, case_id=case_id
        ):
            return "link"

    raise HTTPException(status_code=401, detail=UNAUTHENTICATED_DETAIL)
