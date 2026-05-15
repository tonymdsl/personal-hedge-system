"""Earnings/transcript qualitative analyzer."""

from __future__ import annotations

from typing import Any

from ._analyzer_base import AnalysisResult, LocalFirstAnalyzer


class EarningsAnalyzer(LocalFirstAnalyzer):
    analyzer_name = "earnings"
    positive_terms = (
        "beat",
        "raise",
        "raised guidance",
        "margin expansion",
        "accelerating",
        "record revenue",
        "strong demand",
    )
    negative_terms = (
        "miss",
        "lowered guidance",
        "margin pressure",
        "decelerating",
        "weak demand",
        "inventory build",
        "one-time",
    )

    def build_prompt(self, ticker: str, payload: Any) -> str:
        return (
            "You are an equity earnings analyst. Return strict JSON only with "
            "score (0-100), management_confidence, revenue_guidance, "
            "margin_trajectory, competitive_position, risk_factors, "
            "capital_allocation, per_category_reasoning, bull_case, bear_case, "
            "key_quotes, one_line_summary, summary, drivers, and risks. Score "
            "the category fields 1-10. Focus on guidance, revenue quality, "
            "margins, cash flow, and management tone.\n\n"
            f"Ticker: {ticker}\nEarnings material:\n{payload}"
        )

    def local_payload(self, ticker: str, payload: Any) -> dict[str, Any]:
        base = super().local_payload(ticker, payload)
        category_score = max(1, min(10, round(float(base["score"]) / 10.0)))
        return {
            **base,
            "management_confidence": category_score,
            "revenue_guidance": category_score,
            "margin_trajectory": category_score,
            "competitive_position": category_score,
            "risk_factors": max(1, min(10, 11 - len(base["risks"]))),
            "capital_allocation": category_score,
            "per_category_reasoning": {
                "management_confidence": "Local keyword screen based on transcript tone.",
                "revenue_guidance": "Local keyword screen based on guidance language.",
                "margin_trajectory": "Local keyword screen based on margin language.",
            },
            "bull_case": "Positive transcript language supports the long thesis." if base["drivers"] else "",
            "bear_case": "Negative transcript language or missing evidence weakens confidence." if base["risks"] else "",
            "key_quotes": [],
            "one_line_summary": base["summary"],
        }


def analyze_earnings(ticker: str, payload: Any, **kwargs: Any) -> AnalysisResult:
    return EarningsAnalyzer(**{k: v for k, v in kwargs.items() if k in {"client", "cache", "config", "cost_tracker"}}).analyze(
        ticker,
        payload,
        artifact_id=kwargs.get("artifact_id", "latest"),
        use_ai=bool(kwargs.get("use_ai", False)),
        require_ai=bool(kwargs.get("require_ai", False)),
    )
