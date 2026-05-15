"""Company risk qualitative analyzer."""

from __future__ import annotations

from typing import Any

from ._analyzer_base import AnalysisResult, LocalFirstAnalyzer


class RiskAnalyzer(LocalFirstAnalyzer):
    analyzer_name = "risk"
    positive_terms = (
        "hedged",
        "diversified",
        "net cash",
        "low leverage",
        "stable demand",
        "contracted revenue",
    )
    negative_terms = (
        "high leverage",
        "refinancing",
        "cyclical",
        "regulatory",
        "supply chain",
        "fraud",
        "short squeeze",
        "liquidity risk",
    )

    def build_prompt(self, ticker: str, payload: Any) -> str:
        return (
            "You are a long/short equity risk analyst. Return strict JSON only "
            "with score (0-100), new_risks, material_risks, "
            "boilerplate_percentage, risk_severity, one_line_summary, summary, "
            "drivers, risks, and mitigants. Separate material risks from "
            "boilerplate, flag new risks versus prior filing when available, "
            "and penalize tail risk, leverage, liquidity, crowding, regulatory "
            "overhangs, and short-squeeze risk.\n\n"
            f"Ticker: {ticker}\nRisk material:\n{payload}"
        )

    def local_payload(self, ticker: str, payload: Any) -> dict[str, Any]:
        base = super().local_payload(ticker, payload)
        material_risks = list(base["risks"])
        severity = "low" if float(base["score"]) >= 70 else ("high" if float(base["score"]) < 40 else "medium")
        return {
            **base,
            "new_risks": [],
            "material_risks": material_risks,
            "boilerplate_percentage": 0.0 if material_risks else 100.0,
            "risk_severity": severity,
            "one_line_summary": base["summary"],
            "mitigants": list(base["drivers"]),
        }


def analyze_risk(ticker: str, payload: Any, **kwargs: Any) -> AnalysisResult:
    return RiskAnalyzer(**{k: v for k, v in kwargs.items() if k in {"client", "cache", "config", "cost_tracker"}}).analyze(
        ticker,
        payload,
        artifact_id=kwargs.get("artifact_id", "latest"),
        use_ai=bool(kwargs.get("use_ai", False)),
        require_ai=bool(kwargs.get("require_ai", False)),
    )
