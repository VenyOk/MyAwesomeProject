"""Lightweight, idempotent SQLite migrations.

Both stores create their tables with ``CREATE TABLE IF NOT EXISTS`` and never
``ALTER``. New columns/tables are therefore added here through guarded helpers
that introspect ``PRAGMA table_info`` so existing ``brain.db`` files keep working.
"""
from __future__ import annotations

import sqlite3


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    row = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
    return any(r[1] == column for r in row)


def add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add ``column`` to ``table`` if it does not already exist.

    ``ddl`` is the full column definition after the name, e.g. ``"INTEGER DEFAULT 0"``.
    SQLite has no ``ADD COLUMN IF NOT EXISTS``, hence the introspection.
    """
    if column_exists(conn, table, column):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")  # noqa: S608
    conn.commit()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None
