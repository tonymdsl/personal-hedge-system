"""SEC filing qualitative analyzer."""

from __future__ import annotations

from typing import Any

from ._analyzer_base import AnalysisResult, LocalFirstAnalyzer


class FilingAnalyzer(LocalFirstAnalyzer):
    analyzer_name = "filing"
    positive_terms = (
        "share repurchase",
        "free cash flow",
        "operating leverage",
        "reduced debt",
        "recurring revenue",
        "backlog increased",
    )
    negative_terms = (
        "material weakness",
        "going concern",
        "impairment",
        "litigation",
        "subpoena",
        "restatement",
        "customer concentration",
    )

    def build_prompt(self, ticker: str, payload: Any) -> str:
        return (
            "You are reviewing a public-company filing. Return strict JSON only "
            "with score (0-100), earnings_quality_score, balance_sheet_score, "
            "red_flags, green_flags, risk_level, summary, drivers, and risks. "
            "Use 8 quarters of fundamentals when available. Assess CFO vs net "
            "income, receivables/revenue quality, balance sheet health, accruals, "
            "liquidity, debt, accounting quality, and guidance.\n\n"
            f"Ticker: {ticker}\nFiling excerpt/material:\n{payload}"
        )

    def local_payload(self, ticker: str, payload: Any) -> dict[str, Any]:
        base = super().local_payload(ticker, payload)
        green_flags = list(base["drivers"])
        red_flags = list(base["risks"])
        risk_level = "low" if float(base["score"]) >= 70 else ("high" if float(base["score"]) < 40 else "medium")
        return {
            **base,
            "earnings_quality_score": float(base["score"]),
            "balance_sheet_score": float(base["score"]),
            "red_flags": red_flags,
            "green_flags": green_flags,
            "risk_level": risk_level,
        }


def analyze_filing(ticker: str, payload: Any, **kwargs: Any) -> AnalysisResult:
    return FilingAnalyzer(**{k: v for k, v in kwargs.items() if k in {"client", "cache", "config", "cost_tracker"}}).analyze(
        ticker,
        payload,
        artifact_id=kwargs.get("artifact_id", "latest"),
        use_ai=bool(kwargs.get("use_ai", False)),
        require_ai=bool(kwargs.get("require_ai", False)),
    )
