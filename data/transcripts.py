"""Financial Modeling Prep earnings transcript helpers."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

import requests

from data.providers import get_api_key


def ensure_transcripts_schema(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_transcripts (
            ticker TEXT NOT NULL,
            fiscal_year INTEGER,
            quarter INTEGER,
            transcript TEXT,
            source TEXT NOT NULL DEFAULT 'fmp',
            cached_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (ticker, fiscal_year, quarter)
        )
        """
    )
    connection.commit()


def fetch_fmp_transcript(ticker: str, year: int, quarter: int, api_key: str, *, timeout: float = 20.0) -> dict[str, object] | None:
    normalized_ticker = ticker.strip().upper()
    url = 'https://financialmodelingprep.com/stable/earning-call-transcript'
    response = requests.get(url, params={'symbol': normalized_ticker, 'year': year, 'quarter': quarter, 'apikey': api_key}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not data:
        return None
    if isinstance(data, list):
        item = data[0] if data else None
    elif isinstance(data, Mapping):
        item = data
    else:
        item = None
    if not isinstance(item, Mapping):
        return None
    transcript = item.get('content') or item.get('transcript')
    if not isinstance(transcript, str) or not transcript.strip():
        return None
    return {'ticker': normalized_ticker, 'fiscal_year': year, 'quarter': quarter, 'transcript': transcript, 'source': 'fmp'}


def ingest_transcripts(tickers: Iterable[str], connection, *, config: Mapping[str, object] | None = None, candidate_only: bool = True) -> dict[str, int | bool | str]:
    ensure_transcripts_schema(connection)
    api_key = (get_api_key('fmp', config=config) or '').strip()
    if not api_key:
        return {'skipped': True, 'reason': 'missing_FMP_API_KEY', 'transcripts': 0}
    return {'skipped': True, 'reason': 'candidate_gate_not_available_yet' if candidate_only else 'not_requested', 'transcripts': 0}
