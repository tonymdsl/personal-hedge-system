"""Simple Barra-style factor risk model."""
from __future__ import annotations

import numpy as np
import pandas as pd


def standardize_exposures(exposures: pd.DataFrame) -> pd.DataFrame:
    numeric = exposures.apply(pd.to_numeric, errors='coerce')
    std = numeric.std(ddof=0).replace(0, np.nan)
    return ((numeric - numeric.mean()) / std).fillna(0.0)


def estimate_factor_returns(asset_returns: pd.DataFrame, exposures: pd.DataFrame) -> pd.DataFrame:
    aligned = exposures.reindex(asset_returns.columns).dropna(how='all')
    returns_frame = asset_returns.reindex(columns=aligned.index)
    x = standardize_exposures(aligned).to_numpy(dtype=float)
    factors = list(aligned.columns)
    if not factors or x.size == 0:
        return pd.DataFrame(columns=factors)
    rows = []
    for dt, returns in returns_frame.iterrows():
        y = pd.to_numeric(returns, errors='coerce').reindex(aligned.index).fillna(0.0).to_numpy(dtype=float)
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        row = {'date': dt}
        row.update({factor: float(value) for factor, value in zip(factors, beta)})
        rows.append(row)
    return pd.DataFrame(rows).set_index('date') if rows else pd.DataFrame(columns=factors)


def risk_decomposition(weights: pd.Series, exposures: pd.DataFrame, factor_cov: pd.DataFrame, specific_var: pd.Series | None = None) -> dict[str, object]:
    weights = pd.to_numeric(weights, errors='coerce').fillna(0.0).reindex(exposures.index).fillna(0.0)
    x = standardize_exposures(exposures)
    cov = factor_cov.reindex(index=x.columns, columns=x.columns).fillna(0.0).to_numpy(dtype=float)
    factor_load = weights.to_numpy(dtype=float) @ x.to_numpy(dtype=float)
    factor_var = float(factor_load @ cov @ factor_load.T)
    spec = specific_var.reindex(weights.index).fillna(0.0) if specific_var is not None else pd.Series(0.0, index=weights.index)
    specific_variance = float(((weights ** 2) * spec).sum())
    total = factor_var + specific_variance
    return {'factor_variance': factor_var, 'specific_variance': specific_variance, 'total_variance': total, 'factor_exposure': dict(zip(x.columns, factor_load))}


def marginal_contribution_to_risk(weights: pd.Series, predicted_covariance: pd.DataFrame) -> pd.DataFrame:
    """Return per-position marginal/component total risk contribution."""
    tickers = list(predicted_covariance.index)
    w = pd.to_numeric(weights, errors='coerce').reindex(tickers).fillna(0.0).astype(float)
    cov = predicted_covariance.reindex(index=tickers, columns=tickers).fillna(0.0).to_numpy(dtype=float)
    w_arr = w.to_numpy(dtype=float)
    variance = float(w_arr @ cov @ w_arr.T)
    sigma = float(np.sqrt(max(variance, 0.0)))
    if sigma <= 0:
        marginal = np.zeros_like(w_arr)
        component = np.zeros_like(w_arr)
        pct = np.zeros_like(w_arr)
    else:
        marginal = cov @ w_arr / sigma
        component = w_arr * marginal
        pct = component / sigma
    result = pd.DataFrame(
        {
            'ticker': tickers,
            'weight': w_arr,
            'marginal_risk': marginal,
            'mctr': component,
            'mctr_pct': pct,
        }
    )
    result['mctr_flag'] = result['mctr_pct'].abs() > (1.5 * result['weight'].abs())
    return result


def build_factor_risk_model(
    asset_returns: pd.DataFrame,
    exposures: pd.DataFrame,
    *,
    weights: pd.Series | None = None,
    annualization: int = 252,
) -> dict[str, object]:
    """Build a Barra-style factor model from returns and sector-ranked exposures.

    ``exposures`` are expected to be stock-by-factor 0-100 scores/ranks. They are
    standardized cross-sectionally before regression, matching the L5 prompt.
    """
    if exposures.empty or asset_returns.empty:
        empty_cov = pd.DataFrame(index=exposures.index, columns=exposures.index, dtype=float).fillna(0.0)
        empty_factor_cov = pd.DataFrame(index=exposures.columns, columns=exposures.columns, dtype=float).fillna(0.0)
        empty_specific = pd.Series(0.0, index=exposures.index, dtype=float)
        empty_weights = pd.Series(0.0, index=exposures.index, dtype=float) if weights is None else weights
        return {
            'factor_returns': pd.DataFrame(columns=exposures.columns),
            'factor_covariance': empty_factor_cov,
            'specific_variance': empty_specific,
            'predicted_covariance': empty_cov,
            'risk_decomposition': risk_decomposition(empty_weights, exposures, empty_factor_cov, empty_specific) if not exposures.empty else {'factor_variance': 0.0, 'specific_variance': 0.0, 'total_variance': 0.0, 'factor_exposure': {}},
            'mctr': marginal_contribution_to_risk(empty_weights, empty_cov) if len(empty_cov.index) else pd.DataFrame(columns=['ticker', 'weight', 'marginal_risk', 'mctr', 'mctr_pct', 'mctr_flag']),
        }

    tickers = [ticker for ticker in exposures.index if ticker in asset_returns.columns]
    aligned_exposures = exposures.loc[tickers].apply(pd.to_numeric, errors='coerce').fillna(50.0)
    aligned_returns = asset_returns.reindex(columns=tickers).apply(pd.to_numeric, errors='coerce').fillna(0.0)
    standardized = standardize_exposures(aligned_exposures)
    factor_returns = estimate_factor_returns(aligned_returns, aligned_exposures)
    factor_covariance = factor_returns.cov(ddof=0).reindex(index=aligned_exposures.columns, columns=aligned_exposures.columns).fillna(0.0) * annualization

    if factor_returns.empty:
        fitted = pd.DataFrame(0.0, index=aligned_returns.index, columns=tickers)
    else:
        common_dates = aligned_returns.index.intersection(factor_returns.index)
        fitted_values = factor_returns.loc[common_dates].to_numpy(dtype=float) @ standardized.to_numpy(dtype=float).T
        fitted = pd.DataFrame(fitted_values, index=common_dates, columns=tickers)
        aligned_returns = aligned_returns.loc[common_dates]

    residuals = aligned_returns - fitted.reindex_like(aligned_returns).fillna(0.0)
    specific_variance = residuals.var(ddof=0).fillna(0.0) * annualization
    factor_cov_arr = factor_covariance.to_numpy(dtype=float)
    factor_component = standardized.to_numpy(dtype=float) @ factor_cov_arr @ standardized.to_numpy(dtype=float).T
    predicted_covariance = pd.DataFrame(factor_component, index=tickers, columns=tickers)
    for ticker, value in specific_variance.items():
        predicted_covariance.loc[ticker, ticker] = float(predicted_covariance.loc[ticker, ticker]) + float(value)

    model_weights = pd.Series(0.0, index=tickers, dtype=float) if weights is None else pd.to_numeric(weights, errors='coerce').reindex(tickers).fillna(0.0)
    return {
        'factor_returns': factor_returns,
        'factor_covariance': factor_covariance,
        'specific_variance': specific_variance,
        'predicted_covariance': predicted_covariance,
        'risk_decomposition': risk_decomposition(model_weights, aligned_exposures, factor_covariance, specific_variance),
        'mctr': marginal_contribution_to_risk(model_weights, predicted_covariance),
    }
