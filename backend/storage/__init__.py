"""SQLite schema tooling shared by the future application modules.

This module does not connect the in-memory CallService to persistence. Callers
own connections and transactions; keep blocking SQLite work off the media loop.
"""

import hashlib
from pathlib import Path
import re
import sqlite3
import time


MIGRATIONS = Path(__file__).with_name("migrations")


def connect_database(path: str | Path) -> sqlite3.Connection:
    """Open an explicit local database with foreign keys and bounded lock waits."""
    if sqlite3.sqlite_version_info < (3, 38, 0):
        raise RuntimeError("SQLite 3.38 or newer is required (STRICT tables and JSON).")
    connection = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _statements(script: str):
    """Split trusted migration SQL, preserving complete trigger definitions."""
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise ValueError("Migration must end with a complete SQL statement.")


def apply_migrations(
    connection: sqlite3.Connection, directory: Path = MIGRATIONS
) -> list[str]:
    """Apply pending migrations atomically; reject edited or missing history.

    The optional directory is for trusted local migration files and tests, not
    client input. No executescript(): its implicit commit would break rollback.
    """
    if connection.in_transaction:
        raise ValueError("Apply migrations outside an application transaction.")
    if not connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise ValueError("Foreign keys must be enabled on this connection.")
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise ValueError("No migration files found.")
    migrations = []
    for version, path in enumerate(files, start=1):
        if not re.fullmatch(rf"{version:04d}_[a-z0-9_]+\.sql", path.name):
            raise ValueError("Migration numbers must be consecutive, starting at 0001.")
        content = path.read_bytes()
        migrations.append((version, path.name, hashlib.sha256(content).hexdigest(), content.decode()))

    applied = []
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL CHECK (length(checksum) = 64),
                applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= 0)
            ) STRICT
        """)
        history = connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        if len(history) > len(migrations):
            raise ValueError("Database is newer than these migration files.")
        for row, expected in zip(history, migrations):
            if tuple(row) != expected[:3]:
                raise ValueError("Applied migration history differs from the checked-in files.")
        for version, name, checksum, script in migrations[len(history):]:
            for statement in _statements(script):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                (version, name, checksum, time.time_ns() // 1_000_000),
            )
            applied.append(name)
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise sqlite3.IntegrityError("Migration left invalid foreign keys.")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return applied
