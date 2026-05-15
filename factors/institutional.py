"""Institutional sponsorship and 13F activity factor scoring."""

from __future__ import annotations

import pandas as pd

from .common import MetricSpec, score_factor


INSTITUTIONAL_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("tracked_fund_holder_count", aliases=("tracked_fund_holder_count", "tracked_funds_holding")),
    MetricSpec("aggregate_holding_change", aliases=("aggregate_holding_change", "institutional_ownership_change", "13f_ownership_change")),
    MetricSpec("multi_fund_opening", aliases=("multi_fund_opening", "multi_fund_opening_flag", "new_fund_openings")),
)


def score_institutional(frame: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Return ``frame`` with institutional metric scores and ``institutional_score``."""

    return score_factor(frame, prefix="institutional", metrics=INSTITUTIONAL_METRICS, **kwargs)


score = score_institutional
