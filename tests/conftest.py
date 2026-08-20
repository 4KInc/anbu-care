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
