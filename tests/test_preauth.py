"""Cashless pre-authorization: the walls, not the happy path.

Every test here is about a claim NOT made. A request is not an authorization.
An authorization is not a settlement. A clock that lapses is a fact recorded
once, with the instant it lapsed carried as data and separate from whenever
anybody noticed. And none of it touches a diagnosis.

The happy path is three lines of glue. These are the reasons it is allowed to
exist.
"""

from __future__ import annotations

import pathlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from anbu_care import service
from anbu_care.preauth import cashless
from anbu_care.tools import onboarding_tools, triage_tools
from anbu_care.webauth import DEMO_TOKEN

SON = "+14155550301"


@pytest.fixture(scope="module")
def client() -> TestClient:
    from anbu_care.server import app

    return TestClient(app)


def _admitted(cashless_eligible: bool = True, sum_insured: int = 500_000,
              consents: list[str] | None = None) -> tuple[str, str]:
    """A parent admitted this morning, with a policy on file."""
    parent_id = onboarding_tools.create_parent_profile(
        name="Ashanthi Machado", age=71, city="Thoothukudi", lat=8.7642, lon=78.1400,
        chronic_conditions=["Hypertension"], allergies=["Penicillin"],
    )["profile"]["parent_id"]
    onboarding_tools.record_insurance_policy(
        parent_id, insurer="Star Health", policy_number="SH-88",
        sum_insured_inr=sum_insured, network_hospitals=["Sacred Heart Hospital"],
        cashless_eligible=cashless_eligible)
    onboarding_tools.record_family_contact(
        parent_id=parent_id, name="Arun", relationship="son", whatsapp_e164=SON,
        timezone_name="America/Los_Angeles", is_primary=True,
        consent_purposes=(consents if consents is not None
                          else ["admission_alerts", "status_updates",
                                "billing_updates", "claim_updates"]))
    case_id = triage_tools.run_triage(
        parent_id=parent_id, symptoms=["chest pain"], free_text="",
        reported_by="caregiver", lat=0.0, lon=0.0, case_id="")["case_id"]
    return parent_id, case_id


def _kinds(case_id: str) -> list[str]:
    return [r.kind for r in service.get_chain(case_id).receipts]


# ---- requested is not authorized -----------------------------------------


def test_a_request_writes_no_authorisation(monkeypatch):
    """The wall this lane exists behind. A pre-auth REQUEST is a question asked
    of a counterparty. Until that counterparty answers, nothing is authorised,
    and a family told otherwise would walk into a hospital expecting cover that
    does not exist yet."""
    _parent_id, case_id = _admitted()

    # Stop at the request: the adjudicator never runs.
    monkeypatch.setattr(cashless, "_decide",
                        lambda req: {"status": "ok", "stopped_before_deciding": True})
    cashless.request_cashless_preauth(case_id)

    kinds = _kinds(case_id)
    assert "pre_auth.requested" in kinds
    assert "pre_auth.authorized" not in kinds
    req = cashless.open_preauth(case_id)
    assert req is not None
    assert req.outcome == cashless.REQUESTED
    assert req.decided_at is None
    assert req.provisional_ceiling_inr is None, \
        "a ceiling was stated before anybody authorised anything"


def test_authorisation_is_written_only_when_the_counterparty_authorises():
    _parent_id, case_id = _admitted()
    result = cashless.request_cashless_preauth(case_id)

    assert result["preauth"]["outcome"] == cashless.AUTHORIZED
    assert "pre_auth.authorized" in _kinds(case_id)


def test_a_policy_that_is_not_cashless_is_refused_not_authorised():
    """Saying otherwise at the door sends a family in expecting something that
    will not happen."""
    _parent_id, case_id = _admitted(cashless_eligible=False)
    result = cashless.request_cashless_preauth(case_id)

    assert result["preauth"]["outcome"] == cashless.DENIED
    assert "pre_auth.denied" in _kinds(case_id)
    assert "pre_auth.authorized" not in _kinds(case_id)
    assert result["preauth"]["provisional_ceiling_inr"] is None


