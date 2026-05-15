"""Shared helpers for Layer 2 factor scoring.

The scoring layer intentionally operates on pandas DataFrames supplied by the
local data layer/tests.  It does not fetch market data or write outside the
project.  Factor metrics are ranked 0-100 within GICS sector; when a sector has
insufficient valid observations, or a row has no usable data for a factor, the
neutral score of 50 is used.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from common.dataframe import normalize_tickers

TICKER_COL = "ticker"
SECTOR_COL = "gics_sector"
NEUTRAL_SCORE = 50.0
MIN_SECTOR_COUNT = 2
SCORE_MIN = 0.0
SCORE_MAX = 100.0


@dataclass(frozen=True)
class MetricSpec:
    """Definition of a raw metric and how it should be scored.

    ``higher_is_better=False`` creates an inverted percentile score, useful for
    valuation multiples, leverage, short interest, accruals, etc.
    """

    name: str
    aliases: tuple[str, ...] = ()
    higher_is_better: bool = True
    compute: Callable[[pd.DataFrame], pd.Series] | None = None
    raw_column: str | None = None
    score_column: str | None = None


def ensure_scoring_columns(
    frame: pd.DataFrame,
    *,
    ticker_col: str = TICKER_COL,
    sector_col: str = SECTOR_COL,
) -> pd.DataFrame:
    """Return a copy with normalized ticker and GICS sector columns.

    Accepted sector aliases are copied into ``gics_sector``.  Missing/blank
    sectors are grouped under ``Unknown`` so percentile ranking remains stable.
    """

    df = frame.copy()

    if ticker_col in df.columns:
        df[ticker_col] = normalize_tickers(df[ticker_col])

    if sector_col not in df.columns:
        for alias in ("sector", "gicsSector", "gics_sector_name", "GICS Sector"):
            if alias in df.columns:
                df[sector_col] = df[alias]
                break

    if sector_col not in df.columns:
        df[sector_col] = "Unknown"

    df[sector_col] = (
        df[sector_col]
        .astype("string")
        .fillna("Unknown")
        .str.strip()
        .replace("", "Unknown")
    )
    return df


def to_numeric_series(value: Any, index: pd.Index | None = None) -> pd.Series:
    """Coerce a scalar/array/Series into a float Series."""

    if isinstance(value, pd.Series):
        series = value.copy()
        if index is not None:
            series = series.reindex(index)
    else:
        series = pd.Series(value, index=index)
    return pd.to_numeric(series, errors="coerce").astype("float64")


def coalesce_series(*series: pd.Series | None, index: pd.Index | None = None) -> pd.Series:
    """Return first non-null value across Series, preserving index."""

    resolved_index = index
    for item in series:
        if item is not None:
            resolved_index = item.index if resolved_index is None else resolved_index
            break
    if resolved_index is None:
        resolved_index = pd.RangeIndex(0)

    output = pd.Series(np.nan, index=resolved_index, dtype="float64")
    for item in series:
        if item is None:
            continue
        numeric = to_numeric_series(item, index=resolved_index)
        output = output.where(output.notna(), numeric)
    return output


def safe_divide(numerator: Any, denominator: Any, *, index: pd.Index | None = None) -> pd.Series:
    """Vectorized division that returns NaN for zero/missing denominators."""

    num = to_numeric_series(numerator, index=index)
    den = to_numeric_series(denominator, index=num.index if index is None else index)
    den = den.replace(0, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = num / den
    return result.replace([np.inf, -np.inf], np.nan).astype("float64")


def column_or_nan(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric column or an all-NaN Series when absent."""

    if column in frame.columns:
        return to_numeric_series(frame[column], index=frame.index)
    return pd.Series(np.nan, index=frame.index, dtype="float64")


def metric_series(
    frame: pd.DataFrame,
    aliases: Sequence[str] = (),
    compute: Callable[[pd.DataFrame], pd.Series] | None = None,
) -> pd.Series:
    """Resolve a raw metric from aliases and an optional compute fallback."""

    resolved = pd.Series(np.nan, index=frame.index, dtype="float64")
    for alias in aliases:
        if alias in frame.columns:
            values = to_numeric_series(frame[alias], index=frame.index)
            resolved = resolved.where(resolved.notna(), values)

    if compute is not None:
        computed = to_numeric_series(compute(frame), index=frame.index)
        resolved = resolved.where(resolved.notna(), computed)

    return resolved.replace([np.inf, -np.inf], np.nan).astype("float64")


def sector_percentile_rank(
    frame: pd.DataFrame,
    value_col: str | pd.Series,
    *,
    sector_col: str = SECTOR_COL,
    higher_is_better: bool = True,
    min_count: int = MIN_SECTOR_COUNT,
    neutral_score: float = NEUTRAL_SCORE,
) -> pd.Series:
    """Rank a metric 0-100 within GICS sector.

    The best valid observation in a sufficiently populated sector receives 100,
    the worst receives 0, and ties receive the average percentile.  Sectors with
    fewer than ``min_count`` valid observations and individual missing values are
    assigned ``neutral_score``.
    """

    df = ensure_scoring_columns(frame, sector_col=sector_col)
    if isinstance(value_col, pd.Series):
        values = to_numeric_series(value_col, index=df.index)
    else:
        if value_col not in df.columns:
            return pd.Series(neutral_score, index=df.index, dtype="float64")
        values = to_numeric_series(df[value_col], index=df.index)

    result = pd.Series(neutral_score, index=df.index, dtype="float64")
    min_count = max(int(min_count), 1)

    group_columns: str | list[str] = sector_col
    if "date" in df.columns:
        group_columns = ["date", sector_col]

    for _, group_index in df.groupby(group_columns, dropna=False).groups.items():
        group_values = values.loc[group_index]
        valid = group_values.dropna()
        if len(valid) < min_count:
            continue

        if len(valid) == 1:
            scaled = pd.Series(neutral_score, index=valid.index, dtype="float64")
        else:
            ranks = valid.rank(method="average", ascending=higher_is_better)
            scaled = ((ranks - 1.0) / (len(valid) - 1.0)) * 100.0

        result.loc[valid.index] = scaled.clip(SCORE_MIN, SCORE_MAX)

    return result.astype("float64")


