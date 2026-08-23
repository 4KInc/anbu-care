"""What the policy math says about photographed bills. An estimate, never a verdict.

This reuses `tpa/adjudicator.py` rather than reimplementing it — the same
`SUBLIMIT_RULES`, the same `NON_COVERED_ITEMS`, the same `_cap_for` arithmetic
that produces the ₹66,000 figure in the claim beat. A second copy of those
rules that drifted from the first would be worse than having no estimate at
all, because two numbers that disagree teach a family to trust neither.

**The whole surface is an estimate and says so in its own field names.**

Anbu Care has not asked the insurer anything. The adjudicator is simulated. So
every figure here is `estimated_`, and `settled_inr` is a separate field that
stays None until a real `claim.adjudicated` receipt says otherwise. Those are
different kinds of number and collapsing them would be the most damaging thing
this feature could do: a family that reads "covered ₹292,500" as money they
have is a family that finds out otherwise at the counter.

The reimbursement case makes it sharper still. On a reimbursement claim the
family pays first and is repaid later, so even a correct estimate is not money
they hold today. The disclaimer says that in words rather than leaving it to be
inferred from a layout.
"""

from __future__ import annotations

from anbu_care import service
from anbu_care.schemas import (
    CoverageEstimate,
    CoverageLine,
    ExtractedBill,
    InsurancePolicy,
)
from anbu_care.tpa.adjudicator import (
    NON_COVERED_ITEMS,
    _cap_for,
    stay_days,
)


def _line_estimate(item: str, label: str, amount: int,
                   policy: InsurancePolicy | None, days: int,
                   bill: ExtractedBill | None = None) -> CoverageLine:
    """One line, through the same rules the adjudicator uses."""
    key = item.strip().lower()
    # Carried so three bills' worth of "Nursing charges" can be told apart.
    src = {"bill_id": bill.bill_id if bill else "",
           "vendor": bill.vendor if bill else None}

    if policy is None:
        return CoverageLine(
            label=label, item=item, claimed_inr=amount,
            estimated_covered_inr=0, estimated_you_pay_inr=amount,
            rule="no policy is on file, so nothing can be estimated as covered", **src,
        )

    if key in NON_COVERED_ITEMS:
        return CoverageLine(
            label=label, item=item, claimed_inr=amount,
            estimated_covered_inr=0, estimated_you_pay_inr=amount,
            rule="conventionally excluded from cover", **src,
        )

    explicit = policy.sub_limits_inr.get(key)
    if explicit is not None and amount > explicit:
        return CoverageLine(
            label=label, item=item, claimed_inr=amount,
            estimated_covered_inr=explicit,
            estimated_you_pay_inr=amount - explicit,
            rule=f"policy sub-limit for {key}: INR {explicit:,}", **src,
        )

    # A cap the policy schedule states wins over the conventional percentage.
    # A photographed schedule is this family's actual terms; SUBLIMIT_RULES is
    # what most policies do.
    stated_per_day = None
    if key in {"room_rent", "room", "ward"}:
        stated_per_day = policy.sub_limits_inr.get("room_rent_per_day")
    elif "icu" in key:
        stated_per_day = policy.sub_limits_inr.get("icu_per_day")
    capped = ((stated_per_day * days,
               (f"policy limit INR {stated_per_day:,}/day x {days} day(s) "
                f"= INR {stated_per_day * days:,}"))
              if stated_per_day else _cap_for(key, policy.sum_insured_inr, days))
    if capped is not None:
        cap, rule = capped
        if amount > cap:
            return CoverageLine(
                label=label, item=item, claimed_inr=amount,
                estimated_covered_inr=cap, estimated_you_pay_inr=amount - cap,
                rule=rule, **src,
        )
        return CoverageLine(
            label=label, item=item, claimed_inr=amount,
            estimated_covered_inr=amount, estimated_you_pay_inr=0,
            rule=f"within the sub-limit ({rule})", **src,
        )

    return CoverageLine(
        label=label, item=item, claimed_inr=amount,
        estimated_covered_inr=amount, estimated_you_pay_inr=0,
        rule="no sub-limit applies to this item", **src,
        )