def test_a_missing_sum_insured_is_queried_and_prices_nothing():
    """A ceiling cannot be stated without one, and inventing it would be
    inventing money."""
    _parent_id, case_id = _admitted(sum_insured=0)
    result = cashless.request_cashless_preauth(case_id)

    assert result["preauth"]["outcome"] == cashless.QUERIED
    assert result["preauth"]["provisional_ceiling_inr"] is None
    assert "pre_auth.queried" in _kinds(case_id)


# ---- authorized is not settled -------------------------------------------


def test_authorisation_settles_nothing(client):
    """Cashless means the INSURER pays the HOSPITAL. Anbu Care never moves that
    money and never reports it moved. Mirrors the payment lane: `settled_inr`
    stays None until a distinct actor says otherwise, and `payment.confirmed`
    is never written by the code that initiates."""
    from anbu_care.bills import coverage

    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)

    kinds = _kinds(case_id)
    assert "claim.adjudicated" not in kinds, "a pre-auth wrote a claim adjudication"
    assert "payment.confirmed" not in kinds
    assert not any(k.startswith("payment.") for k in kinds)

    estimate = coverage.estimate_for_case(case_id, [])
    assert estimate.settled_inr is None, \
        "an authorisation was read as settled money"
    assert service.latest_adjudication(case_id) is None


def test_the_provisional_ceiling_never_enters_the_coverage_split():
    """It is a ceiling under the policy, not a covered amount. The split reads
    `claim.adjudicated` receipts and this lane writes none, so the two cannot
    meet by accident."""
    from anbu_care.bills import coverage

    _parent_id, case_id = _admitted(sum_insured=500_000)
    result = cashless.request_cashless_preauth(case_id)
    assert result["preauth"]["provisional_ceiling_inr"] == 500_000

    estimate = coverage.estimate_for_case(case_id, [])
    assert estimate.settled_inr is None
    assert estimate.estimated_covered_inr != 500_000 or estimate.estimated_covered_inr == 0


# ---- it is not a claim packet --------------------------------------------


def test_a_preauth_is_not_a_claim_packet_and_cannot_become_one():
    """A half-filled claim packet would sit in the claim store looking like a
    claim somebody abandoned. This is its own type under its own key."""
    _parent_id, case_id = _admitted()
    result = cashless.request_cashless_preauth(case_id)
    preauth_id = result["preauth"]["preauth_id"]

    assert service.load_packet(case_id, preauth_id) is None
    assert service.load_packet(case_id, f"{preauth_id}:adapter") is None
    case = service.load_case(case_id)
    assert case is not None and case.packet_id is None, \
        "the pre-auth attached itself to the case as a claim packet"


def test_one_admission_is_one_preauth_decided_or_not():
    """Two clocks on one admission would mean two deadlines and two breaches
    for one thing the insurer owes once.

    The rule is per ADMISSION, not per open request. This first blocked only
    while a request was still awaiting an answer, and a live check against the
    deployed service caught it: asking again after an authorisation opened a
    second clock and returned a second authorisation.
    """
    _parent_id, case_id = _admitted()
    first = cashless.request_cashless_preauth(case_id)
    assert first["status"] == "ok"
    assert first["preauth"]["outcome"] == cashless.AUTHORIZED

    again = cashless.request_cashless_preauth(case_id)

    assert again["status"] == "already_requested"
    assert again["preauth"]["preauth_id"] == first["preauth"]["preauth_id"]
    assert "does not ask the counterparty again" in again["note"]
    assert _kinds(case_id).count("pre_auth.requested") == 1
    assert _kinds(case_id).count("pre_auth.authorized") == 1
    assert len(service.list_preauths(case_id)) == 1


