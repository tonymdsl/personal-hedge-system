"""Universe construction with a weekly Wikipedia S&P 500 cache.

The module is local-first: cached CSV data is preferred while fresh; dry-runs do
not make network calls; and benchmarks are available even when the S&P 500 cache
has not been populated yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Mapping

import pandas as pd
import requests

from common.config import PROJECT_ROOT, ensure_project_path
from common.dataframe import normalize_tickers

WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SP500_USER_AGENT = "MeridianCapitalPaperTrading/0.1 (S&P 500 universe refresh; contact: research@meridiancapital.local)"
SP500_REQUEST_TIMEOUT = 20.0
UNIVERSE_COLUMNS = [
    "ticker",
    "name",
    "sector",
    "industry",
    "source",
    "is_benchmark",
]


@dataclass(frozen=True)
class UniverseLoadResult:
    """Universe dataframe plus provenance details."""

    frame: pd.DataFrame
    from_cache: bool
    cache_path: Path
    refreshed: bool = False
    error: str | None = None


def _universe_config(config: Mapping[str, object] | None) -> Mapping[str, object]:
    data = config.get("data") if config else None
    if not isinstance(data, Mapping):
        return {}
    universe = data.get("universe")
    return universe if isinstance(universe, Mapping) else {}


def universe_cache_path(config: Mapping[str, object] | None = None) -> Path:
    """Return the constrained local CSV cache path for the S&P 500 universe."""

    universe = _universe_config(config)
    raw = universe.get("cache_path", "cache/universe_sp500.csv")
    return ensure_project_path(str(raw), PROJECT_ROOT)


def cache_ttl_days(config: Mapping[str, object] | None = None) -> int:
    universe = _universe_config(config)
    try:
        return int(universe.get("cache_ttl_days", 7))
    except (TypeError, ValueError):
        return 7


def _empty_universe() -> pd.DataFrame:
    return pd.DataFrame(columns=UNIVERSE_COLUMNS)


def _is_fresh(path: Path, ttl_days: int) -> bool:
    if not path.exists():
        return False
    age_seconds = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    return age_seconds <= ttl_days * 24 * 60 * 60


def _read_cache(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    return normalize_universe_frame(frame)


def normalize_universe_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize Wikipedia/cache columns to the project universe schema."""

    if frame.empty:
        return _empty_universe()

    rename_map: dict[str, str] = {}
    for column in frame.columns:
        normalized = str(column).strip().lower()
        if normalized in {"symbol", "ticker", "ticker_symbol"}:
            rename_map[column] = "ticker"
        elif normalized in {"security", "name", "company", "company_name"}:
            rename_map[column] = "name"
        elif normalized in {"gics sector", "sector"}:
            rename_map[column] = "sector"
        elif normalized in {"gics sub-industry", "gics sub industry", "industry", "sub_industry"}:
            rename_map[column] = "industry"
        elif normalized == "source":
            rename_map[column] = "source"
        elif normalized in {"is_benchmark", "benchmark"}:
            rename_map[column] = "is_benchmark"

    normalized_frame = frame.rename(columns=rename_map).copy()
    for column in UNIVERSE_COLUMNS:
        if column not in normalized_frame.columns:
            if column == "source":
                normalized_frame[column] = "wikipedia_sp500"
            elif column == "is_benchmark":
                normalized_frame[column] = False
            else:
                normalized_frame[column] = ""

    normalized_frame = normalized_frame[UNIVERSE_COLUMNS]
    normalized_frame["ticker"] = normalize_tickers(normalized_frame["ticker"])
    normalized_frame["name"] = normalized_frame["name"].fillna("").astype(str).str.strip()
    normalized_frame["sector"] = normalized_frame["sector"].fillna("").astype(str).str.strip()
    normalized_frame["industry"] = normalized_frame["industry"].fillna("").astype(str).str.strip()
    normalized_frame["source"] = normalized_frame["source"].fillna("wikipedia_sp500").astype(str)
    normalized_frame["is_benchmark"] = normalized_frame["is_benchmark"].fillna(False).astype(bool)
    normalized_frame = normalized_frame[normalized_frame["ticker"].notna() & (normalized_frame["ticker"] != "")]
    return normalized_frame.drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)


def _fetch_sp500_html(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
) -> str:
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def _download_sp500(read_html: object | None = None, fetch_html: object | None = None) -> pd.DataFrame:
    reader = read_html or pd.read_html
    if fetch_html is None and read_html is not None:
        tables = reader(WIKIPEDIA_SP500_URL, flavor="lxml")
    else:
        fetcher = fetch_html or _fetch_sp500_html
        html = fetcher(
            WIKIPEDIA_SP500_URL,
            headers={"User-Agent": SP500_USER_AGENT},
            timeout=SP500_REQUEST_TIMEOUT,
        )
        tables = reader(StringIO(html), flavor="lxml")
    if not tables:
        raise RuntimeError("Wikipedia returned no tables for S&P 500 universe")
    return normalize_universe_frame(tables[0])


