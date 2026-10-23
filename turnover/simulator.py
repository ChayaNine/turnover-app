"""Scenario player: a whole host day, delivered as signed Ring webhooks.

The Ring Developer Playground simulates live view but does not deliver webhooks, and Ring
documents no webhook test sender, so without real hardware there is no way to exercise
a webhook handler end to end. This player builds v1.1 payloads shaped like the documented
examples, signs them with the HMAC key (X-Signature: sha256=<hex>), and POSTs them to the
real /webhooks/ring route, so verification, idempotency and processing all run unchanged.

Time is virtual: each simulated minute advances the engine clock and runs the absence
rules, so "the cleaner didn't show by noon" plays out in seconds.

CLI, to send one signed event to any running instance (local or deployed):

    python -m turnover.simulator send --url https://<host>/webhooks/ring --key "$RING_HMAC_KEY" \
        --device ava1.ring.device.XXXX --type motion_detected --sub-type human
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from .models import Booking, Property, Severity
from .ring.webhooks import SIGNATURE_HEADER, build_payload, encode, sign

if TYPE_CHECKING:
    from .app import Runtime

DEMO_TZ = "America/Los_Angeles"
IMAGES = Path(__file__).resolve().parent.parent / "demo" / "images"


def demo_properties() -> list[Property]:
    return [
        Property(
            id="lakeview", name="Lakeview Cabin", city="South Lake Tahoe, CA", timezone=DEMO_TZ,
            device_ids=["ava1.ring.device.demo-lakeview-front"], max_guests=6,
            cleaner_name="Maria", cleaner_contact="+1 555 0101", backup_cleaner_name="Dev", backup_cleaner_contact="+1 555 0102",
            cleaner_grace_min=60, min_clean_min=75,
        ),
        Property(
            id="harbor", name="Harbor Loft", city="San Diego, CA", timezone=DEMO_TZ,
            device_ids=["ava1.ring.device.demo-harbor-door"], max_guests=4,
            cleaner_name="Ana", backup_cleaner_name="Luis",
        ),
        Property(
            id="juniper", name="Juniper Bungalow", city="Palm Springs, CA", timezone=DEMO_TZ,
            device_ids=["ava1.ring.device.demo-juniper-front"], max_guests=4,
            cleaner_name="Rosa", backup_cleaner_name="Dev",
        ),
        Property(
            id="cedar", name="Cedar Studio", city="Joshua Tree, CA", timezone=DEMO_TZ,
            device_ids=["ava1.ring.device.demo-cedar-porch"], max_guests=2,
            cleaner_name="Rosa", backup_cleaner_name="Dev",
        ),
    ]


def demo_bookings(day: date) -> list[Booking]:
    d = timedelta(days=1)
    return [
        Booking("bk_lake_a", "lakeview", day - 3 * d, day, 5, "Reserved · HMK2Q9"),
        Booking("bk_lake_b", "lakeview", day, day + 4 * d, 4, "Reserved · HMR7T1"),
        Booking("bk_harbor", "harbor", day - 1 * d, day + 2 * d, 4, "Reserved · HM3XW8"),
        Booking("bk_juniper", "juniper", day + 2 * d, day + 6 * d, 3, "Reserved · HMP5L0"),
        Booking("bk_cedar", "cedar", day + 3 * d, day + 5 * d, 2, "Reserved · HMC9D4"),
    ]


@dataclass
class Step:
    at: str  # local HH:MM
    prop: str
    type: str
    sub_type: str | None = None
    frame: str | None = None  # demo/images/<frame>.jpg: what the camera "saw"
    note: str = ""


# A full day across four homes. Annotations say what the host should see.
SCENARIO: list[Step] = [
    Step("07:12", "harbor", "motion_detected", "human", note="guest heads out: muted"),
    Step("07:41", "lakeview", "motion_detected", "human", note="departing guests pack the car: muted"),
    Step("08:03", "lakeview", "motion_detected", "vehicle"),
    Step("08:37", "harbor", "motion_detected", "human"),
    Step("09:16", "harbor", "motion_detected", "human"),
    Step("09:18", "harbor", "button_press", note="food delivery ding during a stay: muted"),
    Step("09:40", "cedar", "motion_detected", "motion", frame="cedar-wind", note="Smart Alerts off; vision sees nobody: false alarm avoided"),
    Step("10:22", "lakeview", "motion_detected", "human"),
    Step("10:47", "lakeview", "motion_detected", "human"),
    Step("10:51", "lakeview", "motion_detected", "vehicle", note="guests leave; turnover window opens at 11:00"),
    Step("11:34", "juniper", "motion_detected", "package_delivery", note="package at a vacant home"),
    # 12:00: cleaner deadline passes with no activity -> absence alert (raised by tick)
    Step("12:52", "lakeview", "motion_detected", "human", note="backup cleaner arrives"),
    Step("13:20", "harbor", "motion_detected", "human"),
    Step("13:31", "lakeview", "motion_detected", "human", note="trash run"),
    Step("14:05", "harbor", "motion_detected", "vehicle"),
    Step("14:41", "lakeview", "motion_detected", "human", note="cleaner leaves"),
    Step("15:02", "juniper", "device_offline", note="camera drops off wifi at an empty home"),
    Step("15:24", "juniper", "device_online"),
    # 15:30: ready check for the 4:00 PM check-in -> turnover verified (raised by tick)
    Step("16:26", "lakeview", "motion_detected", "human", note="new guests arrive"),
    Step("16:27", "lakeview", "button_press"),
    Step("17:10", "harbor", "motion_detected", "human"),
    Step("17:55", "lakeview", "motion_detected", "vehicle"),
    Step("18:45", "lakeview", "motion_detected", "human"),
    Step("19:30", "harbor", "motion_detected", "human"),
    Step("20:15", "lakeview", "motion_detected", "human"),
    Step("21:42", "cedar", "motion_detected", "motion", frame="cedar-night", note="someone at a vacant home; old camera, vision confirms a person"),
    Step("22:05", "harbor", "motion_detected", "human"),
    Step("23:04", "harbor", "motion_detected", "human"),
    Step("23:11", "harbor", "motion_detected", "human"),
    Step("23:15", "harbor", "button_press", frame="harbor-gathering", note="3rd late arrival -> CV check -> possible gathering"),
]

START, END = "06:45", "23:30"


def _local(day: date, hhmm: str) -> datetime:
    hh, mm = map(int, hhmm.split(":"))
    return datetime.combine(day, time(hh, mm), tzinfo=ZoneInfo(DEMO_TZ))


def demo_day(now: datetime | None = None) -> date:
    return (now or datetime.now(ZoneInfo(DEMO_TZ))).astimezone(ZoneInfo(DEMO_TZ)).date()


class Player:
    """Plays SCENARIO against a Runtime, one simulated minute at a time."""

    def __init__(self, rt: "Runtime", deliver: Callable[[bytes, dict[str, str]], Awaitable[int]]) -> None:
        self.rt = rt
        self.deliver = deliver
        self.running = False
        self.paused = False
        self.pause_on_urgent = True
        self.minute_delay = 0.12  # real seconds per simulated minute
        self.day: date = demo_day()
        self._resume = asyncio.Event()
        self._resume.set()
        self._task: asyncio.Task | None = None
        self.delivered = 0

    def status(self) -> dict:
        return {
            "running": self.running, "paused": self.paused, "minute_delay": self.minute_delay,
            "pause_on_urgent": self.pause_on_urgent, "delivered": self.delivered,
        }

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run())

    def pause(self) -> None:
        self.paused = True
        self._resume.clear()

    def resume(self) -> None:
        self.paused = False
        self._resume.set()

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self.running = False
        self.resume()

    async def _run(self) -> None:
        self.running = True
        self.delivered = 0
        rt = self.rt
        try:
            t = _local(self.day, START)
            end = _local(self.day, END)
            by_minute: dict[str, list[Step]] = {}
            for s in SCENARIO:
                by_minute.setdefault(s.at, []).append(s)
            while t <= end:
                await self._resume.wait()
                rt.clock.set(t)
                seen_before = {a.id for a in rt.engine.alerts(limit=500)}
                for step in by_minute.get(t.strftime("%H:%M"), []):
                    await self._send(step, t)
                await rt.drain()
                await rt.engine.tick()
                rt.hub.publish("clock", {"now": t.isoformat(), **self.status()})
                if self.pause_on_urgent:
                    fresh = [a for a in rt.engine.alerts(limit=50) if a.id not in seen_before]
                    if any(a.severity in (Severity.URGENT, Severity.WARN) for a in fresh):
                        self.pause()
                        rt.hub.publish("clock", {"now": t.isoformat(), **self.status()})
                await asyncio.sleep(self.minute_delay)
                t += timedelta(minutes=1)
        finally:
            self.running = False
            rt.hub.publish("clock", {"now": rt.clock.now().isoformat(), **self.status()})

    async def _send(self, step: Step, t: datetime) -> None:
        prop = self.rt.engine.property(step.prop)
        if prop is None or not prop.device_ids:
            return
        device = prop.device_ids[0]
        # Ring event timestamps land a few seconds into the minute.
        when = t + timedelta(seconds=7)
        if step.frame:
            self.rt.media.register_demo_frame(device, when, IMAGES / f"{step.frame}.jpg")
        payload = build_payload(step.type, device, when, sub_type=step.sub_type, request_id=str(uuid.uuid4()))
        body = encode(payload)
        status = await self.deliver(body, {SIGNATURE_HEADER: sign(self.rt.settings.webhook_signing_key, body), "Content-Type": "application/json"})
        if status == 200:
            self.delivered += 1


def register_fixtures(fixture_vision) -> None:
    """Teach the offline FixtureVision the demo frames (by content hash)."""
    from .vision import load_fixture

    for img in IMAGES.glob("*.jpg"):
        fx = load_fixture(img)
        if fx:
            fixture_vision.register(img.read_bytes(), fx)


# --- CLI --------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m turnover.simulator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="send one signed webhook to a running instance")
    s.add_argument("--url", required=True, help="e.g. http://localhost:8000/webhooks/ring")
    s.add_argument("--key", required=True, help="HMAC signing key")
    s.add_argument("--device", required=True)
    s.add_argument("--type", default="motion_detected")
    s.add_argument("--sub-type", default=None)
    args = ap.parse_args()

    from datetime import timezone

    payload = build_payload(args.type, args.device, datetime.now(timezone.utc), sub_type=args.sub_type, request_id=str(uuid.uuid4()))
    body = encode(payload)
    r = httpx.post(args.url, content=body, headers={SIGNATURE_HEADER: sign(args.key, body), "Content-Type": "application/json"}, timeout=10)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