def test_a_request_still_awaiting_an_answer_also_blocks_a_second():
    _parent_id, case_id = _admitted()
    import anbu_care.preauth.cashless as mod
    original = mod._decide
    mod._decide = lambda req: {"status": "ok", "held": True}
    try:
        mod.request_cashless_preauth(case_id)
        again = mod.request_cashless_preauth(case_id)
    finally:
        mod._decide = original

    assert again["status"] == "already_requested"
    assert "clock is already running" in again["note"]
    assert _kinds(case_id).count("pre_auth.requested") == 1


def test_the_hospital_is_taken_from_the_ranking_and_is_only_a_name():
    """It was read from a payload key that does not exist, so every pre-auth
    carried an empty hospital. The ranking is where the name actually is, and
    the name is the only thing taken from it."""
    _parent_id, case_id = _admitted()
    result = cashless.request_cashless_preauth(case_id)

    assert result["preauth"]["hospital_name"], "the hospital came back empty"
    requested = next(r for r in service.get_chain(case_id).receipts
                     if r.kind == "pre_auth.requested")
    assert requested.payload["hospital_name"]
    assert "severity" not in str(requested.payload).lower()


# ---- the clock ------------------------------------------------------------


def test_the_breach_fires_on_real_elapsed_time_not_a_flag():
    """Backdated by a real hour and a minute, against the real deadline. No
    compressed constant, no test-only switch."""
    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)

    req = service.list_preauths(case_id)[0]
    req.outcome = cashless.REQUESTED          # still waiting on the counterparty
    req.requested_at = datetime.now(UTC) - timedelta(hours=1, minutes=1)
    req.decision_due_at = req.requested_at + timedelta(hours=1)
    service.save_preauth(req)

    before = cashless.sla_tick()
    assert before["breached"], "an hour that had passed was not recorded"
    assert "pre_auth.clock_breached" in _kinds(case_id)


def test_a_clock_with_time_left_is_not_breached():
    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)
    req = service.list_preauths(case_id)[0]
    req.outcome = cashless.REQUESTED
    service.save_preauth(req)

    assert cashless.sla_tick()["breached"] == []
    assert "pre_auth.clock_breached" not in _kinds(case_id)


def test_the_breach_carries_the_lapse_instant_apart_from_when_it_was_noticed():
    """The honest-timestamp invariant. Nothing on the chain may imply somebody
    was watching at the moment the hour ran out."""
    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)
    req = service.list_preauths(case_id)[0]
    started = datetime.now(UTC) - timedelta(hours=2)
    req.outcome = cashless.REQUESTED
    req.requested_at = started
    req.decision_due_at = started + timedelta(hours=1)
    service.save_preauth(req)

    cashless.sla_tick()

    receipt = next(r for r in service.get_chain(case_id).receipts
                   if r.kind == "pre_auth.clock_breached")
    due = datetime.fromisoformat(receipt.payload["decision_due_at"])
    requested = datetime.fromisoformat(receipt.payload["requested_at"])
    observed = datetime.fromisoformat(receipt.payload["observed_at"])

    assert due == requested + timedelta(hours=1), "the deadline was not request + 1h"
    assert observed > due, "the breach claimed to have been seen before it happened"
    assert receipt.payload["observed_late_by_seconds"] > 0
    # And the receipt's own written-at is a different fact from the deadline.
    assert abs((receipt.created_at - due).total_seconds()) > 60


def test_the_tick_is_idempotent_per_clock():
    """A breach is a fact about an hour that passed, not an event that keeps
    happening. A chain that repeated it every minute would be counting ticks."""
    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)
    req = service.list_preauths(case_id)[0]
    req.outcome = cashless.REQUESTED
    req.requested_at = datetime.now(UTC) - timedelta(hours=3)
    req.decision_due_at = req.requested_at + timedelta(hours=1)
    service.save_preauth(req)

    cashless.sla_tick()
    second = cashless.sla_tick()

    assert second["breached"] == []
    assert _kinds(case_id).count("pre_auth.clock_breached") == 1


