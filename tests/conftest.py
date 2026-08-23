import os

# Set before anything imports config: the suite must never touch GCP, and a
# developer's .env pointing at a real project must not change that.
os.environ["ANBU_STORE_BACKEND"] = "memory"
os.environ["ANBU_PUBSUB_ENABLED"] = "false"
os.environ.setdefault("ANBU_SIGNING_KEY_B64", "")

import pytest

from anbu_care.config import settings
from anbu_care.provenance.store import MemoryStore, set_store

settings.cache_clear()


@pytest.fixture(autouse=True)
def no_payment_provider_calls(monkeypatch):
    """The suite never talks to a payment provider.

    ANBU_PAYMENT_MODE lives in .env, so the moment Razorpay was wired the whole
    suite started making real API calls: 3 seconds became 28, and a machine
    without keys or without a network would have failed for reasons unrelated
    to the code under test.

    A test that wants the provider path asks for it explicitly by setting the
    mode itself, which is a visible decision rather than an inherited one.
    """
    monkeypatch.setenv("ANBU_PAYMENT_MODE", "simulated")


@pytest.fixture(autouse=True)
def fresh_store():
    """Every test gets its own store — receipt sequences must not leak between tests."""
    set_store(MemoryStore())
    yield


TRANSPORT_ENV = (
    "ANBU_WHATSAPP_MODE",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_API_KEY_SID",
    "TWILIO_API_KEY_SECRET",
    "TWILIO_WHATSAPP_FROM",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
)


@pytest.fixture(autouse=True)
def no_ambient_transport(monkeypatch):
    """The suite must never reach a real provider.

    A populated .env is normal on a developer machine, and without this the
    tests would quietly start spending the account's message quota — and pass
    or fail depending on whose laptop they ran on.
    """
    for var in TRANSPORT_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch, request):
    """No test reaches Gemini unless it explicitly asks to.

    Symptom extraction is advisory and every failure path already returns "no
    terms", so the default here is the same thing a timeout produces. That is
    deliberate: it means the suite exercises the deterministic keyword floor,
    which is the part that must hold when the model is unavailable. A test that
    wants model behaviour fakes it explicitly.
    """
    # A test that needs the real function marks itself; it must still not
    # reach the network, so it stubs the client instead.
    if request.node.get_closest_marker("real_extraction"):
        return

    from anbu_care.wellbeing import escalation

    # Patch read(), not extract_symptoms(): assess() calls read() directly, and
    # patching only the wrapper let the whole suite phone Gemini for real. That
    # has now happened twice — once for Twilio, once here — so the rule is to
    # stub the function the code path actually reaches.
    monkeypatch.setattr(
        escalation, "read",
        lambda text: escalation.Reading(note="model disabled in tests"),
    )
    monkeypatch.setattr(
        escalation, "extract_symptoms",
        lambda text: ([], False, "model disabled in tests"),
    )
