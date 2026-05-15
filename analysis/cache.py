"""SQLite TTL cache for qualitative analyzer artifacts."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from common.config import PROJECT_ROOT, ensure_project_path
from common.db import initialize_schema

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AnalysisCacheError(RuntimeError):
    """Raised for invalid cache configuration."""


class AnalysisCache:
    """Cache analyzer/ticker/artifact JSON payloads with a TTL."""

    def __init__(self, db_path: str | Path, *, table_name: str = "analysis_results", ttl_days: int | float | None = 30):
        if not _IDENTIFIER_RE.match(table_name):
            raise AnalysisCacheError(f"Invalid cache table name: {table_name!r}")
        self.db_path = ensure_project_path(db_path, PROJECT_ROOT)
        self.table_name = table_name
        self.ttl_days = ttl_days
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._initialize(connection)

    @property
    def ttl_seconds(self) -> float | None:
        if self.ttl_days is None:
            return None
        return float(self.ttl_days) * 86_400.0

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self, connection: sqlite3.Connection) -> None:
        initialize_schema(connection)
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                analyzer TEXT NOT NULL,
                ticker TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                model TEXT,
                metadata TEXT,
                created_at REAL NOT NULL,
                expires_at REAL,
                PRIMARY KEY (analyzer, ticker, artifact_id)
            )
            """
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_expires_at ON {self.table_name}(expires_at)"
        )
        connection.commit()

    @staticmethod
    def _normalize_key(analyzer: str, ticker: str, artifact_id: str) -> tuple[str, str, str]:
        analyzer_key = str(analyzer).strip().lower()
        ticker_key = str(ticker).strip().upper()
        artifact_key = str(artifact_id).strip()
        if not analyzer_key or not ticker_key or not artifact_key:
            raise AnalysisCacheError("analyzer, ticker, and artifact_id are required cache keys")
        return analyzer_key, ticker_key, artifact_key

    def set(
        self,
        analyzer: str,
        ticker: str,
        artifact_id: str,
        payload: Mapping[str, Any] | list[Any],
        *,
        model: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        ttl_days: int | float | None | object = None,
    ) -> None:
        analyzer_key, ticker_key, artifact_key = self._normalize_key(analyzer, ticker, artifact_id)
        now = time.time()
        effective_ttl = self.ttl_days if ttl_days is None else ttl_days
        expires_at = None if effective_ttl is None else now + float(effective_ttl) * 86_400.0
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute(
                f"""
                INSERT INTO {self.table_name}(
                    analyzer, ticker, artifact_id, payload, model, metadata, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(analyzer, ticker, artifact_id) DO UPDATE SET
                    payload = excluded.payload,
                    model = excluded.model,
                    metadata = excluded.metadata,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (
                    analyzer_key,
                    ticker_key,
                    artifact_key,
                    json.dumps(payload, sort_keys=True, ensure_ascii=False),
                    model,
                    json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False),
                    now,
                    expires_at,
                ),
            )
            connection.commit()

    def get_record(self, analyzer: str, ticker: str, artifact_id: str) -> dict[str, Any] | None:
        analyzer_key, ticker_key, artifact_key = self._normalize_key(analyzer, ticker, artifact_id)
        now = time.time()
        with self._connect() as connection:
            self._initialize(connection)
            row = connection.execute(
                f"""
                SELECT payload, model, metadata, created_at, expires_at
                FROM {self.table_name}
                WHERE analyzer = ? AND ticker = ? AND artifact_id = ?
                """,
                (analyzer_key, ticker_key, artifact_key),
            ).fetchone()
            if row is None:
                return None
            expires_at = row["expires_at"]
            ttl_seconds = self.ttl_seconds
            created_at = float(row["created_at"])
            expired_by_row = expires_at is not None and float(expires_at) < now
            expired_by_current_ttl = ttl_seconds is not None and created_at + ttl_seconds < now
            if expired_by_row or expired_by_current_ttl:
                connection.execute(
                    f"DELETE FROM {self.table_name} WHERE analyzer = ? AND ticker = ? AND artifact_id = ?",
                    (analyzer_key, ticker_key, artifact_key),
                )
                connection.commit()
                return None
            metadata_raw = row["metadata"]
            try:
                metadata = json.loads(str(metadata_raw or "{}"))
            except json.JSONDecodeError:
                metadata = {}
            return {
                "payload": json.loads(str(row["payload"])),
                "model": row["model"],
                "metadata": metadata,
                "created_at": created_at,
                "expires_at": expires_at,
            }

    def get(self, analyzer: str, ticker: str, artifact_id: str) -> Any | None:
        record = self.get_record(analyzer, ticker, artifact_id)
        return None if record is None else record["payload"]

    def delete_expired(self) -> int:
        now = time.time()
        with self._connect() as connection:
            self._initialize(connection)
            cursor = connection.execute(
                f"DELETE FROM {self.table_name} WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            connection.commit()
            return int(cursor.rowcount or 0)

    def clear(self) -> None:
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute(f"DELETE FROM {self.table_name}")
            connection.commit()