def test_the_breach_states_the_right_and_claims_nothing():
    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)
    req = service.list_preauths(case_id)[0]
    req.outcome = cashless.REQUESTED
    req.requested_at = datetime.now(UTC) - timedelta(hours=2)
    req.decision_due_at = req.requested_at + timedelta(hours=1)
    service.save_preauth(req)
    cashless.sla_tick()

    right = next(r for r in service.get_chain(case_id).receipts
                 if r.kind == "pre_auth.clock_breached").payload["irdai_right"]

    assert "IRDAI/HLT/CIR/PRO/84/5/2024" in right
    assert "one hour" in right and "three hours" in right
    assert "two percent above the" in right
    assert "Ombudsman" in right
    assert "has not filed anything" in right
    assert "cannot compel anyone" in right
    for promise in ("we will win", "will be forced", "guaranteed", "you will be paid"):
        assert promise not in right.lower()


# ---- credentialed ---------------------------------------------------------


def test_the_tick_is_credentialed(client):
    """It writes to a family's chain and states a right on their case. An open
    version would let anybody put a breach on somebody else's record."""
    assert client.post("/api/claims/sla-tick").status_code == 401
    assert client.post("/api/claims/sla-tick",
                       headers={"Authorization": "Bearer not-the-token"}
                       ).status_code == 401
    assert client.post("/api/claims/sla-tick",
                       headers={"Authorization": f"Bearer {DEMO_TOKEN}"}
                       ).status_code == 200


# ---- no clinical inference ------------------------------------------------


def test_no_diagnosis_or_severity_reaches_the_preauth():
    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)

    for receipt in service.get_chain(case_id).receipts:
        if not receipt.kind.startswith("pre_auth."):
            continue
        blob = str(receipt.payload).lower()
        for word in ("severity", "diagnosis", "chest pain", "symptom",
                     "triage", "acute coronary"):
            assert word not in blob, f"a pre-auth receipt carried '{word}'"


def test_the_lane_never_calls_triage_or_severity():
    """Grep the CODE, not the prose.

    Docstrings are stripped first, because this module's docstrings talk about
    severity at length in order to say it is never read - and a test that
    failed on the explanation while passing on the behaviour would be checking
    the wrong thing.
    """
    import ast

    tree = ast.parse(pathlib.Path("anbu_care/preauth/cashless.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)

    for banned in ("severity", "esc.assess", "run_triage", "triage_tools",
                   "RED_FLAGS", "diagnosis", "assess("):
        assert banned not in code, f"the pre-auth lane references {banned}"
    # The one permitted touch, and it takes a name and nothing else.
    assert "hospital_name" in code


# ---- simulated, everywhere ------------------------------------------------


def test_the_family_message_does_not_carry_the_build_note(monkeypatch):
    """A family reading about their mother's admission is not the audience for
    a note about the build's counterparty. The disclosure moved, it did not go:
    the test below asserts it on every surface where the claim can be checked.
    """
    from anbu_care.comms.policy import render_template

    body = render_template("preauth_status",
                           {"parent_name": "Ashanthi", "state": "authorised",
                            "detail": "Nothing has been settled."},
                           case_id="case-x", parent_id="p-x")
    body = body if isinstance(body, str) else str(body)

    assert "simulated" not in body.lower()
    assert "authorised" in body


def test_every_preauth_surface_says_simulated(client):
    _parent_id, case_id = _admitted()
    result = cashless.request_cashless_preauth(case_id)

    assert result["simulated"] is True
    assert "SIMULATED" in result["adjudicator"]

    for receipt in service.get_chain(case_id).receipts:
        if receipt.kind in {"pre_auth.authorized", "pre_auth.queried",
                            "pre_auth.denied"}:
            assert receipt.payload["simulated"] is True
            assert "SIMULATED" in receipt.payload["adjudicator"]
        if receipt.kind == "pre_auth.requested":
            assert receipt.payload["simulated"] is True

    body = client.get(f"/api/cases/{case_id}/preauth",
                      headers={"Authorization": f"Bearer {DEMO_TOKEN}"}).json()
    assert body["simulated"] is True
    assert "SIMULATED" in body["adjudicator"]

    page = pathlib.Path("anbu_care/webui/index.html").read_text()
    for kind in ("pre_auth.authorized", "pre_auth.queried", "pre_auth.denied"):
        sentence = re.search(rf'case "{kind}": return [^;]+;', page)
        assert sentence and "SIMULATED" in sentence.group(0), \
            f"{kind} renders without saying the counterparty is simulated"


# ---- the trace still renders only what happened ---------------------------


def test_the_trace_for_a_preauth_case_is_exactly_its_receipts():
    from anbu_care.trace import compose

    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)
    req = service.list_preauths(case_id)[0]
    req.outcome = cashless.REQUESTED
    req.requested_at = datetime.now(UTC) - timedelta(hours=2)
    req.decision_due_at = req.requested_at + timedelta(hours=1)
    service.save_preauth(req)
    cashless.sla_tick()

    receipts = service.get_chain(case_id).receipts
    trace = compose.compose_trace(case_id)

    assert len(trace.steps) == len(receipts)
    assert [s.seq for s in trace.steps] == [r.seq for r in receipts]
    for step in trace.steps:
        assert not step.what.startswith("pre_auth."), \
            "a pre-auth step printed its database name"


# ---- the consent direction ------------------------------------------------


def test_the_family_update_asks_for_claim_updates(monkeypatch):
    """Not billing_updates. Being willing to receive billing summaries is not
    the same agreement as following a claim."""
    from anbu_care.tools import whatsapp_tools

    seen: list[dict] = []
    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: seen.append(kw) or {"allowed": True})

    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)

    assert seen, "the family was not told"
    assert seen[0]["purpose_override"] == "claim_updates"
    assert seen[0]["template_name"] == "preauth_status"


