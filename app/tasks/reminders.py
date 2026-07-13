"""Reminder store backed by the ``reminders`` table (migration 0004).

CRUD plus the atomic ``claim_due`` used by the scheduler to ensure each
reminder fires exactly once, even across restarts and concurrent ticks.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_scheduled_at(value: str, timezone_name: str = "Europe/Moscow") -> str:
    """Normalize a user-entered ISO datetime to UTC.

    The web form emits a naive ``datetime-local`` value. It represents local
    time in the selected reminder timezone, not UTC, so attach that timezone
    before storing a comparable UTC value.
    """
    zone = _timezone(timezone_name)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("scheduled_at must be a valid ISO datetime") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise ValueError(f"unknown timezone: {timezone_name}") from None


_UNSET = object()


@dataclass
class Reminder:
    id: int
    workspace_id: int
    task_id: int | None
    title: str
    scheduled_at: str
    timezone: str
    recurrence_rule: str | None
    status: str
    channel: str
    created_at: str
    fired_at: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "title": self.title,
            "scheduled_at": self.scheduled_at,
            "timezone": self.timezone,
            "recurrence_rule": self.recurrence_rule,
            "status": self.status,
            "channel": self.channel,
            "created_at": self.created_at,
            "fired_at": self.fired_at,
        }


SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL DEFAULT 1,
    task_id INTEGER,
    title TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    recurrence_rule TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled',
    channel TEXT NOT NULL DEFAULT 'web',
    created_at TEXT NOT NULL,
    fired_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);
CREATE INDEX IF NOT EXISTS idx_reminders_scheduled ON reminders(scheduled_at);
"""


def _row_to_reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=row["id"],
        workspace_id=row["workspace_id"],
        task_id=row["task_id"],
        title=row["title"],
        scheduled_at=row["scheduled_at"],
        timezone=row["timezone"],
        recurrence_rule=row["recurrence_rule"],
        status=row["status"],
        channel=row["channel"],
        created_at=row["created_at"],
        fired_at=row["fired_at"],
    )


