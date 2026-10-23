"""One-way account linking through the real routes, with Ring's API stubbed."""

import time

from fastapi.testclient import TestClient

from turnover import config
from turnover.app import create_app
from turnover.ring import client as ring_client
from turnover.ring.linking import compute_nonce

KEY = "hmac-key"


def test_link_then_setup_then_complete(tmp_path, monkeypatch):
    calls = []

    async def confirm_link(self, nonce, account_identifier):
        calls.append(("POST", nonce, account_identifier))
        return {}

    async def set_status(self, status):
        calls.append(("PATCH", status))
        return {}

    async def devices(self, include=()):
        return [ring_client.Device("ava1.ring.device.REAL1", "Front Door", True, capabilities={"video": {}}, configurations={"e2e_encryption": {"enabled": False}})]

    monkeypatch.setattr(ring_client.RingClient, "confirm_link", confirm_link)
    monkeypatch.setattr(ring_client.RingClient, "set_integration_status", set_status)
    monkeypatch.setattr(ring_client.RingClient, "devices", devices)

    s = config.Settings(db_path=str(tmp_path / "l.db"), demo_mode=True, ring_hmac_key=KEY, host_email="host@example.com", host_password="pw")
    app = create_app(s)
    with TestClient(app) as c:
        rt = app.state.rt
        rt.tokens.park("acct-1", {"access_token": "a1", "refresh_token": "r1", "expires_in": 14400})
        rt.tokens.park("acct-2", {"access_token": "a2", "refresh_token": "r2", "expires_in": 14400})
        t = str(int(time.time() * 1000))
        nonce = compute_nonce(t, "acct-2", KEY)

        # The link page is a sign-in form, never an automatic verifier.
        page = c.get(f"/ring/link?nonce={nonce}&time={t}")
        assert page.status_code == 200 and 'type="password"' in page.text
        assert calls == []

        bad = c.post("/ring/link", data={"nonce": nonce, "time": t, "email": "host@example.com", "password": "nope"}, follow_redirects=False)
        assert bad.status_code == 303 and "Wrong" in bad.headers["location"] and calls == []

        stale = str(int(time.time() * 1000) - 700_000)
        old = c.post("/ring/link", data={"nonce": compute_nonce(stale, "acct-2", KEY), "time": stale, "email": "host@example.com", "password": "pw"}, follow_redirects=False)
        assert "expired" in old.headers["location"] and calls == []

        ok = c.post("/ring/link", data={"nonce": nonce, "time": t, "email": "host@example.com", "password": "pw"}, follow_redirects=False)
        assert ok.status_code == 303 and ok.headers["location"] == "/ring/setup"
        assert calls == [("POST", nonce, "h***t@example.com")]
        status = {r.account_id: r.status for r in rt.tokens.records()}
        assert status == {"acct-1": "unclaimed", "acct-2": "awaiting"}  # only the matching token

        setup = c.get("/ring/setup")
        assert "Front Door" in setup.text
        done = c.post("/ring/setup/complete", data={"ava1.ring.device.REAL1": "juniper"}, follow_redirects=False)
        assert done.status_code == 303
        assert calls[-1] == ("PATCH", "completed")
        assert "ava1.ring.device.REAL1" in rt.engine.property("juniper").device_ids
        assert {r.account_id: r.status for r in rt.tokens.records()}["acct-2"] == "completed"


def test_dashboard_password_protects_dashboard_not_ring_routes(tmp_path):
    s = config.Settings(db_path=str(tmp_path / "p.db"), demo_mode=True, ring_hmac_key=KEY, dashboard_password="secret")
    with TestClient(create_app(s)) as c:
        assert c.get("/api/state").status_code == 401
        assert c.get("/api/state", auth=("host", "secret")).status_code == 200
        assert c.get("/healthz").status_code == 200
        # Ring-facing routes authenticate by signature, not by the dashboard password.
        assert c.post("/webhooks/ring", content=b"{}", headers={"X-Signature": "sha256=00"}).status_code == 401
        assert c.get("/ring/link").status_code == 200