def benchmark_universe(config: Mapping[str, object] | None = None) -> pd.DataFrame:
    """Return configured benchmark and sector ETF rows."""

    universe = _universe_config(config)
    if universe.get("include_benchmarks", True) is False:
        return _empty_universe()

    rows: list[dict[str, object]] = []
    for ticker in universe.get("benchmark_tickers", []) or []:
        rows.append(
            {
                "ticker": ticker,
                "name": f"Benchmark {ticker}",
                "sector": "Benchmark",
                "industry": "Benchmark",
                "source": "benchmark",
                "is_benchmark": True,
            }
        )

    sector_etfs = universe.get("sector_etfs", {})
    if isinstance(sector_etfs, Mapping):
        for sector, ticker in sector_etfs.items():
            rows.append(
                {
                    "ticker": ticker,
                    "name": f"Sector ETF {ticker}",
                    "sector": str(sector).replace("_", " ").title(),
                    "industry": "Sector ETF",
                    "source": "benchmark",
                    "is_benchmark": True,
                }
            )

    return normalize_universe_frame(pd.DataFrame(rows)) if rows else _empty_universe()


def append_benchmarks(frame: pd.DataFrame, config: Mapping[str, object] | None = None) -> pd.DataFrame:
    """Append configured benchmark rows, de-duplicating by ticker."""

    base = normalize_universe_frame(frame)
    benchmarks = benchmark_universe(config)
    if benchmarks.empty:
        return base
    combined = pd.concat([base, benchmarks], ignore_index=True)
    combined["is_benchmark"] = combined["is_benchmark"].fillna(False).astype(bool)
    return combined.drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)


def load_sp500_universe(
    config: Mapping[str, object] | None = None,
    *,
    force_refresh: bool = False,
    dry_run: bool = False,
    read_html: object | None = None,
    fetch_html: object | None = None,
) -> UniverseLoadResult:
    """Load the S&P 500 universe from cache or Wikipedia.

    Dry-runs never call Wikipedia. If no cache exists in dry-run mode, the S&P
    member portion is empty and benchmark rows can still be appended by callers.
    """

    cache_path = universe_cache_path(config)
    ttl_days = cache_ttl_days(config)

    if not force_refresh and _is_fresh(cache_path, ttl_days):
        return UniverseLoadResult(frame=_read_cache(cache_path), from_cache=True, cache_path=cache_path)

    if dry_run:
        if cache_path.exists():
            return UniverseLoadResult(frame=_read_cache(cache_path), from_cache=True, cache_path=cache_path)
        return UniverseLoadResult(frame=_empty_universe(), from_cache=False, cache_path=cache_path)

    try:
        frame = _download_sp500(read_html=read_html, fetch_html=fetch_html)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(cache_path, index=False)
        return UniverseLoadResult(frame=frame, from_cache=False, cache_path=cache_path, refreshed=True)
    except Exception as exc:  # network/cache failures should not crash dry local workflows.
        if cache_path.exists():
            return UniverseLoadResult(
                frame=_read_cache(cache_path),
                from_cache=True,
                cache_path=cache_path,
                error=str(exc),
            )
        return UniverseLoadResult(
            frame=_empty_universe(),
            from_cache=False,
            cache_path=cache_path,
            error=str(exc),
        )


def ensure_universe_schema(connection) -> None:
    """Create the local universe table."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS universe (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            sector TEXT,
            industry TEXT,
            source TEXT NOT NULL,
            is_benchmark INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def upsert_universe(connection, frame: pd.DataFrame) -> int:
    """Upsert normalized universe rows into SQLite and return row count."""

    ensure_universe_schema(connection)
    normalized = normalize_universe_frame(frame)
    rows = [
        (
            row.ticker,
            row.name,
            row.sector,
            row.industry,
            row.source,
            int(bool(row.is_benchmark)),
        )
        for row in normalized.itertuples(index=False)
    ]
    connection.executemany(
        """
        INSERT INTO universe(ticker, name, sector, industry, source, is_benchmark, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(ticker) DO UPDATE SET
            name = excluded.name,
            sector = excluded.sector,
            industry = excluded.industry,
            source = excluded.source,
            is_benchmark = excluded.is_benchmark,
            updated_at = excluded.updated_at
        """,
        rows,
    )
    return len(rows)


def get_universe_tickers(connection, *, include_benchmarks: bool = True) -> list[str]:
    """Read universe tickers from SQLite."""

    ensure_universe_schema(connection)
    sql = "SELECT ticker FROM universe"
    params: tuple[object, ...] = ()
    if not include_benchmarks:
        sql += " WHERE is_benchmark = 0"
    sql += " ORDER BY ticker"
    return [str(row[0]) for row in connection.execute(sql, params).fetchall()]


def ingest_universe(
    connection,
    config: Mapping[str, object] | None = None,
    *,
    dry_run: bool = False,
    force_refresh: bool = False,
) -> dict[str, object]:
    """Load, optionally persist, and return Layer 1 universe metadata."""

    ensure_universe_schema(connection)
    loaded = load_sp500_universe(config, force_refresh=force_refresh, dry_run=dry_run)
    frame = append_benchmarks(loaded.frame, config)
    rows_written = 0 if dry_run else upsert_universe(connection, frame)
    return {
        "status": "ok" if loaded.error is None else "partial",
        "count": int(len(frame)),
        "rows_written": rows_written,
        "from_cache": loaded.from_cache,
        "refreshed": loaded.refreshed,
        "cache_path": str(loaded.cache_path.relative_to(PROJECT_ROOT)),
        "error": loaded.error,
        "tickers": frame["ticker"].astype(str).tolist(),
    }
