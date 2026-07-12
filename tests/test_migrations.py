from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db.connection import connect
from app.db.migrations import (
    MIGRATIONS,
    make_backup,
    run_migrations,
)


def _versions(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r["version"] for r in rows}


def test_fresh_database_applies_all_migrations(tmp_path: Path):
    """A brand-new db gets every migration applied and recorded exactly once."""
    db = tmp_path / "fresh.db"
    conn = connect(db)
    applied = run_migrations(conn, db)
    assert applied == [name for name, _ in MIGRATIONS]
    assert _versions(conn) == {name for name, _ in MIGRATIONS}
    conn.close()


def test_migrations_are_idempotent(tmp_path: Path):
    """Running migrations twice applies nothing the second time."""
    db = tmp_path / "idem.db"
    conn = connect(db)
    run_migrations(conn, db)
    second = run_migrations(conn, db)
    assert second == []
    conn.close()


def test_migrations_create_expected_tables(tmp_path: Path):
    db = tmp_path / "tables.db"
    conn = connect(db)
    run_migrations(conn, db)
    tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for expected in (
        "schema_migrations",
        "workspaces",
        "tasks",
        "reminders",
        "tool_runs",
        "summaries",
        "outbox",
        "confirmations",
    ):
        assert expected in tables
    conn.close()


def test_legacy_database_preserves_data_and_backs_up(tmp_path: Path):
    """A pre-existing db with user data but no migration history must be
    backed up before migration, and all existing rows preserved."""
    db = tmp_path / "legacy.db"
    conn = connect(db)
    # build a legacy schema + data the way the old stores did
    conn.executescript(
        """
        CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, chat_id INTEGER, role TEXT, content TEXT, created_at TEXT);
        CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, summary TEXT, tags TEXT DEFAULT '[]', source TEXT DEFAULT 'chat', created_at TEXT, updated_at TEXT);
        """
    )
    conn.execute("INSERT INTO chats (title, created_at, updated_at) VALUES ('Привет', '2024-01-01', '2024-01-01')")
    conn.execute("INSERT INTO memories (content, created_at, updated_at) VALUES ('не ем арахис', '2024-01-01', '2024-01-01')")
    conn.commit()
    conn.close()

    conn = connect(db)
    applied = run_migrations(conn, db)
    assert len(applied) == len(MIGRATIONS)

    # user data preserved
    assert conn.execute("SELECT COUNT(*) AS c FROM chats").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"] == 1
    # backfilled columns have sane defaults
    chat = conn.execute("SELECT workspace_id FROM chats").fetchone()
    assert chat["workspace_id"] == 1
    mem = conn.execute("SELECT workspace_id, kind, status, importance FROM memories").fetchone()
    assert mem["workspace_id"] == 1
    assert mem["kind"] == "fact"
    assert mem["status"] == "active"
    assert mem["importance"] == 0.5
    # default workspace exists
    assert conn.execute("SELECT COUNT(*) AS c FROM workspaces").fetchone()["c"] == 1
    conn.close()

    # a timestamped backup was created next to the db
    backups = list(db.parent.glob("*.backup-*"))
    assert len(backups) == 1


def test_no_backup_on_fresh_database(tmp_path: Path):
    """A brand-new db (no user tables) must not produce a backup."""
    db = tmp_path / "nobackup.db"
    conn = connect(db)
    run_migrations(conn, db)
    conn.close()
    backups = list(db.parent.glob("*.backup-*"))
    assert backups == []


def test_make_backup_returns_none_for_missing_file(tmp_path: Path):
    assert make_backup(tmp_path / "does-not-exist.db") is None


def test_migration_failure_rolls_back(tmp_path: Path):
    """A migration that raises must roll back its transaction and leave no
    record of a half-applied migration in schema_migrations."""
    db = tmp_path / "fail.db"
    conn = connect(db)
    run_migrations(conn, db)

    def boom(c):
        raise RuntimeError("simulated failure")

    # inject a bad migration and re-run
    from app.db import migrations as mod

    original = mod.MIGRATIONS
    mod.MIGRATIONS = (*original, ("9999_boom", boom))
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            run_migrations(conn, db)
    finally:
        mod.MIGRATIONS = original

    # 9999_boom was NOT recorded as applied
    assert "9999_boom" not in _versions(conn)
    conn.close()
