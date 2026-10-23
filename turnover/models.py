"""Domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from enum import Enum
from typing import Any


class State(str, Enum):
    VACANT = "vacant"
    OCCUPIED = "occupied"
    TURNOVER = "turnover"


class Severity(str, Enum):
    URGENT = "urgent"  # act now: no-show, activity at a vacant home, blind spot before check-in
    WARN = "warn"  # look soon: possible gathering, short turnover
    INFO = "info"  # good to know: package at a vacant home
    GOOD = "good"  # business confirmations: cleaner arrived, turnover verified, guests in


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@dataclass
class Property:
    id: str
    name: str
    city: str
    timezone: str
    device_ids: list[str]
    max_guests: int = 4
    checkin_time: str = "16:00"
    checkout_time: str = "11:00"
    turnover_hours: float = 5.0
    cleaner_name: str = "Cleaner"
    cleaner_contact: str | None = None
    backup_cleaner_name: str | None = None
    backup_cleaner_contact: str | None = None
    cleaner_grace_min: int = 60
    min_clean_min: int = 60
    ready_check_min: int = 30  # minutes before check-in
    quiet_start: str = "22:00"
    quiet_end: str = "07:00"
    gathering_events: int = 3  # human arrivals within the window that trigger a CV check
    gathering_window_min: int = 30
    ical_url: str | None = None
    location_id: str | None = None  # Ring location, when a location-scoped subscription covers it
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Property":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Booking:
    id: str
    property_id: str
    start: date  # check-in date
    end: date  # checkout date
    guests: int | None = None
    label: str = "Reservation"
    source: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Booking":
        return cls(
            id=d["id"],
            property_id=d["property_id"],
            start=date.fromisoformat(d["start"]),
            end=date.fromisoformat(d["end"]),
            guests=d.get("guests"),
            label=d.get("label", "Reservation"),
            source=d.get("source", "manual"),
        )


@dataclass
class Alert:
    id: str
    property_id: str
    created_at: datetime
    severity: Severity
    kind: str
    title: str
    body: str
    snapshot_id: str | None = None
    actions: list[dict[str, str]] = field(default_factory=list)
    status: str = "open"  # open | acted | resolved | dismissed
    resolved_at: datetime | None = None
    updates: list[str] = field(default_factory=list)
    vision: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["resolved_at"] = self.resolved_at.isoformat() if self.resolved_at else None
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Alert":
        d = dict(d)
        d["created_at"] = datetime.fromisoformat(d["created_at"])
        d["resolved_at"] = datetime.fromisoformat(d["resolved_at"]) if d.get("resolved_at") else None
        d["severity"] = Severity(d["severity"])
        return cls(**d)


@dataclass
class Expectation:
    """Something that *should* happen. Absence rules are expectations that missed."""

    id: str
    property_id: str
    kind: str  # cleaner_arrival | turnover_ready | guest_arrival
    due_from: datetime
    deadline: datetime
    status: str = "pending"  # pending | met | late | missed
    met_at: datetime | None = None
    alert_id: str | None = None
    booking_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("due_from", "deadline", "met_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Expectation":
        d = dict(d)
        for k in ("due_from", "deadline", "met_at"):
            d[k] = datetime.fromisoformat(d[k]) if d.get(k) else None
        return cls(**d)


@dataclass
class Visit:
    """A turnover visit seen at the door: first and last human activity in the window."""

    id: str
    property_id: str
    window_start: datetime
    window_end: datetime
    first_at: datetime
    last_at: datetime
    events: int = 1
    closed: bool = False

    @property
    def minutes(self) -> int:
        return int((self.last_at - self.first_at).total_seconds() // 60)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("window_start", "window_end", "first_at", "last_at"):
            d[k] = d[k].isoformat()
        d["minutes"] = self.minutes
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Visit":
        d = {k: v for k, v in d.items() if k != "minutes"}
        for k in ("window_start", "window_end", "first_at", "last_at"):
            d[k] = datetime.fromisoformat(d[k])
        return cls(**d)


@dataclass
class EventRecord:
    """A Ring event as Turnover saw it, with what it decided."""

    id: str
    property_id: str | None
    device_id: str | None
    type: str
    sub_type: str | None
    occurred_at: datetime
    source: str  # webhook | history
    state: str | None = None
    classified_as: str | None = None
    disposition: str = "logged"  # suppressed | alerted | tracked | logged
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["occurred_at"] = self.occurred_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EventRecord":
        d = dict(d)
        d["occurred_at"] = datetime.fromisoformat(d["occurred_at"])
        return cls(**d)
