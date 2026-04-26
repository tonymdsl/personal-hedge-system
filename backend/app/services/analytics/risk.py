from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.analytics.drawdown import drawdown_series
from app.services.analytics.metrics import daily_returns


def _asset_returns(price_map: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return aligned daily returns by asset."""
    series = {}
    for symbol, prices in price_map.items():
        if prices.empty:
            continue
        ordered = prices.sort_values("date")
        returns = daily_returns(ordered.set_index(pd.to_datetime(ordered["date"]))["close"])
        if not returns.empty:
            series[symbol] = returns
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).dropna(how="all").fillna(0.0)


def calculate_portfolio_risk(price_map: dict[str, pd.DataFrame]) -> dict:
    """Calculate equal-weight portfolio risk metrics."""
    returns = _asset_returns(price_map)
    symbols = list(returns.columns)
    if returns.empty or not symbols:
        return {
            "assumption": "equal_weight_no_real_positions",
            "max_asset_weight": 0.0,
            "total_exposure": 0.0,
            "current_drawdown": 0.0,
            "portfolio_volatility": 0.0,
            "portfolio_max_drawdown": 0.0,
            "current_portfolio_drawdown": 0.0,
            "risk_contribution": [],
            "concentration_alerts": [],
            "drawdown_alerts": [],
            "alerts": [],
        }

    weight = 1 / len(symbols)
    weights = pd.Series({symbol: weight for symbol in symbols})
    portfolio_returns = returns.mul(weights, axis=1).sum(axis=1)
    equity = (1 + portfolio_returns).cumprod()
    dd = drawdown_series(equity)
    volatility = float(portfolio_returns.std(ddof=1) * np.sqrt(252)) if len(portfolio_returns) > 1 else 0.0

    weighted_vol = returns.std(ddof=1).fillna(0.0) * weights
    denominator = float(weighted_vol.sum())
    contributions = []
    for symbol in symbols:
        contribution = float(weighted_vol[symbol] / denominator) if denominator else weight
        contributions.append(
            {
                "symbol": symbol,
                "weight": round(float(weights[symbol]), 6),
                "volatility": round(float(returns[symbol].std(ddof=1) * np.sqrt(252)), 6),
                "contribution": round(contribution, 6),
            }
        )

    concentration_alerts = []
    if weight > 0.35:
        concentration_alerts.append(f"Equal-weight asset weight {weight:.1%} exceeds 35% concentration limit.")
    drawdown_alerts = []
    current_drawdown = float(dd.iloc[-1]) if not dd.empty else 0.0
    max_drawdown = float(dd.min()) if not dd.empty else 0.0
    if current_drawdown <= -0.10:
        drawdown_alerts.append(f"Current portfolio drawdown {current_drawdown:.1%} breaches -10% alert level.")
    if max_drawdown <= -0.15:
        drawdown_alerts.append(f"Portfolio max drawdown {max_drawdown:.1%} breaches -15% alert level.")

    return {
        "assumption": "equal_weight_no_real_positions",
        "max_asset_weight": round(float(weight), 6),
        "total_exposure": 1.0,
        "current_drawdown": round(current_drawdown, 6),
        "portfolio_volatility": round(volatility, 6),
        "portfolio_max_drawdown": round(max_drawdown, 6),
        "current_portfolio_drawdown": round(current_drawdown, 6),
        "risk_contribution": contributions,
        "concentration_alerts": concentration_alerts,
        "drawdown_alerts": drawdown_alerts,
        "alerts": concentration_alerts + drawdown_alerts,
    }
