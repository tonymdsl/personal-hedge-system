"""Factor and sector exposure calculations."""

from __future__ import annotations

from typing import Mapping

import pandas as pd


def calculate_factor_exposure(
    weights: Mapping[str, float] | pd.Series,
    exposures: pd.DataFrame,
) -> pd.Series:
    """Return weighted exposure to each factor column."""

    weight_series = pd.Series(weights, dtype="float64")
    aligned = exposures.copy()
    aligned.index = aligned.index.astype(str)
    joined = aligned.join(weight_series.rename("weight"), how="inner")
    if joined.empty:
        return pd.Series(dtype="float64")
    factors = joined.drop(columns=["weight"]).apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return factors.mul(joined["weight"], axis=0).sum(axis=0)


def sector_exposure(
    portfolio: pd.DataFrame,
    *,
    sector_col: str = "sector",
    weight_col: str = "weight",
) -> pd.DataFrame:
    """Return gross and net exposure by sector."""

    if portfolio.empty:
        return pd.DataFrame(columns=[sector_col, "gross_exposure", "net_exposure"])
    if sector_col not in portfolio.columns or weight_col not in portfolio.columns:
        raise ValueError(f"portfolio must contain {sector_col!r} and {weight_col!r}")
    grouped = portfolio.assign(abs_weight=portfolio[weight_col].abs()).groupby(sector_col, dropna=False)
    return grouped.agg(gross_exposure=("abs_weight", "sum"), net_exposure=(weight_col, "sum")).reset_index()


def factor_exposure_warnings(
    current_exposure: Mapping[str, float] | pd.Series,
    historical_exposures: pd.DataFrame,
    *,
    std_threshold: float = 1.0,
) -> list[dict[str, object]]:
    """Flag factor spreads more than N historical standard deviations away."""

    if historical_exposures.empty:
        return []
    current = pd.Series(current_exposure, dtype="float64")
    warnings: list[dict[str, object]] = []
    for factor, value in current.items():
        if factor not in historical_exposures.columns:
            continue
        history = pd.to_numeric(historical_exposures[factor], errors="coerce").dropna()
        if len(history) < 2:
            continue
        mean = float(history.mean())
        std = float(history.std(ddof=0))
        if std <= 0:
            continue
        zscore = (float(value) - mean) / std
        if abs(zscore) > float(std_threshold):
            warnings.append(
                {
                    "warning": "factor_exposure_deviation",
                    "factor": str(factor),
                    "value": float(value),
                    "historical_mean": mean,
                    "historical_std": std,
                    "zscore": float(zscore),
                    "threshold": float(std_threshold),
                }
            )
    return warnings
