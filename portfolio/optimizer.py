"""Conviction-tilt long/short portfolio optimizer."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Mapping

import numpy as np
import pandas as pd

from .beta import calculate_portfolio_beta


def _portfolio_config(config: Mapping[str, object] | None) -> Mapping[str, object]:
    section = (config or {}).get("portfolio", {}) if isinstance(config, Mapping) else {}
    return section if isinstance(section, Mapping) else {}


def _bounded_allocation(convictions: pd.Series, gross: float, min_weight: float, max_weight: float) -> pd.Series:
    n = len(convictions)
    if n == 0 or gross <= 0:
        return pd.Series(dtype="float64")
    min_weight = max(0.0, float(min_weight))
    max_weight = max(min_weight, float(max_weight))
    if gross > n * max_weight + 1e-12:
        raise ValueError("Cannot allocate requested gross exposure within max_position_weight")
    if gross < n * min_weight - 1e-12:
        raise ValueError("Cannot allocate requested gross exposure within min_position_weight")

    raw = pd.to_numeric(convictions, errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype="float64")
    if raw.sum() <= 0:
        raw = np.ones(n, dtype="float64")
    weights = np.full(n, min_weight, dtype="float64")
    remaining = float(gross) - float(weights.sum())
    caps = np.full(n, max_weight - min_weight, dtype="float64")

    while remaining > 1e-12:
        active = caps > 1e-12
        if not bool(active.any()):
            break
        active_raw = raw[active]
        shares = active_raw / active_raw.sum() if active_raw.sum() > 0 else np.ones(active.sum()) / active.sum()
        proposed = shares * remaining
        additions = np.minimum(proposed, caps[active])
        weights[active] += additions
        caps[active] -= additions
        used = float(additions.sum())
        if used <= 1e-15:
            break
        remaining -= used
    if abs(float(weights.sum()) - float(gross)) > 1e-8:
        # Put tiny residual into the first name with spare capacity.
        residual = float(gross) - float(weights.sum())
        for idx in range(n):
            candidate = weights[idx] + residual
            if min_weight - 1e-12 <= candidate <= max_weight + 1e-12:
                weights[idx] = candidate
                break
    return pd.Series(weights, index=convictions.index, dtype="float64")


def _side_count(available: int, target: int, gross: float, min_weight: float, max_weight: float) -> int:
    if available <= 0 or gross <= 0:
        return 0
    min_required = int(math.ceil((gross - 1e-12) / max_weight)) if max_weight > 0 else available + 1
    max_allowed = int(math.floor((gross + 1e-12) / min_weight)) if min_weight > 0 else available
    count = max(int(target), min_required)
    count = min(count, max_allowed, available)
    if count < min_required:
        raise ValueError("Not enough candidates to satisfy gross exposure and max_position_weight")
    return max(0, count)


def _apply_sector_gross_cap(portfolio: pd.DataFrame, max_sector_gross_weight: float | None) -> pd.DataFrame:
    if max_sector_gross_weight is None or "sector" not in portfolio.columns or portfolio.empty:
        return portfolio
    cap = float(max_sector_gross_weight)
    if cap <= 0:
        return portfolio
    result = portfolio.copy()
    for sector, rows in result.groupby("sector", dropna=False):
        gross = float(rows["weight"].abs().sum())
        if gross > cap + 1e-12:
            result.loc[rows.index, "weight"] *= cap / gross
    return result


def _apply_beta_cap(portfolio: pd.DataFrame, beta_cap_abs: float | None) -> pd.DataFrame:
    if beta_cap_abs is None or "beta" not in portfolio.columns or portfolio.empty:
        return portfolio
    cap = abs(float(beta_cap_abs))
    if cap <= 0:
        return portfolio
    beta_value = calculate_portfolio_beta(portfolio.set_index("ticker")["weight"], portfolio.set_index("ticker")["beta"])
    if abs(beta_value) <= cap + 1e-12:
        portfolio = portfolio.copy()
        portfolio["portfolio_beta"] = beta_value
        return portfolio
    result = portfolio.copy()
    if beta_value > cap:
        side_mask = result["weight"] > 0
    else:
        side_mask = result["weight"] < 0
    side_beta = float((result.loc[side_mask, "weight"] * result.loc[side_mask, "beta"]).sum())
    other_beta = float((result.loc[~side_mask, "weight"] * result.loc[~side_mask, "beta"]).sum())
    if abs(side_beta) > 1e-12:
        target_side_beta = (cap if beta_value > cap else -cap) - other_beta
        scale = max(0.0, min(1.0, target_side_beta / side_beta))
        result.loc[side_mask, "weight"] *= scale
    result["portfolio_beta"] = calculate_portfolio_beta(result.set_index("ticker")["weight"], result.set_index("ticker")["beta"])
    return result


def _as_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _score_tier_multipliers(frame: pd.DataFrame, score_col: str) -> pd.Series:
    multipliers = pd.Series(1.0, index=frame.index, dtype="float64")
    if frame.empty:
        return multipliers
    ranked_high = frame.sort_values(score_col, ascending=False)
    top_5 = max(1, int(math.ceil(len(frame) * 0.05)))
    top_10 = max(top_5, int(math.ceil(len(frame) * 0.10)))
    multipliers.loc[ranked_high.head(top_10).index] = 1.25
    multipliers.loc[ranked_high.head(top_5).index] = 1.50

    ranked_low = frame.sort_values(score_col, ascending=True)
    multipliers.loc[ranked_low.head(top_10).index] = 1.25
    multipliers.loc[ranked_low.head(top_5).index] = 1.50
    return multipliers


def _apply_position_adjustments(
    portfolio: pd.DataFrame,
    *,
    nav: float,
    current_date: date | datetime | str | None,
    earnings_advisory_days: int,
    max_trade_adv_pct: float,
) -> pd.DataFrame:
    if portfolio.empty:
        return portfolio
    result = portfolio.copy()
    result["liquidity_cap_weight"] = pd.NA
    if "avg_dollar_volume" in result.columns and float(nav) > 0:
        cap = pd.to_numeric(result["avg_dollar_volume"], errors="coerce") * float(max_trade_adv_pct) / float(nav)
        result["liquidity_cap_weight"] = cap
        too_large = cap.notna() & (result["weight"].abs() > cap)
        result.loc[too_large, "weight"] = result.loc[too_large, "weight"].clip(
            lower=-cap.loc[too_large],
            upper=cap.loc[too_large],
        )
    result["earnings_size_multiplier"] = 1.0
    today = _as_date(current_date) or date.today()
    if "earnings_date" in result.columns:
        event_dates = result["earnings_date"].apply(_as_date)
        near_earnings = event_dates.apply(lambda event: event is not None and 0 <= (event - today).days <= int(earnings_advisory_days))
        result.loc[near_earnings, "earnings_size_multiplier"] = 0.50
        result.loc[near_earnings, "weight"] *= 0.50
    return result


def build_conviction_tilt_portfolio(
    candidates: pd.DataFrame,
    *,
    config: Mapping[str, object] | None = None,
    score_col: str = "combined_score",
    ticker_col: str = "ticker",
    sector_col: str = "sector",
    target_longs: int | None = None,
    target_shorts: int | None = None,
    long_gross_exposure: float | None = None,
    short_gross_exposure: float | None = None,
    max_position_weight: float | None = None,
    min_position_weight: float | None = None,
    max_sector_gross_weight: float | None = None,
    beta_cap_abs: float | None = None,
    neutral_score: float = 50.0,
    nav: float = 1_000_000.0,
    current_date: date | datetime | str | None = None,
    earnings_advisory_days: int = 5,
    max_trade_adv_pct: float = 0.05,
) -> pd.DataFrame:
    """Build a signed long/short portfolio from combined scores.

    Longs are top-ranked names; shorts are bottom-ranked names.  Position sizes
    tilt toward conviction while respecting min/max position weights and gross
    side exposure when feasible.
    """

    if score_col not in candidates.columns:
        raise ValueError(f"Missing required column: {score_col}")
    if ticker_col not in candidates.columns:
        raise ValueError(f"Missing required column: {ticker_col}")
    cfg = _portfolio_config(config)
    target_longs = int(target_longs if target_longs is not None else cfg.get("target_longs", 20))
    target_shorts = int(target_shorts if target_shorts is not None else cfg.get("target_shorts", 20))
    long_gross = float(long_gross_exposure if long_gross_exposure is not None else cfg.get("long_gross_exposure", 0.75))
    short_gross = float(short_gross_exposure if short_gross_exposure is not None else cfg.get("short_gross_exposure", 0.75))
    max_weight = float(max_position_weight if max_position_weight is not None else cfg.get("max_position_weight", 0.05))
    min_weight = float(min_position_weight if min_position_weight is not None else cfg.get("min_position_weight", 0.005))
    sector_cap = max_sector_gross_weight if max_sector_gross_weight is not None else cfg.get("max_sector_gross_weight")
    beta_cap = beta_cap_abs if beta_cap_abs is not None else cfg.get("beta_cap_abs")

    frame = candidates.copy()
    frame[score_col] = pd.to_numeric(frame[score_col], errors="coerce")
    frame = frame.dropna(subset=[score_col])
    if frame.empty:
        return pd.DataFrame(columns=["ticker", "side", "weight", "score", "conviction"])
    frame[ticker_col] = frame[ticker_col].astype(str).str.strip().str.upper()
    frame = frame.drop_duplicates(subset=[ticker_col], keep="first")
    frame["conviction_multiplier"] = _score_tier_multipliers(frame, score_col)

    long_count = _side_count(len(frame), target_longs, long_gross, min_weight, max_weight)
    short_count = _side_count(max(0, len(frame) - long_count), target_shorts, short_gross, min_weight, max_weight)

    ranked_high = frame.sort_values(score_col, ascending=False)
    longs = ranked_high.head(long_count).copy()
    remaining = frame[~frame[ticker_col].isin(longs[ticker_col])]
    shorts = remaining.sort_values(score_col, ascending=True).head(short_count).copy()

    rows: list[pd.DataFrame] = []
    if not longs.empty:
        long_conviction = (longs[score_col] - neutral_score).clip(lower=1.0) * longs["conviction_multiplier"]
        longs["weight"] = _bounded_allocation(long_conviction, long_gross, min_weight, max_weight)
        longs["side"] = "long"
        longs["conviction"] = long_conviction
        rows.append(longs)
    if not shorts.empty:
        short_conviction = (neutral_score - shorts[score_col]).clip(lower=1.0) * shorts["conviction_multiplier"]
        shorts["weight"] = -_bounded_allocation(short_conviction, short_gross, min_weight, max_weight)
        shorts["side"] = "short"
        shorts["conviction"] = short_conviction
        rows.append(shorts)
    if not rows:
        return pd.DataFrame(columns=["ticker", "side", "weight", "score", "conviction"])

    portfolio = pd.concat(rows, axis=0, ignore_index=True)
    portfolio = portfolio.rename(columns={ticker_col: "ticker", score_col: "score"})
    if sector_col in portfolio.columns and sector_col != "sector":
        portfolio = portfolio.rename(columns={sector_col: "sector"})
    portfolio = _apply_position_adjustments(
        portfolio,
        nav=float(nav),
        current_date=current_date,
        earnings_advisory_days=earnings_advisory_days,
        max_trade_adv_pct=max_trade_adv_pct,
    )
    portfolio = _apply_sector_gross_cap(portfolio, float(sector_cap) if sector_cap is not None else None)
    portfolio = _apply_beta_cap(portfolio, float(beta_cap) if beta_cap is not None else None)
    portfolio["abs_weight"] = portfolio["weight"].abs()
    portfolio["gross_exposure"] = float(portfolio["abs_weight"].sum())
    portfolio["net_exposure"] = float(portfolio["weight"].sum())
    return portfolio.sort_values(["side", "score"], ascending=[True, False]).reset_index(drop=True)


optimize_portfolio = build_conviction_tilt_portfolio
