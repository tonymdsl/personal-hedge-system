"""Short-interest snapshots."""
from __future__ import annotations

from datetime import date
from typing import Mapping


def ensure_short_interest_schema(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS short_interest_snapshots (
            ticker TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            shares_short REAL,
            short_ratio REAL,
            short_percent_float REAL,
            source TEXT NOT NULL DEFAULT 'yfinance',
            PRIMARY KEY (ticker, snapshot_date)
        )
        """
    )
    connection.commit()


def snapshot_from_yfinance_info(ticker: str, info: Mapping[str, object], snapshot_date: str | None = None) -> dict[str, object]:
    return {
        'ticker': ticker.upper(),
        'snapshot_date': snapshot_date or date.today().isoformat(),
        'shares_short': info.get('sharesShort'),
        'short_ratio': info.get('shortRatio'),
        'short_percent_float': info.get('shortPercentOfFloat'),
        'source': 'yfinance',
    }


def upsert_short_interest(connection, row: Mapping[str, object]) -> int:
    ensure_short_interest_schema(connection)
    connection.execute(
        """
        INSERT INTO short_interest_snapshots(ticker, snapshot_date, shares_short, short_ratio, short_percent_float, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, snapshot_date) DO UPDATE SET
            shares_short=excluded.shares_short,
            short_ratio=excluded.short_ratio,
            short_percent_float=excluded.short_percent_float,
            source=excluded.source
        """,
        (row['ticker'], row['snapshot_date'], row.get('shares_short'), row.get('short_ratio'), row.get('short_percent_float'), row.get('source', 'yfinance')),
    )
    connection.commit()
    return 1


def ingest_short_interest(tickers, connection, *, dry_run: bool = True) -> dict[str, int | bool]:
    ensure_short_interest_schema(connection)
    return {'snapshots': 0, 'dry_run': dry_run}
