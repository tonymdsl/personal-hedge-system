"""Beta estimation helpers."""

from __future__ import annotations

from typing import Mapping

import pandas as pd


def returns_from_prices(prices: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """Convert price levels to simple returns."""

    return prices.sort_index().pct_change().dropna(how="all")


def calculate_beta(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    min_periods: int = 2,
) -> float:
    """Calculate CAPM beta as Cov(asset, benchmark) / Var(benchmark)."""

    aligned = pd.concat(
        [pd.to_numeric(asset_returns, errors="coerce"), pd.to_numeric(benchmark_returns, errors="coerce")],
        axis=1,
        join="inner",
    ).dropna()
    if len(aligned) < int(min_periods):
        raise ValueError("Not enough overlapping observations to calculate beta")
    benchmark = aligned.iloc[:, 1]
    variance = float(benchmark.var())
    if abs(variance) < 1e-18:
        raise ValueError("Benchmark variance is zero; beta is undefined")
    covariance = float(aligned.iloc[:, 0].cov(benchmark))
    return covariance / variance


def calculate_betas(
    asset_returns: pd.DataFrame,
    benchmark_returns: pd.Series,
    *,
    min_periods: int = 20,
) -> pd.Series:
    """Calculate beta for each asset-return column."""

    betas: dict[str, float] = {}
    for column in asset_returns.columns:
        try:
            betas[str(column)] = calculate_beta(asset_returns[column], benchmark_returns, min_periods=min_periods)
        except ValueError:
            betas[str(column)] = float("nan")
    return pd.Series(betas, dtype="float64")


def calculate_portfolio_beta(weights: Mapping[str, float] | pd.Series, betas: Mapping[str, float] | pd.Series) -> float:
    """Calculate signed portfolio beta exposure."""

    weights_series = pd.Series(weights, dtype="float64")
    beta_series = pd.Series(betas, dtype="float64")
    aligned = pd.concat([weights_series, beta_series], axis=1, join="inner").dropna()
    if aligned.empty:
        return 0.0
    return float((aligned.iloc[:, 0] * aligned.iloc[:, 1]).sum())
