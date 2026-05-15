"""SQLite helpers for local-first state and cache files."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .config import PROJECT_ROOT, ensure_project_path

DEFAULT_DB_RELATIVE_PATH = "cache/meridian.sqlite3"
BASE_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def default_database_path(config: Mapping[str, Any] | None = None) -> Path:
    """Return the configured SQLite path, constrained to the project root."""

    default_path = DEFAULT_DB_RELATIVE_PATH
    if config:
        project_config = config.get("project", {})
        if isinstance(project_config, Mapping):
            default_path = str(project_config.get("default_db_path", default_path))
    return ensure_project_path(default_path, PROJECT_ROOT)


def get_connection(
    db_path: str | os.PathLike[str] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
    timeout: float = 30.0,
    row_factory: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite connection and create the parent directory if needed."""

    path = ensure_project_path(db_path, PROJECT_ROOT) if db_path else default_database_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=timeout)
    connection.execute("PRAGMA foreign_keys = ON")
    if row_factory:
        connection.row_factory = sqlite3.Row
    return connection


def initialize_schema(
    connection: sqlite3.Connection,
    *,
    schema_sql: str | None = None,
    version: str = "base_v1",
) -> sqlite3.Connection:
    """Create the base local schema and record the applied schema version."""

    connection.executescript(schema_sql or BASE_SCHEMA_SQL)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        (version,),
    )
    connection.commit()
    return connection


@contextmanager
def connect(
    db_path: str | os.PathLike[str] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
    initialize: bool = True,
) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success, rolls back on errors, closes always."""

    connection = get_connection(db_path, config=config)
    try:
        if initialize:
            initialize_schema(connection)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """Return true if a table exists in the connected database."""

    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def upsert_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    """Insert or update a metadata key/value pair."""

    connection.execute(
        """
        INSERT INTO app_metadata(key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value),
    )


def fetch_metadata(connection: sqlite3.Connection, key: str) -> str | None:
    """Fetch a metadata value by key."""

    row = connection.execute(
        "SELECT value FROM app_metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return str(row["value"] if isinstance(row, sqlite3.Row) else row[0])
