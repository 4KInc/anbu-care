"""The chain's only job is to make silent edits detectable. Test that."""

from __future__ import annotations

from anbu_care.provenance.chain import GENESIS_HASH, ReceiptChain, canonical_json, verify_chain
from anbu_care.provenance.signing import Signer, generate_key_b64, verify


def test_empty_chain_starts_at_genesis():
    chain = ReceiptChain("case-1")
    assert chain.head_hash == GENESIS_HASH
    assert chain.next_seq == 0
    assert chain.verify().ok


def test_chain_links_each_receipt_to_the_previous():
    chain = ReceiptChain("case-1")
    first = chain.append("a", "agent", {"x": 1})
    second = chain.append("b", "agent", {"x": 2})
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    assert chain.verify().ok


def test_altered_payload_breaks_verification_at_that_receipt():
    chain = ReceiptChain("case-1")
    chain.append("triage.decision", "triage_agent", {"severity": "HIGH"})
    chain.append("claim.submitted", "insurer_liaison_agent", {"amount": 358500})

    chain.receipts[0].payload["severity"] = "LOW"

    result = chain.verify()
    assert not result.ok
    assert result.broken_at == 0
    assert "altered" in (result.reason or "")


def test_dropped_receipt_is_detected_as_a_sequence_gap():
    chain = ReceiptChain("case-1")
    chain.append("a", "agent", {})
    chain.append("b", "agent", {})
    chain.append("c", "agent", {})

    del chain.receipts[1]

    result = verify_chain(chain.receipts)
    assert not result.ok
    assert "sequence gap" in (result.reason or "")


def test_reordered_receipts_do_not_verify():
    chain = ReceiptChain("case-1")
    chain.append("a", "agent", {"n": 1})
    chain.append("b", "agent", {"n": 2})

    chain.receipts[0].seq, chain.receipts[1].seq = 1, 0

    assert not verify_chain(chain.receipts).ok


def test_forged_receipt_from_another_key_fails_signature_check():
    """Re-signing an edited receipt with a different key must not rescue it."""
    import base64

    from cryptography.hazmat.primitives import serialization

    chain = ReceiptChain("case-1")
    receipt = chain.append("triage.decision", "triage_agent", {"severity": "HIGH"})

    attacker_pem = base64.b64decode(generate_key_b64())
    attacker_key = serialization.load_pem_private_key(attacker_pem, password=None)
    attacker = Signer(private_key=attacker_key, ephemeral=True)  # type: ignore[arg-type]

    receipt.payload["severity"] = "LOW"
    receipt.hash = receipt.compute_hash()
    receipt.signature = attacker.sign(receipt.signing_input())
    # The attacker keeps the original public key on the receipt, as they would
    # have to — swapping it in is exactly what an auditor checks.

    assert not chain.verify().ok


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_signature_verifies_against_recorded_public_key():
    chain = ReceiptChain("case-1")
    receipt = chain.append("a", "agent", {"x": 1})
    assert verify(receipt.public_key, receipt.signing_input(), receipt.signature)
    assert not verify(receipt.public_key, b"different bytes", receipt.signature)


def test_loaded_key_is_not_marked_ephemeral():
    import base64

    from cryptography.hazmat.primitives import serialization

    pem = base64.b64decode(generate_key_b64())
    key = serialization.load_pem_private_key(pem, password=None)
    signer = Signer(private_key=key, ephemeral=False)  # type: ignore[arg-type]
    assert not signer.ephemeral
    assert len(base64.b64decode(signer.public_key_b64)) == 32
