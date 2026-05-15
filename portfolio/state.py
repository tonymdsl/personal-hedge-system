"""SQLite state for portfolio positions, history, and approvals."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import pandas as pd

from common.config import PROJECT_ROOT, ensure_project_path
from common.db import initialize_schema


PORTFOLIO_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_positions (
    ticker TEXT PRIMARY KEY,
    weight REAL NOT NULL,
    quantity REAL DEFAULT 0,
    price REAL DEFAULT 0,
    entry_price REAL DEFAULT 0,
    entry_date TEXT,
    current_price REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    sector TEXT,
    factor_scores_at_entry TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS portfolio_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    weight REAL NOT NULL,
    quantity REAL DEFAULT 0,
    price REAL DEFAULT 0,
    current_price REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    sector TEXT,
    factor_scores_at_entry TEXT,
    as_of TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS position_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at TEXT,
    approved_by TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at TEXT,
    approved_by TEXT
);

CREATE TABLE IF NOT EXISTS candidate_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    side TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    payload TEXT,
    decided_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidate_reviews_ticker_id
ON candidate_reviews(ticker, id);
"""

CANDIDATE_REVIEW_STATUSES = {"approved", "rejected", "watch"}


class PortfolioState:
    """Local SQLite portfolio state; safe for paper/research workflows."""

    def __init__(self, db_path: str | Path):
        self.db_path = ensure_project_path(db_path, PROJECT_ROOT)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            self.initialize(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, connection: sqlite3.Connection) -> None:
        initialize_schema(connection)
        connection.executescript(PORTFOLIO_SCHEMA_SQL)
        self._ensure_position_columns(connection)
        connection.commit()

    @staticmethod
    def _ensure_position_columns(connection: sqlite3.Connection) -> None:
        existing = {row[1] for row in connection.execute("PRAGMA table_info(portfolio_positions)").fetchall()}
        columns = {
            "entry_price": "REAL DEFAULT 0",
            "entry_date": "TEXT",
            "current_price": "REAL DEFAULT 0",
            "unrealized_pnl": "REAL DEFAULT 0",
            "sector": "TEXT",
            "factor_scores_at_entry": "TEXT",
        }
        for column, definition in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE portfolio_positions ADD COLUMN {column} {definition}")

        history_existing = {row[1] for row in connection.execute("PRAGMA table_info(portfolio_history)").fetchall()}
        history_columns = {
            "current_price": "REAL DEFAULT 0",
            "unrealized_pnl": "REAL DEFAULT 0",
            "sector": "TEXT",
            "factor_scores_at_entry": "TEXT",
        }
        for column, definition in history_columns.items():
            if column not in history_existing:
                connection.execute(f"ALTER TABLE portfolio_history ADD COLUMN {column} {definition}")

    def set_positions(self, positions: pd.DataFrame | Iterable[Mapping[str, Any]]) -> None:
        frame = positions if isinstance(positions, pd.DataFrame) else pd.DataFrame(list(positions))
        if frame.empty:
            return
        required = {"ticker", "weight"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Missing required position columns: {', '.join(sorted(missing))}")
        with self.connect() as connection:
            self.initialize(connection)
            for row in frame.to_dict(orient="records"):
                connection.execute(
                    """
                    INSERT INTO portfolio_positions(
                        ticker, weight, quantity, price, entry_price, entry_date,
                        current_price, unrealized_pnl, sector, factor_scores_at_entry, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(ticker) DO UPDATE SET
                        weight = excluded.weight,
                        quantity = excluded.quantity,
                        price = excluded.price,
                        current_price = excluded.current_price,
                        unrealized_pnl = excluded.unrealized_pnl,
                        sector = excluded.sector,
                        factor_scores_at_entry = excluded.factor_scores_at_entry,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(row["ticker"]).upper(),
                        float(row.get("weight", 0.0)),
                        float(row.get("quantity", 0.0) or 0.0),
                        float(row.get("price", 0.0) or 0.0),
                        float(row.get("entry_price", row.get("price", 0.0)) or 0.0),
                        row.get("entry_date"),
                        float(row.get("current_price", row.get("price", 0.0)) or 0.0),
                        float(row.get("unrealized_pnl", 0.0) or 0.0),
                        row.get("sector"),
                        json.dumps(row.get("factor_scores_at_entry", {}), sort_keys=True, ensure_ascii=False)
                        if not isinstance(row.get("factor_scores_at_entry"), str)
                        else row.get("factor_scores_at_entry"),
                    ),
                )
            connection.commit()

    def get_positions(self) -> pd.DataFrame:
        with self.connect() as connection:
            self.initialize(connection)
            rows = connection.execute("SELECT * FROM portfolio_positions ORDER BY ticker").fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    def record_history(self, positions: pd.DataFrame | Iterable[Mapping[str, Any]], *, as_of: str | None = None) -> None:
        frame = positions if isinstance(positions, pd.DataFrame) else pd.DataFrame(list(positions))
        if frame.empty:
            return
        timestamp = as_of or "datetime('now')"
        with self.connect() as connection:
            self.initialize(connection)
            for row in frame.to_dict(orient="records"):
                if as_of:
                    connection.execute(
                        """
                        INSERT INTO portfolio_history(
                            ticker, weight, quantity, price, current_price, unrealized_pnl, sector, factor_scores_at_entry, as_of
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(row["ticker"]).upper(),
                            float(row.get("weight", 0.0)),
                            float(row.get("quantity", 0.0) or 0.0),
                            float(row.get("price", 0.0) or 0.0),
                            float(row.get("current_price", row.get("price", 0.0)) or 0.0),
                            float(row.get("unrealized_pnl", 0.0) or 0.0),
                            row.get("sector"),
                            json.dumps(row.get("factor_scores_at_entry", {}), sort_keys=True, ensure_ascii=False)
                            if not isinstance(row.get("factor_scores_at_entry"), str)
                            else row.get("factor_scores_at_entry"),
                            timestamp,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO portfolio_history(
                            ticker, weight, quantity, price, current_price, unrealized_pnl, sector, factor_scores_at_entry
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(row["ticker"]).upper(),
                            float(row.get("weight", 0.0)),
                            float(row.get("quantity", 0.0) or 0.0),
                            float(row.get("price", 0.0) or 0.0),
                            float(row.get("current_price", row.get("price", 0.0)) or 0.0),
                            float(row.get("unrealized_pnl", 0.0) or 0.0),
                            row.get("sector"),
                            json.dumps(row.get("factor_scores_at_entry", {}), sort_keys=True, ensure_ascii=False)
                            if not isinstance(row.get("factor_scores_at_entry"), str)
                            else row.get("factor_scores_at_entry"),
                        ),
                    )
            connection.commit()

    def request_approval(self, action: str, payload: Mapping[str, Any]) -> int:
        with self.connect() as connection:
            self.initialize(connection)
            cursor = connection.execute(
                "INSERT INTO position_approvals(action, payload) VALUES (?, ?)",
                (str(action), json.dumps(payload, sort_keys=True, ensure_ascii=False)),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def approve(self, approval_id: int, *, approved_by: str = "local_user") -> None:
        with self.connect() as connection:
            self.initialize(connection)
            connection.execute(
                """
                UPDATE position_approvals
                SET status = 'approved', approved_at = datetime('now'), approved_by = ?
                WHERE id = ?
                """,
                (approved_by, int(approval_id)),
            )
            connection.commit()

    def record_candidate_review(
        self,
        ticker: str,
        status: str,
        *,
        side: str | None = None,
        reason: str | None = None,
        payload: Mapping[str, Any] | None = None,
        decided_by: str = "local_user",
    ) -> int:
        normalized_ticker = str(ticker).strip().upper()
        if not normalized_ticker:
            raise ValueError("ticker is required")
        normalized_status = str(status).strip().lower()
        if normalized_status not in CANDIDATE_REVIEW_STATUSES:
            raise ValueError("status must be approved, rejected, or watch")
        normalized_side = str(side).strip().lower() if side else None
        if normalized_side == "":
            normalized_side = None
        with self.connect() as connection:
            self.initialize(connection)
            cursor = connection.execute(
                """
                INSERT INTO candidate_reviews(ticker, side, status, reason, payload, decided_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_ticker,
                    normalized_side,
                    normalized_status,
                    reason,
                    json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str),
                    decided_by,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def clear_candidate_review(self, ticker: str) -> int:
        normalized_ticker = str(ticker).strip().upper()
        if not normalized_ticker:
            raise ValueError("ticker is required")
        with self.connect() as connection:
            self.initialize(connection)
            cursor = connection.execute(
                "DELETE FROM candidate_reviews WHERE UPPER(ticker) = ?",
                (normalized_ticker,),
            )
            connection.commit()
            return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def get_candidate_reviews(self, *, latest: bool = True) -> pd.DataFrame:
        with self.connect() as connection:
            self.initialize(connection)
            if latest:
                rows = connection.execute(
                    """
                    SELECT reviews.*
                    FROM candidate_reviews AS reviews
                    JOIN (
                        SELECT ticker, MAX(id) AS id
                        FROM candidate_reviews
                        GROUP BY ticker
                    ) AS latest_reviews
                    ON reviews.ticker = latest_reviews.ticker
                    AND reviews.id = latest_reviews.id
                    ORDER BY reviews.decided_at DESC, reviews.id DESC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM candidate_reviews ORDER BY decided_at DESC, id DESC"
                ).fetchall()
        return pd.DataFrame([dict(row) for row in rows])
