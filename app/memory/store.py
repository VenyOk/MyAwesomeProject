from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Memory:
    id: int
    content: str
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    source: str = "chat"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "summary": self.summary,
            "tags": list(self.tags),
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _row_to_memory(row: sqlite3.Row) -> Memory:
    try:
        tags = json.loads(row["tags"]) if row["tags"] else []
    except (ValueError, TypeError):
        tags = []
    return Memory(
        id=row["id"],
        content=row["content"],
        summary=row["summary"],
        tags=tags,
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    summary TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'chat',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class MemoryStore:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self.init()

    def init(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add(
        self,
        content: str,
        summary: str | None = None,
        tags: Iterable[str] | None = None,
        source: str = "chat",
    ) -> Memory:
        now = _now()
        tag_list = list(tags) if tags else []
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memories (content, summary, tags, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (content, summary, json.dumps(tag_list), source, now, now),
            )
            self._conn.commit()
            return self.get(cur.lastrowid)  # type: ignore[return-value]

    def get(self, memory_id: int) -> Memory | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return _row_to_memory(row) if row else None

    def list_recent(self, limit: int = 10) -> list[Memory]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def all(self) -> list[Memory]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY id ASC"
            ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def update(
        self,
        memory_id: int,
        content: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
    ) -> Memory | None:
        current = self.get(memory_id)
        if current is None:
            return None
        new_content = content if content is not None else current.content
        new_summary = summary if summary is not None else current.summary
        new_tags = tags if tags is not None else current.tags
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET content=?, summary=?, tags=?, updated_at=? WHERE id=?",
                (
                    new_content,
                    new_summary,
                    json.dumps(new_tags),
                    _now(),
                    memory_id,
                ),
            )
            self._conn.commit()
        return self.get(memory_id)

    def add_tag(self, memory_id: int, tag: str) -> Memory | None:
        current = self.get(memory_id)
        if current is None:
            return None
        tag = tag.strip()
        if tag and tag not in current.tags:
            current.tags.append(tag)
        return self.update(memory_id, tags=current.tags)

    def delete(self, memory_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def search_text(self, query: str, limit: int = 10) -> list[Memory]:
        like = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
                (like, limit),
            ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()
        return int(row["c"]) if row else 0

    def tag_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for mem in self.all():
            for tag in mem.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts
