from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.db.migrate import add_column


# Valid values for the domain columns added by migration 0003.
KINDS = ("fact", "preference", "decision", "idea", "person", "project")
STATUSES = ("candidate", "active", "superseded", "deleted")
SENSITIVITIES = ("normal", "private", "secret")


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
    # Domain fields (migration 0003). Defaults keep the legacy shape usable.
    workspace_id: int = 1
    kind: str = "fact"
    normalized_content: str | None = None
    importance: float = 0.5
    confidence: float = 0.5
    sensitivity: str = "normal"
    source_type: str = "chat"
    source_message_id: int | None = None
    status: str = "active"
    embedding_status: str = "ready"
    deleted_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "summary": self.summary,
            "tags": list(self.tags),
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "kind": self.kind,
            "importance": self.importance,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity,
            "source_type": self.source_type,
            "source_message_id": self.source_message_id,
            "status": self.status,
            "deleted_at": self.deleted_at,
        }


def _row_to_memory(row: sqlite3.Row) -> Memory:
    def _get(col: str, default=None):
        try:
            return row[col]
        except (IndexError, KeyError):
            return default

    try:
        tags = json.loads(row["tags"]) if row["tags"] else []
    except (ValueError, TypeError, KeyError):
        tags = []
    return Memory(
        id=row["id"],
        content=row["content"],
        summary=row["summary"],
        tags=tags,
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        workspace_id=_get("workspace_id", 1) or 1,
        kind=_get("kind", "fact") or "fact",
        normalized_content=_get("normalized_content"),
        importance=_get("importance", 0.5),
        confidence=_get("confidence", 0.5),
        sensitivity=_get("sensitivity", "normal") or "normal",
        source_type=_get("source_type", "chat") or "chat",
        source_message_id=_get("source_message_id"),
        status=_get("status", "active") or "active",
        embedding_status=_get("embedding_status", "ready") or "ready",
        deleted_at=_get("deleted_at"),
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    summary TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'chat',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    workspace_id INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL DEFAULT 'fact',
    normalized_content TEXT,
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.5,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    source_type TEXT NOT NULL DEFAULT 'chat',
    source_message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    embedding_status TEXT NOT NULL DEFAULT 'ready',
    deleted_at TEXT
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
            # Enrich databases created before the curated-memory schema existed.
            add_column(self._conn, "memories", "workspace_id", "INTEGER NOT NULL DEFAULT 1")
            add_column(self._conn, "memories", "kind", "TEXT NOT NULL DEFAULT 'fact'")
            add_column(self._conn, "memories", "normalized_content", "TEXT")
            add_column(self._conn, "memories", "importance", "REAL NOT NULL DEFAULT 0.5")
            add_column(self._conn, "memories", "confidence", "REAL NOT NULL DEFAULT 0.5")
            add_column(self._conn, "memories", "sensitivity", "TEXT NOT NULL DEFAULT 'normal'")
            add_column(self._conn, "memories", "source_type", "TEXT NOT NULL DEFAULT 'chat'")
            add_column(self._conn, "memories", "source_message_id", "INTEGER")
            add_column(self._conn, "memories", "status", "TEXT NOT NULL DEFAULT 'active'")
            add_column(self._conn, "memories", "embedding_status", "TEXT NOT NULL DEFAULT 'ready'")
            add_column(self._conn, "memories", "deleted_at", "TEXT")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------- create ----------------------------

    def add(
        self,
        content: str,
        summary: str | None = None,
        tags: Iterable[str] | None = None,
        source: str = "chat",
        *,
        kind: str = "fact",
        importance: float = 0.5,
        confidence: float = 0.5,
        sensitivity: str = "normal",
        source_type: str | None = None,
        source_message_id: int | None = None,
        status: str = "active",
    ) -> Memory:
        now = _now()
        tag_list = list(tags) if tags else []
        # ``source`` is the legacy column; ``source_type`` is the domain column.
        src_type = source_type if source_type is not None else source
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memories "
                "(content, summary, tags, source, created_at, updated_at, "
                " kind, importance, confidence, sensitivity, source_type, "
                " source_message_id, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    content,
                    summary,
                    json.dumps(tag_list),
                    source,
                    now,
                    now,
                    kind,
                    importance,
                    confidence,
                    sensitivity,
                    src_type,
                    source_message_id,
                    status,
                ),
            )
            self._conn.commit()
            return self.get(cur.lastrowid)  # type: ignore[return-value]

    # ---------------------------- read ----------------------------

    def get(self, memory_id: int) -> Memory | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return _row_to_memory(row) if row else None

    def list_recent(self, limit: int = 10) -> list[Memory]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE deleted_at IS NULL "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def list_by_status(self, status: str, limit: int = 100) -> list[Memory]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE status = ? AND deleted_at IS NULL "
                "ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def list_candidates(self, limit: int = 100) -> list[Memory]:
        return self.list_by_status("candidate", limit=limit)

    def all(self) -> list[Memory]:
        """All non-deleted memories, oldest first (used for FAISS rebuild)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE deleted_at IS NULL "
                "ORDER BY id ASC"
            ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def update(
        self,
        memory_id: int,
        content: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        kind: str | None = None,
    ) -> Memory | None:
        current = self.get(memory_id)
        if current is None:
            return None
        new_content = content if content is not None else current.content
        new_summary = summary if summary is not None else current.summary
        new_tags = tags if tags is not None else current.tags
        new_kind = kind if kind is not None else current.kind
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET content=?, summary=?, tags=?, kind=?, updated_at=? "
                "WHERE id=?",
                (
                    new_content,
                    new_summary,
                    json.dumps(new_tags),
                    new_kind,
                    _now(),
                    memory_id,
                ),
            )
            self._conn.commit()
        return self.get(memory_id)

    def update_status(self, memory_id: int, status: str) -> Memory | None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memories SET status=?, updated_at=? WHERE id=?",
                (status, _now(), memory_id),
            )
            self._conn.commit()
        return self.get(memory_id) if cur.rowcount else None

    def activate(self, memory_id: int) -> Memory | None:
        return self.update_status(memory_id, "active")

    def add_tag(self, memory_id: int, tag: str) -> Memory | None:
        current = self.get(memory_id)
        if current is None:
            return None
        tag = tag.strip()
        if tag and tag not in current.tags:
            current.tags.append(tag)
        return self.update(memory_id, tags=current.tags)

    # ---------------------------- delete / restore ----------------------------

    def delete(self, memory_id: int) -> bool:
        """Soft-delete: set deleted_at and mark status. FAISS rebuild happens
        in the caller (RecallService) via rebuild_from_store which skips rows
        with deleted_at IS NOT NULL through all()."""
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memories SET deleted_at=?, status='deleted', updated_at=? "
                "WHERE id=? AND deleted_at IS NULL",
                (now, now, memory_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def restore(self, memory_id: int) -> Memory | None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memories SET deleted_at=NULL, status='active', updated_at=? "
                "WHERE id=?",
                (_now(), memory_id),
            )
            self._conn.commit()
        return self.get(memory_id) if cur.rowcount else None

    # ---------------------------- search ----------------------------

    def search_text(self, query: str, limit: int = 10) -> list[Memory]:
        like = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE content LIKE ? AND deleted_at IS NULL "
                "ORDER BY id DESC LIMIT ?",
                (like, limit),
            ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM memories WHERE deleted_at IS NULL"
            ).fetchone()
        return int(row["c"]) if row else 0

    def tag_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for mem in self.all():
            for tag in mem.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts
