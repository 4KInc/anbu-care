"""Cloud Run entrypoint.

Serves ADK's agent API (and dev UI) plus the few plain HTTP routes the family
dashboard and the demo need — health, intake webhook, and chain verification a
family or insurer can call without going through an agent.

    uv run uvicorn anbu_care.server:app --port 8080
"""

from __future__ import annotations

import hmac
import logging
import os
import urllib.parse
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from fastapi import (
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import FileResponse, HTMLResponse
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
from anbu_care.webauth import require_case_access, require_family_session
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

    `no-cache` because the page ships its own JavaScript inline. Without a
    cache-control header a browser applies HEURISTIC caching off last-modified,
    which means a family can be served the previous build after a deploy — a
    fix that is live on the server and invisible in the tab. This was hit while
    verifying one. `no-cache` still revalidates against the ETag, so an
    unchanged page is a 304 and costs nothing; it only forbids serving a stale
    copy without asking.
    """
    return FileResponse(WEBUI, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


LOGO = Path(__file__).resolve().parent / "webui" / "static" / "logo.png"


@app.get("/logo.png")
def logo() -> FileResponse:
    """The mark, served publicly.

    WhatsApp fetches a business profile photo from a URL it can reach without
    a credential, so this one route is deliberately open. It carries no case
    content — it is a letter on a teal square.
    """
    return FileResponse(LOGO, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/auth-config")
def auth_config() -> dict[str, Any]:
    """What sign-in methods this deployment offers.

    The Google client id is public by design — it identifies the application
    to Google and is embedded in every page that offers the button. What is
    secret is nothing: the security is that the ID TOKEN is verified server
    side against Google's keys, with this id pinned as the audience.
    """
    from anbu_care.webauth import google_client_id

    return {
        "google_client_id": google_client_id(),
        "demo_sign_in": True,
        "note": ("A Google account must already be a family contact on the "
                 "parent being read. Signing in is not permission."),
    }


@app.get("/api/whoami")
def whoami(request: Request,
           authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Who the presented credential says you are. Never 401s.

    A sign-in that succeeds but shows nothing is indistinguishable from one
    that failed, so the dashboard asks this and puts a name in the header.
    """
    from anbu_care.webauth import DEMO_TOKEN, verify_google_identity

    if not authorization or not authorization.lower().startswith("bearer "):
        return {"signed_in": False}

    presented = authorization.split(" ", 1)[1].strip()
    if hmac.compare_digest(presented, DEMO_TOKEN):
        return {"signed_in": True, "method": "demo", "name": "the family (demo)"}

    claims = verify_google_identity(presented)
    if claims is None:
        return {"signed_in": False}
    return {"signed_in": True, "method": "google",
            "name": claims.get("name") or claims.get("email"),
            "email": claims.get("email"), "picture": claims.get("picture")}


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


@app.get("/api/map-config")
def map_config() -> dict[str, Any]:
    """What the dashboard needs to draw a real map.

    The key is referrer-restricted to this service, so publishing it here is
    how a browser key is meant to work — it is not a secret, the restriction
    is the control. Absent, the map degrades to a list rather than breaking.
    """
    return {
        "maps_api_key": os.getenv("ANBU_MAPS_API_KEY", ""),
        "label": ("Hospital identity and location verified against Google Places. "
                  "Insurer empanelment and capability remain seeded."),
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
        name="Ashanthi Machado",
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
        # `or` and not a getenv default: the deploy passes ${VAR:-} for each of
        # these, so on Cloud Run they are SET AND EMPTY when unset in .env, and
        # a getenv default never fires for a set-but-empty variable. A seeded
        # contact with an empty name or number fails quietly and looks like data
        # loss.
        name=os.getenv("ANBU_DEMO_FAMILY_NAME") or "Heartlin Machado",
        relationship="son",
        # Overridable so a recorded demo can point at a real opted-in handset.
        # Defaults to a Twilio test number, which accepts sends and delivers
        # nothing, so an unconfigured deploy cannot message a real person.
        whatsapp_e164=os.getenv("ANBU_DEMO_FAMILY_E164") or "+14155550142",
        # Same reasoning as the number: a recorded demo needs the seeded family
        # bound to the account that will actually sign in, or every fresh seed
        # has to be re-linked by hand before the sign-in beat works. Empty by
        # default, which means the seeded contact cannot sign in — sending
        # messages and reading the record are separate permissions here.
        email=os.getenv("ANBU_DEMO_FAMILY_EMAIL") or "",
        timezone_name="America/Los_Angeles",
        is_primary=True,
        consent_purposes=[
            "admission_alerts", "status_updates", "billing_updates", "claim_updates",
            # Named explicitly. Inbound and outbound are separate agreements and
            # neither is implied by the four above.
            consent.INBOUND_WELLBEING, consent.OUTBOUND_NOTIFY,
        ],
    )
    # The parent's own agreement that her record may be shown to a treating
    # clinician. Recorded on HER profile, separately from the six purposes
    # above, because those are the son's agreements about his own traffic and
    # this is hers about her own data. Without it the handoff link is refused,
    # which is correct but makes a seeded demo look broken.
    onboarding_tools.record_emergency_disclosure_consent(parent_id)

    return {
        "status": "seeded",
        "parent_id": parent_id,
        "next": f"POST /api/intake with parent_id={parent_id}",
    }


@app.get("/api/parents/{parent_id}")
def parent_detail(parent_id: str, _session: str = Depends(require_case_access)) -> dict[str, Any]:
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
async def wellbeing_inbound(request: Request, background: BackgroundTasks) -> Response:
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
        if media.kind == "image":
            return _handle_bill_photo(sender, media, background)
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

    handled = (
        # The reading came back with the transcript, so the escalation does not
        # make a second model call.
        wellbeing_escalation.handle(entry, sender.parent_id, reading=heard.reading)
        if heard.ok else
        wellbeing_escalation.handle_unclear_voice(entry, sender.parent_id)
    )

    return Response(
        content=('<?xml version="1.0" encoding="UTF-8"?>'
                 f"<Response><Message>{escape(handled.reply)}</Message></Response>"),
        media_type="application/xml",
    )


@app.get("/api/parents/{parent_id}/wellbeing")
def parent_wellbeing(
    parent_id: str, _session: str = Depends(require_case_access)
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
def case_brief(case_id: str, _session: str = Depends(require_case_access)) -> dict[str, Any]:
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
def case_trail(case_id: str, _session: str = Depends(require_case_access)) -> dict[str, Any]:
    """Reconstruct every decision on a case, in order, with its hash links."""
    result = provenance_tools.get_case_trail(case_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/api/cases/{case_id}/trace")
def case_trace(case_id: str, _session: str = Depends(require_case_access)) -> dict[str, Any]:
    """The decision sequence, as the chain recorded it.

    Credentialed, because the steps carry case content. The integrity half —
    `/verify` — stays public, so a reader can watch the agent decide here and
    check that it did over there, without either view leaking the other's
    property.
    """
    from anbu_care.trace import compose_trace

    if service.load_case(case_id) is None:
        raise HTTPException(status_code=404, detail=f"no case {case_id}")

    trace = compose_trace(case_id)
    return {
        **trace.model_dump(mode="json"),
        # The pairing this view exists to make: autonomy you can check.
        "verify_url": f"/api/cases/{case_id}/verify",
        "verify_is_public": True,
    }


@app.get("/api/cases/{case_id}/bills")
def case_bills(case_id: str, _session: str = Depends(require_case_access)) -> dict[str, Any]:
    """Photographed bills and the estimated split. Credentialed, always.

    Bill content is financial *and* clinical — the line items name the
    procedures — so this sits behind the same door as `/trail` and `/brief`.
    Every amount is labelled an estimate, and each line carries the bill and
    the source image it was read from, so a mis-read number can be checked
    against the photograph rather than argued about.
    """
    from anbu_care.bills import estimate_for_case, list_bills

    if service.load_case(case_id) is None:
        raise HTTPException(status_code=404, detail=f"no case {case_id}")

    bills = list_bills(case_id)
    estimate = estimate_for_case(case_id, bills)

    return {
        "case_id": case_id,
        "bills": [
            {
                **bill.model_dump(mode="json", exclude={"image_object"}),
                # The object name is never handed out. A short-lived signed URL
                # is minted on request, per bill, through the route below.
                "image_url": f"/api/cases/{case_id}/bills/{bill.bill_id}/image",
                "computed_total_inr": bill.computed_total_inr,
            }
            for bill in bills
        ],
        "estimate": estimate.model_dump(mode="json"),
        "is_estimate_not_settlement": True,
    }


@app.get("/api/cases/{case_id}/bills/{bill_id}/image")
def case_bill_image(case_id: str, bill_id: str,
                    _session: str = Depends(require_case_access)) -> dict[str, Any]:
    """A short-lived signed URL for the source photograph.

    The bucket stays private. This is how a family checks INR 96,000 against
    what the paper actually says, which is the whole reason the image is kept.
    """
    from anbu_care.bills import list_bills
    from anbu_care.comms import storage

    bill = next((b for b in list_bills(case_id) if b.bill_id == bill_id), None)
    if bill is None:
        raise HTTPException(status_code=404, detail=f"no bill {bill_id} on case {case_id}")
    if not bill.image_object:
        raise HTTPException(status_code=404, detail="no image was stored for this bill")

    signed = storage.signed_url(bill.image_object)
    if not signed.stored or not signed.url:
        raise HTTPException(status_code=503, detail=signed.detail)
    return {"url": signed.url, "expires_in_seconds": signed.expires_in_seconds,
            "note": "short-lived signed URL; the bucket itself is not public"}


@app.get("/api/parents/{parent_id}/documents/{document_id}/image")
def parent_document_image(parent_id: str, document_id: str,
                          _session: str = Depends(require_case_access)) -> dict[str, Any]:
    """A short-lived signed URL for a document's source photograph.

    The same reason the bill lane has one: a reading is worth very little if
    nobody can hold it against the paper it came from. A discharge summary
    especially — the dates on it price the claim.
    """
    from anbu_care.comms import storage

    doc = next((d for d in service.list_documents(parent_id)
                if d.document_id == document_id), None)
    if doc is None:
        raise HTTPException(status_code=404,
                            detail=f"no document {document_id} for {parent_id}")
    if not doc.source_filename:
        raise HTTPException(status_code=404,
                            detail="no photograph was stored for this document")

    signed = storage.signed_url(doc.source_filename)
    if not signed.stored or not signed.url:
        raise HTTPException(status_code=503, detail=signed.detail)
    return {"url": signed.url, "expires_in_seconds": signed.expires_in_seconds,
            "note": "short-lived signed URL; the bucket itself is not public"}


# ---- interim bill payment -------------------------------------------------
#
# Granting and revoking are family acts and need a family session. Reading the
# money view needs case access like every other content route. Nothing here can
# choose a destination: that is fixed at grant time by a human and read from
# the mandate by the enforcer.


@app.post("/api/cases/{case_id}/payment-mandate")
def grant_mandate(case_id: str, body: dict[str, Any],
                  _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """Authorise automatic payment of interim bills, within bounds."""
    from anbu_care.payments import MandateRejected, grant

    case = service.load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no case {case_id}")
    try:
        mandate = grant(
            parent_id=case.parent_id, case_id=case_id,
            payee_vpa=str(body.get("payee_vpa", "")),
            payee_label=str(body.get("payee_label", "")),
            per_bill_cap_inr=int(body.get("per_bill_cap_inr", 0)),
            total_cap_inr=int(body.get("total_cap_inr", 0)),
            hours=int(body.get("hours", 48)),
            granted_by=str(body.get("granted_by", "")),
        )
    except MandateRejected as rejected:
        raise HTTPException(status_code=400, detail=str(rejected)) from None
    except (TypeError, ValueError) as bad:
        raise HTTPException(status_code=400, detail=str(bad)) from None

    from anbu_care.payments import payee_ref

    return {
        "status": "granted",
        "mandate_id": mandate.mandate_id,
        # The destination never leaves the store, not even to the family who
        # typed it. A reference proves which one it is; it cannot be paid to.
        "payee_ref": payee_ref(mandate.payee_vpa),
        "payee_label": mandate.payee_label,
        "per_bill_cap_inr": mandate.per_bill_cap_inr,
        "total_cap_inr": mandate.total_cap_inr,
        "window_closes_at": mandate.window_closes_at.isoformat(),
    }


@app.delete("/api/cases/{case_id}/payment-mandate")
def revoke_mandate(case_id: str,
                   _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """Stop automatic payment now. Everything after this escalates."""
    from anbu_care.payments import revoke

    mandate = revoke(case_id, revoked_by="family")
    if mandate is None:
        return {"status": "no_live_mandate"}
    return {"status": "revoked", "mandate_id": mandate.mandate_id}


@app.get("/api/cases/{case_id}/payments")
def case_payments(case_id: str,
                  _session: str = Depends(require_case_access)) -> dict[str, Any]:
    """What has been paid, what is merely initiated, and what authority remains."""
    from anbu_care.payments import escalations, money_view

    payments = service.list_payments(case_id)
    return {
        "case_id": case_id,
        "payments": [
            {"payment_id": p.payment_id, "bill_id": p.bill_id,
             "amount_inr": p.amount_inr, "payee_label": p.payee_label,
             "payee_ref": p.payee_ref, "autonomous": p.autonomous,
             "guards_passed": p.guards_passed,
             "initiated_at": p.initiated_at.isoformat(),
             "confirmed": p.confirmed_at is not None,
             "settlement_note": p.settlement_note}
            for p in payments
        ],
        # Refusals, reconciled against the chain. A bill refused and later
        # approved comes back resolved, so the dashboard never keeps asking
        # for an approval that already happened.
        "escalations": escalations(case_id),
        **money_view(case_id),
    }


@app.post("/api/cases/{case_id}/payments/{payment_id}/confirm")
def confirm_payment(case_id: str, payment_id: str,
                    _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """Record a settlement confirmation. Never called by the initiating path."""
    from anbu_care.payments import PaymentRefused, confirm

    try:
        return confirm(case_id=case_id, payment_id=payment_id)
    except PaymentRefused as refused:
        raise HTTPException(status_code=400, detail=str(refused)) from None


@app.post("/api/cases/{case_id}/bills/{bill_id}/consider")
def consider_bill_for_payment(case_id: str, bill_id: str,
                              _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """Put a bill already on file in front of the enforcer.

    The enforcer normally runs the moment a bill arrives. That leaves a hole:
    a mandate granted AFTER a bill was photographed never reconsiders it, and
    the bill sits there while cashless lapses. This is the same decision, asked
    for again.

    It triggers the enforcer; it does not make the decision, and it cannot
    influence it — no amount, no payee and no override crosses this boundary.
    The amount comes from the stored bill's balance due and the destination
    from the mandate, exactly as on the automatic path.
    """
    from anbu_care.bills import list_bills
    from anbu_care.payments import consider_bill

    bill = next((b for b in list_bills(case_id) if b.bill_id == bill_id), None)
    if bill is None:
        raise HTTPException(status_code=404, detail=f"no bill {bill_id} on case {case_id}")
    if not bill.balance_due_inr or bill.balance_due_inr <= 0:
        return {"outcome": "nothing_due",
                "detail": "this bill has no balance outstanding, so there is "
                          "nothing to pay"}

    case = service.load_case(case_id)
    return consider_bill(case_id=case_id, parent_id=case.parent_id,
                         bill_id=bill_id, amount_inr=bill.balance_due_inr,
                         extracted_payee=bill.vendor)


@app.post("/api/cases/{case_id}/payments/approve")
def approve_payment(case_id: str, body: dict[str, Any],
                    _session: str = Depends(require_family_session)) -> dict[str, Any]:
    """A human approving a bill the enforcer refused to pay automatically."""
    from anbu_care.payments import PaymentRefused, approve_escalated

    case = service.load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no case {case_id}")
    try:
        return approve_escalated(
            case_id=case_id, parent_id=case.parent_id,
            bill_id=str(body.get("bill_id", "")),
            amount_inr=int(body.get("amount_inr", 0)),
            approved_by=str(body.get("approved_by", "family")))
    except PaymentRefused as refused:
        raise HTTPException(status_code=400, detail=str(refused)) from None
    except (TypeError, ValueError) as bad:
        raise HTTPException(status_code=400, detail=str(bad)) from None


@app.get("/api/cases/{case_id}/verify")
def case_verify(case_id: str) -> dict[str, Any]:
    """Independently verify a case's chain.

    Deliberately unauthenticated in this build: the point of the receipt chain
    is that a family or an insurer can check it without trusting us to run the
    check for them.
    """
    return provenance_tools.verify_case_chain(case_id)


@app.post("/api/cases/{case_id}/handoff-link")
def mint_handoff_link(
    case_id: str, allow_notes: bool = False,
    _session: str = Depends(require_family_session)
) -> dict[str, Any]:
    """Mint an emergency-access link for the treating team.

    Family session required. The token delegates a subset of the caller's own
    access — it is not a second way in, which is why this endpoint sits behind
    the same credential as everything else that touches content.
    """
    from anbu_care.handoff import access

    try:
        token = access.mint(case_id, allow_notes=allow_notes)
    except access.HandoffDenied as denied:
        raise HTTPException(status_code=409, detail=str(denied)) from None

    path = f"/handoff/{token}"
    return {
        "status": "issued",
        "url": path,
        # Inline SVG rather than a data-URI PNG or a CDN script: it scales on
        # any screen a nurse points a camera at, adds no dependency to the
        # page, and survives a hospital network that blocks everything.
        "qr_svg": _qr_svg(path),
        "expires_in_seconds": access.HANDOFF_TTL_SECONDS,
        "grants": ("the emergency clinical summary for this case, plus leaving a note"
                   if allow_notes else
                   "the emergency clinical summary for this case, read only"),
        "may_write_note": allow_notes,
        "does_not_grant": ["/api/cases/{id}/trail", "/api/parents/{id}", "any other case"],
    }


@app.post("/api/cases/{case_id}/handoff-link/send")
def send_handoff_link(
    case_id: str, to_care_circle: bool = False,
    _session: str = Depends(require_family_session),
) -> dict[str, Any]:
    """Mint a handoff link and send it over WhatsApp.

    A QR on a dashboard assumes someone is holding a laptop next to the nurse.
    In the situation this exists for, the family is asleep eleven time zones
    away and the person at the hospital is whoever answered the phone. So the
    link goes where they already are.

    **Two consents, and they are not the same consent.** The parent must have
    agreed her record may be disclosed to a treating clinician
    (`emergency_clinical_share`, enforced when the link is minted). The
    recipient must separately have agreed to receive messages — a neighbour
    listed as a care-circle contact holds `outbound_notify`, and the family
    decision-maker holds `admission_alerts`. Neither implies the other, and
    neither implies the first.

    The message itself carries a link and an instruction and no clinical
    detail. The allergies live behind the link, because the comms gate would
    rightly refuse to carry them over WhatsApp — and a link is not the thing it
    points at.
    """
    from anbu_care.care_circle import notify as circle
    from anbu_care.handoff import access

    case = service.load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no case {case_id}")
    profile = service.load_profile(case.parent_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="no parent record on this case")

    try:
        token = access.mint(case_id)
    except access.HandoffDenied as denied:
        raise HTTPException(status_code=409, detail=str(denied)) from None

    base = os.getenv("ANBU_PUBLIC_BASE_URL", "").rstrip("/")
    url = f"{base}/handoff/{token}" if base else f"/handoff/{token}"
    params = {
        "parent_name": profile.name.split()[0] or profile.name,
        "handoff_url": url,
        "expires_minutes": str(access.HANDOFF_TTL_SECONDS // 60),
    }

    recipients = (circle.care_circle(case.parent_id) if to_care_circle
                  else [c for c in profile.family_contacts if c.is_primary]
                  or profile.family_contacts)
    purpose = (consent.OUTBOUND_NOTIFY if to_care_circle else consent.ADMISSION_ALERTS)

    results = []
    for contact in recipients:
        sent = whatsapp_tools.send_family_update(
            case_id=case_id, parent_id=case.parent_id, to_e164=contact.whatsapp_e164,
            template_name="clinician_handoff_link", template_params=params,
            message_class="logistics", purpose_override=purpose,
        )
        results.append({
            "name": contact.name, "to": contact.whatsapp_e164,
            "consented": sent.get("consented", sent.get("allowed")),
            "allowed": sent.get("allowed"),
            "delivered": bool(sent.get("delivered")),
            "detail": sent.get("detail") or sent.get("reason"),
        })

    return {
        "status": "sent" if any(r["delivered"] for r in results) else "not_delivered",
        "url": url,
        "expires_in_seconds": access.HANDOFF_TTL_SECONDS,
        "purpose_required": purpose,
        "recipients": results,
        "note": ("The message carries a link, never a finding. Delivery is "
                 "reported per recipient, and a permitted-but-undelivered "
                 "message is not a sent one."),
    }


@app.post("/api/cases/{case_id}/handoff-link/revoke")
def revoke_handoff_links(
    case_id: str, _session: str = Depends(require_family_session)
) -> dict[str, Any]:
    """Stop sharing. Every outstanding link for this case dies immediately."""
    from anbu_care.handoff import access

    try:
        access.revoke(case_id)
    except access.HandoffDenied as denied:
        raise HTTPException(status_code=404, detail=str(denied)) from None
    return {"status": "revoked", "note": "every outstanding link for this case is now dead"}


@app.get("/handoff/{token}", response_class=HTMLResponse)
def handoff_page(token: str) -> HTMLResponse:
    """The clinician's view. No login, because a nurse will not make one.

    Every refusal renders the same near-empty page: expired, revoked, forged
    and malformed are indistinguishable to the holder, and none of them leak
    whether the case exists or whose it is.
    """
    from anbu_care.handoff import access, summary as handoff_summary

    try:
        grant = access.resolve(token)
    except access.HandoffDenied as denied:
        return HTMLResponse(_handoff_denied_html(str(denied)), status_code=403)

    # The open is recorded BEFORE the content is returned. A read that the
    # family cannot see is the one thing this design must not allow, so the
    # receipt is not contingent on the render succeeding.
    access.record_access(grant)

    composed = handoff_summary.compose_emergency_summary(grant.parent_id)
    return HTMLResponse(_handoff_html(composed, grant))


class NoteConfirmRequest(BaseModel):
    text: str
    # Present only when the text came out of the transcriber. Without it the
    # note is still recorded — as typed, which is what it would be.
    ticket: str = ""
    recorded_by: str = ""


@app.post("/handoff/{token}/note/draft")
async def handoff_note_draft(token: str, request: Request) -> dict[str, Any]:
    """Transcribe a spoken note for review. Writes nothing.

    The response is a draft and a ticket. Until `confirm` is called with them,
    no receipt exists, no brief field moves, and the record is untouched.
    """
    from anbu_care.handoff import access, notes

    try:
        grant = access.resolve(token)
        audio = await request.body()
        draft = notes.draft_from_voice(
            grant, audio,
            mime_type=request.headers.get("content-type", "audio/ogg").split(";")[0],
        )
    except access.HandoffDenied as denied:
        raise HTTPException(status_code=403, detail=str(denied)) from None

    return {
        "status": "draft",
        "written": False,
        "text": draft.text,
        "ticket": draft.ticket,
        "engine": draft.engine,
        "next": "check the words, correct anything wrong, then POST .../note/confirm",
        "warning": "Nothing has been recorded yet. An unconfirmed transcript is discarded.",
    }


@app.post("/handoff/{token}/note/confirm")
def handoff_note_confirm(token: str, body: NoteConfirmRequest) -> dict[str, Any]:
    """Record a confirmed note. The only write a handoff link can perform."""
    from anbu_care.handoff import access, notes

    try:
        grant = access.resolve(token)
        return notes.confirm(grant, body.text, ticket=body.ticket,
                             recorded_by=body.recorded_by)
    except access.HandoffDenied as denied:
        raise HTTPException(status_code=403, detail=str(denied)) from None


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
    parent_id: str, _session: str = Depends(require_case_access)
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
def case_detail(case_id: str, _session: str = Depends(require_case_access)) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# The clinician handoff page
#
# Deliberately plain HTML with inline styles and no script. It is opened on an
# unknown device, on hospital wifi, by someone who has seconds — so it must
# render with no fonts to fetch, no JS to run and nothing to fail. Allergies
# are first and largest because that is the field that kills people when it is
# missed.
# ---------------------------------------------------------------------------

_HANDOFF_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     background:#f4f6f8;color:#12212e;padding:18px}
main{max-width:640px;margin:0 auto}
.band{background:#fff;border:1px solid #dbe3ea;border-radius:12px;padding:16px;margin-bottom:12px}
.allergy{border:2px solid #b3261e;background:#fff5f4}
.allergy h2{color:#b3261e;font-size:12px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px}
.allergy .v{font-size:30px;font-weight:800;line-height:1.15;color:#8c1d18}
.allergy .none{font-size:17px;font-weight:700;color:#8c1d18;line-height:1.45}
h2{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#5c6f7e;margin-bottom:10px}
.row{display:flex;justify-content:space-between;gap:14px;padding:7px 0;
     border-bottom:1px solid #eef2f5}
.row:last-child{border-bottom:0}
.row .k{color:#5c6f7e}
.row .v{font-weight:700;text-align:right}
li{list-style:none;padding:6px 0;border-bottom:1px solid #eef2f5;font-weight:700}
li:last-child{border-bottom:0}
.miss{color:#6b7c8a;font-style:italic;font-weight:400}
.foot{font-size:13px;color:#5c6f7e;line-height:1.55}
.tag{display:inline-block;background:#eef2f5;color:#48606f;border-radius:99px;
     padding:4px 10px;font-size:11px;font-weight:700;letter-spacing:.05em;margin-bottom:10px}
"""


def _esc(text: object) -> str:
    return escape(str(text if text is not None else ""))


def _handoff_denied_html(reason: str) -> str:
    """Every refusal looks the same and shows nothing.

    Expired, revoked, forged and malformed are one page. A holder learns that
    it did not work, and not whether the case exists, whose it is, or how close
    they were.
    """
    return (
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Link not available</title><style>{_HANDOFF_CSS}</style></head><body><main>"
        f"<div class=band><h2>Emergency clinical summary</h2>"
        f"<p style='font-size:18px;font-weight:700'>{_esc(reason)}.</p>"
        f"<p class=foot style='margin-top:10px'>Ask the family to send a new link. "
        f"Nothing about this patient is shown here.</p></div></main></body></html>"
    )


def _handoff_html(summary: Any, grant: Any) -> str:
    def facts(items: list[Any], bullet: bool = False) -> str:
        out = []
        for fact in items:
            if not fact.known:
                out.append(f"<div class=row><span class=k>{_esc(fact.label)}</span>"
                           f"<span class='v miss'>not on file</span></div>"
                           f"<p class=foot>{_esc(fact.source.note)}</p>")
            elif bullet:
                out.append(f"<li>{_esc(fact.value)}</li>")
            else:
                out.append(f"<div class=row><span class=k>{_esc(fact.label)}</span>"
                           f"<span class=v>{_esc(fact.value)}</span></div>")
        return "".join(out) or "<p class=foot>nothing on file</p>"

    allergies = summary.allergies
    if allergies and allergies[0].known:
        allergy_block = "".join(
            f"<div class=v>{_esc(f.value)}</div>" for f in allergies if f.known
        )
    else:
        note = allergies[0].source.note if allergies else "not on file"
        allergy_block = f"<div class=none>Not on file. {_esc(note)}</div>"

    return (
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Emergency clinical summary</title><style>{_HANDOFF_CSS}</style>"
        f"</head><body><main>"
        f"<span class=tag>READ ONLY &middot; NOT CONNECTED TO ANY HOSPITAL SYSTEM</span>"
        f"<div class='band allergy'><h2>Allergies</h2>{allergy_block}</div>"
        f"<div class=band><h2>Patient</h2>{facts(summary.identity)}</div>"
        f"<div class=band><h2>Conditions</h2><ul>{facts(summary.conditions, bullet=True)}</ul></div>"
        f"<div class=band><h2>Current medication</h2>{facts(summary.medications)}</div>"
        f"<div class=band><h2>Recent results</h2>{facts(summary.recent_labs)}</div>"
        f"<div class=band><p class=foot>{_esc(summary.disclaimer)}</p>"
        f"<p class=foot style='margin-top:8px'>The family has been shown that this "
        f"summary was opened. This link expires on its own.</p></div>"
        f"</main></body></html>"
    )


def _qr_svg(path: str) -> str:
    """A scannable QR for the handoff link, as inline SVG.

    Absolute URL where the public base is known, because a QR carrying a
    relative path scans to nothing. Error correction is set high: this gets
    photographed off a screen at an angle, in a corridor, by someone in a
    hurry.
    """
    import io

    import segno

    base = os.getenv("ANBU_PUBLIC_BASE_URL", "").rstrip("/")
    target = f"{base}{path}" if base else path

    buffer = io.BytesIO()
    segno.make(target, error="h").save(
        buffer, kind="svg", scale=5, border=2, dark="#12212e", light="#ffffff",
        svgclass=None, lineclass=None, xmldecl=False, svgns=True,
    )
    return buffer.getvalue().decode("utf-8")


def _handle_bill_photo(sender: Any, media: Any, background: BackgroundTasks) -> Response:
    """A photographed bill arriving over the same WhatsApp thread.

    Acknowledge now, read it after. Reading a bill takes about fifteen seconds
    and Twilio abandons a webhook at roughly the same mark — so doing the work
    inline is a coin flip, and it lost: the bill was ingested correctly and the
    family got silence, because Twilio had already given up on the reply.

    So the response returns immediately and the extraction runs after it, with
    the result sent as its own message. That also makes the two things honest
    about themselves: the acknowledgement says the bill arrived, and only the
    second message says what is in it.
    """
    case_id = _latest_open_case_for(sender.parent_id)
    if case_id is None:
        return _twiml(
            "Thanks. There is no open case to attach that bill to yet, so it "
            "has not been recorded. Send it again once a case is open."
        )

    background.add_task(_read_bill_and_report, case_id, sender.parent_id,
                        media.data, media.mime_type)
    return _twiml(
        "Got that. Reading it now — what it turned out to be, and anything it "
        "changes, will follow in a moment. Nothing is recorded until it has "
        "been read."
    )


def _read_bill_and_report(case_id: str, parent_id: str, image: bytes, mime_type: str) -> None:
    """Classify the photograph, route it, then tell the family. Never raises.

    Runs after the response, so an exception here reaches no caller. Every
    outcome therefore ends in a message, including the failures — a background
    task that dies quietly is indistinguishable from one that never ran.

    Classification costs a call, and a bill then costs a second one for its
    detailed line-item read. That is deliberate: merging both into one prompt
    would trade a clear router and a proven bill extractor for a single prompt
    doing two jobs, and this path has no latency budget left to protect now
    that it runs off the request.
    """
    from anbu_care.bills import BillRejected, estimate_for_case, ingest_bill_image, list_bills
    from anbu_care.docvision import DocumentRejected, ingest_document_image
    from anbu_care.docvision import read as docvision_read

    profile = service.load_profile(parent_id)
    first_name = (profile.name.split()[0] if profile and profile.name else "your parent")
    contact = next((c for c in (profile.family_contacts if profile else []) if c.is_primary),
                   None) or next(iter(profile.family_contacts if profile else []), None)
    if contact is None:
        logger.warning("document read for %s but no contact to tell", parent_id)
        return

    def tell(template: str, params: dict[str, str], klass: str, purpose: str):
        try:
            return whatsapp_tools.send_family_update(
                case_id=case_id, parent_id=parent_id, to_e164=contact.whatsapp_e164,
                template_name=template, template_params=params,
                message_class=klass, purpose_override=purpose,
            )
        except Exception:  # noqa: BLE001 - a failed telling must not hide the outcome
            logger.exception("could not report the document outcome")
            return None

    def unreadable(subject: str, reason: str) -> None:
        """Could not be read: there is something for the sender to do."""
        tell("document_unreadable", {"subject": subject, "reason": reason[:200]},
             "logistics", consent.STATUS_UPDATES)

    def already(template: str, subject: str) -> None:
        """Already on file. Nothing to do, and it must not read like a failure."""
        params = {"parent_name": first_name}
        if subject:
            params["subject"] = subject
        tell(template, params, "logistics", consent.STATUS_UPDATES)

    reading = docvision_read.read(image, mime_type)
    if not reading.ok and reading.kind != "bill":
        unreadable("document", f"{reading.detail}.")
        return

    # ---- not a bill: a document for the record -----------------------------
    if not reading.is_bill:
        try:
            result = ingest_document_image(parent_id, image, mime_type, case_id=case_id)
        except DocumentRejected as rejected:
            # A duplicate is not a failure. It was read, it was recognised, and
            # it was deliberately not recorded twice — which is the behaviour
            # this check exists for, so say that rather than asking for a
            # clearer photograph of something already on file.
            if rejected.already_recorded:
                already("document_already_recorded", rejected.subject)
            else:
                unreadable(rejected.subject, str(rejected))
            return
        except Exception:  # noqa: BLE001 - never die silently
            logger.exception("document ingestion failed")
            unreadable("document", "something went wrong reading it.")
            return

        sent = tell("document_recorded", {
            "parent_name": first_name,
            "document_kind": result["kind"].replace("_", " "),
            "summary": result["message_summary"][:300],
            "applied_line": (f"{result['applied']}.\n" if result.get("applied") else ""),
        }, "logistics", consent.STATUS_UPDATES)

        # If the gate still refuses it, the family must not be left in silence.
        # Same fallback the wellbeing lane uses for a withheld quote: say that
        # something was recorded and where to read it, carrying nothing that
        # could have been the reason for the block.
        if sent is not None and sent.get("allowed") is False:
            tell("document_recorded_withheld", {
                "parent_name": first_name,
                "document_kind": result["kind"].replace("_", " "),
            }, "logistics", consent.STATUS_UPDATES)
        return

    # ---- a bill: the existing lane, which reads line items in detail -------
    try:
        bill = ingest_bill_image(case_id, parent_id, image, mime_type)
    except BillRejected as rejected:
        if rejected.already_recorded:
            already("bill_already_recorded", "")
        else:
            tell("bill_unreadable", {"reason": str(rejected)[:200]},
                 "logistics", consent.STATUS_UPDATES)
        return
    except Exception:  # noqa: BLE001 - never die silently
        logger.exception("bill ingestion failed")
        tell("bill_unreadable", {"reason": "something went wrong reading it."},
             "logistics", consent.STATUS_UPDATES)
        return

    bills = list_bills(case_id)
    estimate = estimate_for_case(case_id, bills)

    running = ""
    if len(bills) > 1:
        running = (f"Across {len(bills)} bills on this stay: "
                   f"INR {estimate.total_billed_inr:,} billed.\n")

    # Quote what the bill says it comes to, not what the line items add up to.
    # An Indian bill prints a sub-total, then a discount, then GST, then a
    # TOTAL — and the first real bill through here had a 12,000 discount, so
    # the family was told a figure 12,000 higher than their own bill.
    adjustment = ""
    parts = []
    if bill.discount_inr:
        parts.append(f"a discount of INR {bill.discount_inr:,}")
    if bill.tax_inr:
        parts.append(f"GST of INR {bill.tax_inr:,}")
    if parts and bill.subtotal_inr:
        adjustment = (f"That is INR {bill.subtotal_inr:,} of charges, with "
                      f"{' and '.join(parts)}.\n")

    # The payment decision runs FIRST so its outcome rides in the same message.
    # Two arriving a second apart, both starting "Anbu Care:", both linking the
    # same tab, both about one photograph, read as duplication — and carried
    # five different figures about one piece of paper between them.
    payment_line = _consider_payment(case_id, parent_id, bill)

    tell("bill_recorded", {
        "parent_name": first_name,
        "line_count": str(len(bill.line_items)),
        "this_bill": f"{bill.payable_total_inr:,}",
        "adjustment_line": adjustment,
        "running_total_line": running,
        "estimated_covered": f"{estimate.estimated_covered_inr:,}",
        "estimated_you_pay": f"{estimate.estimated_you_pay_inr:,}",
        "payment_line": payment_line,
    }, "billing", consent.BILLING_UPDATES)


def _consider_payment(case_id: str, parent_id: str, bill) -> str:
    """Hand a payable bill to the enforcer, and return what to tell the family.

    Returns a paragraph for the bill message rather than sending one of its
    own. The enforcer decides; this only reports. The vendor rides along NOT so
    it can be paid to — nothing here can choose a destination — but so the
    enforcer can treat a bill from another hospital, or one naming a different
    payee, as evidence that this is not the bill the authority was for.
    """
    from anbu_care.payments import consider_bill, money_view

    payable = bill.balance_due_inr
    if not payable or payable <= 0:
        return ""   # nothing outstanding on this bill; nothing to pay

    try:
        outcome = consider_bill(
            case_id=case_id, parent_id=parent_id, bill_id=bill.bill_id,
            amount_inr=payable, extracted_payee=bill.vendor,
            extracted_vendor=bill.vendor)
    except Exception:  # noqa: BLE001 - a payment decision must not kill the lane
        logger.exception("payment decision failed")
        return ""

    if outcome["outcome"] == "escalated":
        # No mandate at all is the ordinary state, not an incident. Telling a
        # family "we did not automatically pay" when they never asked us to
        # would be noise on top of an already frightening day.
        if outcome["failed_check"] == "mandate_present" and \
                "no payment mandate" in outcome["reason"]:
            return ""
        return (f"\nINR {outcome['amount_inr']:,} of that is outstanding now, and "
                f"it was NOT paid automatically: {outcome['reason'][:180]}. "
                f"Nothing has moved, and it needs you.\n")

    view = money_view(case_id)
    running = ""
    if view["payment_count"] > 1:
        running = (f"Across this stay: INR {view['paid_inr']:,} settled and INR "
                   f"{view['initiated_unconfirmed_inr']:,} initiated but not yet "
                   f"confirmed.\n")
    return (f"\nINR {outcome['amount_inr']:,} of that was outstanding and has "
            f"been paid automatically, inside the limits you set: checked "
            f"against your per-bill cap, your total cap, the window, and the "
            f"one account you authorised.\n"
            f"{running}"
            f"That is what the hospital wanted now. A paid interim amount is "
            f"normally adjusted when the insurer settles.\n")


def _payee_label(case_id: str) -> str:
    from anbu_care.payments import live_for_case

    mandate = live_for_case(case_id)
    return mandate.payee_label if mandate else "the hospital"


def _latest_open_case_for(parent_id: str) -> str | None:
    """The case a bill belongs to, or None. Never a guess."""
    case = service.latest_case_for_parent(parent_id)
    return case.case_id if case else None


def _twiml(message: str) -> Response:
    return Response(
        content=('<?xml version="1.0" encoding="UTF-8"?>'
                 f"<Response><Message>{escape(message)}</Message></Response>"),
        media_type="application/xml",
    )
