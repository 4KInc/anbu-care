"""Domain models. These are the contract between agents — every tool takes and
returns one of these shapes so agents stay swappable."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


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
    # The parent's own WhatsApp number, if they have one. Only used to label a
    # check-in as coming from the registered parent number.
    whatsapp_e164: str | None = None
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

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, value: object) -> object:
        """Accept a number where a reading is expected.

        A vision model reading "232" off a lab report emits the JSON number
        232, not the string "232" — they are the same reading, and rejecting
        one of them fails the whole ingest. Stored as text because plenty of
        real results are not numeric ("Positive", "<0.01", "Trace").
        """
        if isinstance(value, bool):          # bool is an int subclass; not a reading
            return value
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            # 8.4 must not become "8.400000000000001", and 165.0 must not
            # become "165.0" when the same reading arrived as 165 last time.
            return f"{value:g}"
        return value
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
    # Per-day sub-limits cannot be applied without these. Kept as explicit
    # fields rather than parsed out of admission_summary prose, because a
    # payable figure that depends on regex over free text is not a figure
    # anyone should defend.
    admitted_on: str | None = None
    discharged_on: str | None = None
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
    QUERIED = "queried"
    PARTIALLY_APPROVED = "partially_approved"
    APPROVED = "approved"
    DENIED = "denied"
    PAID = "paid"


class AdjudicationOutcome(str, Enum):
    """What the simulated TPA came back with.

    QUERY is evaluated before PARTIAL on purpose: a payable figure cannot be
    computed while a required document is missing, so a real adjudicator asks
    first and prices second.
    """

    PASS = "PASS"
    PARTIAL = "PARTIAL"
    QUERY = "QUERY"
    DENY = "DENY"


class LineAssessment(BaseModel):
    """One claimed line, priced against the policy."""

    item: str
    claimed_inr: int
    allowed_inr: int
    disallowed_inr: int
    rule: str


class Adjudication(BaseModel):
    """A simulated adjudication result.

    Everything here is produced by deterministic local rules. No insurer or TPA
    is contacted, and the payload says so in every response.
    """

    adjudication_id: str
    submission_id: str
    packet_id: str
    case_id: str
    outcome: AdjudicationOutcome
    reasons: list[str] = Field(default_factory=list)
    lines: list[LineAssessment] = Field(default_factory=list)
    total_claimed_inr: int = 0
    total_allowed_inr: int = 0
    total_disallowed_inr: int = 0
    missing_documents: list[str] = Field(default_factory=list)
    attempt: int = 1
    simulated: bool = True
    adjudicator: str = "SIMULATED — deterministic local rules, not an insurer"
    adjudicated_at: datetime = Field(default_factory=utcnow)


class ClaimSubmission(BaseModel):
    submission_id: str
    packet_id: str
    case_id: str
    stage: ClaimStage
    # Cashless pre-auth must clear in 1 hour; reimbursement runs a 30-day clock
    # under the IRDAI 2024 Master Circular. We track both against real wall time.
    sla_kind: str = "cashless_preauth"
    sla_deadline: datetime | None = None
    # A raised query starts its own response window. The original SLA deadline
    # above keeps running and is still reported — this is an additional clock,
    # not a replacement.
    query_raised_at: datetime | None = None
    query_response_deadline: datetime | None = None
    adjudication_attempts: int = 0
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


# --------------------------------------------------------------------------
# Arrival brief
# --------------------------------------------------------------------------


class FactSource(BaseModel):
    """Where a line in the brief came from.

    Carried on every fact, including the unknown ones. A brief whose lines
    cannot be traced is just a summary; a brief whose lines can be is a
    synthesis someone can audit.
    """

    kind: str                       # "receipt" | "profile" | "derived" | "unknown"
    receipt_seq: int | None = None
    receipt_kind: str | None = None
    field: str | None = None
    note: str | None = None         # for unknown: why it is not known


class ArrivalFact(BaseModel):
    """One line of the brief.

    `known=False` means the state does not contain this yet. It is never a
    guess, and `value` is None rather than a plausible placeholder.
    """

    label: str
    value: str | None = None
    known: bool = False
    source: FactSource


class WellbeingEntry(BaseModel):
    """What someone SAID, not what anyone measured.

    Deliberately has no severity, no mood, no score, and no derived state. A
    check-in is a sentence a human typed on a phone; turning it into a health
    assessment would be inference dressed as a reading, and the whole point of
    this record is that it never becomes one.

    `source` is a provenance label, not an identity claim. "self-reported"
    means the message arrived from the number registered to the parent. It does
    not prove who was holding the phone, and nothing downstream may treat it as
    proof.
    """

    entry_id: str
    parent_id: str
    # "self-reported" | "caregiver:<name>"
    source: str
    text: str
    received_at: datetime = Field(default_factory=utcnow)
    channel: str = "whatsapp"


class ArrivalBrief(BaseModel):
    """What is waiting when the family lands.

    Composed deterministically from the signed receipt chain plus the parent's
    stored profile. Nothing here is inferred by a model.
    """

    case_id: str
    parent_id: str
    generated_at: datetime = Field(default_factory=utcnow)
    # Timestamp of the most recent receipt — the real "truth as of".
    as_of: datetime | None = None
    chain_receipt_count: int = 0
    chain_head_hash: str = ""
    chain_verified: bool = False
    facts: list[ArrivalFact] = Field(default_factory=list)
    actions_taken: list[ArrivalFact] = Field(default_factory=list)
    pending: list[ArrivalFact] = Field(default_factory=list)
    bring_with_you: list[ArrivalFact] = Field(default_factory=list)
    contacts: list[ArrivalFact] = Field(default_factory=list)

    @property
    def unknown_count(self) -> int:
        return sum(1 for f in self.facts if not f.known)


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
