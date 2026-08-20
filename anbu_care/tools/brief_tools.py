"""Arrival brief tools.

The brief is composed in code. These tools hand the composed result to an agent
to relay — the agent chooses wording, never content. Read-only: composing a
brief never changes the case it describes.
"""

from __future__ import annotations

from typing import Any

from anbu_care.brief import compose_brief, render_brief_text


def get_arrival_brief(case_id: str) -> dict[str, Any]:
    """Compose the arrival brief for a case, from the signed receipt chain.

    Every line carries where it came from. Anything the recorded state does not
    contain comes back as `known: false` with a reason — those are facts about
    what is unknown, and they must be reported as unknown, not filled in.

    Args:
        case_id: The case to brief on.

    Returns:
        The structured brief, a plain-text rendering, and a count of unknowns.
    """
    brief = compose_brief(case_id)
    return {
        "status": "ok",
        "brief": brief.model_dump(mode="json"),
        "rendered": render_brief_text(brief),
        "unknown_count": brief.unknown_count,
        "as_of": brief.as_of.isoformat() if brief.as_of else None,
        "chain_verified": brief.chain_verified,
        "reporting_rules": (
            "Relay only what this brief contains. Any field with known=false is "
            "'not yet known' — say that plainly and do not substitute a likely "
            "value. State the 'as of' time; this is a snapshot of what has been "
            "recorded, not a live view."
        ),
    }
