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

from fastapi import FastAPI, Request

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


@app.post("/prepare")
async def prepare(request: Request) -> dict:
    body = await request.json()
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
async def commit(request: Request) -> dict:
    body = await request.json()
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
                               fallback_phone=str(body.get("fallback_phone") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.exception("commit failed")
        return {"outcome": "unavailable",
                "detail": f"the booking driver failed ({type(exc).__name__})"}

    # Only a submission that actually landed counts as one worth de-duplicating.
    if key and result.get("outcome") in {"requested", "confirmed"}:
        _RECENT[key] = now
    return result