class ReminderStore:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create(
        self,
        title: str,
        scheduled_at: str,
        *,
        task_id: int | None = None,
        timezone_name: str = "Europe/Moscow",
        recurrence_rule: str | None = None,
        workspace_id: int = 1,
    ) -> Reminder:
        now = _now_utc()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO reminders (workspace_id, task_id, title, scheduled_at, "
                "timezone, recurrence_rule, status, channel, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'scheduled', 'web', ?)",
                (workspace_id, task_id, title, normalize_scheduled_at(scheduled_at, timezone_name), timezone_name,
                 recurrence_rule, now),
            )
            self._conn.commit()
            rid = cur.lastrowid
        return self.get(rid)  # type: ignore[return-value]

    def update(
        self,
        reminder_id: int,
        *,
        title: str | object = _UNSET,
        scheduled_at: str | object = _UNSET,
        timezone_name: str | object = _UNSET,
        recurrence_rule: str | None | object = _UNSET,
    ) -> Reminder | None:
        current = self.get(reminder_id)
        if current is None or current.status != "scheduled":
            return None

        next_timezone = current.timezone if timezone_name is _UNSET else str(timezone_name)
        _timezone(next_timezone)
        next_scheduled_at = (
            current.scheduled_at
            if scheduled_at is _UNSET
            else normalize_scheduled_at(str(scheduled_at), next_timezone)
        )
        next_title = current.title if title is _UNSET else str(title)
        next_rule = current.recurrence_rule if recurrence_rule is _UNSET else recurrence_rule
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reminders SET title=?, scheduled_at=?, timezone=?, recurrence_rule=? "
                "WHERE id=? AND status='scheduled'",
                (next_title, next_scheduled_at, next_timezone, next_rule, reminder_id),
            )
            self._conn.commit()
        return self.get(reminder_id) if cur.rowcount else None

    def get(self, reminder_id: int) -> Reminder | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reminders WHERE id=?", (reminder_id,)
            ).fetchone()
        return _row_to_reminder(row) if row else None

    def list(self, status: str | None = "scheduled", workspace_id: int = 1) -> list[Reminder]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM reminders WHERE workspace_id=? AND status=? "
                    "ORDER BY scheduled_at ASC",
                    (workspace_id, status),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM reminders WHERE workspace_id=? ORDER BY scheduled_at ASC",
                    (workspace_id,),
                ).fetchall()
        return [_row_to_reminder(r) for r in rows]

    def cancel(self, reminder_id: int) -> Reminder | None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=? AND status='scheduled'",
                (reminder_id,),
            )
            self._conn.commit()
        return self.get(reminder_id) if cur.rowcount else None

    def cancel_for_task(self, task_id: int, workspace_id: int = 1) -> list[Reminder]:
        """Cancel all scheduled reminders linked to a task."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM reminders WHERE workspace_id=? AND task_id=? AND status='scheduled'",
                (workspace_id, task_id),
            ).fetchall()
            if not rows:
                return []
            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"UPDATE reminders SET status='cancelled' "
                f"WHERE workspace_id=? AND task_id=? AND status='scheduled' "
                f"AND id IN ({placeholders})",  # noqa: S608
                (workspace_id, task_id, *ids),
            )
            self._conn.commit()
        cancelled: list[Reminder] = []
        for reminder_id in ids:
            reminder = self.get(reminder_id)
            if reminder is not None:
                cancelled.append(reminder)
        return cancelled

    def claim_due(
        self,
        now_utc: str | None = None,
        limit: int = 100,
        workspace_id: int = 1,
    ) -> list[Reminder]:
        """Atomically select scheduled reminders whose time has come and mark
        them ``fired`` so no two ticks can claim the same row (plan §13:
        «не отправлять одно напоминание дважды»).

        Returns the claimed reminders so the caller can create outbox entries.
        """
        now = normalize_scheduled_at(now_utc, "UTC") if now_utc else _now_utc()
        with self._lock:
            # A write transaction makes the select-and-mark sequence atomic
            # across multiple scheduler ticks/processes, not just threads in
            # this Python instance.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT * FROM reminders WHERE workspace_id=? AND status='scheduled' AND scheduled_at <= ? "
                    "ORDER BY scheduled_at ASC LIMIT ?",
                    (workspace_id, now, limit),
                ).fetchall()
                ids = [r["id"] for r in rows]
                if not ids:
                    self._conn.commit()
                    return []
                placeholders = ",".join("?" * len(ids))
                self._conn.execute(
                    f"UPDATE reminders SET status='fired', fired_at=? "
                    f"WHERE status='scheduled' AND id IN ({placeholders})",  # noqa: S608
                    (now, *ids),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            refreshed = self._conn.execute(
                f"SELECT * FROM reminders WHERE id IN ({placeholders}) ORDER BY scheduled_at ASC",  # noqa: S608
                ids,
            ).fetchall()
        return [_row_to_reminder(r) for r in refreshed]

    def list_fired_without_outbox(
        self,
        workspace_id: int = 1,
        limit: int = 100,
    ) -> list[Reminder]:
        """Fired reminders missing their durable outbox event.

        This is the small recovery query used after a process stops between
        claiming a reminder and committing its notification. It stays bounded
        to missing events instead of rechecking the full delivery history.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.* FROM reminders r WHERE r.workspace_id=? AND r.status='fired' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM outbox o WHERE o.workspace_id=r.workspace_id "
                "AND o.dedupe_key=('reminder:' || r.id)"
                ") ORDER BY r.scheduled_at ASC LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        return [_row_to_reminder(r) for r in rows]

    def list_missed(self, now_utc: str | None = None, workspace_id: int = 1) -> list[Reminder]:
        """Scheduled reminders already past their time (for restart recovery)."""
        now = normalize_scheduled_at(now_utc, "UTC") if now_utc else _now_utc()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reminders WHERE workspace_id=? AND status='scheduled' "
                "AND scheduled_at <= ? ORDER BY scheduled_at ASC",
                (workspace_id, now),
            ).fetchall()
        return [_row_to_reminder(r) for r in rows]

    def count(self, status: str | None = None, workspace_id: int = 1) -> int:
        with self._lock:
            if status:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM reminders WHERE workspace_id=? AND status=?",
                    (workspace_id, status),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM reminders WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchone()
        return int(row["c"]) if row else 0
