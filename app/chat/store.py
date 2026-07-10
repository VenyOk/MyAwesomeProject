from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.db.migrate import add_column, table_exists

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
class Folder:
    id: int
    name: str
    description: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
        }


@dataclass
class Chat:
    id: int
    title: str
    created_at: str
    updated_at: str
    folder_id: int | None = None
    pinned: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "folder_id": self.folder_id,
            "pinned": self.pinned,
        }


@dataclass
class Message:
    id: int
    chat_id: int
    role: str
    content: str
    created_at: str
    edited_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "edited_at": self.edited_at,
        }


SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'Новый чат',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    folder_id INTEGER DEFAULT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    edited_at TEXT DEFAULT NULL,
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
        # migrations for pre-existing databases created before folders/pins/edits
        if not table_exists(self._conn, "folders"):
            self._conn.executescript(
                "CREATE TABLE IF NOT EXISTS folders ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                "description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);"
            )
            self._conn.commit()
        add_column(self._conn, "chats", "folder_id", "INTEGER DEFAULT NULL")
        add_column(self._conn, "chats", "pinned", "INTEGER NOT NULL DEFAULT 0")
        add_column(self._conn, "messages", "edited_at", "TEXT DEFAULT NULL")

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
                "SELECT * FROM chats ORDER BY pinned DESC, updated_at DESC, id DESC"
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

    def update_chat(
        self,
        chat_id: int,
        *,
        title: str | None = None,
        folder_id: int | None = None,
        pinned: bool | None = None,
    ) -> Chat | None:
        """Patch chat fields. ``folder_id=None`` unsets the folder only when
        ``set_folder_none`` is desired; here passing None means "leave unchanged"
        for folder, use move_chat to explicitly unset."""
        sets: list[str] = []
        params: list[object] = []
        if title is not None:
            sets.append("title=?")
            params.append(title.strip() or DEFAULT_TITLE)
        if folder_id is not None:
            sets.append("folder_id=?")
            params.append(folder_id)
        if pinned is not None:
            sets.append("pinned=?")
            params.append(1 if pinned else 0)
        if not sets:
            return self.get(chat_id)
        sets.append("updated_at=?")
        params.append(_now())
        params.append(chat_id)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE chats SET {', '.join(sets)} WHERE id=?", params  # noqa: S608
            )
            self._conn.commit()
        return self.get(chat_id) if cur.rowcount else None

    def move_chat(self, chat_id: int, folder_id: int | None) -> Chat | None:
        """Move a chat into a folder. ``folder_id=None`` removes it from any folder."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE chats SET folder_id=?, updated_at=? WHERE id=?",
                (folder_id, _now(), chat_id),
            )
            self._conn.commit()
        return self.get(chat_id) if cur.rowcount else None

    def set_pinned(self, chat_id: int, pinned: bool) -> Chat | None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE chats SET pinned=? WHERE id=?", (1 if pinned else 0, chat_id)
            )
            self._conn.commit()
        return self.get(chat_id) if cur.rowcount else None

    # ---------------------------- folders ----------------------------

    def create_folder(self, name: str, description: str = "") -> Folder:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO folders (name, description, created_at) VALUES (?, ?, ?)",
                (name.strip() or "Папка", description, now),
            )
            self._conn.commit()
            fid = cur.lastrowid
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM folders WHERE id=?", (fid,)
            ).fetchone()
        return _row_to_folder(row)  # type: ignore[arg-type]

    def list_folders(self) -> list[Folder]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM folders ORDER BY id ASC"
            ).fetchall()
        return [_row_to_folder(r) for r in rows]

    def get_folder(self, folder_id: int) -> Folder | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM folders WHERE id=?", (folder_id,)
            ).fetchone()
        return _row_to_folder(row) if row else None

    def rename_folder(
        self, folder_id: int, name: str | None = None, description: str | None = None
    ) -> Folder | None:
        sets: list[str] = []
        params: list[object] = []
        if name is not None:
            sets.append("name=?")
            params.append(name.strip() or "Папка")
        if description is not None:
            sets.append("description=?")
            params.append(description)
        if not sets:
            return self.get_folder(folder_id)
        params.append(folder_id)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE folders SET {', '.join(sets)} WHERE id=?", params  # noqa: S608
            )
            self._conn.commit()
        return self.get_folder(folder_id) if cur.rowcount else None

    def delete_folder(self, folder_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM folders WHERE id=?", (folder_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

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

    def get_message(self, message_id: int) -> Message | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        return _row_to_message(row) if row else None

    def delete_message(self, message_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM messages WHERE id = ?", (message_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def update_message(self, message_id: int, content: str) -> Message | None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE messages SET content=?, edited_at=? WHERE id=?",
                (content, _now(), message_id),
            )
            self._conn.commit()
        if not cur.rowcount:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        return _row_to_message(row) if row else None

    def search_messages(self, query: str, limit: int = 50) -> list[Message]:
        """Substring search across all messages (mirrors MemoryStore.search_text)."""
        q = f"%{query.strip()}%"
        if not query.strip():
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE content LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (q, limit),
            ).fetchall()
        return [_row_to_message(r) for r in rows]


def _row_to_chat(row: sqlite3.Row) -> Chat:
    return Chat(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        folder_id=row["folder_id"],
        pinned=bool(row["pinned"]),
    )


def _row_to_folder(row: sqlite3.Row) -> Folder:
    return Folder(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
    )


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        chat_id=row["chat_id"],
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
        edited_at=row["edited_at"],
    )