def test_a_contact_without_claim_updates_is_refused():
    """Correctly refused, and the refusal is recorded rather than swallowed."""
    _parent_id, case_id = _admitted(
        consents=["admission_alerts", "status_updates", "billing_updates"])
    result = cashless.request_cashless_preauth(case_id)

    told = result["family_told"]
    assert told is not None and told["allowed"] is False
    assert "claim_updates" in told["reason"]
    assert "comms.blocked" in _kinds(case_id)


def test_the_existing_claim_stage_send_is_untouched():
    """This lane does not refactor the billing_updates path it sits beside."""
    from anbu_care.comms.policy import TEMPLATES
    from anbu_care.tools.whatsapp_tools import PURPOSE_BY_CLASS

    assert PURPOSE_BY_CLASS[__import__(
        "anbu_care.schemas", fromlist=["MessageClass"]
    ).MessageClass.BILLING] == "billing_updates"
    assert TEMPLATES["claim_stage"]["params"] == ["parent_name", "stage", "amount"]


# ---- the adjudicator was reused -------------------------------------------


def test_the_adjudicator_is_reused_not_duplicated():
    """One adjudicator, one enum, one simulated label."""
    from anbu_care.schemas import AdjudicationOutcome
    from anbu_care.tpa import adjudicator as claim_adjudicator

    assert cashless.adjudicate is claim_adjudicator.adjudicate
    assert cashless.SIMULATED_ADJUDICATOR is claim_adjudicator.SIMULATED_ADJUDICATOR
    assert [o.value for o in AdjudicationOutcome] == ["PASS", "PARTIAL", "QUERY", "DENY"], \
        "the verdict enum gained a member"

    source = pathlib.Path("anbu_care/preauth/cashless.py").read_text()
    assert "def adjudicate(" not in source, "the adjudicator was copied"
    assert "SUBLIMIT_RULES" not in source and "NON_COVERED_ITEMS" not in source


