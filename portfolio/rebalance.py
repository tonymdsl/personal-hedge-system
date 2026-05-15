"""Rebalance planning helpers for paper portfolios."""

from __future__ import annotations

from typing import Mapping

import pandas as pd

from .mvo_optimizer import optimize_mvo
from .optimizer import build_conviction_tilt_portfolio
from .transaction_costs import estimate_portfolio_transaction_cost


def generate_rebalance_orders(
    current_weights: Mapping[str, float] | pd.Series,
    target_portfolio: pd.DataFrame,
    *,
    nav: float = 1_000_000.0,
    min_trade_weight: float = 0.0,
) -> pd.DataFrame:
    """Return paper orders needed to move from current to target weights."""

    current_quantity_map: dict[str, float] = {}
    current_price_map: dict[str, float] = {}
    if isinstance(current_weights, pd.Series):
        raw_quantities = current_weights.attrs.get("quantities", {})
        if isinstance(raw_quantities, Mapping):
            current_quantity_map = {str(key).upper(): float(value) for key, value in raw_quantities.items()}
        raw_prices = current_weights.attrs.get("prices", {})
        if isinstance(raw_prices, Mapping):
            current_price_map = {str(key).upper(): float(value) for key, value in raw_prices.items()}
    current = pd.Series(current_weights, dtype="float64")
    if target_portfolio.empty:
        target = pd.Series(dtype="float64")
        metadata = pd.DataFrame()
    else:
        target_frame = target_portfolio.copy()
        target_frame["ticker"] = target_frame["ticker"].astype(str).str.upper()
        target_frame = target_frame.drop_duplicates(subset=["ticker"], keep="first")
        metadata = target_frame.set_index("ticker")
        target = metadata["weight"].astype("float64")
    tickers = sorted(set(current.index.astype(str)) | set(target.index.astype(str)))
    rows: list[dict[str, float | str | None]] = []
    for ticker in tickers:
        delta_weight = float(target.get(ticker, 0.0) - current.get(ticker, 0.0))
        if abs(delta_weight) < float(min_trade_weight):
            continue
        delta_notional = delta_weight * float(nav)
        row: dict[str, float | str | None] = {
            "ticker": ticker,
            "current_weight": float(current.get(ticker, 0.0)),
            "target_weight": float(target.get(ticker, 0.0)),
            "delta_weight": delta_weight,
            "delta_notional": delta_notional,
            "side": "buy" if delta_weight > 0 else "sell",
        }
        if ticker in current_quantity_map:
            row["current_quantity"] = current_quantity_map[ticker]
        if ticker in current_price_map and current_price_map[ticker] > 0:
            row["price"] = current_price_map[ticker]
        if ticker in metadata.index:
            meta = metadata.loc[ticker]
            for column in ["price", "sector", "quantity", "dollar_adv", "avg_dollar_volume", "beta", "earnings_date"]:
                if column in metadata.columns and pd.notna(meta.get(column)):
                    row[column] = meta.get(column)
            price = pd.to_numeric(pd.Series([row.get("price")]), errors="coerce").iloc[0] if "price" in row else pd.NA
            if pd.notna(price) and float(price) > 0:
                row["shares"] = abs(delta_notional) / float(price)
            elif "quantity" in row:
                row["shares"] = abs(float(row["quantity"]))
        elif "current_quantity" in row and "price" in row:
            row["shares"] = abs(float(row["current_quantity"]))
        rows.append(row)
    return pd.DataFrame(rows)


def plan_rebalance(
    candidates: pd.DataFrame,
    current_weights: Mapping[str, float] | pd.Series | None = None,
    *,
    method: str = "conviction_tilt",
    nav: float = 1_000_000.0,
    transaction_cost_bps: float = 10.0,
    **optimizer_kwargs: object,
) -> dict[str, object]:
    """Create a target portfolio, paper orders, and estimated costs."""

    if method == "mvo":
        target = optimize_mvo(candidates, transaction_cost_bps=transaction_cost_bps, **optimizer_kwargs)
    elif method == "conviction_tilt":
        target = build_conviction_tilt_portfolio(candidates, **optimizer_kwargs)
    else:
        raise ValueError("method must be 'conviction_tilt' or 'mvo'")
    current = current_weights if current_weights is not None else {}
    orders = generate_rebalance_orders(current, target, nav=nav)
    costs = estimate_portfolio_transaction_cost(
        pd.Series(current, dtype="float64"),
        target.set_index("ticker")["weight"] if not target.empty else pd.Series(dtype="float64"),
        nav=nav,
        slippage_bps=transaction_cost_bps,
    )
    return {"target_portfolio": target, "orders": orders, "transaction_costs": costs}