def _settled_so_far(case_id: str) -> int | None:
    """What an adjudication actually decided, if one has. Otherwise None.

    None means nobody has decided anything, which is emphatically not the same
    as nothing being owed — so it is carried as None rather than zero.
    """
    for receipt in reversed(service.get_chain(case_id).receipts):
        if receipt.kind == "claim.adjudicated":
            disallowed = receipt.payload.get("total_disallowed_inr")
            outcome = receipt.payload.get("outcome")
            # Only a priced outcome carries a real figure. A QUERY prices
            # nothing, and reporting its zero would be a false reassurance.
            if outcome in {"PARTIAL", "PASS"} and isinstance(disallowed, int):
                return disallowed
    return None


def estimate_for_case(case_id: str, bills: list[ExtractedBill]) -> CoverageEstimate:
    """The running split across every bill on a case."""
    case = service.load_case(case_id)
    profile = service.load_profile(case.parent_id) if case else None
    policy = profile.policy if profile else None

    # Read off the bills, never inferred. A bill with no discount printed on it
    # contributes nothing here.
    discount = sum(b.discount_inr or 0 for b in bills)

    # A per-day sub-limit is multiplied by the length of THAT bill's stay.
    #
    # One case can carry bills from two different admissions — a general ward
    # stay in August and a cardiac ICU stay a week later are two stays, two
    # date ranges and two day counts. Computing one number per case and
    # applying it to every line was right only by coincidence when both stays
    # happened to be three days, and silently wrong the moment they differ.
    #
    # The claim packet still wins where one exists, for every bill: those dates
    # are structured fields somebody entered rather than a model's reading of a
    # photograph, and a packet describes the admission being claimed for.
    packet_days: int | None = None
    packet_basis = ""
    if case is not None:
        for receipt in reversed(service.get_chain(case_id).receipts):
            if receipt.kind == "claim.packet_assembled":
                packet = service.load_packet(case_id, receipt.payload.get("packet_id", ""))
                if packet is not None:
                    computed = stay_days(packet.admitted_on, packet.discharged_on)
                    if computed:
                        packet_days = computed
                        packet_basis = (f"{computed} day(s) from the claim packet "
                                        f"({packet.admitted_on} to {packet.discharged_on})")
                break

    def days_for(bill: ExtractedBill) -> tuple[int, str]:
        if packet_days:
            return packet_days, packet_basis
        stay = stay_days(bill.admitted_on, bill.discharged_on)
        if stay:
            return stay, (f"{stay} day(s) as printed on the bill "
                          f"({bill.admitted_on} to {bill.discharged_on})")
        return 1, "one day assumed: no admission or discharge date is on record"

    lines: list[CoverageLine] = []
    bases: list[str] = []
    for bill in bills:
        days, basis_days = days_for(bill)
        if basis_days not in bases:
            bases.append(basis_days)
        for entry in bill.line_items:
            lines.append(_line_estimate(entry.item, entry.label, entry.amount_inr,
                                        policy, days, bill))

    lines = _apply_proportionate_deduction(lines, bills, policy)
    lines = _apply_copay(lines, policy)

    # The estimate models proportionate deduction now, so it is no longer
    # silently optimistic. What remains uncertain is narrower and worth saying:
    # WHICH heads a given policy exempts is its own wording, and ours is the
    # common carve-out rather than a reading of this schedule.
    deducted = [l for l in lines if "proportionate deduction" in l.rule]
    understates = bool(deducted)
    note = ""
    if understates:
        heads = sorted({l.label for l in deducted})[:3]
        note = (
            f"A room or ICU charge was above its per-day limit, so the other "
            f"hospital charges have been reduced in the same proportion — "
            f"{', '.join(heads)}{' and others' if len(deducted) > len(heads) else ''}. "
            f"Medicines, consumables and implants are treated as exempt, which is "
            f"the usual carve-out but is decided by your policy wording rather "
            f"than by a general rule. Check the schedule before relying on the "
            f"exact figure."
        )

    return CoverageEstimate(
        case_id=case_id,
        may_understate=understates,
        may_understate_note=note,
        lines=lines,
        bills_counted=len(bills),
        total_billed_inr=sum(line.claimed_inr for line in lines),
        total_discount_inr=discount,
        estimated_covered_inr=sum(line.estimated_covered_inr for line in lines),
        # A hospital discount comes off what the FAMILY owes, not off what the
        # insurer pays: the insurer's share is capped by sub-limits on the line
        # items and a concession by the hospital does not raise that cap. So it
        # reduces the residual, and the split then reconciles with the TOTAL
        # printed on the bill instead of with the sub-total.
        #
        # Floored at zero. A discount larger than the residual would otherwise
        # report the family as owed money, which no bill has ever meant.
        estimated_you_pay_inr=max(
            0, sum(line.estimated_you_pay_inr for line in lines) - discount),
        settled_inr=_settled_so_far(case_id),
        basis=(f"policy sub-limits applied over {'; '.join(bases) or 'no bills'}. "
               f"{'Cashless eligible at network hospitals.' if policy and policy.cashless_eligible else 'Reimbursement: the family pays first and is repaid later.'}"
               if policy else "no policy on file"),
        needs_review=any(bill.needs_review for bill in bills),
    )


