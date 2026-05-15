"""Build Layer 2 factor input frames from the local Layer 1 database."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from common.config import PROJECT_ROOT, ensure_project_path
from common.db import table_exists
from data.institutional import calculate_institutional_signals


BASE_COLUMNS = [
    "date",
    "ticker",
    "name",
    "gics_sector",
    "industry",
    "price",
    "close",
    "adj_close",
    "volume",
    "price_21d_ago",
    "price_63d_ago",
    "price_126d_ago",
    "price_252d_ago",
    "return_1m",
    "return_3m",
    "return_6m",
    "return_12m",
    "return_12_1m",
    "momentum_acceleration",
    "high_52w",
    "proximity_52w",
    "forward_return",
]


def _empty_frame(columns: Iterable[str] = BASE_COLUMNS) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def _read_table(connection, table_name: str) -> pd.DataFrame:
    if not table_exists(connection, table_name):
        return pd.DataFrame()
    return pd.read_sql_query(f"SELECT * FROM {table_name}", connection)


def _price_inputs(connection) -> pd.DataFrame:
    prices = _read_table(connection, "daily_prices")
    if prices.empty:
        return _empty_frame()

    prices = prices.copy()
    prices["ticker"] = prices["ticker"].astype(str).str.upper().str.strip()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices = prices.dropna(subset=["ticker", "date"]).sort_values(["ticker", "date"])
    for column in ["close", "adj_close", "volume"]:
        prices[column] = pd.to_numeric(prices.get(column), errors="coerce")
    prices["price"] = prices["adj_close"].where(prices["adj_close"].notna(), prices["close"])

    grouped = prices.groupby("ticker", group_keys=False)
    for days in [21, 63, 126, 252]:
        prices[f"price_{days}d_ago"] = grouped["price"].shift(days)
    prices["return_1m"] = prices["price"] / prices["price_21d_ago"] - 1.0
    prices["return_3m"] = prices["price"] / prices["price_63d_ago"] - 1.0
    prices["return_6m"] = prices["price"] / prices["price_126d_ago"] - 1.0
    prices["return_12m"] = prices["price"] / prices["price_252d_ago"] - 1.0
    prices["return_12_1m"] = prices["price_21d_ago"] / prices["price_252d_ago"] - 1.0
    prices["momentum_acceleration"] = (2.0 * prices["return_3m"]) - prices["return_6m"]
    prices["high_52w"] = grouped["price"].transform(lambda series: series.rolling(252, min_periods=1).max())
    prices["proximity_52w"] = prices["price"] / prices["high_52w"]
    prices["forward_return"] = grouped["price"].shift(-1) / prices["price"] - 1.0
    prices["date"] = prices["date"].dt.strftime("%Y-%m-%d")
    return prices


def _universe_inputs(connection) -> pd.DataFrame:
    universe = _read_table(connection, "universe")
    if universe.empty:
        return pd.DataFrame(columns=["ticker", "name", "gics_sector", "industry"])
    universe = universe.rename(columns={"sector": "gics_sector"}).copy()
    universe["ticker"] = universe["ticker"].astype(str).str.upper().str.strip()
    for column in ["name", "gics_sector", "industry"]:
        if column not in universe.columns:
            universe[column] = ""
    return universe[["ticker", "name", "gics_sector", "industry"]].drop_duplicates("ticker", keep="last")


def _latest_by_ticker(frame: pd.DataFrame, date_column: str) -> pd.DataFrame:
    if frame.empty or date_column not in frame.columns:
        return frame
    latest = frame.copy()
    latest[date_column] = pd.to_datetime(latest[date_column], errors="coerce")
    latest = latest.sort_values(["ticker", date_column]).drop_duplicates("ticker", keep="last")
    return latest.drop(columns=[date_column])


def _fundamental_inputs(connection) -> pd.DataFrame:
    ratios = _read_table(connection, "fundamental_ratios")
    if ratios.empty:
        return pd.DataFrame(columns=["ticker"])
    ratios["ticker"] = ratios["ticker"].astype(str).str.upper().str.strip()
    latest = _latest_by_ticker(ratios, "fiscal_date")
    aliases = {
        "revenue": "revenue_ttm",
        "net_income": "net_income_ttm",
        "free_cash_flow": "free_cash_flow_ttm",
        "operating_cash_flow": "cash_flow_from_operations_ttm",
    }
    for source, alias in aliases.items():
        if source in latest.columns and alias not in latest.columns:
            latest[alias] = latest[source]
    return latest


def _short_interest_inputs(connection) -> pd.DataFrame:
    snapshots = _read_table(connection, "short_interest_snapshots")
    if snapshots.empty:
        return pd.DataFrame(columns=["ticker"])
    snapshots["ticker"] = snapshots["ticker"].astype(str).str.upper().str.strip()
    snapshots = snapshots.sort_values(["ticker", "snapshot_date"])
    snapshots["short_interest_change"] = snapshots.groupby("ticker")["short_percent_float"].pct_change()
    latest = _latest_by_ticker(snapshots, "snapshot_date")
    if "short_ratio" in latest.columns and "days_to_cover" not in latest.columns:
        latest["days_to_cover"] = latest["short_ratio"]
    return latest


def _estimate_inputs(connection) -> pd.DataFrame:
    estimates = _read_table(connection, "analyst_estimates")
    if estimates.empty:
        return pd.DataFrame(columns=["ticker"])
    estimates["ticker"] = estimates["ticker"].astype(str).str.upper().str.strip()
    estimates = estimates.sort_values(["ticker", "snapshot_date"])
    grouped = estimates.groupby("ticker", group_keys=False)
    for days in [30, 60, 90]:
        estimates[f"eps_revision_{days}d"] = grouped["forward_eps"].pct_change(periods=max(1, days))
    return _latest_by_ticker(estimates, "snapshot_date")


def _insider_inputs(connection) -> pd.DataFrame:
    transactions = _read_table(connection, "insider_transactions")
    if transactions.empty:
        return pd.DataFrame(columns=["ticker"])
    transactions["ticker"] = transactions["ticker"].astype(str).str.upper().str.strip()
    transactions["dollar_value"] = pd.to_numeric(transactions["shares"], errors="coerce").fillna(0.0) * pd.to_numeric(
        transactions["price"], errors="coerce"
    ).fillna(0.0)
    buys = transactions["is_open_market_purchase"].astype(bool)
    sells = transactions["transaction_code"].astype(str).str.upper().eq("S")
    grouped = transactions.assign(
        insider_buy_value=transactions["dollar_value"].where(buys, 0.0),
        insider_sell_value=transactions["dollar_value"].where(sells, 0.0),
        ceo_cfo_open_market_purchases=transactions["dollar_value"].where(buys & transactions["is_ceo_cfo"].astype(bool), 0.0),
        insider_cluster_buys=transactions["cluster_buy"].astype(bool).astype(int),
    ).groupby("ticker", as_index=False)[
        ["insider_buy_value", "insider_sell_value", "ceo_cfo_open_market_purchases", "insider_cluster_buys"]
    ].sum()
    grouped["insider_net_buy_value"] = grouped["insider_buy_value"] - grouped["insider_sell_value"]
    return grouped


def _institutional_inputs(connection) -> pd.DataFrame:
    holdings = _read_table(connection, "institutional_holdings")
    if holdings.empty:
        return pd.DataFrame(columns=["ticker"])
    report_dates = sorted(str(value) for value in holdings["report_date"].dropna().unique())
    current = holdings[holdings["report_date"] == report_dates[-1]]
    previous = holdings[holdings["report_date"] == report_dates[-2]] if len(report_dates) > 1 else None
    return calculate_institutional_signals(current, previous)


def _merge_optional(base: pd.DataFrame, optional: pd.DataFrame) -> pd.DataFrame:
    if optional.empty or "ticker" not in optional.columns:
        return base
    return base.merge(optional, on="ticker", how="left", suffixes=("", "_duplicate"))


def build_factor_inputs_from_database(connection) -> pd.DataFrame:
    frame = _price_inputs(connection)
    if frame.empty:
        return frame

    for optional in [
        _universe_inputs(connection),
        _fundamental_inputs(connection),
        _short_interest_inputs(connection),
        _estimate_inputs(connection),
        _insider_inputs(connection),
        _institutional_inputs(connection),
    ]:
        frame = _merge_optional(frame, optional)

    if "gics_sector" not in frame.columns:
        frame["gics_sector"] = "Unknown"
    frame["gics_sector"] = frame["gics_sector"].fillna("Unknown").replace("", "Unknown")
    duplicate_columns = [column for column in frame.columns if column.endswith("_duplicate")]
    return frame.drop(columns=duplicate_columns).sort_values(["date", "ticker"]).reset_index(drop=True)


def export_factor_inputs(frame: pd.DataFrame, path: str | Path = "output/factor_inputs.csv") -> Path:
    output_path = ensure_project_path(path, PROJECT_ROOT)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
    return output_path
