"""Run-level token and dollar cost controls for qualitative analysis.

The tracker is intentionally provider-neutral.  API clients and analyzers submit
estimated or actual costs before/after work; the tracker enforces a run ceiling
without making network calls itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


class CostLimitExceeded(RuntimeError):
    """Raised when a requested analysis would exceed the configured ceiling."""


@dataclass(frozen=True)
class CostEstimate:
    """Estimated or actual token/cost usage for one analysis call."""

    input_tokens: int = 0
    output_tokens: int = 0
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    model: str | None = None
    analyzer: str | None = None
    ticker: str | None = None
    artifact_id: str | None = None

    @property
    def total_tokens(self) -> int:
        return int(self.input_tokens) + int(self.output_tokens)

    @property
    def total_cost_usd(self) -> float:
        return float(self.input_cost_usd) + float(self.output_cost_usd)

    def as_dict(self) -> dict[str, object]:
        return {
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "total_tokens": self.total_tokens,
            "input_cost_usd": float(self.input_cost_usd),
            "output_cost_usd": float(self.output_cost_usd),
            "total_cost_usd": self.total_cost_usd,
            "model": self.model,
            "analyzer": self.analyzer,
            "ticker": self.ticker,
            "artifact_id": self.artifact_id,
        }


@dataclass
class CostTracker:
    """Track projected and spent analysis costs against a hard ceiling."""

    run_cost_ceiling_usd: float
    reserved: list[CostEstimate] = field(default_factory=list)
    actual: list[CostEstimate] = field(default_factory=list)

    @property
    def reserved_cost_usd(self) -> float:
        return sum(item.total_cost_usd for item in self.reserved)

    @property
    def spent_cost_usd(self) -> float:
        return sum(item.total_cost_usd for item in self.actual)

    @property
    def projected_cost_usd(self) -> float:
        return self.reserved_cost_usd + self.spent_cost_usd

    def remaining_usd(self) -> float:
        return max(0.0, float(self.run_cost_ceiling_usd) - self.projected_cost_usd)

    def assert_can_add(self, estimate: CostEstimate) -> None:
        projected = self.projected_cost_usd + estimate.total_cost_usd
        if projected > float(self.run_cost_ceiling_usd) + 1e-12:
            raise CostLimitExceeded(
                "Analysis cost ceiling would be exceeded: "
                f"projected=${projected:.4f}, ceiling=${float(self.run_cost_ceiling_usd):.4f}"
            )

    def reserve(self, estimate: CostEstimate) -> CostEstimate:
        """Reserve budget for a planned call, raising if it exceeds the ceiling."""

        self.assert_can_add(estimate)
        self.reserved.append(estimate)
        return estimate

    def record_actual(self, estimate: CostEstimate, *, release_reserved: bool = True) -> CostEstimate:
        """Record actual usage after a call and optionally release one reservation."""

        if release_reserved and self.reserved:
            self.reserved.pop(0)
        self.assert_can_add(estimate)
        self.actual.append(estimate)
        return estimate

    def extend_reservations(self, estimates: Iterable[CostEstimate]) -> None:
        for estimate in estimates:
            self.reserve(estimate)

    def summary(self) -> dict[str, object]:
        return {
            "ceiling_usd": float(self.run_cost_ceiling_usd),
            "reserved_cost_usd": self.reserved_cost_usd,
            "spent_cost_usd": self.spent_cost_usd,
            "projected_cost_usd": self.projected_cost_usd,
            "remaining_usd": self.remaining_usd(),
            "reserved_calls": len(self.reserved),
            "actual_calls": len(self.actual),
        }