# --------------------------------------------------------------------------
# The two rules that decide what a family actually owes
# --------------------------------------------------------------------------

# Heads a proportionate deduction does NOT touch. Indian policies carve out the
# cost of medicines, consumables and implants: those are what they are whatever
# room you were in, and reducing them would be charging a patient for the ward.
_PROPORTION_EXEMPT = (
    "pharmacy", "medicine", "medicines", "drug", "drugs", "consumable",
    "consumables", "implant", "implants", "iv_fluids", "injection", "injections",
    "stent",
)

_ROOM_KEYS = ("room_rent", "room", "ward", "icu", "icu_room", "cardiac_icu_room", "bed")


def _is_room_line(item: str) -> bool:
    key = item.strip().lower()
    return any(k in key for k in _ROOM_KEYS)


def _exempt_from_proportion(item: str) -> bool:
    key = item.strip().lower()
    return any(k in key for k in _PROPORTION_EXEMPT)


def _apply_proportionate_deduction(lines, bills, policy):
    """Reduce associated charges in the ratio the room was over its limit.

    This is the single largest reason a family owes more than a naive sub-limit
    sum suggests, and leaving it out made every estimate optimistic in the one
    direction that hurts. An insurer does not merely refuse the excess room
    rent: where the room occupied is above the eligible category, the ASSOCIATED
    medical expenses are reduced by the same ratio the eligible rent bears to
    the rent actually charged.

    Applied per bill, because the ratio comes from that bill's own room line.
    Medicines, consumables and implants are exempt, as the policy wording says.
    The room line itself is already capped and is not reduced twice.
    """
    if policy is None or not getattr(policy, "proportionate_deduction", True):
        return lines

    # The ratio each bill's room line implies. covered/claimed on that line is
    # exactly "eligible rent over actual rent" without needing the day count
    # again.
    ratio_by_bill: dict[str, tuple[float, str]] = {}
    for line in lines:
        if not _is_room_line(line.item) or line.claimed_inr <= 0:
            continue
        if line.estimated_covered_inr >= line.claimed_inr:
            continue                       # within the limit: nothing to spread
        ratio = line.estimated_covered_inr / line.claimed_inr
        current = ratio_by_bill.get(line.bill_id)
        # The most restrictive room line on a bill governs it.
        if current is None or ratio < current[0]:
            ratio_by_bill[line.bill_id] = (ratio, line.label)

    if not ratio_by_bill:
        return lines

    out = []
    for line in lines:
        entry = ratio_by_bill.get(line.bill_id)
        if (entry is None or _is_room_line(line.item)
                or _exempt_from_proportion(line.item)
                or line.estimated_covered_inr <= 0):
            out.append(line)
            continue
        ratio, room_label = entry
        covered = round(line.estimated_covered_inr * ratio)
        out.append(line.model_copy(update={
            "estimated_covered_inr": covered,
            "estimated_you_pay_inr": line.claimed_inr - covered,
            "rule": (f"{line.rule}; then reduced to {ratio * 100:.0f}% because "
                     f"{room_label} was above its per-day limit "
                     f"(proportionate deduction)"),
        }))
    return out


def _apply_copay(lines, policy):
    """The share of every admissible claim the insured pays regardless.

    Applied last, on what is left as covered, because a co-pay is a share of
    the admissible amount rather than of the bill.
    """
    percent = getattr(policy, "copay_percent", 0) if policy else 0
    if not percent:
        return lines

    out = []
    for line in lines:
        if line.estimated_covered_inr <= 0:
            out.append(line)
            continue
        covered = round(line.estimated_covered_inr * (100 - percent) / 100)
        out.append(line.model_copy(update={
            "estimated_covered_inr": covered,
            "estimated_you_pay_inr": line.claimed_inr - covered,
            "rule": f"{line.rule}; then {percent}% co-pay",
        }))
    return out
