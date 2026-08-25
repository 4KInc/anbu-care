"""Every consent purpose the system recognises, named in one place.

DPDP requires purpose-specific consent: agreeing to one thing is not agreeing
to another. That only holds if each purpose has its own name, and the fastest
way to lose it is to reach for an existing constant that sounds close enough.

That is exactly what happened here. Inbound wellbeing originally reused
"status_updates", which is the purpose for *sending* a family member status
messages. The result was that consenting to receive updates about your parent
silently made you eligible to file reports about them — one flag standing for
two different agreements, in opposite directions.

Hence this module. A purpose belongs here before it is used anywhere, and the
direction is part of its name, because "status_updates" not saying which way it
pointed is what made the collapse invisible.
"""

from __future__ import annotations

# ---- outbound: things we may SEND to a person ----------------------------
# The family decision-maker's stream. Unchanged.
ADMISSION_ALERTS = "admission_alerts"
STATUS_UPDATES = "status_updates"
BILLING_UPDATES = "billing_updates"
CLAIM_UPDATES = "claim_updates"

# The care circle's narrower stream: a neighbour or a listed doctor may be told
# where the parent was taken without being sent the family's billing and claim
# traffic. Distinct from ADMISSION_ALERTS on purpose — being told once, as a
# notified party, is not the same agreement as receiving the case feed.
OUTBOUND_NOTIFY = "outbound_notify"

# ---- inbound: things a person may SEND to us -----------------------------
# May this person file a wellbeing check-in about the parent. Nothing to do
# with what they may receive.
INBOUND_WELLBEING = "inbound_wellbeing"

# ---- outbound TO THE PARENT herself --------------------------------------
# A fourth direction, and it has to be one for the same reason the first three
# do. Every purpose above is held by a FAMILY MEMBER about their own traffic:
# what the son may be sent, what the son may file. This one is held by the
# PARENT, about messages sent to HER.
#
# Recovery check-ins are the first thing this system has ever sent her. Until
# now the only text that reached her was a reply to something she sent, so
# there was no outbound-to-parent agreement to hold, and nothing existing comes
# close enough to borrow:
#
#   status_updates etc.  belong to a family contact, about their own feed. She
#                        is not a family contact and holds none of them.
#   inbound_wellbeing    points the other way. Reusing it would repeat exactly
#                        the collapse this module was written to undo.
#   emergency_clinical_share  is disclosure of her record to a third party.
#                        Messaging her is not that.
#
# So it lives here, on her profile, in `contact_consents` — deliberately NOT in
# `disclosure_consents`, which is the disclosure direction. Two directions
# sharing one dict is how the distinction gets lost.
RECOVERY_CHECKINS = "recovery_checkins"

# ---- disclosure: showing the parent's OWN RECORD to a third party --------
# A third direction, and it needs to be one. The two above are both about
# messages to or from a person. This is about handing someone the record
# itself, which is a different act with a different subject: the others are
# agreements held by a family member about their own traffic, and this one is
# the PARENT's agreement about her own data.
#
# It is deliberately not reachable from any outbound message purpose. Being
# willing to receive claim updates is not agreeing that a stranger in a
# hospital corridor may read your allergies, and if those two ever share a
# flag the collapse will be invisible in exactly the way status_updates was.
EMERGENCY_CLINICAL_SHARE = "emergency_clinical_share"

# Giving her details OUT to a third party who will hold them, which is a
# different act again from showing a clinician her record at the bedside.
#
# A diagnostic centre asked to hold a slot needs a name and a number, and once
# it has them it keeps them, on its own systems, under its own policy, after
# the appointment is over. That is not a disclosure that ends when the browser
# closes. Being willing to have a treating team read her allergies in a
# corridor is not agreeing to be entered into a lab's customer database, and
# folding the two together would be exactly the collapse EMERGENCY_CLINICAL_SHARE
# was split out to prevent.
BOOKING_DISCLOSURE = "booking_disclosure"

OUTBOUND_PURPOSES = frozenset({
    ADMISSION_ALERTS, STATUS_UPDATES, BILLING_UPDATES, CLAIM_UPDATES, OUTBOUND_NOTIFY,
})
INBOUND_PURPOSES = frozenset({INBOUND_WELLBEING})
DISCLOSURE_PURPOSES = frozenset({EMERGENCY_CLINICAL_SHARE, BOOKING_DISCLOSURE})
# Held by the parent, about what may be sent to her. Kept as its own set so a
# loop over "the outbound purposes" cannot silently start treating a family
# member's agreement as hers.
PARENT_OUTBOUND_PURPOSES = frozenset({RECOVERY_CHECKINS})
ALL_PURPOSES = (
    OUTBOUND_PURPOSES | INBOUND_PURPOSES | DISCLOSURE_PURPOSES | PARENT_OUTBOUND_PURPOSES
)


def describe(purpose: str) -> str:
    """Plain wording for a consent screen or an audit trail."""
    return {
        ADMISSION_ALERTS: "be told when your parent is admitted",
        STATUS_UPDATES: "receive status updates about the case",
        BILLING_UPDATES: "receive billing summaries",
        CLAIM_UPDATES: "receive insurance claim updates",
        OUTBOUND_NOTIFY: "be notified, as a care-circle contact, where your parent was taken",
        INBOUND_WELLBEING: "send wellbeing check-ins about your parent",
        BOOKING_DISCLOSURE: (
            "have your name, age and a contact number given to a diagnostic "
            "centre so an appointment can be held for you"
        ),
        RECOVERY_CHECKINS: (
            "be sent recovery check-in messages after you come home, asking how "
            "you are and whether you took your medicines"
        ),
        EMERGENCY_CLINICAL_SHARE: (
            "allow your allergies, conditions, medication and recent results to "
            "be shown to a treating clinician in an emergency"
        ),
    }.get(purpose, purpose)