def test_preauth_mode_skips_only_the_discharge_shaped_checks():
    """The two skipped gates are both discharge-shaped, and the policy checks
    above them still apply."""
    from anbu_care.schemas import ClaimPacket, InsurancePolicy
    from anbu_care.tpa import adjudicate

    lapsed = InsurancePolicy(insurer="Star Health", policy_number="SH-1",
                             sum_insured_inr=500_000, cashless_eligible=True,
                             valid_until="2020-01-01")
    packet = ClaimPacket(packet_id="p", case_id="c", parent_id="p1")

    # A lapsed policy still denies in pre-auth mode.
    assert adjudicate(packet, lapsed, set(), preauth=True).outcome.value == "DENY"

    live = InsurancePolicy(insurer="Star Health", policy_number="SH-1",
                           sum_insured_inr=500_000, cashless_eligible=True)
    # No discharge summary, no bills, no discharge date - and it still answers.
    assert adjudicate(packet, live, set(), preauth=True).outcome.value == "PASS"
    # The same packet as a CLAIM is queried for the document it lacks.
    assert adjudicate(packet, live, set()).outcome.value == "DENY"


# ---- the breach tells somebody -------------------------------------------


def test_the_breach_sends_on_claim_updates(monkeypatch):
    """A clock watched on a family's behalf that lapses in silence is a clock
    nobody was watching for them."""
    from anbu_care.tools import whatsapp_tools

    seen: list[dict] = []
    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: seen.append(kw) or {"allowed": True})

    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)
    seen.clear()

    req = service.list_preauths(case_id)[0]
    req.outcome = cashless.REQUESTED
    req.requested_at = datetime.now(UTC) - timedelta(hours=2)
    req.decision_due_at = req.requested_at + timedelta(hours=1)
    service.save_preauth(req)
    cashless.sla_tick()

    assert seen, "the hour lapsed and nobody was told"
    assert seen[0]["purpose_override"] == "claim_updates"
    assert seen[0]["template_name"] == "preauth_status"
    assert seen[0]["template_params"]["state"] == "still unanswered after the 1-hour window"


def test_the_breach_message_states_the_right_and_no_verdict(monkeypatch):
    """On a breach the outcome is still `requested`, because nobody answered.
    Saying anything else would invent a verdict out of a silence."""
    from anbu_care.comms.policy import render_template
    from anbu_care.tools import whatsapp_tools

    seen: list[dict] = []
    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: seen.append(kw) or {"allowed": True})

    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)
    seen.clear()
    req = service.list_preauths(case_id)[0]
    req.outcome = cashless.REQUESTED
    req.requested_at = datetime.now(UTC) - timedelta(hours=2)
    req.decision_due_at = req.requested_at + timedelta(hours=1)
    service.save_preauth(req)
    cashless.sla_tick()

    body = render_template("preauth_status", seen[0]["template_params"],
                           case_id=case_id, parent_id=_parent_id)
    body = body if isinstance(body, str) else str(body)

    assert "one hour" in body
    assert "two percent above the bank rate" in body
    assert "Insurance Ombudsman" in body
    assert "has not filed anything" in body and "cannot compel anyone" in body
    for invented in ("authorised", "approved", "refused", "denied", "covered"):
        assert invented not in body.lower(), f"a breach message implied a verdict: {invented}"
    for clinical in ("chest pain", "diagnosis", "severity", "symptom"):
        assert clinical not in body.lower()


def test_the_breach_sends_once_not_per_tick(monkeypatch):
    from anbu_care.tools import whatsapp_tools

    seen: list[dict] = []
    monkeypatch.setattr(whatsapp_tools, "send_family_update",
                        lambda **kw: seen.append(kw) or {"allowed": True})

    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)
    seen.clear()
    req = service.list_preauths(case_id)[0]
    req.outcome = cashless.REQUESTED
    req.requested_at = datetime.now(UTC) - timedelta(hours=2)
    req.decision_due_at = req.requested_at + timedelta(hours=1)
    service.save_preauth(req)

    cashless.sla_tick()
    cashless.sla_tick()
    cashless.sla_tick()

    assert len(seen) == 1, "the family was told the same hour lapsed more than once"


