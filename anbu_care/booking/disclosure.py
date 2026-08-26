"""What a diagnostic centre is told about her, and nothing else.

Everywhere else in this system the privacy question is "what may leave over
WhatsApp" or "what may go on a public chain". This is the first place the answer
lands in somebody else's database. A centre asked to hold a slot needs a name
and a number, and once it has them it keeps them - on its own systems, under its
own policy, after the appointment is over. There is no revoking that.

So the rule is a WHITELIST checked against the payload that is actually about to
be sent, not a blacklist of things we remembered to strip. A blacklist fails
open: the day somebody adds a field to the form map, it goes out. This fails
closed, and the test that guards it reads the payload rather than the intention.

The line is drawn at what is needed to hold a slot:

  PERMITTED   her name, her age, a contact number, the test as the clinician
              worded it, whether it is a home collection, and - because real
              booking forms will not proceed without them - her gender and the
              pincode of the address a home collection would come to.

  REFUSED     allergies, conditions, medications, the policy number, the
              insurer, the case id, the hospital she was admitted to, and
              anything from the clinician's note beyond the test label itself.

Age is in and date of birth is out, deliberately: a centre needs to know she is
seventy-one because some tests are prepared differently for the elderly, and it
never needs the day she was born. The narrowest thing that does the job.

An EMAIL ADDRESS is the uncomfortable one, and it is here because without it
this lane cannot book anywhere in India. Every centre driven so far - DLABS in
Thoothukudi, Anderson in Chennai - makes it a required field. It is also
different in kind from the others: a name or an age is a fact about her that a
lab already needs, and an email is a CHANNEL. Whoever holds it can reach her
for ever, and she cannot take it back.

So it is a separate field from the family contact's address that Google sign-in
is matched against, it is set deliberately and never derived, and an unset value
refuses the form rather than substituting one somebody used for something else.
A family that would rather give the son's address than the patient's can, and
should.

Gender and pincode were added for the same reason and only after a real centre
refused without them - Aarthi Scans in Thoothukudi will not take a booking with
the gender radio empty. They are not comfortable fields to hand over and they
are here because the alternative is a lane that cannot book anywhere, not
because they seemed harmless. Both are a genuine part of holding a slot: a
gender because some tests are prepared differently, a pincode because a home
collection has to find the house. Neither is ever inferred - an unset value
means the form is refused and the refusal names the field.

The test label is the uncomfortable one, and it is permitted because it has to
be - you cannot book a test without naming it. It is the single piece of
clinical detail this lane discloses, it is the clinician's own wording carried
through unrewritten, and it is why this needs its own consent purpose rather
than borrowing one somebody granted for something else.
"""

from __future__ import annotations

ALLOWED_FIELDS = frozenset({
    "name",
    "age",
    "phone",
    "test_label",
    "home_collection",
    "gender",
    "pincode",
    "email",
})

# Named individually rather than caught by a rule, so that adding a field to the
# payload and adding it here are two separate decisions somebody has to make.
NEVER_DISCLOSE = frozenset({
    "allergies", "conditions", "chronic_conditions", "medications",
    "policy_number", "insurer", "sum_insured_inr",
    "case_id", "parent_id", "order_id", "note", "clinician_note",
    "hospital", "admitted_to", "diagnosis", "dob", "date_of_birth",
    "family_contact", "son_phone",
})


class DisclosureRefused(Exception):
    """The payload carried something that may not leave. Nothing was sent."""


def check(payload: dict) -> None:
    """Refuse a payload carrying anything not on the whitelist.

    Raises rather than filtering. Silently dropping a field would let a caller
    believe it had been sent and let the next reader believe the whitelist is
    advisory; a refusal makes somebody look at why they added it.
    """
    offending = sorted(set(payload) - ALLOWED_FIELDS)
    if offending:
        raise DisclosureRefused(
            "this booking would have disclosed " + ", ".join(offending)
            + " to a diagnostic centre, which is not on the permitted list"
        )


def payload_for(*, name: str, age: int | str, phone: str, test_label: str,
                home_collection: bool, gender: str = "",
                pincode: str = "", email: str = "") -> dict:
    """The only way this lane builds what it sends.

    A single constructor because the guarantee is about the payload's shape, and
    a guarantee that depends on every caller assembling a dict correctly is not
    a guarantee.
    """
    payload = {
        "name": (name or "").strip(),
        "age": str(age or "").strip(),
        "phone": (phone or "").strip(),
        # The clinician's wording, carried through. Rewriting it into something
        # a booking form parses more easily would be this system deciding what
        # was ordered, which is the wall the whole diagnostics lane stands on.
        "test_label": (test_label or "").strip(),
        "home_collection": bool(home_collection),
        # Empty when unrecorded, and empty is meaningful: the driver refuses a
        # form that requires one rather than putting a guess on her record.
        "gender": (gender or "").strip().lower(),
        "pincode": (pincode or "").strip(),
        "email": (email or "").strip(),
    }
    check(payload)
    return payload
