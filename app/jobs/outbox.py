"""Notification outbox backed by the ``outbox`` table (migration 0004).

The scheduler writes fired-reminder events here; the UI polls them and marks
them sent when the user has seen the notification. Keeps delivery reliable
across restarts (plan §13).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass
class OutboxItem:
    id: int
    workspace_id: int
    channel: str
    event_type: str
    payload: dict
    available_at: str
    status: str
    attempts: int
    last_error: str | None
    dedupe_key: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel": self.channel,
            "event_type": self.event_type,
            "payload": self.payload,
            "available_at": self.available_at,
            "status": self.status,
        }


SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL DEFAULT 1,
    channel TEXT NOT NULL DEFAULT 'web',
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    available_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    dedupe_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);
CREATE INDEX IF NOT EXISTS idx_outbox_available ON outbox(available_at);
"""


def _row_to_item(row: sqlite3.Row) -> OutboxItem:
    try:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
    except (ValueError, TypeError):
        payload = {}
    return OutboxItem(
        id=row["id"],
        workspace_id=row["workspace_id"],
        channel=row["channel"],
        event_type=row["event_type"],
        payload=payload,
        available_at=row["available_at"],
        status=row["status"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        dedupe_key=row["dedupe_key"] if "dedupe_key" in row.keys() else None,
    )


class OutboxStore:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        with self._lock:
            self._conn.executescript(SCHEMA)
            # Existing databases gain this index in migration 0006. A fresh
            # standalone store already has the column and can create it now.
            try:
                self._conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_workspace_dedupe "
                    "ON outbox(workspace_id, dedupe_key) WHERE dedupe_key IS NOT NULL"
                )
            except sqlite3.OperationalError:
                pass
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create(
        self,
        event_type: str,
        payload: dict,
        *,
        available_at: str | None = None,
        channel: str = "web",
        workspace_id: int = 1,
        dedupe_key: str | None = None,
    ) -> OutboxItem:
        now = _now_utc()
        with self._lock:
            if dedupe_key is None:
                cur = self._conn.execute(
                    "INSERT INTO outbox (workspace_id, channel, event_type, payload_json, "
                    "available_at, status, dedupe_key) VALUES (?, ?, ?, ?, ?, 'pending', NULL)",
                    (workspace_id, channel, event_type, json.dumps(payload, ensure_ascii=False),
                     available_at or now),
                )
            else:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO outbox (workspace_id, channel, event_type, payload_json, "
                    "available_at, status, dedupe_key) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                    (workspace_id, channel, event_type, json.dumps(payload, ensure_ascii=False),
                     available_at or now, dedupe_key),
                )
            self._conn.commit()
            if cur.rowcount:
                oid = cur.lastrowid
            else:
                row = self._conn.execute(
                    "SELECT id FROM outbox WHERE workspace_id=? AND dedupe_key=?",
                    (workspace_id, dedupe_key),
                ).fetchone()
                oid = row["id"]
        return self.get(oid)  # type: ignore[return-value]

    def get(self, item_id: int) -> OutboxItem | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM outbox WHERE id=?", (item_id,)).fetchone()
        return _row_to_item(row) if row else None

    def list_pending(self, limit: int = 50, workspace_id: int = 1) -> list[OutboxItem]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbox WHERE workspace_id=? AND status='pending' "
                "ORDER BY available_at ASC LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    def list_available(
        self,
        limit: int = 50,
        workspace_id: int = 1,
        now_utc: str | None = None,
    ) -> list[OutboxItem]:
        """Return pending notifications whose delivery time has arrived."""
        now = _utc(now_utc) if now_utc else _now_utc()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbox WHERE workspace_id=? AND status='pending' "
                "AND available_at <= ? ORDER BY available_at ASC LIMIT ?",
                (workspace_id, now, limit),
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    def mark_sent(self, item_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE outbox SET status='sent' WHERE id=? AND status='pending'",
                (item_id,),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def count_pending(self, workspace_id: int = 1) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM outbox WHERE workspace_id=? AND status='pending'",
                (workspace_id,),
            ).fetchone()
        return int(row["c"]) if row else 0
