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
