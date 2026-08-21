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

OUTBOUND_PURPOSES = frozenset({
    ADMISSION_ALERTS, STATUS_UPDATES, BILLING_UPDATES, CLAIM_UPDATES, OUTBOUND_NOTIFY,
})
INBOUND_PURPOSES = frozenset({INBOUND_WELLBEING})
ALL_PURPOSES = OUTBOUND_PURPOSES | INBOUND_PURPOSES


def describe(purpose: str) -> str:
    """Plain wording for a consent screen or an audit trail."""
    return {
        ADMISSION_ALERTS: "be told when your parent is admitted",
        STATUS_UPDATES: "receive status updates about the case",
        BILLING_UPDATES: "receive billing summaries",
        CLAIM_UPDATES: "receive insurance claim updates",
        OUTBOUND_NOTIFY: "be notified, as a care-circle contact, where your parent was taken",
        INBOUND_WELLBEING: "send wellbeing check-ins about your parent",
    }.get(purpose, purpose)
