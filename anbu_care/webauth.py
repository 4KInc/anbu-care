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

import hmac
import os

from fastapi import Header, HTTPException

# A stable demo credential. Deliberately not generated per-boot: the dashboard
# auto-signs-in with it during the demo, and a rotating value would make the
# published README instructions wrong.
DEMO_TOKEN = os.getenv("ANBU_DEMO_TOKEN", "anbu-demo-family-token")

UNAUTHENTICATED_DETAIL = (
    "This endpoint returns case or patient content and requires a family "
    "session. Verification is deliberately open and needs no credential: try "
    "/api/cases/{case_id}/verify instead."
)


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
