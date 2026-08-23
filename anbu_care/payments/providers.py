"""Talking to the payment provider. One function, one seam.

The enforcer decides whether to pay and where to. This module is only the last
mile: handing an already-authorised amount to a rail and reporting honestly
what came back.

Razorpay in TEST MODE. The API call is real, the order is real, the identifier
is real, and the webhook that confirms it is real. What is not real is the
money: test-mode funds do not exist and the keys cannot be switched to live.
That is a narrower claim than "we take payments" and a much wider one than
"settlement is simulated", and it should be stated as exactly what it is.

What this still is NOT: autonomous debit. Creating an order is an instruction,
not a transfer. Pulling funds from somebody's account without them present
needs UPI Autopay or an e-mandate under NPCI, which needs a registered merchant
and mandate approval. No amount of code here reaches that.

Nothing in this module holds a banking credential. It holds an API key for our
own account, which is a different thing: it authorises us to ask a provider to
collect, never to read or move anybody's funds directly.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

API = "https://api.razorpay.com/v1"
TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    reference: str = ""
    checkout_url: str = ""
    detail: str = ""
    provider: str = "razorpay"


def configured() -> bool:
    return bool(os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET"))


def is_test_key() -> bool:
    """A live key must never reach this build.

    Test keys are prefixed rzp_test_. Refusing anything else is cheap, and the
    failure it prevents is the only one in this project that cannot be undone.
    """
    return (os.getenv("RAZORPAY_KEY_ID") or "").startswith("rzp_test_")


def _auth_header() -> str:
    pair = f"{os.environ['RAZORPAY_KEY_ID']}:{os.environ['RAZORPAY_KEY_SECRET']}"
    return "Basic " + base64.b64encode(pair.encode()).decode()


def _post(path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{API}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": _auth_header()},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
            return error.code, {"error": {"description": error.reason}}


def create_order(*, payment_id: str, amount_inr: int, payee_label: str,
                 bill_id: str) -> ProviderResult:
    """Ask the provider to collect this amount. Returns an INSTRUCTION, not a receipt.

    A PAYMENT LINK rather than a bare order. An Order is a server-side object
    with no way to pay it: I built this on orders first and generated a
    checkout URL pointing at checkout.razorpay.com/v1/checkout.js, which is a
    JavaScript file and not a page. The order sat at `created` for ever and the
    only thing that could confirm it was a webhook posted by hand, which is the
    manual step the integration existed to remove.

    A payment link comes back with a hosted page that accepts UPI, so the
    instruction the agent created is one somebody can actually complete, and
    the confirmation arrives on its own.

    The payee label rides along as a note so the instruction is legible in the
    provider's own dashboard. The destination itself is NOT sent: this is a
    collection, and where money finally goes is the mandate's business rather
    than the rail's.
    """
    if not configured():
        return ProviderResult(ok=False, detail="no payment provider is configured")
    if not is_test_key():
        return ProviderResult(
            ok=False,
            detail="refusing to use a non-test Razorpay key; this build may "
                   "not move real money")

    status, body = _post("/payment_links", {
        # Paise. A rupee amount sent as paise is a hundredfold error, which is
        # why the conversion lives here and nowhere else.
        "amount": amount_inr * 100,
        "currency": "INR",
        "description": f"Interim bill {bill_id}, {payee_label}"[:100],
        "reference_id": payment_id[:40],
        # No chasing. The family already gets one message about this from us,
        # and a provider sending its own reminders would be a second voice
        # nobody agreed to hear from.
        "reminder_enable": False,
        "notes": {"bill_id": bill_id[:60], "payee": payee_label[:60],
                  "source": "anbu-care"},
    })

    if status != 200 or "id" not in body:
        described = (body.get("error") or {}).get("description") or f"HTTP {status}"
        return ProviderResult(ok=False, detail=f"the provider refused: {described}")

    return ProviderResult(
        ok=True, reference=body["id"],
        checkout_url=body.get("short_url", ""),
        detail=f"payment link {body['id']} created",
    )


def verify_webhook(*, body: bytes, signature: str) -> bool:
    """Is this callback really from the provider.

    An unverified webhook is an open endpoint that anybody can use to mark a
    payment as settled, which is the same class of mistake as trusting a JWT
    the browser decoded for itself.
    """
    import hashlib
    import hmac

    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
