"""Snapshots: fetched only when an alert needs one, kept only as long as the alert does.

Privacy by design: Turnover never pulls video for routine events, never for guest check-in
or cleaner arrival confirmations, and never runs CV on encrypted (TAKE/E2EE) devices.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .ring.client import RingAPIError, RingClient
from .store import Store


@dataclass
class Snapshot:
    id: str | None
    image: bytes | None
    content_type: str | None
    note: str | None = None


class SnapshotService:
    def __init__(self, store: Store, ring: RingClient | None = None) -> None:
        self._store = store
        self.ring = ring
        self._encrypted: set[str] = set()
        # Demo: the simulator registers the frame a synthetic camera "saw" at a timestamp.
        self._demo_frames: dict[tuple[str, int], Path] = {}

    def mark_encrypted(self, device_id: str, encrypted: bool) -> None:
        (self._encrypted.add if encrypted else self._encrypted.discard)(device_id)

    def is_encrypted(self, device_id: str) -> bool:
        return device_id in self._encrypted

    def register_demo_frame(self, device_id: str, at: datetime, path: Path) -> None:
        self._demo_frames[(device_id, int(at.timestamp()))] = path

    def clear_demo_frames(self) -> None:
        self._demo_frames.clear()

    async def fetch(self, device_id: str, at: datetime) -> Snapshot:
        if device_id in self._encrypted:
            return Snapshot(None, None, None, "video is end-to-end encrypted; Turnover cannot read it")
        frame = self._demo_frames.get((device_id, int(at.timestamp())))
        if frame is not None and frame.exists():
            body = frame.read_bytes()
            ctype = "image/png" if frame.suffix == ".png" else "image/jpeg"
            return self._keep(body, ctype, at)
        if self.ring is None or device_id.startswith("ava1.ring.device.demo-"):
            return Snapshot(None, None, None, "no snapshot available")
        try:
            media = await self.ring.snapshot(device_id, at=at)
        except RingAPIError:
            # The exact frame may not be indexed yet; take the latest one around the event.
            try:
                media = await self.ring.snapshot(device_id, between=(at - timedelta(seconds=30), at + timedelta(seconds=90)))
            except RingAPIError as exc:
                return Snapshot(None, None, None, f"Ring snapshot unavailable ({exc.status})")
        if not media.content:
            return Snapshot(None, None, None, "empty snapshot")
        return self._keep(media.content, media.content_type or "image/jpeg", at)

    def _keep(self, body: bytes, ctype: str, at: datetime) -> Snapshot:
        sid = "snap_" + uuid.uuid4().hex[:12]
        self._store.put_blob(sid, body, ctype, at.isoformat())
        return Snapshot(sid, body, ctype)
