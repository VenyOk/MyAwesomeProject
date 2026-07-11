"""Versioned SQLite migrations for Second Brain.

Replaces the previous ad-hoc ``add_column`` helpers with a numbered, ordered
sequence applied exactly once and tracked in ``schema_migrations``.

Properties:
- Each migration runs in a transaction and is applied at most once.
- Before the first migration on a pre-existing database, a timestamped backup
  is written next to the db file.
- Migrations are idempotent at the SQL level where possible (``IF NOT EXISTS``)
  so a partially-applied transaction can be retried safely.
- The migration runner is the single owner of schema DDL for domain tables.

The existing stores (``ChatStore``, ``MemoryStore``) keep their
``CREATE TABLE IF NOT EXISTS`` blocks so a brand-new database bootstraps even if
migrations have not run yet; the migrations then enrich that baseline schema.
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.db.migrate import column_exists, table_exists


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _exec(conn: sqlite3.Connection, sql: str) -> None:
    conn.executescript(sql)


# --------------------------------------------------------------------------- #
# Migration 1: workspace support.
#
# Adds a ``workspaces`` table and a default workspace (id 1) for the single
# local user, then backfills ``workspace_id`` on every existing row of the
# domain tables that already exist. New tables created by later migrations are
# expected to declare their own ``workspace_id NOT NULL DEFAULT 1``.
# --------------------------------------------------------------------------- #
def m1_workspaces(conn: sqlite3.Connection) -> None:
    _exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """,
    )
    conn.execute(
        "INSERT OR IGNORE INTO workspaces (id, name, created_at) VALUES (1, ?, ?)",
        ("Локальный", _now()),
    )

    # Backfill workspace_id on pre-existing tables. Each ALTER is guarded by a
    # column check so this migration is safe to re-run and safe on fresh dbs.
    for table in ("chats", "messages", "memories"):
        if not table_exists(conn, table):
            continue
        if not column_exists(conn, table, "workspace_id"):
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1"  # noqa: S608
            )


# --------------------------------------------------------------------------- #
# Migration 2: soft-delete + lifecycle columns.
#
# Messages get ``deleted_at`` (soft delete, per plan §8.2) so derived memories
# can reference a removed message until the user decides what to do with them.
# Memories get ``deleted_at`` and ``updated_at`` is ensured present.
# --------------------------------------------------------------------------- #
def m2_soft_delete(conn: sqlite3.Connection) -> None:
    if table_exists(conn, "messages") and not column_exists(conn, "messages", "deleted_at"):
        conn.execute("ALTER TABLE messages ADD COLUMN deleted_at TEXT DEFAULT NULL")
    if table_exists(conn, "memories") and not column_exists(conn, "memories", "deleted_at"):
        conn.execute("ALTER TABLE memories ADD COLUMN deleted_at TEXT DEFAULT NULL")


# --------------------------------------------------------------------------- #
# Migration 3: curated memory schema.
#
# Extends ``memories`` to the full second-brain domain model (plan §8.3):
# kind, normalized/summary, importance/confidence/sensitivity, source linkage,
# status lifecycle, versioning and embedding status. Existing simple memories
# are backfilled as active ``fact`` entries with neutral defaults.
# --------------------------------------------------------------------------- #
def m3_memories_domain(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "memories"):
        return
    additions = {
        "kind": "TEXT NOT NULL DEFAULT 'fact'",
        "normalized_content": "TEXT",
        "importance": "REAL NOT NULL DEFAULT 0.5",
        "confidence": "REAL NOT NULL DEFAULT 0.5",
        "sensitivity": "TEXT NOT NULL DEFAULT 'normal'",
        "source_type": "TEXT NOT NULL DEFAULT 'chat'",
        "source_message_id": "INTEGER",
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "valid_from": "TEXT",
        "valid_to": "TEXT",
        "supersedes_id": "INTEGER",
        "embedding_status": "TEXT NOT NULL DEFAULT 'ready'",
    }
    for column, ddl in additions.items():
        if not column_exists(conn, "memories", column):
            conn.execute(f"ALTER TABLE memories ADD COLUMN {column} {ddl}")  # noqa: S608


# --------------------------------------------------------------------------- #
# Migration 4: new domain tables — tasks, reminders, tool_runs, summaries,
# outbox (plan §8.4, §8.5). All carry workspace_id for future multi-tenant use.
# --------------------------------------------------------------------------- #
def m4_tasks_and_friends(conn: sqlite3.Connection) -> None:
    _exec(
        conn,
        """
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

        CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            period_type TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            content TEXT NOT NULL,
            source_snapshot TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_summaries_workspace ON summaries(workspace_id);

        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            channel TEXT NOT NULL DEFAULT 'web',
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            available_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);
        CREATE INDEX IF NOT EXISTS idx_outbox_available ON outbox(available_at);
        """,
    )


MIGRATIONS = (
    ("0001_workspaces", m1_workspaces),
    ("0002_soft_delete", m2_soft_delete),
    ("0003_memories_domain", m3_memories_domain),
    ("0004_tasks_and_friends", m4_tasks_and_friends),
)


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    if not table_exists(conn, "schema_migrations"):
        return set()
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r["version"] for r in rows}


def _needs_backup(conn: sqlite3.Connection, db_path: Path) -> bool:
    """A backup is warranted when the file already holds user data but the
    migrations table is absent or empty — i.e. this is a legacy db being
    migrated for the first time."""
    has_user_tables = table_exists(conn, "chats") or table_exists(conn, "memories")
    has_migration_history = table_exists(conn, "schema_migrations") and bool(
        conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
    )
    return has_user_tables and not has_migration_history and db_path.exists()


def make_backup(db_path: Path) -> Path | None:
    """Copy ``db_path`` to a timestamped sibling. Returns the backup path."""
    if not db_path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.stem}.backup-{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup)
    return backup


def run_migrations(conn: sqlite3.Connection, db_path: Path | str) -> list[str]:
    """Apply all pending migrations in order. Returns the applied version names.

    Each migration commits its own transaction. ``schema_migrations`` is updated
    inside the same transaction as the migration so a crash leaves no record of
    a half-applied migration.
    """
    db_path = Path(db_path)
    _ensure_schema_migrations_table(conn)
    applied = _applied_versions(conn)
    pending = [(name, fn) for name, fn in MIGRATIONS if name not in applied]

    if not pending:
        return []

    if _needs_backup(conn, db_path):
        make_backup(db_path)

    newly_applied: list[str] = []
    for name, fn in pending:
        try:
            conn.execute("BEGIN")
            fn(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (name, _now()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        newly_applied.append(name)
    return newly_applied
