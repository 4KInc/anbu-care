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

    capped = _cap_for(key, policy.sum_insured_inr, days)
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

    # A line capped by a per-day room or ICU sub-limit is the trigger for
    # proportionate deduction under a real Indian policy, which this estimate
    # does not model. Say so, rather than let the number look complete.
    capped = [l for l in lines if "per day" in l.rule and l.estimated_you_pay_inr > 0]
    understates = bool(capped)
    note = ""
    if understates:
        worst = max(capped, key=lambda l: l.estimated_you_pay_inr)
        note = (
            f"{worst.label} was charged above the per-day limit. Indian insurers "
            f"usually also reduce the OTHER hospital charges in the same "
            f"proportion — medicines, consumables and implants excepted — so the "
            f"real shortfall is likely to be larger than the figure above. This "
            f"estimate does not model that reduction."
        )

    return CoverageEstimate(
        case_id=case_id,
        may_understate=understates,
        may_understate_note=note,
        lines=lines,
        bills_counted=len(bills),
        total_billed_inr=sum(line.claimed_inr for line in lines),
        estimated_covered_inr=sum(line.estimated_covered_inr for line in lines),
        estimated_you_pay_inr=sum(line.estimated_you_pay_inr for line in lines),
        settled_inr=_settled_so_far(case_id),
        basis=(f"policy sub-limits applied over {'; '.join(bases) or 'no bills'}. "
               f"{'Cashless eligible at network hospitals.' if policy and policy.cashless_eligible else 'Reimbursement: the family pays first and is repaid later.'}"
               if policy else "no policy on file"),
        needs_review=any(bill.needs_review for bill in bills),
    )
