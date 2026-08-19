import os

os.environ["ANBU_STORE_BACKEND"] = "memory"
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
