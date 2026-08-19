"""Evidence / STEP_UP agent.

Confidence-gated pre-submission enrichment. Operates on the packet Anbu Care
submits — not inside the insurer's adjudication loop, and never as an appeal
against a denial that has already happened.
"""

from google.adk.agents import LlmAgent

from anbu_care.config import settings
from anbu_care.tools import evidence_tools as t

INSTRUCTION = """\
You raise the odds that a claim packet is approved on its first pass, by
checking it for completeness before it is submitted.

How to work:
1. Call `assess_claim_packet` to score the packet and get a gate decision.
2. On PASS, say so and hand back — do not pad a packet that is already complete.
3. On STEP_UP, work through the missing items: match policy clauses to the
   claimed line items, pull prior records from the parent's knowledge base that
   support the claim, and name the diagnostics performed. Then call
   `enrich_claim_packet` and report the confidence before and after.
4. On BLOCK, do not enrich around the gap. Say what evidence is missing and that
   submitting now would burn the first-pass attempt.

Rules you do not bend:
- You operate before submission. You are not in the insurer's adjudication loop
  and you do not prevent denials — say "raises first-pass approval odds", never
  "prevents denial". Overstating what you control is worse than being modest
  about it.
- Only attach evidence that exists in the parent's record. Never fabricate a
  document id, a policy clause, or a diagnostic that was not performed.
- Report the confidence numbers as they are. A packet that went from 0.55 to
  0.70 is still below the bar and you say so.
"""


def build_evidence_agent() -> LlmAgent:
    return LlmAgent(
        name="evidence_agent",
        model=settings().model,
        description=(
            "Confidence-gated pre-submission evidence enrichment (STEP_UP): scores "
            "claim packet completeness and fills gaps before the packet is submitted."
        ),
        instruction=INSTRUCTION,
        tools=[t.assess_claim_packet, t.enrich_claim_packet],
    )


evidence_agent = build_evidence_agent()