# ---- the demonstration seed is fenced ------------------------------------


def test_a_backdated_clock_is_marked_on_the_chain():
    """A seeded start must never read as an hour that elapsed on its own."""
    _parent_id, case_id = _admitted()
    out = cashless.backdate_request(case_id, minutes=70)

    assert out["status"] == "ok"
    assert out["requested_at_source"] == cashless.DEMONSTRATION_SEED
    assert out["seconds_past_deadline"] > 0

    requested = next(r for r in service.get_chain(case_id).receipts
                     if r.kind == "pre_auth.requested")
    assert requested.payload["requested_at_source"] == cashless.DEMONSTRATION_SEED
    assert "set backwards for a demonstration" in requested.payload["note"]

    cashless.sla_tick()
    breach = next(r for r in service.get_chain(case_id).receipts
                  if r.kind == "pre_auth.clock_breached")
    assert breach.payload["requested_at_source"] == cashless.DEMONSTRATION_SEED


def test_backdating_keeps_the_deadline_honest():
    """Only the start moves. The deadline is still start plus one hour, and the
    breach still has to be genuinely past it."""
    _parent_id, case_id = _admitted()
    out = cashless.backdate_request(case_id, minutes=70)

    started = datetime.fromisoformat(out["requested_at"])
    due = datetime.fromisoformat(out["decision_due_at"])
    assert due - started == timedelta(hours=1)
    assert due < datetime.now(UTC)


def test_backdating_refuses_to_undo_a_decision():
    """Reversing a recorded decision to make a demo work would be editing the
    record to fit the story."""
    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)

    out = cashless.backdate_request(case_id, minutes=70)

    assert out["status"] == "error"
    assert "has no clock to breach" in out["error"]
    assert "pre_auth.clock_breached" not in _kinds(case_id)


def test_backdating_is_safe_to_call_again_after_a_breach():
    _parent_id, case_id = _admitted()
    cashless.backdate_request(case_id, minutes=70)
    cashless.sla_tick()

    again = cashless.backdate_request(case_id, minutes=70)

    assert again["status"] == "already_breached"
    assert _kinds(case_id).count("pre_auth.clock_breached") == 1


