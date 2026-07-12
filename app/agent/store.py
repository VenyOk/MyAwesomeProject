"""Persistence for tool audit records and user confirmations."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL DEFAULT 1,
    chat_id INTEGER,
    message_id INTEGER,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    policy_decision TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_runs_chat ON tool_runs(chat_id);

CREATE TABLE IF NOT EXISTS confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL DEFAULT 1,
    chat_id INTEGER,
    tool_run_id INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_confirmations_chat_status
    ON confirmations(chat_id, status);
"""


@dataclass
class Confirmation:
    id: int
    workspace_id: int
    chat_id: int | None
    tool_run_id: int
    tool_name: str
    arguments: dict
    risk: str
    status: str
    created_at: str
    resolved_at: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "tool_run_id": self.tool_run_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "risk": self.risk,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }


@dataclass
class ToolRun:
    """A persisted execution record used by tool cards and the audit view."""

    id: int
    workspace_id: int
    chat_id: int | None
    message_id: int | None
    tool_name: str
    arguments: dict
    result: dict | None
    policy_decision: str
    status: str
    created_at: str
    finished_at: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "policy_decision": self.policy_decision,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


def _row_to_confirmation(row: sqlite3.Row) -> Confirmation:
    return Confirmation(
        id=row["id"],
        workspace_id=row["workspace_id"],
        chat_id=row["chat_id"],
        tool_run_id=row["tool_run_id"],
        tool_name=row["tool_name"],
        arguments=json.loads(row["arguments_json"]),
        risk=row["risk"],
        status=row["status"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


def _row_to_tool_run(row: sqlite3.Row) -> ToolRun:
    return ToolRun(
        id=row["id"],
        workspace_id=row["workspace_id"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        tool_name=row["tool_name"],
        arguments=json.loads(row["arguments_json"]),
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        policy_decision=row["policy_decision"],
        status=row["status"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )


class AgentStore:
    def __init__(self, db_path: Path | str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def start_tool_run(
        self,
        tool_name: str,
        arguments: dict,
        *,
        chat_id: int | None,
        policy_decision: str,
        workspace_id: int = 1,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tool_runs (workspace_id, chat_id, tool_name, arguments_json, "
                "policy_decision, status, created_at) VALUES (?, ?, ?, ?, ?, 'running', ?)",
                (
                    workspace_id,
                    chat_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    policy_decision,
                    _now(),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def finish_tool_run(self, tool_run_id: int, status: str, result: dict) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tool_runs SET status=?, result_json=?, finished_at=? WHERE id=?",
                (status, json.dumps(result, ensure_ascii=False), _now(), tool_run_id),
            )
            self._conn.commit()

    def list_tool_runs(self, *, chat_id: int | None = None) -> list[ToolRun]:
        """Return persisted tool runs newest-first, optionally for one chat."""
        with self._lock:
            if chat_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM tool_runs ORDER BY id DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM tool_runs WHERE chat_id=? ORDER BY id DESC",
                    (chat_id,),
                ).fetchall()
        return [_row_to_tool_run(row) for row in rows]

    def create_confirmation(
        self,
        *,
        tool_run_id: int,
        tool_name: str,
        arguments: dict,
        risk: str,
        chat_id: int | None,
        workspace_id: int = 1,
    ) -> Confirmation:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO confirmations (workspace_id, chat_id, tool_run_id, tool_name, "
                "arguments_json, risk, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    chat_id,
                    tool_run_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    risk,
                    _now(),
                ),
            )
            self._conn.commit()
        return self.get_confirmation(int(cur.lastrowid))  # type: ignore[return-value]

    def get_confirmation(self, confirmation_id: int) -> Confirmation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM confirmations WHERE id=?", (confirmation_id,)
            ).fetchone()
        return _row_to_confirmation(row) if row else None

    def list_confirmations(
        self, *, chat_id: int | None = None, status: str = "pending"
    ) -> list[Confirmation]:
        with self._lock:
            if chat_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM confirmations WHERE status=? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM confirmations WHERE chat_id=? AND status=? ORDER BY id DESC",
                    (chat_id, status),
                ).fetchall()
        return [_row_to_confirmation(row) for row in rows]

    def resolve_confirmation(
        self, confirmation_id: int, *, approved: bool
    ) -> Confirmation | None:
        status = "approved" if approved else "rejected"
        with self._lock:
            cur = self._conn.execute(
                "UPDATE confirmations SET status=?, resolved_at=? "
                "WHERE id=? AND status='pending'",
                (status, _now(), confirmation_id),
            )
            self._conn.commit()
        if cur.rowcount != 1:
            return None
        return self.get_confirmation(confirmation_id)
