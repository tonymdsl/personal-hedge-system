from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.analytics.drawdown import drawdown_series


def daily_returns(prices: pd.DataFrame | pd.Series) -> pd.Series:
    """Calculate close-to-close daily returns."""
    if isinstance(prices, pd.DataFrame):
        close = prices["close"]
    else:
        close = prices
    return pd.to_numeric(close, errors="coerce").dropna().pct_change().dropna()


def calculate_metrics(prices: pd.DataFrame) -> dict:
    """Calculate core market metrics for a price history."""
    if prices.empty or "close" not in prices:
        return {
            "cumulative_return": 0.0,
            "annualized_volatility": 0.0,
            "current_drawdown": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "best_day": 0.0,
            "worst_day": 0.0,
        }
    ordered = prices.sort_values("date")
    close = pd.to_numeric(ordered["close"], errors="coerce").dropna()
    returns = daily_returns(close)
    dd = drawdown_series(close)
    annualized_vol = float(returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 else 0.0
    sharpe = 0.0
    if len(returns) > 1 and returns.std(ddof=1) != 0:
        sharpe = float(returns.mean() / returns.std(ddof=1) * np.sqrt(252))
    cumulative = float(close.iloc[-1] / close.iloc[0] - 1) if len(close) > 1 else 0.0
    return {
        "cumulative_return": round(cumulative, 6),
        "annualized_volatility": round(annualized_vol, 6),
        "current_drawdown": round(float(dd.iloc[-1]), 6) if not dd.empty else 0.0,
        "max_drawdown": round(float(dd.min()), 6) if not dd.empty else 0.0,
        "sharpe_ratio": round(sharpe, 6),
        "best_day": round(float(returns.max()), 6) if not returns.empty else 0.0,
        "worst_day": round(float(returns.min()), 6) if not returns.empty else 0.0,
    }


def performance_points(prices: pd.DataFrame) -> list[dict]:
    """Build cumulative return points for charts."""
    if prices.empty:
        return []
    ordered = prices.sort_values("date").copy()
    returns = ordered["close"].astype(float) / float(ordered["close"].iloc[0]) - 1
    return [
        {"date": pd.to_datetime(row.date).date().isoformat(), "value": round(float(value), 6)}
        for row, value in zip(ordered.itertuples(index=False), returns)
    ]
