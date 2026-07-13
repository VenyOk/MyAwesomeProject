"""Application services that keep tasks and reminders consistent."""
from __future__ import annotations

import sqlite3

from app.tasks.reminders import Reminder, ReminderStore, _now_utc, normalize_scheduled_at
from app.tasks.store import Task, TaskStore, _now


class TaskReminderService:
    """Coordinate task/reminder writes on one SQLite connection."""

    def __init__(self, task_store: TaskStore, reminder_store: ReminderStore):
        self.task_store = task_store
        self.reminder_store = reminder_store

    def create_with_reminder(
        self,
        *,
        title: str,
        scheduled_at: str,
        description: str = "",
        priority: int = 0,
        timezone_name: str = "Europe/Moscow",
        workspace_id: int = 1,
    ) -> tuple[Task, Reminder]:
        """Create both rows atomically and return their refreshed records."""
        normalized_at = normalize_scheduled_at(scheduled_at, timezone_name)
        task_now = _now()
        reminder_now = _now_utc()
        conn = self.task_store._conn
        with self.task_store._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                task_cur = conn.execute(
                    "INSERT INTO tasks (workspace_id, title, description, status, priority, "
                    "due_at, created_at, updated_at) VALUES (?, ?, ?, 'open', ?, ?, ?, ?)",
                    (workspace_id, title, description, priority, scheduled_at, task_now, task_now),
                )
                task_id = int(task_cur.lastrowid)
                reminder_cur = conn.execute(
                    "INSERT INTO reminders (workspace_id, task_id, title, scheduled_at, "
                    "timezone, status, channel, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'scheduled', 'web', ?)",
                    (workspace_id, task_id, title, normalized_at, timezone_name, reminder_now),
                )
                reminder_id = int(reminder_cur.lastrowid)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        task = self.task_store.get(task_id)
        reminder = self.reminder_store.get(reminder_id)
        if task is None or reminder is None:  # pragma: no cover - commit guarantees both rows
            raise sqlite3.DatabaseError("task/reminder transaction committed incomplete rows")
        return task, reminder

    def cancel_linked_reminders(self, task_id: int) -> list[Reminder]:
        return self.reminder_store.cancel_for_task(task_id)
