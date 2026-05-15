"""Layer 5 risk check command."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pandas as pd

from common.cli import add_common_arguments
from common.config import PROJECT_ROOT, ensure_project_path, load_config
from risk.circuit_breakers import clear_halt, evaluate_circuit_breakers
from risk.factor_risk_model import build_factor_risk_model
from risk.risk_state import write_risk_state
from risk.stress_test import stress_test_portfolio
from risk.tail_risk import gross_exposure_multiplier


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Meridian Layer 5 risk checks.')
    add_common_arguments(parser)
    parser.add_argument('--stress', action='store_true')
    parser.add_argument('--tail-only', action='store_true')
    parser.add_argument('--clear-halt', action='store_true')
    parser.add_argument('--vix', type=float, default=None)
    parser.add_argument('--credit-spread-z', type=float, default=None)
    parser.add_argument('--daily-pnl-pct', type=float, default=0.0)
    parser.add_argument('--weekly-pnl-pct', type=float, default=0.0)
    parser.add_argument('--drawdown-pct', type=float, default=0.0)
    parser.add_argument('--single-position-loss-pct', type=float, default=0.0)
    parser.add_argument('--nav', type=float, default=1.0)
    parser.add_argument('--portfolio-input', default='output/rebalance_orders_latest.csv')
    parser.add_argument('--state-path', default='cache/risk_state.json')
    return parser


def _read_portfolio(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path
    if not input_path.exists():
        return pd.DataFrame()
    return pd.read_csv(input_path)


def _score_exposures(portfolio: pd.DataFrame) -> pd.DataFrame:
    if portfolio.empty or 'ticker' not in portfolio.columns:
        return pd.DataFrame()
    factor_cols = [col for col in portfolio.columns if col.endswith('_score')]
    if not factor_cols:
        known = {'momentum', 'value', 'quality', 'growth', 'revisions', 'insider', 'short_interest', 'institutional'}
        factor_cols = [col for col in portfolio.columns if col in known]
    if not factor_cols:
        return pd.DataFrame()
    exposures = portfolio.set_index(portfolio['ticker'].astype(str))[factor_cols].apply(pd.to_numeric, errors='coerce').fillna(50.0)
    exposures.columns = [col.removesuffix('_score') for col in exposures.columns]
    return exposures


def _weights(portfolio: pd.DataFrame) -> pd.Series:
    if portfolio.empty or 'ticker' not in portfolio.columns or 'weight' not in portfolio.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(portfolio.set_index(portfolio['ticker'].astype(str))['weight'], errors='coerce').fillna(0.0)


def _safe_write_state(state: Mapping[str, object], path: str | Path) -> Path:
    output = Path(path)
    if output.is_absolute():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(dict(state), indent=2, sort_keys=True, default=str), encoding='utf-8')
        return output
    return write_risk_state(state, output)


def run_risk_pipeline(
    portfolio: pd.DataFrame,
    *,
    config: Mapping[str, object] | None = None,
    state_path: str | Path = 'cache/risk_state.json',
    daily_pnl_pct: float = 0.0,
    weekly_pnl_pct: float = 0.0,
    drawdown_pct: float = 0.0,
    single_position_loss_pct: float = 0.0,
    include_stress: bool = False,
    vix: float | None = None,
    credit_spread_z: float | None = None,
    nav: float = 1.0,
) -> dict[str, object]:
    circuit_state = {
        'daily_pnl_pct': float(daily_pnl_pct),
        'weekly_pnl_pct': float(weekly_pnl_pct),
        'drawdown_pct': float(drawdown_pct),
        'single_position_loss_pct': float(single_position_loss_pct),
    }
    circuit_breakers = evaluate_circuit_breakers(circuit_state, config=config)
    tail_multiplier = gross_exposure_multiplier(vix=vix, credit_spread_z=credit_spread_z, config=config)

    exposures = _score_exposures(portfolio)
    weights = _weights(portfolio)
    factor_exposures: dict[str, float] = {}
    risk_decomp: dict[str, object] = {'factor_variance': 0.0, 'specific_variance': 0.0, 'total_variance': 0.0, 'factor_exposure': {}}
    top_mctr_positions: list[dict[str, object]] = []
    if not exposures.empty and not weights.empty:
        gross = float(weights.abs().sum()) or 1.0
        factor_exposures = {factor: float((weights.reindex(exposures.index).fillna(0.0) * exposures[factor]).sum() / gross) for factor in exposures.columns}
        risk_decomp['factor_exposure'] = factor_exposures
        synthetic_returns = pd.DataFrame(columns=exposures.index)
        factor_model = build_factor_risk_model(synthetic_returns, exposures, weights=weights)
        risk_decomp = factor_model['risk_decomposition']  # type: ignore[assignment]
        risk_decomp['factor_exposure'] = factor_exposures
        top_mctr_positions = factor_model['mctr'].sort_values('mctr_pct', key=lambda col: col.abs(), ascending=False).head(10).to_dict(orient='records')  # type: ignore[index]

    alerts = [{'type': 'circuit_breaker', 'action': action} for action in circuit_breakers.get('actions', [])]
    if tail_multiplier < 1.0:
        alerts.append({'type': 'tail_risk', 'gross_exposure_multiplier': tail_multiplier})

    state: dict[str, object] = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        **circuit_state,
        'tail_multiplier': tail_multiplier,
        'circuit_breaker_usage': circuit_breakers,
        'factor_exposures': factor_exposures,
        'risk_decomposition': risk_decomp,
        'per_factor_contributions': risk_decomp.get('factor_exposure', {}),
        'top_mctr_positions': top_mctr_positions,
        'alerts': alerts,
    }

    payload: dict[str, object] = {
        'tail_multiplier': tail_multiplier,
        'circuit_breakers': circuit_breakers,
        'alerts': alerts,
        'risk_state_path': str(_safe_write_state(state, state_path)),
    }
    if include_stress:
        stress = stress_test_portfolio(portfolio, nav=nav)
        payload['stress'] = stress.to_dict(orient='records')
        state['stress'] = payload['stress']
        _safe_write_state(state, state_path)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.clear_halt:
        lock = config.get('risk', {}).get('circuit_breakers', {}).get('lock_file', 'cache/trading_halt.lock')
        print(json.dumps({'cleared': clear_halt(lock), 'lock_file': lock}, indent=2))
        return 0
    if args.tail_only:
        payload: dict[str, object] = {'tail_multiplier': gross_exposure_multiplier(vix=args.vix, credit_spread_z=args.credit_spread_z, config=config)}
        print(json.dumps(payload, indent=2, default=str))
        return 0
    portfolio = _read_portfolio(args.portfolio_input)
    payload = run_risk_pipeline(
        portfolio,
        config=config,
        state_path=args.state_path,
        daily_pnl_pct=args.daily_pnl_pct,
        weekly_pnl_pct=args.weekly_pnl_pct,
        drawdown_pct=args.drawdown_pct,
        single_position_loss_pct=args.single_position_loss_pct,
        include_stress=args.stress,
        vix=args.vix,
        credit_spread_z=args.credit_spread_z,
        nav=args.nav,
    )
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
