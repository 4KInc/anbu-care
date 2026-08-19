"""Domain models. These are the contract between agents — every tool takes and
returns one of these shapes so agents stay swappable."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Onboarding / knowledge base
# --------------------------------------------------------------------------


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class DocumentKind(str, Enum):
    BLOOD_REPORT = "blood_report"
    ECG = "ecg"
    DISCHARGE_SUMMARY = "discharge_summary"
    PRESCRIPTION = "prescription"
    BILL = "bill"
    POLICY = "policy"
    OTHER = "other"


class Medication(BaseModel):
    name: str
    dose: str | None = None
    frequency: str | None = None
    started: str | None = None


class InsurancePolicy(BaseModel):
    insurer: str
    policy_number: str
    sum_insured_inr: int
    cashless_eligible: bool = True
    network_hospitals: list[str] = Field(default_factory=list)
    sub_limits_inr: dict[str, int] = Field(default_factory=dict)
    valid_until: str | None = None


class ParentProfile(BaseModel):
    """The baseline record. Every later observation is judged against this."""

    parent_id: str
    name: str
    age: int
    city: str
    lat: float
    lon: float
    chronic_conditions: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    medications: list[Medication] = Field(default_factory=list)
    policy: InsurancePolicy | None = None
    family_contacts: list[FamilyContact] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class FamilyContact(BaseModel):
    name: str
    relationship: str
    whatsapp_e164: str
    timezone: str = "UTC"
    is_primary: bool = False
    # DPDP requires purpose-specific, timestamped consent. A blanket checkbox
    # is not sufficient, so consent is recorded per purpose.
    consents: dict[str, datetime] = Field(default_factory=dict)


class Observation(BaseModel):
    """One structured fact extracted from an uploaded document."""

    name: str
    value: str
    unit: str | None = None
    reference_range: str | None = None
    flag: str | None = None  # "high" | "low" | "normal" | "abnormal"
    observed_on: str | None = None


class ParsedDocument(BaseModel):
    document_id: str
    parent_id: str
    kind: DocumentKind
    source_filename: str | None = None
    observations: list[Observation] = Field(default_factory=list)
    summary: str = ""
    parsed_at: datetime = Field(default_factory=utcnow)
    # Set by the KB agent when compared against the baseline record.
    delta_vs_baseline: str | None = None


# --------------------------------------------------------------------------
# Triage
# --------------------------------------------------------------------------


class Hospital(BaseModel):
    hospital_id: str
    name: str
    city: str
    lat: float
    lon: float
    specialties: list[str] = Field(default_factory=list)
    has_emergency: bool = True
    cardiac_icu: bool = False
    stroke_unit: bool = False
    open_24x7: bool = True
    # Which insurers this hospital is empanelled with. Empanelment varies
    # hospital by hospital and changes — treat the seeded values as a snapshot.
    empanelled_insurers: list[str] = Field(default_factory=list)
    source_note: str | None = None


class HospitalScore(BaseModel):
    hospital: Hospital
    distance_km: float
    capability_score: float
    network_match: bool
    total_score: float
    reasons: list[str] = Field(default_factory=list)


class SymptomReport(BaseModel):
    parent_id: str
    reported_by: str
    symptoms: list[str]
    free_text: str = ""
    lat: float | None = None
    lon: float | None = None
    reported_at: datetime = Field(default_factory=utcnow)


class TriageDecision(BaseModel):
    """The differentiating artefact — a routing decision that can explain itself."""

    case_id: str
    parent_id: str
    severity: Severity
    severity_rationale: list[str]
    matched_specialties: list[str]
    ranked_hospitals: list[HospitalScore]
    recommended_hospital_id: str | None
    explanation: str
    decided_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# Evidence / STEP_UP
# --------------------------------------------------------------------------


class EvidenceGate(str, Enum):
    PASS = "PASS"
    STEP_UP = "STEP_UP"
    BLOCK = "BLOCK"


class ClaimPacket(BaseModel):
    packet_id: str
    case_id: str
    parent_id: str
    policy_number: str | None = None
    admission_summary: str = ""
    itemized_bills_inr: dict[str, int] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)
    attached_document_ids: list[str] = Field(default_factory=list)
    matched_policy_clauses: list[str] = Field(default_factory=list)
    total_claimed_inr: int = 0
    assembled_at: datetime = Field(default_factory=utcnow)


class EvidenceAssessment(BaseModel):
    """Confidence-gated pre-submission enrichment.

    This runs on the packet Anbu Care submits. It is NOT inside the insurer's
    adjudication loop and does not pre-empt a denial — its only job is to raise
    first-pass approval odds before submission.
    """

    packet_id: str
    confidence: float
    gate: EvidenceGate
    missing_fields: list[str] = Field(default_factory=list)
    enrichment_actions: list[str] = Field(default_factory=list)
    rationale: str = ""
    assessed_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# Insurer liaison
# --------------------------------------------------------------------------


class ClaimStage(str, Enum):
    ASSEMBLED = "assembled"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    DENIED = "denied"
    PAID = "paid"


class ClaimSubmission(BaseModel):
    submission_id: str
    packet_id: str
    case_id: str
    stage: ClaimStage
    # Cashless pre-auth must clear in 1 hour; reimbursement runs a 30-day clock
    # under the IRDAI 2024 Master Circular. We track both against real wall time.
    sla_kind: str = "cashless_preauth"
    sla_deadline: datetime | None = None
    simulated: bool = True
    counterparty_note: str = "SIMULATED TPA — no production insurer API integrated."
    submitted_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# Communications
# --------------------------------------------------------------------------


class MessageClass(str, Enum):
    """Meta healthcare policy + DPDP draw a hard line through what may leave
    the platform over WhatsApp."""

    LOGISTICS = "logistics"       # admission, hospital name/location, doctor names
    STATUS = "status"             # "Admitted at X, 4:12 PM"
    BILLING = "billing"           # cost summaries, payment links
    CLINICAL = "clinical"         # diagnosis, lab values, prescription specifics


class OutboundMessage(BaseModel):
    message_id: str
    case_id: str
    to_e164: str
    message_class: MessageClass
    template_name: str | None = None
    body: str
    allowed: bool
    block_reason: str | None = None
    sent_at: datetime | None = None


# --------------------------------------------------------------------------
# Case
# --------------------------------------------------------------------------


class Case(BaseModel):
    case_id: str
    parent_id: str
    opened_at: datetime = Field(default_factory=utcnow)
    closed_at: datetime | None = None
    stage: str = "intake"
    triage_decision_id: str | None = None
    packet_id: str | None = None
    submission_id: str | None = None


ParentProfile.model_rebuild()
