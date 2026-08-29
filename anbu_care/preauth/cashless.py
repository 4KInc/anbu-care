"""Cashless pre-authorization at admission, and the clock the insurer owes.

The other half of what a son does. The first insurance act on the morning a
parent is admitted is not a claim - a claim comes weeks later, after discharge,
with bills attached. It is getting CASHLESS PRE-AUTHORIZATION filed so the
family does not pay the hospital out of pocket and wait to be repaid. Then it
is watching the clock, because under the IRDAI 2024 Master Circular the insurer
owes an authorization decision within one hour of a complete request, and a
family with no one watching simply waits.

WHAT THIS IS NOT, and the lines are load-bearing:

  NOT the hospital insurance desk. In reality a cashless pre-auth is filed BY
  the hospital INTO the insurer or TPA. A patient-side app is not a participant
  in that exchange. So this request goes to the SAME simulated adjudicator the
  claim lane already uses, labelled simulated on every surface it touches.
  Nothing here has filed anything into a real insurer.

  NOT an authorization. `pre_auth.requested` is a request. `pre_auth.authorized`
  is written only when the simulated adjudicator returns an authorising verdict,
  never assumed and never optimistic.

  NOT a settlement. Cashless means the INSURER pays the HOSPITAL. Anbu Care does
  not move that money and never says the hospital was paid. This is the same
  distinction the payment lane draws between initiated and confirmed, and it is
  kept the same way: the coverage estimate's `settled_inr` reads only
  `claim.adjudicated` receipts, and nothing in this module writes one.

  NOT clinical. A pre-auth here asks whether COVER EXISTS. It carries the policy,
  the sum insured, the admission date and the hospital, and nothing else. No
  diagnosis, no severity, no justification of treatment. Nothing in this module
  reads a severity or calls triage.

  NOT enforcement. When the hour lapses without a decision, this records the
  breach and states what the policyholder is entitled to. It does not file a
  grievance, does not compel anybody, and does not claim it will win anything.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from anbu_care import service
from anbu_care.schemas import (
    AdjudicationOutcome,
    ClaimPacket,
    DocumentKind,
    PreAuthRequest,
)
from anbu_care.tpa import adjudicate
from anbu_care.tpa.adjudicator import SIMULATED_ADJUDICATOR

logger = logging.getLogger(__name__)

# The pre-auth vocabulary. It exists only here and on the receipts; the
# adjudicator's own enum is untouched and gains no member.
REQUESTED = "requested"
AUTHORIZED = "authorized"
AUTHORIZED_WITH_LIMITS = "authorized_with_limits"
QUERIED = "queried"
DENIED = "denied"

_FROM_VERDICT = {
    AdjudicationOutcome.PASS: AUTHORIZED,
    AdjudicationOutcome.PARTIAL: AUTHORIZED_WITH_LIMITS,
    AdjudicationOutcome.QUERY: QUERIED,
    AdjudicationOutcome.DENY: DENIED,
}

_RECEIPT_KIND = {
    AUTHORIZED: "pre_auth.authorized",
    AUTHORIZED_WITH_LIMITS: "pre_auth.authorized",
    QUERIED: "pre_auth.queried",
    DENIED: "pre_auth.denied",
}

# IRDAI/HLT/CIR/PRO/84/5/2024, 29 May 2024, Master Circular on Health Insurance
# Business. Stated as information, in the policyholder's own words. Anbu Care
# does not file, does not enforce, and does not claim any of this will be won.
IRDAI_RIGHT = (
    "Under the IRDAI Master Circular on Health Insurance Business "
    "(IRDAI/HLT/CIR/PRO/84/5/2024, 29 May 2024) an insurer owes a cashless "
    "authorisation decision within one hour of a complete request, and final "
    "discharge authorisation within three hours. Where a delay causes extra "
    "cost, such as an additional room day, the circular places that cost on the "
    "insurer, and delayed settlement carries interest at two percent above the "
    "bank rate. A policyholder who wants to pursue this raises a grievance with "
    "the insurer first and may then approach the Insurance Ombudsman. "
    "Anbu Care has not filed anything, cannot compel anyone, and is not "
    "claiming this will be won. This is the right, stated."
)

PROVISIONAL_NOTE = (
    "PROVISIONAL. An admission-time ceiling under the policy, not a decision "
    "about any bill and not the covered amount. Nothing is settled: cashless "
    "means the insurer pays the hospital, which Anbu Care does not do and does "
    "not report."
)


def _hospital(case_id: str) -> str:
    """Where she was taken, off the triage receipt, and nothing else from it.

    The hospital is a logistics fact and it belongs on a pre-auth form. The
    severity that sits beside it on the same receipt is not read here, is not
    carried, and never reaches the adjudicator: a pre-auth asks whether cover
    exists, never whether treatment is warranted.
    """
    triage = next(
        (r for r in reversed(service.get_chain(case_id).receipts)
         if r.kind == "triage.decision"), None)
    if triage is None:
        return ""
    ranked = triage.payload.get("ranked") or []
    if not ranked:
        return ""
    return str(ranked[0].get("name") or "")


def open_preauth(case_id: str) -> PreAuthRequest | None:
    """The pre-auth already open on this case, if there is one."""
    return next((p for p in service.list_preauths(case_id)
                 if p.outcome == REQUESTED), None)


def request_cashless_preauth(case_id: str) -> dict:
    """Ask the simulated adjudicator whether cover exists for this admission.

    Idempotent per case. One admission is one pre-auth: a second request does
    not open a second clock, because two clocks on one admission would mean two
    deadlines and two breaches for one thing the insurer owes once.
    """
    case = service.load_case(case_id)
    if case is None:
        return {"status": "error", "error": f"no case {case_id}"}
    profile = service.load_profile(case.parent_id)
    if profile is None:
        return {"status": "error", "error": "no parent record for this case"}

    # ONE PRE-AUTH PER ADMISSION, decided or not.
    #
    # This first blocked only while a request was still awaiting an answer,
    # which is not the same rule and let a re-request after a decision open a
    # second clock on the same admission. A live check caught it: the second
    # call returned a fresh authorisation where it should have returned the
    # first one. An insurer owes this decision once.
    existing = next(iter(service.list_preauths(case_id)), None)
    if existing is not None:
        awaiting = existing.outcome == REQUESTED
        return {"status": "already_requested",
                "preauth": existing.model_dump(mode="json"),
                "note": (("A cashless pre-authorisation is already open on this "
                          "admission and its clock is already running.")
                         if awaiting else
                         ("This admission already has a cashless "
                          f"pre-authorisation and it came back {existing.outcome}. "
                          "Asking again does not ask the counterparty again."))}

    policy = profile.policy
    now = datetime.now(UTC)
    req = PreAuthRequest(
        preauth_id=service.new_id("preauth"),
        case_id=case_id,
        parent_id=case.parent_id,
        insurer=policy.insurer if policy else "",
        policy_number=policy.policy_number if policy else "",
        sum_insured_inr=policy.sum_insured_inr if policy else 0,
        cashless_eligible=bool(policy.cashless_eligible) if policy else False,
        admitted_on=case.opened_at.date().isoformat(),
        hospital_name=_hospital(case_id),
        requested_at=now,
        # The instant the insurer's hour is up, stored as data. A breach states
        # this, not whenever a tick happened to notice it.
        decision_due_at=service.sla_deadline("cashless_preauth", start=now),
        adjudicator=SIMULATED_ADJUDICATOR,
    )
    service.save_preauth(req)

    service.append_receipt(
        case_id,
        kind="pre_auth.requested",
        actor="insurer_liaison_agent",
        payload={
            "preauth_id": req.preauth_id,
            "insurer": req.insurer,
            "policy_number": req.policy_number,
            "sum_insured_inr": req.sum_insured_inr,
            "cashless_eligible": req.cashless_eligible,
            "admitted_on": req.admitted_on,
            "hospital_name": req.hospital_name,
            "requested_at": now.isoformat(),
            "decision_due_at": req.decision_due_at.isoformat() if req.decision_due_at else None,
            "sla": "IRDAI 2024 Master Circular: cashless authorisation decision within 1 hour",
            "requested_at_source": req.requested_at_source,
            "simulated": True,
            "adjudicator": SIMULATED_ADJUDICATOR,
            "note": ("A request, not an authorisation. It went to the simulated "
                     "adjudicator, not to a real insurer, and nothing is "
                     "authorised until that adjudicator answers."),
        },
    )

    return _decide(req)


def _decide(req: PreAuthRequest) -> dict:
    """Run the same adjudicator, in pre-auth mode, and record what it said."""
    profile = service.load_profile(req.parent_id)

    # An ADAPTER, built here and never stored. It exists only to hand the
    # adjudicator the shape it already reads. It is not saved, never gets a
    # PACKET# key, and is not put on the case - so nothing downstream can pick
    # it up and treat this admission as a claim that somebody half-assembled.
    adapter = ClaimPacket(
        packet_id=f"{req.preauth_id}:adapter",
        case_id=req.case_id,
        parent_id=req.parent_id,
        policy_number=req.policy_number or None,
        admitted_on=req.admitted_on or None,
    )
    verdict = adjudicate(
        adapter,
        profile.policy if profile else None,
        set[DocumentKind](),
        preauth=True,
    )

    req.outcome = _FROM_VERDICT[verdict.outcome]
    req.decided_at = datetime.now(UTC)
    req.reasons = list(verdict.reasons)
    req.missing = list(verdict.missing_documents)
    req.provisional_ceiling_inr = (
        req.sum_insured_inr
        if req.outcome in {AUTHORIZED, AUTHORIZED_WITH_LIMITS} else None)
    service.save_preauth(req)

    receipt = service.append_receipt(
        req.case_id,
        kind=_RECEIPT_KIND[req.outcome],
        actor="simulated_tpa",
        payload={
            "preauth_id": req.preauth_id,
            "outcome": req.outcome,
            "verdict": verdict.outcome.value,
            "reasons": req.reasons,
            "missing": req.missing,
            "provisional_ceiling_inr": req.provisional_ceiling_inr,
            "decided_at": req.decided_at.isoformat(),
            "decision_due_at": req.decision_due_at.isoformat() if req.decision_due_at else None,
            "simulated": True,
            "adjudicator": SIMULATED_ADJUDICATOR,
            "note": PROVISIONAL_NOTE,
        },
    )

    told = _tell_the_family(req)

    return {"status": "ok", "preauth": req.model_dump(mode="json"),
            "receipt_id": receipt.receipt_id, "simulated": True,
            "adjudicator": SIMULATED_ADJUDICATOR, "family_told": told}


# What the family is told, in the state's own words. No figure is quoted as
# covered money: the ceiling is provisional and says so, and a QUERY prices
# nothing at all.
_PROVISIONAL = ("This is provisional cover at admission, not a decision about "
                "any bill, and nothing has been settled.")

# What the family is told when the hour lapses. Status and right, nothing else.
# No verdict, because none was given: that is the whole point of the message.
BREACHED = "clock_breached"

_BREACH_DETAIL = (
    "The insurer owes a decision within one hour under the IRDAI 2024 Master "
    "Circular. Where a delay causes extra cost, such as an additional room day, "
    "that cost sits with the insurer, and delayed settlement carries interest at "
    "two percent above the bank rate. You can raise a grievance with the insurer "
    "and then approach the Insurance Ombudsman. Anbu Care has not filed anything, "
    "cannot compel anyone, and is not claiming this will be won."
)

_SAID = {
    BREACHED: ("still unanswered after the 1-hour window", _BREACH_DETAIL),
    AUTHORIZED: ("authorised", _PROVISIONAL),
    AUTHORIZED_WITH_LIMITS: ("authorised with limits", _PROVISIONAL),
    QUERIED: ("not decided yet",
              "The counterparty asked for more before it will authorise."),
    DENIED: ("refused",
             ("This admission is not covered for cashless, so the family pays "
              "the hospital and claims afterwards.")),
}


def _tell_the_family(req: PreAuthRequest, state_key: str = "") -> dict | None:
    """Send the outcome on the CLAIM consent direction, not the billing one.

    `claim_updates` is the purpose this message belongs to and the only one it
    asks for. A contact who agreed to billing summaries has not thereby agreed
    to follow a claim, and a send that quietly accepted the wrong purpose would
    be the exact conflation the consent module was split to prevent. A contact
    without it is refused, which is correct.

    Never raises. A message that could not go out must not undo an
    authorisation that was recorded.
    """
    from anbu_care.comms import consent as consent_purposes
    from anbu_care.tools import whatsapp_tools

    profile = service.load_profile(req.parent_id)
    if profile is None:
        return None
    contact = next((c for c in profile.family_contacts if c.is_primary),
                   None) or next(iter(profile.family_contacts), None)
    if contact is None:
        return None

    # The breach is told against its own key, not the outcome: on a breach the
    # outcome is still "requested", because nobody answered. Saying anything
    # else would invent a verdict out of a silence.
    state, detail = _SAID.get(state_key or req.outcome, (req.outcome, ""))
    first = profile.name.split()[0] if profile.name else "your parent"
    try:
        return whatsapp_tools.send_family_update(
            case_id=req.case_id, parent_id=req.parent_id,
            to_e164=contact.whatsapp_e164, contact_name=contact.name,
            template_name="preauth_status",
            template_params={"parent_name": first, "state": state,
                             "detail": detail},
            message_class="billing",
            purpose_override=consent_purposes.CLAIM_UPDATES,
        )
    except Exception:
        logger.exception("could not tell the family the pre-auth outcome")
        return None


DEMONSTRATION_SEED = "demonstration_seed"

# How long a seeded clock owns the thread. Long enough that a second seed
# during one recording session is refused, short enough that the next session
# is not blocked by the last one.
SEED_COOLDOWN = timedelta(hours=2)


def backdate_request(case_id: str, minutes: int = 70, force: bool = False) -> dict:
    """Move a pending request's clock into the past, for demonstration.

    The one-hour breach is real and takes an hour. Filming it that way is not
    possible, and faking the lapse with a flag would make the breach a claim
    rather than a fact. So this sets a REAL past `requested_at`, leaves
    `decision_due_at` at exactly that plus one hour, and lets the ordinary tick
    judge it against real wall time. Every invariant holds: the deadline is
    still request plus an hour, the breach still has to be genuinely past, and
    the three timestamps stay three separate facts.

    What it costs is provenance, so provenance is what it records.
    `requested_at_source` becomes "demonstration_seed" on the request and on
    the breach that follows, and a backdated clock can never read on the chain
    as an hour that elapsed on its own.

    Refuses a request that has already been answered: a decided request has no
    clock left to breach, and reversing a recorded decision to make a demo work
    would be editing the record to fit the story.

    Safe to call repeatedly. Once the breach is recorded the clock is spent,
    and this says so rather than issuing a second one.
    """
    case = service.load_case(case_id)
    if case is None:
        return {"status": "error", "error": f"no case {case_id}"}

    # ONE SEEDED CLOCK AT A TIME, per parent.
    #
    # Each seed opens its own case with its own clock, so each is correctly
    # breached once and each sends its own message. That is right per clock and
    # wrong on a recording: two identical breach messages in one thread read as
    # a duplicate-send bug, and the presenter ends up explaining something that
    # is not part of the story. It happened.
    #
    # A message cannot be unsent, so the refusal has to come before the second
    # clock exists rather than after.
    # `force` is the operator saying they cleared the thread. The tool cannot
    # see a WhatsApp conversation and must not pretend to: the guard protects
    # against a second identical message sitting beside the first, and once the
    # first is gone the only one who knows that is the person who deleted it.
    recent = [] if force else [
        r for r in service.recent_seeded_preauths(case.parent_id, SEED_COOLDOWN)
        if r.case_id != case_id]
    if recent:
        latest = max(recent, key=lambda r: r.requested_at)
        return {"status": "already_seeded",
                "preauth": latest.model_dump(mode="json"),
                "seeded_case_id": latest.case_id,
                "note": (f"A demonstration clock was already seeded for this parent on "
                         f"{latest.case_id}, and its breach message is in the thread. "
                         f"Seeding again would put a second identical message beside it. "
                         f"Use that one, or wait "
                         f"{int(SEED_COOLDOWN.total_seconds() // 60)} minutes.")}

    req = next(iter(service.list_preauths(case_id)), None)
    if req is not None and req.outcome != REQUESTED:
        return {"status": "error",
                "error": (f"the pre-authorisation on this case came back "
                          f"{req.outcome}. A decided request has no clock to "
                          "breach, and this will not undo a recorded decision.")}
    if req is not None and req.breach_recorded:
        return {"status": "already_breached",
                "preauth": req.model_dump(mode="json"),
                "note": ("This clock has already been recorded as breached. "
                         "Use a fresh case for another demonstration.")}

    started = datetime.now(UTC) - timedelta(minutes=max(1, int(minutes)))
    profile = service.load_profile(case.parent_id)
    policy = profile.policy if profile else None

    if req is None:
        req = PreAuthRequest(
            preauth_id=service.new_id("preauth"),
            case_id=case_id, parent_id=case.parent_id,
            insurer=policy.insurer if policy else "",
            policy_number=policy.policy_number if policy else "",
            sum_insured_inr=policy.sum_insured_inr if policy else 0,
            cashless_eligible=bool(policy.cashless_eligible) if policy else False,
            admitted_on=case.opened_at.date().isoformat(),
            hospital_name=_hospital(case_id),
            adjudicator=SIMULATED_ADJUDICATOR,
        )
        wrote_receipt = True
    else:
        wrote_receipt = False

    req.requested_at = started
    req.decision_due_at = started + timedelta(hours=1)
    req.outcome = REQUESTED
    req.requested_at_source = DEMONSTRATION_SEED
    service.save_preauth(req)

    if wrote_receipt:
        service.append_receipt(
            case_id, kind="pre_auth.requested", actor="insurer_liaison_agent",
            payload={
                "preauth_id": req.preauth_id, "insurer": req.insurer,
                "policy_number": req.policy_number,
                "sum_insured_inr": req.sum_insured_inr,
                "cashless_eligible": req.cashless_eligible,
                "admitted_on": req.admitted_on,
                "hospital_name": req.hospital_name,
                "requested_at": started.isoformat(),
                "decision_due_at": req.decision_due_at.isoformat(),
                "sla": ("IRDAI 2024 Master Circular: cashless authorisation "
                        "decision within 1 hour"),
                "requested_at_source": DEMONSTRATION_SEED,
                "simulated": True, "adjudicator": SIMULATED_ADJUDICATOR,
                "note": ("A request, not an authorisation. Its start time was "
                         "set backwards for a demonstration of the breach path, "
                         "which this receipt records rather than hides. The "
                         "deadline is still that start plus one hour and the "
                         "counterparty has not answered."),
            })

    return {"status": "ok", "preauth": req.model_dump(mode="json"),
            "requested_at": started.isoformat(),
            "decision_due_at": req.decision_due_at.isoformat(),
            "seconds_past_deadline": int(
                (datetime.now(UTC) - req.decision_due_at).total_seconds()),
            "requested_at_source": DEMONSTRATION_SEED,
            "note": ("The clock is real and the deadline is real. Only its start "
                     "was set backwards, and the chain says so.")}


def sla_tick(now: datetime | None = None) -> dict:
    """Record every cashless clock that has actually lapsed. Sends nothing new.

    Called from outside, like the recovery tick, because Cloud Run holds no
    timer. It reads stored deadlines and compares them to real wall time; it
    does not shorten a window, and it cannot fire early.

    Written ONCE per pre-auth. A breach is a fact about an hour that passed, not
    an event that keeps happening, and a chain that repeated it every minute
    would be counting ticks rather than recording what occurred.
    """
    moment = now or datetime.now(UTC)
    breached: list[dict] = []
    considered = 0

    for req in service.preauths_awaiting_decision():
        considered += 1
        if req.decision_due_at is None or req.breach_recorded:
            continue
        due = req.decision_due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        if moment <= due:
            continue

        req.breach_recorded = True
        service.save_preauth(req)

        late_by = int((moment - due).total_seconds())
        service.append_receipt(
            req.case_id,
            kind="pre_auth.clock_breached",
            actor="sla_clock",
            payload={
                "preauth_id": req.preauth_id,
                # The instant the hour was up, carried as data. The receipt's
                # own timestamp says when this was noticed, which is later and
                # is a different fact. Nothing here claims to have been
                # watching at the moment it lapsed.
                "decision_due_at": due.isoformat(),
                "requested_at": req.requested_at.isoformat(),
                "observed_at": moment.isoformat(),
                "observed_late_by_seconds": late_by,
                "window": "1 hour, cashless authorisation",
                # Where the clock's start came from. A request whose start was
                # set backwards for a demonstration must never read on the
                # chain as an hour that naturally elapsed.
                "requested_at_source": req.requested_at_source,
                "simulated_counterparty": True,
                "adjudicator": SIMULATED_ADJUDICATOR,
                "irdai_right": IRDAI_RIGHT,
                "note": ("The hour passed with no decision recorded. This states "
                         "the right and does nothing else: no grievance has been "
                         "filed, no one has been compelled, and nothing is "
                         "claimed to have been won."),
            },
        )
        # TELL THEM. A clock watched on a family's behalf that lapses in
        # silence is a clock nobody was watching for them. Once, because the
        # breach receipt is written once: a later tick finds breach_recorded
        # set, never reaches here, and sends nothing.
        told = _tell_the_family(req, state_key=BREACHED)

        breached.append({"case_id": req.case_id, "preauth_id": req.preauth_id,
                         "decision_due_at": due.isoformat(),
                         "observed_late_by_seconds": late_by,
                         "requested_at_source": req.requested_at_source,
                         "family_told": (told or {}).get("status")})

    return {"status": "ok", "considered": considered, "breached": breached,
            "note": ("Only clocks that have actually lapsed against real wall "
                     "time. A breach is recorded once and never re-emitted.")}
