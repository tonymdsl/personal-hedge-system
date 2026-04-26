from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.database import latest_price_summary, list_watchlist, load_prices, save_prices
from app.services.analytics.metrics import calculate_metrics, performance_points
from app.services.analytics.regime import classify_market_regime
from app.services.analytics.risk import calculate_portfolio_risk
from app.services.data_sources.stooq import fetch_prices_with_fallback, normalize_app_symbol


def refresh_symbol(symbol: str) -> dict:
    """Refresh one symbol and store prices."""
    clean_symbol = normalize_app_symbol(symbol)
    data = fetch_prices_with_fallback(clean_symbol)
    rows = save_prices(clean_symbol, data)
    return {"symbol": clean_symbol, "rows": rows, "source": data["source"].iloc[-1] if not data.empty else None}


def refresh_watchlist() -> dict:
    """Refresh all watchlist symbols."""
    results = [refresh_symbol(item["symbol"]) for item in list_watchlist()]
    return {"updated": results, "symbols": len(results), "rows": sum(item["rows"] for item in results)}


def ensure_symbol_prices(symbol: str) -> pd.DataFrame:
    """Load existing prices or refresh a missing symbol."""
    clean_symbol = normalize_app_symbol(symbol)
    data = load_prices(clean_symbol)
    if data.empty or _latest_source(data) == "sample":
        refresh_symbol(clean_symbol)
        data = load_prices(clean_symbol)
    return data


def _latest_source(data: pd.DataFrame) -> str | None:
    """Return the source for the newest loaded price row."""
    if data.empty or "source" not in data.columns:
        return None
    return str(data["source"].iloc[-1]).lower()


def get_regime() -> dict:
    """Return current market regime from SPY and QQQ."""
    price_map = {"SPY": ensure_symbol_prices("SPY"), "QQQ": ensure_symbol_prices("QQQ")}
    return classify_market_regime(price_map)


def get_dashboard_payload() -> dict:
    """Build dashboard data for the frontend."""
    watchlist = list_watchlist()
    summaries = []
    price_map = {}
    for item in watchlist:
        prices = ensure_symbol_prices(item["symbol"])
        price_map[item["symbol"]] = prices
        metrics = calculate_metrics(prices)
        summary = latest_price_summary(item["symbol"])
        summaries.append({**item, **summary, "metrics": metrics, "metadata": build_price_metadata(prices)})
    movers = sorted(
        [item for item in summaries if item.get("latest_return") is not None],
        key=lambda item: abs(item["latest_return"]),
        reverse=True,
    )[:8]
    benchmark = ensure_symbol_prices("SPY")
    return {
        "watchlist": summaries,
        "regime": get_regime(),
        "performance": performance_points(benchmark),
        "movers": movers,
        "risk": calculate_portfolio_risk(price_map),
    }


def _iso_datetime(value) -> str | None:
    """Serialize a date-like value to ISO datetime text."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return pd.to_datetime(value).isoformat()


def _iso_date(value) -> str | None:
    """Serialize a date-like value to ISO date text."""
    if value is None or pd.isna(value):
        return None
    return pd.to_datetime(value).date().isoformat()


def build_price_metadata(prices: pd.DataFrame) -> dict:
    """Build data source metadata for a price history."""
    if prices.empty:
        return {
            "source": "missing",
            "last_updated": None,
            "data_range_start": None,
            "data_range_end": None,
            "price_type": "close",
            "is_sample_data": True,
        }
    sources = sorted({str(source).lower() for source in prices["source"].dropna().unique()})
    source = sources[0] if len(sources) == 1 else "mixed"
    return {
        "source": source,
        "last_updated": _iso_datetime(prices["updated_at"].max()) if "updated_at" in prices else None,
        "data_range_start": _iso_date(prices["date"].min()),
        "data_range_end": _iso_date(prices["date"].max()),
        "price_type": "close",
        "is_sample_data": "sample" in sources,
    }


def serialize_prices(prices: pd.DataFrame) -> list[dict]:
    """Serialize price rows for JSON responses."""
    rows = []
    for row in prices.sort_values("date").to_dict("records"):
        row["date"] = _iso_date(row.get("date"))
        row["updated_at"] = _iso_datetime(row.get("updated_at"))
        rows.append(row)
    return rows


def get_price_payload(symbol: str) -> dict:
    """Return prices and metadata for one symbol."""
    clean_symbol = normalize_app_symbol(symbol)
    prices = ensure_symbol_prices(clean_symbol)
    return {"symbol": clean_symbol, "metadata": build_price_metadata(prices), "prices": serialize_prices(prices)}
