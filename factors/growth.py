"""Growth factor scoring."""

from __future__ import annotations

import pandas as pd

from .common import MetricSpec, coalesce_series, column_or_nan, safe_divide, score_factor


def _revenue_growth(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        safe_divide(column_or_nan(frame, "revenue_ttm"), column_or_nan(frame, "revenue_ttm_prior_year"), index=frame.index) - 1.0,
        safe_divide(column_or_nan(frame, "sales_ttm"), column_or_nan(frame, "sales_ttm_prior_year"), index=frame.index) - 1.0,
        index=frame.index,
    )


def _eps_growth(frame: pd.DataFrame) -> pd.Series:
    return safe_divide(column_or_nan(frame, "eps_ttm"), column_or_nan(frame, "eps_ttm_prior_year"), index=frame.index) - 1.0


def _fcf_growth(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        safe_divide(column_or_nan(frame, "free_cash_flow_ttm"), column_or_nan(frame, "free_cash_flow_ttm_prior_year"), index=frame.index) - 1.0,
        safe_divide(column_or_nan(frame, "fcf_ttm"), column_or_nan(frame, "fcf_ttm_prior_year"), index=frame.index) - 1.0,
        index=frame.index,
    )


def _revenue_growth_acceleration(frame: pd.DataFrame) -> pd.Series:
    current = coalesce_series(column_or_nan(frame, "revenue_growth"), column_or_nan(frame, "sales_growth"), index=frame.index)
    prior = coalesce_series(
        column_or_nan(frame, "revenue_growth_4q_ago"),
        column_or_nan(frame, "sales_growth_4q_ago"),
        column_or_nan(frame, "revenue_growth_prior_year"),
        index=frame.index,
    )
    return current - prior


def _rd_intensity(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        column_or_nan(frame, "rd_intensity"),
        safe_divide(column_or_nan(frame, "research_development_expense"), column_or_nan(frame, "revenue_ttm"), index=frame.index),
        safe_divide(column_or_nan(frame, "rd_expense"), column_or_nan(frame, "revenue_ttm"), index=frame.index),
        index=frame.index,
    )


GROWTH_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("revenue_growth", aliases=("revenue_growth", "sales_growth"), compute=_revenue_growth),
    MetricSpec("eps_growth", aliases=("eps_growth", "earnings_growth"), compute=_eps_growth),
    MetricSpec(
        "revenue_growth_acceleration",
        aliases=("revenue_growth_acceleration", "sales_growth_acceleration"),
        compute=_revenue_growth_acceleration,
    ),
    MetricSpec("rd_intensity", aliases=("rd_intensity", "research_development_intensity"), compute=_rd_intensity),
    MetricSpec("fcf_growth", aliases=("fcf_growth", "free_cash_flow_growth"), compute=_fcf_growth),
)


def score_growth(frame: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Return ``frame`` with growth metric scores and ``growth_score``."""

    return score_factor(frame, prefix="growth", metrics=GROWTH_METRICS, **kwargs)


score = score_growth
