"""End-to-end run of the Anbu Care spine, with no model in the loop.

Everything here is the deterministic layer: severity classification, hospital
routing, the compliance gate, packet assembly, SLA tracking, and the signed
receipt chain. If this script passes, the parts the demo depends on work
regardless of what any LLM does on the day.

    uv run python scripts/demo_spine.py
"""

from __future__ import annotations

import os
import sys

# The spine runs standalone: no Firestore, no Pub/Sub, no model. Set before
# anything imports config, and set outright rather than defaulted, so a .env
# pointing at a real project cannot turn this into a live run.
os.environ["ANBU_STORE_BACKEND"] = "memory"
os.environ["ANBU_PUBSUB_ENABLED"] = "false"

from anbu_care import service
from anbu_care.tools import (
    evidence_tools,
    insurer_tools,
    onboarding_tools,
    provenance_tools,
    triage_tools,
    whatsapp_tools,
)

BAR = "─" * 78


def step(n: int, title: str) -> None:
    print(f"\n{BAR}\n{n}. {title}\n{BAR}")


def main() -> int:
    step(1, "Onboarding — baseline record, insurance, and family consent")
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
    print(f"   parent_id: {parent_id}")

    onboarding_tools.record_medications(parent_id, [
        {"name": "Telmisartan", "dose": "40 mg", "frequency": "once daily"},
        {"name": "Atorvastatin", "dose": "20 mg", "frequency": "at night"},
        {"name": "Metformin", "dose": "500 mg", "frequency": "twice daily"},
    ])
    policy = onboarding_tools.record_insurance_policy(
        parent_id,
        insurer="Star Health",
        policy_number="SH-NRI-4471902",
        sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital", "Sundaram Arulrhaj Hospitals"],
        cashless_eligible=True,
    )
    print(f"   policy:    {policy['policy']['insurer']} / sum insured INR {policy['policy']['sum_insured_inr']:,}")

    contact = onboarding_tools.record_family_contact(
        parent_id,
        # Same override the seeded demo uses. `or` rather than a getenv default,
        # because a set-but-empty variable would otherwise name nobody.
        name=os.getenv("ANBU_DEMO_FAMILY_NAME") or "Heartlin Machado",
        relationship="son",
        whatsapp_e164=os.getenv("ANBU_DEMO_FAMILY_E164") or "+14155550142",
        timezone_name="America/Los_Angeles",
        is_primary=True,
        consent_purposes=["admission_alerts", "status_updates", "billing_updates", "claim_updates"],
    )
    print(f"   family:    {contact['contact']['name']} ({contact['contact']['relationship']}), "
          f"{len(contact['contact']['consents'])} purpose-specific consents")

    step(2, "Document ingestion — baseline, then a follow-up that must read as new")
    first = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="lipid_panel_mar2026.pdf",
        summary="Routine lipid panel, March 2026.",
        observations=[
            {"name": "LDL", "value": "165", "unit": "mg/dL", "reference_range": "<100", "flag": "high", "observed_on": "2026-03-14"},
            {"name": "HDL", "value": "38", "unit": "mg/dL", "reference_range": ">40", "flag": "low", "observed_on": "2026-03-14"},
        ],
    )
    print(f"   {first['delta_vs_baseline']}")

    second = onboarding_tools.ingest_document(
        parent_id, kind="blood_report", source_filename="lipid_panel_aug2026.pdf",
        summary="Repeat lipid panel, August 2026.",
        observations=[
            {"name": "LDL", "value": "165", "unit": "mg/dL", "reference_range": "<100", "flag": "high", "observed_on": "2026-08-02"},
            {"name": "HbA1c", "value": "8.4", "unit": "%", "reference_range": "<7.0", "flag": "high", "observed_on": "2026-08-02"},
        ],
    )
    print(f"   {second['delta_vs_baseline']}")

    step(3, "Triage — the explainable routing decision")
    case = service.open_case(parent_id)
    triage = triage_tools.run_triage(
        parent_id=parent_id,
        symptoms=["chest pain", "sweating"],
        free_text="Neighbour called: she has had chest tightness for about 20 minutes, radiating to her left arm, and is sweating.",
        reported_by="neighbour",
        lat=0.0, lon=0.0,
        case_id=case.case_id,
    )
    print(f"   severity:  {triage['severity']}  specialties: {triage['matched_specialties']}")
    for line in triage["severity_rationale"]:
        print(f"     · {line}")
    print(f"\n   {triage['explanation']}\n")
    print("   ranked:")
    for h in triage["ranked_hospitals"]:
        flag = "◀ chosen" if h["hospital_id"] == triage["recommended_hospital"]["hospital_id"] else ""
        print(f"     {h['total_score']:.3f}  {h['name']:<44} {h['distance_km']:>5.1f} km  "
              f"cap {h['capability_score']:.2f}  network {'yes' if h['network_match'] else 'no ':<3} {flag}")
    kb = triage["knowledge_base"]
    print(f"\n   KB provenance — locations: {kb['location_status']} "
          f"({kb['location_verified_on']})")
    print(f"                  capability: {kb['capability_status']} "
          f"(seeded {kb['seeded_on']})")

    step(4, "WhatsApp — what may go out, and what must not")
    hospital = triage["recommended_hospital"]
    allowed = whatsapp_tools.send_family_update(
        case_id=case.case_id, parent_id=parent_id, to_e164="+14155550142",
        template_name="admission_alert",
        template_params={
            "parent_name": "Amma",
            "hospital_name": hospital["name"],
            "hospital_area": "Thoothukudi",
            "reason_short": "chest pain, being assessed",
        },
        message_class="logistics",
    )
    print(f"   ALLOWED  {allowed['message']['body']}")

    blocked = whatsapp_tools.check_message_allowed(
        body="ECG shows ST elevation in leads II, III, aVF. Troponin I is 0.94 ng/mL.",
        message_class="status",
    )
    print(f"   BLOCKED  {blocked['reason']}")

    step(5, "Claim packet — assembly, coverage check, STEP_UP gate")
    packet_result = insurer_tools.assemble_claim_packet(
        case_id=case.case_id,
        parent_id=parent_id,
        admission_summary="Admitted 19 Aug 2026 with acute chest pain. Managed in cardiac ICU. Discharged 22 Aug 2026.",
        itemized_bills_inr={"cardiac_icu_room": 96_000, "procedures": 210_000, "pharmacy": 34_500, "diagnostics": 18_000},
        diagnostics=[],
        attached_document_ids=[],
    )
    packet_id = packet_result["packet"]["packet_id"]
    print(f"   packet:    {packet_id}  total INR {packet_result['packet']['total_claimed_inr']:,}")
    coverage = packet_result["coverage_check"]
    print(f"   coverage:  {coverage['insurer']}, sum insured INR {coverage['sum_insured_inr']:,}")
    for warning in coverage["warnings"]:
        print(f"     ! {warning}")

    assessment = evidence_tools.assess_claim_packet(case.case_id, packet_id)["assessment"]
    print(f"\n   gate:      {assessment['gate']} at confidence {assessment['confidence']}")
    print(f"   {assessment['rationale']}")
    for action in assessment["enrichment_actions"]:
        print(f"     → {action}")

    docs = service.list_documents(parent_id)
    enriched = evidence_tools.enrich_claim_packet(
        case_id=case.case_id, packet_id=packet_id,
        matched_policy_clauses=[
            "Sec 3.1 Hospitalisation — inpatient care exceeding 24h is covered",
            "Sec 3.4 ICU charges — payable up to 2% of sum insured per day",
            "Sec 5.2 Pre-hospitalisation diagnostics — 30 days prior, covered",
        ],
        additional_document_ids=[d.document_id for d in docs],
        diagnostics=["ECG", "Troponin I", "2D Echo", "Coronary angiogram"],
    )
    print(f"\n   enriched:  confidence {enriched['confidence_before']} → {enriched['confidence_after']}")
    regate = evidence_tools.assess_claim_packet(case.case_id, packet_id)["assessment"]
    print(f"   gate:      {regate['gate']} — {regate['rationale']}")

    step(6, "Submission — real SLA clock, simulated counterparty")
    submitted = insurer_tools.submit_claim(case.case_id, packet_id, sla_kind="cashless_preauth")
    print(f"   {submitted['notice']}")
    print(f"   ref:       {submitted['counterparty_ack']['tpa_reference']}")
    sla = submitted["sla"]
    print(f"   SLA:       {sla['kind']}, deadline {sla['deadline']}, "
          f"{sla['seconds_remaining'] // 60} min remaining, breached={sla['breached']}")

    insurer_tools.advance_claim_stage(case.case_id, submitted["submission"]["submission_id"], "under_review")
    insurer_tools.advance_claim_stage(case.case_id, submitted["submission"]["submission_id"], "approved")
    print("   stages:    submitted → under_review → approved  (all simulated responses)")

    step(7, "Provenance — reconstruct the trail, then prove tampering breaks it")
    trail = provenance_tools.get_case_trail(case.case_id)
    for r in trail["receipts"]:
        print(f"   [{r['seq']:>2}] {r['kind']:<26} by {r['actor']:<22} {r['prev_hash']} → {r['hash']}")

    verified = provenance_tools.verify_case_chain(case.case_id)
    print(f"\n   verified:  {verified['verified']} over {verified['receipt_count']} receipts")
    if verified["key_warning"]:
        print(f"   warning:   {verified['key_warning']}")

    print("\n   Now silently altering receipt 1's payload, as a dispute would allege:")
    chain = service.get_chain(case.case_id)
    chain.receipts[1].payload["severity"] = "LOW"
    result = chain.verify()
    print(f"   re-verify: ok={result.ok} broken_at={result.broken_at}")
    print(f"              {result.reason}")

    print(f"\n{BAR}")
    print("Spine complete. Real: parsing, routing, packet assembly, SLA clocks, receipts.")
    print("Simulated and labelled: the TPA response, and the seeded hospital KB.")
    print(BAR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
