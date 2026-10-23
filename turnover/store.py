"""SQLite persistence: a small document store keyed by collection.

SQLite keeps the demo self-contained and runs anywhere. The interface is deliberately
narrow (put/get/list/delete per collection) so a DynamoDB adapter is a drop-in.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    collection TEXT NOT NULL,
    id TEXT NOT NULL,
    property_id TEXT,
    ts TEXT,
    body TEXT NOT NULL,
    PRIMARY KEY (collection, id)
);
CREATE INDEX IF NOT EXISTS docs_by_prop ON docs (collection, property_id, ts);
CREATE TABLE IF NOT EXISTS blobs (
    id TEXT PRIMARY KEY,
    content_type TEXT,
    body BLOB NOT NULL,
    created TEXT
);
"""


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    # --- documents -----------------------------------------------------------------------

    def put(self, collection: str, id: str, body: dict[str, Any], *, property_id: str | None = None, ts: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO docs (collection, id, property_id, ts, body) VALUES (?, ?, ?, ?, ?)",
                (collection, id, property_id, ts, json.dumps(body, default=str)),
            )
            self._db.commit()

    def insert_once(self, collection: str, id: str, body: dict[str, Any] | None = None) -> bool:
        """Insert if absent. Returns False if the id was already there (idempotency)."""
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO docs (collection, id, body) VALUES (?, ?, ?)",
                (collection, id, json.dumps(body or {})),
            )
            self._db.commit()
            return cur.rowcount == 1

    def get(self, collection: str, id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM docs WHERE collection=? AND id=?", (collection, id)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, collection: str, *, property_id: str | None = None, since: str | None = None, limit: int | None = None, newest_first: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT body FROM docs WHERE collection=?"
        args: list[Any] = [collection]
        if property_id is not None:
            sql += " AND property_id=?"
            args.append(property_id)
        if since is not None:
            sql += " AND ts>=?"
            args.append(since)
        sql += " ORDER BY ts DESC, rowid DESC" if newest_first else " ORDER BY ts ASC, rowid ASC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def delete(self, collection: str, id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM docs WHERE collection=? AND id=?", (collection, id))
            self._db.commit()

    def delete_where(self, collection: str, property_ids: Iterable[str]) -> None:
        ids = list(property_ids)
        if not ids:
            return
        with self._lock:
            self._db.execute(
                f"DELETE FROM docs WHERE collection=? AND property_id IN ({','.join('?' * len(ids))})",
                [collection, *ids],
            )
            self._db.commit()

    def clear(self, collections: Iterable[str]) -> None:
        with self._lock:
            for c in collections:
                self._db.execute("DELETE FROM docs WHERE collection=?", (c,))
            self._db.execute("DELETE FROM blobs")
            self._db.commit()

    # --- blobs (snapshots) ---------------------------------------------------------------

    def put_blob(self, id: str, body: bytes, content_type: str, created: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO blobs (id, content_type, body, created) VALUES (?, ?, ?, ?)",
                (id, content_type, body, created),
            )
            self._db.commit()

    def get_blob(self, id: str) -> tuple[bytes, str] | None:
        with self._lock:
            row = self._db.execute("SELECT body, content_type FROM blobs WHERE id=?", (id,)).fetchone()
        return (row[0], row[1]) if row else None

    def purge_blobs_before(self, created_before: str) -> int:
        """Snapshot retention: alerts keep a snapshot for review, not forever."""
        with self._lock:
            cur = self._db.execute("DELETE FROM blobs WHERE created < ?", (created_before,))
            self._db.commit()
            return cur.rowcount
