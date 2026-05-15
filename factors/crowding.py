"""Factor crowding diagnostics from top-minus-bottom factor spreads."""
from __future__ import annotations

from itertools import combinations
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_CORRELATION_BASELINES: dict[tuple[str, str], float] = {
    ("momentum", "value"): -0.30,
    ("momentum", "quality"): 0.10,
}


def _factor_name(column: str) -> str:
    name = str(column).strip().lower()
    if name.endswith("_score"):
        name = name[:-6]
    return name


def _pair_key(factor_a: str, factor_b: str) -> tuple[str, str]:
    return tuple(sorted((_factor_name(factor_a), _factor_name(factor_b))))


def factor_return_spreads(frame: pd.DataFrame, factor_columns: Iterable[str], *, return_col: str = 'forward_return', quantile: float = 0.20) -> pd.DataFrame:
    rows = []
    for factor in factor_columns:
        if factor not in frame.columns or return_col not in frame.columns:
            continue
        sub = frame[[factor, return_col]].dropna()
        if len(sub) < 5:
            continue
        lo = sub[factor].quantile(quantile)
        hi = sub[factor].quantile(1 - quantile)
        top = sub[sub[factor] >= hi][return_col].mean()
        bottom = sub[sub[factor] <= lo][return_col].mean()
        rows.append({
            'factor': _factor_name(factor),
            'factor_column': factor,
            'top_return': top,
            'bottom_return': bottom,
            'spread': top - bottom,
            'long_count': int((sub[factor] >= hi).sum()),
            'short_count': int((sub[factor] <= lo).sum()),
        })
    return pd.DataFrame(rows)


def daily_factor_return_spreads(
    frame: pd.DataFrame,
    factor_columns: Iterable[str],
    *,
    date_col: str = 'date',
    return_col: str = 'forward_return',
    quantile: float = 0.20,
) -> pd.DataFrame:
    if date_col not in frame.columns:
        return factor_return_spreads(frame, factor_columns, return_col=return_col, quantile=quantile)

    rows = []
    for date, group in frame.groupby(date_col, sort=True):
        day_spreads = factor_return_spreads(group, factor_columns, return_col=return_col, quantile=quantile)
        if day_spreads.empty:
            continue
        day_spreads.insert(0, 'date', date)
        rows.append(day_spreads)
    if not rows:
        return pd.DataFrame(columns=['date', 'factor', 'factor_column', 'top_return', 'bottom_return', 'spread', 'long_count', 'short_count'])
    return pd.concat(rows, ignore_index=True)


def rolling_factor_correlations(history: pd.DataFrame, *, window: int = 60, min_periods: int | None = None) -> pd.DataFrame:
    if 'date' in history.columns:
        pivot = history.pivot_table(index='date', columns='factor', values='spread')
    else:
        pivot = history.copy()
    if pivot.empty or len(pivot.columns) < 2:
        return pd.DataFrame(columns=['date', 'factor_a', 'factor_b', 'correlation', 'observations'])

    min_obs = min_periods if min_periods is not None else min(window, 20)
    rows = []
    pivot = pivot.sort_index()
    for idx in range(len(pivot)):
        start = max(0, idx - window + 1)
        window_frame = pivot.iloc[start:idx + 1]
        date = pivot.index[idx]
        for factor_a, factor_b in combinations(pivot.columns, 2):
            pair = window_frame[[factor_a, factor_b]].dropna()
            if len(pair) < min_obs:
                continue
            correlation = pair[factor_a].corr(pair[factor_b])
            if pd.isna(correlation):
                continue
            rows.append({
                'date': date,
                'factor_a': _factor_name(factor_a),
                'factor_b': _factor_name(factor_b),
                'correlation': float(correlation),
                'observations': int(len(pair)),
            })
    return pd.DataFrame(rows)


def correlation_crowding_warnings(
    correlations: pd.DataFrame,
    *,
    baselines: dict[tuple[str, str], float] | None = None,
    deviation_threshold: float = 0.40,
) -> list[dict[str, object]]:
    if correlations.empty:
        return []

    baseline_map = baselines or DEFAULT_CORRELATION_BASELINES
    warnings: list[dict[str, object]] = []
    for row in correlations.to_dict(orient='records'):
        factor_a = _factor_name(str(row.get('factor_a', '')))
        factor_b = _factor_name(str(row.get('factor_b', '')))
        baseline = baseline_map.get(_pair_key(factor_a, factor_b), 0.0)
        correlation = float(row['correlation'])
        deviation = correlation - baseline
        if abs(deviation) <= deviation_threshold:
            continue
        warnings.append({
            'warning': 'factor_correlation_deviation',
            'date': row.get('date'),
            'factor_a': factor_a,
            'factor_b': factor_b,
            'correlation': correlation,
            'baseline': float(baseline),
            'deviation': float(deviation),
            'threshold': float(deviation_threshold),
            'observations': int(row.get('observations', 0)),
        })
    return warnings


def detect_crowding(
    spread_history: pd.DataFrame,
    *,
    zscore_threshold: float = 2.0,
    window: int = 60,
    min_periods: int | None = None,
    baselines: dict[tuple[str, str], float] | None = None,
    deviation_threshold: float = 0.40,
) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []
    if spread_history.empty:
        return warnings
    if {'factor', 'spread'}.issubset(spread_history.columns):
        grouped = spread_history.groupby('factor')
        for factor, rows in grouped:
            values = pd.to_numeric(rows['spread'], errors='coerce').dropna()
            if len(values) < 3:
                continue
            std = values.std(ddof=0)
            if std == 0 or np.isnan(std):
                continue
            z = (values.iloc[-1] - values.mean()) / std
            if abs(z) >= zscore_threshold:
                warnings.append({'factor': factor, 'zscore': float(z), 'warning': 'factor_spread_deviation'})
    if {'date', 'factor', 'spread'}.issubset(spread_history.columns):
        correlations = rolling_factor_correlations(spread_history, window=window, min_periods=min_periods)
        warnings.extend(correlation_crowding_warnings(correlations, baselines=baselines, deviation_threshold=deviation_threshold))
    return warnings
