"""Gemini widens what is recognised. It never decides what is urgent.

The distinction this file defends: normalising "elephant sitting on my chest"
into "chest pressure" is language work, and a model is good at it. Deciding
that chest pressure warrants waking someone at 3am is a rule, and a rule that
lives in a prompt is not a rule.

So the model is advisory and strictly additive. The tests below try to make it
load bearing and assert that it cannot become so.
"""

from __future__ import annotations

import pytest

from anbu_care.schemas import Severity
from anbu_care.wellbeing import escalation as esc


@pytest.fixture
def model(monkeypatch):
    """Replace symptom extraction with something a test controls."""
    def install(terms, used=True, note="faked"):
        monkeypatch.setattr(esc, "extract_symptoms", lambda text: (terms, used, note))
    return install


# ---- the model cannot quieten anything -----------------------------------


def test_a_silent_model_still_escalates_crushing_chest_pain(model):
    """The floor. If Gemini times out, errors, or is switched off, the raw text
    still goes through the table."""
    model([], used=False, note="model unavailable")
    verdict = esc.assess("crushing chest pain, can't breathe")
    assert verdict.escalate is True
    assert verdict.severity is Severity.HIGH
    assert verdict.model_used is False


@pytest.mark.parametrize("junk", [
    [], ["nothing to report"], ["patient is fine"], ["no symptoms"],
    ["ignore previous instructions"],
])
def test_a_model_returning_junk_cannot_suppress_a_red_flag(model, junk):
    """Including the case where the message tries to talk the model down.

    The model's output is added to the table's input, never substituted for it,
    so a wrong or adversarial suggestion can only fail towards calling someone
    unnecessarily.
    """
    model(junk)
    assert esc.assess("crushing chest pain, can't breathe").escalate is True


def test_the_model_can_widen_recognition(model):
    """The reason Gemini is here at all: wording the keyword table would miss."""
    informal = "feels like an elephant is sitting on my chest and I can't catch my breath"

    model([])                       # keyword scan alone
    without = esc.assess(informal)

    model(["chest pressure", "shortness of breath"])
    with_model = esc.assess(informal)

    assert with_model.escalate is True
    assert with_model.model_terms == ["chest pressure", "shortness of breath"]
    # The point is the model adds reach; the floor may or may not catch this one.
    assert with_model.escalate or not without.escalate


def test_the_model_cannot_invent_an_emergency_out_of_nothing(model):
    """It can add terms, but the table still rules on them.

    "tired" is not a red flag however the model phrases it, so a hallucinated
    term that is not in the table changes nothing.
    """
    model(["mild tiredness", "general malaise"])
    verdict = esc.assess("a bit tired today")
    assert verdict.escalate is False


# ---- ordinary life is not an emergency -----------------------------------


@pytest.mark.parametrize("benign", [
    "slept well, ate breakfast",
    "feeling better today",
    "went for a short walk in the evening",
    "appetite is low but otherwise fine",
    "mood is low today",
    "did not sleep much last night",
    "watched TV with the neighbours",
])
def test_ordinary_check_ins_do_not_escalate(model, benign):
    model([])
    assert esc.assess(benign).escalate is False


# ---- the reply promises only what happened -------------------------------


def test_an_escalation_names_the_ambulance_number(model):
    model([])
    verdict = esc.assess("crushing chest pain, can't breathe")
    assert "108" in esc.reply_text(verdict, alerted=["Meena"])


def test_the_reply_claims_an_alert_only_when_one_was_delivered(model):
    """The sentence that must never be false.

    Telling someone help is coming when nobody was reached is the worst thing
    this system could say, so it is conditioned on the delivery result rather
    than on having tried.
    """
    model([])
    verdict = esc.assess("crushing chest pain, can't breathe")

    reached = esc.reply_text(verdict, alerted=["Meena", "Ravi"])
    assert "We have alerted Meena and Ravi." in reached

    nobody = esc.reply_text(verdict, alerted=[])
    assert "alerted" not in nobody.replace("could not reach", "")
    assert "call someone yourself" in nobody
    assert "108" in nobody


