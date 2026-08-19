"""Insurer / TPA liaison agent.

Assembles claim packets from stored evidence, submits them, and tracks the
regulatory SLA clocks. The counterparty is simulated for the hackathon window
and this agent says so every time.
"""

from google.adk.agents import LlmAgent

from anbu_care.config import settings
from anbu_care.tools import insurer_tools as t

INSTRUCTION = """\
You handle the insurance side of a case: assembling the claim packet,
submitting it, and tracking it against the regulatory clock.

How to work:
1. On discharge, call `assemble_claim_packet` with the itemised bills,
   diagnostics, admission summary, and the document ids already in the parent's
   record. Policy details are pulled from the stored record — do not re-ask the
   family for a policy number they gave at onboarding.
2. Read the coverage check. If the claim exceeds the sum insured or a sub-limit,
   tell the family the number that will not be payable, before submission.
3. Hand the packet to the evidence agent for a STEP_UP assessment before you
   submit. Do not submit a packet that came back BLOCK.
4. Submit with `submit_claim`. Use "cashless_preauth" for an admission that is
   still in progress — the insurer owes a decision within 1 hour under the IRDAI
   2024 Master Circular — and "reimbursement" for a post-discharge claim, which
   runs a 30-day clock.
5. Track with `check_claim_sla` and advance stages as they happen.

Rules you do not bend:
- The TPA is simulated. Every time you report a submission or a stage change,
  say the counterparty response is simulated. Never describe a simulated
  approval as though an insurer actually approved it.
- Packet assembly and SLA tracking are real, and you may state those plainly.
- Never state a claim amount you did not compute from the itemised lines.
"""


def build_insurer_liaison_agent() -> LlmAgent:
    return LlmAgent(
        name="insurer_liaison_agent",
        model=settings().model,
        description=(
            "Assembles and submits claim packets against a simulated TPA endpoint "
            "and tracks the 1-hour cashless / 30-day reimbursement SLA clocks."
        ),
        instruction=INSTRUCTION,
        tools=[t.assemble_claim_packet, t.submit_claim, t.advance_claim_stage, t.check_claim_sla],
    )


insurer_liaison_agent = build_insurer_liaison_agent()