def combine_score_columns(
    frame: pd.DataFrame,
    score_columns: Iterable[str],
    *,
    neutral_score: float = NEUTRAL_SCORE,
) -> pd.Series:
    """Equal-weight average of score columns, defaulting missing values to neutral."""

    columns = list(score_columns)
    if not columns:
        return pd.Series(neutral_score, index=frame.index, dtype="float64")

    scores = pd.DataFrame(index=frame.index)
    for column in columns:
        if column in frame.columns:
            scores[column] = to_numeric_series(frame[column], index=frame.index).fillna(neutral_score)
        else:
            scores[column] = neutral_score
    return scores.mean(axis=1).clip(SCORE_MIN, SCORE_MAX).astype("float64")


def score_factor(
    frame: pd.DataFrame,
    *,
    prefix: str,
    metrics: Sequence[MetricSpec],
    sector_col: str = SECTOR_COL,
    min_count: int = MIN_SECTOR_COUNT,
    neutral_score: float = NEUTRAL_SCORE,
    rerank_aggregate: bool = True,
) -> pd.DataFrame:
    """Score a factor from multiple metric specifications."""

    df = ensure_scoring_columns(frame, sector_col=sector_col)
    raw_columns: list[str] = []
    score_columns: list[str] = []

    for spec in metrics:
        raw_col = spec.raw_column or f"{prefix}_{spec.name}"
        score_col = spec.score_column or f"{prefix}_{spec.name}_score"
        df[raw_col] = metric_series(df, spec.aliases, spec.compute)
        df[score_col] = sector_percentile_rank(
            df,
            raw_col,
            sector_col=sector_col,
            higher_is_better=spec.higher_is_better,
            min_count=min_count,
            neutral_score=neutral_score,
        )
        raw_columns.append(raw_col)
        score_columns.append(score_col)

    valid_metric_count_col = f"{prefix}_valid_metrics"
    raw_score_col = f"{prefix}_raw_score"
    factor_score_col = f"{prefix}_score"

    if raw_columns:
        df[valid_metric_count_col] = df[raw_columns].notna().sum(axis=1).astype("int64")
    else:
        df[valid_metric_count_col] = 0

    df[raw_score_col] = combine_score_columns(df, score_columns, neutral_score=neutral_score)
    if rerank_aggregate:
        df[factor_score_col] = sector_percentile_rank(
            df,
            raw_score_col,
            sector_col=sector_col,
            higher_is_better=True,
            min_count=min_count,
            neutral_score=neutral_score,
        )
    else:
        df[factor_score_col] = df[raw_score_col]

    # Rows with no usable raw data for the factor should remain neutral even if
    # other names in the sector have high/low scores.
    df.loc[df[valid_metric_count_col] == 0, factor_score_col] = neutral_score
    return df


def weighted_score_sum(
    frame: pd.DataFrame,
    weights: Mapping[str, float],
    *,
    neutral_score: float = NEUTRAL_SCORE,
) -> pd.Series:
    """Weighted sum of ``<factor>_score`` columns using neutral for missing factors."""

    if not weights:
        return pd.Series(neutral_score, index=frame.index, dtype="float64")

    total = pd.Series(0.0, index=frame.index, dtype="float64")
    weight_sum = 0.0
    for factor, weight in weights.items():
        numeric_weight = float(weight)
        if numeric_weight <= 0:
            continue
        column = f"{factor}_score"
        if column in frame.columns:
            values = to_numeric_series(frame[column], index=frame.index).fillna(neutral_score)
        else:
            values = pd.Series(neutral_score, index=frame.index, dtype="float64")
        total = total + values * numeric_weight
        weight_sum += numeric_weight

    if weight_sum <= 0:
        return pd.Series(neutral_score, index=frame.index, dtype="float64")
    return (total / weight_sum).clip(SCORE_MIN, SCORE_MAX).astype("float64")


def quintile_from_score(score: pd.Series | Any) -> pd.Series:
    """Map 0-100 scores into bottom(1) through top(5) quintiles."""

    values = to_numeric_series(score).fillna(NEUTRAL_SCORE).clip(SCORE_MIN, SCORE_MAX)
    quintile = pd.Series(3, index=values.index, dtype="int64")
    quintile.loc[values <= 20.0] = 1
    quintile.loc[(values > 20.0) & (values < 40.0)] = 2
    quintile.loc[(values >= 40.0) & (values < 60.0)] = 3
    quintile.loc[(values >= 60.0) & (values < 80.0)] = 4
    quintile.loc[values >= 80.0] = 5
    return quintile


def score_frame_to_records(frame: pd.DataFrame, columns: Sequence[str], limit: int | None = None) -> list[dict[str, Any]]:
    """Convert selected DataFrame columns into JSON-friendly records."""

    selected = frame.loc[:, [column for column in columns if column in frame.columns]].copy()
    if limit is not None:
        selected = selected.head(limit)
    selected = selected.replace({np.nan: None})
    return selected.to_dict(orient="records")
