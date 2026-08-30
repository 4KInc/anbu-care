"""Tool-layer operations the demo driver needs that the public HTTP API does not expose.

These run locally against the SAME Firestore the deployed service uses, so every
receipt they write is visible through the deployed /api/cases/{id}/verify.

Kept out of anbu_care/ deliberately: this is demo scaffolding, not application
logic. Nothing here is imported by the service.

    uv run python scripts/demo_support.py <command> [args]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

STATE = Path(".anbu/demo_state.json")


def _load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"cases": [], "parents": []}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def track(case_id: str = "", parent_id: str = "") -> None:
    """Record what this run created so --reset can remove exactly that."""
    state = _load_state()
    if case_id and case_id not in state["cases"]:
        state["cases"].append(case_id)
    if parent_id and parent_id not in state["parents"]:
        state["parents"].append(parent_id)
    _save_state(state)


def block_receipt(case_id: str, parent_id: str, to_e164: str) -> None:
    """Attempt a send that smuggles clinical detail through a logistics-shaped call.

    The agent already refuses this at the pre-check. This bypasses the agent
    entirely and calls the send tool directly — which is the point: the boundary
    has to hold when the model is not the thing enforcing it.
    """
    from anbu_care.tools import whatsapp_tools

    result = whatsapp_tools.send_family_update(
        case_id=case_id,
        parent_id=parent_id,
        to_e164=to_e164,
        template_name="status_update",
        template_params={
            "parent_name": "Amma",
            # Disguised as a status string; carries a lab value and a measurement.
            "status": "stable after review — ECG shows ST elevation, troponin I 0.94 ng/mL",
            "hospital_name": "Sacred Heart Hospital",
            "timestamp": "4:12 PM",
        },
        message_class="logistics",
    )
    print(json.dumps({
        "allowed": result["allowed"],
        "status": result["status"],
        "reason": result["reason"],
        "receipt_id": result.get("receipt_id"),
    }, indent=2))


def tamper(case_id: str) -> None:
    """Silently rewrite a stored receipt, as a disputed record would allege.

    Writes straight to Firestore under the same key, leaving hash and signature
    untouched — exactly what an after-the-fact edit looks like.
    """
    from anbu_care.provenance.store import get_store, receipt_sk
    from anbu_care.service import get_chain

    chain = get_chain(case_id)
    target = next((r for r in chain.receipts if r.kind == "triage.decision"), None)
    if target is None:
        print(f"no triage.decision receipt on {case_id}", file=sys.stderr)
        raise SystemExit(1)

    row = target.model_dump(mode="json")
    before = row["payload"]["severity"]
    row["payload"]["severity"] = "LOW"
    row["payload"]["explanation"] = "Severity LOW. Recommending Arputham Hospital (0.9 km)."
    get_store().put(f"CASE#{case_id}", receipt_sk(target.seq), row)

    print(json.dumps({
        "tampered_case": case_id,
        "receipt_seq": target.seq,
        "field": "payload.severity",
        "before": before,
        "after": "LOW",
        "note": "hash and signature left untouched, as a silent edit would be",
    }, indent=2))


def reload_verify(case_id: str) -> None:
    """Reload from Firestore in this fresh process and verify — not process memory."""
    from anbu_care.provenance.store import load_receipts
    from anbu_care.service import get_chain

    receipts = load_receipts(case_id)
    result = get_chain(case_id).verify()
    print(json.dumps({
        "case_id": case_id,
        "receipts_read_from_firestore": len(receipts),
        "kinds": [r.kind for r in sorted(receipts, key=lambda x: x.seq)],
        "verified": result.ok,
        "broken_at_seq": result.broken_at,
        "failure_mode": result.reason,
    }, indent=2))


def ingest_doc(url: str, parent_id: str, image_path: str, session_id: str = "") -> None:
    """Send a synthetic document to the DEPLOYED agent and report what it did.

    Prints the extracted observations, the tool's own status, and then the
    stored-document count read back from the service — the ground truth. If the
    agent ever claims an ingest it did not perform, the count contradicts it on
    screen.
    """
    import base64
    import subprocess
    import tempfile

    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()

    def post(path: str, payload: dict):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(payload, fh)
            body = fh.name
        out = subprocess.run(
            ["curl", "-s", "--max-time", "300", "-X", "POST", f"{url}{path}",
             "-H", "content-type: application/json", "--data-binary", f"@{body}"],
            capture_output=True, text=True, check=False).stdout
        try:
            return json.loads(out)
        except Exception:  # noqa: BLE001 - any malformed reply means "no events"
            print(f"    (agent call failed: {out[:120]})")
            return None

    if not session_id:
        created = post("/apps/anbu_care/users/demo/sessions", {})
        session_id = created["id"] if created else ""

    events = post("/run", {
        "app_name": "anbu_care", "user_id": "demo", "session_id": session_id,
        "new_message": {"role": "user", "parts": [
            {"text": f"Attached is a lab report for parent_id {parent_id}. "
                     f"Read it and ingest it into her record."},
            {"inlineData": {"mimeType": "image/png", "data": b64}},
        ]},
    })

    reported = None
    for event in events or []:
        for part in (event.get("content") or {}).get("parts") or []:
            call = part.get("functionCall")
            if call and call["name"] == "ingest_document":
                observations = call["args"].get("observations", [])
                print("    extracted by Gemini vision:")
                for o in observations:
                    unit = f" {o.get('unit')}" if o.get("unit") else ""
                    flag = f"  [{o.get('flag')}]" if o.get("flag") else ""
                    print(f"      {o.get('name')}: {o.get('value')}{unit}{flag}")
            response = part.get("functionResponse")
            if response and response["name"] == "ingest_document":
                body = response["response"]
                reported = body.get("status")
                print(f"    tool status: {reported}")
                if body.get("delta_vs_baseline"):
                    print("    living-record delta:")
                    for note in str(body["delta_vs_baseline"]).split("; "):
                        print(f"      · {note}")

    docs_count(url, parent_id, reported or "<no ingest_document call>")


DEMO_TOKEN = os.getenv("ANBU_DEMO_TOKEN", "anbu-demo-family-token")


def docs_count(url: str, parent_id: str, claimed: str = "") -> None:
    """Read the stored-document count back from the service — ground truth.

    The record is credentialed, so this presents the published demo token. The
    ground-truth check is only meaningful if it reads the *same* protected
    endpoint the dashboard reads, rather than some unguarded side door.
    """
    import subprocess

    out = subprocess.run(
        ["curl", "-s", "-H", f"Authorization: Bearer {DEMO_TOKEN}",
         f"{url}/api/parents/{parent_id}"],
        capture_output=True, text=True, check=False).stdout
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        print(f"    GROUND TRUTH. Could not read the record back: {out[:100]}")
        return
    if "documents" not in payload:
        print(f"    GROUND TRUTH. Record refused the read: {payload.get('detail', payload)}")
        return
    stored = len(payload["documents"])
    print(f"    GROUND TRUTH. Documents actually stored for this parent: {stored}")
    if claimed:
        agrees = (claimed == "ingested" and stored > 0) or (claimed != "ingested" and stored == 0)
        verdict = "consistent" if agrees else "CONTRADICTED — the agent claimed more than it did"
        print(f"    reported status '{claimed}' vs stored count {stored}: {verdict}")


def claim_flow(case_id: str, parent_id: str) -> None:
    """Submit a claim, get queried, resolve it, get priced — against the deployed store.

    Runs at the tool layer so every take is byte-identical. The insurer-liaison
    agent makes exactly these calls itself when driven through /run; this is a
    deterministic replay of that path, not a re-implementation of it, and the
    narration must not claim an agent is reacting here when a script is.
    """
    from anbu_care.tools import insurer_tools, onboarding_tools

    packet = insurer_tools.assemble_claim_packet(
        case_id=case_id, parent_id=parent_id,
        admission_summary="Admitted 19 Aug 2026 with acute chest pain. Cardiac ICU. Discharged 22 Aug 2026.",
        itemized_bills_inr={"cardiac_icu_room": 96_000, "procedures": 210_000,
                            "pharmacy": 34_500, "diagnostics": 18_000},
        diagnostics=["ECG", "Troponin I", "2D Echo"],
        attached_document_ids=[],
        admitted_on="2026-08-19", discharged_on="2026-08-22",
    )
    packet_id = packet["packet"]["packet_id"]
    print(f"    packet {packet_id}: INR {packet['packet']['total_claimed_inr']:,} claimed")

    submitted = insurer_tools.submit_claim(case_id, packet_id, "cashless_preauth")
    adj = submitted["adjudication"]
    print(f"\n    [1] SUBMITTED -> {adj['outcome']}   ({submitted['adjudicator']})")
    for reason in adj["reasons"]:
        print(f"        · {reason}")
    print(f"        original SLA: {submitted['sla']['seconds_remaining'] // 60} min remaining")
    if submitted.get("query_response_deadline"):
        print("        query response clock started, both now running")

    print("\n    [2] Resolving the query — the document is located on the record and attached.")
    print("        (Replayed here at the tool layer so every take is identical. The")
    print("         insurer-liaison agent performs exactly these calls itself — see")
    print("         `make demo` for the full spine with no model in the loop.)")
    doc = onboarding_tools.ingest_document(
        parent_id, kind="discharge_summary", source_filename="discharge_22aug2026.pdf",
        summary="Admitted 19 Aug 2026, cardiac ICU, discharged 22 Aug 2026.",
        observations=[],
    )
    doc_id = doc["document"]["document_id"]
    print(f"        found and attached: {doc_id} (kind=discharge_summary)")

    responded = insurer_tools.respond_to_query(
        case_id, submitted["submission"]["submission_id"], [doc_id],
    )
    adj2 = responded["adjudication"]
    print(f"\n    [3] RESUBMITTED -> {adj2['outcome']}")
    for reason in adj2["reasons"]:
        print(f"        · {reason}")
    print(f"\n        claimed    INR {adj2['total_claimed_inr']:,}")
    print(f"        allowed    INR {adj2['total_allowed_inr']:,}")
    print(f"        DISALLOWED INR {adj2['total_disallowed_inr']:,}   <- family told now, not at settlement")


def adjudicator_branches(parent_id: str) -> None:
    """Exercise all four outcomes live, so completeness can be verified not claimed."""
    from anbu_care.schemas import ClaimPacket, DocumentKind, InsurancePolicy
    from anbu_care.tpa import adjudicate

    policy = InsurancePolicy(insurer="Star Health", policy_number="SH-NRI-4471902",
                             sum_insured_inr=500_000)
    def pkt(bills, **kw):
        return ClaimPacket(packet_id="branch", case_id="branch", parent_id=parent_id,
                           itemized_bills_inr=bills, total_claimed_inr=sum(bills.values()),
                           admitted_on=kw.get("admitted_on", "2026-08-19"),
                           discharged_on=kw.get("discharged_on", "2026-08-22"))

    D = {DocumentKind.DISCHARGE_SUMMARY}
    cases = [
        ("PASS",    pkt({"cardiac_icu_room": 25_000, "pharmacy": 4_000}), policy, D),
        ("PARTIAL", pkt({"cardiac_icu_room": 96_000, "procedures": 210_000}), policy, D),
        ("QUERY",   pkt({"cardiac_icu_room": 96_000}), policy, set()),
        ("DENY",    pkt({"toiletries": 900, "attendant_charges": 4_000}), policy, D),
    ]
    for label, packet, pol, kinds in cases:
        result = adjudicate(packet, pol, kinds)
        assert result.outcome.value == label, f"{label} branch returned {result.outcome.value}"
        print(f"    {result.outcome.value:<8} claimed INR {result.total_claimed_inr:>9,}  "
              f"allowed INR {result.total_allowed_inr:>9,}  disallowed INR {result.total_disallowed_inr:>9,}")
        print(f"             {result.reasons[0][:110]}")
    print("\n    All four branches reachable and self-consistent. Rules are deterministic;")
    print("    every result carries: " + adjudicate(cases[0][1], policy, D).adjudicator)


def reset() -> None:
    """Delete everything this driver created. Leaves no half-seeded state."""
    from anbu_care.provenance.store import get_store

    state = _load_state()
    store = get_store()
    removed = {"receipts": 0, "cases": 0, "packets": 0, "submissions": 0, "parents": 0, "documents": 0}

    for case_id in state["cases"]:
        pk = f"CASE#{case_id}"
        for prefix, key in (("RECEIPT#", "receipts"), ("PACKET#", "packets"), ("SUBMISSION#", "submissions")):
            for row in store.query_prefix(pk, prefix):
                store.delete(pk, row["sk"])
                removed[key] += 1
        if store.get(pk, "META"):
            store.delete(pk, "META")
            removed["cases"] += 1

    for parent_id in state["parents"]:
        pk = f"PARENT#{parent_id}"
        for row in store.query_prefix(pk, "DOC#"):
            store.delete(pk, row["sk"])
            removed["documents"] += 1
        if store.get(pk, "PROFILE"):
            store.delete(pk, "PROFILE")
            removed["parents"] += 1

    if STATE.exists():
        STATE.unlink()
    print(json.dumps({"reset": "complete", "removed": removed}, indent=2))


COMMANDS = {
    "track": track,
    "ingest-doc": ingest_doc,
    "claim-flow": claim_flow,
    "adjudicator-branches": adjudicator_branches,
    "docs-count": docs_count,
    "block-receipt": block_receipt,
    "tamper": tamper,
    "reload-verify": reload_verify,
    "reset": reset,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: demo_support.py [{' | '.join(COMMANDS)}] [args]", file=sys.stderr)
        return 2
    COMMANDS[argv[0]](*argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
