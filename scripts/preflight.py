"""Everything that has silently ruined a take, checked before you roll.

Run it against the deployed service:

    make preflight
    python scripts/preflight.py --fix        # also clears what is safe to clear

**Why this exists.** This system has one failure mode it produces over and over:
correct code turned into a no-op by DATA. A handset still bound as a clinician
from a rehearsal files the opening voice note as a doctor's note. A stray test
parent hijacks the WhatsApp number index and the voice note lands on the wrong
record. A neighbour holding the wrong consent has her bill photo dropped with a
204. None of these throw. No error, no red log, no failed request - the system
does exactly nothing, and the only symptom is an absence, on a continuous take,
that nobody can see happening.

**Why it is fast.** Almost all of it is one authenticated request. The state
lives beside the API, where reading it costs a millisecond; asking from a laptop
would be a dozen round trips and a pre-flight nobody runs because it takes a
minute is worse than no pre-flight at all. The public probes go in parallel
beside it.

**Why it does not fix anything by default.** A check that repairs what it is
checking cannot be trusted to report on it. `--fix` clears the two things that
are always safe to clear before a take - a bound handset and a stale code
request - and says so.

What it does NOT do: it checks STATE, not behaviour. It will not tell you that
Gemini is reachable, that Twilio will deliver, or that the browser works. A
warm-up voice note is still the real smoke test; this only promises that when
the system tries, the data will not make it a no-op.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_URL = "https://anbu-care-37j4eofpwq-el.a.run.app"
TIMEOUT = 20

GREEN, RED, AMBER, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _get(url: str, token: str = "") -> tuple[int, dict | None]:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, None
    except urllib.error.HTTPError as error:
        return error.code, None
    except Exception:  # noqa: BLE001
        return 0, None


def _status(url: str) -> int:
    return _get(url)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("ANBU_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.getenv("ANBU_DEMO_TOKEN",
                                                     "anbu-demo-family-token"))
    parser.add_argument("--fix", action="store_true",
                        help="unbind a bound handset and close a stale code request")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    started = time.time()
    with ThreadPoolExecutor(max_workers=6) as pool:
        state = pool.submit(_get, f"{base}/api/preflight", args.token)
        probes = {
            "content is refused without a credential":
                pool.submit(_status, f"{base}/api/parents/nobody"),
            "verify is open to everyone":
                pool.submit(_status, f"{base}/api/cases/nothing/verify"),
            "an expired short link says so":
                pool.submit(_status, f"{base}/s/nosuchcodeee"),
        }
        code, body = state.result()
        wanted = {"content is refused without a credential": 401,
                  "verify is open to everyone": 200,
                  "an expired short link says so": 410}
        probed = {name: future.result() for name, future in probes.items()}

    lines: list[tuple[bool, bool, str, str]] = []   # ok, fatal, name, detail

    for name, got in probed.items():
        lines.append((got == wanted[name], True, name,
                      f"HTTP {got}, expected {wanted[name]}"))

    if code != 200 or not body:
        lines.append((False, True, "preflight endpoint",
                      (f"HTTP {code} - is the token right, and is this revision "
                       "deployed?")))
    else:
        for check in body.get("checks", []):
            lines.append((check["ok"], check.get("fatal", True),
                          check["name"], check["detail"]))

    width = max(len(name) for _ok, _f, name, _d in lines)
    fatal_failures = 0
    print()
    for ok, fatal, name, detail in lines:
        if ok:
            mark, colour = "ok  ", GREEN
        elif fatal:
            mark, colour, fatal_failures = "FAIL", RED, fatal_failures + 1
        else:
            mark, colour = "warn", AMBER
        print(f"  {colour}{mark}{OFF}  {name:<{width}}  {DIM}{detail}{OFF}")

    if args.fix:
        print(f"\n  {DIM}--fix asked; clearing what is safe to clear{OFF}")
        _fix(base, args.token)

    elapsed = time.time() - started
    print()
    if fatal_failures:
        print(f"  {RED}{fatal_failures} thing(s) would ruin the take{OFF}  "
              f"{DIM}({elapsed:.1f}s){OFF}\n")
        return 1
    print(f"  {GREEN}ready{OFF}  {DIM}({elapsed:.1f}s) - "
          f"now send a warm-up voice note, which is the real smoke test{OFF}\n")
    return 0


def _fix(base: str, token: str) -> None:
    """Only the things that are always safe to clear before a take.

    Deliberately not "make every check pass". Granting a mandate or fabricating
    a consent to turn a line green would be the pre-flight lying on behalf of
    the thing it exists to catch.

    Closing a leftover recovery window belongs here because it is not a tidy-up
    that hides anything: the check-ins really do end, `recovery.stopped` says so
    on the chain with the reason in it, and the window row stays exactly where
    it was. Nothing is deleted and nothing is claimed that did not happen.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from anbu_care import service
    from anbu_care.booking import otp
    from anbu_care.handoff import channel
    from anbu_care.recovery import window as recovery

    handset = os.getenv("ANBU_DEMO_CIRCLE_E164", "")
    if not handset:
        print(f"  {DIM}ANBU_DEMO_CIRCLE_E164 is not set; nothing to clear{OFF}")
        return

    key = service.number_key(handset)
    while (bound := channel.for_number(key)) is not None:
        channel.unbind(bound)
        print(f"  unbound the handset from {bound.case_id}")

    owner = service.lookup_whatsapp_number(handset) or {}
    parent_id = owner.get("parent_id", "")
    if parent_id:
        closed = otp.sweep(parent_id)
        pending = otp.live_for(parent_id)
        if pending is not None:
            otp.close(pending, outcome="cleared before a recording")
            closed += 1
        if closed:
            print(f"  closed {closed} outstanding code request(s)")

        ended = recovery.stop(
            parent_id, "cleared before a recording",
            detail=("A window left open by an earlier take would have answered "
                    "the tick instead of the window the discharge summary "
                    "opens. No check-in is owed on it now."))
        for w in ended:
            print(f"  closed recovery window {w.window_id} from {w.case_id}")


if __name__ == "__main__":
    raise SystemExit(main())
