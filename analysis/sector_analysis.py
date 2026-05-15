"""Sector-level qualitative/score summaries and reranking helpers."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .combined_score import rerank_within_sector


def summarize_sector(
    scores: pd.DataFrame,
    *,
    sector: str | None = None,
    sector_col: str = "sector",
    score_col: str = "combined_score",
    ticker_col: str = "ticker",
    top_n: int = 5,
) -> dict[str, Any]:
    """Return a compact sector summary with top/bottom names."""

    if score_col not in scores.columns:
        raise ValueError(f"Missing required column: {score_col}")
    frame = scores.copy()
    if sector is not None and sector_col in frame.columns:
        frame = frame[frame[sector_col].astype(str).str.casefold() == str(sector).casefold()]
    ranked = frame.sort_values(score_col, ascending=False)
    tickers = ranked[ticker_col].astype(str).tolist() if ticker_col in ranked.columns else ranked.index.astype(str).tolist()
    return {
        "sector": sector,
        "count": int(len(frame)),
        "mean_score": float(pd.to_numeric(frame[score_col], errors="coerce").mean()) if len(frame) else None,
        "median_score": float(pd.to_numeric(frame[score_col], errors="coerce").median()) if len(frame) else None,
        "top": tickers[:top_n],
        "bottom": list(reversed(tickers[-top_n:])),
    }


def rerank_sector_scores(scores: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
    """Public wrapper around combined_score.rerank_within_sector."""

    return rerank_within_sector(scores, **kwargs)


# Alias matching the requested module name in user context.
analyze_sector = summarize_sector
