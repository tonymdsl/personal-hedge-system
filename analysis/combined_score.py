"""Combine quantitative scores with optional Codex qualitative scores."""

from __future__ import annotations

from typing import Any

import pandas as pd


def _coerce_score(series: pd.Series, *, neutral_score: float) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").clip(0, 100).fillna(neutral_score)


def combine_scores(
    scores: pd.DataFrame,
    *,
    quant_col: str = "quant_score",
    qualitative_col: str = "qualitative_score",
    sector_col: str = "sector",
    quantitative_weight: float = 0.60,
    qualitative_weight: float = 0.40,
    neutral_score: float = 50.0,
    sector_rerank: bool = True,
) -> pd.DataFrame:
    """Return scores with 60/40 quant/Codex qualitative blend when available.

    Rows with no qualitative score remain 100% quantitative, as requested.  The
    two weights are normalized only for rows where both scores exist.
    """

    if quant_col not in scores.columns:
        raise ValueError(f"Missing required column: {quant_col}")
    result = scores.copy()
    quant = _coerce_score(result[quant_col], neutral_score=neutral_score)
    if qualitative_col in result.columns:
        qualitative_raw = pd.to_numeric(result[qualitative_col], errors="coerce")
    else:
        qualitative_raw = pd.Series(float("nan"), index=result.index, dtype="float64")
    qualitative = qualitative_raw.clip(0, 100)
    has_qualitative = qualitative.notna()

    total_weight = float(quantitative_weight) + float(qualitative_weight)
    if total_weight <= 0:
        raise ValueError("Score weights must sum to a positive value")
    q_weight = float(quantitative_weight) / total_weight
    c_weight = float(qualitative_weight) / total_weight

    result["combined_score"] = quant.where(~has_qualitative, quant * q_weight + qualitative * c_weight).clip(0, 100)
    result["qualitative_used"] = has_qualitative

    if sector_rerank and sector_col in result.columns:
        result = rerank_within_sector(result, sector_col=sector_col, score_col="combined_score")
    else:
        result["overall_rank"] = result["combined_score"].rank(ascending=False, method="dense")
    return result


def rerank_within_sector(
    frame: pd.DataFrame,
    *,
    sector_col: str = "sector",
    score_col: str = "combined_score",
) -> pd.DataFrame:
    """Add sector-level rank/percentile columns without changing scores."""

    if score_col not in frame.columns:
        raise ValueError(f"Missing required column: {score_col}")
    result = frame.copy()
    if sector_col not in result.columns:
        result["overall_rank"] = result[score_col].rank(ascending=False, method="dense")
        return result
    result["sector_rank"] = result.groupby(sector_col, dropna=False)[score_col].rank(ascending=False, method="dense")
    result["sector_percentile"] = result.groupby(sector_col, dropna=False)[score_col].rank(
        pct=True, ascending=True
    ) * 100.0
    result["overall_rank"] = result[score_col].rank(ascending=False, method="dense")
    return result


def combine_score_records(records: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
    """Convenience wrapper for list-of-dicts inputs."""

    return combine_scores(pd.DataFrame(records), **kwargs).to_dict(orient="records")
