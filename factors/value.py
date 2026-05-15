"""Value factor scoring.

Higher valuation yield metrics score better.  EV/EBITDA is inverted so cheaper
(lower multiple) receives the higher percentile score.
"""

from __future__ import annotations

import pandas as pd

from .common import MetricSpec, coalesce_series, column_or_nan, safe_divide, score_factor


def _earnings_yield(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        safe_divide(column_or_nan(frame, "eps_ttm"), column_or_nan(frame, "price"), index=frame.index),
        safe_divide(column_or_nan(frame, "net_income_ttm"), column_or_nan(frame, "market_cap"), index=frame.index),
        safe_divide(column_or_nan(frame, "net_income"), column_or_nan(frame, "market_cap"), index=frame.index),
        index=frame.index,
    )


def _book_to_price(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        safe_divide(column_or_nan(frame, "book_value_per_share"), column_or_nan(frame, "price"), index=frame.index),
        safe_divide(column_or_nan(frame, "total_equity"), column_or_nan(frame, "market_cap"), index=frame.index),
        safe_divide(column_or_nan(frame, "book_value"), column_or_nan(frame, "market_cap"), index=frame.index),
        index=frame.index,
    )


def _fcf_yield(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        safe_divide(column_or_nan(frame, "free_cash_flow_ttm"), column_or_nan(frame, "market_cap"), index=frame.index),
        safe_divide(column_or_nan(frame, "fcf_ttm"), column_or_nan(frame, "market_cap"), index=frame.index),
        index=frame.index,
    )


def _ev_to_ebitda(frame: pd.DataFrame) -> pd.Series:
    return safe_divide(column_or_nan(frame, "enterprise_value"), column_or_nan(frame, "ebitda_ttm"), index=frame.index)


def _shareholder_yield(frame: pd.DataFrame) -> pd.Series:
    dividend_yield = coalesce_series(
        column_or_nan(frame, "dividend_yield"),
        safe_divide(column_or_nan(frame, "dividends_paid_ttm") * -1.0, column_or_nan(frame, "market_cap"), index=frame.index),
        index=frame.index,
    )
    buyback_yield = coalesce_series(
        column_or_nan(frame, "buyback_yield"),
        column_or_nan(frame, "net_buyback_yield"),
        safe_divide(column_or_nan(frame, "net_share_repurchases_ttm"), column_or_nan(frame, "market_cap"), index=frame.index),
        index=frame.index,
    )
    has_component = dividend_yield.notna() | buyback_yield.notna()
    total_yield = dividend_yield.fillna(0.0) + buyback_yield.fillna(0.0)
    return total_yield.where(has_component)


def _sales_to_ev(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        safe_divide(column_or_nan(frame, "revenue_ttm"), column_or_nan(frame, "enterprise_value"), index=frame.index),
        safe_divide(column_or_nan(frame, "sales_ttm"), column_or_nan(frame, "enterprise_value"), index=frame.index),
        index=frame.index,
    )


VALUE_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("earnings_yield", aliases=("earnings_yield", "ey"), compute=_earnings_yield),
    MetricSpec("book_to_price", aliases=("book_to_price", "book_price", "book_yield"), compute=_book_to_price),
    MetricSpec("fcf_yield", aliases=("fcf_yield", "free_cash_flow_yield"), compute=_fcf_yield),
    MetricSpec(
        "ev_to_ebitda",
        aliases=("ev_to_ebitda", "ev_ebitda", "enterprise_value_to_ebitda"),
        higher_is_better=False,
        compute=_ev_to_ebitda,
    ),
    MetricSpec("shareholder_yield", aliases=("shareholder_yield",), compute=_shareholder_yield),
    MetricSpec("sales_to_ev", aliases=("sales_to_ev", "sales_ev"), compute=_sales_to_ev),
)


def score_value(frame: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Return ``frame`` with value metric scores and ``value_score``."""

    return score_factor(frame, prefix="value", metrics=VALUE_METRICS, **kwargs)


score = score_value
