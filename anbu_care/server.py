"""Cloud Run entrypoint.

Serves ADK's agent API (and dev UI) plus the few plain HTTP routes the family
dashboard and the demo need — health, intake webhook, and chain verification a
family or insurer can call without going through an agent.

    uv run uvicorn anbu_care.server:app --port 8080
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from google.adk.cli.fast_api import get_fast_api_app
from pydantic import BaseModel

from anbu_care import service
from anbu_care.care_circle import notify as care_notify
from anbu_care.comms import consent, inbound
from anbu_care.config import settings
from anbu_care.kb.hospitals import KB_META, load_hospitals
from anbu_care.provenance.signing import load_signer
from anbu_care.tools import (
    brief_tools,
    intake_tools,
    onboarding_tools,
    provenance_tools,
    triage_tools,
    whatsapp_tools,
)
from anbu_care.webauth import require_family_session
from anbu_care.wellbeing import handler as wellbeing_escalation
from anbu_care.wellbeing import store as wellbeing_store

logger = logging.getLogger(__name__)

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
        # Overridable so a recorded demo can point at a real opted-in handset.
        # Defaults to a Twilio test number, which accepts sends and delivers
        # nothing, so an unconfigured deploy cannot message a real person.
        whatsapp_e164=os.getenv("ANBU_DEMO_FAMILY_E164", "+14155550142"),
        timezone_name="America/Los_Angeles",
        is_primary=True,
        consent_purposes=[
            "admission_alerts", "status_updates", "billing_updates", "claim_updates",
            # Named explicitly. Inbound and outbound are separate agreements and
            # neither is implied by the four above.
            consent.INBOUND_WELLBEING, consent.OUTBOUND_NOTIFY,
        ],
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


@app.post("/api/wellbeing/inbound")
async def wellbeing_inbound(request: Request) -> Response:
    """Twilio's inbound webhook: a check-in arriving over WhatsApp.

    Unauthenticated by necessity — Twilio cannot carry a bearer token — so the
    signature is the entire access control on a write path. It is checked
    against the parameters exactly as they arrived, parsed once from the raw
    body and never re-serialised.

    This endpoint has no route to triage. It cannot open a case and cannot set
    a severity. Whatever the words are, they are stored as words.
    """
    raw = await request.body()
    form = urllib.parse.parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True)

    try:
        inbound.verify_twilio_signature(
            inbound.public_url(request), form, request.headers.get("X-Twilio-Signature")
        )
    except inbound.SignatureRejected as exc:
        # One answer for missing, malformed and wrong. A caller learns only
        # that it was refused.
        logger.warning("wellbeing inbound rejected: %s", exc)
        raise HTTPException(status_code=403, detail="signature verification failed") from exc

    fields = dict(form)
    body = (fields.get("Body") or "").strip()
    sender = inbound.resolve_sender(fields.get("From") or "")

    # A voice note arrives with an empty Body. Before this it fell straight
    # through to the "nothing to store" branch and was discarded silently,
    # which is the input a breathless seventy-one year old actually sends.
    media = inbound.media_from(fields) if sender is not None else None
    if sender is not None and media is not None:
        return _handle_voice_note(sender, media)

    if sender is None or not body:
        # Unknown number, withdrawn consent, or an empty message. Nothing is
        # stored, and Twilio is told the webhook succeeded so it does not retry
        # a message we will never accept.
        logger.info("wellbeing inbound not stored: unregistered sender or empty body")
        return Response(status_code=204)

    entry = wellbeing_store.record(sender.parent_id, sender.source, body)
    logger.info("wellbeing recorded %s for %s", entry.entry_id, sender.parent_id)

    # Stored either way. What follows decides whether a person is told, never
    # what is wrong with anyone: Gemini restates the wording, the deterministic
    # severity table rules, and the raw text reaches that table regardless so a
    # silent model cannot quieten a red flag.
    alerted = wellbeing_escalation.handle(entry, sender.parent_id)

    return Response(
        content=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Message>{escape(alerted.reply)}</Message></Response>"
        ),
        media_type="application/xml",
    )


def _handle_voice_note(sender: Any, media: Any) -> Response:
    """Store the recording, transcribe it, and act on whichever we end up with.

    The audio is kept either way. It is the record; the transcript is derived
    from it, and if a model could not make out the words a human still can.
    """
    from anbu_care.comms import storage, transcribe

    stored = storage.store(
        f"voice/{sender.parent_id}/{service.new_id('vn')}.ogg",
        media.audio, content_type=media.mime_type,
    )
    heard = transcribe.transcribe(media.audio, media.mime_type)
    logger.info("voice note from %s: %s", sender.parent_id, heard.detail)

    entry = wellbeing_store.record(
        sender.parent_id, sender.source,
        heard.text if heard.ok else "(voice note, not transcribed)",
        source_kind="voice",
        audio_object=stored.object_name,
    )

    handled = (wellbeing_escalation.handle(entry, sender.parent_id) if heard.ok
               else wellbeing_escalation.handle_unclear_voice(entry, sender.parent_id))

    return Response(
        content=('<?xml version="1.0" encoding="UTF-8"?>'
                 f"<Response><Message>{escape(handled.reply)}</Message></Response>"),
        media_type="application/xml",
    )


@app.get("/api/parents/{parent_id}/wellbeing")
def parent_wellbeing(
    parent_id: str, _session: str = Depends(require_family_session)
) -> dict[str, Any]:
    """Check-ins for a parent. Credentialed, because it returns what was said."""
    entries = wellbeing_store.list_entries(parent_id)
    return {
        "parent_id": parent_id,
        "count": len(entries),
        "label": "Self-reported. Not a clinical assessment and not a measured vital.",
        "entries": [e.model_dump(mode="json") for e in entries],
    }


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


@app.post("/api/cases/{case_id}/notify-claim")
def notify_claim(case_id: str, _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """Send the family the claim outcome, with the assessment attached.

    Credentialed, because it causes a message to leave the platform. The
    recipient is read from the parent's registered family contact, never from
    the request, so this cannot be pointed at an arbitrary number by anyone who
    holds the session token.

    Everything else is the ordinary gated path: the classifier decides whether
    the message may go, the artifact is built from adjudicator output by code,
    and a refusal at either point is recorded rather than smoothed over.
    """
    case = service.load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no case {case_id}")

    profile = service.load_profile(case.parent_id)
    if profile is None or not profile.family_contacts:
        raise HTTPException(status_code=404, detail="no family contact on this parent")
    contact = next((c for c in profile.family_contacts if c.is_primary), profile.family_contacts[0])

    adjudication = service.latest_adjudication(case_id)
    if adjudication is None:
        raise HTTPException(status_code=409, detail="this case has not been adjudicated yet")

    stage = {"PASS": "approved", "PARTIAL": "partially approved",
             "QUERY": "under query", "DENY": "declined"}[adjudication.outcome.value]
    # An unpriced outcome has no payable figure. "not yet known" is the honest
    # value; a zero here would state a decision the adjudicator never made.
    amount = (_inr_plain(adjudication.total_allowed_inr)
              if adjudication.outcome.value in {"PASS", "PARTIAL"} else "not yet known")

    return whatsapp_tools.send_family_update(
        case_id=case_id,
        parent_id=case.parent_id,
        to_e164=contact.whatsapp_e164,
        template_name="claim_stage",
        template_params={"parent_name": profile.name.split()[0],
                         "stage": stage, "amount": amount},
        message_class="billing",
        attach_claim_summary=True,
    )


def _inr_plain(amount: int) -> str:
    from anbu_care.comms.artifacts import _inr

    return _inr(amount)


@app.post("/api/cases/{case_id}/notify-care-circle")
def notify_care_circle(
    case_id: str, _session: str = Depends(require_family_session)
) -> dict[str, Any]:
    """Tell the listed contacts where the parent was taken.

    Outbound only. These are notified parties, not integrated providers: no
    channel opens back, nothing waits for a reply, and no clinician is "in the
    system".

    Credentialed, because it sends real messages. Triage never calls this — a
    notification is a decision someone takes, not something the guarantee layer
    does on its own.

    Logistics are read from the triage receipt, so the notice can only say what
    the chain already recorded.
    """
    case = service.load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no case {case_id}")

    triage = next(
        (r for r in reversed(service.get_chain(case_id).receipts)
         if r.kind == "triage.decision"), None,
    )
    if triage is None:
        raise HTTPException(
            status_code=409,
            detail="triage has not run on this case; there is no hospital to name yet",
        )

    hospital = triage.payload.get("recommended_hospital_name") or _hospital_from(triage)
    cashless = _cashless_line(case.parent_id)

    results = care_notify.notify(
        case_id=case_id,
        parent_id=case.parent_id,
        hospital_name=hospital,
        timestamp=triage.created_at.strftime("%d %b, %H:%M UTC"),
        cashless_status=cashless,
    )
    return {
        "case_id": case_id,
        "label": "Notified parties. Anbu Care does not integrate with any provider.",
        # Counted, never collapsed: a caller must not be able to read this as
        # "the care circle was notified" when one of them was not.
        "sent": sum(1 for r in results if r.delivered),
        "not_delivered": sum(1 for r in results if r.consented and not r.delivered),
        "skipped_no_consent": sum(1 for r in results if not r.consented),
        "results": [r.model_dump(mode="json") for r in results],
    }


def _hospital_from(triage: Any) -> str:
    ranked = triage.payload.get("ranked") or []
    recommended = triage.payload.get("recommended_hospital_id")
    for entry in ranked:
        if entry.get("hospital_id") == recommended:
            return str(entry.get("name") or "the hospital")
    return "the hospital"


def _cashless_line(parent_id: str) -> str:
    """Whether the policy says cashless, in plain words. No claim outcome here."""
    profile = service.load_profile(parent_id)
    policy = getattr(profile, "policy", None) if profile else None
    if policy is None:
        return "Insurance details are not on record"
    return ("Cashless is available at network hospitals"
            if policy.cashless_eligible else "This admission is reimbursement only")


@app.get("/api/parents/{parent_id}/care-circle")
def parent_care_circle(
    parent_id: str, _session: str = Depends(require_family_session)
) -> dict[str, Any]:
    """Who would be notified. Derived from consent, not from a stored roster."""
    contacts = care_notify.care_circle(parent_id)
    return {
        "parent_id": parent_id,
        "label": "Notified parties, not integrated providers.",
        "contacts": [
            {"name": c.name, "relationship": c.relationship, "role": c.role,
             "to_e164": c.whatsapp_e164}
            for c in contacts
        ],
    }


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
