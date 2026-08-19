"""Tamper-evident receipt chain.

Every consequential agent decision is appended as a receipt whose hash covers
the previous receipt's hash. Any silent edit to an earlier entry breaks every
hash after it, so a family and an insurer can independently confirm that the
evidence set behind an approval or denial is the one that was actually used.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel, Field

from anbu_care.provenance.signing import Signer, load_signer, verify

GENESIS_HASH = "0" * 64


def canonical_json(payload: Any) -> bytes:
    """Deterministic encoding — sorted keys, no incidental whitespace.

    Two processes must produce byte-identical input for the same logical
    payload or verification is meaningless.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_encode_extra,
    ).encode("utf-8")


def _encode_extra(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"cannot canonicalise {type(value).__name__}")


class Receipt(BaseModel):
    """One link in a case's chain."""

    receipt_id: str
    case_id: str
    seq: int
    kind: str                     # e.g. "triage.decision", "claim.submitted"
    actor: str                    # which agent produced it
    payload: dict[str, Any]
    prev_hash: str
    hash: str
    signature: str
    public_key: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def signing_input(self) -> bytes:
        """The exact bytes covered by both the hash and the signature."""
        return canonical_json(
            {
                "receipt_id": self.receipt_id,
                "case_id": self.case_id,
                "seq": self.seq,
                "kind": self.kind,
                "actor": self.actor,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
                "created_at": self.created_at,
            }
        )

    def compute_hash(self) -> str:
        return hashlib.sha256(self.signing_input()).hexdigest()


def build_receipt(
    *,
    case_id: str,
    seq: int,
    kind: str,
    actor: str,
    payload: dict[str, Any],
    prev_hash: str,
    signer: Signer | None = None,
    created_at: datetime | None = None,
) -> Receipt:
    signer = signer or load_signer()
    created = created_at or datetime.now(timezone.utc)
    receipt_id = f"{case_id}:{seq:06d}"
    draft = Receipt(
        receipt_id=receipt_id,
        case_id=case_id,
        seq=seq,
        kind=kind,
        actor=actor,
        payload=payload,
        prev_hash=prev_hash,
        hash="",
        signature="",
        public_key=signer.public_key_b64,
        created_at=created,
    )
    body = draft.signing_input()
    draft.hash = hashlib.sha256(body).hexdigest()
    draft.signature = signer.sign(body)
    return draft


class VerificationResult(BaseModel):
    ok: bool
    length: int
    broken_at: int | None = None
    reason: str | None = None


def verify_chain(receipts: Iterable[Receipt]) -> VerificationResult:
    """Walk the chain and confirm nothing was altered or dropped."""
    ordered = sorted(receipts, key=lambda r: r.seq)
    prev = GENESIS_HASH
    for index, receipt in enumerate(ordered):
        if receipt.seq != index:
            return VerificationResult(
                ok=False, length=len(ordered), broken_at=index,
                reason=f"sequence gap: expected seq {index}, found {receipt.seq}",
            )
        if receipt.prev_hash != prev:
            return VerificationResult(
                ok=False, length=len(ordered), broken_at=receipt.seq,
                reason="prev_hash does not match the preceding receipt",
            )
        body = receipt.signing_input()
        if receipt.compute_hash() != receipt.hash:
            return VerificationResult(
                ok=False, length=len(ordered), broken_at=receipt.seq,
                reason="payload does not hash to the recorded hash — content was altered",
            )
        if not verify(receipt.public_key, body, receipt.signature):
            return VerificationResult(
                ok=False, length=len(ordered), broken_at=receipt.seq,
                reason="signature does not verify against the recorded public key",
            )
        prev = receipt.hash
    return VerificationResult(ok=True, length=len(ordered))


class ReceiptChain:
    """In-memory view of one case's chain, backed by a store."""

    def __init__(self, case_id: str, receipts: list[Receipt] | None = None):
        self.case_id = case_id
        self.receipts: list[Receipt] = sorted(receipts or [], key=lambda r: r.seq)

    @property
    def head_hash(self) -> str:
        return self.receipts[-1].hash if self.receipts else GENESIS_HASH

    @property
    def next_seq(self) -> int:
        return len(self.receipts)

    def append(self, kind: str, actor: str, payload: dict[str, Any]) -> Receipt:
        receipt = build_receipt(
            case_id=self.case_id,
            seq=self.next_seq,
            kind=kind,
            actor=actor,
            payload=payload,
            prev_hash=self.head_hash,
        )
        self.receipts.append(receipt)
        return receipt

    def verify(self) -> VerificationResult:
        return verify_chain(self.receipts)
