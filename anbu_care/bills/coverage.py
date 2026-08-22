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
                   policy: InsurancePolicy | None, days: int) -> CoverageLine:
    """One line, through the same rules the adjudicator uses."""
    key = item.strip().lower()

    if policy is None:
        return CoverageLine(
            label=label, item=item, claimed_inr=amount,
            estimated_covered_inr=0, estimated_you_pay_inr=amount,
            rule="no policy is on file, so nothing can be estimated as covered",
        )

    if key in NON_COVERED_ITEMS:
        return CoverageLine(
            label=label, item=item, claimed_inr=amount,
            estimated_covered_inr=0, estimated_you_pay_inr=amount,
            rule="conventionally excluded from cover",
        )

    explicit = policy.sub_limits_inr.get(key)
    if explicit is not None and amount > explicit:
        return CoverageLine(
            label=label, item=item, claimed_inr=amount,
            estimated_covered_inr=explicit,
            estimated_you_pay_inr=amount - explicit,
            rule=f"policy sub-limit for {key}: INR {explicit:,}",
        )

    capped = _cap_for(key, policy.sum_insured_inr, days)
    if capped is not None:
        cap, rule = capped
        if amount > cap:
            return CoverageLine(
                label=label, item=item, claimed_inr=amount,
                estimated_covered_inr=cap, estimated_you_pay_inr=amount - cap,
                rule=rule,
            )
        return CoverageLine(
            label=label, item=item, claimed_inr=amount,
            estimated_covered_inr=amount, estimated_you_pay_inr=0,
            rule=f"within the sub-limit ({rule})",
        )

    return CoverageLine(
        label=label, item=item, claimed_inr=amount,
        estimated_covered_inr=amount, estimated_you_pay_inr=0,
        rule="no sub-limit applies to this item",
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

    # Days come from the claim packet when one exists, because that is where
    # admission and discharge are recorded as structured fields. Absent one,
    # a single day is used and the basis says so — never inferred from a bill.
    days = 1
    basis_days = "one day assumed: no claim packet records admission and discharge dates"
    if case is not None:
        for receipt in reversed(service.get_chain(case_id).receipts):
            if receipt.kind == "claim.packet_assembled":
                packet = service.load_packet(case_id, receipt.payload.get("packet_id", ""))
                if packet is not None:
                    computed = stay_days(packet.admitted_on, packet.discharged_on)
                    if computed:
                        days, basis_days = computed, (
                            f"{computed} day(s) from the claim packet "
                            f"({packet.admitted_on} to {packet.discharged_on})")
                break

    lines: list[CoverageLine] = []
    for bill in bills:
        for entry in bill.line_items:
            lines.append(_line_estimate(entry.item, entry.label, entry.amount_inr,
                                        policy, days))

    return CoverageEstimate(
        case_id=case_id,
        lines=lines,
        bills_counted=len(bills),
        total_billed_inr=sum(line.claimed_inr for line in lines),
        estimated_covered_inr=sum(line.estimated_covered_inr for line in lines),
        estimated_you_pay_inr=sum(line.estimated_you_pay_inr for line in lines),
        settled_inr=_settled_so_far(case_id),
        basis=(f"policy sub-limits applied over {basis_days}. "
               f"{'Cashless eligible at network hospitals.' if policy and policy.cashless_eligible else 'Reimbursement: the family pays first and is repaid later.'}"
               if policy else "no policy on file"),
        needs_review=any(bill.needs_review for bill in bills),
    )
