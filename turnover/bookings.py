"""Booking calendars and the property state machine.

Hosts already have a machine-readable calendar: Airbnb, VRBO and Booking.com all export
an iCal feed per listing. Each VEVENT is a stay: DTSTART is the check-in date and DTEND
the checkout date (all-day, end-exclusive in the RFC sense, which is exactly checkout).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Booking, Property, State, parse_hhmm


def parse_ical(text: str, property_id: str) -> list[Booking]:
    from icalendar import Calendar

    cal = Calendar.from_ical(text)
    out: list[Booking] = []
    for comp in cal.walk("VEVENT"):
        start = comp.decoded("DTSTART")
        end = comp.decoded("DTEND") if comp.get("DTEND") else None
        if isinstance(start, datetime):
            start = start.date()
        if isinstance(end, datetime):
            end = end.date()
        if end is None or end <= start:
            continue
        summary = str(comp.get("SUMMARY", "Reservation"))
        # Airbnb publishes owner blocks as "Not available"/"Airbnb (Not available)": those are
        # not stays, nobody checks in or out, and no cleaner is needed.
        if "not available" in summary.lower() or "blocked" in summary.lower():
            continue
        uid = str(comp.get("UID") or f"{start}-{end}-{summary}")
        bid = "bk_" + hashlib.sha1(f"{property_id}:{uid}".encode()).hexdigest()[:12]
        out.append(Booking(id=bid, property_id=property_id, start=start, end=end, label=summary, source="ical"))
    return sorted(out, key=lambda b: b.start)


@dataclass
class Moment:
    """What the property is doing at an instant."""

    state: State
    booking: Booking | None = None  # current stay (occupied) or the stay that just ended (turnover)
    next_booking: Booking | None = None
    window_start: datetime | None = None  # turnover window
    window_end: datetime | None = None
    quiet_hours: bool = False


def _at(prop: Property, day: date, hhmm: str) -> datetime:
    return datetime.combine(day, parse_hhmm(hhmm), tzinfo=ZoneInfo(prop.timezone))


def checkin_dt(prop: Property, b: Booking) -> datetime:
    return _at(prop, b.start, prop.checkin_time)


def checkout_dt(prop: Property, b: Booking) -> datetime:
    return _at(prop, b.end, prop.checkout_time)


def turnover_window(prop: Property, ended: Booking, bookings: list[Booking]) -> tuple[datetime, datetime, Booking | None]:
    start = checkout_dt(prop, ended)
    nxt = next((b for b in sorted(bookings, key=lambda x: x.start) if b.start >= ended.end and b.id != ended.id), None)
    end = start + timedelta(hours=prop.turnover_hours)
    if nxt is not None:
        end = min(end, checkin_dt(prop, nxt))
    return start, end, nxt


def in_quiet_hours(prop: Property, t: datetime) -> bool:
    local = t.astimezone(ZoneInfo(prop.timezone)).time()
    qs, qe = parse_hhmm(prop.quiet_start), parse_hhmm(prop.quiet_end)
    return local >= qs or local < qe if qs > qe else qs <= local < qe


def moment(prop: Property, bookings: list[Booking], t: datetime) -> Moment:
    quiet = in_quiet_hours(prop, t)
    ordered = sorted(bookings, key=lambda b: b.start)
    for b in ordered:
        if checkin_dt(prop, b) <= t < checkout_dt(prop, b):
            nxt = next((x for x in ordered if x.start >= b.end and x.id != b.id), None)
            return Moment(State.OCCUPIED, booking=b, next_booking=nxt, quiet_hours=quiet)
    for b in ordered:
        ws, we, nxt = turnover_window(prop, b, ordered)
        if ws <= t < we:
            return Moment(State.TURNOVER, booking=b, next_booking=nxt, window_start=ws, window_end=we, quiet_hours=quiet)
    nxt = next((b for b in ordered if checkin_dt(prop, b) > t), None)
    return Moment(State.VACANT, next_booking=nxt, quiet_hours=quiet)
