"""Momentum factor scoring.

Metrics scored within GICS sector:
- 12 minus 1 month momentum
- 6 month return
- 3 month return
- acceleration (recent 3m minus prior 3m)
- proximity to 52 week high
- relative strength versus sector ETF/benchmark
"""

from __future__ import annotations

import pandas as pd

from .common import MetricSpec, coalesce_series, column_or_nan, safe_divide, score_factor


def _return_from_prices(frame: pd.DataFrame, lag_column: str) -> pd.Series:
    price = coalesce_series(
        column_or_nan(frame, "price"),
        column_or_nan(frame, "current_price"),
        column_or_nan(frame, "close"),
        index=frame.index,
    )
    lagged = column_or_nan(frame, lag_column)
    return safe_divide(price, lagged, index=frame.index) - 1.0


def _return_12_1m(frame: pd.DataFrame) -> pd.Series:
    direct_price = safe_divide(column_or_nan(frame, "price_21d_ago"), column_or_nan(frame, "price_252d_ago"), index=frame.index) - 1.0
    return_12m = coalesce_series(
        column_or_nan(frame, "return_12m"),
        column_or_nan(frame, "total_return_12m"),
        _return_from_prices(frame, "price_252d_ago"),
        index=frame.index,
    )
    return_1m = coalesce_series(
        column_or_nan(frame, "return_1m"),
        _return_from_prices(frame, "price_21d_ago"),
        index=frame.index,
    )
    compounded = safe_divide(1.0 + return_12m, 1.0 + return_1m, index=frame.index) - 1.0
    return coalesce_series(direct_price, compounded, index=frame.index)


def _return_6m(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        column_or_nan(frame, "return_6m"),
        column_or_nan(frame, "total_return_6m"),
        _return_from_prices(frame, "price_126d_ago"),
        index=frame.index,
    )


def _return_3m(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        column_or_nan(frame, "return_3m"),
        column_or_nan(frame, "total_return_3m"),
        _return_from_prices(frame, "price_63d_ago"),
        index=frame.index,
    )


def _acceleration(frame: pd.DataFrame) -> pd.Series:
    # Approximate current 3m return minus prior 3m return.  If only 6m and 3m
    # returns are available, prior 3m ~= return_6m - return_3m.
    return_3m = _return_3m(frame)
    return_6m = _return_6m(frame)
    return (2.0 * return_3m) - return_6m


def _proximity_52w(frame: pd.DataFrame) -> pd.Series:
    price = coalesce_series(
        column_or_nan(frame, "price"),
        column_or_nan(frame, "current_price"),
        column_or_nan(frame, "close"),
        index=frame.index,
    )
    high = coalesce_series(
        column_or_nan(frame, "high_52w"),
        column_or_nan(frame, "fifty_two_week_high"),
        column_or_nan(frame, "52_week_high"),
        index=frame.index,
    )
    return safe_divide(price, high, index=frame.index)


def _relative_strength_sector(frame: pd.DataFrame) -> pd.Series:
    own_return = coalesce_series(_return_6m(frame), _return_3m(frame), index=frame.index)
    sector_return = coalesce_series(
        column_or_nan(frame, "sector_etf_return_6m"),
        column_or_nan(frame, "sector_return_6m"),
        column_or_nan(frame, "sector_benchmark_return_6m"),
        index=frame.index,
    )
    return own_return - sector_return


MOMENTUM_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "return_12_1m",
        aliases=("return_12_1m", "momentum_12_1m", "mom_12_1"),
        compute=_return_12_1m,
    ),
    MetricSpec("return_6m", aliases=("return_6m", "total_return_6m"), compute=_return_6m),
    MetricSpec("return_3m", aliases=("return_3m", "total_return_3m"), compute=_return_3m),
    MetricSpec("acceleration", aliases=("momentum_acceleration", "return_acceleration"), compute=_acceleration),
    MetricSpec("proximity_52w", aliases=("proximity_52w", "fifty_two_week_proximity"), compute=_proximity_52w),
    MetricSpec(
        "relative_strength_sector",
        aliases=("relative_strength_sector", "sector_relative_strength", "rs_vs_sector_etf"),
        compute=_relative_strength_sector,
    ),
)


def score_momentum(frame: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Return ``frame`` with momentum metric scores and ``momentum_score``."""

    return score_factor(frame, prefix="momentum", metrics=MOMENTUM_METRICS, **kwargs)


score = score_momentum
