"""Daily P&L attribution into beta, sector, factor, and alpha residual."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd


def pnl_attribution(positions: pd.DataFrame, returns: pd.DataFrame | pd.Series, *, beta_return: float = 0.0) -> dict[str, float]:
    if positions.empty:
        return {'total': 0.0, 'beta': 0.0, 'sector': 0.0, 'factor': 0.0, 'alpha': 0.0}
    ret = returns if isinstance(returns, pd.Series) else returns.iloc[-1]
    weights = pd.to_numeric(positions.set_index('ticker')['weight'], errors='coerce').fillna(0.0)
    aligned = pd.to_numeric(ret.reindex(weights.index), errors='coerce').fillna(0.0)
    total = float((weights * aligned).sum())
    beta = float(weights.sum() * beta_return)
    sector = 0.0
    factor = 0.0
    return {'total': total, 'beta': beta, 'sector': sector, 'factor': factor, 'alpha': total - beta - sector - factor}


def daily_pnl_attribution(
    positions: pd.DataFrame,
    returns: pd.DataFrame | pd.Series,
    *,
    attribution_date: str,
    spy_return: float = 0.0,
    sector_returns: Mapping[str, float] | pd.Series | None = None,
    factor_returns: Mapping[str, float] | pd.Series | None = None,
    output_path: str | Path = 'output/daily_attribution.csv',
) -> pd.DataFrame:
    if positions.empty:
        row = _attribution_row(attribution_date, 0.0, 0.0, 0.0, 0.0)
        return _persist(pd.DataFrame([row]), output_path)

    frame = positions.copy()
    frame['ticker'] = frame['ticker'].astype(str)
    frame['_weight'] = pd.to_numeric(frame.get('weight', 0), errors='coerce').fillna(0.0)
    frame['_beta'] = pd.to_numeric(frame.get('beta', 0), errors='coerce').fillna(0.0)
    ret = _returns_series(returns).reindex(frame['ticker']).fillna(0.0)
    total = float((frame['_weight'].to_numpy(dtype=float) * ret.to_numpy(dtype=float)).sum())
    beta_component = float((frame['_weight'] * frame['_beta']).sum() * float(spy_return))
    sector_component = _sector_component(frame, sector_returns or {}, float(spy_return))
    factor_component = _factor_component(frame, factor_returns or {})
    row = _attribution_row(attribution_date, total, beta_component, sector_component, factor_component)
    return _persist(pd.DataFrame([row]), output_path)


def _returns_series(returns: pd.DataFrame | pd.Series) -> pd.Series:
    if isinstance(returns, pd.Series):
        return pd.to_numeric(returns, errors='coerce')
    if {'ticker', 'return'}.issubset(returns.columns):
        return pd.to_numeric(returns.set_index(returns['ticker'].astype(str))['return'], errors='coerce')
    if returns.empty:
        return pd.Series(dtype=float)
    return pd.to_numeric(returns.iloc[-1], errors='coerce')


def _sector_component(frame: pd.DataFrame, sector_returns: Mapping[str, float] | pd.Series, spy_return: float) -> float:
    if 'sector' not in frame.columns:
        return 0.0
    sector_map = pd.Series(dict(sector_returns), dtype=float)
    values = frame['sector'].map(sector_map).fillna(spy_return).astype(float)
    return float((frame['_weight'] * (values - spy_return)).sum())


def _factor_component(frame: pd.DataFrame, factor_returns: Mapping[str, float] | pd.Series) -> float:
    total = 0.0
    for factor, value in dict(factor_returns).items():
        exposure_col = f'{factor}_exposure'
        score_col = f'{factor}_score'
        if exposure_col in frame.columns:
            exposure = pd.to_numeric(frame[exposure_col], errors='coerce').fillna(0.0)
        elif score_col in frame.columns:
            exposure = (pd.to_numeric(frame[score_col], errors='coerce').fillna(50.0) - 50.0) / 50.0
        else:
            continue
        total += float((frame['_weight'] * exposure).sum() * float(value))
    return total


def _attribution_row(date_value: str, total: float, beta: float, sector: float, factor: float) -> dict[str, float | str]:
    alpha = total - beta - sector - factor
    return {
        'date': date_value,
        'total_return': round(total, 10),
        'beta_return': round(beta, 10),
        'sector_return': round(sector, 10),
        'factor_return': round(factor, 10),
        'alpha_residual': round(alpha, 10),
    }


def _persist(frame: pd.DataFrame, output_path: str | Path) -> pd.DataFrame:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return frame
