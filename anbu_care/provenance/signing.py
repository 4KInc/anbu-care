"""Ed25519 signing for provenance receipts.

Independently reimplemented for this hackathon. The hash-chain + signed-receipt
pattern is prior work of ours (GenuProof); see DISCLOSURE.md.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from anbu_care.config import settings


@dataclass(frozen=True)
class Signer:
    private_key: ed25519.Ed25519PrivateKey
    ephemeral: bool

    @property
    def public_key_b64(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode()

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self.private_key.sign(payload)).decode()


def verify(public_key_b64: str, payload: bytes, signature_b64: str) -> bool:
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        pub.verify(base64.b64decode(signature_b64), payload)
    except Exception:
        return False
    return True


@lru_cache(maxsize=1)
def load_signer() -> Signer:
    """Load the signing key from env, or mint an ephemeral dev key.

    An ephemeral key means chains cannot be verified across process restarts —
    fine for local runs, never for a demo recording or deployment.
    """
    encoded = settings().signing_key_b64
    if encoded:
        pem = base64.b64decode(encoded)
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise TypeError("ANBU_SIGNING_KEY_B64 must hold an Ed25519 private key")
        return Signer(private_key=key, ephemeral=False)
    return Signer(private_key=ed25519.Ed25519PrivateKey.generate(), ephemeral=True)


def generate_key_b64() -> str:
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode()
