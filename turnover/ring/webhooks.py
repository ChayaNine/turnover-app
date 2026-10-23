"""Ring webhook verification and parsing (payload v1.1).

Ring signs each delivery with HMAC-SHA256 over the *raw* request body, hex encoded, in
``X-Signature: sha256=<hex>``. Verify the bytes you received before parsing: re-serialising
the JSON changes the bytes and breaks the digest.

The same HMAC key is used for account-linking nonces, but nonces are URL-safe base64 with
no padding (see ``ring.linking``). Mixing the two encodings is a documented common mistake.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SIGNATURE_HEADER = "X-Signature"

DEVICE_EVENTS = frozenset(
    {"motion_detected", "button_press", "device_added", "device_removed", "device_online", "device_offline"}
)
INTEGRATION_EVENTS = frozenset({"app_integration_added", "app_integration_removed"})
SUBSCRIPTION_EVENTS = frozenset({"subscription_activated", "subscription_deactivated"})
KNOWN_EVENTS = DEVICE_EVENTS | INTEGRATION_EVENTS | SUBSCRIPTION_EVENTS

# Documented motion sub_types. With Smart Alerts off Ring sends "motion" for everything, and
# no API reports whether Smart Alerts is on, so this is an open vocabulary: unknown values
# are passed through, never dropped (release notes, Oct 5 2026).
MOTION_SUB_TYPES = ("motion", "human", "vehicle", "package_delivery", "other_motion")


class InvalidSignature(Exception):
    """The X-Signature header did not match the body."""


def sign(signing_key: str, raw_body: bytes) -> str:
    """The X-Signature value Ring sends for ``raw_body``."""
    return "sha256=" + hmac.new(signing_key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify(signing_key: str, raw_body: bytes, header_value: str | None) -> bool:
    """Constant-time check of an X-Signature header against the raw body."""
    if not signing_key or not header_value:
        return False
    received = header_value.strip()
    if received.lower().startswith("sha256="):
        received = received[len("sha256="):]
    expected = hmac.new(signing_key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received.lower())


def _ms_to_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None


@dataclass
class WebhookEvent:
    id: str
    type: str
    request_id: str | None
    occurred_at: datetime | None
    device_id: str | None = None
    sub_type: str | None = None
    component_ids: list[str] = field(default_factory=list)
    account_id: str | None = None
    covered_device_ids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def dedupe_key(self) -> str:
        # Ring retries failed deliveries with the same request_id.
        return self.request_id or self.id

    @property
    def known(self) -> bool:
        return self.type in KNOWN_EVENTS


def parse(raw_body: bytes | str | dict[str, Any]) -> WebhookEvent:
    doc = raw_body if isinstance(raw_body, dict) else json.loads(raw_body)
    meta = doc.get("meta") or {}
    data = doc.get("data") or {}
    attrs = data.get("attributes") or {}
    source_type = attrs.get("source_type")
    source = attrs.get("source")
    # `source` is a device id only for device events; app_integration_* carries the account
    # id (source_type "users"); subscription_* lists covered devices in relationships.
    device_id = source if source_type == "devices" else None
    rel = ((data.get("relationships") or {}).get("devices") or {}).get("data") or []
    covered = [d.get("id") for d in rel if isinstance(d, dict) and d.get("id")]
    account_id = meta.get("account_id") or (source if source_type == "users" else None)
    return WebhookEvent(
        id=str(data.get("id", "")),
        type=str(data.get("type", "unknown")),
        request_id=meta.get("request_id"),
        occurred_at=_ms_to_dt(attrs.get("timestamp")) or _ms_to_dt(meta.get("time")),
        device_id=device_id,
        sub_type=attrs.get("sub_type"),
        component_ids=[str(c) for c in attrs.get("component_ids") or []],
        account_id=account_id,
        covered_device_ids=covered,
        raw=doc,
    )


def verify_and_parse(signing_key: str, raw_body: bytes, header_value: str | None) -> WebhookEvent:
    if not verify(signing_key, raw_body, header_value):
        raise InvalidSignature("X-Signature does not match the request body")
    return parse(raw_body)


# --- Payload builder, used by the simulator and the tests ---------------------------------

def build_payload(
    event_type: str,
    device_id: str | None,
    when: datetime,
    *,
    sub_type: str | None = None,
    request_id: str,
    account_id: str = "ava1.ring.account.DEMO",
    component_ids: list[str] | None = None,
) -> dict[str, Any]:
    """A v1.1 payload shaped like the documented examples."""
    ts = int(when.timestamp() * 1000)
    meta = {
        "version": "1.1",
        "time": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "request_id": request_id,
        "account_id": account_id,
    }
    if event_type in INTEGRATION_EVENTS:
        return {
            "meta": meta,
            "data": {
                "id": request_id,
                "type": event_type,
                "attributes": {"source": account_id, "source_type": "users", "timestamp": ts},
            },
        }
    attrs: dict[str, Any] = {"source": device_id, "source_type": "devices", "timestamp": ts}
    segment = event_type
    if event_type == "motion_detected":
        attrs["sub_type"] = sub_type or "motion"
        attrs["timestamp_readable"] = when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        segment = attrs["sub_type"]
    if component_ids:
        attrs["component_ids"] = component_ids
    return {
        "meta": meta,
        "data": {
            "id": f"{device_id}_{segment}_{ts}",
            "type": event_type,
            "attributes": attrs,
            "relationships": {"devices": {"links": {"self": f"/v1/devices/{device_id}"}}},
        },
    }


def encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")
