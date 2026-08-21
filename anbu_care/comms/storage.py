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


def _bucket_name() -> str | None:
    value = os.getenv("ANBU_ARTIFACT_BUCKET")
    return value.strip() if value else None


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
            version="v4", expiration=SIGNED_URL_TTL, method="GET",
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "no link"
        return StoredArtifact(
            stored=False, url=None,
            detail=f"upload failed, no link exists: {type(exc).__name__}: {exc}"[:250],
        )

    return StoredArtifact(
        stored=True, url=url,
        detail=(
            f"uploaded to gs://{bucket_name}/artifacts/{filename} and signed for "
            f"{int(SIGNED_URL_TTL.total_seconds() // 60)} minutes"
        ),
        expires_in_seconds=int(SIGNED_URL_TTL.total_seconds()),
    )
