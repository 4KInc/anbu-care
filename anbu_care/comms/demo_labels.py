"""Who a message was addressed to, written on the front of it. Demo only.

The system sends to four different people. In a recording there is one handset,
so all four arrive in the same WhatsApp thread, in order, from the same number,
and the only thing that distinguishes the mother's Tamil check-in from the son's
English alert is a narrator saying so. The narration is the problem: it makes
the viewer take on trust the exact thing the demo is trying to show, which is
that these are separate people with separate consent and separate content.

So when ANBU_DEMO_ROLE_TAGS is on, every outbound message carries one line
naming its addressee. It is a caption on a screen recording, nothing more.

Three rules keep it from becoming a lie:

  IT IS OFF BY DEFAULT.        An unset variable means no tag, so nothing here
                               changes what a real family would receive.
  IT IS ADDED AFTER THE GATE.  The content gate rules on the message; this puts
                               a fixed string in front of what it permitted. It
                               can never carry content past a decision, because
                               it has no access to any.
  IT IS ON THE RECEIPT.        `demo_tag` is recorded with the delivery, so a
                               chain that includes a tagged message says the
                               message was tagged. A caption nobody can see in
                               the audit trail would be an undisclosed edit to
                               what left the platform.

The addressee is passed in by the caller rather than inferred from the number,
because in a one-handset demo every number is the same number, and inferring
from it would label the mother's check-in as the son's alert.
"""

from __future__ import annotations

import os

PARENT = "parent"
FAMILY = "family"
CARE_CIRCLE = "care_circle"
CLINICIAN = "clinician"

# Emoji rather than words alone: at video resolution, in a thread scrolling past
# at demo speed, the glyph is what the eye catches before it reads anything.
MARKS: dict[str, tuple[str, str]] = {
    PARENT:      ("\N{OLDER WOMAN}", "TO AMMA"),
    FAMILY:      ("\N{MOBILE PHONE}", "TO HER SON"),
    CARE_CIRCLE: ("\N{HOUSE BUILDING}", "TO THE NEIGHBOUR"),
    CLINICIAN:   ("\N{STETHOSCOPE}", "TO THE TREATING TEAM"),
}


def enabled() -> bool:
    """Off unless switched on, and only the affirmative words count."""
    return os.getenv("ANBU_DEMO_ROLE_TAGS", "off").strip().lower() in {
        "1", "on", "true", "yes"}


def tag_for(audience: str, recipient_name: str = "") -> str:
    """The one line, or empty when tagging is off or the audience is unknown.

    An unknown audience gets NO tag rather than a guessed one. A message
    captioned for the wrong person on a recording is worse than one captioned
    for nobody, because the viewer believes the caption.
    """
    if not enabled():
        return ""
    mark = MARKS.get((audience or "").strip().lower())
    if mark is None:
        return ""
    glyph, who = mark
    first = (recipient_name or "").strip().split(" ")[0]
    return f"{glyph} {who}, {first.upper()}" if first else f"{glyph} {who}"


def apply(body: str, audience: str, recipient_name: str = "") -> tuple[str, str]:
    """Return the body to send and the tag that was put on it.

    Both are returned because the caller has to record the second one. A
    function that quietly returned only the modified body would make the tag
    invisible to the receipt, which is the one thing this must not be.
    """
    tag = tag_for(audience, recipient_name)
    if not tag:
        return body, ""
    return f"{tag}\n\n{body}", tag
