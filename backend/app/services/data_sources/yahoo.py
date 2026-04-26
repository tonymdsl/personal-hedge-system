from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import requests


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PersonalHedgeSystem/0.2; +http://127.0.0.1)",
}


def fetch_yahoo_prices(symbol: str, timeout: int = 8) -> pd.DataFrame:
    """Fetch daily prices from Yahoo Finance."""
    clean_symbol = symbol.strip().upper()
    if not clean_symbol:
        return pd.DataFrame()

    response = requests.get(
        YAHOO_CHART_URL.format(symbol=clean_symbol),
        params={"range": "3y", "interval": "1d", "events": "history", "includeAdjustedClose": "true"},
        timeout=timeout,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()
    payload = response.json()
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        return pd.DataFrame()

    timestamps = result.get("timestamp") or []
    quotes = result.get("indicators", {}).get("quote") or []
    if not timestamps or not quotes:
        return pd.DataFrame()

    quote = quotes[0]
    data = pd.DataFrame(
        {
            "date": [datetime.fromtimestamp(value, tz=UTC).date() for value in timestamps],
            "open": quote.get("open", []),
            "high": quote.get("high", []),
            "low": quote.get("low", []),
            "close": quote.get("close", []),
            "volume": quote.get("volume", []),
        }
    )
    data["source"] = "yahoo"
    return data[["date", "open", "high", "low", "close", "volume", "source"]].dropna(subset=["close"])
