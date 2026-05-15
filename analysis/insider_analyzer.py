"""Insider-activity qualitative analyzer."""

from __future__ import annotations

from typing import Any

from ._analyzer_base import AnalysisResult, LocalFirstAnalyzer


class InsiderAnalyzer(LocalFirstAnalyzer):
    analyzer_name = "insider"
    positive_terms = (
        "open market purchase",
        "cluster buying",
        "ceo purchase",
        "cfo purchase",
        "10b5-1 terminated",
        "net buying",
    )
    negative_terms = (
        "large sale",
        "cluster selling",
        "ceo sale",
        "cfo sale",
        "10b5-1 sale",
        "net selling",
    )

    def build_prompt(self, ticker: str, payload: Any) -> str:
        return (
            "You are analyzing Form 4 and insider activity. Return strict JSON "
            "only with score (0-100), signal_strength (STRONG_BUY to "
            "STRONG_SELL), confidence, key_transactions, reasoning, "
            "one_line_summary, summary, drivers, and risks. Distinguish "
            "open-market discretionary transactions from planned sales and "
            "equity compensation.\n\n"
            f"Ticker: {ticker}\nInsider material:\n{payload}"
        )

    def local_payload(self, ticker: str, payload: Any) -> dict[str, Any]:
        base = super().local_payload(ticker, payload)
        score = float(base["score"])
        if score >= 75:
            signal = "STRONG_BUY"
        elif score >= 55:
            signal = "BUY"
        elif score <= 25:
            signal = "STRONG_SELL"
        elif score <= 45:
            signal = "SELL"
        else:
            signal = "NEUTRAL"
        return {
            **base,
            "signal_strength": signal,
            "confidence": min(1.0, max(0.1, (abs(score - 50.0) / 50.0) + 0.3)),
            "key_transactions": payload if isinstance(payload, list) else [],
            "reasoning": base["summary"],
            "one_line_summary": base["summary"],
        }


def analyze_insider(ticker: str, payload: Any, **kwargs: Any) -> AnalysisResult:
    return InsiderAnalyzer(**{k: v for k, v in kwargs.items() if k in {"client", "cache", "config", "cost_tracker"}}).analyze(
        ticker,
        payload,
        artifact_id=kwargs.get("artifact_id", "latest"),
        use_ai=bool(kwargs.get("use_ai", False)),
        require_ai=bool(kwargs.get("require_ai", False)),
    )
