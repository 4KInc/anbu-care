"""The clinician handoff — what a treating team needs, and nothing else.

Anbu Care is the record. It is not the doctor. Everything in this package
reports what is stored about a parent; nothing in it suggests what to do about
any of it.
"""

from anbu_care.handoff.summary import (
    NOT_ON_FILE,
    compose_emergency_summary,
    render_summary_text,
)

__all__ = ["NOT_ON_FILE", "compose_emergency_summary", "render_summary_text"]
