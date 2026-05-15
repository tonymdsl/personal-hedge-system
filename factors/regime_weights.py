"""Regime-conditioned factor weights."""
from __future__ import annotations

from typing import Mapping

DEFAULT_WEIGHTS = {
    'momentum': 0.20,
    'quality': 0.20,
    'value': 0.15,
    'revisions': 0.15,
    'insider': 0.10,
    'growth': 0.10,
    'short_interest': 0.05,
    'institutional': 0.05,
}

LOW_VIX_WEIGHTS = {
    **DEFAULT_WEIGHTS,
    'momentum': 0.28,
    'quality': 0.17,
    'value': 0.10,
}

HIGH_VIX_WEIGHTS = {
    **DEFAULT_WEIGHTS,
    'momentum': 0.10,
    'quality': 0.28,
    'value': 0.22,
    'revisions': 0.10,
}


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    cleaned = {str(k): max(0.0, float(v)) for k, v in weights.items()}
    total = sum(cleaned.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in cleaned.items()}


def apply_regime_weights(base_weights: Mapping[str, float] | None = None, *, vix: float | None = None, config: Mapping[str, object] | None = None) -> dict[str, float]:
    weights = dict(base_weights or DEFAULT_WEIGHTS)
    scoring = (config or {}).get('scoring', {}) if isinstance(config, Mapping) else {}
    regime = scoring.get('regime_weights', {}) if isinstance(scoring, Mapping) else {}
    if isinstance(scoring, Mapping) and isinstance(scoring.get('default_weights'), Mapping) and base_weights is None:
        weights = {str(k): float(v) for k, v in scoring['default_weights'].items()}
    if not isinstance(regime, Mapping) or not regime.get('enabled', True) or vix is None:
        return normalize_weights(weights)
    low = float(regime.get('low_vix_threshold', 15))
    high = float(regime.get('high_vix_threshold', 25))
    if vix <= low:
        return normalize_weights(regime.get('low_vix_weights', LOW_VIX_WEIGHTS) if isinstance(regime, Mapping) else LOW_VIX_WEIGHTS)
    elif vix >= high:
        return normalize_weights(regime.get('high_vix_weights', HIGH_VIX_WEIGHTS) if isinstance(regime, Mapping) else HIGH_VIX_WEIGHTS)
    return normalize_weights(weights)