def test_the_backdate_endpoint_is_credentialed(client):
    _parent_id, case_id = _admitted()
    path = f"/api/cases/{case_id}/preauth/backdate"

    assert client.post(path).status_code == 401
    assert client.post(path, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post(path, headers={"Authorization": f"Bearer {DEMO_TOKEN}"}
                       ).status_code == 200


# ---- nobody triggers it --------------------------------------------------


def test_admission_files_the_preauth_without_being_asked(client):
    """The pitch, in code. A son who is awake files cashless in the first ten
    minutes; a lane that waits for somebody to trigger it has already failed at
    the thing it exists for.
    """
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wellbeing_store

    parent_id, _case_id = _admitted()
    entry = wellbeing_store.record(parent_id, "self-reported",
                                   "I have chest pain and cannot breathe")
    handled = handler.handle(entry, parent_id)

    assert handled.escalated
    kinds = _kinds(handled.case_id)
    assert "pre_auth.requested" in kinds, "admission did not file a pre-authorisation"
    assert "pre_auth.authorized" in kinds
    req = service.list_preauths(handled.case_id)[0]
    assert req.requested_at_source == "live", "an admission-filed clock was marked seeded"
    assert req.decision_due_at is not None


def test_a_failed_preauth_never_costs_the_alert(monkeypatch):
    """An emergency alert that failed because an insurer's clock could not be
    started would be a far worse outcome than an admission with no pre-auth."""
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wellbeing_store

    def boom(case_id):
        raise RuntimeError("the counterparty exploded")

    import anbu_care.preauth as preauth_pkg
    monkeypatch.setattr(preauth_pkg, "request_cashless_preauth", boom)

    parent_id, _case_id = _admitted()
    entry = wellbeing_store.record(parent_id, "self-reported",
                                   "I have chest pain and cannot breathe")
    handled = handler.handle(entry, parent_id)

    assert handled.escalated, "an escalation was lost to a pre-auth failure"
    assert handled.case_id
    assert "pre_auth.requested" not in _kinds(handled.case_id)


def test_the_preauth_reads_no_severity_from_the_escalation_that_filed_it():
    """It rides in on an escalation that assessed severity. None of that
    travels: a pre-auth asks whether cover exists, never whether treatment is
    warranted."""
    from anbu_care.wellbeing import handler
    from anbu_care.wellbeing import store as wellbeing_store

    parent_id, _case_id = _admitted()
    entry = wellbeing_store.record(parent_id, "self-reported",
                                   "I have chest pain and cannot breathe")
    handled = handler.handle(entry, parent_id)

    for receipt in service.get_chain(handled.case_id).receipts:
        if not receipt.kind.startswith("pre_auth."):
            continue
        blob = str(receipt.payload).lower()
        for word in ("severity", "diagnosis", "chest pain", "symptom", "high"):
            assert word not in blob, f"a pre-auth receipt carried '{word}'"


def test_a_second_seed_for_the_same_parent_is_refused():
    """Each seed is its own clock and each is correctly breached once, which is
    right per clock and wrong in a thread: two identical breach messages read
    as a duplicate-send bug and the presenter ends up explaining something that
    is not part of the story. A message cannot be unsent, so the refusal has to
    come before the second clock exists.
    """
    parent_id, first_case = _admitted()
    assert cashless.backdate_request(first_case, minutes=70)["status"] == "ok"

    second_case = service.open_case(parent_id).case_id
    out = cashless.backdate_request(second_case, minutes=70)

    assert out["status"] == "already_seeded"
    assert out["seeded_case_id"] == first_case
    assert "already seeded" in out["note"].lower()
    assert not service.list_preauths(second_case), "a second clock was started anyway"
    assert "pre_auth.requested" not in _kinds(second_case)


def test_the_guard_is_per_parent_not_global():
    """A different family's demonstration must not be blocked by this one."""
    _p1, case_one = _admitted()
    cashless.backdate_request(case_one, minutes=70)

    _p2, case_two = _admitted()
    assert cashless.backdate_request(case_two, minutes=70)["status"] == "ok"


def test_re_seeding_the_same_case_is_still_safe():
    """The guard skips the case being seeded, so calling it twice on ONE case
    behaves as it did: it re-times that clock rather than refusing."""
    _parent_id, case_id = _admitted()
    first = cashless.backdate_request(case_id, minutes=70)
    again = cashless.backdate_request(case_id, minutes=90)

    assert first["status"] == "ok"
    assert again["status"] == "ok"
    assert len(service.list_preauths(case_id)) == 1


def test_force_seeds_again_when_the_thread_was_cleared():
    """The guard protects against a second identical message sitting beside the
    first. Once the first is deleted it is protecting nothing, and the only one
    who can know that is whoever deleted it. So the override is explicit."""
    parent_id, first_case = _admitted()
    cashless.backdate_request(first_case, minutes=70)

    second_case = service.open_case(parent_id).case_id
    refused = cashless.backdate_request(second_case, minutes=70)
    forced = cashless.backdate_request(second_case, minutes=70, force=True)

    assert refused["status"] == "already_seeded"
    assert forced["status"] == "ok"
    assert forced["requested_at_source"] == cashless.DEMONSTRATION_SEED
    assert "pre_auth.requested" in _kinds(second_case)


def test_force_still_refuses_to_undo_a_decision():
    """The override skips the duplicate-message guard and nothing else. A
    decided request still has no clock to breach."""
    _parent_id, case_id = _admitted()
    cashless.request_cashless_preauth(case_id)

    out = cashless.backdate_request(case_id, minutes=70, force=True)

    assert out["status"] == "error"
    assert "has no clock to breach" in out["error"]
