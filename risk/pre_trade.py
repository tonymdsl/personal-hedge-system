"""Pre-trade veto checks with absolute risk override."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping

import pandas as pd


@dataclass(frozen=True)
class Trade:
    ticker: str
    side: str
    quantity: float
    price: float
    weight: float = 0.0
    sector: str | None = None
    dollar_adv: float | None = None
    beta: float | None = None
    earnings_date: str | date | datetime | None = None
    is_closing: bool = False

    @property
    def notional(self) -> float:
        return abs(float(self.quantity) * float(self.price))


@dataclass
class PreTradeDecision:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


def _section(config: Mapping[str, object] | None, name: str) -> Mapping[str, object]:
    value = (config or {}).get(name, {}) if isinstance(config, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def _is_closing(trade: Trade, portfolio: pd.DataFrame | None = None) -> bool:
    if trade.is_closing or trade.side.lower() in {'close', 'cover'}:
        return True
    if portfolio is None or portfolio.empty or 'ticker' not in portfolio.columns:
        return False
    rows = portfolio[portfolio['ticker'].astype(str).str.upper() == trade.ticker.upper()]
    if rows.empty or 'quantity' not in rows.columns:
        return False
    current_qty = float(pd.to_numeric(pd.Series([rows.iloc[0]['quantity']]), errors='coerce').fillna(0.0).iloc[0])
    side = trade.side.lower()
    return (current_qty > 0 and side in {'sell', 'short'}) or (current_qty < 0 and side in {'buy', 'cover'})


def _coerce_date(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _numeric_column(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors='coerce').fillna(default)


def _log_rejection(path: str | Path | None, trade: Trade, decision: PreTradeDecision) -> None:
    if path is None or decision.approved:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'ticker': trade.ticker,
        'side': trade.side,
        'notional': trade.notional,
        'weight': trade.weight,
        'reasons': decision.reasons,
        'warnings': decision.warnings,
    }
    with output.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, sort_keys=True, default=str) + '\n')


def check_pre_trade(
    trade: Trade | Mapping[str, object],
    *,
    portfolio: pd.DataFrame | None = None,
    config: Mapping[str, object] | None = None,
    halt_lock: str | Path | None = None,
    pairwise_correlations: Mapping[str, float] | None = None,
    log_path: str | Path | None = None,
    as_of: str | date | datetime | None = None,
) -> PreTradeDecision:
    if not isinstance(trade, Trade):
        trade = Trade(**trade)  # type: ignore[arg-type]
    risk = _section(config, 'risk')
    portfolio_cfg = _section(config, 'portfolio')
    liquidity = risk.get('liquidity', {}) if isinstance(risk.get('liquidity', {}), Mapping) else {}
    reasons: list[str] = []
    warnings: list[str] = []
    if _is_closing(trade, portfolio):
        return PreTradeDecision(True, warnings=['closing_or_covering_trade_allowed'], checks={'closing_or_covering_trade_allowed': True})

    cb = risk.get('circuit_breakers', {}) if isinstance(risk.get('circuit_breakers', {}), Mapping) else {}
    lock_path = Path(halt_lock or cb.get('lock_file', 'cache/trading_halt.lock'))
    checks: dict[str, bool] = {}

    checks['halt_lock_active'] = not lock_path.exists()
    if lock_path.exists():
        reasons.append('halt_lock_active')

    today = _coerce_date(as_of) or date.today()
    earnings_date = _coerce_date(trade.earnings_date)
    blackout_days = int(risk.get('earnings_blackout_days', 5))
    checks['earnings_blackout'] = True
    if earnings_date is not None:
        days_until = (earnings_date - today).days
        if 0 <= days_until <= blackout_days:
            checks['earnings_blackout'] = False
            reasons.append('earnings_blackout')

    max_pos = float(portfolio_cfg.get('max_position_weight', 0.05))
    checks['max_position_weight'] = abs(float(trade.weight or 0.0)) <= max_pos + 1e-12
    if abs(float(trade.weight or 0.0)) > max_pos + 1e-12:
        reasons.append('max_position_weight')

    min_adv = float(liquidity.get('min_dollar_adv', 10_000_000))
    max_adv_pct = float(liquidity.get('max_trade_adv_pct', 0.05))
    checks['liquidity_min_adv'] = True
    checks['liquidity_trade_adv_pct'] = True
    if trade.dollar_adv is not None:
        if float(trade.dollar_adv) < min_adv:
            checks['liquidity_min_adv'] = False
            reasons.append('liquidity_min_adv')
        if trade.notional / max(float(trade.dollar_adv), 1.0) > max_adv_pct:
            checks['liquidity_trade_adv_pct'] = False
            reasons.append('liquidity_trade_adv_pct')

    checks['gross_exposure'] = True
    checks['net_exposure'] = True
    checks['sector_cap'] = True
    checks['net_beta'] = True
    if portfolio is not None and not portfolio.empty and 'weight' in portfolio.columns:
        portfolio_weights = _numeric_column(portfolio, 'weight')
        gross = float(portfolio_weights.abs().sum()) + abs(float(trade.weight or 0.0))
        net = float(portfolio_weights.sum()) + float(trade.weight or 0.0)
        gross_limit = float(risk.get('gross_exposure_limit', 1.65))
        net_min = float(risk.get('net_exposure_min', -0.10))
        net_max = float(risk.get('net_exposure_max', 0.15))
        if gross > gross_limit + 1e-12:
            checks['gross_exposure'] = False
            reasons.append('gross_exposure')
        if net < net_min - 1e-12 or net > net_max + 1e-12:
            checks['net_exposure'] = False
            reasons.append('net_exposure')
        sector_col = 'sector' if 'sector' in portfolio.columns else 'gics_sector' if 'gics_sector' in portfolio.columns else None
        if trade.sector and sector_col is not None:
            sector_gross = float(portfolio_weights.loc[portfolio[sector_col] == trade.sector].abs().sum()) + abs(float(trade.weight or 0.0))
            if sector_gross > float(portfolio_cfg.get('max_sector_gross_weight', 0.25)) + 1e-12:
                checks['sector_cap'] = False
                reasons.append('sector_cap')

        if trade.beta is not None:
            current_beta = float((portfolio_weights * _numeric_column(portfolio, 'beta')).sum())
            net_beta = current_beta + float(trade.beta) * float(trade.weight or 0.0)
            beta_limit = float(risk.get('net_beta_limit', portfolio_cfg.get('beta_cap_abs', 0.20)))
            if abs(net_beta) > beta_limit + 1e-12:
                checks['net_beta'] = False
                reasons.append('net_beta')
    elif trade.beta is not None and abs(float(trade.beta) * float(trade.weight or 0.0)) > float(risk.get('net_beta_limit', portfolio_cfg.get('beta_cap_abs', 0.20))) + 1e-12:
        checks['net_beta'] = False
        reasons.append('net_beta')

    corr_limit = float(risk.get('pairwise_correlation_limit', 0.80))
    checks['pairwise_correlation'] = not bool(pairwise_correlations and any(abs(float(v)) > corr_limit for v in pairwise_correlations.values()))
    if pairwise_correlations and any(abs(float(v)) > corr_limit for v in pairwise_correlations.values()):
        reasons.append('pairwise_correlation')
    decision = PreTradeDecision(approved=not reasons, reasons=reasons, warnings=warnings, checks=checks)
    _log_rejection(log_path, trade, decision)
    return decision


pre_trade_veto = check_pre_trade
