"""A family of your own, for anybody who wants to try this from a phone.

The sibling project can hand a stranger a WhatsApp number and let them declare
an incident, because the data being coordinated belongs to a school and the
texter is a reporter. Nothing about them is stored and nothing about anyone is
revealed to them.

This product cannot do that. Its data subject is one woman, and its entire
claim is that who you are decides what you get: an unknown number resolves to
nobody, on purpose. Letting a stranger talk to her system would not demonstrate
the product, it would falsify it.

So the answer is not to open the door. It is to give the visitor their own
family, and their own roles in it. They text START, and get a synthetic parent
nobody is filming, with their number registered as her handset, as the son, and
as the care circle at once. That is not a shortcut around the consent model: it
is the same model, with them legitimately holding those roles on a record that
is theirs. Role captions are already on, so every message that arrives says
which of the three it was for, and the thing they came to see is the thing they
end up watching.

WHAT IT REFUSES. It will not provision without being asked, because an
unrequested sandbox is a message somebody did not consent to receive. It will
not touch the demo family, which is found by a different handset entirely. It
caps how many exist in a day, because this is a public number in a public
document. And it lets go: after a day it stops the check-ins and releases the
number, so a visitor who wandered off is not still being messaged next week.

NOTHING HERE IS FOR REAL DATA, and the welcome says so in its first lines. A
sandbox parent is synthetic, the policy is synthetic, and a visitor who ignores
that and sends real clinical detail about a real person has stored it in
somebody else's Firestore. Saying so plainly is the only control that exists.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from anbu_care import service
from anbu_care.provenance.store import get_store

log = logging.getLogger(__name__)

PK = "SANDBOX"
NAME = "Amma (your sandbox)"

# One word, matched exactly, so it cannot be tripped by a sentence that happens
# to contain it. The same discipline STOP already uses.
KEYWORD = "START"


def enabled() -> bool:
    return os.getenv("ANBU_SANDBOX", "off").strip().lower() in {"on", "true", "1"}


def daily_cap() -> int:
    try:
        return int(os.getenv("ANBU_SANDBOX_DAILY_CAP", "25"))
    except ValueError:
        return 25


def ttl_hours() -> int:
    try:
        return int(os.getenv("ANBU_SANDBOX_TTL_HOURS", "24"))
    except ValueError:
        return 24


@dataclass(frozen=True)
class Outcome:
    """What happened, and the message to send back."""

    status: str
    reply: str
    parent_id: str = ""

    @property
    def provisioned(self) -> bool:
        return self.status == "provisioned"


def asked_for_one(body: str) -> bool:
    """Exactly the keyword, not a sentence containing it."""
    return (body or "").strip().strip(".!").upper() == KEYWORD


def _rows() -> list[dict]:
    try:
        return get_store().query_prefix(PK, "")
    except Exception:  # a cap that cannot be read is not a reason to fail open
        log.exception("could not read sandbox rows")
        return []


def _row_for(number: str) -> dict | None:
    key = service.number_key(number)
    return next((r for r in _rows() if r.get("number_key") == key), None)


def provision(from_number: str, *, now: datetime | None = None) -> Outcome:
    """Give this number a family of its own. Never raises."""
    now = now or datetime.now(UTC)
    if not enabled():
        return Outcome("disabled", _CLOSED)

    existing = _row_for(from_number)
    if existing and service.load_profile(existing.get("parent_id", "")) is not None:
        # Asking twice is not an error, and minting a second family for one
        # number would leave the first one being messaged by nobody.
        return Outcome("already", _welcome(existing["parent_id"], again=True),
                       existing["parent_id"])

    today = now.date().isoformat()
    made_today = sum(1 for r in _rows() if str(r.get("created_at", ""))[:10] == today)
    if made_today >= daily_cap():
        return Outcome("capped", _CAPPED)

    try:
        parent_id = _build(from_number, now)
    except Exception:  # a visitor gets a sentence, not a stack trace
        log.exception("could not provision a sandbox for %s", from_number)
        return Outcome("failed", _FAILED)

    get_store().put(PK, parent_id, {
        "parent_id": parent_id,
        "number_key": service.number_key(from_number),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=ttl_hours())).isoformat(),
        "released": False,
    })
    return Outcome("provisioned", _welcome(parent_id), parent_id)


def _build(from_number: str, now: datetime) -> str:
    """The same onboarding tools the agent uses. No special path."""
    from anbu_care.booking import mandate as booking_mandate
    from anbu_care.payments import mandate as payment_mandate
    from anbu_care.tools import onboarding_tools

    created = onboarding_tools.create_parent_profile(
        name=NAME, age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )
    parent_id = created["profile"]["parent_id"]

    onboarding_tools.record_insurance_policy(
        parent_id, insurer="Star Health (synthetic)",
        policy_number="SH-SANDBOX-0001", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True,
    )

    # HER OWN HANDSET, so a check-in has somewhere to go and an answer to it is
    # attributed to her rather than to a family member.
    profile = service.load_profile(parent_id)
    profile.whatsapp_e164 = from_number
    profile.language = "en"
    service.save_profile(profile)
    onboarding_tools.record_recovery_checkin_consent(parent_id)

    # AND the two family roles, on the same number. One handset plays three
    # parts here exactly as it does in the recorded demo, which is why the
    # captions exist.
    # INBOUND_WELLBEING is what lets this number WRITE IN, as against merely be
    # written to. Without it a visitor is provisioned, welcomed, and then met
    # with silence on their next message: `resolve_sender` finds a contact with
    # no permission to file a report and refuses, which is correct and useless.
    onboarding_tools.record_family_contact(
        parent_id, name="You (as the son)", relationship="son",
        whatsapp_e164=from_number, timezone_name="Asia/Kolkata",
        is_primary=True,
        consent_purposes=["billing_updates", "status_updates", "outbound_notify",
                          "inbound_wellbeing"],
    )
    onboarding_tools.record_family_contact(
        parent_id, name="You (as the neighbour)", relationship="neighbour",
        whatsapp_e164=from_number, timezone_name="Asia/Kolkata",
        is_primary=False, role="care_circle",
        consent_purposes=["outbound_notify", "inbound_wellbeing"],
    )
    onboarding_tools.record_booking_disclosure_consent(parent_id)

    # Small caps. This is a public number and the money lane is simulated, but
    # a mandate a stranger can create should still be the smallest one that
    # demonstrates anything.
    # STANDING, not per-case: a visitor has no admission yet, and the whole
    # point of a standing grant is that it exists before the emergency does.
    payment_mandate.grant_standing(
        parent_id=parent_id, payee_vpa="sacredheart@okhdfcbank",
        payee_label="Sacred Heart Hospital", per_bill_cap_inr=50_000,
        total_cap_inr=100_000, hours=ttl_hours(), granted_by="the sandbox",
    )
    booking_mandate.grant_standing(parent_id=parent_id, granted_by="the sandbox")
    return parent_id


def release_expired(*, now: datetime | None = None) -> list[str]:
    """Stop messaging anybody who wandered off, and let their number go.

    Not a delete. The record stays, because a chain that can be made to vanish
    is not evidence of anything; what ends is the outbound. The number is
    released so it resolves to nobody again, which is the state it was in
    before they texted.
    """
    now = now or datetime.now(UTC)
    released: list[str] = []
    for row in _rows():
        if row.get("released"):
            continue
        try:
            due = datetime.fromisoformat(str(row.get("expires_at")))
        except (TypeError, ValueError):
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        if now < due:
            continue

        parent_id = row.get("parent_id", "")
        try:
            from anbu_care.recovery import window as recovery

            recovery.stop(parent_id, "sandbox released",
                          detail=("This was a sandbox family, and its day is up. "
                                  "The check-ins have stopped and the number that "
                                  "created it is no longer attached to any record."))
            key = row.get("number_key")
            if key:
                get_store().delete(f"WANUMBER#{key}", "OWNER")
        except Exception:  # one stuck sandbox must not block the rest
            log.exception("could not release sandbox %s", parent_id)
            continue

        get_store().put(PK, parent_id, {**row, "released": True,
                                        "released_at": now.isoformat()})
        released.append(parent_id)
    return released


# --- what a visitor is told -------------------------------------------------

_CLOSED = (
    "Thanks for trying. The sandbox is switched off at the moment, so there is "
    "nothing here for a number that is not on a record. You can still see it "
    "work, with no credential: "
    "https://anbu-care-37j4eofpwq-el.a.run.app/app"
)

_CAPPED = (
    "The sandbox has handed out as many families as it will today. That cap is "
    "deliberate: this is a public number in a public document. Try again "
    "tomorrow, or see it work now with no credential: "
    "https://anbu-care-37j4eofpwq-el.a.run.app/app"
)

_FAILED = (
    "Something went wrong setting that up, and nothing was half-made. Try "
    "START again, or see it work with no credential: "
    "https://anbu-care-37j4eofpwq-el.a.run.app/app"
)


def _welcome(parent_id: str, *, again: bool = False) -> str:
    head = ("You already have one, so here it is again."
            if again else "Done. You have a family of your own.")
    return (
        f"{head}\n\n"
        "IT IS ALL SYNTHETIC. The parent, the policy and the hospital are made "
        "up. Please do not send real personal or health information to this "
        "number.\n\n"
        "Your number now holds three roles on that record at once: hers, her "
        "son's, and a neighbour's. Every message back is captioned with which "
        "one it was for, because one handset is playing three parts.\n\n"
        "Try these, in this order:\n"
        "1. Tell it she has chest pain. It decides severity in code, tells the "
        "son, gives the neighbour a bedside link, files cashless cover and "
        "starts a one hour clock.\n"
        "2. Photograph a hospital bill. It reads it, and pays only the part "
        "the insurer is not settling.\n"
        "3. Photograph a discharge summary. It starts a fortnight of check-ins "
        "and files the claim by itself.\n"
        "4. Reply STOP to end the check-ins.\n\n"
        f"Your record: https://anbu-care-37j4eofpwq-el.a.run.app/app?case=\n"
        "It is yours for a day, then the check-ins stop and this number is "
        "released."
    )
