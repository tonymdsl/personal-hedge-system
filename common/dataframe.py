"""Small dataframe helpers used by future layers."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


def require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    """Raise a clear error if required columns are missing."""

    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")


def normalize_tickers(series: pd.Series) -> pd.Series:
    """Normalize ticker symbols for joins and cache keys."""

    return series.astype("string").str.strip().str.upper().str.replace(".", "-", regex=False)


def safe_percentile_rank(
    series: pd.Series,
    *,
    ascending: bool = True,
    neutral_score: float = 50.0,
) -> pd.Series:
    """Return 0-100 percentile ranks, using a neutral score when data is absent."""

    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() == 0:
        return pd.Series(neutral_score, index=series.index, dtype="float64")
    ranked = numeric.rank(pct=True, ascending=ascending) * 100.0
    return ranked.fillna(neutral_score)


def winsorize_series(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """Clip a numeric series to quantile bounds."""

    if not 0 <= lower <= upper <= 1:
        raise ValueError("Expected 0 <= lower <= upper <= 1")
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.dropna().empty:
        return numeric
    low_value, high_value = np.nanquantile(numeric, [lower, upper])
    return numeric.clip(lower=low_value, upper=high_value)
