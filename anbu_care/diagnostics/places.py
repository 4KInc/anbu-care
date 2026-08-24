"""Finding where a clinician-ordered test can actually be done.

The son in Nashville opens his phone and searches for a lab near his mother's
hospital. This is that search, and it is a real one: Google Places, live, over
the network, returning real Thoothukudi diagnostic centres with real place ids.

`ANBU_DIAGNOSTICS_MODE` picks:

  places   a real Places API (New) text search near the hospital.
  seeded   no call. The hospitals already on file that offer diagnostics,
           which is a much thinner list and says so.
  off      refuse to search at all.

The mode is reported on every surface for the same reason the settlement rails
are: "here are the nearest labs" from a live search and from a seeded snapshot
are different claims, and a family deciding where to send a seventy-one year
old is entitled to know which one they are reading.

**This module finds places. It does not book them.** There is no call here that
reserves a slot, and nothing downstream may say a lab was engaged — Anbu Care
is not integrated with any of these centres and cannot be. It is doing what the
son does: looking up where the test can be done and sending the list.

The key is deliberately its own variable. `ANBU_MAPS_API_KEY` is a browser key
with referrer restrictions, and Places refuses it outright — "API keys with
referer restrictions cannot be used with this API". Using one credential for
both looked tidy and did not work.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
TIMEOUT_SECONDS = 20
SEARCH_RADIUS_M = 12_000.0
MAX_RESULTS = 8

FIELD_MASK = ("places.id,places.displayName,places.formattedAddress,"
              "places.location,places.primaryType,places.businessStatus")

LIVE_LABEL = ("Found by a live Google Places search near the hospital, at the "
              "time this was recorded.")
SEEDED_LABEL = ("From the seeded hospital knowledge base, not a live search. "
                "It is a short list and may not include the nearest option.")
OFF_LABEL = "Searching for diagnostic centres is switched off on this deployment."


@dataclass(frozen=True)
class Centre:
    """A place a test might be done. Not a booking, and not a promise."""

    place_id: str
    name: str
    address: str
    lat: float
    lon: float
    primary_type: str = ""
    # Whether this centre offers HOME COLLECTION, when that is knowable. Places
    # does not carry it, so it stays None rather than being guessed: a guess
    # here decides whether somebody who cannot travel is offered anything, and
    # inventing a yes is worse than admitting the gap.
    home_collection: bool | None = None


@dataclass(frozen=True)
class SearchResult:
    ok: bool
    centres: list[Centre] = field(default_factory=list)
    source: str = "none"
    source_label: str = ""
    detail: str = ""


def mode() -> str:
    return os.getenv("ANBU_DIAGNOSTICS_MODE", "seeded").strip().lower()


def configured() -> bool:
    return bool(os.getenv("ANBU_PLACES_API_KEY"))


def source() -> str:
    """Which source a search would use, named for what it is."""
    if mode() == "off":
        return "off"
    if mode() == "places" and configured():
        return "google_places_live"
    return "seeded_kb"


def source_label() -> str:
    return {"off": OFF_LABEL,
            "google_places_live": LIVE_LABEL}.get(source(), SEEDED_LABEL)


def _query_for(test_label: str, city: str = "") -> str:
    """What the son would type. The clinician's words, plus where to look.

    The test label is passed through as the clinician wrote it. Rewriting it
    into something that searches better would be this system deciding what was
    ordered, which is the one thing it must not do.

    The CITY is appended, and it is load-bearing. A location bias is a bias,
    not a restriction: searching "diagnostic centre laboratory Troponin I
    (repeat)" biased to Thoothukudi returned one result, a lab in Noida, 2,205
    km away — a national brand matching the assay name. Naming the city turns
    that into eight local centres, all under four kilometres, with the
    clinician's words untouched.
    """
    cleaned = " ".join((test_label or "").split())[:120]
    where = " ".join((city or "").split())[:60]
    return " ".join(p for p in ("diagnostic centre laboratory", cleaned, where) if p)


def search(*, test_label: str, lat: float, lon: float,
           city: str = "") -> SearchResult:
    """Centres near this point that might do this test. Never raises.

    A failure is an answer: an empty list with a reason beats a list that
    quietly came from somewhere other than where it claims.
    """
    current = mode()
    if current == "off":
        return SearchResult(ok=False, source="off", source_label=OFF_LABEL,
                            detail=OFF_LABEL)

    if current == "places" and configured():
        result = _search_places(test_label=test_label, lat=lat, lon=lon, city=city)
        if result.ok:
            return result
        # A live search that failed does NOT silently become a seeded list
        # wearing a live label. It returns the failure, and the caller says so.
        return result

    return SearchResult(ok=False, source="seeded_kb", source_label=SEEDED_LABEL,
                        detail="no live search is configured on this deployment")


def _search_places(*, test_label: str, lat: float, lon: float,
                   city: str = "") -> SearchResult:
    payload = {
        "textQuery": _query_for(test_label, city),
        "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon},
                                    "radius": SEARCH_RADIUS_M}},
        "maxResultCount": MAX_RESULTS,
    }
    request = urllib.request.Request(
        SEARCH_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "X-Goog-Api-Key": os.environ["ANBU_PLACES_API_KEY"],
                 "X-Goog-FieldMask": FIELD_MASK})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        detail = f"the diagnostic search failed: HTTP {error.code}"
        logger.warning("%s", detail)
        return SearchResult(ok=False, source="google_places_live",
                            source_label=LIVE_LABEL, detail=detail)
    except Exception as exc:  # noqa: BLE001 - a failed search is an outcome
        detail = f"the diagnostic search failed: {type(exc).__name__}"
        logger.warning("%s", detail)
        return SearchResult(ok=False, source="google_places_live",
                            source_label=LIVE_LABEL, detail=detail)

    centres = [c for c in (_centre_from(p) for p in body.get("places", [])) if c]
    return SearchResult(
        ok=bool(centres), centres=centres, source="google_places_live",
        source_label=LIVE_LABEL,
        detail=(f"{len(centres)} centres found within "
                f"{SEARCH_RADIUS_M / 1000:.0f} km" if centres else
                "the search returned nothing near the hospital"))


def _centre_from(place: dict) -> Centre | None:
    location = place.get("location") or {}
    place_id = str(place.get("id") or "")
    name = ((place.get("displayName") or {}).get("text") or "").strip()
    if not place_id or not name:
        return None
    # A closed business is not an option. Places says so; passing it on as one
    # would send somebody to a locked door.
    if str(place.get("businessStatus") or "").upper() == "CLOSED_PERMANENTLY":
        return None
    try:
        lat = float(location["latitude"])
        lon = float(location["longitude"])
    except (KeyError, TypeError, ValueError):
        return None

    return Centre(place_id=place_id, name=name,
                  address=str(place.get("formattedAddress") or "").strip(),
                  lat=lat, lon=lon,
                  primary_type=str(place.get("primaryType") or ""))
