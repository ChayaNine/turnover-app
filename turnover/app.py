"""Turnover web service.

Routes
  POST /webhooks/ring             Ring webhook URL: verify X-Signature, de-duplicate, 200 fast
  POST /ring/token-exchange       Ring Token Exchange URL (one-way account linking)
  GET  /ring/link                 Ring Account Link URL: mandatory partner sign-in
  POST /ring/link                 sign-in -> nonce match -> POST app-integrations (awaiting)
  GET  /ring/setup                Ring App Homepage URL: map devices to properties
  POST /ring/setup/complete       PATCH app-integrations status=completed
  GET  /                          host dashboard
  GET  /api/state, /api/stream    dashboard data, live server-sent events
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import brief as briefing
from . import config
from . import mcp_server
from .bookings import parse_ical
from .clock import RealClock, VirtualClock
from .engine import Engine
from .media import SnapshotService
from .models import Booking, Property, Severity
from .notify import Hub, Notifier
from .ring import webhooks
from .ring.client import RingAPIError, RingClient
from .ring.linking import LinkError, TokenManager, check_freshness, mask_email
from .simulator import DEMO_TZ, Player, START, demo_bookings, demo_day, demo_properties, register_fixtures
from .store import Store
from .vision import BedrockVision, FixtureVision, NoVision

log = logging.getLogger("turnover")
STATIC = Path(__file__).resolve().parent / "static"


class Runtime:
    def __init__(self, settings: config.Settings) -> None:
        self.settings = settings
        self.store = Store(settings.db_path)
        self.clock = VirtualClock() if settings.demo_mode else RealClock()
        self.hub = Hub()
        self.notifier = Notifier(self.hub, sns_topic_arn=settings.sns_topic_arn, region=settings.aws_region)
        self.tokens = TokenManager(
            self.store, client_id=settings.ring_client_id, client_secret=settings.ring_client_secret, oauth_url=settings.ring_oauth_url
        )
        if settings.ring_access_token:
            self.tokens.playground_token = settings.ring_access_token
        self.ring: RingClient | None = RingClient(self.tokens.source_for(None), base_url=settings.ring_api_base)
        self.media = SnapshotService(self.store, self.ring)
        self.bedrock = None
        if settings.bedrock_enabled:
            import boto3

            self.bedrock = boto3.client("bedrock-runtime", region_name=settings.aws_region)
            self.vision = BedrockVision(settings.bedrock_model_id, settings.aws_region, guardrail_id=settings.bedrock_guardrail_id)
        elif settings.demo_mode:
            self.vision = FixtureVision()
            register_fixtures(self.vision)
        else:
            self.vision = NoVision()
        self.engine = Engine(self.store, self.clock, self.media, self.notifier, self.vision)
        self.queue: asyncio.Queue[webhooks.WebhookEvent] = asyncio.Queue()
        self.player: Player | None = None
        self.ring_devices: list[dict[str, Any]] = []
        self.ring_account: str | None = None
        self.sessions: dict[str, str] = {}
        self._tasks: list[asyncio.Task] = []

    async def drain(self) -> None:
        await self.queue.join()

    async def worker(self) -> None:
        while True:
            evt = await self.queue.get()
            try:
                await self.engine.ingest(evt)
            except Exception:
                log.exception("failed to process %s", evt.dedupe_key)
            finally:
                self.queue.task_done()

    async def ticker(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(15)
            n += 1
            try:
                await self.engine.tick()
                if n % 40 == 0:  # every 10 min: reconcile with Ring Event History
                    await reconcile(self)
                if n % 5760 == 0:  # daily: proactive token refresh
                    await self.tokens.refresh_all()
                if n % 240 == 0:  # hourly: snapshot retention, 7 days
                    self.store.purge_blobs_before((self.clock.now() - timedelta(days=7)).isoformat())
            except Exception:
                log.exception("tick failed")

    def seed_demo(self) -> None:
        self.store.clear(["properties", "bookings", "events", "alerts", "expectations", "visits"])
        day = demo_day()
        for p in demo_properties():
            self.engine.save_property(p)
        for b in demo_bookings(day):
            self.engine.save_booking(b)
        hh, mm = map(int, START.split(":"))
        self.clock.set(datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo(DEMO_TZ)).replace(hour=hh, minute=mm) - timedelta(minutes=1))
        self.media.clear_demo_frames()
        for k in ("gathering_check:harbor", "gathering_check:lakeview", "gathering_check:juniper", "gathering_check:cedar"):
            self.store.delete("kv", k)
        if self.player:
            self.player.day = day


async def reconcile(rt: Runtime) -> int:
    """Backfill from Ring Event History: webhooks can be missed during a deploy or outage,
    history is the source of truth. Deduplicated against what webhooks already delivered."""
    if rt.ring is None or not (rt.tokens.playground_token or rt.tokens.records()):
        return 0
    added = 0
    for prop in rt.engine.properties():
        for device_id in prop.device_ids:
            if device_id.startswith("ava1.ring.device.demo-"):
                continue
            try:
                items = await rt.ring.history(device_id, max_pages=2)
            except (RingAPIError, LinkError):
                continue
            for h in items:
                if h.start is None:
                    continue
                et = h.event_type.lower()
                if "ding" in et or "button" in et:
                    typ, sub = "button_press", None
                elif et.startswith("motion"):
                    typ, sub = "motion_detected", (et.split(".", 1)[1] if "." in et else "motion")
                else:
                    continue
                ts = int(h.start.timestamp() * 1000)
                evt = webhooks.WebhookEvent(
                    id=f"{device_id}_{sub or typ}_{ts}", type=typ, request_id=None, occurred_at=h.start, device_id=device_id, sub_type=sub
                )
                if await rt.engine.ingest(evt, source="history"):
                    added += 1
    return added


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.load()
    rt = Runtime(settings)
    mcp = mcp_server.build(rt)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=mcp_server.transport_security(settings.public_base_url),
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            async with _lifespan(app):
                yield

    @contextlib.asynccontextmanager
    async def _lifespan(app: FastAPI):
        rt._tasks.append(asyncio.create_task(rt.worker()))
        if settings.demo_mode:
            rt.seed_demo()
            transport = httpx.ASGITransport(app=app)
            client = httpx.AsyncClient(transport=transport, base_url="http://turnover.internal")

            async def deliver(body: bytes, headers: dict[str, str]) -> int:
                r = await client.post("/webhooks/ring", content=body, headers=headers)
                return r.status_code

            rt.player = Player(rt, deliver)
            rt.player.day = demo_day()
        else:
            rt._tasks.append(asyncio.create_task(rt.ticker()))
        yield
        for t in rt._tasks:
            t.cancel()
        if rt.player:
            await rt.player.stop()

    app = FastAPI(title="Turnover", lifespan=lifespan)
    app.state.rt = rt

    if settings.dashboard_password:
        import base64

        open_prefixes = ("/webhooks/", "/ring/", "/healthz", "/static/")

        @app.middleware("http")
        async def dashboard_auth(request: Request, call_next):
            if request.url.path.startswith(open_prefixes):
                return await call_next(request)
            header = request.headers.get("authorization", "")
            ok = False
            if header.lower().startswith("basic "):
                try:
                    user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                    ok = secrets.compare_digest(pw, settings.dashboard_password or "")
                except ValueError:
                    ok = False
            if not ok:
                return Response("Sign in to see the Turnover dashboard.", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Turnover"'})
            return await call_next(request)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ------------------------------------------------------------------ Ring webhooks

    @app.post("/webhooks/ring")
    async def ring_webhook(request: Request) -> JSONResponse:
        raw = await request.body()  # verify the exact bytes Ring signed
        key = settings.webhook_signing_key
        if not webhooks.verify(key, raw, request.headers.get(webhooks.SIGNATURE_HEADER)):
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        try:
            evt = webhooks.parse(raw)
        except ValueError:
            return JSONResponse({"error": "malformed payload"}, status_code=400)
        # Ring retries with the same request_id: acknowledge duplicates without reprocessing.
        if not rt.store.insert_once("webhook_requests", evt.dedupe_key, {"type": evt.type}):
            return JSONResponse({"status": "duplicate", "request_id": evt.request_id})
        # Respond inside Ring's 5-second budget; the worker does the rest.
        await rt.queue.put(evt)
        return JSONResponse({"status": "accepted", "request_id": evt.request_id})

    # ------------------------------------------------------------------ account linking

    @app.post("/ring/token-exchange")
    async def token_exchange(request: Request) -> JSONResponse:
        ctype = request.headers.get("content-type", "")
        data = await request.json() if "json" in ctype else dict(await request.form())
        code = data.get("code") or data.get("authorization_code")
        if not code:
            raise HTTPException(400, "missing code")
        try:
            tokens = await rt.tokens.exchange_code(str(code))  # within the 60 s code lifetime
            access = tokens["access_token"]

            async def fixed(force: bool) -> str:
                return access

            account_id = await RingClient(fixed, base_url=settings.ring_api_base).me()
        except (LinkError, RingAPIError, KeyError) as exc:
            log.warning("token exchange failed: %s", exc)
            raise HTTPException(502, "token exchange failed")
        rt.tokens.park(account_id, tokens)  # unclaimed until the host signs in and the nonce matches
        return JSONResponse({"status": "ok"})

    @app.get("/ring/link", response_class=HTMLResponse)
    async def link_page(nonce: str = "", time: str = "", error: str = "") -> HTMLResponse:
        return HTMLResponse(_page("Connect Ring to Turnover", f"""
          <p class="lede">Sign in to finish connecting your Ring account. Turnover will only see the devices you chose in the Ring app.</p>
          {f'<p class="err">{html.escape(error)}</p>' if error else ''}
          <form method="post" action="/ring/link">
            <input type="hidden" name="nonce" value="{html.escape(nonce)}"><input type="hidden" name="time" value="{html.escape(time)}">
            <label>Email<input name="email" type="email" required autocomplete="username"></label>
            <label>Password<input name="password" type="password" required autocomplete="current-password"></label>
            <button type="submit">Sign in and connect</button>
          </form>"""))

    @app.post("/ring/link")
    async def link_submit(nonce: str = Form(...), time: str = Form(...), email: str = Form(...), password: str = Form(...)):
        # 1. partner sign-in is mandatory and happens first
        if not (secrets.compare_digest(email.lower(), settings.host_email.lower()) and secrets.compare_digest(password, settings.host_password)):
            return RedirectResponse(f"/ring/link?nonce={nonce}&time={time}&error=Wrong+email+or+password", status_code=303)
        # 2. freshness, then nonce match over unclaimed tokens
        try:
            check_freshness(time)
            if not settings.ring_hmac_key:
                raise LinkError("RING_HMAC_KEY not configured")
            rec = rt.tokens.match_nonce(nonce, time, settings.ring_hmac_key)
            if rec is None:
                raise LinkError("This link didn't match a pending Ring connection. Start again from the Ring app.")
            # 3. POST app-integrations with the nonce -> awaiting
            client = RingClient(rt.tokens.source_for(rec.account_id), base_url=settings.ring_api_base)
            await client.confirm_link(nonce, mask_email(email))
        except (LinkError, RingAPIError) as exc:
            return RedirectResponse(f"/ring/link?nonce={nonce}&time={time}&error={html.escape(str(exc))}", status_code=303)
        rt.tokens.mark(rec.account_id, status="awaiting", claimed_by=email)
        sid = secrets.token_urlsafe(16)
        rt.sessions[sid] = rec.account_id
        resp = RedirectResponse("/ring/setup", status_code=303)
        resp.set_cookie("turnover_sid", sid, httponly=True, secure=settings.public_base_url.startswith("https"), samesite="lax")
        return resp

    @app.get("/ring/setup", response_class=HTMLResponse)
    async def setup_page(request: Request) -> HTMLResponse:
        account = rt.sessions.get(request.cookies.get("turnover_sid", ""))
        if not account:
            return HTMLResponse(_page("Turnover setup", "<p>Open Turnover from the Ring app to set it up.</p>"))
        try:
            devices = await RingClient(rt.tokens.source_for(account), base_url=settings.ring_api_base).devices()
        except (RingAPIError, LinkError) as exc:
            return HTMLResponse(_page("Turnover setup", f"<p class='err'>Couldn't load your devices: {html.escape(str(exc))}</p>"))
        opts = "".join(f"<option value='{p.id}'>{html.escape(p.name)}</option>" for p in rt.engine.properties())
        rows = "".join(
            f"<label>{html.escape(d.name)}{' · video encrypted' if d.encrypted else ''}<select name='{html.escape(d.id)}'><option value=''>Not used</option>{opts}</select></label>"
            for d in devices if not d.is_chime  # chimes play audio; everything else can see the door
        )
        return HTMLResponse(_page("Which home is each camera at?", f"""
          <p class="lede">Turnover reads your booking calendar for each home, so it needs to know where each camera is.</p>
          <form method="post" action="/ring/setup/complete">{rows}<button type="submit">Finish setup</button></form>"""))

    @app.post("/ring/setup/complete")
    async def setup_complete(request: Request):
        account = rt.sessions.get(request.cookies.get("turnover_sid", ""))
        if not account:
            raise HTTPException(401, "no session")
        form = dict(await request.form())
        for prop in rt.engine.properties():
            prop.device_ids = sorted({*prop.device_ids, *[d for d, pid in form.items() if pid == prop.id]})
            rt.engine.save_property(prop)
        try:
            await RingClient(rt.tokens.source_for(account), base_url=settings.ring_api_base).set_integration_status("completed")
        except (RingAPIError, LinkError) as exc:
            return HTMLResponse(_page("Almost there", f"<p class='err'>Ring didn't accept the final step: {html.escape(str(exc))}</p>"))
        rt.tokens.mark(account, status="completed")
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------ dashboard API

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "demo": settings.demo_mode}

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        now = rt.clock.now()
        days = [rt.engine.snapshot_of_day(p, now) for p in rt.engine.properties()]
        totals = {k: sum(d["metrics"][k] for d in days) for k in ("ring_events", "suppressed", "tracked", "alerts")}
        return {
            "now": now.isoformat(),
            "virtual_clock": rt.clock.virtual,
            "demo": settings.demo_mode,
            "player": rt.player.status() if rt.player else None,
            "vision": rt.vision.name,
            "channels": rt.notifier.channels,
            "ring": {"connected": bool(rt.tokens.playground_token or rt.tokens.records()), "account": rt.ring_account, "devices": rt.ring_devices},
            "totals": totals,
            "properties": days,
            "alerts": [a.to_dict() for a in rt.engine.alerts(limit=60)],
        }

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        q = rt.hub.subscribe()

        async def gen():
            try:
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=20)
                        yield f"data: {msg}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                rt.hub.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/alerts/{alert_id}/actions/{action}")
    async def alert_action(alert_id: str, action: str) -> dict[str, Any]:
        a = rt.engine.act(alert_id, action)
        if a is None:
            raise HTTPException(404, "no such alert")
        prop = rt.engine.property(a.property_id)
        def twelve(hhmm: str) -> str:
            return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")

        if prop and action == "text_backup":
            await rt.notifier.message(
                prop.backup_cleaner_name or "backup cleaner",
                f"Hi {prop.backup_cleaner_name or 'there'}, can you turn over {prop.name} today? Guests arrive at {twelve(prop.checkin_time)}.",
            )
        elif prop and action == "message_guest":
            await rt.notifier.message("guest", f"Hi! A friendly reminder that quiet hours at {prop.name} start at {twelve(prop.quiet_start)}. Thanks for being great guests.")
        return a.to_dict()

    @app.get("/api/snapshots/{sid}")
    async def snapshot(sid: str) -> Response:
        blob = rt.store.get_blob(sid)
        if blob is None:
            raise HTTPException(404)
        return Response(blob[0], media_type=blob[1], headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/properties")
    async def create_property(request: Request) -> dict[str, Any]:
        """Add a home: {"name", "city", "timezone", "checkin_time", "checkout_time", "max_guests",
        "cleaner_name", "backup_cleaner_name", "ical_url"?, "device_ids"?}."""
        body = await request.json()
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "name is required")
        try:
            ZoneInfo(body.get("timezone", "America/Los_Angeles"))
        except Exception:
            raise HTTPException(400, "timezone must be an IANA name, e.g. America/Los_Angeles")
        pid = "".join(ch for ch in name.lower().replace(" ", "-") if ch.isalnum() or ch == "-")[:40] or secrets.token_hex(4)
        if rt.engine.property(pid):
            raise HTTPException(409, f"a home with id {pid} already exists")
        prop = Property.from_dict({"id": pid, "city": "", "timezone": "America/Los_Angeles", "device_ids": [], **body, "name": name})
        rt.engine.save_property(prop)
        if prop.ical_url:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                r = await c.get(prop.ical_url)
                r.raise_for_status()
            rt.engine.replace_bookings(pid, parse_ical(r.text, pid))
        return prop.to_dict()

    @app.post("/api/properties/{pid}/brief")
    async def property_brief(pid: str) -> dict[str, str]:
        prop = rt.engine.property(pid)
        if prop is None:
            raise HTTPException(404)
        day = rt.engine.snapshot_of_day(prop)
        alerts = [a.to_dict() for a in rt.engine.alerts(pid, limit=40) if a.created_at >= datetime.fromisoformat(day["day_start"])]
        return await briefing.write(day, alerts, bedrock_client=rt.bedrock, model_id=settings.bedrock_model_id)

    @app.post("/api/properties/{pid}/calendar")
    async def property_calendar(pid: str, request: Request) -> dict[str, Any]:
        """Import an Airbnb / VRBO / Booking.com iCal export (URL or pasted text)."""
        prop = rt.engine.property(pid)
        if prop is None:
            raise HTTPException(404)
        body = await request.json()
        text = body.get("ical")
        if not text and body.get("url"):
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                r = await c.get(body["url"])
                r.raise_for_status()
                text = r.text
            prop.ical_url = body["url"]
            rt.engine.save_property(prop)
        if not text:
            raise HTTPException(400, "send {url} or {ical}")
        items = parse_ical(text, pid)
        rt.engine.replace_bookings(pid, items)
        return {"bookings": [b.to_dict() for b in items]}

    @app.post("/api/properties/{pid}/devices")
    async def property_devices(pid: str, request: Request) -> dict[str, Any]:
        prop = rt.engine.property(pid)
        if prop is None:
            raise HTTPException(404)
        body = await request.json()
        prop.device_ids = [str(d) for d in body.get("device_ids", [])]
        rt.engine.save_property(prop)
        return prop.to_dict()

    # ------------------------------------------------------------------ Ring (playground)

    @app.post("/api/ring/playground")
    async def ring_playground(request: Request) -> dict[str, Any]:
        """Connect with a Developer Playground token (~30 min). Kept in memory only."""
        body = await request.json()
        token = str(body.get("token", "")).strip()
        if not token:
            raise HTTPException(400, "token required")
        rt.tokens.playground_token = token
        try:
            rt.ring_account = await rt.ring.me()
            devices = await rt.ring.devices()
        except (RingAPIError, LinkError) as exc:
            rt.tokens.playground_token = None
            raise HTTPException(400, f"Ring rejected the token: {exc}")
        rt.ring_devices = []
        for d in devices:
            rt.media.mark_encrypted(d.id, d.encrypted)
            rt.ring_devices.append({"id": d.id, "name": d.name, "online": d.online, "encrypted": d.encrypted, "chime": d.is_chime, "camera": d.is_camera, "location": d.location})
        return {"account": rt.ring_account, "devices": rt.ring_devices}

    @app.post("/api/ring/reconcile")
    async def ring_reconcile() -> dict[str, int]:
        return {"backfilled": await reconcile(rt)}

    @app.get("/api/ring/devices/{device_id}/snapshot")
    async def ring_live_snapshot(device_id: str) -> Response:
        if rt.ring is None:
            raise HTTPException(400)
        now = datetime.now(ZoneInfo("UTC"))
        try:
            media = await rt.ring.snapshot(device_id, between=(now - timedelta(minutes=10), now))
        except RingAPIError as exc:
            raise HTTPException(exc.status, str(exc))
        return Response(media.content, media_type=media.content_type or "image/jpeg")

    # ------------------------------------------------------------------ demo controls

    def _player() -> Player:
        if not rt.player:
            raise HTTPException(400, "demo mode is off")
        return rt.player

    @app.post("/api/demo/start")
    async def demo_start(request: Request) -> dict[str, Any]:
        p = _player()
        body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
        if p.running:
            p.resume()
            return p.status()
        rt.seed_demo()
        if "minute_delay" in body:
            p.minute_delay = max(0.01, float(body["minute_delay"]))
        if "pause_on_urgent" in body:
            p.pause_on_urgent = bool(body["pause_on_urgent"])
        rt.hub.publish("reset", {})
        p.start()
        return p.status()

    @app.post("/api/demo/pause")
    async def demo_pause() -> dict[str, Any]:
        p = _player()
        p.pause()
        rt.hub.publish("clock", {"now": rt.clock.now().isoformat(), **p.status()})
        return p.status()

    @app.post("/api/demo/resume")
    async def demo_resume() -> dict[str, Any]:
        p = _player()
        p.resume()
        return p.status()

    @app.post("/api/demo/speed")
    async def demo_speed(request: Request) -> dict[str, Any]:
        p = _player()
        body = await request.json()
        p.minute_delay = max(0.01, float(body.get("minute_delay", p.minute_delay)))
        if "pause_on_urgent" in body:
            p.pause_on_urgent = bool(body["pause_on_urgent"])
        return p.status()

    @app.post("/api/demo/reset")
    async def demo_reset() -> dict[str, Any]:
        p = _player()
        await p.stop()
        rt.seed_demo()
        rt.hub.publish("reset", {})
        return p.status()

    # MCP (Streamable HTTP) at /mcp, mounted last so every route above takes precedence.
    app.mount("/", mcp_app)
    return app


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · Turnover</title><link rel="stylesheet" href="/static/app.css"></head>
<body class="plain"><main class="card narrow"><div class="brand"><svg class="mark" viewBox="0 0 24 24" aria-hidden="true"><rect x="1" y="1" width="22" height="22" rx="7" fill="currentColor"/><rect x="8" y="6" width="8" height="12.5" rx="1.4" fill="none" stroke="#fff" stroke-width="1.7"/><circle cx="13.7" cy="12.4" r="1.05" fill="#F2B544"/></svg>Turnover</div><h1>{html.escape(title)}</h1>{body}</main></body></html>"""

