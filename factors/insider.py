"""Insider activity factor scoring."""

from __future__ import annotations

import pandas as pd

from .common import MetricSpec, coalesce_series, column_or_nan, score_factor


def _net_insider_buying(frame: pd.DataFrame) -> pd.Series:
    buys = coalesce_series(
        column_or_nan(frame, "insider_buy_value"),
        column_or_nan(frame, "insider_purchases_value"),
        index=frame.index,
    )
    sells = coalesce_series(
        column_or_nan(frame, "insider_sell_value"),
        column_or_nan(frame, "insider_sales_value"),
        index=frame.index,
    )
    return buys.fillna(0.0) - sells.fillna(0.0).where(buys.notna() | sells.notna())


def _ceo_cfo_open_market_purchases(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        column_or_nan(frame, "ceo_cfo_open_market_purchases"),
        column_or_nan(frame, "ceo_cfo_buys"),
        column_or_nan(frame, "insider_ceo_cfo_buys"),
        index=frame.index,
    ) * 3.0


INSIDER_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "net_buy_value",
        aliases=("insider_net_buy_value", "net_insider_buy_value", "insider_net_purchases"),
        compute=_net_insider_buying,
    ),
    MetricSpec(
        "ceo_cfo_open_market_purchases",
        aliases=("ceo_cfo_open_market_purchases", "ceo_cfo_buys", "insider_ceo_cfo_buys"),
        compute=_ceo_cfo_open_market_purchases,
    ),
    MetricSpec("cluster_buys", aliases=("insider_cluster_buys", "cluster_buys", "open_market_buys")),
)


def score_insider(frame: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Return ``frame`` with insider metric scores and ``insider_score``."""

    return score_factor(frame, prefix="insider", metrics=INSIDER_METRICS, **kwargs)


score = score_insider
