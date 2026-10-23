"""The expectation engine.

Ring reports what happened at the door. A host needs to know what it *means*, and that
depends on the booking calendar:

* 2 pm motion on a turnover day is the cleaner: track the visit, don't alarm.
* 2 pm motion at a vacant home is a stranger: alert now, with a snapshot.
* motion during a stay is the guests: suppress it, the host doesn't want 40 pings a day.
* no motion by noon on a turnover day is the most expensive event of all, and Ring can't
  report it because nothing happened. Turnover models it as an expectation that missed.

Every Ring event gets a recorded disposition (suppressed / tracked / alerted), so the
dashboard can show exactly how much noise was removed and why.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import bookings as bk
from .clock import RealClock, VirtualClock
from .media import SnapshotService
from .models import Alert, Booking, EventRecord, Expectation, Property, Severity, State, Visit
from .notify import Notifier
from .ring.webhooks import WebhookEvent
from .store import Store
from .vision import NoVision, Vision, VisionResult

log = logging.getLogger("turnover.engine")

ACTIVITY = {"motion_detected", "button_press"}
COALESCE = {"vacant_activity": 15, "package_vacant": 60, "vehicle_vacant_night": 30, "camera_offline": 240, "gathering": 60}
LATE_EVENT = timedelta(minutes=20)  # older than this (e.g. backfilled from history): log, don't page


def _utc(t: datetime) -> str:
    """Stored timestamps are compared as strings, so they must all be UTC."""
    return t.astimezone(ZoneInfo("UTC")).isoformat()


def _fmt_time(t: datetime, tz: str) -> str:
    return t.astimezone(ZoneInfo(tz)).strftime("%I:%M %p").lstrip("0")


def _fmt_dur(minutes: int) -> str:
    h, m = divmod(max(0, minutes), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


class Engine:
    def __init__(
        self,
        store: Store,
        clock: RealClock | VirtualClock,
        media: SnapshotService,
        notifier: Notifier,
        vision: Vision | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.media = media
        self.notifier = notifier
        self.vision: Vision = vision or NoVision()

    # ------------------------------------------------------------------ configuration

    def properties(self) -> list[Property]:
        return [Property.from_dict(d) for d in self.store.list("properties")]

    def property(self, pid: str) -> Property | None:
        d = self.store.get("properties", pid)
        return Property.from_dict(d) if d else None

    def save_property(self, prop: Property) -> None:
        self.store.put("properties", prop.id, prop.to_dict(), property_id=prop.id)

    def property_for_device(self, device_id: str | None) -> Property | None:
        if not device_id:
            return None
        return next((p for p in self.properties() if device_id in p.device_ids), None)

    def bookings(self, pid: str) -> list[Booking]:
        return [Booking.from_dict(d) for d in self.store.list("bookings", property_id=pid)]

    def save_booking(self, b: Booking) -> None:
        self.store.put("bookings", b.id, b.to_dict(), property_id=b.property_id, ts=b.start.isoformat())

    def replace_bookings(self, pid: str, items: list[Booking]) -> None:
        for d in self.store.list("bookings", property_id=pid):
            self.store.delete("bookings", d["id"])
        for b in items:
            self.save_booking(b)

    # ------------------------------------------------------------------ records

    def _expectations(self, pid: str) -> list[Expectation]:
        return [Expectation.from_dict(d) for d in self.store.list("expectations", property_id=pid)]

    def _save_exp(self, e: Expectation) -> None:
        self.store.put("expectations", e.id, e.to_dict(), property_id=e.property_id, ts=_utc(e.deadline))

    def _visits(self, pid: str) -> list[Visit]:
        return [Visit.from_dict(d) for d in self.store.list("visits", property_id=pid)]

    def _save_visit(self, v: Visit) -> None:
        self.store.put("visits", v.id, v.to_dict(), property_id=v.property_id, ts=_utc(v.first_at))

    def alerts(self, pid: str | None = None, limit: int = 100) -> list[Alert]:
        return [Alert.from_dict(d) for d in self.store.list("alerts", property_id=pid, limit=limit, newest_first=True)]

    def _save_alert(self, a: Alert) -> None:
        self.store.put("alerts", a.id, a.to_dict(), property_id=a.property_id, ts=_utc(a.created_at))

    def events(self, pid: str | None = None, since: datetime | None = None) -> list[EventRecord]:
        return [
            EventRecord.from_dict(d)
            for d in self.store.list("events", property_id=pid, since=_utc(since) if since else None)
        ]

    def _save_event(self, r: EventRecord) -> None:
        self.store.put("events", r.id, r.to_dict(), property_id=r.property_id, ts=_utc(r.occurred_at))

    # ------------------------------------------------------------------ alerts

    async def _raise(
        self,
        prop: Property,
        severity: Severity,
        kind: str,
        title: str,
        body: str,
        *,
        at: datetime,
        snapshot_id: str | None = None,
        actions: list[dict[str, str]] | None = None,
        vision: VisionResult | None = None,
        quiet: bool = False,
    ) -> Alert:
        window = COALESCE.get(kind)
        if window:
            for a in self.alerts(prop.id, limit=20):
                if a.kind == kind and a.status == "open" and at - a.created_at <= timedelta(minutes=window):
                    a.updates.append(f"{_fmt_time(at, prop.timezone)} · {title}")
                    if snapshot_id and not a.snapshot_id:
                        a.snapshot_id = snapshot_id
                    self._save_alert(a)
                    await self.notifier.alert(a, prop.name, update=True)
                    return a
        alert = Alert(
            id="al_" + uuid.uuid4().hex[:10],
            property_id=prop.id,
            created_at=at,
            severity=severity,
            kind=kind,
            title=title,
            body=body,
            snapshot_id=snapshot_id,
            actions=actions or [],
            vision=vision.to_dict() if vision else None,
        )
        self._save_alert(alert)
        if quiet:
            alert.updates.append("Backfilled from Ring event history: logged, not pushed")
            self._save_alert(alert)
            self.notifier.hub.publish("alert", alert.to_dict())
        else:
            await self.notifier.alert(alert, prop.name)
        return alert

    def resolve(self, alert_id: str, note: str, at: datetime | None = None) -> Alert | None:
        d = self.store.get("alerts", alert_id)
        if not d:
            return None
        a = Alert.from_dict(d)
        a.status = "resolved"
        a.resolved_at = at or self.clock.now()
        a.updates.append(note)
        self._save_alert(a)
        self.notifier.hub.publish("alert_update", a.to_dict())
        return a

    def act(self, alert_id: str, action: str, at: datetime | None = None) -> Alert | None:
        d = self.store.get("alerts", alert_id)
        if not d:
            return None
        a = Alert.from_dict(d)
        label = next((x["label"] for x in a.actions if x.get("id") == action), action)
        a.status = "dismissed" if action == "dismiss" else "acted"
        now = at or self.clock.now()
        prop = self.property(a.property_id)
        a.updates.append(f"{_fmt_time(now, prop.timezone if prop else 'UTC')} · You: {label}")
        self._save_alert(a)
        self.notifier.hub.publish("alert_update", a.to_dict())
        return a

    # ------------------------------------------------------------------ planning

    def plan(self, prop: Property, day: date) -> list[Expectation]:
        """Create the day's expectations from the calendar. Idempotent."""
        made: list[Expectation] = []
        items = self.bookings(prop.id)
        existing = {e.id for e in self._expectations(prop.id)}
        for b in items:
            if b.end == day:
                ws, we, nxt = bk.turnover_window(prop, b, items)
                cid = f"exp_{prop.id}_clean_{b.id}"
                if cid not in existing:
                    made.append(Expectation(cid, prop.id, "cleaner_arrival", ws, ws + timedelta(minutes=prop.cleaner_grace_min), booking_id=b.id))
                rid = f"exp_{prop.id}_ready_{b.id}"
                if rid not in existing:
                    if nxt is not None and nxt.start == day:
                        deadline = bk.checkin_dt(prop, nxt) - timedelta(minutes=prop.ready_check_min)
                    else:
                        deadline = we
                    made.append(Expectation(rid, prop.id, "turnover_ready", ws, deadline, booking_id=b.id))
            if b.start == day:
                gid = f"exp_{prop.id}_guest_{b.id}"
                if gid not in existing:
                    ci = bk.checkin_dt(prop, b)
                    made.append(Expectation(gid, prop.id, "guest_arrival", ci - timedelta(minutes=30), ci + timedelta(hours=6), booking_id=b.id))
        for e in made:
            self._save_exp(e)
        return made

    def _plan_around(self, prop: Property, t: datetime) -> None:
        local = t.astimezone(ZoneInfo(prop.timezone)).date()
        self.plan(prop, local)

    # ------------------------------------------------------------------ ingest

    async def ingest(self, evt: WebhookEvent, *, source: str = "webhook") -> EventRecord | None:
        if evt.type in ("app_integration_added", "app_integration_removed", "subscription_activated", "subscription_deactivated"):
            await self._account_event(evt)
            return None
        at = evt.occurred_at or self.clock.now()
        rid = f"ev_{evt.id}" if evt.id else f"ev_{evt.dedupe_key}"
        if self.store.get("events", rid):
            return None  # already seen (webhook retry, or history overlap)
        prop = self.property_for_device(evt.device_id)
        rec = EventRecord(rid, prop.id if prop else None, evt.device_id, evt.type, evt.sub_type, at, source)
        if prop is None or not prop.enabled:
            rec.note = "device not assigned to a property" if prop is None else "property paused (no active subscription)"
            if evt.type == "device_added":
                rec.note = "new device shared with Turnover: assign it to a property"
                self.notifier.hub.publish("device_added", {"device_id": evt.device_id})
            self._save_event(rec)
            return rec
        self._plan_around(prop, at)
        stale = self.clock.now() - at > LATE_EVENT
        if evt.type in ACTIVITY:
            await self._activity(prop, evt, rec, quiet=stale)
        elif evt.type in ("device_offline", "device_online"):
            await self._connectivity(prop, evt, rec, quiet=stale)
        else:
            rec.disposition = "logged"
            rec.note = evt.type.replace("_", " ")
        self._save_event(rec)
        self.notifier.hub.publish("event", rec.to_dict())
        return rec

    async def _classify(self, prop: Property, evt: WebhookEvent, at: datetime, decision_matters: bool) -> tuple[str, str | None, VisionResult | None]:
        """Tier 0 + Tier 2/3 of the funnel. Returns (class, snapshot_id, vision)."""
        if evt.type == "button_press":
            return "human", None, None  # someone pressed the doorbell
        st = (evt.sub_type or "motion").lower()
        if st == "human":
            return "human", None, None
        if st == "vehicle":
            return "vehicle", None, None
        if st == "package_delivery":
            return "package", None, None
        if st == "other_motion":
            return "other", None, None
        if st != "motion":
            return f"unknown:{st}", None, None  # open vocabulary: keep it, decide conservatively
        # sub_type "motion": Smart Alerts is off, Ring did not classify. Only spend a snapshot
        # and a model call when the answer would change what we do.
        if not decision_matters:
            return "unclassified", None, None
        snap = await self.media.fetch(evt.device_id or "", at)
        if snap.image is None:
            return "unclassified", None, None
        vr = await self.vision.analyze(snap.image, snap.content_type or "image/jpeg")
        if not vr.ok:
            return "unclassified", snap.id, vr
        if (vr.people_count or 0) > 0:
            return "human", snap.id, vr
        if vr.package_present:
            return "package", snap.id, vr
        if vr.vehicle_present:
            return "vehicle", snap.id, vr
        return "other", snap.id, vr

    async def _activity(self, prop: Property, evt: WebhookEvent, rec: EventRecord, *, quiet: bool) -> None:
        at = rec.occurred_at
        items = self.bookings(prop.id)
        m = bk.moment(prop, items, at)
        rec.state = m.state.value
        # Spend a snapshot + model call on an unclassified event only where one event changes
        # the outcome. During a stay a single motion never does; the gathering check runs
        # its own, rate-limited CV call when the arrival count crosses the threshold.
        matters = m.state in (State.VACANT, State.TURNOVER)
        cls, snap_id, vr = await self._classify(prop, evt, at, matters)
        rec.classified_as = cls
        is_person = cls == "human" or (cls in ("unclassified",) or cls.startswith("unknown:")) and m.state != State.OCCUPIED
        tz = prop.timezone
        when = _fmt_time(at, tz)

        if m.state == State.VACANT:
            if is_person:
                if snap_id is None:
                    snap = await self.media.fetch(evt.device_id or "", at)
                    snap_id = snap.id
                nxt = f" Next check-in {(m.next_booking.start.strftime('%a %b ') + str(m.next_booking.start.day))}." if m.next_booking else ""
                how = "Doorbell pressed" if evt.type == "button_press" else "Person at the door"
                if cls == "unclassified":
                    how = "Motion at the door (camera has no person detection; treated as a person)"
                elif vr is not None and vr.ok:
                    how = f"Person at the door (vision check: {vr.people_count} visible)"
                await self._raise(
                    prop, Severity.URGENT, "vacant_activity", f"Activity at a vacant home · {when}",
                    f"{how} while {prop.name} is empty.{nxt}", at=at, snapshot_id=snap_id, vision=vr, quiet=quiet,
                    actions=[{"id": "live", "label": "Open live view"}, {"id": "neighbor", "label": "Ask neighbor to check"}, {"id": "dismiss", "label": "It's expected"}],
                )
                rec.disposition = "alerted"
            elif cls == "package":
                await self._raise(
                    prop, Severity.INFO, "package_vacant", f"Package left at a vacant home · {when}",
                    f"Nobody is staying at {prop.name}. Ask the next cleaner to bring it inside.", at=at, quiet=quiet,
                    actions=[{"id": "note_cleaner", "label": "Add to cleaner's checklist"}, {"id": "dismiss", "label": "Dismiss"}],
                )
                rec.disposition = "alerted"
            elif cls == "vehicle" and m.quiet_hours:
                await self._raise(
                    prop, Severity.WARN, "vehicle_vacant_night", f"Vehicle at a vacant home · {when}",
                    f"A vehicle was detected at {prop.name} during quiet hours.", at=at, quiet=quiet,
                    actions=[{"id": "live", "label": "Open live view"}, {"id": "dismiss", "label": "Dismiss"}],
                )
                rec.disposition = "alerted"
            else:
                rec.disposition = "suppressed"
                rec.note = f"{cls} at a vacant home, outside quiet hours"
            return

        if m.state == State.TURNOVER:
            if is_person:
                await self._turnover_activity(prop, m, at, quiet=quiet)
                rec.disposition = "tracked"
                rec.note = "cleaner seen at the door"
            else:
                rec.disposition = "suppressed"
                rec.note = f"{cls} during turnover"
            return

        # OCCUPIED
        rec.disposition = "suppressed"
        rec.note = "guest activity during a stay"
        if cls in ("human", "unclassified"):
            await self._guest_arrival(prop, m, at, quiet=quiet)
            if m.quiet_hours:
                await self._gathering_check(prop, m, evt, at, rec, quiet=quiet)

    async def _turnover_activity(self, prop: Property, m: bk.Moment, at: datetime, *, quiet: bool) -> None:
        assert m.window_start and m.window_end
        visit = next((v for v in self._visits(prop.id) if v.window_start == m.window_start), None)
        tz = prop.timezone
        if visit is None:
            visit = Visit("vi_" + uuid.uuid4().hex[:10], prop.id, m.window_start, m.window_end, at, at)
            self._save_visit(visit)
            exp = next((e for e in self._expectations(prop.id) if e.kind == "cleaner_arrival" and e.due_from == m.window_start), None)
            until = ""
            if m.next_booking and m.next_booking.start == at.astimezone(ZoneInfo(tz)).date():
                mins = int((bk.checkin_dt(prop, m.next_booking) - at).total_seconds() // 60)
                until = f" {_fmt_dur(mins)} until the {_fmt_time(bk.checkin_dt(prop, m.next_booking), tz)} check-in."
            if exp and exp.status == "pending":
                exp.status, exp.met_at = "met", at
                self._save_exp(exp)
                await self._raise(prop, Severity.GOOD, "cleaner_arrived", f"Cleaner arrived · {_fmt_time(at, tz)}", f"Turnover started on time.{until}", at=at, quiet=quiet)
            elif exp and exp.status == "missed":
                exp.status, exp.met_at = "late", at
                self._save_exp(exp)
                late = int((at - exp.deadline).total_seconds() // 60) + prop.cleaner_grace_min
                if exp.alert_id:
                    self.resolve(exp.alert_id, f"{_fmt_time(at, tz)} · Someone arrived at the door: turnover underway", at=at)
                await self._raise(prop, Severity.GOOD, "cleaner_arrived", f"Cleaner arrived · {_fmt_time(at, tz)}", f"{_fmt_dur(late)} after checkout.{until}", at=at, quiet=quiet)
            else:
                await self._raise(prop, Severity.GOOD, "cleaner_arrived", f"Turnover started · {_fmt_time(at, tz)}", f"First activity at the door.{until}", at=at, quiet=quiet)
        else:
            visit.last_at = max(visit.last_at, at)
            visit.events += 1
            self._save_visit(visit)
        self.notifier.hub.publish("visit", visit.to_dict())

    async def _guest_arrival(self, prop: Property, m: bk.Moment, at: datetime, *, quiet: bool) -> None:
        if not m.booking:
            return
        exp = next((e for e in self._expectations(prop.id) if e.kind == "guest_arrival" and e.booking_id == m.booking.id), None)
        if exp and exp.status in ("pending", "missed") and at >= exp.due_from:
            was_missed = exp.status == "missed"
            exp.status, exp.met_at = ("late" if was_missed else "met"), at
            self._save_exp(exp)
            if was_missed and exp.alert_id:
                self.resolve(exp.alert_id, f"{_fmt_time(at, prop.timezone)} · Guests arrived", at=at)
            await self._raise(prop, Severity.GOOD, "guest_arrived", f"Guests arrived · {_fmt_time(at, prop.timezone)}", "Check-in confirmed at the door. Routine activity is muted until checkout.", at=at, quiet=quiet)

    async def _gathering_check(self, prop: Property, m: bk.Moment, evt: WebhookEvent, at: datetime, rec: EventRecord, *, quiet: bool) -> None:
        window_start = at - timedelta(minutes=prop.gathering_window_min)
        recent = [
            r for r in self.events(prop.id, since=window_start)
            if r.type in ACTIVITY and r.classified_as in ("human", "unclassified") and r.occurred_at <= at
        ]
        arrivals = len(recent) + 1  # this event is not saved yet
        if arrivals < prop.gathering_events:
            return
        # One CV check per hour per property: cost control and no repeat pings.
        last = self.store.get("kv", f"gathering_check:{prop.id}")
        if last and at - datetime.fromisoformat(last["at"]) < timedelta(minutes=60):
            return
        self.store.put("kv", f"gathering_check:{prop.id}", {"at": at.isoformat()})
        limit = (m.booking.guests if m.booking and m.booking.guests else prop.max_guests)
        snap = await self.media.fetch(evt.device_id or "", at)
        when = _fmt_time(at, prop.timezone)
        if snap.image is None:
            await self._raise(
                prop, Severity.WARN, "gathering", f"Unusual late-night activity · {when}",
                f"{arrivals} arrivals at the door in {prop.gathering_window_min} min during quiet hours. No snapshot was available ({snap.note}).",
                at=at, quiet=quiet, actions=[{"id": "live", "label": "Open live view"}, {"id": "dismiss", "label": "Dismiss"}],
            )
            rec.disposition, rec.note = "alerted", "late-night arrivals"
            return
        vr = await self.vision.analyze(snap.image, snap.content_type or "image/jpeg", ring_sub_type=evt.sub_type)
        if vr.ok and (vr.people_count or 0) <= limit:
            rec.note = f"CV check: {vr.people_count} people, within the {limit}-guest limit"
            return
        if vr.ok:
            title = f"Possible gathering · {when}"
            body = f"About {vr.people_count} people at the door vs a {limit}-guest booking, after {arrivals} late arrivals in {prop.gathering_window_min} min. Review the snapshot before contacting the guest."
        else:
            title = f"Unusual late-night activity · {when}"
            body = f"{arrivals} arrivals in {prop.gathering_window_min} min during quiet hours. The vision check couldn't assess the snapshot reliably ({vr.fallback_reason}), so please review it yourself."
        await self._raise(
            prop, Severity.WARN, "gathering", title, body, at=at, snapshot_id=snap.id, vision=vr, quiet=quiet,
            actions=[{"id": "message_guest", "label": "Send quiet-hours reminder"}, {"id": "live", "label": "Open live view"}, {"id": "dismiss", "label": "Looks fine"}],
        )
        rec.disposition, rec.note = "alerted", "possible gathering"

    async def _connectivity(self, prop: Property, evt: WebhookEvent, rec: EventRecord, *, quiet: bool) -> None:
        at = rec.occurred_at
        m = bk.moment(prop, self.bookings(prop.id), at)
        rec.state = m.state.value
        rec.disposition = "alerted"
        open_alert = next((a for a in self.alerts(prop.id, limit=30) if a.kind == "camera_offline" and a.status == "open"), None)
        if evt.type == "device_offline":
            # device_offline lags the real loss of connectivity (docs), so say "since about".
            critical = m.state in (State.VACANT, State.TURNOVER) or (
                m.next_booking is not None and bk.checkin_dt(prop, m.next_booking) - at < timedelta(hours=6)
            )
            why = {
                State.VACANT: f"{prop.name} is empty, so nobody would notice activity there.",
                State.TURNOVER: "Turnover verification is paused until it reconnects.",
                State.OCCUPIED: "Guests are staying; the camera usually reconnects on its own.",
            }[m.state]
            await self._raise(
                prop, Severity.URGENT if critical else Severity.WARN, "camera_offline", f"Camera offline · {_fmt_time(at, prop.timezone)}",
                f"The front-door camera stopped reporting. {why}", at=at, quiet=quiet,
                actions=[{"id": "note_cleaner", "label": "Ask cleaner to power-cycle it"}, {"id": "dismiss", "label": "Dismiss"}],
            )
        elif open_alert:
            mins = int((at - open_alert.created_at).total_seconds() // 60)
            self.resolve(open_alert.id, f"{_fmt_time(at, prop.timezone)} · Back online after {_fmt_dur(mins)}", at=at)
            rec.disposition, rec.note = "logged", "back online; closed the offline alert"
        else:
            rec.disposition = "logged"

    async def _account_event(self, evt: WebhookEvent) -> None:
        if evt.type == "app_integration_removed":
            # The user removed Turnover in the Ring app: tokens are already revoked on Ring's
            # side. Delete ours and the media we hold, and stop monitoring.
            self.store.delete("kv", f"tokens:{evt.account_id}")
            self.store.purge_blobs_before("9999")
            for p in self.properties():
                p.enabled = False
                self.save_property(p)
            self.notifier.hub.publish("account", {"type": evt.type, "account_id": evt.account_id})
        elif evt.type in ("subscription_activated", "subscription_deactivated"):
            on = evt.type == "subscription_activated"
            covered = set(evt.covered_device_ids)
            for p in self.properties():
                if covered & set(p.device_ids):
                    p.enabled = on
                    self.save_property(p)
            self.notifier.hub.publish("account", {"type": evt.type, "devices": sorted(covered)})
        else:
            self.notifier.hub.publish("account", {"type": evt.type, "account_id": evt.account_id})

    # ------------------------------------------------------------------ time passing

    async def tick(self) -> list[Alert]:
        """Evaluate absence rules. Runs every few seconds in production; the demo calls it
        after every simulated minute."""
        now = self.clock.now()
        raised: list[Alert] = []
        for prop in self.properties():
            if not prop.enabled:
                continue
            self._plan_around(prop, now)
            for e in self._expectations(prop.id):
                if e.status != "pending" or now < e.deadline:
                    continue
                a = await self._expire(prop, e, now)
                if a:
                    raised.append(a)
        return raised

    async def _expire(self, prop: Property, e: Expectation, now: datetime) -> Alert | None:
        tz = prop.timezone
        items = self.bookings(prop.id)
        booking = next((b for b in items if b.id == e.booking_id), None)
        if e.kind == "cleaner_arrival":
            e.status = "missed"
            nxt = None
            if booking:
                _, _, nxt = bk.turnover_window(prop, booking, items)
            ci = f" Guests check in at {_fmt_time(bk.checkin_dt(prop, nxt), tz)}." if nxt and nxt.start == booking.end else ""
            backup = prop.backup_cleaner_name or "your backup cleaner"
            a = await self._raise(
                prop, Severity.URGENT, "cleaner_no_show", f"No cleaner yet · {_fmt_time(now, tz)}",
                f"Checkout was {_fmt_time(e.due_from, tz)} and nobody has arrived at the door. {prop.cleaner_name} was due by {_fmt_time(e.deadline, tz)}.{ci}",
                at=now,
                actions=[{"id": "text_backup", "label": f"Text {backup}"}, {"id": "call_cleaner", "label": f"Call {prop.cleaner_name}"}, {"id": "dismiss", "label": "Cleaner is inside"}],
            )
            e.alert_id = a.id
            self._save_exp(e)
            return a
        if e.kind == "turnover_ready":
            visit = next((v for v in self._visits(prop.id) if v.window_start == e.due_from), None)
            nxt_text = ""
            if booking:
                _, _, nxt = bk.turnover_window(prop, booking, items)
                if nxt and nxt.start == booking.end:
                    nxt_text = f" for the {_fmt_time(bk.checkin_dt(prop, nxt), tz)} check-in"
            if visit and visit.events >= 2 and visit.minutes >= prop.min_clean_min:
                e.status, e.met_at = "met", now
                visit.closed = True
                self._save_visit(visit)
                a = await self._raise(
                    prop, Severity.GOOD, "turnover_verified", f"Ready{nxt_text}",
                    f"Turnover verified at the door: on site {_fmt_time(visit.first_at, tz)}–{_fmt_time(visit.last_at, tz)} ({_fmt_dur(visit.minutes)}).",
                    at=now, actions=[{"id": "pay_cleaner", "label": "Approve cleaner payment"}],
                )
            elif visit:
                e.status = "missed"
                seen = (
                    f"only {_fmt_dur(visit.minutes)} of activity ({_fmt_time(visit.first_at, tz)}–{_fmt_time(visit.last_at, tz)})"
                    if visit.events >= 2 else f"an arrival at {_fmt_time(visit.first_at, tz)} but no departure"
                )
                a = await self._raise(
                    prop, Severity.WARN, "turnover_short", f"Check the turnover{nxt_text}",
                    f"The door saw {seen}. A turnover here usually takes at least {_fmt_dur(prop.min_clean_min)}.",
                    at=now, actions=[{"id": "call_cleaner", "label": f"Call {prop.cleaner_name}"}, {"id": "dismiss", "label": "It's fine"}],
                )
            else:
                e.status = "missed"
                a = await self._raise(
                    prop, Severity.URGENT, "turnover_missing", f"Not turned over{nxt_text}",
                    f"Nobody was seen at the door since checkout at {_fmt_time(e.due_from, tz)}.",
                    at=now, actions=[{"id": "text_backup", "label": f"Text {prop.backup_cleaner_name or 'backup cleaner'}"}, {"id": "message_guest", "label": "Message incoming guest"}],
                )
            e.alert_id = a.id
            self._save_exp(e)
            return a
        if e.kind == "guest_arrival":
            e.status = "missed"
            a = await self._raise(
                prop, Severity.INFO, "guest_not_seen", f"No guest arrival seen · {_fmt_time(now, tz)}",
                f"Check-in was {_fmt_time(e.due_from + timedelta(minutes=30), tz)}. Late arrivals are common; reach out if you expected them.",
                at=now, actions=[{"id": "message_guest", "label": "Message guest"}, {"id": "dismiss", "label": "Dismiss"}],
            )
            e.alert_id = a.id
            self._save_exp(e)
            return a
        return None

    # ------------------------------------------------------------------ views

    def snapshot_of_day(self, prop: Property, now: datetime | None = None) -> dict[str, Any]:
        now = now or self.clock.now()
        tz = ZoneInfo(prop.timezone)
        local = now.astimezone(tz)
        day_start = datetime.combine(local.date(), datetime.min.time(), tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        items = self.bookings(prop.id)
        m = bk.moment(prop, items, now)
        evs = [e for e in self.events(prop.id, since=day_start.astimezone(ZoneInfo("UTC"))) if e.occurred_at < day_end]
        activity = [e for e in evs if e.type in ACTIVITY]
        alerts_today = [a for a in self.alerts(prop.id, limit=200) if day_start <= a.created_at < day_end]
        visits = [v for v in self._visits(prop.id) if day_start <= v.window_start < day_end]
        exps = [e for e in self._expectations(prop.id) if day_start <= e.deadline < day_end + timedelta(hours=6)]
        bands = []
        for b in items:
            ci, co = bk.checkin_dt(prop, b), bk.checkout_dt(prop, b)
            if ci < day_end and co > day_start:
                bands.append({"kind": "stay", "start": max(ci, day_start).isoformat(), "end": min(co, day_end).isoformat(), "label": b.label, "guests": b.guests})
            ws, we, _ = bk.turnover_window(prop, b, items)
            if ws < day_end and we > day_start:
                bands.append({"kind": "turnover", "start": ws.isoformat(), "end": we.isoformat(), "label": "Turnover window"})
        status = m.state.value
        headline = {"vacant": "Vacant", "occupied": "Guests staying", "turnover": "Turnover in progress"}[status]
        open_alerts = [a for a in alerts_today if a.status == "open" and a.severity in (Severity.URGENT, Severity.WARN)]
        if open_alerts:
            headline = open_alerts[0].title.split(" · ")[0]
        live = None
        cur_visit = next((v for v in visits if v.window_start == m.window_start), None) if m.state == State.TURNOVER else None
        if m.state == State.TURNOVER:
            live = f"On site since {_fmt_time(cur_visit.first_at, prop.timezone)}, last seen at the door {_fmt_time(cur_visit.last_at, prop.timezone)}" if cur_visit else f"Waiting for {prop.cleaner_name}"
        elif m.state == State.OCCUPIED and m.booking:
            live = f"Checkout {bk.checkout_dt(prop, m.booking).strftime('%a')} {prop.checkout_time}"
        elif m.next_booking:
            live = f"Next check-in {(m.next_booking.start.strftime('%a %b ') + str(m.next_booking.start.day))}"
        def stay(b: Booking | None) -> dict[str, Any] | None:
            if b is None:
                return None
            return {"id": b.id, "label": b.label, "guests": b.guests, "checkin": bk.checkin_dt(prop, b).isoformat(), "checkout": bk.checkout_dt(prop, b).isoformat()}

        return {
            "property": prop.to_dict(),
            "current_stay": stay(m.booking if m.state == State.OCCUPIED else None),
            "last_stay": stay(m.booking if m.state == State.TURNOVER else None),
            "next_stay": stay(m.next_booking),
            "window": {"start": m.window_start.isoformat(), "end": m.window_end.isoformat()} if m.window_start and m.window_end else None,
            "camera_offline": any(a.kind == "camera_offline" and a.status == "open" for a in self.alerts(prop.id, limit=30)),
            "state": status,
            "quiet_hours": m.quiet_hours,
            "headline": headline,
            "live": live,
            "attention": bool(open_alerts),
            "day_start": day_start.isoformat(),
            "day_end": day_end.isoformat(),
            "bands": bands,
            "events": [e.to_dict() for e in evs],
            "visits": [v.to_dict() for v in visits],
            "expectations": [e.to_dict() for e in exps],
            "metrics": {
                "ring_events": len(activity),
                "suppressed": sum(1 for e in activity if e.disposition == "suppressed"),
                "tracked": sum(1 for e in activity if e.disposition == "tracked"),
                "alerts": len(alerts_today),
            },
        }
