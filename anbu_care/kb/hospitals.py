"""Seeded hospital knowledge base.

There is no live hospital-capability feed in this build — the JSON is a dated
snapshot and says so. Every consumer surfaces that provenance rather than
presenting the values as current fact.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from anbu_care.schemas import Hospital

_DATA = Path(__file__).parent / "data" / "hospitals_thoothukudi.json"


@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads(_DATA.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_hospitals() -> tuple[Hospital, ...]:
    return tuple(Hospital.model_validate(h) for h in _raw()["hospitals"])


def KB_META() -> dict:
    return _raw()["_meta"]


def get_hospital(hospital_id: str) -> Hospital | None:
    return next((h for h in load_hospitals() if h.hospital_id == hospital_id), None)
