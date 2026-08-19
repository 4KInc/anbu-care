"""`uv run python -m anbu_care.provenance.keygen` — mint a signing key."""

from anbu_care.provenance.signing import generate_key_b64

if __name__ == "__main__":
    print("ANBU_SIGNING_KEY_B64=" + generate_key_b64())
