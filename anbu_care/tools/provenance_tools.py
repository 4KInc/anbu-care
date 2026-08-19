"""Provenance tools — available to the coordinator and to a family or insurer
reconstructing what actually happened on a case."""

from __future__ import annotations

from typing import Any

from anbu_care import service
from anbu_care.provenance.signing import load_signer


def verify_case_chain(case_id: str) -> dict[str, Any]:
    """Verify that a case's decision trail was not altered after the fact.

    Recomputes every hash and signature in order. If any earlier entry was
    edited, every hash after it stops matching and this reports where.

    Args:
        case_id: The case to verify.

    Returns:
        Whether the chain verifies, its length, and where it broke if it did.
    """
    result = service.verify_case(case_id)
    signer = load_signer()
    return {
        "status": "ok",
        "case_id": case_id,
        "verified": result.ok,
        "receipt_count": result.length,
        "broken_at_seq": result.broken_at,
        "reason": result.reason,
        "public_key": signer.public_key_b64,
        "key_warning": (
            "Signing key is ephemeral — chains cannot be verified across restarts. "
            "Set ANBU_SIGNING_KEY_B64 before recording a demo."
            if signer.ephemeral
            else None
        ),
    }


def get_case_trail(case_id: str) -> dict[str, Any]:
    """Reconstruct the exact evidence set behind every decision on a case.

    Args:
        case_id: The case to reconstruct.

    Returns:
        Every receipt in order, with its kind, actor, payload, and hash links.
    """
    chain = service.get_chain(case_id)
    if not chain.receipts:
        return {"status": "error", "error": f"no receipts for case {case_id}"}
    result = chain.verify()
    return {
        "status": "ok",
        "case_id": case_id,
        "verified": result.ok,
        "head_hash": chain.head_hash,
        "receipts": [
            {
                "seq": r.seq,
                "kind": r.kind,
                "actor": r.actor,
                "created_at": r.created_at.isoformat(),
                "prev_hash": r.prev_hash[:16] + "...",
                "hash": r.hash[:16] + "...",
                "payload": r.payload,
            }
            for r in chain.receipts
        ],
    }
