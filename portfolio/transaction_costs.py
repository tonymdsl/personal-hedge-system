"""Transaction-cost estimates for paper/research rebalances."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd


@dataclass(frozen=True)
class TransactionCostBreakdown:
    quantity: float
    price: float
    side: str
    notional: float
    commission_usd: float
    spread_cost_usd: float
    slippage_cost_usd: float
    regulatory_fees_usd: float
    market_impact_cost_usd: float
    spread_cost_bps: float
    market_impact_bps: float
    total_cost_usd: float
    total_bps: float

    def as_dict(self) -> dict[str, float | str]:
        return {
            "quantity": float(self.quantity),
            "price": float(self.price),
            "side": self.side,
            "notional": float(self.notional),
            "commission_usd": float(self.commission_usd),
            "spread_cost_usd": float(self.spread_cost_usd),
            "slippage_cost_usd": float(self.slippage_cost_usd),
            "regulatory_fees_usd": float(self.regulatory_fees_usd),
            "market_impact_cost_usd": float(self.market_impact_cost_usd),
            "spread_cost_bps": float(self.spread_cost_bps),
            "market_impact_bps": float(self.market_impact_bps),
            "total_cost_usd": float(self.total_cost_usd),
            "total_bps": float(self.total_bps),
        }


def normalize_side(quantity: float, side: str | None = None) -> str:
    if side:
        normalized = str(side).strip().lower()
        if normalized in {"buy", "long", "cover"}:
            return "buy"
        if normalized in {"sell", "short"}:
            return "sell"
        raise ValueError("side must be buy/sell/long/short/cover")
    return "buy" if float(quantity) >= 0 else "sell"


def estimate_transaction_cost(
    *,
    quantity: float,
    price: float,
    side: str | None = None,
    commission_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    regulatory_fees_bps: float = 0.0,
    avg_daily_range_bps: float | None = None,
    adv_notional: float | None = None,
    daily_vol_bps: float | None = None,
    market_impact_coef: float = 0.10,
) -> TransactionCostBreakdown:
    """Estimate one trade's all-in cost.

    Inputs are interpreted as full notional basis points, not half-spread, which
    keeps the function transparent for scenario analysis.
    """

    if float(price) < 0:
        raise ValueError("price must be non-negative")
    notional = abs(float(quantity)) * float(price)
    normalized_side = normalize_side(quantity, side)
    commission = notional * max(0.0, float(commission_bps)) / 10_000.0
    effective_spread_bps = max(0.0, float(spread_bps))
    if avg_daily_range_bps is not None:
        effective_spread_bps = 0.05 * max(0.0, float(avg_daily_range_bps))
    market_impact_bps = 0.0
    if adv_notional is not None and daily_vol_bps is not None and float(adv_notional) > 0:
        trade_adv_pct = notional / float(adv_notional)
        market_impact_bps = max(0.0, float(market_impact_coef)) * (trade_adv_pct ** 0.5) * max(0.0, float(daily_vol_bps))
    spread = notional * effective_spread_bps / 10_000.0
    slippage = notional * max(0.0, float(slippage_bps)) / 10_000.0
    market_impact = notional * market_impact_bps / 10_000.0
    regulatory = notional * max(0.0, float(regulatory_fees_bps)) / 10_000.0 if normalized_side == "sell" else 0.0
    total = commission + spread + slippage + regulatory + market_impact
    total_bps = 0.0 if notional == 0 else total / notional * 10_000.0
    return TransactionCostBreakdown(
        quantity=float(quantity),
        price=float(price),
        side=normalized_side,
        notional=notional,
        commission_usd=commission,
        spread_cost_usd=spread,
        slippage_cost_usd=slippage,
        regulatory_fees_usd=regulatory,
        market_impact_cost_usd=market_impact,
        spread_cost_bps=effective_spread_bps,
        market_impact_bps=market_impact_bps,
        total_cost_usd=total,
        total_bps=total_bps,
    )


def estimate_portfolio_transaction_cost(
    current_weights: Mapping[str, float] | pd.Series,
    target_weights: Mapping[str, float] | pd.Series,
    *,
    nav: float,
    commission_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 10.0,
) -> dict[str, object]:
    """Estimate cost for moving from current weights to target weights."""

    current = pd.Series(current_weights, dtype="float64")
    target = pd.Series(target_weights, dtype="float64")
    tickers = sorted(set(current.index.astype(str)) | set(target.index.astype(str)))
    rows: list[dict[str, float | str]] = []
    total = 0.0
    for ticker in tickers:
        delta_notional = (float(target.get(ticker, 0.0)) - float(current.get(ticker, 0.0))) * float(nav)
        cost = estimate_transaction_cost(
            quantity=delta_notional,
            price=1.0,
            commission_bps=commission_bps,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
        )
        row = cost.as_dict()
        row["ticker"] = ticker
        row["delta_weight"] = delta_notional / float(nav) if nav else 0.0
        rows.append(row)
        total += cost.total_cost_usd
    return {"total_cost_usd": total, "total_cost_bps_nav": total / float(nav) * 10_000.0 if nav else 0.0, "trades": rows}
