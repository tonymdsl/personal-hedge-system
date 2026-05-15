"""Correlation and effective-number-of-bets diagnostics."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_correlations(returns: pd.DataFrame, *, window: int = 60) -> pd.DataFrame:
    return returns.rolling(window=min(window, max(1, len(returns)))).corr()


def effective_number_of_bets(weights: pd.Series) -> float:
    w = pd.to_numeric(weights, errors='coerce').abs().fillna(0.0)
    total = float(w.sum())
    if total <= 0:
        return 0.0
    p = w / total
    return float(1.0 / np.square(p).sum())


def effective_bets_from_correlation(correlation: pd.DataFrame) -> float:
    if correlation.empty:
        return 0.0
    matrix = correlation.fillna(0.0).to_numpy(dtype=float)
    eigenvalues = np.linalg.eigvalsh(matrix)
    eigenvalues = np.clip(eigenvalues, 0.0, None)
    total = float(eigenvalues.sum())
    if total <= 0:
        return 0.0
    p = eigenvalues / total
    p = p[p > 0]
    return float(np.exp(-(p * np.log(p)).sum()))


def _average_pairwise_correlation(correlation: pd.DataFrame) -> float:
    if len(correlation.columns) < 2:
        return 0.0
    mask = np.triu(np.ones(correlation.shape, dtype=bool), k=1)
    values = correlation.where(mask).stack()
    return float(values.mean()) if not values.empty else 0.0


def correlation_alerts(returns: pd.DataFrame, books: dict[str, list[str]], *, window: int = 60, threshold: float = 0.60) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []
    sample = returns.tail(min(window, max(1, len(returns)))).apply(pd.to_numeric, errors='coerce')
    for book, tickers in books.items():
        available = [ticker for ticker in tickers if ticker in sample.columns]
        if len(available) < 2:
            continue
        corr = sample[available].corr().fillna(0.0)
        avg_corr = _average_pairwise_correlation(corr)
        if avg_corr > threshold:
            alerts.append(
                {
                    'book': book,
                    'average_correlation': avg_corr,
                    'threshold': threshold,
                    'effective_bets': effective_bets_from_correlation(corr),
                }
            )
    return alerts
