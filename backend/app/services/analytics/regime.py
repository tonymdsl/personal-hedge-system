from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.services.analytics.drawdown import drawdown_series
from app.services.analytics.metrics import daily_returns


def _evidence_for(prices: pd.DataFrame) -> dict:
    """Calculate regime evidence for one asset."""
    ordered = prices.sort_values("date")
    close = pd.to_numeric(ordered["close"], errors="coerce").dropna()
    if len(close) < 2:
        return {
            "above_200dma": False,
            "realized_volatility": 0.0,
            "current_drawdown": 0.0,
            "last_close": 0.0,
            "moving_average_200": 0.0,
        }
    moving_average = close.rolling(200, min_periods=min(50, len(close))).mean()
    returns = daily_returns(close).tail(30)
    realized_vol = float(returns.std(ddof=1) * (252**0.5)) if len(returns) > 1 else 0.0
    current_drawdown = float(drawdown_series(close).iloc[-1])
    return {
        "above_200dma": bool(close.iloc[-1] > moving_average.iloc[-1]),
        "realized_volatility": round(realized_vol, 6),
        "current_drawdown": round(current_drawdown, 6),
        "last_close": round(float(close.iloc[-1]), 4),
        "moving_average_200": round(float(moving_average.iloc[-1]), 4),
    }


def classify_market_regime(price_map: dict[str, pd.DataFrame]) -> dict:
    """Classify the current market regime using SPY and QQQ."""
    thresholds = {
        "max_volatility_for_risk_on": 0.25,
        "stress_drawdown": -0.10,
        "risk_off_drawdown": -0.07,
        "stress_volatility": 0.35,
    }
    spy = _evidence_for(price_map.get("SPY", pd.DataFrame()))
    qqq = _evidence_for(price_map.get("QQQ", pd.DataFrame()))
    vol = max(spy["realized_volatility"], qqq["realized_volatility"])
    drawdown = min(spy["current_drawdown"], qqq["current_drawdown"])
    both_above = spy["above_200dma"] and qqq["above_200dma"]
    evidence = {
        "spy_above_200dma": spy["above_200dma"],
        "qqq_above_200dma": qqq["above_200dma"],
        "realized_volatility_below_threshold": vol < thresholds["max_volatility_for_risk_on"],
        "drawdown_above_stress_threshold": drawdown > thresholds["stress_drawdown"],
    }
    values = {
        "spy_price": spy["last_close"],
        "spy_200dma": spy["moving_average_200"],
        "qqq_price": qqq["last_close"],
        "qqq_200dma": qqq["moving_average_200"],
        "realized_volatility": round(vol, 6),
        "current_drawdown": round(drawdown, 6),
    }
    if drawdown <= thresholds["stress_drawdown"] or vol >= thresholds["stress_volatility"]:
        regime = "market_stress"
        confidence = 0.9
    elif not spy["above_200dma"] or drawdown <= thresholds["risk_off_drawdown"] or vol >= thresholds["max_volatility_for_risk_on"]:
        regime = "risk_off"
        confidence = 0.75
    elif both_above and vol < thresholds["max_volatility_for_risk_on"]:
        regime = "risk_on"
        confidence = 0.8
    else:
        regime = "risk_off"
        confidence = 0.6
    return {
        "regime": regime,
        "confidence": confidence,
        "evidence": evidence,
        "thresholds": thresholds,
        "values": values,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
