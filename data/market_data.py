"""Daily OHLCV ingestion into the local SQLite ``daily_prices`` table."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping

import pandas as pd

from .providers import select_provider

PRICE_COLUMNS = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]


class MarketDataError(RuntimeError):
    """Raised when market data cannot be normalized."""


def ensure_price_schema(connection) -> None:
    """Create the daily prices schema."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adj_close REAL,
            volume REAL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (ticker, date)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON daily_prices(date)"
    )


def _prices_config(config: Mapping[str, object] | None) -> Mapping[str, object]:
    data = config.get("data") if config else None
    if not isinstance(data, Mapping):
        return {}
    prices = data.get("prices")
    return prices if isinstance(prices, Mapping) else {}


def lookback_years(config: Mapping[str, object] | None = None) -> int:
    try:
        return int(_prices_config(config).get("lookback_years", 3))
    except (TypeError, ValueError):
        return 3


def interval(config: Mapping[str, object] | None = None) -> str:
    return str(_prices_config(config).get("interval", "1d"))


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def next_start_date(connection, ticker: str, *, years: int = 3, incremental: bool = True) -> date | None:
    """Return the next date to request for ``ticker`` or ``None`` if up to date."""

    ensure_price_schema(connection)
    fallback = _today_utc() - timedelta(days=365 * years)
    if not incremental:
        return fallback
    row = connection.execute(
        "SELECT MAX(date) AS max_date FROM daily_prices WHERE ticker = ?",
        (ticker.upper(),),
    ).fetchone()
    max_date = row[0] if row else None
    if not max_date:
        return fallback
    parsed = datetime.strptime(str(max_date), "%Y-%m-%d").date()
    start = parsed + timedelta(days=1)
    if start > _today_utc():
        return None
    return start


def _normalize_downloaded_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        # A single-ticker yfinance call may still return multi-index columns in
        # newer versions. Prefer the first level carrying price names.
        if ticker in frame.columns.get_level_values(-1):
            frame = frame.xs(ticker, axis=1, level=-1, drop_level=True)
        elif ticker in frame.columns.get_level_values(0):
            frame = frame.xs(ticker, axis=1, level=0, drop_level=True)
        else:
            frame.columns = ["_".join(str(part) for part in column if part) for column in frame.columns]

    if "Date" not in frame.columns:
        frame = frame.reset_index()

    rename = {
        "Date": "date",
        "Datetime": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Adj_Close": "adj_close",
        "Volume": "volume",
    }
    frame = frame.rename(columns=rename)
    if "date" not in frame.columns:
        first_col = frame.columns[0]
        frame = frame.rename(columns={first_col: "date"})

    for column in PRICE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ticker.upper() if column == "ticker" else None
    frame["ticker"] = ticker.upper()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "adj_close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[PRICE_COLUMNS].dropna(subset=["date"])
    return frame.drop_duplicates(subset=["ticker", "date"], keep="last").reset_index(drop=True)


def fetch_yfinance_ohlcv(
    tickers: Iterable[str],
    *,
    start: date | str,
    end: date | str | None = None,
    interval_value: str = "1d",
    downloader: Callable[..., pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV data from yfinance for the supplied tickers."""

    if downloader is None:
        try:
            import yfinance as yf  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency declared in pyproject.
            raise MarketDataError("yfinance is required for market data ingestion") from exc
        downloader = yf.download

    frames: list[pd.DataFrame] = []
    end_value = end or (_today_utc() + timedelta(days=1))
    for ticker in [str(item).upper().strip() for item in tickers if str(item).strip()]:
        raw = downloader(
            ticker,
            start=str(start),
            end=str(end_value),
            interval=interval_value,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        normalized = _normalize_downloaded_frame(raw, ticker)
        if not normalized.empty:
            frames.append(normalized)
    if not frames:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def upsert_daily_prices(connection, frame: pd.DataFrame, *, source: str = "yfinance") -> int:
    """Upsert OHLCV rows and return the number of rows supplied."""

    ensure_price_schema(connection)
    if frame.empty:
        return 0
    normalized = frame.copy()
    for column in PRICE_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None
    normalized["ticker"] = normalized["ticker"].astype(str).str.upper().str.strip()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "adj_close", "volume"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["ticker", "date"])
    rows = [
        (
            row.ticker,
            row.date,
            None if pd.isna(row.open) else float(row.open),
            None if pd.isna(row.high) else float(row.high),
            None if pd.isna(row.low) else float(row.low),
            None if pd.isna(row.close) else float(row.close),
            None if pd.isna(row.adj_close) else float(row.adj_close),
            None if pd.isna(row.volume) else float(row.volume),
            source,
        )
        for row in normalized[PRICE_COLUMNS].itertuples(index=False)
    ]
    connection.executemany(
        """
        INSERT INTO daily_prices(ticker, date, open, high, low, close, adj_close, volume, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(ticker, date) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            adj_close = excluded.adj_close,
            volume = excluded.volume,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        rows,
    )
    return len(rows)


def ingest_market_data(
    connection,
    tickers: Iterable[str],
    config: Mapping[str, object] | None = None,
    *,
    dry_run: bool = False,
    downloader: Callable[..., pd.DataFrame] | None = None,
) -> dict[str, object]:
    """Incrementally ingest 3y daily OHLCV data into SQLite."""

    ensure_price_schema(connection)
    ticker_list = [str(ticker).upper().strip() for ticker in tickers if str(ticker).strip()]
    selection = select_provider("prices", config=config)
    if dry_run:
        return {
            "status": "skipped",
            "reason": "dry_run",
            "provider": selection.provider,
            "tickers": len(ticker_list),
            "rows_written": 0,
            "count": 0,
        }
    if not ticker_list:
        return {"status": "skipped", "reason": "no_tickers", "rows_written": 0, "count": 0}
    if selection.provider not in {"yfinance", "polygon"}:
        return {
            "status": "skipped",
            "reason": selection.reason,
            "provider": selection.provider,
            "rows_written": 0,
            "count": 0,
        }

    years = lookback_years(config)
    interval_value = interval(config)
    incremental = bool(_prices_config(config).get("incremental", True))
    total_rows = 0
    errors: dict[str, str] = {}

    # Polygon is selected only when a key exists. Layer 1 stores the selection,
    # but yfinance remains the implemented local-first downloader for OHLCV until
    # a paid Polygon client is added.
    source = "yfinance" if selection.provider in {"yfinance", "polygon"} else str(selection.provider)

    for ticker in ticker_list:
        start = next_start_date(connection, ticker, years=years, incremental=incremental)
        if start is None:
            continue
        try:
            frame = fetch_yfinance_ohlcv(
                [ticker],
                start=start,
                interval_value=interval_value,
                downloader=downloader,
            )
            total_rows += upsert_daily_prices(connection, frame, source=source)
        except Exception as exc:  # keep a partial run moving across tickers.
            errors[ticker] = str(exc)

    return {
        "status": "partial" if errors else "ok",
        "provider": selection.provider,
        "tickers": len(ticker_list),
        "rows_written": total_rows,
        "count": total_rows,
        "errors": errors,
    }
