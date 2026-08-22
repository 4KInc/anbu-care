"""Domain models. These are the contract between agents — every tool takes and
returns one of these shapes so agents stay swappable."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
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
    # Consent to DISCLOSE this record to a third party, held by the parent
    # rather than by a family member, because it is her data being shown. A son
    # agreeing to receive claim updates has not agreed that a stranger may read
    # her allergies — those are different people agreeing to different things,
    # and storing them in the same place is how that distinction gets lost.
    # Purpose -> when it was granted, same shape as FamilyContact.consents.
    disclosure_consents: dict[str, datetime] = Field(default_factory=dict)
    # Her local time, so an alert can say whether a message arrived at 2am or
    # at lunchtime. Set at onboarding rather than guessed from coordinates.
    timezone: str = "Asia/Kolkata"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class FamilyContact(BaseModel):
    name: str
    relationship: str
    whatsapp_e164: str
    timezone: str = "UTC"
    is_primary: bool = False
    # Display only: "family" or "care_circle". Nothing reads this to decide
    # whether a message may be sent. Membership of the care circle is the set
    # of contacts holding outbound_notify consent, so the roster cannot drift
    # away from what people actually agreed to.
    role: str = "family"
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
    # This is the part a mapping provider cannot verify, which is why the
    # dashboard's caveat now names empanelment specifically rather than
    # disclaiming the whole record.
    empanelled_insurers: list[str] = Field(default_factory=list)
    source_note: str | None = None
    # Where the identity and coordinates came from. Carried so the dashboard
    # can say "verified against Google Places on this date" rather than asking
    # anyone to take the location on trust — and so re-verification is visible
    # when it happens.
    place_id: str | None = None
    verified_name: str | None = None
    address: str | None = None
    location_source: str | None = None
    location_verified_on: str | None = None


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


class NotificationResult(BaseModel):
    """What happened for ONE care-circle contact.

    Per contact, never aggregated. A fan-out where one number is unreachable
    is two deliveries and one failure, not "the care circle was notified".
    """

    contact_name: str
    to_e164: str
    role: str = "care_circle"
    consented: bool = False
    allowed: bool = False
    delivered: bool = False
    reason: str = ""
    receipt_id: str | None = None


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
    # "text" when she typed it, "voice" when a model transcribed a recording.
    # The distinction is load-bearing: a transcript is what a model heard, not
    # what she said, and everything downstream is worded accordingly.
    source_kind: str = "text"
    # Where the recording itself lives. The audio is the record; the text above
    # is derived from it. Credentialed, and served through a short-lived
    # signed URL so her son can hear her voice rather than read a paraphrase.
    audio_object: str | None = None


class BillLineItem(BaseModel):
    """One line a model read off a photographed bill.

    `label` is what the bill actually said; `item` is the normalised key the
    coverage rules are looked up under. Both are kept, because a family
    checking a mis-read needs to see the words that were on the paper, and the
    rules need a key they recognise.
    """

    label: str
    item: str
    amount_inr: int
    # Where on the bill this was read from, if the model could say. Free text,
    # shown to a person checking a number against the photograph.
    source_hint: str | None = None


class ExtractedBill(BaseModel):
    """What a model read off a bill image. NOT what is owed.

    Every figure here is a reading, not a fact. A model that reads 96,000 where
    the paper says 9,600 has produced a number that looks exactly as
    authoritative as a correct one, so the image is kept and the reading stays
    traceable back to it. `needs_review` is set whenever the extraction itself
    is uncertain or the arithmetic does not tie out.
    """

    bill_id: str
    case_id: str
    parent_id: str
    line_items: list[BillLineItem] = Field(default_factory=list)
    stated_total_inr: int | None = None
    currency: str = "INR"
    vendor: str | None = None
    bill_date: str | None = None
    # Admission and discharge as printed on the bill. Read because a per-day
    # sub-limit is multiplied by the length of stay: a three-day ICU stay read
    # as one day understates what the insurer covers, and therefore overstates
    # what the family is told they owe.
    admitted_on: str | None = None
    discharged_on: str | None = None
    # The private object the numbers came from. Credentialed access only; this
    # is never handed to a browser except through a short-lived signed URL.
    image_object: str | None = None
    image_sha256: str = ""
    extracted_at: datetime = Field(default_factory=utcnow)
    engine: str = ""
    needs_review: bool = False
    review_reason: str | None = None

    @property
    def computed_total_inr(self) -> int:
        return sum(line.amount_inr for line in self.line_items)


class CoverageLine(BaseModel):
    """One line of the estimated split. Estimated, never settled."""

    label: str
    item: str
    claimed_inr: int
    estimated_covered_inr: int
    estimated_you_pay_inr: int
    rule: str


class CoverageEstimate(BaseModel):
    """What the policy math says about the bills on a case.

    This is an ESTIMATE produced by the same deterministic sub-limit rules the
    simulated adjudicator uses. It is not the insurer's decision, the insurer
    has not been asked, and no field here may be presented as settled money.
    The distinction is carried in the field names on purpose: everything is
    `estimated_`, and `settled_inr` exists separately and stays None until an
    adjudication actually says otherwise.
    """

    case_id: str
    lines: list[CoverageLine] = Field(default_factory=list)
    bills_counted: int = 0
    total_billed_inr: int = 0
    estimated_covered_inr: int = 0
    estimated_you_pay_inr: int = 0
    # Only ever set from a real claim.adjudicated receipt. None means nobody
    # has decided anything yet, which is different from "nothing is owed".
    settled_inr: int | None = None
    basis: str = ""
    disclaimer: str = (
        "Estimated split based on your policy, not the insurer's final "
        "decision. Anbu Care has not asked the insurer and does not decide "
        "claims. On a reimbursement claim the family usually pays first and is "
        "repaid later, so an estimated-covered amount is not money you have."
    )
    needs_review: bool = False


class TraceStep(BaseModel):
    """One step in the decision trace. Always exactly one receipt.

    `seq` and `receipt_hash` are carried so any step can be checked against the
    chain it claims to come from — a trace whose steps could not be traced back
    would be a story, not a record.
    """

    seq: int
    at: datetime
    actor: str
    kind: str
    what: str
    detail: str = ""
    receipt_hash: str = ""


class DecisionTrace(BaseModel):
    """The chain rendered as a sequence a person can follow.

    Read-only, and structurally incapable of inventing a beat: `steps` is built
    one-per-receipt, so `len(steps) == receipt_count` always. If a step is not a
    receipt it does not exist here.
    """

    case_id: str
    steps: list[TraceStep] = Field(default_factory=list)
    receipt_count: int = 0
    chain_head_hash: str = ""
    chain_verified: bool = False
    # Present only when the chain actually contains a QUERY. Describes real
    # receipts by their sequence numbers; it never causes one to be rendered.
    query_fork: dict[str, Any] | None = None

    @property
    def synthesized_steps(self) -> int:
        """Must always be zero. Stated as code because it is the guarantee."""
        return len(self.steps) - self.receipt_count


class EmergencySummary(BaseModel):
    """What a treating team is handed when the family is not there.

    Composed deterministically from stored fields. Nothing here is inferred by
    a model, and nothing here is advice.

    The shape is the guarantee. There is no `recommendation`, no `guidance`, no
    `suggested_treatment`, no `severity` and no `notes` field, because a record
    that offers somewhere for an instruction to sit will eventually have one
    sitting in it. Anbu Care reports what is stored about a patient. What to do
    about it is the clinician's job, and this system is not qualified to have an
    opinion — a test asserts these fields never appear.

    `allergies` is first and separate for the same reason it is first on the
    page: it is the field that kills people when it is missed, and no future
    layout change should be able to fold it into a list of equals.
    """

    parent_id: str
    generated_at: datetime = Field(default_factory=utcnow)
    allergies: list[ArrivalFact] = Field(default_factory=list)
    identity: list[ArrivalFact] = Field(default_factory=list)
    conditions: list[ArrivalFact] = Field(default_factory=list)
    medications: list[ArrivalFact] = Field(default_factory=list)
    recent_labs: list[ArrivalFact] = Field(default_factory=list)
    source_documents: list[ArrivalFact] = Field(default_factory=list)

    # Stated on the artifact itself, not left to the reader to infer.
    disclaimer: str = (
        "Emergency clinical summary for the treating team. Read-only, and not "
        "connected to any hospital system. This is a record of what the family "
        "has provided, not a clinical assessment and not advice."
    )


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
    # Bumped to revoke every outstanding emergency-handoff link at once. The
    # epoch is signed into each token, so incrementing it invalidates them all
    # without storing a list of what was ever issued. A family that wants to
    # stop sharing wants to stop sharing — not to pick which of several links
    # they half-remember sending should die.
    handoff_epoch: int = 0
    triage_decision_id: str | None = None
    packet_id: str | None = None
    submission_id: str | None = None


ParentProfile.model_rebuild()
