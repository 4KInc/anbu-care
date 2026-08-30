"""Central configuration. Everything env-driven so the same code runs locally,
against the Firestore emulator, and on Cloud Run."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Vertex AI / Gemini
    project_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", "anbu-care-hack"))
    location: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    model: str = field(default_factory=lambda: os.getenv("ANBU_MODEL", "gemini-3.5-flash"))

    # Storage
    store_backend: str = field(default_factory=lambda: os.getenv("ANBU_STORE_BACKEND", "firestore"))
    firestore_database: str = field(default_factory=lambda: os.getenv("ANBU_FIRESTORE_DATABASE", "(default)"))
    emulator_host: str | None = field(default_factory=lambda: os.getenv("FIRESTORE_EMULATOR_HOST"))

    # Pub/Sub
    pubsub_enabled: bool = field(default_factory=lambda: _flag("ANBU_PUBSUB_ENABLED", False))
    topic_intake: str = field(default_factory=lambda: os.getenv("ANBU_TOPIC_INTAKE", "anbu-intake-events"))
    topic_case_updates: str = field(default_factory=lambda: os.getenv("ANBU_TOPIC_CASE_UPDATES", "anbu-case-updates"))
    topic_claim_status: str = field(default_factory=lambda: os.getenv("ANBU_TOPIC_CLAIM_STATUS", "anbu-claim-status"))

    # Provenance
    signing_key_b64: str | None = field(default_factory=lambda: os.getenv("ANBU_SIGNING_KEY_B64") or None)

    # WhatsApp — sandbox only for the hackathon window.
    whatsapp_mode: str = field(default_factory=lambda: os.getenv("ANBU_WHATSAPP_MODE", "sandbox"))
    whatsapp_phone_number_id: str | None = field(default_factory=lambda: os.getenv("WHATSAPP_PHONE_NUMBER_ID") or None)
    whatsapp_access_token: str | None = field(default_factory=lambda: os.getenv("WHATSAPP_ACCESS_TOKEN") or None)

    # Insurer / TPA — simulated for the hackathon window.
    tpa_mode: str = field(default_factory=lambda: os.getenv("ANBU_TPA_MODE", "simulated"))
    tpa_endpoint: str | None = field(default_factory=lambda: os.getenv("ANBU_TPA_ENDPOINT") or None)

    # The one store that outlives a case. A full Agent Engine resource name,
    # projects/<p>/locations/<l>/reasoningEngines/<id>. Absent, lessons are not
    # written and not read, and every check-in falls back to the profile.
    memory_bank: str | None = field(default_factory=lambda: os.getenv("ANBU_MEMORY_BANK") or None)

    @property
    def use_memory_store(self) -> bool:
        return self.store_backend.lower() == "memory"


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
