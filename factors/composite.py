"""Composite factor blending and long/short candidate flags."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd

from common.config import PROJECT_ROOT, ensure_project_path
from .common import NEUTRAL_SCORE, SECTOR_COL, sector_percentile_rank
from .regime_weights import DEFAULT_WEIGHTS, apply_regime_weights, normalize_weights

FACTOR_SCORE_COLUMNS = {
    'momentum': 'momentum_score',
    'quality': 'quality_score',
    'value': 'value_score',
    'revisions': 'revisions_score',
    'insider': 'insider_score',
    'growth': 'growth_score',
    'short_interest': 'short_interest_score',
    'institutional': 'institutional_score',
}


def blend_factor_scores(frame: pd.DataFrame, *, weights: Mapping[str, float] | None = None, neutral_score: float = NEUTRAL_SCORE) -> pd.Series:
    weights = normalize_weights(weights or DEFAULT_WEIGHTS)
    total = pd.Series(0.0, index=frame.index, dtype='float64')
    for factor, weight in weights.items():
        col = FACTOR_SCORE_COLUMNS.get(factor, f'{factor}_score')
        values = pd.to_numeric(frame[col], errors='coerce').fillna(neutral_score) if col in frame.columns else pd.Series(neutral_score, index=frame.index)
        total = total + float(weight) * values
    return total.clip(0, 100)


def score_composite(
    frame: pd.DataFrame,
    *,
    weights: Mapping[str, float] | None = None,
    vix: float | None = None,
    config: Mapping[str, object] | None = None,
    sector_col: str = SECTOR_COL,
    long_quantile: float = 0.80,
    short_quantile: float = 0.20,
    neutral_score: float = NEUTRAL_SCORE,
) -> pd.DataFrame:
    df = frame.copy()
    active_weights = apply_regime_weights(weights, vix=vix, config=config)
    df['composite_raw'] = blend_factor_scores(df, weights=active_weights, neutral_score=neutral_score)
    df['composite_score'] = sector_percentile_rank(df, df['composite_raw'], sector_col=sector_col, higher_is_better=True, neutral_score=neutral_score)
    df['long_candidate'] = False
    df['short_candidate'] = False
    if sector_col not in df.columns:
        df[sector_col] = 'Unknown'
    group_columns: str | list[str] = sector_col
    if 'date' in df.columns:
        group_columns = ['date', sector_col]
    for _, idx in df.groupby(group_columns, dropna=False).groups.items():
        scores = pd.to_numeric(df.loc[idx, 'composite_score'], errors='coerce')
        if scores.notna().sum() < 2:
            continue
        long_cut = scores.quantile(long_quantile)
        short_cut = scores.quantile(short_quantile)
        df.loc[idx, 'long_candidate'] = scores >= long_cut
        df.loc[idx, 'short_candidate'] = scores <= short_cut
    df.attrs['factor_weights'] = active_weights
    return df


def export_scored_universe(frame: pd.DataFrame, path: str | Path = 'output/scored_universe_latest.csv') -> Path:
    output_path = ensure_project_path(path, PROJECT_ROOT)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
    return output_path


def top_longs_shorts(frame: pd.DataFrame, n: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    score_col = 'composite_score' if 'composite_score' in frame.columns else 'composite_raw'
    long_mask = frame['long_candidate'] if 'long_candidate' in frame.columns else pd.Series(False, index=frame.index)
    short_mask = frame['short_candidate'] if 'short_candidate' in frame.columns else pd.Series(False, index=frame.index)
    longs = frame[long_mask == True].sort_values(score_col, ascending=False).head(n)
    shorts = frame[short_mask == True].sort_values(score_col, ascending=True).head(n)
    return longs, shorts
