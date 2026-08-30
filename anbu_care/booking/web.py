"""Driving a centre's own booking form, from somewhere a browser can live.

The browser does not run here. It runs in a second Cloud Run service, and this
is the client that talks to it.

**Why a separate service and not a job.** A job cannot return a value, so the
lane would have to write to Firestore and poll for its own answer - more moving
parts, more ways to be half-finished, and a polling loop in the path a family is
waiting on. A service answers. It also gets its own sizing (Chromium wants
several times what the API needs), its own concurrency of one because a browser
is stateful, its own timeout, and it scales to zero when nobody is booking.

**Why it is not in the API container.** Cloud Run gives the service one CPU and
a gigabyte, and Twilio abandons a webhook in about fifteen seconds. A browser in
that container competes for memory with the lane that answers her voice note,
and loses it for both.

**This client cannot break the family's path.** Every failure - the booker down,
a timeout, a bad response, no credentials - comes back as UNAVAILABLE, and the
lane moves to the next centre. There is no exception from here that reaches a
person waiting on WhatsApp.

**Authentication is service-to-service.** The booker is deployed with no public
invoker; this mints a Google-signed identity token for it. A browser that will
type a stranger's name into a form on request is not something to leave open.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from anbu_care.booking.channels import (
    UNAVAILABLE,
    AttemptResult,
    Preparation,
)

logger = logging.getLogger(__name__)

BOOKER_URL_ENV = "ANBU_BOOKER_URL"

# Generous, because a real browser navigating a real Indian lab site over a
# cold start is not fast. Still finite: a driver that hangs must become a
# refusal rather than a lane that never answers.
PREPARE_TIMEOUT_SECONDS = 90
COMMIT_TIMEOUT_SECONDS = 90
# Longer, because commit may park holding the browser while somebody reads a
# text message and types six digits. Comfortably over the driver's own wait, so
# the driver's timeout is the one that fires and its answer is the one reported.
COMMIT_WITH_OTP_TIMEOUT_SECONDS = 330


class WebChannel:
    """Ask the booker to fill a centre's form, in two halves."""

    name = "web"

    @staticmethod
    def configured() -> bool:
        return bool(os.getenv(BOOKER_URL_ENV))

    def can_serve(self, centre: dict) -> bool:
        """Only a centre Google gave us a website for.

        The URL comes from Places, never from a page and never from a model. It
        is the same rule as the destination on a payment: the counterparty does
        not get to say where this goes.
        """
        website = str((centre or {}).get("website") or "")
        return website.startswith(("http://", "https://"))

    def prepare(self, *, centre: dict, payload: dict) -> Preparation:
        body = self._call("/prepare", {
            "url": centre.get("website"),
            "payload": payload,
            # Passed so the driver has a cancellation path even where the page
            # offers none. It is Google's number for the centre, which is what a
            # person would ring anyway.
            "fallback_phone": centre.get("phone") or "",
            "idempotency_key": _key(centre, payload),
        }, PREPARE_TIMEOUT_SECONDS)

        if body is None:
            return Preparation(
                outcome=UNAVAILABLE,
                detail="the booking service could not be reached, so nothing "
                       "was sent to this centre")

        return Preparation(
            outcome=str(body.get("outcome") or UNAVAILABLE),
            detail=str(body.get("detail") or ""),
            cancel_url=str(body.get("cancel_url") or ""),
            cancel_phone=str(body.get("cancel_phone") or ""),
            page_text=str(body.get("page_text") or ""),
            handle=dict(body.get("handle") or {}),
            expects_otp=bool(body.get("expects_otp")),
            expects_otp_because=str(body.get("expects_otp_because") or ""),
        )

    def commit(self, *, centre: dict, payload: dict, prepared: Preparation,
               session_id: str = "", otp_wait_seconds: int = 0) -> AttemptResult:
        body = self._call("/commit", {
            "url": centre.get("website"),
            "payload": payload,
            "fallback_phone": centre.get("phone") or "",
            "handle": prepared.handle,
            "idempotency_key": _key(centre, payload),
            "session_id": session_id,
            "otp_wait_seconds": otp_wait_seconds,
        }, COMMIT_WITH_OTP_TIMEOUT_SECONDS if otp_wait_seconds
           else COMMIT_TIMEOUT_SECONDS)

        if body is None:
            # The dangerous case, and it is named rather than guessed at: the
            # request may or may not have gone. It is reported as unavailable
            # so the lane does not record an appointment nobody can prove, and
            # the duplicate guard keys on the ORDER, so a later retry at the
            # same centre cannot become a second slot.
            return AttemptResult(
                outcome=UNAVAILABLE,
                detail="the booking service stopped answering while this was "
                       "being sent, so whether it arrived is not known")

        return AttemptResult(
            outcome=str(body.get("outcome") or UNAVAILABLE),
            detail=str(body.get("detail") or ""),
            cancel_url=str(body.get("cancel_url") or prepared.cancel_url),
            cancel_phone=str(body.get("cancel_phone") or prepared.cancel_phone),
            slot_text=str(body.get("slot_text") or ""),
            provider_ref=str(body.get("provider_ref") or ""),
            evidence=str(body.get("evidence") or ""),
            evidence_sent=str(body.get("evidence_sent") or ""),
        )

    def _call(self, path: str, body: dict, timeout: int) -> dict | None:
        """One request. Returns None for every kind of failure, and never raises."""
        base = os.getenv(BOOKER_URL_ENV, "").rstrip("/")
        if not base:
            return None

        request = urllib.request.Request(
            base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **_auth_header(base)})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            logger.warning("booker %s returned HTTP %s", path, error.code)
            return None
        except Exception as exc:  # noqa: BLE001 - a failed call is an outcome
            logger.warning("booker %s failed: %s", path, type(exc).__name__)
            return None


def _auth_header(audience: str) -> dict:
    """A Google-signed identity token for the booker, when running on Cloud Run.

    Absent locally, where the booker is usually reached without auth. A missing
    token is not fatal here: the booker refuses, this reads the refusal as
    unavailable, and the lane moves on.
    """
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        token = google.oauth2.id_token.fetch_id_token(
            google.auth.transport.requests.Request(), audience)
        return {"Authorization": f"Bearer {token}"}
    except Exception:  # noqa: BLE001
        return {}


def _key(centre: dict, payload: dict) -> str:
    """Stable per centre and per person, so a retry is recognisable as one.

    The booker uses it to refuse submitting the same form twice inside a short
    window. It is defence in depth, not the authority: the lane's duplicate
    guard keys on the ORDER and is what actually stops two appointments.
    """
    return f"{centre.get('place_id', '')}:{payload.get('phone', '')}"


def deliver_otp(session_id: str, code: str) -> bool:
    """Hand a code to whichever browser session is waiting for it.

    Called from the inbound webhook, which is why it never raises: a code that
    cannot be delivered must come back as "not delivered" and be answered with a
    sentence, not become a 500 on the path her neighbour is messaging.
    """
    body = WebChannel()._call("/otp", {"session_id": session_id, "code": code},
                              PREPARE_TIMEOUT_SECONDS)
    return bool(body and body.get("status") == "delivered")
