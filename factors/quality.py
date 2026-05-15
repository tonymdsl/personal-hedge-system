"""Quality factor scoring with financial-strength color coding."""

from __future__ import annotations

import pandas as pd

from .common import MetricSpec, coalesce_series, column_or_nan, safe_divide, score_factor, to_numeric_series


def _roe_stability(frame: pd.DataFrame) -> pd.Series:
    roe_mean = coalesce_series(column_or_nan(frame, "roe_mean_5y"), column_or_nan(frame, "roe_mean"), index=frame.index)
    roe_std = coalesce_series(
        column_or_nan(frame, "roe_std_5y"),
        column_or_nan(frame, "roe_std"),
        column_or_nan(frame, "roe_volatility"),
        index=frame.index,
    ).abs()
    stability_from_mean = safe_divide(roe_mean, 1.0 + roe_std, index=frame.index)
    inverse_volatility = -roe_std
    return coalesce_series(stability_from_mean, inverse_volatility, index=frame.index)


def _gross_margin_trend(frame: pd.DataFrame) -> pd.Series:
    current = coalesce_series(
        column_or_nan(frame, "gross_margin"),
        column_or_nan(frame, "gross_margin_ttm"),
        index=frame.index,
    )
    prior = coalesce_series(
        column_or_nan(frame, "gross_margin_prior_year"),
        column_or_nan(frame, "gross_margin_1y_ago"),
        column_or_nan(frame, "gross_margin_py"),
        index=frame.index,
    )
    return current - prior


def _debt_to_equity(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        safe_divide(column_or_nan(frame, "total_debt"), column_or_nan(frame, "total_equity"), index=frame.index),
        safe_divide(column_or_nan(frame, "debt"), column_or_nan(frame, "shareholders_equity"), index=frame.index),
        index=frame.index,
    )


def _cfo_to_net_income(frame: pd.DataFrame) -> pd.Series:
    return coalesce_series(
        safe_divide(column_or_nan(frame, "cash_flow_from_operations_ttm"), column_or_nan(frame, "net_income_ttm"), index=frame.index),
        safe_divide(column_or_nan(frame, "cfo_ttm"), column_or_nan(frame, "net_income_ttm"), index=frame.index),
        safe_divide(column_or_nan(frame, "operating_cash_flow"), column_or_nan(frame, "net_income"), index=frame.index),
        index=frame.index,
    )


def _accruals(frame: pd.DataFrame) -> pd.Series:
    net_income = coalesce_series(column_or_nan(frame, "net_income_ttm"), column_or_nan(frame, "net_income"), index=frame.index)
    cfo = coalesce_series(
        column_or_nan(frame, "cash_flow_from_operations_ttm"),
        column_or_nan(frame, "cfo_ttm"),
        column_or_nan(frame, "operating_cash_flow"),
        index=frame.index,
    )
    assets = coalesce_series(column_or_nan(frame, "total_assets"), column_or_nan(frame, "assets"), index=frame.index)
    return safe_divide(net_income - cfo, assets, index=frame.index)


def quality_color(score: float | int | pd.Series) -> str | pd.Series:
    """Map quality score to traffic-light colors.

    Green: >=70, Yellow: 40-70, Red: <40.
    """

    if isinstance(score, pd.Series):
        values = to_numeric_series(score).fillna(50.0)
        colors = pd.Series("yellow", index=values.index, dtype="object")
        colors.loc[values >= 70.0] = "green"
        colors.loc[values < 40.0] = "red"
        return colors

    value = float(score)
    if value >= 70.0:
        return "green"
    if value < 40.0:
        return "red"
    return "yellow"


QUALITY_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("roe_stability", aliases=("roe_stability", "roe_quality"), compute=_roe_stability),
    MetricSpec("gross_margin_level", aliases=("gross_margin", "gross_margin_ttm")),
    MetricSpec("gross_margin_trend", aliases=("gross_margin_trend",), compute=_gross_margin_trend),
    MetricSpec(
        "debt_to_equity",
        aliases=("debt_to_equity", "debt_equity", "de_ratio"),
        higher_is_better=False,
        compute=_debt_to_equity,
    ),
    MetricSpec("cfo_to_net_income", aliases=("cfo_to_net_income", "cfo_ni"), compute=_cfo_to_net_income),
    MetricSpec("accruals", aliases=("accruals", "total_accruals"), higher_is_better=False, compute=_accruals),
    MetricSpec("piotroski", aliases=("piotroski", "piotroski_score", "f_score")),
    MetricSpec("altman_z", aliases=("altman_z", "altman_z_score", "z_score")),
)


def score_quality(frame: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Return ``frame`` with quality metric scores, ``quality_score`` and color."""

    scored = score_factor(frame, prefix="quality", metrics=QUALITY_METRICS, **kwargs)
    scored["quality_color"] = quality_color(scored["quality_score"])
    return scored


score = score_quality
