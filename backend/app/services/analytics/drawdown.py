from __future__ import annotations

import pandas as pd


def drawdown_series(close: pd.Series) -> pd.Series:
    """Calculate drawdown from close prices."""
    prices = pd.to_numeric(close, errors="coerce").dropna()
    if prices.empty:
        return pd.Series(dtype=float)
    return prices / prices.cummax() - 1
