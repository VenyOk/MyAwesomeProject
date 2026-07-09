from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TITLE = "Новый чат"
TITLE_MAX = 40


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def derive_title(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    first = first.strip()
    if not first:
        return DEFAULT_TITLE
    if first.startswith("/"):
        first = first.lstrip("/").strip() or DEFAULT_TITLE
    if len(first) > TITLE_MAX:
        first = first[: TITLE_MAX - 1].rstrip() + "…"
    return first


@dataclass
class Chat:
    id: int
    title: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Message:
    id: int
    chat_id: int
    role: str
    content: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
        }


SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'Новый чат',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
"""


class ChatStore:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_chat(self, title: str | None = None) -> Chat:
        now = _now()
        title = title or DEFAULT_TITLE
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO chats (title, created_at, updated_at) VALUES (?, ?, ?)",
                (title, now, now),
            )
            self._conn.commit()
            return self.get(cur.lastrowid)  # type: ignore[return-value]

    def list_chats(self) -> list[Chat]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chats ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return [_row_to_chat(r) for r in rows]

    def get(self, chat_id: int) -> Chat | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
        return _row_to_chat(row) if row else None

    def rename(self, chat_id: int, title: str) -> Chat | None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE chats SET title=?, updated_at=? WHERE id=?",
                (title.strip() or DEFAULT_TITLE, _now(), chat_id),
            )
            self._conn.commit()
        return self.get(chat_id) if cur.rowcount else None

    def delete(self, chat_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def touch(self, chat_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE chats SET updated_at=? WHERE id=?", (_now(), chat_id)
            )
            self._conn.commit()

    def maybe_title_from_first_message(self, chat_id: int, text: str) -> None:
        chat = self.get(chat_id)
        if chat and chat.title == DEFAULT_TITLE:
            title = derive_title(text)
            if title != DEFAULT_TITLE:
                self.rename(chat_id, title)

    def add_message(self, chat_id: int, role: str, content: str) -> Message:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, role, content, now),
            )
            self._conn.execute(
                "UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id)
            )
            self._conn.commit()
            mid = cur.lastrowid
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE id = ?", (mid,)
            ).fetchone()
        return _row_to_message(row)

    def list_messages(self, chat_id: int) -> list[Message]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY id ASC",
                (chat_id,),
            ).fetchall()
        return [_row_to_message(r) for r in rows]

    def clear_messages(self, chat_id: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM messages WHERE chat_id = ?", (chat_id,)
            )
            self._conn.commit()
        return cur.rowcount

    def message_count(self, chat_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return int(row["c"]) if row else 0


def _row_to_chat(row: sqlite3.Row) -> Chat:
    return Chat(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        chat_id=row["chat_id"],
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
    )
