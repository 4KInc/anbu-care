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


# =========================================================================
# RAZORPAYX PAYOUTS
# =========================================================================
#
# The other direction, and the only one that removes the person.
#
# A Payment Link is a COLLECTION: it exists to ask somebody to pay, so a lane
# ending in one ends with a human opening a page. A payout PUSHES money from an
# account we hold to a beneficiary, which needs nobody present.
#
# It also sidesteps the limit that kills the obvious alternative. Debiting the
# family by UPI Autopay runs without per-transaction authentication only up to
# INR 15,000 under NPCI's rules — the raised INR 1,00,000 ceiling is restricted
# to specific merchant categories, and hospital billing is not one of them. A
# 31,650 interim bill would prompt for a UPI PIN every single time. A payout
# carries no such step, because it is not pulling from a consumer's account.
#
# Test mode is real here in the way that matters: real API calls, real payout
# objects, real webhooks, against a test balance that is topped up in the
# dashboard. KYC gates LIVE payouts, not these.
#
# Three calls, because RazorpayX addresses a beneficiary rather than an
# address: a contact, a fund account belonging to that contact, then the payout
# to that fund account. The first two are cached per destination so an ordinary
# payment is one call.

XAPI = "https://api.razorpay.com/v1"

# Contact and fund account per destination, for the life of the process. A
# restart re-creates them, which is wasteful rather than wrong.
_fund_accounts: dict[str, str] = {}


def x_configured() -> bool:
    return bool(os.getenv("RAZORPAYX_KEY_ID")
                and os.getenv("RAZORPAYX_KEY_SECRET")
                and os.getenv("RAZORPAYX_ACCOUNT_NUMBER"))


def x_is_test_key() -> bool:
    """Same refusal as the collection rail, and it matters more here.

    A live key on this rail does not create an instruction somebody may choose
    to complete. It moves money, immediately, with nobody present.
    """
    return (os.getenv("RAZORPAYX_KEY_ID") or "").startswith("rzp_test_")


def _x_auth_header() -> str:
    pair = f"{os.environ['RAZORPAYX_KEY_ID']}:{os.environ['RAZORPAYX_KEY_SECRET']}"
    return "Basic " + base64.b64encode(pair.encode()).decode()


def _x_post(path: str, payload: dict, idempotency_key: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json",
               "Authorization": _x_auth_header()}
    if idempotency_key:
        # Mandatory on payouts since March 2025, and the reason a retried
        # request cannot pay a hospital twice.
        headers["X-Payout-Idempotency"] = idempotency_key

    request = urllib.request.Request(
        f"{XAPI}{path}", data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
            return error.code, {"error": {"description": error.reason}}


def _narration(bill_id: str) -> str:
    """What shows on the bank statement. 30 characters, alphanumeric and space."""
    cleaned = "".join(c for c in f"Anbu Care {bill_id}" if c.isalnum() or c == " ")
    return cleaned[:30].strip() or "Anbu Care"


def _fund_account_for(payee_vpa: str, payee_label: str) -> tuple[str, str]:
    """The beneficiary id for this destination, creating it if needed.

    Returns (fund_account_id, detail). An empty id means it could not be made
    and the caller must not go on to attempt a payout.
    """
    if payee_vpa in _fund_accounts:
        return _fund_accounts[payee_vpa], "beneficiary already on file"

    status, contact = _x_post("/contacts", {
        "name": (payee_label or "Hospital")[:50],
        "type": "vendor",
    })
    if status >= 300 or not contact.get("id"):
        return "", f"the beneficiary could not be created: {_error_of(contact)}"

    status, account = _x_post("/fund_accounts", {
        "contact_id": contact["id"],
        "account_type": "vpa",
        "vpa": {"address": payee_vpa},
    })
    if status >= 300 or not account.get("id"):
        return "", f"the destination could not be registered: {_error_of(account)}"

    _fund_accounts[payee_vpa] = account["id"]
    return account["id"], "beneficiary registered"


def _error_of(body: dict) -> str:
    error = body.get("error") or {}
    return str(error.get("description") or error or "unknown provider error")[:160]


def create_payout(*, payment_id: str, amount_inr: int, payee_vpa: str,
                  payee_label: str, bill_id: str) -> ProviderResult:
    """Push the money. Returns an instruction that is already in flight.

    Unlike the collection rail this one genuinely needs the destination, so it
    is passed the VPA rather than a reference to it. That is not a loosening of
    the rule the enforcer holds: the address still comes from the mandate and
    never from the bill, and by the time this is called the payee check has
    already passed. A rail that actually moves money has to know where.

    The result is NOT a confirmation. A payout leaves here as `processing` or
    `queued` and becomes `processed` later, reported by webhook, which is the
    same shape the collection rail has and the same reason confirmation is a
    separate receipt.
    """
    if not x_configured():
        return ProviderResult(ok=False, provider="razorpayx",
                              detail="RazorpayX is not configured on this deployment")
    if not x_is_test_key():
        return ProviderResult(
            ok=False, provider="razorpayx",
            detail="refusing to use a non-test RazorpayX key; this build may "
                   "not move real money")
    if not payee_vpa:
        return ProviderResult(ok=False, provider="razorpayx",
                              detail="no destination on the mandate to pay to")

    fund_account_id, detail = _fund_account_for(payee_vpa, payee_label)
    if not fund_account_id:
        return ProviderResult(ok=False, provider="razorpayx", detail=detail)

    status, body = _x_post("/payouts", {
        "account_number": os.environ["RAZORPAYX_ACCOUNT_NUMBER"],
        "fund_account_id": fund_account_id,
        # Paise. Same hundredfold trap as the collection rail, same single
        # place for the conversion.
        "amount": amount_inr * 100,
        "currency": "INR",
        "mode": "UPI",
        "purpose": "vendor bill",
        # False deliberately. A payout that cannot be funded should come back
        # as a refusal the family is told about, not sit in a queue nobody is
        # watching while the record says it was sent.
        "queue_if_low_balance": False,
        "reference_id": payment_id[:40],
        "narration": _narration(bill_id),
        "notes": {"bill_id": bill_id, "payee": payee_label[:200]},
    }, idempotency_key=payment_id)

    if status >= 300 or not body.get("id"):
        return ProviderResult(ok=False, provider="razorpayx",
                              detail=f"the payout was not accepted: {_error_of(body)}")

    state = str(body.get("status") or "processing")
    if state in {"rejected", "cancelled", "failed", "reversed"}:
        return ProviderResult(ok=False, provider="razorpayx",
                              detail=f"the provider returned the payout as {state}")

    return ProviderResult(
        ok=True, provider="razorpayx", reference=body["id"],
        # No checkout_url, and there cannot be one. Nobody is being asked.
        checkout_url="",
        detail=f"payout {body['id']} to {payee_label} is {state}")
