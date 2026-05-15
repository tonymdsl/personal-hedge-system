"""Historical and synthetic stress scenarios."""
from __future__ import annotations

from typing import Mapping

import pandas as pd

SCENARIOS = {
    '2008_crisis': {'market': -0.35, 'short_squeeze': 0.05},
    '2020_covid_crash': {'market': -0.30, 'short_squeeze': 0.08},
    '2022_rate_hikes': {'market': -0.20, 'duration': -0.15},
    'sector_shock': {'market': -0.08, 'sector': -0.18},
    'momentum_reversal': {'momentum': -0.20},
    'short_squeeze': {'short_squeeze': 0.25},
}


def stress_test_portfolio(portfolio: pd.DataFrame, scenarios: Mapping[str, Mapping[str, float]] | None = None, *, nav: float = 1.0) -> pd.DataFrame:
    scenarios = scenarios or SCENARIOS
    rows = []
    if portfolio.empty:
        portfolio = pd.DataFrame(columns=['ticker', 'weight', 'beta', 'side'])
    weights = pd.to_numeric(portfolio.get('weight', pd.Series(dtype=float)), errors='coerce').fillna(0.0)
    beta = pd.to_numeric(portfolio.get('beta', pd.Series(1.0, index=portfolio.index)), errors='coerce').fillna(1.0)
    side = portfolio.get('side', pd.Series('', index=portfolio.index)).astype(str)
    long_mask = side.str.lower().eq('long') | ((side == '') & (weights > 0))
    short_mask = side.str.lower().eq('short') | ((side == '') & (weights < 0))
    for name, shocks in scenarios.items():
        market_pnl = float((weights * beta * float(shocks.get('market', 0.0))).sum())
        squeeze = float(abs(weights[short_mask]).sum() * -float(shocks.get('short_squeeze', 0.0)))
        factor = float(weights.sum() * (float(shocks.get('momentum', 0.0)) + float(shocks.get('sector', 0.0)) + float(shocks.get('duration', 0.0))))
        long_pnl = float((weights[long_mask] * beta[long_mask] * shocks.get('market', 0.0)).sum())
        short_pnl = float((weights[short_mask] * beta[short_mask] * shocks.get('market', 0.0)).sum()) + squeeze
        estimated_pnl = market_pnl + squeeze + factor
        rows.append(
            {
                'scenario': name,
                'long_pnl': long_pnl,
                'short_pnl': short_pnl,
                'estimated_pnl': estimated_pnl,
                'long_pnl_usd': long_pnl * float(nav),
                'short_pnl_usd': short_pnl * float(nav),
                'estimated_pnl_usd': estimated_pnl * float(nav),
                'estimated_pnl_pct': estimated_pnl,
            }
        )
    return pd.DataFrame(rows)
