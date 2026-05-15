"""Monitor long/short factor spread z-scores."""
from __future__ import annotations

import pandas as pd


def factor_spread_alerts(spreads: pd.Series | pd.DataFrame, *, threshold: float = 1.5, crowding_warnings: list[dict] | None = None) -> list[dict[str, object]]:
    series = spreads if isinstance(spreads, pd.Series) else spreads.iloc[-1]
    alerts = []
    crowded = {w.get('factor') for w in (crowding_warnings or [])}
    for factor, value in pd.to_numeric(series, errors='coerce').dropna().items():
        if abs(float(value)) >= threshold:
            is_crowded = factor in crowded
            alerts.append({'factor': factor, 'zscore': float(value), 'crowded': is_crowded, 'priority': 'HIGH' if is_crowded else 'NORMAL'})
    return alerts
