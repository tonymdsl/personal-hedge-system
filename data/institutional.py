"""13F institutional ownership helpers for tracked funds."""
from __future__ import annotations

from typing import Iterable, Mapping

import pandas as pd

TRACKED_FUNDS = [
    'Citadel', 'Point72', 'Bridgewater', 'Tiger Global', 'Third Point',
    'Berkshire Hathaway', 'Appaloosa', 'Baupost', 'Pershing Square',
]


def ensure_institutional_schema(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS institutional_holdings (
            fund_name TEXT NOT NULL,
            ticker TEXT NOT NULL,
            shares REAL,
            market_value REAL,
            report_date TEXT NOT NULL,
            is_new_position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (fund_name, ticker, report_date)
        );
        """
    )
    connection.commit()


def normalize_13f_holdings(frame: pd.DataFrame, fund_name: str | None = None, report_date: str | None = None) -> pd.DataFrame:
    rename = {
        'nameOfIssuer': 'issuer', 'symbol': 'ticker', 'Ticker': 'ticker',
        'sshPrnamt': 'shares', 'Value': 'market_value', 'value': 'market_value',
        'reportDate': 'report_date', 'periodOfReport': 'report_date', 'fund': 'fund_name',
    }
    df = frame.rename(columns={c: rename.get(c, c) for c in frame.columns}).copy()
    if fund_name is not None:
        df['fund_name'] = fund_name
    if report_date is not None:
        df['report_date'] = report_date
    for col in ('fund_name', 'ticker', 'report_date'):
        if col not in df.columns:
            df[col] = None
    df['ticker'] = df['ticker'].astype(str).str.upper().str.replace('.', '-', regex=False).str.strip()
    for col in ('shares', 'market_value'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        else:
            df[col] = pd.NA
    return df[['fund_name', 'ticker', 'shares', 'market_value', 'report_date']].dropna(subset=['ticker'])


def calculate_institutional_signals(current: pd.DataFrame, previous: pd.DataFrame | None = None) -> pd.DataFrame:
    cur = normalize_13f_holdings(current)
    if cur.empty:
        return pd.DataFrame(columns=['ticker', 'tracked_fund_holder_count', 'aggregate_holding_change', 'multi_fund_opening'])
    grouped = cur.groupby('ticker', as_index=False).agg(
        tracked_fund_holder_count=('fund_name', 'nunique'),
        current_shares=('shares', 'sum'),
        current_value=('market_value', 'sum'),
    )
    if previous is not None and not previous.empty:
        prev = normalize_13f_holdings(previous).groupby('ticker', as_index=False).agg(previous_shares=('shares', 'sum'))
        grouped = grouped.merge(prev, on='ticker', how='left')
    else:
        grouped['previous_shares'] = 0.0
    grouped['aggregate_holding_change'] = grouped['current_shares'].fillna(0) - grouped['previous_shares'].fillna(0)
    previous_pairs = set()
    if previous is not None and not previous.empty:
        prev_norm = normalize_13f_holdings(previous)
        previous_pairs = set(zip(prev_norm['fund_name'].astype(str), prev_norm['ticker'].astype(str)))
    cur['is_new_position'] = [(str(row.fund_name), str(row.ticker)) not in previous_pairs for row in cur.itertuples(index=False)]
    openings = cur[cur['is_new_position']].groupby('ticker')['fund_name'].nunique().rename('new_fund_openings')
    grouped = grouped.merge(openings, on='ticker', how='left')
    grouped['new_fund_openings'] = grouped['new_fund_openings'].fillna(0).astype(int)
    grouped['multi_fund_opening'] = grouped['new_fund_openings'] >= 3
    return grouped


def ingest_13f(tickers: Iterable[str], connection, *, config: Mapping[str, object] | None = None, no_13f: bool = False) -> dict[str, int | bool | str]:
    ensure_institutional_schema(connection)
    if no_13f:
        return {'skipped': True, 'reason': 'no_13f', 'holdings': 0}
    return {'skipped': True, 'reason': 'edgar_13f_fetch_not_configured', 'holdings': 0}
