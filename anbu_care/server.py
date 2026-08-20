"""Cloud Run entrypoint.

Serves ADK's agent API (and dev UI) plus the few plain HTTP routes the family
dashboard and the demo need — health, intake webhook, and chain verification a
family or insurer can call without going through an agent.

    uv run uvicorn anbu_care.server:app --port 8080
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from google.adk.cli.fast_api import get_fast_api_app
from pydantic import BaseModel

from anbu_care import service
from anbu_care.config import settings
from anbu_care.kb.hospitals import KB_META, load_hospitals
from anbu_care.provenance.signing import load_signer
from anbu_care.tools import (
    brief_tools,
    intake_tools,
    onboarding_tools,
    provenance_tools,
    triage_tools,
)
from anbu_care.webauth import require_family_session

# ADK discovers agents by directory. The repo root holds the `anbu_care`
# package, whose agent.py exposes `root_agent`.
AGENTS_DIR = str(Path(__file__).resolve().parent.parent)

# Memory Bank for cross-session context across weeks of a case. Set
# ANBU_MEMORY_SERVICE_URI to an agentengine:// resource to enable it; without
# it ADK falls back to in-memory, which is fine locally and wrong in production.
MEMORY_SERVICE_URI = os.getenv("ANBU_MEMORY_SERVICE_URI") or None
SESSION_SERVICE_URI = os.getenv("ANBU_SESSION_SERVICE_URI") or None

app = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    web=os.getenv("ANBU_SERVE_DEV_UI", "true").lower() == "true",
    memory_service_uri=MEMORY_SERVICE_URI,
    session_service_uri=SESSION_SERVICE_URI,
    allow_origins=["*"],
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8080")),
)


class IntakeSignalRequest(BaseModel):
    parent_id: str
    channel: str = "er_desk_webhook"
    raw_text: str = ""
    reported_by: str = "unknown"
    # Optional: triage immediately, which is what a real intake desk posting
    # would want. Kept explicit so the two steps stay visibly separate.
    triage_now: bool = True
    symptoms: list[str] = []
    lat: float = 0.0
    lon: float = 0.0


class IntakeRequest(BaseModel):
    parent_id: str
    symptoms: list[str]
    free_text: str = ""
    reported_by: str = "unknown"
    lat: float = 0.0
    lon: float = 0.0
    case_id: str = ""


WEBUI = Path(__file__).resolve().parent / "webui" / "index.html"


@app.get("/app")
def dashboard() -> FileResponse:
    """The family dashboard.

    A view over the endpoints that already exist — it computes nothing the
    backend guarantees. Severity, routing scores, adjudication arithmetic and
    chain verification are all rendered exactly as the audited endpoints
    returned them.
    """
    return FileResponse(WEBUI, media_type="text/html")


@app.get("/api/healthz")
def healthz() -> dict[str, Any]:
    """Liveness, plus the two things that are easy to get wrong on deploy.

    Served under /api/ because Google Front End reserves the bare /healthz path
    and never forwards it to the container — a route defined there works
    locally and returns 404 in Cloud Run.
    """
    signer = load_signer()
    return {
        "status": "ok",
        "project": settings().project_id,
        "model": settings().model,
        "store_backend": settings().store_backend,
        "tpa_mode": settings().tpa_mode,
        "whatsapp_mode": settings().whatsapp_mode,
        "memory_bank": "configured" if MEMORY_SERVICE_URI else "in-memory (not persistent)",
        "signing_key": "ephemeral — set ANBU_SIGNING_KEY_B64" if signer.ephemeral else "configured",
    }


@app.get("/api/hospitals")
def hospitals() -> dict[str, Any]:
    """The seeded knowledge base, served with its provenance attached."""
    return {"meta": KB_META(), "hospitals": [h.model_dump(mode="json") for h in load_hospitals()]}


@app.post("/api/intake")
def intake(request: IntakeRequest) -> dict[str, Any]:
    """Direct triage, bypassing the agent loop.

    This is how an automated intake signal — a hospital feed, a wearable alert,
    a neighbour tapping a button — enters the system, and it is the path the
    demo uses when it needs the routing decision to be reproducible.
    """
    result = triage_tools.run_triage(
        parent_id=request.parent_id,
        symptoms=request.symptoms,
        free_text=request.free_text,
        reported_by=request.reported_by,
        lat=request.lat,
        lon=request.lon,
        case_id=request.case_id,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/api/demo/seed")
def demo_seed() -> dict[str, Any]:
    """Create the Thoothukudi demo family and return its parent_id.

    A freshly deployed service has no parent on record, which makes
    `/api/intake` unusable until someone completes an onboarding conversation.
    This is the shortcut the demo and any smoke test need — it uses the same
    onboarding tools the agent does, so nothing here is a special path.
    """
    created = onboarding_tools.create_parent_profile(
        name="Rajeswari Manickam",
        age=71,
        city="Thoothukudi",
        lat=8.7642,
        lon=78.1400,
        chronic_conditions=["Hypertension", "High cholesterol", "Type 2 diabetes"],
        allergies=["Penicillin"],
    )
    parent_id = created["profile"]["parent_id"]

    onboarding_tools.record_medications(parent_id, [
        {"name": "Telmisartan", "dose": "40 mg", "frequency": "once daily"},
        {"name": "Atorvastatin", "dose": "20 mg", "frequency": "at night"},
        {"name": "Metformin", "dose": "500 mg", "frequency": "twice daily"},
    ])
    onboarding_tools.record_insurance_policy(
        parent_id,
        insurer="Star Health",
        policy_number="SH-NRI-4471902",
        sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital", "Sundaram Arulrhaj Hospitals"],
        cashless_eligible=True,
    )
    onboarding_tools.record_family_contact(
        parent_id,
        name="Karthik Manickam",
        relationship="son",
        whatsapp_e164="+14155550142",
        timezone_name="America/Los_Angeles",
        is_primary=True,
        consent_purposes=["admission_alerts", "status_updates", "billing_updates", "claim_updates"],
    )
    return {
        "status": "seeded",
        "parent_id": parent_id,
        "next": f"POST /api/intake with parent_id={parent_id}",
    }


@app.get("/api/parents/{parent_id}")
def parent_detail(parent_id: str, _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """The baseline record and every document ingested for this parent."""
    result = onboarding_tools.get_parent_profile(parent_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/api/intake-signal")
def intake_signal(request: IntakeSignalRequest) -> dict[str, Any]:
    """An intake signal arriving from outside — the only way an episode starts.

    Anbu Care does not monitor and cannot notice anything on its own. This is
    the inbound channel something else posts to. Every signal is labelled
    SIMULATED in this build, because no real ER system posts here.
    """
    signal = intake_tools.receive_intake_signal(
        parent_id=request.parent_id,
        channel=request.channel,
        raw_text=request.raw_text,
        reported_by=request.reported_by,
    )
    if signal.get("status") == "error":
        raise HTTPException(status_code=400, detail=signal["error"])

    response: dict[str, Any] = {"signal": signal}
    if request.triage_now:
        response["triage"] = triage_tools.run_triage(
            parent_id=request.parent_id,
            symptoms=request.symptoms,
            free_text=request.raw_text,
            reported_by=request.reported_by,
            lat=request.lat,
            lon=request.lon,
            case_id=signal["case_id"],
        )
    return response


@app.get("/api/intake-channels")
def intake_channels() -> dict[str, Any]:
    """The channels an episode can start on. All labelled stubs in this build."""
    return intake_tools.list_intake_channels()


@app.get("/api/cases/{case_id}/brief")
def case_brief(case_id: str, _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """The arrival brief: what is waiting when the family lands.

    Composed from the signed chain. Every line carries its provenance, and
    anything the recorded state does not contain is returned as unknown with a
    reason rather than filled in. Read-only.
    """
    result = brief_tools.get_arrival_brief(case_id)
    if result["brief"]["chain_receipt_count"] == 0:
        raise HTTPException(status_code=404, detail=f"no case {case_id}")
    return result


@app.get("/api/cases/{case_id}/trail")
def case_trail(case_id: str, _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """Reconstruct every decision on a case, in order, with its hash links."""
    result = provenance_tools.get_case_trail(case_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/api/cases/{case_id}/verify")
def case_verify(case_id: str) -> dict[str, Any]:
    """Independently verify a case's chain.

    Deliberately unauthenticated in this build: the point of the receipt chain
    is that a family or an insurer can check it without trusting us to run the
    check for them.
    """
    return provenance_tools.verify_case_chain(case_id)


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str, _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """Case metadata and its current chain head."""
    case = service.load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no case {case_id}")
    chain = service.get_chain(case_id)
    return {
        "case": case.model_dump(mode="json"),
        "receipt_count": len(chain.receipts),
        "head_hash": chain.head_hash,
        "verified": chain.verify().ok,
    }
