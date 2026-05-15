"""Optional mean-variance optimizer with scipy fallback."""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from .optimizer import build_conviction_tilt_portfolio


def score_to_expected_return(score: float, *, min_return: float = -0.15, max_return: float = 0.15) -> float:
    """Map score 0-100 linearly to annual expected return."""

    clipped = min(100.0, max(0.0, float(score)))
    return float(min_return) + (clipped / 100.0) * (float(max_return) - float(min_return))


def expected_returns_from_scores(
    candidates: pd.DataFrame,
    *,
    score_col: str = "combined_score",
    ticker_col: str = "ticker",
) -> pd.Series:
    if score_col not in candidates.columns:
        score_col = "composite_score" if "composite_score" in candidates.columns else score_col
    if score_col not in candidates.columns or ticker_col not in candidates.columns:
        return pd.Series(dtype="float64")
    scores = pd.to_numeric(candidates[score_col], errors="coerce").fillna(50.0)
    tickers = candidates[ticker_col].astype(str).str.upper().str.strip()
    return pd.Series([score_to_expected_return(score) for score in scores], index=tickers, dtype="float64")


def optimize_mvo(
    candidates: pd.DataFrame,
    *,
    expected_returns: pd.Series | Mapping[str, float] | None = None,
    covariance: pd.DataFrame | None = None,
    risk_aversion: float = 5.0,
    transaction_cost_bps: float = 10.0,
    fallback_to_conviction: bool = True,
    **kwargs: object,
) -> pd.DataFrame:
    """Optimize selected names with scipy if available, otherwise fallback.

    The function first constructs a conviction portfolio to select sides and
    bounds.  If scipy/covariance/returns are unavailable, that portfolio is
    returned rather than failing a dry-run workflow.
    """

    initial = build_conviction_tilt_portfolio(candidates, **kwargs)
    if initial.empty:
        return initial
    if expected_returns is None:
        expected_returns = expected_returns_from_scores(candidates, score_col=str(kwargs.get("score_col", "combined_score")))
    if covariance is None:
        if fallback_to_conviction:
            initial["optimizer"] = "conviction_tilt_fallback"
            return initial
        raise ValueError("expected_returns and covariance are required for MVO")

    covariance = covariance.copy()
    covariance.index = covariance.index.astype(str).str.strip().str.upper()
    covariance.columns = covariance.columns.astype(str).str.strip().str.upper()
    tickers = initial["ticker"].astype(str).str.strip().str.upper().tolist()
    missing_coverage = sorted(
        ticker
        for ticker in tickers
        if ticker not in covariance.index or ticker not in covariance.columns
    )
    if missing_coverage:
        message = f"missing covariance coverage for tickers: {', '.join(missing_coverage)}"
        if fallback_to_conviction:
            initial["optimizer"] = "conviction_tilt_fallback"
            initial["mvo_message"] = message
            return initial
        raise ValueError(message)

    try:
        from scipy.optimize import minimize  # type: ignore
    except Exception:
        if fallback_to_conviction:
            initial["optimizer"] = "conviction_tilt_fallback"
            return initial
        raise

    mu = pd.Series(expected_returns, dtype="float64").reindex(tickers).fillna(0.0).to_numpy()
    cov = covariance.reindex(index=tickers, columns=tickers).fillna(0.0).to_numpy(dtype="float64")
    signs = np.where(initial["side"].to_numpy() == "short", -1.0, 1.0)
    start = initial["weight"].abs().to_numpy(dtype="float64")
    max_weights = np.maximum(start, initial["abs_weight"].max())
    min_weights = np.zeros_like(start)
    long_mask = signs > 0
    short_mask = signs < 0
    long_gross = float(start[long_mask].sum())
    short_gross = float(start[short_mask].sum())

    def objective(magnitudes: np.ndarray) -> float:
        weights = magnitudes * signs
        expected = float(mu @ weights)
        risk = float(weights @ cov @ weights)
        turnover_penalty = float(np.abs(magnitudes - start).sum()) * float(transaction_cost_bps) / 10_000.0
        return -expected + float(risk_aversion) * risk + turnover_penalty

    constraints = []
    if long_mask.any():
        constraints.append({"type": "eq", "fun": lambda x: float(x[long_mask].sum() - long_gross)})
    if short_mask.any():
        constraints.append({"type": "eq", "fun": lambda x: float(x[short_mask].sum() - short_gross)})
    result = minimize(objective, start, method="SLSQP", bounds=list(zip(min_weights, max_weights)), constraints=constraints)
    if not result.success:
        if fallback_to_conviction:
            initial["optimizer"] = "conviction_tilt_fallback"
            initial["mvo_message"] = str(result.message)
            return initial
        raise ValueError(f"MVO failed: {result.message}")
    optimized = initial.copy()
    optimized["weight"] = result.x * signs
    optimized["abs_weight"] = optimized["weight"].abs()
    optimized["gross_exposure"] = float(optimized["abs_weight"].sum())
    optimized["net_exposure"] = float(optimized["weight"].sum())
    optimized["optimizer"] = "mvo"
    return optimized


mvo_optimizer = optimize_mvo
