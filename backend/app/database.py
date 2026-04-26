from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from uuid import uuid4

import duckdb
import pandas as pd

from app.config import DEFAULT_WATCHLIST, ensure_runtime_dirs, get_db_path
from app.services.data_sources.sample_data import generate_sample_prices
from app.services.data_sources.stooq import normalize_app_symbol


@contextmanager
def get_connection():
    """Yield a DuckDB connection."""
    ensure_runtime_dirs()
    connection = duckdb.connect(str(get_db_path()))
    try:
        yield connection
    finally:
        connection.close()


def initialize_database() -> None:
    """Create all DuckDB tables."""
    with get_connection() as connection:
        connection.execute(
            """
            create table if not exists watchlist (
                symbol varchar primary key,
                name varchar,
                asset_type varchar,
                currency varchar,
                created_at timestamp default current_timestamp
            )
            """
        )
        connection.execute(
            """
            create table if not exists prices (
                symbol varchar,
                date date,
                open double,
                high double,
                low double,
                close double,
                volume bigint,
                source varchar,
                updated_at timestamp default current_timestamp,
                primary key (symbol, date)
            )
            """
        )
        connection.execute(
            """
            create table if not exists ft_notes (
                id varchar primary key,
                title varchar not null,
                url varchar,
                published_date date not null,
                summary varchar not null,
                assets varchar,
                sectors varchar,
                macro_themes varchar,
                sentiment varchar not null,
                impact varchar not null,
                horizon varchar not null,
                portfolio_relevance varchar default 'medium',
                notes varchar,
                created_at timestamp default current_timestamp
            )
            """
        )
        _ensure_column(connection, "ft_notes", "portfolio_relevance", "varchar default 'medium'")


def _ensure_column(connection: duckdb.DuckDBPyConnection, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing DuckDB table when missing."""
    exists = connection.execute(
        """
        select count(*)
        from information_schema.columns
        where table_name = ? and column_name = ?
        """,
        [table, column],
    ).fetchone()[0]
    if not exists:
        connection.execute(f"alter table {table} add column {column} {ddl}")


def bootstrap_database() -> None:
    """Seed watchlist and sample prices when the database is empty."""
    initialize_database()
    if not list_watchlist():
        for item in DEFAULT_WATCHLIST:
            add_watchlist_item(item)
    for item in list_watchlist():
        if load_prices(item["symbol"]).empty:
            save_prices(item["symbol"], generate_sample_prices(item["symbol"]))


def add_watchlist_item(item: dict) -> dict:
    """Add or update one watchlist item."""
    symbol = normalize_app_symbol(item["symbol"])
    row = {
        "symbol": symbol,
        "name": item.get("name") or symbol,
        "asset_type": item.get("asset_type") or "Equity",
        "currency": item.get("currency") or "USD",
    }
    with get_connection() as connection:
        connection.execute(
            """
            insert or replace into watchlist (symbol, name, asset_type, currency)
            values (?, ?, ?, ?)
            """,
            [row["symbol"], row["name"], row["asset_type"], row["currency"]],
        )
    return row


def list_watchlist() -> list[dict]:
    """Return all watchlist items."""
    with get_connection() as connection:
        rows = connection.execute(
            "select symbol, name, asset_type, currency, created_at from watchlist order by symbol"
        ).fetchdf()
    return rows.to_dict("records")


def save_prices(symbol: str, prices: pd.DataFrame) -> int:
    """Persist historical prices for a symbol."""
    if prices.empty:
        return 0
    clean_symbol = normalize_app_symbol(symbol)
    count = 0
    with get_connection() as connection:
        connection.execute("delete from prices where symbol = ?", [clean_symbol])
        for row in prices.itertuples(index=False):
            connection.execute(
                """
                insert into prices (symbol, date, open, high, low, close, volume, source, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
                """,
                [
                    clean_symbol,
                    pd.to_datetime(row.date).date(),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    int(row.volume),
                    str(row.source),
                ],
            )
            count += 1
    return count


def load_prices(symbol: str) -> pd.DataFrame:
    """Load price history for one symbol."""
    clean_symbol = normalize_app_symbol(symbol)
    with get_connection() as connection:
        data = connection.execute(
            """
            select symbol, date, open, high, low, close, volume, source, updated_at
            from prices
            where symbol = ?
            order by date
            """,
            [clean_symbol],
        ).fetchdf()
    return data


def latest_price_summary(symbol: str) -> dict:
    """Return latest price and one-day return for a symbol."""
    prices = load_prices(symbol)
    if prices.empty:
        return {"symbol": normalize_app_symbol(symbol), "latest_price": None, "latest_return": None}
    close = prices["close"].astype(float)
    latest_return = float(close.iloc[-1] / close.iloc[-2] - 1) if len(close) > 1 else 0.0
    return {
        "symbol": normalize_app_symbol(symbol),
        "latest_price": round(float(close.iloc[-1]), 4),
        "latest_return": round(latest_return, 6),
        "source": str(prices["source"].iloc[-1]),
        "updated_at": prices["updated_at"].iloc[-1],
    }


def create_ft_note(note: dict) -> dict:
    """Store one manual FT note."""
    record = {
        "id": note.get("id") or str(uuid4()),
        "title": note["title"],
        "url": note.get("url"),
        "published_date": note["published_date"],
        "summary": note["summary"],
        "assets": [normalize_app_symbol(asset) for asset in note.get("assets", [])],
        "sectors": note.get("sectors", []),
        "macro_themes": note.get("macro_themes", []),
        "sentiment": note["sentiment"],
        "impact": note["impact"],
        "horizon": note["horizon"],
        "portfolio_relevance": note.get("portfolio_relevance", "medium"),
        "notes": note.get("notes"),
    }
    with get_connection() as connection:
        connection.execute(
            """
            insert into ft_notes (
                id, title, url, published_date, summary, assets, sectors,
                macro_themes, sentiment, impact, horizon, portfolio_relevance, notes
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record["id"],
                record["title"],
                record["url"],
                pd.to_datetime(record["published_date"]).date(),
                record["summary"],
                json.dumps(record["assets"]),
                json.dumps(record["sectors"]),
                json.dumps(record["macro_themes"]),
                record["sentiment"],
                record["impact"],
                record["horizon"],
                record["portfolio_relevance"],
                record["notes"],
            ],
        )
    return get_ft_note(record["id"])


def _decode_list(value: str | None) -> list[str]:
    """Decode JSON list fields from DuckDB."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _serialize_note(row: dict) -> dict:
    """Serialize a DuckDB FT note row."""
    row["assets"] = _decode_list(row.get("assets"))
    row["sectors"] = _decode_list(row.get("sectors"))
    row["macro_themes"] = _decode_list(row.get("macro_themes"))
    return row


def get_ft_note(note_id: str) -> dict:
    """Return one FT note by id."""
    with get_connection() as connection:
        data = connection.execute("select * from ft_notes where id = ?", [note_id]).fetchdf()
    if data.empty:
        raise KeyError(note_id)
    return _serialize_note(data.iloc[0].to_dict())


def list_ft_notes() -> list[dict]:
    """Return FT notes newest first."""
    with get_connection() as connection:
        data = connection.execute("select * from ft_notes order by created_at desc").fetchdf()
    return [_serialize_note(row) for row in data.to_dict("records")]


def latest_price_date(symbol: str) -> date | None:
    """Return the latest available price date for a symbol."""
    prices = load_prices(symbol)
    if prices.empty:
        return None
    return pd.to_datetime(prices["date"].max()).date()
