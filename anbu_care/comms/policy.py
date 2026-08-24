"""WhatsApp message policy.

Meta's healthcare policy and India's DPDP Act draw a hard line: logistics,
status, and billing may go over WhatsApp; clinical detail may not. This is
enforced in code, before send, because a prompt that is asked not to leak a lab
value is not a control.

The design consequence is deliberate: because clinical data cannot go over
WhatsApp, the WhatsApp beats are logistics and billing. The ECG reading and the
diagnosis live inside the secure dashboard.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from anbu_care.schemas import MessageClass

# Free-form replies are only permitted within 24h of family-initiated contact;
# outside it, only pre-approved templates may be sent.
FREE_FORM_WINDOW = timedelta(hours=24)

ALLOWED_CLASSES = {MessageClass.LOGISTICS, MessageClass.STATUS, MessageClass.BILLING}

# Patterns that mark a message as clinical. Matching any of these blocks the
# send outright, whatever the caller claimed the message class was.
CLINICAL_PATTERNS: list[tuple[str, str]] = [
    (r"\b(diagnos(is|ed|tic finding)|impression)\b", "states a diagnosis"),
    (r"\b(ecg|ekg|echo|troponin|creatinine|hba1c|ldl|hdl|cholesterol|wbc|rbc|platelet|haemoglobin|hemoglobin)\b",
     "names a lab or diagnostic result"),
    (r"\b\d+(\.\d+)?\s*(mg/dl|mmol/l|ng/ml|g/dl|mmhg|bpm|iu/l)\b", "carries a clinical measurement"),
    (r"\b(prescrib\w*|dosage|\d+\s*mg\b|\d+\s*ml\b|twice daily|bd|tds|od)\b", "carries prescription specifics"),
    (r"\b(myocardial infarction|stroke|infarct|stenosis|tumou?r|carcinoma|sepsis|arrhythmia)\b",
     "names a clinical condition"),
    (r"\b(biopsy|histopath\w*|culture report|scan (report|findings))\b", "references a diagnostic report"),
]

# Where a family member goes for anything the gate will not send. Every
# template points here, because "we did not tell you the lab value" is only
# reasonable if the same message says where the lab value actually is.
DASHBOARD_URL = os.getenv("ANBU_DASHBOARD_URL", "https://anbu-care-37j4eofpwq-el.a.run.app/app")

# The pre-approved template set. Production template approval takes ~10-15
# business days and will not clear inside the hackathon window, so these are
# registered against the WhatsApp Business API sandbox.
#
# These are read by a worried son or daughter on a phone, usually at a bad
# hour, so they are written the way a person would write them. No em dashes,
# no semicolons, no stacked clauses, and a link rather than an instruction to
# go and find something.
TEMPLATES: dict[str, dict[str, object]] = {
    "admission_alert": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: {parent_name} is being taken to {hospital_name} in {hospital_area}. "
                "The reason given was {reason_short}. "
                "You can see everything we know here: {dashboard_url}",
        "params": ["parent_name", "hospital_name", "hospital_area", "reason_short"],
    },
    "status_update": {
        "message_class": MessageClass.STATUS,
        "body": "Anbu Care: {parent_name} is {status}. "
                "That is from {hospital_name} at {timestamp}. "
                "The full record is here: {dashboard_url}",
        "params": ["parent_name", "status", "hospital_name", "timestamp"],
    },
    "claim_stage": {
        "message_class": MessageClass.BILLING,
        "body": "Anbu Care: the claim for {parent_name} has moved to {stage}. "
                "The amount claimed is INR {amount}. "
                "You can follow it here: {dashboard_url}",
        "params": ["parent_name", "stage", "amount"],
    },
    "billing_summary": {
        "message_class": MessageClass.BILLING,
        "body": "Anbu Care: the bill for {parent_name} comes to INR {total} across {line_count} items. "
                "The itemised breakdown is here: {dashboard_url}",
        "params": ["parent_name", "total", "line_count"],
    },
    "bill_recorded": {
        # BILLING, and the numbers here are the family's own bill read back to
        # them — what was charged, not what a clinician found. The estimate is
        # named an estimate in the copy, because a figure in a WhatsApp message
        # is the version people remember.
        "message_class": MessageClass.BILLING,
        # Order matters more than wording here. Two figures were arriving in
        # the wrong order and reading as contradictory: what the family will
        # END UP paying once the insurer settles, and what the hospital wants
        # TODAY before it has. The second is larger, comes first in time, and
        # is the only one anybody can act on, so it leads.
        "body": "Anbu Care: that bill is on {parent_name}'s record. "
                "{line_count} line items, INR {this_bill} on this bill.\n"
                "{adjustment_line}"
                "{payment_line}"
                "{settlement_lines}"
                "That is an estimate from the policy terms, not the insurer's "
                "decision.\n\n"
                "The itemised breakdown, and the photo it was read from, are "
                "here: {dashboard_url}",
        # Opens on the bill rather than the front page: a message about money
        # that lands you on a triage timeline reads as a broken link.
        "view": "claim",
        "params": ["parent_name", "line_count", "this_bill", "adjustment_line",
                   "settlement_lines", "payment_line"],
    },
    "document_recorded": {
        # LOGISTICS. The summary line repeats what the document states and is
        # written by the extractor, never composed here — but it can carry a
        # diagnosis, so the gate classifies the rendered body like any other
        # message and will refuse it if it does.
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: that {document_kind} is on {parent_name}'s record.\n"
                "{summary}\n"
                "{applied_line}"
                "It is a reading of a photograph, and the photograph is kept. "
                "Check it here: {dashboard_url}",
        "view": "record",
        "params": ["parent_name", "document_kind", "summary", "applied_line"],
    },
    # A document that could not be read. Distinct from the bill wording,
    # because calling a lab report a bill and asking for "the amounts by hand"
    # is advice that cannot be followed.
    "document_unreadable": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: that {subject} could not be read. {reason}\n"
                "The photo is kept, so nothing is lost. Send a clearer one, or "
                "add it here: {dashboard_url}",
        "view": "record",
        "params": ["subject", "reason"],
    },

    # The same photograph arriving twice. NOT a failure, and it must not read
    # like one: the record is intact, the earlier reading stands, and there is
    # nothing for the sender to do. Sending the unreadable template here told a
    # family their successfully-recorded lab report was an unreadable bill.
    "document_already_recorded": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: that {subject} is already on {parent_name}'s record. "
                "It is the same photograph as the one recorded earlier, so it "
                "has not been added twice.\n"
                "What was read from it is here: {dashboard_url}",
        "view": "record",
        "params": ["parent_name", "subject"],
    },

    # The bill lane's version. Separate because a duplicated bill has a
    # specific consequence worth stating plainly: the money was not doubled.
    # That is the whole reason the check exists.
    "bill_already_recorded": {
        "message_class": MessageClass.BILLING,
        "body": "Anbu Care: that bill is already on {parent_name}'s record. "
                "It is the same photograph as the one recorded earlier, so the "
                "amount has not been counted twice.\n"
                "The itemised breakdown is here: {dashboard_url}",
        "view": "claim",
        "params": ["parent_name"],
    },

    # A DIFFERENT photograph of a bill already on file. Separate copy from the
    # same-photograph case because they are different events to the sender: one
    # is a retry, the other is somebody who retook a blurry photo and would
    # reasonably wonder why the system thinks it is the same picture.
    "bill_already_recorded_retake": {
        "message_class": MessageClass.BILLING,
        "body": "Anbu Care: that is bill {bill_no}, which is already on "
                "{parent_name}'s record. It is a different photograph of the "
                "same bill, so the amount has not been counted twice.\n"
                "The itemised breakdown is here: {dashboard_url}",
        "view": "claim",
        "params": ["parent_name", "bill_no"],
    },

    # The test is deliberately NOT named. Run through the real classifier,
    # "ECG", "troponin I", "lipid profile" and "HbA1c" all trip "names a lab or
    # diagnostic result" and the message is refused — correctly, because that
    # is clinical detail and WhatsApp is not where it goes. So the message says
    # a test was ordered and points at the record, where the name sits behind
    # the credential. Loosening the classifier to let test names through would
    # be trading the guarantee for a nicer sentence.
    "diagnostic_options_ready": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: {clinician} has ordered a test for {parent_name}. "
                "Anbu Care found {option_count} nearby places it could be done "
                "and has not booked any of them.\n"
                "The test, the options and how far each one is: {dashboard_url}",
        "view": "record",
        "params": ["clinician", "parent_name", "option_count"],
    },

    "document_recorded_withheld": {
        # The fallback when even the safe summary is refused. Carries a kind and
        # a link and nothing else, so a family is told something arrived rather
        # than left with silence they cannot distinguish from a failure.
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: a {document_kind} for {parent_name} has been recorded. "
                "What it says is not carried over WhatsApp. Read it here: "
                "{dashboard_url}",
        "view": "record",
        "params": ["parent_name", "document_kind"],
    },
    "bill_unreadable": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: that bill could not be read. {reason}\n"
                "The photo is kept. Send a clearer one, or add the amounts by "
                "hand here: {dashboard_url}",
        # Also opens on the bills, since that is what the message is about.
        "view": "claim",
        "params": ["reason"],
    },
    "clinician_handoff_link": {
        # LOGISTICS, and it must stay that way: this message carries a link and
        # an instruction, never a finding. The allergies live behind the link,
        # which is the whole point — the gate would block them travelling over
        # WhatsApp, and a link is not the thing it points at.
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: if you are with {parent_name} at the hospital, or you "
                "can reach whoever is, show the treating team this link.\n"
                "{handoff_url}\n\n"
                "It opens a read-only summary of her allergies, conditions, "
                "medication and recent results. No login is needed. It stops "
                "working in {expires_minutes} minutes and you can stop it sooner "
                "from the dashboard.\n"
                "Every time it is opened, that is recorded on her case.",
        "params": ["parent_name", "handoff_url", "expires_minutes"],
    },
    "urgent_family_alert": {
        "message_class": MessageClass.STATUS,
        # Written for one reader: the son or daughter who wakes at 2am to this.
        # Everything they will ask in the first thirty seconds, in the order
        # they will ask it — what happened, where is she, why there, is it
        # covered, what do I do — and then a way to see the rest.
        "body": "Anbu Care, urgent. {parent_name} sent this at {timestamp}:\n"
                "\"{said}\"\n"
                "{words_note}"
                "{understood_as}"
                "\nShe is being directed to {hospital_name}, {distance_km} km away. "
                "{why_hospital}\n"
                "{cashless_status}.\n\n"
                "Call her now. If you cannot reach her, call 108, the ambulance "
                "line in India. Anbu Care has not called an ambulance and cannot.\n"
                "Everything known so far: {dashboard_url}",
        "params": ["parent_name", "timestamp", "said", "words_note", "understood_as",
                   "hospital_name",
                   "distance_km", "why_hospital", "cashless_status"],
    },
    "urgent_family_alert_withheld": {
        "message_class": MessageClass.STATUS,
        # The same alert with the quote removed, for when what she wrote
        # contains medical detail the gate will not carry. Everything else is
        # identical, because the routing, the cost and the instruction to call
        # were never the problem. Being more clinically precise must not make a
        # mother harder to help.
        "body": "Anbu Care, urgent. {parent_name} sent a message at {timestamp}.\n"
                "What she wrote contains medical detail, so it is not repeated here. "
                "You can read it in the dashboard.\n"
                "{understood_as}"
                "\nShe is being directed to {hospital_name}, {distance_km} km away. "
                "{why_hospital}\n"
                "{cashless_status}.\n\n"
                "Call her now. If you cannot reach her, call 108, the ambulance "
                "line in India. Anbu Care has not called an ambulance and cannot.\n"
                "Her exact words and everything else: {dashboard_url}",
        "params": ["parent_name", "timestamp", "understood_as", "hospital_name",
                   "distance_km", "why_hospital", "cashless_status"],
    },
    "voice_note_unclear": {
        "message_class": MessageClass.STATUS,
        # A case is opened, but no triage has run and no hospital was chosen,
        # so this template names neither. Claiming a routing decision that was
        # never made would be a worse failure than the one it is reporting.
        "body": "Anbu Care, urgent. {parent_name} sent a voice note at {timestamp}.\n"
                "Anbu Care could not make out what she said. No symptoms have been "
                "identified and nothing has been assessed.\n\n"
                "Please listen to the recording and call her now. If you cannot reach "
                "her, call 108, the ambulance line in India. Anbu Care has not called "
                "an ambulance and cannot.\n"
                "Listen to it here: {dashboard_url}",
        "params": ["parent_name", "timestamp"],
    },
    "care_circle_unclear": {
        "message_class": MessageClass.LOGISTICS,
        # The neighbour is asked to go round. No hospital is named because none
        # was chosen, and no recording is shared because they are a notified
        # party, not someone with access to her record.
        "body": "Anbu Care: {parent_name} sent a voice message at {timestamp} that "
                "could not be understood. Please check on her or call her now. "
                "You are receiving this as a listed contact. No medical details are "
                "shared here.",
        "params": ["parent_name", "timestamp"],
    },
    "care_circle_notice": {
        "message_class": MessageClass.LOGISTICS,
        # Where, when, and whether the bill is covered. There is no slot for a
        # reason, a finding or a condition, so the commonest way clinical
        # detail reaches a template — someone filling a free-text field — does
        # not exist here.
        "body": "Anbu Care: {parent_name} is being directed to {hospital_name}, {timestamp}. "
                "{cashless_status}. "
                "You are receiving this as a listed contact. No reply is needed, "
                "and no medical details are shared here.",
        "params": ["parent_name", "hospital_name", "timestamp", "cashless_status"],
    },
    # ---- recovery check-ins --------------------------------------------
    #
    # The first template in this file addressed to the PARENT rather than to a
    # family member, and the wording carries three deliberate refusals.
    #
    # It names no medicine. "Telmisartan 40 mg" is prescription specifics; the
    # gate would block this message and be right to. "Today's medicines" asks
    # the same question and carries none of it.
    #
    # It asks and does not tell. There is no "you should", no "make sure you",
    # no "if X then Y". A son asks how his mother is; he does not issue her
    # instructions, and a system standing in for one must not either.
    #
    # It says what it is. The last line exists so that nothing about a daily
    # message from something that knows her medical history can be mistaken for
    # a clinician checking on her.
    "recovery_check_in": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: good morning {parent_name}. "
                "It is day {day} since you came home.\n\n"
                "How are you feeling today?\n"
                "Did you take today's medicines?\n"
                "Is there any new discomfort?\n\n"
                "Reply here in your own words, or send a voice note. "
                "This is a check-in, not medical advice. Nobody has assessed you. "
                "If something is wrong now, call 108.\n"
                "Reply STOP at any time and these messages end.",
        "params": ["parent_name", "day"],
    },

    # What the family is told when a recovery answer trips the deterministic
    # table. It reports WHAT WAS HEARD and never what it might mean.
    #
    # No hospital line, no routing sentence, no "this could be". The acute
    # alert names a hospital because triage ranked one; this one says the words
    # she used and asks a human to call her, which is the entire honest content
    # of "your mother said something concerning".
    "recovery_escalation_family": {
        "message_class": MessageClass.STATUS,
        "body": "Anbu Care: {parent_name} answered today's recovery check-in at "
                "{timestamp}, day {day} since she came home.\n\n"
                "We heard: \"{said}\"\n"
                "{words_note}"
                "{understood_as}"
                "\nThat is what she said. Nobody has assessed it and Anbu Care "
                "has not.\n\n"
                "Please call her now. If you cannot reach her, call 108, the "
                "ambulance line in India. Anbu Care has not called an ambulance "
                "and cannot.\n"
                "Everything recorded: {dashboard_url}",
        "params": ["parent_name", "timestamp", "day", "said", "words_note",
                   "understood_as"],
    },

    # The same alert with her words removed, for when what she said carries
    # medical detail the gate will not carry. Being more clinically precise
    # must not make a mother harder to help — the same reasoning, and the same
    # fallback, as the acute lane's withheld variant.
    "recovery_escalation_family_withheld": {
        "message_class": MessageClass.STATUS,
        "body": "Anbu Care: {parent_name} answered today's recovery check-in at "
                "{timestamp}, day {day} since she came home.\n\n"
                "What she said contains medical detail, so it is not repeated "
                "here. You can read it in the dashboard.\n"
                "{understood_as}"
                "\nNobody has assessed it and Anbu Care has not.\n\n"
                "Please call her now. If you cannot reach her, call 108, the "
                "ambulance line in India. Anbu Care has not called an ambulance "
                "and cannot.\n"
                "Her exact words and everything else: {dashboard_url}",
        "params": ["parent_name", "timestamp", "day", "understood_as"],
    },

    "doctor_assigned": {
        "message_class": MessageClass.LOGISTICS,
        "body": "Anbu Care: Dr. {doctor_name} from {department} is now looking after {parent_name} "
                "at {hospital_name}. "
                "You can see the case here: {dashboard_url}",
        "params": ["doctor_name", "department", "parent_name", "hospital_name"],
    },
}


@dataclass
class GateResult:
    allowed: bool
    message_class: MessageClass
    reason: str
    requires_template: bool
    detected_clinical: list[str]


def classify_message(body: str, declared: MessageClass | None = None) -> tuple[MessageClass, list[str]]:
    """Detect clinical content regardless of what the caller declared."""
    lowered = body.lower()
    hits = [why for pattern, why in CLINICAL_PATTERNS if re.search(pattern, lowered)]
    if hits:
        return MessageClass.CLINICAL, hits
    return (declared or MessageClass.STATUS), []


def gate_message(
    body: str,
    declared: MessageClass | None = None,
    *,
    last_inbound_at: datetime | None = None,
    template_name: str | None = None,
    now: datetime | None = None,
) -> GateResult:
    """Decide whether this message may leave the platform over WhatsApp."""
    actual, hits = classify_message(body, declared)

    if actual is MessageClass.CLINICAL:
        return GateResult(
            allowed=False,
            message_class=actual,
            reason=(
                "Blocked: clinical detail may not be sent over WhatsApp under Meta's "
                "healthcare policy and DPDP. It "
                + ", ".join(hits)
                + ". Deliver this through the secure dashboard instead."
            ),
            requires_template=False,
            detected_clinical=hits,
        )

    if actual not in ALLOWED_CLASSES:
        return GateResult(
            allowed=False,
            message_class=actual,
            reason=f"Blocked: message class {actual.value} is not permitted over WhatsApp.",
            requires_template=False,
            detected_clinical=[],
        )

    current = now or datetime.now(UTC)
    in_window = last_inbound_at is not None and (current - last_inbound_at) <= FREE_FORM_WINDOW

    if in_window:
        return GateResult(
            allowed=True,
            message_class=actual,
            reason="Allowed: inside the 24-hour customer-service window opened by family-initiated contact.",
            requires_template=False,
            detected_clinical=[],
        )

    if template_name and template_name in TEMPLATES:
        return GateResult(
            allowed=True,
            message_class=actual,
            reason=f"Allowed: outside the 24-hour window, sent as pre-approved template '{template_name}'.",
            requires_template=True,
            detected_clinical=[],
        )

    return GateResult(
        allowed=False,
        message_class=actual,
        reason=(
            "Blocked: outside the 24-hour window and no pre-approved template named. "
            "Free-form messages are only permitted within 24h of family-initiated contact."
        ),
        requires_template=True,
        detected_clinical=[],
    )


def render_template(template_name: str, params: dict[str, str],
                    case_id: str | None = None, parent_id: str | None = None) -> str:
    template = TEMPLATES.get(template_name)
    if template is None:
        raise KeyError(f"unknown template '{template_name}'")
    required = set(template["params"])  # type: ignore[arg-type]
    missing = required - params.keys()
    if missing:
        raise ValueError(f"template '{template_name}' missing params: {sorted(missing)}")
    # The link is injected here, not passed in: a caller must not be able to
    # point a family member at an address of its choosing. It may name which
    # case to open, so the link lands on the episode the message is about
    # rather than on an empty dashboard.
    url = DASHBOARD_URL
    if case_id:
        url = f"{url}?case={case_id}"
        # A signed, expiring credential for this case only, so the person who
        # was woken at 2am can open the link instead of hunting for a token.
        # Absent when no signing secret is configured, in which case the link
        # still works but asks them to sign in.
        if parent_id:
            from anbu_care.webauth import make_link_token

            token = make_link_token(parent_id, case_id)
            if token:
                url = f"{url}&t={token}"
        # A template may name which tab it is about, so a message about a bill
        # opens on the bill. Still injected here rather than passed in: the
        # template chooses a view, never an address.
        view = template.get("view")
        if view:
            url = f"{url}&view={view}"
    return str(template["body"]).format(**{**params, "dashboard_url": url})


def consent_ok(consents: dict[str, datetime], purpose: str) -> bool:
    """DPDP requires purpose-specific, timestamped opt-in. A blanket checkbox
    is not sufficient, so consent is checked per purpose, not per contact."""
    return purpose in consents
