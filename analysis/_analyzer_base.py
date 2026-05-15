"""Shared utilities for deterministic local analyzer fallbacks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .api_client import estimate_call_cost, extract_json
from .cache import AnalysisCache
from .cost_tracker import CostEstimate, CostTracker


@dataclass
class AnalysisResult:
    ticker: str
    analyzer: str
    artifact_id: str
    score: float
    summary: str
    drivers: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    cached: bool = False
    cost: CostEstimate | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "analyzer": self.analyzer,
            "artifact_id": self.artifact_id,
            "score": float(self.score),
            "summary": self.summary,
            "drivers": list(self.drivers),
            "risks": list(self.risks),
            "raw": dict(self.raw),
            "model": self.model,
            "cached": self.cached,
            "cost": self.cost.as_dict() if self.cost else None,
        }


def text_from_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def keyword_score(
    text: str,
    *,
    positive_terms: Iterable[str],
    negative_terms: Iterable[str],
    neutral: float = 50.0,
    step: float = 5.0,
) -> tuple[float, list[str], list[str]]:
    lowered = text.casefold()
    positives = [term for term in positive_terms if term.casefold() in lowered]
    negatives = [term for term in negative_terms if term.casefold() in lowered]
    score = max(0.0, min(100.0, neutral + step * len(positives) - step * len(negatives)))
    return score, positives, negatives


def normalize_payload(payload: Any, *, default_score: float = 50.0) -> dict[str, Any]:
    parsed = extract_json(payload) if isinstance(payload, str) else payload
    if not isinstance(parsed, Mapping):
        raise ValueError("Analyzer JSON must be an object")
    result = dict(parsed)
    score = float(result.get("score", default_score))
    result["score"] = max(0.0, min(100.0, score))
    result.setdefault("summary", "")
    result.setdefault("drivers", [])
    result.setdefault("risks", [])
    return result


class LocalFirstAnalyzer:
    """Base class for cache-aware analyzers with optional Codex clients."""

    analyzer_name = "base"
    positive_terms: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        client: Any | None = None,
        cache: AnalysisCache | None = None,
        config: Mapping[str, Any] | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.client = client
        self.cache = cache
        self.config = config or {}
        self.cost_tracker = cost_tracker

    def build_prompt(self, ticker: str, payload: Any) -> str:
        return (
            f"Analyze {ticker} for {self.analyzer_name}. Return strict JSON with keys "
            "score (0-100), summary, drivers, risks.\n\n"
            f"Input:\n{text_from_payload(payload)}"
        )

    def local_payload(self, ticker: str, payload: Any) -> dict[str, Any]:
        text = text_from_payload(payload)
        score, drivers, risks = keyword_score(
            text,
            positive_terms=self.positive_terms,
            negative_terms=self.negative_terms,
        )
        return {
            "score": score,
            "summary": f"Local heuristic {self.analyzer_name} analysis for {ticker}.",
            "drivers": drivers,
            "risks": risks,
        }

    def analyze(
        self,
        ticker: str,
        payload: Any,
        *,
        artifact_id: str = "latest",
        use_ai: bool = False,
        require_ai: bool = False,
        expected_output_tokens: int = 800,
    ) -> AnalysisResult:
        ticker_key = str(ticker).strip().upper()
        if self.cache is not None:
            cached_record = self.cache.get_record(self.analyzer_name, ticker_key, artifact_id)
            if cached_record is not None and (not require_ai or cached_record.get("model")):
                normalized = normalize_payload(cached_record["payload"])
                return AnalysisResult(
                    ticker=ticker_key,
                    analyzer=self.analyzer_name,
                    artifact_id=artifact_id,
                    score=float(normalized["score"]),
                    summary=str(normalized.get("summary", "")),
                    drivers=list(normalized.get("drivers", [])),
                    risks=list(normalized.get("risks", [])),
                    raw=dict(normalized),
                    model=str(cached_record.get("model")) if cached_record.get("model") else None,
                    cached=True,
                )

        prompt = self.build_prompt(ticker_key, payload)
        estimate = estimate_call_cost(
            prompt,
            expected_output_tokens=expected_output_tokens,
            config=self.config,
            model=getattr(self.client, "model", None),
            analyzer=self.analyzer_name,
            ticker=ticker_key,
            artifact_id=artifact_id,
        )
        if self.cost_tracker is not None:
            self.cost_tracker.reserve(estimate)

        if (use_ai or require_ai) and self.client is not None:
            raw_payload = self.client.complete_json(prompt)
            normalized = normalize_payload(raw_payload)
        elif require_ai:
            raise RuntimeError(f"{self.analyzer_name} AI analysis is required for {ticker_key}, but no Codex client is configured.")
        else:
            normalized = self.local_payload(ticker_key, payload)

        if self.cache is not None:
            self.cache.set(
                self.analyzer_name,
                ticker_key,
                artifact_id,
                normalized,
                model=getattr(self.client, "model", None),
            )
        return AnalysisResult(
            ticker=ticker_key,
            analyzer=self.analyzer_name,
            artifact_id=artifact_id,
            score=float(normalized["score"]),
            summary=str(normalized.get("summary", "")),
            drivers=list(normalized.get("drivers", [])),
            risks=list(normalized.get("risks", [])),
            raw=dict(normalized),
            model=getattr(self.client, "model", None),
            cached=False,
            cost=estimate,
        )
