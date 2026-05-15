"""Analyst estimate snapshots."""
from __future__ import annotations

from datetime import date
from typing import Mapping


def ensure_estimates_schema(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS analyst_estimates (
            ticker TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            forward_eps REAL,
            price_target_consensus REAL,
            source TEXT NOT NULL DEFAULT 'yfinance',
            PRIMARY KEY (ticker, snapshot_date)
        )
        """
    )
    connection.commit()


def estimate_snapshot(ticker: str, info: Mapping[str, object], snapshot_date: str | None = None) -> dict[str, object]:
    return {
        'ticker': ticker.upper(),
        'snapshot_date': snapshot_date or date.today().isoformat(),
        'forward_eps': info.get('forwardEps') or info.get('epsForward'),
        'price_target_consensus': info.get('targetMeanPrice'),
        'source': 'yfinance',
    }


def upsert_estimate(connection, row: Mapping[str, object]) -> int:
    ensure_estimates_schema(connection)
    connection.execute(
        """
        INSERT INTO analyst_estimates(ticker, snapshot_date, forward_eps, price_target_consensus, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ticker, snapshot_date) DO UPDATE SET
            forward_eps=excluded.forward_eps,
            price_target_consensus=excluded.price_target_consensus,
            source=excluded.source
        """,
        (row['ticker'], row['snapshot_date'], row.get('forward_eps'), row.get('price_target_consensus'), row.get('source', 'yfinance')),
    )
    connection.commit()
    return 1


def ingest_estimates(tickers, connection, *, dry_run: bool = True) -> dict[str, int | bool]:
    ensure_estimates_schema(connection)
    return {'snapshots': 0, 'dry_run': dry_run}
