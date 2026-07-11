"""Single factory for SQLite connections.

Centralizes the connection options the whole app relies on (WAL, foreign keys,
check_same_thread). Stores keep their own ``sqlite3.Connection`` for backward
compatibility, but new code should open connections through :func:`connect`.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


# One reentrant lock per database file: SQLite serializes writes, but we also
# guard schema work and multi-step operations from concurrent threads.
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def lock_for(db_path: str) -> threading.RLock:
    """Return a process-wide RLock keyed by the resolved database path."""
    key = str(Path(db_path).resolve())
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.RLock()
    return _locks[key]


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection configured for the app: WAL, FK on, Row factory."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
