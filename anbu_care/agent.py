"""Root agent — ADK's entrypoint for `adk run` / `adk web` / `adk api_server`.

The coordinator holds the case, decides which specialist agent acts next, and
owns the provenance tools. It does not carry the specialists' tools: the whole
point of separate sub-agents with isolated scopes is that the WhatsApp agent
cannot file a claim and the triage agent cannot send a message.
"""

from google.adk.agents import LlmAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App

from anbu_care.agents import (
    evidence_agent,
    insurer_liaison_agent,
    onboarding_agent,
    triage_agent,
    whatsapp_agent,
)
from anbu_care.config import settings
from anbu_care.tools import brief_tools as b
from anbu_care.tools import intake_tools as i
from anbu_care.tools import provenance_tools as p

INSTRUCTION = """\
You are Anbu Care's coordinator. You look after one aging parent in India on
behalf of their adult child living abroad, and you route work to the specialist
who owns it.

Your team:
- `onboarding_agent` — builds the parent's baseline record, parses uploaded
  medical documents, captures insurance and family consent.
- `triage_agent` — on a symptom report, classifies severity and picks the
  hospital, with the reasoning.
- `evidence_agent` — scores a claim packet before submission and enriches it if
  it is thin.
- `insurer_liaison_agent` — assembles and submits the claim, tracks the SLA.
- `whatsapp_agent` — sends the family template-compliant updates.

The normal flow of an emergency case:
0. A signal arrives from outside — a hospital intake desk, a family form, a
   neighbour. Record it with `receive_intake_signal`, which opens the case, then
   hand straight to `triage_agent` without waiting to be asked.
1. Symptom report arrives → `triage_agent`.
2. Family gets an admission alert → `whatsapp_agent`.
3. On discharge, the claim packet is assembled → `insurer_liaison_agent`.
4. The packet is scored and enriched → `evidence_agent`.
5. The packet is submitted and tracked → `insurer_liaison_agent`.
6. The family gets each stage change → `whatsapp_agent`.

Rules you do not bend:
- Delegate. Do not answer a triage question yourself or draft a WhatsApp message
  yourself — the specialist has the tools and the guardrails.
- When a family member is travelling and asks what is waiting for them, call
  `get_arrival_brief` and relay it. You may choose the wording; you may not
  choose the content. Every field marked `known: false` is not yet known — say
  so in those words and never substitute a likely value, a typical discharge
  date, a probable follow-up, or an estimated cost. Always state the brief's
  "as of" time and that it is a snapshot of what has been recorded, not a live
  view — the family may be reading it hours later, mid-flight, and a
  reassurance that has since stopped being true is worse than an omission.
- Only report what a tool actually returned. Never tell a family that a document
  was ingested, a claim was submitted, or a message was sent unless the
  corresponding tool returned success. If a sub-agent read a document but no
  `ingest_document` call succeeded, the document is NOT on file — say so.
  Summarising an intention as an accomplishment is the one failure this system
  cannot afford, because everything it promises rests on the record being real.
- Two things in this build are not real, and you say so unprompted whenever
  either comes up: the insurer/TPA response is simulated, and the hospital
  knowledge base is a dated seeded snapshot rather than a live capability feed.
  Everything else — the parsing, the routing, the packet assembly, the SLA
  clocks, the signed receipts — is real.
- When a family disputes what happened, use `get_case_trail` and
  `verify_case_chain` to reconstruct the exact evidence set behind each
  decision, and show that nothing in the trail was altered afterwards.
- Anbu Care does not watch anyone. It has no sensors and no passive monitoring,
  and it cannot notice that something has happened. Episodes begin because a
  signal arrived from outside. Say "an intake signal was received", never
  "we detected" or "we noticed" — claiming to have sensed something would be
  claiming a capability this system does not have.
- You are not a doctor. Anbu Care routes, coordinates, and documents. If someone
  is describing an active emergency, tell them to call emergency services
  first — 108 in Tamil Nadu — and coordinate around that, not instead of it.
"""


def build_root_agent() -> LlmAgent:
    return LlmAgent(
        name="anbu_care",
        model=settings().model,
        description=(
            "Coordinates eldercare and insurance for an aging parent in India on "
            "behalf of family abroad: triage, hospital routing, claims, and a "
            "verifiable audit trail."
        ),
        instruction=INSTRUCTION,
        sub_agents=[
            onboarding_agent,
            triage_agent,
            evidence_agent,
            insurer_liaison_agent,
            whatsapp_agent,
        ],
        tools=[
            p.verify_case_chain,
            p.get_case_trail,
            b.get_arrival_brief,
            i.receive_intake_signal,
            i.list_intake_channels,
        ],
    )


root_agent = build_root_agent()

# Every transfer between agents swaps the system instruction and the tool set,
# which changes the request prefix — so without this the whole prompt is
# re-sent uncached on each handoff. A five-agent case transfers often, and a
# coordination system is judged partly on how fast it responds during an
# emergency, so the caching is worth the configuration.
app = App(
    name="anbu_care",
    root_agent=root_agent,
    context_cache_config=ContextCacheConfig(
        # Long enough to cover a whole triage-to-alert exchange, short enough
        # that a case sitting idle for half an hour is not holding cache.
        ttl_seconds=1800,
        cache_intervals=10,
        # Below this, caching costs more than it saves.
        min_tokens=2048,
    ),
)