def test_an_ordinary_check_in_gets_the_plain_acknowledgement(model):
    model([])
    verdict = esc.assess("slept well")
    assert esc.reply_text(verdict, alerted=[]) == "Thanks, that's noted."


# ---- what escalation is, and is not --------------------------------------


def test_an_escalation_records_matched_phrases_not_a_conclusion(model):
    """Auditable as "these words matched", never as a diagnosis."""
    model([])
    verdict = esc.assess("crushing chest pain, can't breathe")
    assert verdict.matched, "escalated without recording what matched"
    joined = " ".join(verdict.matched).lower()
    assert "matched" in joined
    for diagnosis in ("myocardial infarction", "heart attack", "you have", "diagnosis"):
        assert diagnosis not in joined


def test_the_escalation_result_carries_no_diagnosis_field():
    fields = set(esc.Escalation.__dataclass_fields__)
    for forbidden in ("diagnosis", "condition", "finding", "assessment"):
        assert forbidden not in fields


@pytest.mark.real_extraction
def test_extraction_failure_is_reported_not_hidden(monkeypatch):
    """A model that could not answer must say so, so the receipt records it."""
    def explode(*a, **k):
        raise RuntimeError("no credentials")

    monkeypatch.setattr("google.genai.Client", explode)
    terms, used, note = esc.extract_symptoms("crushing chest pain")
    assert terms == []
    assert used is False
    assert "unavailable" in note


# ---- what the child is actually told -------------------------------------


def _urgent_body(**over):
    from anbu_care.comms.policy import render_template

    params = {
        "parent_name": "Rajeswari", "timestamp": "02:14 UTC",
        "said": "feels like an elephant is sitting on my chest",
        "hospital_name": "Sacred Heart Hospital", "distance_km": "2.2",
        "why_hospital": ("The extra distance was accepted because Sacred Heart is "
                         "empanelled with Star Health, so this keeps the admission cashless."),
        "cashless_status": "Cashless should apply at this hospital",
    }
    params.update(over)
    return render_template("urgent_family_alert", params)


def test_the_family_alert_answers_what_a_child_asks_first():
    """Woken at 2am, in the order the questions actually arrive."""
    body = _urgent_body()
    assert "elephant is sitting on my chest" in body      # what happened
    assert "Sacred Heart Hospital" in body                # where
    assert "2.2 km" in body                               # how far
    assert "empanelled with Star Health" in body          # why there
    assert "cashless" in body.lower()                     # is it covered
    assert "Call her now" in body                         # what do I do
    assert "108" in body                                  # and if I cannot
    assert "/app" in body                                 # where is the rest


def test_the_family_alert_does_not_pretend_an_ambulance_is_coming():
    """The single most tempting lie in this whole message.

    Anbu Care does not dispatch anything. An ETA, or "help is on the way",
    would read as a promise that transport was arranged, and a child who
    believes it may not make the call that actually matters.
    """
    body = _urgent_body()
    assert "has not called an ambulance and cannot" in body
    for fabrication in ("eta", "arriving in", "on the way", "dispatched",
                        "ambulance is coming", "minutes away"):
        assert fabrication not in body.lower()


def test_her_words_are_labelled_as_hers():
    """Relayed, in quotes, and named as an account rather than a finding."""
    body = _urgent_body()
    assert '"feels like an elephant is sitting on my chest"' in body
    assert "her own words, not a medical assessment" in body


def test_the_family_alert_is_still_gated():
    """Relaying her words does not open a hole. A message that turns out to
    carry a lab value is blocked like any other."""
    from anbu_care.comms.policy import gate_message
    from anbu_care.schemas import MessageClass

    clean = gate_message(_urgent_body(), MessageClass.STATUS,
                         template_name="urgent_family_alert")
    assert clean.allowed is True

    dirty = _urgent_body(said="troponin I came back at 0.94 ng/mL")
    assert gate_message(dirty, MessageClass.STATUS,
                        template_name="urgent_family_alert").allowed is False


