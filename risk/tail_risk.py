"""Tail-risk gross exposure adjustments."""
from __future__ import annotations

from typing import Mapping


def gross_exposure_multiplier(*, vix: float | None = None, credit_spread_z: float | None = None, config: Mapping[str, object] | None = None) -> float:
    risk = (config or {}).get('risk', {}) if isinstance(config, Mapping) else {}
    tail = risk.get('tail_risk', {}) if isinstance(risk, Mapping) else {}
    moderate_vix = float(tail.get('moderate_vix_threshold', 25)) if isinstance(tail, Mapping) else 25.0
    high_vix = float(tail.get('high_vix_threshold', 35)) if isinstance(tail, Mapping) else 35.0
    credit_z = float(tail.get('credit_spread_stress_zscore', 1.0)) if isinstance(tail, Mapping) else 1.0
    moderate_reduction = float(tail.get('moderate_gross_reduction_multiplier', 0.80)) if isinstance(tail, Mapping) else 0.80
    reduction = float(tail.get('gross_reduction_multiplier', 0.50)) if isinstance(tail, Mapping) else 0.50
    if vix is not None and vix >= high_vix:
        return reduction
    if (vix is not None and vix >= moderate_vix) or (credit_spread_z is not None and credit_spread_z >= credit_z):
        return moderate_reduction
    return 1.0
