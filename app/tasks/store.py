"""Task store backed by the ``tasks`` table (migration 0004).

Minimal CRUD sufficient for the MVP tool set (task.create, task.list,
task.complete). Full reminders/scheduler come in Stage 5.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


_UNSET = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Task:
    id: int
    workspace_id: int
    title: str
    description: str
    status: str
    priority: int
    due_at: str | None
    source_message_id: int | None
    created_at: str
    updated_at: str
    completed_at: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "due_at": self.due_at,
            "source_message_id": self.source_message_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    priority INTEGER NOT NULL DEFAULT 0,
    due_at TEXT,
    source_message_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        workspace_id=row["workspace_id"],
        title=row["title"],
        description=row["description"],
        status=row["status"],
        priority=row["priority"],
        due_at=row["due_at"],
        source_message_id=row["source_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


class TaskStore:
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
        description: str = "",
        *,
        due_at: str | None = None,
        priority: int = 0,
        source_message_id: int | None = None,
        workspace_id: int = 1,
    ) -> Task:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks (workspace_id, title, description, status, priority, "
                "due_at, source_message_id, created_at, updated_at) "
                "VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?)",
                (workspace_id, title, description, priority, due_at, source_message_id, now, now),
            )
            self._conn.commit()
            tid = cur.lastrowid
        return self.get(tid)  # type: ignore[return-value]

    def get(self, task_id: int) -> Task | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list(self, status: str | None = None, workspace_id: int = 1) -> list[Task]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE workspace_id=? AND status=? ORDER BY id DESC",
                    (workspace_id, status),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE workspace_id=? ORDER BY id DESC",
                    (workspace_id,),
                ).fetchall()
        return [_row_to_task(r) for r in rows]

    def update(
        self,
        task_id: int,
        *,
        title: str | object = _UNSET,
        description: str | object = _UNSET,
        due_at: str | None | object = _UNSET,
        priority: int | object = _UNSET,
    ) -> Task | None:
        current = self.get(task_id)
        if current is None:
            return None
        values = {
            "title": current.title if title is _UNSET else title,
            "description": current.description if description is _UNSET else description,
            "due_at": current.due_at if due_at is _UNSET else due_at,
            "priority": current.priority if priority is _UNSET else priority,
        }
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET title=?, description=?, due_at=?, priority=?, updated_at=? WHERE id=?",
                (
                    values["title"],
                    values["description"],
                    values["due_at"],
                    values["priority"],
                    _now(),
                    task_id,
                ),
            )
            self._conn.commit()
        return self.get(task_id)

    def complete(self, task_id: int) -> Task | None:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status='done', completed_at=?, updated_at=? WHERE id=? AND status='open'",
                (now, now, task_id),
            )
            self._conn.commit()
        return self.get(task_id) if cur.rowcount else None

    def cancel(self, task_id: int) -> Task | None:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status='cancelled', updated_at=? WHERE id=? AND status='open'",
                (now, task_id),
            )
            self._conn.commit()
        return self.get(task_id) if cur.rowcount else None

    def count(self, workspace_id: int = 1) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE workspace_id=?", (workspace_id,)
            ).fetchone()
        return int(row["c"]) if row else 0