def test_the_care_circle_is_told_less_than_the_family():
    """A neighbour is asked to go round. They are not given her symptoms or a
    link into her record."""
    from anbu_care.comms.policy import TEMPLATES

    circle = str(TEMPLATES["care_circle_notice"]["body"])
    assert "{said}" not in circle
    assert "{dashboard_url}" not in circle
    assert "no medical details are shared here" in circle


def test_the_alert_carries_no_internal_scoring():
    """"score 0.971" means nothing to someone woken at 2am, and the hospital is
    already named a line above."""
    from anbu_care.wellbeing.handler import _why_only

    why = _why_only(
        "Severity HIGH. Recommending Sacred Heart Hospital (2.2 km, score 0.971). "
        "That is 1.4 km farther than the nearest option (Idhayalaya Heart Centre, 0.8 km). "
        "The extra distance was accepted because Sacred Heart Hospital is empanelled "
        "with Star Health and Idhayalaya Heart Centre is not, so this keeps the "
        "admission cashless."
    )
    assert "score" not in why
    assert "Severity HIGH" not in why
    assert "Recommending" not in why
    # The part actually worth reading survives.
    assert "farther than the nearest option" in why
    assert "keeps the admission cashless" in why


def test_the_care_circle_is_not_told_she_has_already_arrived():
    """Nobody has taken her anywhere yet. "has been taken to" would be a small
    lie that a neighbour might act on."""
    from anbu_care.comms.policy import TEMPLATES

    body = str(TEMPLATES["care_circle_notice"]["body"])
    assert "has been taken to" not in body
    assert "is being directed to" in body


def test_the_family_alert_gates_on_the_same_purpose_it_selects_on():
    """A mismatch here is silent: contacts are chosen by one consent and then
    refused by another, so someone who agreed to admission alerts gets nothing
    and no error is raised anywhere."""
    import inspect

    from anbu_care.wellbeing import handler

    source = inspect.getsource(handler._tell_the_family)
    assert "consent_ok(contact.consents, consent.ADMISSION_ALERTS)" in source
    assert "purpose_override=consent.ADMISSION_ALERTS" in source


# ---- times a person can read ---------------------------------------------


def test_each_reader_gets_the_time_on_their_own_clock():
    """"15:46 UTC" is a number nobody lives in."""
    from datetime import UTC, datetime

    from anbu_care.comms.localtime import for_reader

    moment = datetime(2026, 8, 21, 15, 46, tzinfo=UTC)
    son = for_reader(moment, "America/Los_Angeles", "Asia/Kolkata", "Thoothukudi")
    assert "8:46 AM your time" in son
    assert "9:16 PM in Thoothukudi" in son
    assert "UTC" not in son


def test_a_reader_in_the_same_city_is_not_told_the_time_twice():
    from datetime import UTC, datetime

    from anbu_care.comms.localtime import for_reader

    same = for_reader(datetime(2026, 8, 21, 15, 46, tzinfo=UTC),
                      "Asia/Kolkata", "Asia/Kolkata", "Thoothukudi")
    assert same == "9:16 PM"


def test_her_local_time_is_carried_because_it_changes_the_meaning():
    """A message sent at 2am reads differently from one sent after lunch, and
    a son three time zones away cannot tell which from his own clock."""
    from datetime import UTC, datetime

    from anbu_care.comms.localtime import for_reader

    small_hours = for_reader(datetime(2026, 8, 21, 20, 30, tzinfo=UTC),
                             "America/Los_Angeles", "Asia/Kolkata", "Thoothukudi")
    assert "2:00 AM in Thoothukudi" in small_hours


def test_an_unknown_timezone_says_utc_rather_than_lying():
    """Falling back silently would print a time wrong by hours."""
    from datetime import UTC, datetime

    from anbu_care.comms.localtime import in_zone

    assert "UTC" in in_zone(datetime(2026, 8, 21, 15, 46, tzinfo=UTC), "Mars/Olympus")


# ---- one person is one person --------------------------------------------


