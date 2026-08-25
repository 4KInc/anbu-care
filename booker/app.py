"""The booking service. A browser, behind an authenticated door.

Deliberately small and deliberately dumb: it drives a page and reports what it
saw. It holds no policy. Every guard that decides whether a booking may happen
lives in the main service, ruling on what `/prepare` returns, before `/commit`
is ever called - because a guard that lives next to the thing it guards is a
guard somebody will route around.

It is deployed with no public invoker. A browser that will type a stranger's
name into a form on request is not something to leave open to the internet.
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import Body, FastAPI

import driver

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("booker")

app = FastAPI(title="Anbu Care booker")

# Same key twice inside this window is treated as a retry rather than a second
# booking. Defence in depth: the main service's duplicate guard keys on the
# ORDER and is what actually stops two appointments.
_RECENT: dict[str, float] = {}
REPLAY_WINDOW_SECONDS = 600


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "dry_run": driver.dry_run(),
            "reader": os.getenv("ANBU_BOOKING_READER", "gemini")}


# NOT async. Playwright's sync API refuses to run inside an asyncio loop, and
# an async endpoint here failed every attempt with "you are using Playwright
# Sync API inside the asyncio loop". A plain def is run by FastAPI in a
# threadpool, which is where a blocking browser belongs anyway.
@app.post("/state")
def state(body: dict = Body(default_factory=dict)) -> dict:
    """The same answer as /healthz, over the door the caller already uses.

    The API reaches /prepare and /commit with a Google-signed identity token
    and they work; a GET to /healthz from the same service answered 404, which
    is Cloud Run's way of refusing an unauthorised caller without admitting the
    service exists. Rather than keep two auth paths and debug the one nobody
    depends on, the pre-flight asks the way the booking lane asks.
    """
    return healthz()


@app.post("/prepare")
def prepare(body: dict = Body(default_factory=dict)) -> dict:
    url = str(body.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return {"outcome": "unavailable", "detail": "no usable website for this centre"}
    try:
        return driver.prepare(url=url, payload=dict(body.get("payload") or {}),
                              fallback_phone=str(body.get("fallback_phone") or ""))
    except Exception as exc:  # noqa: BLE001 - a fault here is an outcome there
        logger.exception("prepare failed")
        return {"outcome": "unavailable",
                "detail": f"the booking driver failed ({type(exc).__name__})"}


@app.post("/commit")
def commit(body: dict = Body(default_factory=dict)) -> dict:
    url = str(body.get("url") or "")
    key = str(body.get("idempotency_key") or "")

    now = time.time()
    for stale in [k for k, t in _RECENT.items() if now - t > REPLAY_WINDOW_SECONDS]:
        _RECENT.pop(stale, None)
    if key and key in _RECENT:
        return {"outcome": "unavailable",
                "detail": "this exact booking was submitted moments ago and was "
                          "not sent again"}

    try:
        result = driver.commit(url=url, payload=dict(body.get("payload") or {}),
                               handle=dict(body.get("handle") or {}),
                               fallback_phone=str(body.get("fallback_phone") or ""),
                               session_id=str(body.get("session_id") or ""),
                               otp_wait_seconds=int(body.get("otp_wait_seconds") or 0))
    except Exception as exc:  # noqa: BLE001
        logger.exception("commit failed")
        return {"outcome": "unavailable",
                "detail": f"the booking driver failed ({type(exc).__name__})"}

    # Only a submission that actually landed counts as one worth de-duplicating.
    if key and result.get("outcome") in {"requested", "confirmed"}:
        _RECENT[key] = now
    return result


@app.post("/otp")
def otp(body: dict = Body(default_factory=dict)) -> dict:
    """Hand a one-time code to the browser session that is waiting for it.

    Served while /commit is parked on that session, which is why this container
    runs with a concurrency above one and a single instance: the waiting session
    lives in this process, and a code delivered to a different instance is a
    code delivered to nobody.

    The code is not logged, not stored and not returned. It exists in memory for
    as long as it takes to type it into a form.
    """
    session_id = str(body.get("session_id") or "")
    code = str(body.get("code") or "").strip()
    if not session_id or not code.isdigit():
        return {"status": "ignored", "reason": "no session or no code"}

    delivered = driver.offer_code(session_id, code)
    return {"status": "delivered" if delivered else "no_session_waiting"}
