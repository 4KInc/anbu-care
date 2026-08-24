"""Surfacing where a clinician-ordered test can be done.

What a present son does. The doctor says she needs a scan; he opens his phone,
finds the nearest places that do it, checks which one her insurance is likely
to be fine with, and sends the list. He does not diagnose her, he does not
decide from Nashville whether she is well enough to travel, and he does not
tell her the insurer will pay when he has not asked them.

Three walls, and they are the whole design:

1. **THE ORDER COMES FROM THE CLINICIAN.** Nothing here originates one. The
   test label is carried through exactly as it was recorded, and a referral
   with no order behind it is refused rather than invented.

2. **NO COVERAGE PROMISE.** Network status is read from the same seeded
   knowledge base hospital routing uses, and phrased in that module's own
   words: "is listed as empanelled with X", never "is covered". The
   adjudicator is simulated and its verdicts are about bills already
   incurred — stretching one into a promise about a scan nobody has had yet
   is how somebody pays for an MRI they were told was free.

3. **NO MOBILITY VERDICT.** Whether she can travel is a fact about a person in
   a room in Thoothukudi. If the clinician stated it, it is carried as stated.
   If not, both paths are shown and the sentence saying so is not decoration —
   it is the system declining to decide. Same wall the severity table holds
   when it calls its own output a routing decision and not a clinical one.

And one thing this module does NOT do: arrange anything. No centre here has
been contacted, no appointment exists, and no receipt may say a lab was
engaged. These are options, which is what the son sends.
"""

from __future__ import annotations

import logging

from anbu_care import service
from anbu_care.diagnostics import places as places_api
from anbu_care.kb.hospitals import KB_META, load_hospitals
from anbu_care.triage.routing import haversine_km

logger = logging.getLogger(__name__)

# What the family is told about coverage, everywhere, without exception.
CONFIRM_WITH_INSURER = "Confirm coverage with your insurer before you go."

# Mobility, exactly as the clinician left it. `UNKNOWN` is the default and is
# not a gap to be filled in later by inference.
AMBULATORY = "ambulatory"
NON_AMBULATORY = "non_ambulatory"
UNKNOWN = "unknown"

MOBILITY_UNSTATED = (
    "The clinician did not say whether she can travel to a centre, so both are "
    "shown. Which of these fits depends on whether she can travel, and the "
    "people with her decide that — Anbu Care does not."
)
MOBILITY_NON_AMBULATORY = (
    "The clinician recorded that she cannot travel to a centre. Home collection "
    "is shown first for that reason; the travel options remain listed because "
    "the people with her may know something this record does not."
)

NOT_ARRANGED = (
    "Nothing here is booked. Anbu Care is not connected to any of these "
    "centres and has not contacted them — these are places the test could be "
    "done, for somebody to ring."
)

# Distance is the only term with a real measurement behind it, so it carries
# the most weight. The other two are read off a seeded file and are tie-breaks
# rather than reasons on their own.
WEIGHT_DISTANCE = 0.6
WEIGHT_TEST_MATCH = 0.25
WEIGHT_NETWORK = 0.15

# Beyond this a centre is not "nearby" in any useful sense for a 71-year-old.
FAR_KM = 15.0

# And beyond THIS it is not an option at all, whatever Places thinks. The
# location bias is a bias: a live search once put a lab in Noida, 2,205 km
# away, at the top of a list headed "nearby". Anchoring the query to the city
# fixed that, but a query heuristic is not a guarantee — this is. Anything
# farther is dropped before a family ever sees it.
MAX_DISTANCE_KM = 25.0


class ReferralRefused(Exception):
    """No options were surfaced, and the reason is safe to show."""


def _network_note(centre_name: str, insurer: str | None) -> tuple[bool, str]:
    """Network status for a centre, or an honest admission that we do not know.

    Diagnostic centres are NOT in the hospital knowledge base — that file lists
    five hospitals. So for almost every centre Places returns, the truthful
    answer is that we have nothing, and that is what is said. Where a centre
    matches a hospital already on file, the routing module's exact phrasing is
    reused rather than a second, softer one being invented here.
    """
    if not insurer:
        return False, "No insurer on file to check a network against."

    match = next((h for h in load_hospitals()
                  if h.name.strip().lower() == centre_name.strip().lower()), None)
    if match is None:
        return False, (f"Anbu Care has no network information for this centre. "
                       f"{CONFIRM_WITH_INSURER}")

    if insurer in match.empanelled_insurers:
        # routing.py's words, deliberately: "listed as", not "is".
        return True, (f"{match.name} is listed as empanelled with {insurer}. "
                      f"{CONFIRM_WITH_INSURER}")
    return False, (f"{match.name} is not listed as empanelled with {insurer}. "
                   f"{CONFIRM_WITH_INSURER}")


