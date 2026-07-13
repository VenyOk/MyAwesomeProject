"""SQLite-backed settings for the single local workspace."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_settings (
    workspace_id INTEGER PRIMARY KEY,
    timezone TEXT NOT NULL,
    quiet_hours_start TEXT,
    quiet_hours_end TEXT,
    updated_at TEXT NOT NULL
);
"""


class SettingsStore:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get(self, workspace_id: int = 1) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT workspace_id, timezone, quiet_hours_start, quiet_hours_end, updated_at "
                "FROM workspace_settings WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        return dict(row) if row else None

    def save(
        self,
        timezone_name: str,
        quiet_hours_start: str | None,
        quiet_hours_end: str | None,
        workspace_id: int = 1,
    ) -> dict:
        with self._lock:
            self._conn.execute(
                "INSERT INTO workspace_settings "
                "(workspace_id, timezone, quiet_hours_start, quiet_hours_end, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id) DO UPDATE SET timezone=excluded.timezone, "
                "quiet_hours_start=excluded.quiet_hours_start, "
                "quiet_hours_end=excluded.quiet_hours_end, updated_at=excluded.updated_at",
                (workspace_id, timezone_name, quiet_hours_start, quiet_hours_end, _now()),
            )
            self._conn.commit()
        return self.get(workspace_id) or {}
