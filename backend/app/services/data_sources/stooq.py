from __future__ import annotations

from datetime import date, timedelta
from io import StringIO

import pandas as pd
import requests

from app.services.data_sources.sample_data import generate_sample_prices
from app.services.data_sources.yahoo import fetch_yahoo_prices


STOOQ_URL = "https://stooq.com/q/d/l/"
UNSUPPORTED_SYMBOLS = {"BTC-USD"}


def normalize_app_symbol(symbol: str) -> str:
    """Normalize symbols for internal storage."""
    return symbol.strip().upper().replace(".US", "")


def normalize_stooq_symbol(symbol: str) -> str | None:
    """Convert common user tickers into Stooq symbols."""
    clean = symbol.strip().lower()
    if not clean or clean.upper() in UNSUPPORTED_SYMBOLS:
        return None
    if clean.endswith(".us"):
        return clean
    if "-" in clean:
        return None
    return f"{clean}.us"


def fetch_stooq_prices(symbol: str, timeout: int = 8) -> pd.DataFrame:
    """Fetch daily prices from Stooq for a supported symbol."""
    stooq_symbol = normalize_stooq_symbol(symbol)
    if stooq_symbol is None:
        return pd.DataFrame()
    today = date.today()
    start = today - timedelta(days=365 * 3)
    params = {
        "s": stooq_symbol,
        "i": "d",
        "d1": start.strftime("%Y%m%d"),
        "d2": today.strftime("%Y%m%d"),
    }
    response = requests.get(STOOQ_URL, params=params, timeout=timeout)
    response.raise_for_status()
    data = pd.read_csv(StringIO(response.text))
    if data.empty or "Date" not in data.columns:
        return pd.DataFrame()
    data = data.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    data["date"] = pd.to_datetime(data["date"]).dt.date
    data["source"] = "stooq"
    return data[["date", "open", "high", "low", "close", "volume", "source"]].dropna(subset=["close"])


def fetch_prices_with_fallback(symbol: str) -> pd.DataFrame:
    """Fetch free market prices or return deterministic sample prices."""
    for fetcher in (fetch_stooq_prices, fetch_yahoo_prices):
        try:
            data = fetcher(symbol)
            if not data.empty:
                return data
        except Exception:
            pass
    return generate_sample_prices(symbol)
