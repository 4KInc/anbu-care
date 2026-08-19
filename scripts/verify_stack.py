"""Confirm the mandatory stack is actually reachable, not just configured.

    uv run python scripts/verify_stack.py

Checks, in order: Vertex AI credentials, the Gemini model, Firestore reads and
writes, Pub/Sub topics, and whether the signing key is stable. Each check
reports on its own so a single missing piece does not hide the rest.
"""

from __future__ import annotations

import sys

from anbu_care.config import settings

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


def report(name: str, status: str, detail: str) -> bool:
    mark = {PASS: "✓", FAIL: "✗", WARN: "!"}[status]
    print(f"  {mark} {name:<22} {detail}")
    return status != FAIL


def check_vertex() -> bool:
    cfg = settings()
    try:
        from google import genai

        client = genai.Client(vertexai=True, project=cfg.project_id, location=cfg.location)
        response = client.models.generate_content(
            model=cfg.model,
            contents="Reply with exactly: ANBU_OK",
        )
        text = (response.text or "").strip()
        if "ANBU_OK" in text:
            return report("Vertex AI + Gemini", PASS, f"{cfg.model} responded in {cfg.location}")
        return report("Vertex AI + Gemini", WARN, f"{cfg.model} responded, unexpected text: {text[:60]!r}")
    except Exception as exc:  # noqa: BLE001 - we want the reason, whatever it is
        return report("Vertex AI + Gemini", FAIL, _short(exc))


def check_firestore() -> bool:
    cfg = settings()
    if cfg.use_memory_store:
        return report("Firestore", WARN, "ANBU_STORE_BACKEND=memory — Firestore not exercised")
    try:
        from anbu_care.provenance.store import FirestoreStore

        store = FirestoreStore()
        store.put("HEALTHCHECK", "PROBE", {"ok": True})
        row = store.get("HEALTHCHECK", "PROBE")
        if not row or not row.get("ok"):
            return report("Firestore", FAIL, "wrote a probe document but could not read it back")
        rows = store.query_prefix("HEALTHCHECK", "PROBE")
        count = len(rows)
        # Clean up after ourselves — a health check should not leave a document
        # behind in the real ledger on every run.
        store.delete("HEALTHCHECK", "PROBE")
        return report("Firestore", PASS, f"read/write/range-query/delete ok ({count} row) in {cfg.project_id}")
    except Exception as exc:  # noqa: BLE001
        return report("Firestore", FAIL, _short(exc))


def check_pubsub() -> bool:
    cfg = settings()
    if not cfg.pubsub_enabled:
        return report("Pub/Sub", WARN, "ANBU_PUBSUB_ENABLED=false — publishing is a no-op")
    try:
        from google.cloud import pubsub_v1

        publisher = pubsub_v1.PublisherClient()
        wanted = {cfg.topic_intake, cfg.topic_case_updates, cfg.topic_claim_status}
        existing = {
            t.name.rsplit("/", 1)[-1]
            for t in publisher.list_topics(request={"project": f"projects/{cfg.project_id}"})
        }
        missing = wanted - existing
        if missing:
            return report("Pub/Sub", FAIL, f"missing topics: {', '.join(sorted(missing))}")
        return report("Pub/Sub", PASS, f"{len(wanted)} topics present")
    except Exception as exc:  # noqa: BLE001
        return report("Pub/Sub", FAIL, _short(exc))


def check_signing_key() -> bool:
    from anbu_care.provenance.signing import load_signer

    signer = load_signer()
    if signer.ephemeral:
        return report(
            "Signing key", WARN,
            "ephemeral — chains will not verify across restarts. Run `make keygen`.",
        )
    return report("Signing key", PASS, f"stable, public key {signer.public_key_b64[:16]}...")


def check_agents() -> bool:
    try:
        from anbu_care.agent import root_agent

        names = [a.name for a in root_agent.sub_agents]
        return report("ADK agents", PASS, f"{root_agent.name} + {len(names)} sub-agents: {', '.join(names)}")
    except Exception as exc:  # noqa: BLE001
        return report("ADK agents", FAIL, _short(exc))


def _short(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    return text[:160] + ("..." if len(text) > 160 else "")


def main() -> int:
    cfg = settings()
    print(f"\nAnbu Care stack check — project {cfg.project_id}, location {cfg.location}\n")
    results = [check_agents(), check_vertex(), check_firestore(), check_pubsub(), check_signing_key()]
    ok = all(results)
    print("\n" + ("All required checks passed." if ok else "Some checks FAILED — see above.") + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
