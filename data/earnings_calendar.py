"""Upcoming earnings calendar helpers."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

import pandas as pd


def ensure_earnings_calendar_schema(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            ticker TEXT NOT NULL,
            earnings_date TEXT NOT NULL,
            eps_estimate REAL,
            source TEXT NOT NULL DEFAULT 'yfinance',
            cached_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (ticker, earnings_date)
        )
        """
    )
    connection.commit()


def normalize_earnings_calendar(frame: pd.DataFrame, *, today: date | None = None, lookahead_days: int = 30) -> pd.DataFrame:
    today = today or date.today()
    end = today + timedelta(days=lookahead_days)
    df = frame.rename(columns={'Symbol': 'ticker', 'Earnings Date': 'earnings_date', 'EPS Estimate': 'eps_estimate'}).copy()
    if 'ticker' not in df.columns:
        df['ticker'] = None
    if 'earnings_date' not in df.columns:
        df['earnings_date'] = None
    df['ticker'] = df['ticker'].astype(str).str.upper().str.strip()
    df['earnings_date'] = pd.to_datetime(df['earnings_date'], errors='coerce').dt.date
    df = df[(df['earnings_date'] >= today) & (df['earnings_date'] <= end)]
    cols = ['ticker', 'earnings_date'] + ([c for c in ['eps_estimate'] if c in df.columns])
    return df[cols]


def ingest_earnings_calendar(tickers: Iterable[str], connection, *, dry_run: bool = True, lookahead_days: int = 30) -> dict[str, int | bool]:
    ensure_earnings_calendar_schema(connection)
    return {'events': 0, 'dry_run': dry_run, 'lookahead_days': lookahead_days}
