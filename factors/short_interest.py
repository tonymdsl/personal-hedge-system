"""Short-interest and borrow-crowding factor scoring.

Lower short interest, lower days-to-cover and lower borrow stress score better
for the long/short composite; names with high shorting pressure naturally fall
into lower composite scores and can become short candidates.
"""

from __future__ import annotations

import pandas as pd

from .common import MetricSpec, score_factor


SHORT_INTEREST_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "short_float_pct",
        aliases=("short_float_pct", "short_percent_float", "short_interest_float", "short_float"),
        higher_is_better=False,
    ),
    MetricSpec("days_to_cover", aliases=("days_to_cover", "short_ratio"), higher_is_better=False),
    MetricSpec("short_interest_change", aliases=("short_interest_change", "short_float_change"), higher_is_better=False),
)


def score_short_interest(frame: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Return ``frame`` with short-interest metric scores and ``short_interest_score``."""

    return score_factor(frame, prefix="short_interest", metrics=SHORT_INTEREST_METRICS, **kwargs)


score = score_short_interest
