"""Is the WhatsApp sender actually ready to carry a demo?

Four things have to be true at once, and three of them fail silently. A sender
can be ONLINE with no webhook, in which case every outbound message works and
every inbound photograph vanishes. The 24-hour window can be closed, in which
case sends fail at Twilio rather than in our code. And the deployed service can
still be pointed at a different sender than the one you just configured.

    uv run python scripts/verify_whatsapp_sender.py
"""

from __future__ import annotations

import json
import os
import urllib.request

BASE = os.getenv("ANBU_PUBLIC_BASE_URL", "https://anbu-care-37j4eofpwq-el.a.run.app")
EXPECTED_WEBHOOK = f"{BASE}/api/wellbeing/inbound"


def _twilio(path: str) -> dict:
    sid = os.environ["TWILIO_API_KEY_SID"]
    secret = os.environ["TWILIO_API_KEY_SECRET"]
    request = urllib.request.Request(f"https://messaging.twilio.com/v2{path}")
    import base64

    token = base64.b64encode(f"{sid}:{secret}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> int:
    configured = os.getenv("TWILIO_WHATSAPP_FROM", "")
    print(f"configured sender : {configured or '(unset)'}\n")

    senders = _twilio("/Channels/Senders?Channel=whatsapp&PageSize=20")["senders"]
    match = next((s for s in senders if s.get("sender_id") == configured), None)

    problems: list[str] = []
    if match is None:
        problems.append(f"{configured} is not a sender on this account")
        for s in senders:
            print(f"  available: {s.get('sender_id')}  {s.get('status')}")
    else:
        profile = match.get("profile") or {}
        webhook = match.get("webhook") or {}
        status = match.get("status", "")
        name = profile.get("name") or "(unset)"
        hook = webhook.get("callback_url") or "(UNSET)"

        print(f"  status        : {status}")
        print(f"  display name  : {name}")
        print(f"  inbound hook  : {hook}")

        if not status.startswith("ONLINE"):
            problems.append(f"sender status is {status}, not ONLINE")
        if name == "(unset)":
            problems.append("no display name — the chat header will not say Anbu Care")
        if hook != EXPECTED_WEBHOOK:
            problems.append(
                f"inbound webhook is {hook}, expected {EXPECTED_WEBHOOK}. "
                f"Outbound will work and every inbound message will vanish.")

    # The deployed service has its own copy of the sender, and a stale deploy
    # is invisible from the Twilio side.
    try:
        with urllib.request.urlopen(f"{BASE}/api/healthz", timeout=20) as response:
            health = json.load(response)
        print(f"\n  deployed mode : {health.get('whatsapp_mode')}")
        if health.get("whatsapp_mode") != "twilio":
            problems.append(f"deployed whatsapp_mode is {health.get('whatsapp_mode')}")
    except Exception as exc:  # noqa: BLE001 - report, do not raise
        problems.append(f"could not reach {BASE}: {type(exc).__name__}")

    print()
    if problems:
        for problem in problems:
            print(f"  FAIL  {problem}")
        print("\nNot ready.")
        return 1

    print("  Sender is online, named, and pointed at this deployment.")
    print("\n  One thing this cannot check: WhatsApp's 24-hour window. It is per")
    print("  (business number, handset) pair and this is a NEW number, so no window")
    print("  exists yet. Message the sender once from the handset before recording,")
    print("  or the first outbound send fails at Twilio rather than in our code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
