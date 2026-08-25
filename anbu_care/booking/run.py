"""Choosing a centre, trying it, and trying the next one when it fails.

This is the part that was asked for. Until now the diagnostics lane ranked
options and presented them, and nothing decided - which meant the system did the
easy half of the job and handed a seventy-one year old the hard half at 4am.

The choosing itself is deliberately dull: filter the ranked options by what the
family authorised, order by their stated preference, take the first, and record
WHY that one, the same way hospital routing already explains what it traded for
extra distance. A choice nobody can interrogate is not better than a list.

**The agentic part is the falling through.** Attempt, fail, record the failure
with its reason, try the next, up to the number of attempts the family allowed,
then hand it to a person with an account of everything that was tried. A lane
that tries one centre and gives up is a script. One that tries three, says it
tried three, and says the fourth needs a phone call, is doing the job.

Nothing here decides she needs a test, and nothing here spends.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from anbu_care import service
from anbu_care.booking import channels as channel_registry
from anbu_care.booking import disclosure, enforcer, otp
from anbu_care.booking import mandate as mandates
from anbu_care.comms import consent
from anbu_care.schemas import Appointment
from anbu_care.tools import whatsapp_tools

logger = logging.getLogger(__name__)

# How long the driver may hold a browser open waiting for somebody to read a
# text and type six digits. Shorter than the request's own life, so the code is
# always still valid when it arrives.
OTP_WAIT_SECONDS = 240


class BookingRefused(Exception):
    """Nothing was arranged, and the reason is safe to show."""


def choose(options: list[dict], mandate) -> list[dict]:
    """The centres worth trying, best first.

    Returns a LIST rather than one centre, because the next one matters as much
    as the first: this is the queue the fall-through walks.
    """
    permitted = [o for o in options
                 if float(o.get("distance_km") or 0.0) <= mandate.max_distance_km]
    if mandate.home_collection_only:
        permitted = [o for o in permitted if o.get("home_collection")]

    if mandate.prefer == "nearest":
        permitted.sort(key=lambda o: float(o.get("distance_km") or 0.0))
    else:
        permitted.sort(key=lambda o: (-float(o.get("score") or 0.0),
                                      float(o.get("distance_km") or 0.0)))
    return permitted


def why(centre: dict, mandate) -> str:
    """The sentence that makes the choice auditable."""
    basis = ("it is the nearest of the options that fit"
             if mandate.prefer == "nearest"
             else "it ranked highest of the options that fit")
    home = (" It is listed for home collection."
            if centre.get("home_collection") else "")
    return (f"{centre.get('name', 'this centre')} was tried first because "
            f"{basis}, at {float(centre.get('distance_km') or 0):.1f} km."
            f"{home} Anbu Care chose from its own search; no page suggested it.")


def _consented(parent_id: str) -> bool:
    """Her own agreement that her details may be given to a centre.

    Held by the PARENT, not by the family member who granted the mandate. The
    son may authorise Anbu Care to act; he cannot agree on her behalf to be
    entered into a lab's database.
    """
    profile = service.load_profile(parent_id)
    if profile is None:
        return False
    # `disclosure_consents`, where her own agreements live. Not `consents`,
    # which does not exist on a profile - a getattr default would have made
    # every booking refuse quietly and looked like the guard working.
    return consent.BOOKING_DISCLOSURE in (profile.disclosure_consents or {})


def arrange(*, case_id: str, order_id: str) -> dict:
    """Try to hold an appointment for one ordered test. Never raises upward.

    Returns what happened and, on every path, what was tried.
    """
    case = service.load_case(case_id)
    if case is None:
        raise BookingRefused(f"no case {case_id}")

    order = next((o for o in service.list_diagnostic_orders(case_id)
                  if o.order_id == order_id), None)
    if order is None:
        raise BookingRefused("no clinician ordered that test on this admission")

    mandate = mandates.live_for_case(case_id)
    profile = service.load_profile(case.parent_id)

    if not _consented(case.parent_id):
        return _escalate(case_id, order, mandate, [],
                         "she has not agreed to her details being given to a "
                         "diagnostic centre, so nothing was sent")

    options = list(order.options or [])
    if not options:
        return _escalate(case_id, order, mandate, [],
                         "no centres have been surfaced for this test yet")
    if mandate is None:
        return _escalate(case_id, order, None, [],
                         "nobody has authorised Anbu Care to hold an appointment")

    payload = disclosure.payload_for(
        name=profile.name if profile else "",
        age=getattr(profile, "age", "") if profile else "",
        phone=_number_for(profile),
        test_label=order.test_label,
        home_collection=False,
        # Sent only because real forms will not proceed without them, and only
        # from what is recorded. Never inferred: a gender read off a name is
        # wrong often enough to matter, and a pincode guessed from a city is a
        # collection van at the wrong door. Unset stays unset, and a form that
        # requires one refuses and says which.
        gender=getattr(profile, "gender", "") if profile else "",
        pincode=getattr(profile, "pincode", "") if profile else "",
    )

    queue = choose(options, mandate)
    existing = service.list_appointments(case_id)
    attempts: list[dict] = []
    drivers = channel_registry.available()

    for centre in queue[:mandate.max_attempts]:
        # The payload's home_collection follows the centre, so a home visit is
        # asked for as one rather than being a travel booking with a note.
        this_payload = {**payload, "home_collection": bool(centre.get("home_collection"))}
        driver = next((d for d in drivers if d.can_serve(centre)), drivers[0])

        # Recorded BEFORE the attempt. An instance that dies mid-booking must
        # leave evidence that something was tried, or a retry double-books.
        service.append_receipt(
            case_id, kind="booking.attempted", actor="booking",
            payload={"order_id": order_id, "place_id": centre.get("place_id"),
                     "channel": driver.name,
                     "note": ("An attempt to hold an appointment is starting. "
                              "Recorded before it runs so a crash cannot hide "
                              "it and a retry cannot double-book.")})

        def note(outcome, detail, failed_check=None):
            row = {"place_id": centre.get("place_id"), "name": centre.get("name"),
                   "channel": driver.name, "outcome": outcome, "detail": detail}
            if failed_check:
                row["failed_check"] = failed_check
            attempts.append(row)

        # PREPARE. Navigates, fills, reads - and submits nothing.
        try:
            prepared = driver.prepare(centre=centre, payload=this_payload)
        except Exception as exc:  # noqa: BLE001 - a driver fault is an outcome
            logger.exception("booking driver failed preparing %s", centre.get("name"))
            note(channel_registry.UNAVAILABLE,
                 f"the booking driver could not prepare this centre "
                 f"({type(exc).__name__})")
            continue

        # A channel that never reached the centre has nothing to enforce
        # against, and running the guards anyway made the lane lie: with no
        # driver configured every attempt came back "no way to cancel was
        # found", which is true of a request that was never made and is not the
        # reason. The reason a family is given has to be the real one.
        if not prepared.ready:
            note(prepared.outcome or channel_registry.UNAVAILABLE, prepared.detail)
            continue

        # DECIDE, on what prepare actually saw, while nothing has been sent.
        verdict = enforcer.decide(
            order=order, mandate=mandate, centre=centre, options=options,
            existing=existing, case_id=case_id,
            cancel_url=prepared.cancel_url, cancel_phone=prepared.cancel_phone,
            page_text=prepared.page_text, payload=this_payload)

        if not verdict.allowed:
            note("refused", verdict.reason, verdict.failed_check)
            logger.info("booking refused at %s: %s", centre.get("name"),
                        verdict.failed_check)
            continue

        # A CODE IS COMING. Ask the person in the room for it BEFORE the centre
        # sends it, so she is not handed six digits from a lab nobody told her
        # to expect - and so the request is on file before the reply can arrive.
        pending = None
        if getattr(prepared, "expects_otp", False):
            pending = _ask_for_a_code(case_id, order, centre, prepared)

        # COMMIT. Only now, and only because the guards allowed it.
        try:
            result = driver.commit(
                centre=centre, payload=this_payload, prepared=prepared,
                session_id=pending.session_id if pending else "",
                otp_wait_seconds=OTP_WAIT_SECONDS if pending else 0)
        except Exception as exc:  # noqa: BLE001
            logger.exception("booking driver failed committing %s", centre.get("name"))
            note(channel_registry.UNAVAILABLE,
                 f"the booking driver could not complete this centre "
                 f"({type(exc).__name__})")
            continue

        if pending is not None:
            otp.close(pending, outcome="used" if result.landed else "not_used")

        if not result.landed:
            note(result.outcome, result.detail)
            continue

        return _record(case_id, order, mandate, centre, driver, result,
                       verdict, attempts)

    return _escalate(case_id, order, mandate, attempts,
                     "every centre this system was allowed to try has been tried")


def _ask_for_a_code(case_id, order, centre, prepared):
    """Message whoever is with her, and open the request the reply belongs to.

    The care circle, not the son. He is asleep and cannot read a text sent to a
    phone in Thoothukudi; she is standing in the room. This is the hop the whole
    design says to hand to whoever is present, and it is the one place a booking
    genuinely needs a human - which is exactly what an OTP is for.

    Never raises. A message that could not be sent means nobody will answer,
    which the driver's own timeout already handles honestly.
    """
    from anbu_care.care_circle import notify as care_notify

    case = service.load_case(case_id)
    profile = service.load_profile(case.parent_id) if case else None
    circle = care_notify.care_circle(case.parent_id) if case else []
    # THE NEIGHBOUR, NOT THE SON. He is asleep eleven time zones away and
    # cannot read a text sent to a phone in Thoothukudi; she is in the room.
    # Taking the first name in the list asked him, because he is listed first
    # and holds outbound_notify like everybody else on it - the mechanism was
    # right and the ordering made it a no-op, which is the failure mode this
    # project keeps meeting.
    contact = next((c for c in circle if getattr(c, "role", "") == "care_circle"),
                   None) or next(iter(circle), None)

    request = otp.open_request(
        parent_id=case.parent_id, case_id=case_id, order_id=order.order_id,
        centre_name=str(centre.get("name") or ""),
        place_id=str(centre.get("place_id") or ""),
        asked_of=contact.name if contact else "")

    if contact is None:
        logger.info("no care circle contact to ask for a code")
        return request

    first = profile.name.split()[0] if profile and profile.name else "your parent"
    try:
        whatsapp_tools.send_family_update(
            case_id=case_id, parent_id=case.parent_id,
            to_e164=contact.whatsapp_e164,
            template_name="booking_code_needed",
            template_params={"parent_name": first,
                             "centre": str(centre.get("name") or "the centre"),
                             "minutes": str(OTP_WAIT_SECONDS // 60)},
            message_class="logistics",
            purpose_override=consent.OUTBOUND_NOTIFY)
    except Exception:  # noqa: BLE001 - the send has its own receipts
        logger.exception("could not ask the care circle for a code")
    return request


def _number_for(profile) -> str:
    """The number a centre would ring. Hers, not the family's.

    A centre calling back to confirm needs to reach the person attending. Giving
    them a number in Nashville would mean a confirmation call at 3am to somebody
    who cannot answer a question about her.
    """
    return getattr(profile, "whatsapp_e164", "") or ""


def _record(case_id, order, mandate, centre, driver, result, verdict,
            attempts) -> dict:
    appointment = Appointment(
        appointment_id=service.new_id("appt"), case_id=case_id,
        parent_id=order.parent_id, order_id=order.order_id,
        status=result.outcome, place_id=str(centre.get("place_id") or ""),
        centre_name=str(centre.get("name") or ""),
        centre_address=str(centre.get("address") or ""),
        distance_km=float(centre.get("distance_km") or 0.0),
        home_collection=bool(centre.get("home_collection")),
        channel=driver.name, cancel_url=result.cancel_url,
        cancel_phone=result.cancel_phone, slot_text=result.slot_text,
        evidence=result.evidence,
        provider_ref=result.provider_ref, mandate_id=mandate.mandate_id,
        guards_passed=verdict.passed, attempts=attempts,
        why_this_centre=why(centre, mandate),
        confirmed_at=(datetime.now(UTC)
                      if result.outcome == channel_registry.CONFIRMED else None),
    )
    service.save_appointment(appointment)

    service.append_receipt(
        case_id, kind=f"booking.{result.outcome}", actor="booking",
        payload={
            "appointment_id": appointment.appointment_id,
            "order_id": order.order_id,
            # A public Google identifier, not a fact about her. The TEST NAME is
            # not here, for the same reason it is not on the referral receipt:
            # /verify is public.
            "place_id": appointment.place_id,
            "channel": driver.name,
            "guards_passed": verdict.passed,
            "attempts_before_this": len(attempts),
            "cancellable": bool(result.cancel_url or result.cancel_phone),
            "note": ("An appointment was REQUESTED and the centre has not "
                     "confirmed it. That is not an appointment yet."
                     if result.outcome == channel_registry.REQUESTED else
                     "The centre confirmed a slot. Anbu Care did not choose "
                     "that she needs this test and did not pay for it."),
        })
    _tell_them_it_is_arranged(appointment)
    return {"outcome": result.outcome, "appointment_id": appointment.appointment_id,
            "centre": appointment.centre_name, "why": appointment.why_this_centre,
            "attempts": attempts, "cancel_url": result.cancel_url,
            "cancel_phone": result.cancel_phone}


def map_link(appointment) -> str:
    """Google's own link to the place, built from the place id.

    The id came from the Places search this system ran, so the link points at
    the centre it chose - not at whatever a page claimed to be. Same rule as
    the cancellation number: the counterparty does not get to say where this
    goes, even when "where" is a map pin.
    """
    if not appointment.place_id:
        return ""
    from urllib.parse import quote

    return ("https://www.google.com/maps/search/?api=1"
            f"&query={quote(appointment.centre_name or 'diagnostic centre')}"
            f"&query_place_id={appointment.place_id}")


def readable(name: str) -> str:
    """A centre's name as a person would say it.

    Google returns "AARTHI SCANS & LABS | TUTICORIN | DIAGNOSTIC CENTER",
    which is a database row with pipes in it. Nothing is dropped - a branch
    name matters when a chain has four in one town - it is just punctuated so
    somebody can read it aloud.
    """
    # Only a SPACED pipe is a separator. "Apollo 24|7" is a brand, and
    # splitting on every pipe turned it into "Apollo 24, 7" - a made-up name
    # for a real company, in a message telling somebody where to take their
    # mother.
    import re as _re

    parts = [p.strip() for p in _re.split(r"\s+\|\s+", name or "") if p.strip()]
    if not parts:
        return name or "the centre"
    tidy = [p.title() if p.isupper() else p for p in parts]
    return ", ".join(tidy)


def _status_line(appointment, first: str) -> str:
    """The opening sentence, and it must not overstate what happened.

    A confirmation and a callback request are different events and were being
    described in the same words. "The centre has not confirmed a time yet" is
    true of one and a lie about the other, and the whole card downstream is
    built on the reader believing this line.
    """
    if appointment.status == "confirmed":
        when = f" for {appointment.slot_text}" if appointment.slot_text else ""
        return (f"{first}'s test is BOOKED{when}. The centre confirmed it.")
    return (f"{first}'s test is requested. The centre has not confirmed a time "
            f"yet, and will be in touch.")


def _tell_them_it_is_arranged(appointment) -> None:
    """Say that a booking exists, and how to undo it.

    The template was written and never wired, so the lane made a real enquiry
    at a real clinic and nobody was told - the same shape as a payment that
    settles in silence. An agent that acts without saying so is not autonomous,
    it is unaccountable.

    Two people, for two different reasons. The person who is with her needs to
    know where to take her; the son needs to know it happened. He is told, not
    asked, which is his whole role in this lane.

    Never raises. The appointment is already made either way.
    """
    from anbu_care.care_circle import notify as care_notify

    profile = service.load_profile(appointment.parent_id)
    first = profile.name.split()[0] if profile and profile.name else "your parent"
    circle = care_notify.care_circle(appointment.parent_id)
    primary = next((c for c in (profile.family_contacts if profile else [])
                    if c.is_primary), None)

    # ONE MESSAGE PER HANDSET, not per person.
    #
    # Two people are told for two reasons - the one who is with her needs the
    # address, the son needs to know it happened - and on a shared phone that
    # arrived as the same message twice, a minute apart, with two different
    # short links to the same map pin.
    #
    # Deduping by NAME was the bug: they are different names. It is the same
    # lesson the handoff link learned in the opposite direction, where skipping
    # by number silenced a neighbour who shared the son's phone. A person is a
    # name; a place a message lands is a number.
    told: set[str] = set()
    for contact, purpose in (
            (next((c for c in circle
                   if getattr(c, "role", "") == "care_circle"), None),
             consent.OUTBOUND_NOTIFY),
            (primary, consent.STATUS_UPDATES)):
        if contact is None:
            continue
        handset = service.number_key(contact.whatsapp_e164)
        if handset in told:
            continue
        told.add(handset)
        try:
            whatsapp_tools.send_family_update(
                case_id=appointment.case_id, parent_id=appointment.parent_id,
                to_e164=contact.whatsapp_e164, template_name="booking_done",
                template_params={
                    "status_line": _status_line(appointment, first),
                    "centre": readable(appointment.centre_name),
                    "address": appointment.centre_address or "",
                    "map_line": (f"On the map: {map_link(appointment)}\n"
                                 if map_link(appointment) else ""),
                    "distance": f"{appointment.distance_km:.1f}",
                    "cancel": appointment.cancel_phone or appointment.cancel_url
                              or "the centre",
                },
                message_class="logistics", purpose_override=purpose)
        except Exception:  # noqa: BLE001 - the send has its own receipts
            logger.exception("could not say that a booking was made")


def _escalate(case_id, order, mandate, attempts, detail) -> dict:
    """Hand it to a person, with an account of everything that was tried."""
    service.append_receipt(
        case_id, kind="booking.escalated", actor="booking",
        payload={
            "order_id": getattr(order, "order_id", ""),
            "attempts": [{k: v for k, v in a.items() if k != "detail"}
                         for a in attempts],
            "attempt_count": len(attempts),
            "detail": detail,
            "note": ("Nothing was arranged and a person needs to ring. Every "
                     "centre tried is listed with what stopped it, so the "
                     "person picking this up starts where this left off."),
        })
    return {"outcome": "escalated", "detail": detail, "attempts": attempts}


def cancel(*, case_id: str, appointment_id: str, cancelled_by: str = "") -> dict:
    """Withdraw one. Records the path, because a person may have to use it.

    Phase 0 cannot cancel through a centre's own system, so this marks the
    record and hands back the way to do it. Saying an appointment is cancelled
    when only our row changed would be the exact lie this lane exists to avoid.
    """
    appointment = next((a for a in service.list_appointments(case_id)
                        if a.appointment_id == appointment_id), None)
    if appointment is None:
        raise BookingRefused(f"no appointment {appointment_id} on this case")
    if appointment.cancelled_at is not None:
        return {"outcome": "already_cancelled", "appointment_id": appointment_id}

    appointment.cancelled_at = datetime.now(UTC)
    appointment.status = "cancelled"
    service.save_appointment(appointment)

    service.append_receipt(
        case_id, kind="booking.cancelled", actor="family",
        payload={"appointment_id": appointment_id,
                 "place_id": appointment.place_id,
                 "cancelled_by": cancelled_by,
                 "note": ("Withdrawn on Anbu Care's record. The centre is told "
                          "by whoever holds the cancellation path below; this "
                          "system has not contacted them.")})
    return {"outcome": "cancelled", "appointment_id": appointment_id,
            "cancel_url": appointment.cancel_url,
            "cancel_phone": appointment.cancel_phone,
            "still_to_do": ("Ring or open the cancellation link to tell the "
                            "centre. Anbu Care has not done that.")}
