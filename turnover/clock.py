"""Clocks. The engine never calls datetime.now() directly.

Absence rules ("the cleaner has not arrived by 12:30") depend on time passing, so the
engine reads time from an injected clock. Production uses RealClock; the demo and the
tests use VirtualClock so a whole turnover day can play out in two minutes.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone


class RealClock:
    virtual = False

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class VirtualClock:
    virtual = True

    def __init__(self, start: datetime | None = None) -> None:
        self._lock = threading.Lock()
        self._now = start or datetime.now(timezone.utc)

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("VirtualClock needs an aware datetime")
        with self._lock:
            self._now = when.astimezone(timezone.utc)

    def advance(self, delta: timedelta) -> datetime:
        with self._lock:
            self._now = self._now + delta
            return self._now
