"""Where a generated artifact lives long enough for a provider to fetch it.

WhatsApp providers do not accept bytes. They accept a URL and go and fetch it,
which means the artifact has to be reachable from the public internet for a
moment. A V4 signed URL is how that happens without the object being public:
the link carries its own time-limited authorisation and stops working when it
expires. The bucket itself stays closed.

The TTL is deliberately short. It only has to outlive one fetch by Twilio, not
the family's attention span — the durable way to read the document is the
dashboard, behind the auth boundary, which is where the link in every message
points.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

# Long enough for a provider fetch and a retry, short enough that a leaked link
# is worth little.
SIGNED_URL_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class StoredArtifact:
    stored: bool
    url: str | None
    detail: str
    expires_in_seconds: int | None = None
    object_name: str | None = None


def _bucket_name() -> str | None:
    value = os.getenv("ANBU_ARTIFACT_BUCKET")
    return value.strip() if value else None


METADATA_EMAIL_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/email"
)


def _runtime_service_account() -> str | None:
    """Ask the metadata server who we are actually running as.

    On Cloud Run, google.auth reports the service account email as the literal
    string "default" — that is the metadata server's alias for the attached
    account, not an address. Handing it to the IAM signBytes API fails with
    "Invalid form of account ID default", so the real address has to be
    resolved before signing.
    """
    import requests

    try:
        response = requests.get(
            METADATA_EMAIL_URL, headers={"Metadata-Flavor": "Google"}, timeout=2,
        )
    except Exception:  # noqa: BLE001 - not on GCP, or no metadata server
        return None
    email = response.text.strip() if response.ok else ""
    return email if "@" in email else None


def _signing_kwargs() -> dict[str, str]:
    """How to sign, given whatever credentials we happen to be running under.

    A service account with a private key can sign locally. Anything else — a
    developer's Application Default Credentials, or Cloud Run's attached
    account — has only a token, so signing goes through the IAM signBytes API,
    which needs the signer named as a real email address.
    """
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default()
    if getattr(creds, "signer", None) is not None and getattr(creds, "signer_email", None):
        return {}  # has a private key; the library signs directly

    email = getattr(creds, "service_account_email", None)
    if not email or email == "default" or "@" not in email:
        email = _runtime_service_account() or os.getenv("ANBU_SIGNER_SERVICE_ACCOUNT")

    if not email:
        raise RuntimeError(
            "cannot sign: credentials carry no private key and no signer address could "
            "be resolved. Set ANBU_SIGNER_SERVICE_ACCOUNT to a service account you may "
            "impersonate."
        )
    if not creds.valid:
        creds.refresh(google.auth.transport.requests.Request())
    return {"service_account_email": email, "access_token": creds.token}


def store(filename: str, data: bytes, content_type: str = "application/pdf") -> StoredArtifact:
    """Upload and return a short-lived signed URL, or say plainly that it did not.

    Same discipline as the transport: no bucket, no upload, no pretending. A
    caller that gets stored=False must not go on to claim an attachment was
    sent.
    """
    bucket_name = _bucket_name()
    if not bucket_name:
        return StoredArtifact(
            stored=False, url=None,
            detail="ANBU_ARTIFACT_BUCKET is not set; nothing was uploaded and no link exists.",
        )

    try:
        from google.cloud import storage as gcs

        client = gcs.Client()
        blob = client.bucket(bucket_name).blob(f"artifacts/{filename}")
        blob.upload_from_string(data, content_type=content_type)
        url = blob.generate_signed_url(
            version="v4", expiration=SIGNED_URL_TTL, method="GET", **_signing_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "no link"
        return StoredArtifact(
            stored=False, url=None,
            detail=f"upload failed, no link exists: {type(exc).__name__}: {exc}"[:250],
        )

    return StoredArtifact(
        stored=True, url=url, object_name=f"artifacts/{filename}",
        detail=(
            f"uploaded to gs://{bucket_name}/artifacts/{filename} and signed for "
            f"{int(SIGNED_URL_TTL.total_seconds() // 60)} minutes"
        ),
        expires_in_seconds=int(SIGNED_URL_TTL.total_seconds()),
    )
