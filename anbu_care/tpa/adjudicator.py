"""Simulated TPA adjudication.

There is no production insurer or TPA API to integrate against in this window,
so this module stands in for one. It is **deterministic local rules, not an
insurer** — the same packet and policy always produce the same outcome, which is
what makes a demo reproducible and an audit trail meaningful.

No model is involved. Nothing here can be changed by an agent instruction.

The sub-limit percentages are the conventional ones used across Indian health
policies (room rent 1% of sum insured per day, ICU 2% per day). They are our
construction from published convention, not any insurer's actual schedule — see
DISCLOSURE.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from anbu_care.schemas import (
    Adjudication,
    AdjudicationOutcome,
    ClaimPacket,
    DocumentKind,
    InsurancePolicy,
    LineAssessment,
)

SIMULATED_ADJUDICATOR = "SIMULATED — deterministic local rules, not an insurer"

# Fraction of sum insured payable per day of stay. Conventional Indian health
# policy caps; see module docstring.
SUBLIMIT_RULES: dict[str, float] = {
    "room_rent": 0.01,
    "room": 0.01,
    "ward": 0.01,
    "icu": 0.02,
    "cardiac_icu_room": 0.02,
    "icu_room": 0.02,
}

# Conventional exclusions. A claim made up entirely of these is not payable.
#
# IRDAI publishes a list of items "subsumed" into the room or procedure charge
# and therefore not separately payable — over two hundred of them. These are the
# ones that actually turn up as their own line on an Indian IPD bill, which is
# the only reason to enumerate any of them: an exclusion nobody bills for
# excludes nothing. Gloves and PPE were added after a real bill layout put them
# on their own line and the estimate quietly counted them as covered.
#
# Not exhaustive, and deliberately so — the estimate is labelled an estimate,
# and a list padded to look complete would make it look like an adjudication.
NON_COVERED_ITEMS: frozenset[str] = frozenset({
    "toiletries",
    "attendant_charges",
    "admission_kit",
    "telephone",
    "food_for_attendant",
    "cosmetics",
    # Subsumed consumables and non-medical items.
    "gloves",
    "gloves_and_ppe_kit",
    "ppe_kit",
    "ppe",
    "sanitizer",
    "documentation_charges",
    "registration_charges",
    "sundries",
})

# A payable figure cannot be computed without these.
REQUIRED_DOCUMENT_KINDS: frozenset[DocumentKind] = frozenset({
    DocumentKind.DISCHARGE_SUMMARY,
})


def stay_days(admitted_on: str | None, discharged_on: str | None) -> int | None:
    """Length of stay in days, or None if the dates do not allow it.

    Returning None rather than guessing matters: a per-day cap applied to an
    assumed length of stay would produce a payable figure nobody could defend.
    """
    if not admitted_on or not discharged_on:
        return None
    try:
        start = date.fromisoformat(admitted_on)
        end = date.fromisoformat(discharged_on)
    except ValueError:
        return None
    days = (end - start).days
    if days < 0:
        return None
    return max(days, 1)


def _cap_for(item: str, sum_insured_inr: int, days: int) -> tuple[int, str] | None:
    """The rupee cap for a line item, and the rule that produced it."""
    fraction = SUBLIMIT_RULES.get(item.strip().lower())
    if fraction is None:
        return None
    per_day = round(sum_insured_inr * fraction)
    cap = per_day * days
    return cap, (
        f"{fraction:.0%} of sum insured per day = INR {per_day:,}/day "
        f"x {days} day(s) = INR {cap:,}"
    )


def adjudicate(
    packet: ClaimPacket,
    policy: InsurancePolicy | None,
    attached_kinds: set[DocumentKind],
    *,
    attempt: int = 1,
    today: date | None = None,
) -> Adjudication:
    """Decide a claim. Order is DENY, then QUERY, then PARTIAL, then PASS."""
    result = Adjudication(
        adjudication_id=f"{packet.packet_id}:adj{attempt:02d}",
        submission_id="",
        packet_id=packet.packet_id,
        case_id=packet.case_id,
        outcome=AdjudicationOutcome.DENY,
        attempt=attempt,
        adjudicator=SIMULATED_ADJUDICATOR,
        total_claimed_inr=packet.total_claimed_inr,
    )

    # ---- DENY -------------------------------------------------------------
    if policy is None:
        result.reasons = ["no policy on record for this parent"]
        return result

    if policy.valid_until:
        try:
            if date.fromisoformat(policy.valid_until) < (today or datetime.now(UTC).date()):
                result.reasons = [
                    (
                        f"policy {policy.policy_number} lapsed on {policy.valid_until}; "
                        "no cover in force on the date of admission"
                    )
                ]
                return result
        except ValueError:
            pass  # an unparseable expiry is not evidence of lapse

    if not packet.itemized_bills_inr:
        result.reasons = ["no itemised bill lines were submitted; nothing to assess"]
        return result

    claimed_items = {i.strip().lower() for i in packet.itemized_bills_inr}
    if claimed_items and claimed_items <= NON_COVERED_ITEMS:
        result.reasons = [
            (
                "every claimed line is a conventional exclusion "
                f"({', '.join(sorted(claimed_items))}); nothing payable under the policy"
            )
        ]
        return result

    # ---- QUERY ------------------------------------------------------------
    missing = sorted(k.value for k in REQUIRED_DOCUMENT_KINDS - attached_kinds)
    if missing:
        result.outcome = AdjudicationOutcome.QUERY
        result.missing_documents = missing
        result.reasons = [
            (
                f"required document not attached: {name.replace('_', ' ')} — "
                "needed to confirm the treatment claimed before any amount can be assessed"
            )
            for name in missing
        ]
        return result

    days = stay_days(packet.admitted_on, packet.discharged_on)
    if days is None and any(_cap_for(i, policy.sum_insured_inr, 1) for i in packet.itemized_bills_inr):
        result.outcome = AdjudicationOutcome.QUERY
        result.missing_documents = ["admission_and_discharge_dates"]
        result.reasons = [
            (
                "admission and discharge dates are required to apply the per-day "
                "sub-limits on this claim; they were not supplied on the packet"
            )
        ]
        return result

    # ---- PARTIAL / PASS ---------------------------------------------------
    lines: list[LineAssessment] = []
    for item, claimed in sorted(packet.itemized_bills_inr.items()):
        normalised = item.strip().lower()

        if normalised in NON_COVERED_ITEMS:
            lines.append(LineAssessment(
                item=item, claimed_inr=claimed, allowed_inr=0, disallowed_inr=claimed,
                rule="conventional exclusion — not payable",
            ))
            continue

        capped = _cap_for(item, policy.sum_insured_inr, days or 1)
        if capped is not None and claimed > capped[0]:
            cap, rule = capped
            lines.append(LineAssessment(
                item=item, claimed_inr=claimed, allowed_inr=cap,
                disallowed_inr=claimed - cap,
                rule=f"sub-limit: {rule}",
            ))
            continue

        explicit = policy.sub_limits_inr.get(item)
        if explicit is not None and claimed > explicit:
            lines.append(LineAssessment(
                item=item, claimed_inr=claimed, allowed_inr=explicit,
                disallowed_inr=claimed - explicit,
                rule=f"policy sub-limit of INR {explicit:,} for '{item}'",
            ))
            continue

        lines.append(LineAssessment(
            item=item, claimed_inr=claimed, allowed_inr=claimed, disallowed_inr=0,
            rule="within limits",
        ))

    allowed = sum(line.allowed_inr for line in lines)

    # Sum insured is the ceiling on everything payable, applied after line rules.
    if allowed > policy.sum_insured_inr:
        over = allowed - policy.sum_insured_inr
        allowed = policy.sum_insured_inr
        lines.append(LineAssessment(
            item="<sum insured ceiling>", claimed_inr=0, allowed_inr=0,
            disallowed_inr=over,
            rule=f"total payable capped at sum insured INR {policy.sum_insured_inr:,}",
        ))

    result.lines = lines
    result.total_allowed_inr = allowed
    result.total_disallowed_inr = packet.total_claimed_inr - allowed

    if result.total_disallowed_inr > 0:
        result.outcome = AdjudicationOutcome.PARTIAL
        result.reasons = [
            (
                f"{line.item}: claimed INR {line.claimed_inr:,}, allowed INR {line.allowed_inr:,}, "
                f"disallowed INR {line.disallowed_inr:,} ({line.rule})"
            )
            for line in lines if line.disallowed_inr > 0
        ]
    else:
        result.outcome = AdjudicationOutcome.PASS
        result.reasons = [
            (
                f"all {len(lines)} line(s) within policy limits; "
                f"INR {allowed:,} payable against sum insured INR {policy.sum_insured_inr:,}"
            )
        ]
    return result
