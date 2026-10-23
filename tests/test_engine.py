"""Engine behaviour, driven through the real webhook route."""

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from turnover import config
from turnover.app import create_app
from turnover.ring import webhooks
from turnover.vision import guard


def make_client(tmp_path):
    s = config.Settings(db_path=str(tmp_path / "t.db"), demo_mode=True, ring_hmac_key="k", ring_access_token=None)
    return TestClient(create_app(s))


def post_event(client, device, when, typ="motion_detected", sub="human"):
    body = webhooks.encode(webhooks.build_payload(typ, device, when, sub_type=sub, request_id=str(uuid.uuid4())))
    return client.post("/webhooks/ring", content=body, headers={"X-Signature": webhooks.sign("k", body)})


def test_webhook_rejects_bad_signature_and_dedupes(tmp_path):
    with make_client(tmp_path) as c:
        body = webhooks.encode(webhooks.build_payload("motion_detected", "x", datetime.now(timezone.utc), sub_type="human", request_id="same"))
        assert c.post("/webhooks/ring", content=body, headers={"X-Signature": "sha256=deadbeef"}).status_code == 401
        sig = webhooks.sign("k", body)
        assert c.post("/webhooks/ring", content=body, headers={"X-Signature": sig}).json()["status"] == "accepted"
        assert c.post("/webhooks/ring", content=body, headers={"X-Signature": sig}).json()["status"] == "duplicate"


def run_day(c):
    c.post("/api/demo/start", json={"minute_delay": 0, "pause_on_urgent": False})
    deadline = time.time() + 60
    while time.time() < deadline:
        st = c.get("/api/state").json()
        if st["player"]["delivered"] > 0 and not st["player"]["running"]:
            return st
        time.sleep(0.2)
    raise AssertionError("scenario did not finish")


def test_full_demo_day(tmp_path):
    with make_client(tmp_path) as c:
        st = run_day(c)
        kinds = [(a["property_id"], a["kind"]) for a in st["alerts"]]
        assert ("lakeview", "cleaner_no_show") in kinds
        assert ("lakeview", "cleaner_arrived") in kinds
        assert ("lakeview", "turnover_verified") in kinds
        assert ("lakeview", "guest_arrived") in kinds
        assert ("juniper", "package_vacant") in kinds
        assert ("juniper", "camera_offline") in kinds
        assert ("cedar", "vacant_activity") in kinds
        assert ("harbor", "gathering") in kinds
        # The Smart-Alerts-off false alarm at Cedar (wind) was checked and muted.
        cedar = next(p for p in st["properties"] if p["property"]["id"] == "cedar")
        wind = [e for e in cedar["events"] if e["classified_as"] == "other"]
        assert wind and wind[0]["disposition"] == "suppressed"
        # The no-show alert resolved itself when someone arrived.
        no_show = next(a for a in st["alerts"] if a["kind"] == "cleaner_no_show")
        assert no_show["status"] == "resolved"
        offline = next(a for a in st["alerts"] if a["kind"] == "camera_offline")
        assert offline["status"] == "resolved"
        # Noise removed: most Ring events never became an alert.
        t = st["totals"]
        assert t["ring_events"] >= 25 and t["suppressed"] >= 15
        gathering = next(a for a in st["alerts"] if a["kind"] == "gathering")
        assert gathering["snapshot_id"] and gathering["vision"]["people_count"] == 9
        assert c.get(f"/api/snapshots/{gathering['snapshot_id']}").status_code == 200
        # Guest privacy: no snapshot for check-in confirmations.
        assert all(a["snapshot_id"] is None for a in st["alerts"] if a["kind"] in ("guest_arrived", "cleaner_arrived"))


def test_on_time_cleaner_and_short_turnover(tmp_path):
    with make_client(tmp_path) as c:
        rt = c.app.state.rt
        dev = "ava1.ring.device.demo-lakeview-front"
        day = rt.player.day
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Los_Angeles")
        def at(h, m):
            return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)

        for h, m in [(11, 10), (11, 30)]:
            rt.clock.set(at(h, m))
            post_event(c, dev, at(h, m))
        rt.clock.set(at(15, 31))
        c.portal.call(rt.drain)
        c.portal.call(rt.engine.tick)
        kinds = {a.kind: a for a in rt.engine.alerts("lakeview")}
        assert "cleaner_no_show" not in kinds
        assert kinds["cleaner_arrived"].body.startswith("Turnover started on time")
        assert "turnover_short" in kinds  # 20 minutes < 75 minute minimum


def test_vision_guardrails():
    assert guard(None, "m").ok is False
    assert guard({"people_count": "lots", "confidence": 0.9}, "m").ok is False
    assert guard({"people_count": 99, "confidence": 0.9}, "m").ok is False
    low = guard({"people_count": 3, "confidence": 0.3}, "m")
    assert not low.ok and "low confidence" in low.fallback_reason
    clash = guard({"people_count": 0, "confidence": 0.95}, "m", ring_sub_type="human")
    assert not clash.ok and "Ring detected a person" in clash.fallback_reason
    scrubbed = guard({"people_count": 2, "confidence": 0.9, "scene": "two young men at the door"}, "m")
    assert scrubbed.ok and scrubbed.summary is None  # descriptive text about people is dropped
    fine = guard({"people_count": 2, "confidence": 0.9, "scene": "two people at the front door"}, "m")
    assert fine.summary == "two people at the front door"