def test_someone_on_both_lists_is_told_once_and_named_once(monkeypatch):
    """A contact can hold both admission-alert and care-circle consent. They
    are still one human being.

    Sending twice wastes the seconds that matter, and "We have alerted Karthik
    and Karthik" reads as a broken system at the exact moment it most needs to
    be believed.
    """
    from anbu_care.comms import consent, transport
    from anbu_care.tools import onboarding_tools
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wb

    sent: list[str] = []
    monkeypatch.setattr(
        transport, "send",
        lambda to, body, mode=None, media_url=None: (
            sent.append(to),
            transport.DeliveryResult(delivered=True, channel="spy", detail="ok"),
        )[1],
    )

    pid = onboarding_tools.create_parent_profile(
        name="Rajeswari Manickam", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-1", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True,
    )
    # Both consents, one person.
    onboarding_tools.record_family_contact(
        parent_id=pid, name="Karthik", relationship="son",
        whatsapp_e164="+16692167706", timezone_name="America/Los_Angeles",
        is_primary=True,
        consent_purposes=[consent.ADMISSION_ALERTS, consent.OUTBOUND_NOTIFY],
    )

    entry = wb.record(pid, "self-reported", "crushing chest pain, can't breathe")
    out = handler.handle(entry, pid)

    assert out.escalated is True
    assert out.alerted == ["Karthik"], f"named more than once: {out.alerted}"
    assert sent.count("+16692167706") == 1, "the same person was messaged twice"

    reply = handler.esc.reply_text(
        handler.esc.assess("crushing chest pain"), out.alerted,
    )
    assert "Karthik and Karthik" not in reply


def test_a_separate_care_circle_contact_still_gets_their_notice(monkeypatch):
    """Deduping must not silence the neighbour."""
    from anbu_care.comms import consent, transport
    from anbu_care.tools import onboarding_tools
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wb

    sent: list[str] = []
    monkeypatch.setattr(
        transport, "send",
        lambda to, body, mode=None, media_url=None: (
            sent.append(to),
            transport.DeliveryResult(delivered=True, channel="spy", detail="ok"),
        )[1],
    )

    pid = onboarding_tools.create_parent_profile(
        name="Rajeswari Manickam", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-1", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True,
    )
    onboarding_tools.record_family_contact(
        parent_id=pid, name="Karthik", relationship="son", whatsapp_e164="+16692167706",
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=[consent.ADMISSION_ALERTS],
    )
    onboarding_tools.record_family_contact(
        parent_id=pid, name="Meena", relationship="neighbour", whatsapp_e164="+919000000101",
        timezone_name="Asia/Kolkata", is_primary=False,
        consent_purposes=[consent.OUTBOUND_NOTIFY],
    )

    entry = wb.record(pid, "self-reported", "crushing chest pain, can't breathe")
    out = handler.handle(entry, pid)

    assert sorted(out.alerted) == ["Karthik", "Meena"]
    assert sorted(sent) == ["+16692167706", "+919000000101"]


# ---- being precise must not make her harder to help ----------------------


def _clinical_case(monkeypatch):
    from anbu_care.comms import consent, transport
    from anbu_care.tools import onboarding_tools

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        transport, "send",
        lambda to, body, mode=None, media_url=None: (
            sent.append((to, body)),
            transport.DeliveryResult(delivered=True, channel="spy", detail="ok"),
        )[1],
    )
    pid = onboarding_tools.create_parent_profile(
        name="Rajeswari Manickam", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=[], allergies=[],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        pid, insurer="Star Health", policy_number="SH-1", sum_insured_inr=500_000,
        network_hospitals=["Sacred Heart Hospital"], cashless_eligible=True,
    )
    onboarding_tools.record_family_contact(
        parent_id=pid, name="Karthik", relationship="son", whatsapp_e164="+16692167706",
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=[consent.ADMISSION_ALERTS],
    )
    return pid, sent