def _offers_test(centre: places_api.Centre) -> tuple[bool, str]:
    """Whether this looks like somewhere the test is done.

    A weak signal and labelled as one. Places tells us a category, not a
    catalogue: `medical_clinic` is not the same as "runs this assay". Saying
    "offers this test" from a category string would be a claim the data cannot
    support, so it says what it actually knows.
    """
    kind = (centre.primary_type or "").lower()
    if kind in {"medical_lab", "diagnostic_center"}:
        return True, "Listed as a diagnostic centre."
    if kind in {"medical_clinic", "hospital", "doctor", "pharmacy"}:
        return False, ("Listed as a medical facility. Whether it runs this "
                       "particular test is not something this search can tell.")
    return False, ("Category unknown. Whether it runs this particular test is "
                   "not something this search can tell.")


def _score(distance_km: float, offers: bool, in_network: bool) -> float:
    nearness = max(0.0, 1.0 - (distance_km / FAR_KM))
    return round(WEIGHT_DISTANCE * nearness
                 + WEIGHT_TEST_MATCH * (1.0 if offers else 0.0)
                 + WEIGHT_NETWORK * (1.0 if in_network else 0.0), 4)


def options_for(*, test_label: str, lat: float, lon: float,
                insurer: str | None = None, city: str = "") -> dict:
    """Rank what a search found. Presentation only; nothing is decided here."""
    found = places_api.search(test_label=test_label, lat=lat, lon=lon, city=city)
    if not found.ok:
        raise ReferralRefused(found.detail or "no diagnostic centres were found")

    options = []
    dropped = 0
    for centre in found.centres:
        distance = round(haversine_km(lat, lon, centre.lat, centre.lon), 2)
        if distance > MAX_DISTANCE_KM:
            dropped += 1
            continue
        offers, offers_note = _offers_test(centre)
        in_network, network_note = _network_note(centre.name, insurer)
        options.append({
            "place_id": centre.place_id,
            "name": centre.name,
            "address": centre.address,
            "distance_km": distance,
            "offers_test": offers,
            "offers_test_note": offers_note,
            # NOTE: there is no `covered` field, and there must never be one.
            # A boolean called anything like it would be read as a promise by
            # the first person who saw it, whatever the label beside it said.
            "in_network_listed": in_network,
            "network_note": network_note,
            "home_collection": centre.home_collection,
            "score": _score(distance, offers, in_network),
            "why": (f"{distance:.1f} km from the hospital. {offers_note} "
                    f"{network_note}"),
        })

    options.sort(key=lambda o: (-o["score"], o["distance_km"]))
    if not options:
        raise ReferralRefused(
            f"nothing within {MAX_DISTANCE_KM:.0f} km of the hospital came back "
            f"for that test" + (f"; {dropped} result(s) were farther away and "
                                f"were not offered" if dropped else ""))
    return {
        "options": options,
        "dropped_as_too_far": dropped,
        "source": found.source,
        "source_label": found.source_label,
        "kb_provenance": KB_META().get("capability_status", ""),
        "not_arranged": NOT_ARRANGED,
    }


def group_by_mobility(options: list[dict], mobility: str) -> dict:
    """Two labelled groups, and a sentence about who decides between them.

    Home collection is not knowable from Places, so that group is empty far
    more often than not — and an empty group with an honest label is better
    than quietly filtering the list down and letting somebody assume the ones
    remaining will come to the house.
    """
    travel = [o for o in options if not o.get("home_collection")]
    home = [o for o in options if o.get("home_collection")]

    if mobility == NON_AMBULATORY:
        note = MOBILITY_NON_AMBULATORY
    elif mobility == AMBULATORY:
        note = ("The clinician recorded that she can travel to a centre, so "
                "these are the places to ring.")
    else:
        note = MOBILITY_UNSTATED

    return {
        "mobility_as_stated": mobility,
        "mobility_note": note,
        "travel": travel,
        "home_collection": home,
        "home_collection_note": (
            "No centre in this list is known to offer home collection. That is "
            "an absence of information, not a confirmed no — ring and ask."
            if not home else
            "These are listed as offering home collection."),
    }


def record(*, case_id: str, order_id: str, test_label: str, surfaced: dict,
           grouped: dict) -> str:
    """Put the referral on the chain. Ids and counts, never clinical content.

    The test label is NOT on the receipt. `/verify` is public and proves
    integrity without revealing anything, and "she was sent for a troponin
    test" is exactly the sort of thing it must not leak. What goes on is that a
    referral happened, how many options, from which source, and which places —
    a place id is a public Google identifier, not a fact about her.
    """
    receipt = service.append_receipt(
        case_id,
        kind="diagnostic.referral",
        actor="diagnostic_referral",
        payload={
            "order_id": order_id,
            "option_count": len(surfaced["options"]),
            "option_place_ids": [o["place_id"] for o in surfaced["options"]],
            "source": surfaced["source"],
            "source_label": surfaced["source_label"],
            "mobility_as_stated": grouped["mobility_as_stated"],
            "note": (
                "Options were surfaced for a test a clinician ordered. Anbu "
                "Care did not order the test, did not decide whether she can "
                "travel, has not contacted any of these centres, and makes no "
                "claim about what an insurer will pay."
            ),
        },
    )
    logger.info("diagnostic referral recorded for %s (%s options)",
                case_id, len(surfaced["options"]))
    return receipt.receipt_id
