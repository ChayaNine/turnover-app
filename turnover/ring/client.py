"""Async client for the Ring Partner API (Amazon Vision, https://api.amazonvision.com).

Ring ships REST docs and no official SDK, so this is a thin, typed wrapper that handles the
parts that bite integrators:

* JSON:API compound documents (``?include=status,capabilities,...``)
* 429 with ``Retry-After`` and exponential backoff from 1 s
* 401: refresh once, then surface ``ReauthRequired`` (the user may have removed the app)
* media downloads that answer ``303 See Other`` to a pre-signed URL, which must be fetched
  *without* the bearer token
* TAKE/E2EE devices whose video is unreadable (``configurations.e2e_encryption.enabled``)

All calls are server-to-server: Ring blocks browser-origin requests with CORS.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

import httpx

TokenSource = Callable[[bool], Awaitable[str]]  # force_refresh -> access token


class RingAPIError(Exception):
    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.body = body


class ReauthRequired(RingAPIError):
    """401 after a refresh: the user removed the integration or the refresh token expired."""


class DeviceOffline(RingAPIError):
    """503: the device is offline."""


@dataclass
class Device:
    id: str
    name: str
    online: bool | None
    capabilities: dict[str, Any] = field(default_factory=dict)
    configurations: dict[str, Any] = field(default_factory=dict)
    location: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def encrypted(self) -> bool:
        """True when TAKE/E2EE is on. The value is a JSON boolean, not the "on"/"off"
        strings the rest of the configurations response uses."""
        e2e = self.configurations.get("e2e_encryption")
        return isinstance(e2e, dict) and e2e.get("enabled") is True

    @property
    def is_chime(self) -> bool:
        audio = self.capabilities.get("audio") or {}
        return "chime.play" in (audio.get("supported_actions") or [])

    @property
    def is_camera(self) -> bool:
        caps = self.capabilities
        return "video" in caps or "motion_detection" in caps or "components" in caps


@dataclass
class HistoryEvent:
    id: str
    event_type: str
    start: datetime | None
    device_id: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Media:
    content: bytes
    content_type: str | None
    timestamp_ms: int | None


@dataclass
class SubscriptionCoverage:
    id: str
    status: str | None
    device_ids: list[str]
    location_ids: list[str]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _index_included(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r.get("type"), r.get("id")): r for r in doc.get("included") or [] if isinstance(r, dict)}


def _related(resource: dict[str, Any], rel: str, included: dict) -> dict[str, Any]:
    ref = ((resource.get("relationships") or {}).get(rel) or {}).get("data")
    if not isinstance(ref, dict):
        return {}
    hit = included.get((ref.get("type"), ref.get("id")))
    return (hit or {}).get("attributes") or {}


def parse_devices(doc: dict[str, Any]) -> list[Device]:
    included = _index_included(doc)
    data = doc.get("data") or []
    if isinstance(data, dict):
        data = [data]
    out = []
    for r in data:
        attrs = r.get("attributes") or {}
        status = _related(r, "status", included)
        out.append(
            Device(
                id=r["id"],
                name=attrs.get("name") or r["id"],
                online=status.get("online") if status else None,
                capabilities=_related(r, "capabilities", included),
                configurations=_related(r, "configurations", included),
                location=_related(r, "location", included),
                raw=r,
            )
        )
    return out


def _ms(value: Any) -> datetime | None:
    from datetime import timezone

    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_history(doc: dict[str, Any]) -> tuple[list[HistoryEvent], str | None]:
    from urllib.parse import parse_qs, urlparse

    events = []
    for r in doc.get("data") or []:
        attrs = r.get("attributes") or {}
        src = ((r.get("relationships") or {}).get("source") or {}).get("data") or {}
        events.append(
            HistoryEvent(
                id=str(r.get("id")),
                event_type=attrs.get("event_type", "unknown"),
                start=_ms(attrs.get("start")),
                device_id=src.get("id"),
                raw=r,
            )
        )
    nxt = (doc.get("links") or {}).get("next")
    key = (parse_qs(urlparse(nxt).query).get("page[key]") or [None])[0] if nxt and events else None
    return events, key


def parse_subscriptions(doc: dict[str, Any]) -> list[SubscriptionCoverage]:
    """Read *both* relationships. Since Oct 12 2026 a subscription can be scoped to a
    location; code that only walks ``relationships.devices.data`` treats it as covering
    nothing. Location resources are type "location" (singular)."""
    out = []
    for r in doc.get("data") or []:
        rels = r.get("relationships") or {}
        devs = [d.get("id") for d in (rels.get("devices") or {}).get("data") or [] if isinstance(d, dict)]
        locs = [d.get("id") for d in (rels.get("locations") or {}).get("data") or [] if isinstance(d, dict)]
        out.append(
            SubscriptionCoverage(
                id=str(r.get("id")),
                status=(r.get("attributes") or {}).get("status"),
                device_ids=[d for d in devs if d],
                location_ids=[loc for loc in locs if loc],
                raw=r,
            )
        )
    return out


class RingClient:
    def __init__(
        self,
        token_source: TokenSource,
        *,
        base_url: str = "https://api.amazonvision.com",
        http: httpx.AsyncClient | None = None,
        max_retries: int = 3,
    ) -> None:
        self._token_source = token_source
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._max_retries = max_retries

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        media: bool = False,
    ) -> httpx.Response:
        refreshed = False
        attempt = 0
        while True:
            token = await self._token_source(False if not refreshed else True)
            headers = {"Authorization": f"Bearer {token}"}
            if json is not None:
                headers["Content-Type"] = "application/json"
            resp = await self._http.request(
                method, self._base + path, params=params, json=json, headers=headers, follow_redirects=False
            )
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                continue
            if resp.status_code == 401:
                raise ReauthRequired(401, "token rejected after refresh", _safe_json(resp))
            if resp.status_code == 429 and attempt < self._max_retries:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 2**attempt
                attempt += 1
                await asyncio.sleep(min(delay, 30.0))
                continue
            if media and resp.status_code in (301, 302, 303, 307, 308):
                # Pre-signed media URL: never forward the bearer token to it.
                location = resp.headers["Location"]
                return await self._http.get(location, follow_redirects=True)
            if resp.status_code == 503:
                raise DeviceOffline(503, "device offline", _safe_json(resp))
            if resp.status_code >= 400:
                raise RingAPIError(resp.status_code, _error_detail(resp), _safe_json(resp))
            return resp

    # --- devices -------------------------------------------------------------------------

    async def devices(self, include: tuple[str, ...] = ("status", "capabilities", "location", "configurations")) -> list[Device]:
        params = {"include": ",".join(include)} if include else None
        resp = await self._request("GET", "/v1/devices", params=params)
        return parse_devices(resp.json())

    async def status(self, device_id: str) -> dict[str, Any]:
        return ((await self._request("GET", f"/v1/devices/{device_id}/status")).json().get("data") or {}).get(
            "attributes"
        ) or {}

    async def configurations(self, device_id: str) -> dict[str, Any]:
        doc = (await self._request("GET", f"/v1/devices/{device_id}/configurations")).json()
        return (doc.get("data") or {}).get("attributes") or {}

    async def me(self) -> str:
        """The authenticated Ring Account ID (used for nonce matching and correlation)."""
        doc = (await self._request("GET", "/v1/users/me")).json()
        return str(doc["data"]["id"])

    # --- history -------------------------------------------------------------------------

    async def history(
        self, device_id: str, event_types: list[str] | None = None, max_pages: int = 5
    ) -> list[HistoryEvent]:
        out: list[HistoryEvent] = []
        key: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {}
            if event_types:
                # Plural, comma-separated; composite filters like "motion.human" work.
                params["event_types"] = ",".join(event_types)
            if key:
                params["page[key]"] = key
            resp = await self._request("GET", f"/v1/history/devices/{device_id}/events", params=params or None)
            events, key = parse_history(resp.json())
            out.extend(events)
            if not key:
                break
        return out

    # --- media ---------------------------------------------------------------------------

    async def snapshot(
        self,
        device_id: str,
        *,
        at: datetime | None = None,
        between: tuple[datetime, datetime] | None = None,
        fmt: Literal["jpeg", "png"] = "jpeg",
        component_id: str | None = None,
    ) -> Media:
        if (at is None) == (between is None):
            raise ValueError("pass exactly one of at= or between=")
        if at is not None:
            body: dict[str, Any] = {"type": "at_timestamp", "timestamp": int(at.timestamp() * 1000)}
        else:
            body = {
                "type": "latest_in_range",
                "start_timestamp": int(between[0].timestamp() * 1000),
                "end_timestamp": int(between[1].timestamp() * 1000),
            }
        body["image_options"] = {"format": fmt}
        if component_id is not None:
            # Multi-camera devices: exactly one component per request.
            body["components"] = [{"component_id": str(component_id)}]
        resp = await self._request("POST", f"/v1/devices/{device_id}/media/image/download", json=body, media=True)
        ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ctype not in ("image/jpeg", "image/png"):
            doc = _safe_json(resp)
            if isinstance(doc, dict) and isinstance(doc.get("errors"), list) and doc["errors"]:
                err = doc["errors"][0]
                if isinstance(err, dict):
                    raw_status = str(err.get("status", ""))
                    status = int(raw_status) if raw_status.isdigit() and 400 <= int(raw_status) <= 599 else 502
                    code = err.get("code") or "MEDIA_ERROR"
                    detail = err.get("detail") or err.get("title") or "Snapshot unavailable"
                    raise RingAPIError(status, f"{code}: {detail}", doc)
            raise RingAPIError(502, f"Ring returned {ctype or 'unknown content'} instead of an image")
        if ctype == "image/jpeg" and not resp.content.startswith(bytes.fromhex("ffd8ff")):
            raise RingAPIError(502, "Ring returned invalid JPEG bytes")
        if ctype == "image/png" and not resp.content.startswith(bytes.fromhex("89504e470d0a1a0a")):
            raise RingAPIError(502, "Ring returned invalid PNG bytes")
        ts = resp.headers.get("x-media-timestamp")
        return Media(resp.content, ctype, int(ts) if ts and ts.isdigit() else None)

    async def play_chime(self, device_id: str, audio_ref: str) -> None:
        await self._request("POST", f"/v1/devices/{device_id}/media/audio/playback", json={"audio_ref": audio_ref})

    # --- account & integration -----------------------------------------------------------

    async def subscriptions(self) -> list[SubscriptionCoverage]:
        return parse_subscriptions((await self._request("GET", "/v1/accounts/me/subscriptions")).json())

    async def confirm_link(self, nonce: str, account_identifier: str) -> dict[str, Any]:
        """Step 1 of 2: send the nonce back for Ring-side verification -> status `awaiting`."""
        resp = await self._request(
            "POST",
            "/v1/accounts/me/app-integrations",
            json={"account_identifier": account_identifier, "nonce": nonce},
        )
        return _safe_json(resp) or {}

    async def set_integration_status(self, status: Literal["awaiting", "completed"]) -> dict[str, Any]:
        """Step 2 of 2: `completed` once the host has finished setup. `awaiting` pauses."""
        resp = await self._request("PATCH", "/v1/accounts/me/app-integrations", json={"status": status})
        return _safe_json(resp) or {}


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _error_detail(resp: httpx.Response) -> str:
    body = _safe_json(resp)
    if isinstance(body, dict):
        errs = body.get("errors")
        if isinstance(errs, list) and errs:
            e = errs[0]
            return str(e.get("detail") or e.get("title") or e)
    return resp.reason_phrase or "error"