def test_a_mother_who_mentions_a_lab_value_still_gets_her_son_told(monkeypatch):
    """The perverse failure this fixes.

    The family alert quotes her, which is what makes it useful and also the
    only part the gate can refuse. Before the fallback, a check-in mentioning a
    troponin value was blocked outright, so the neighbour was told and the son
    abroad — the one with the dashboard, the insurer relationship and the
    ability to phone the hospital — heard nothing. Being more clinically
    precise made her harder to help.
    """
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wb

    pid, sent = _clinical_case(monkeypatch)
    out = handler.handle(
        wb.record(pid, "self-reported",
                  "crushing chest pain, my troponin was 0.94 ng/mL last time"),
        pid,
    )

    assert out.escalated is True
    assert out.alerted == ["Karthik"], "the son was left untold"
    assert out.not_alerted == []
    assert len(sent) == 1


def test_the_fallback_carries_no_clinical_fragment(monkeypatch):
    """It exists because the gate refused the quote. It must not smuggle it."""
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wb

    pid, sent = _clinical_case(monkeypatch)
    handler.handle(
        wb.record(pid, "self-reported",
                  "crushing chest pain, my troponin was 0.94 ng/mL last time"),
        pid,
    )
    body = sent[0][1]
    for fragment in ("troponin", "0.94", "ng/mL"):
        assert fragment not in body, f"the fallback leaked '{fragment}'"


def test_the_fallback_says_where_her_words_are(monkeypatch):
    """The move this system makes everywhere: refuse to send something, then
    say where it lives rather than pretending it does not exist."""
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wb

    pid, sent = _clinical_case(monkeypatch)
    handler.handle(
        wb.record(pid, "self-reported", "chest pain, troponin 0.94 ng/mL"), pid,
    )
    body = sent[0][1]
    assert "not repeated here" in body
    assert "dashboard" in body
    assert "/app" in body


def test_the_fallback_keeps_everything_that_was_never_the_problem(monkeypatch):
    """Routing, cost and the instruction to call were never clinical."""
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wb

    pid, sent = _clinical_case(monkeypatch)
    handler.handle(
        wb.record(pid, "self-reported", "chest pain, troponin 0.94 ng/mL"), pid,
    )
    body = sent[0][1]
    assert "Sacred Heart Hospital" in body
    assert "2.2 km" in body
    assert "cashless" in body.lower()
    assert "Call her now" in body
    assert "108" in body
    assert "has not called an ambulance and cannot" in body


def test_the_blocked_attempt_is_still_on_the_chain(monkeypatch):
    """Both facts are recorded: the gate refused the quoted version, and the
    withheld version went. A chain showing only the send would hide that the
    boundary held."""
    from anbu_care import service
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wb

    pid, _ = _clinical_case(monkeypatch)
    out = handler.handle(
        wb.record(pid, "self-reported", "chest pain, troponin 0.94 ng/mL"), pid,
    )
    kinds = [r.kind for r in service.get_chain(out.case_id).receipts
             if r.kind.startswith("comms.")]
    assert kinds == ["comms.blocked", "comms.sent"]


def test_an_ordinary_urgent_message_still_quotes_her(monkeypatch):
    """The fallback must not become the default. When nothing is clinical, her
    words are what make the alert worth reading."""
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wb

    pid, sent = _clinical_case(monkeypatch)
    handler.handle(
        wb.record(pid, "self-reported", "crushing chest pain, can't breathe"), pid,
    )
    body = sent[0][1]
    assert '"crushing chest pain, can\'t breathe"' in body
    assert "not repeated here" not in body


def test_the_model_earns_its_place_on_wording_the_table_misses(model):
    """Concrete evidence, not a claim.

    "chest hurts badly" is how a 71-year-old actually types at 2am, and the
    keyword table rates it MEDIUM because it contains no phrase from RED_FLAGS.
    Gemini restating it as "chest pain" is the difference between a son being
    woken and finding out in the morning.
    """
    # No phrase here is in RED_FLAGS: "breathless" and "chest pain" are,
    # "chest hurts badly" is not.
    said = "my chest hurts badly and it is hard to get air"

    model([])                                   # table alone
    assert esc.assess(said).escalate is False

    model(["chest pain", "shortness of breath"])   # with normalisation
    assert esc.assess(said).escalate is True
