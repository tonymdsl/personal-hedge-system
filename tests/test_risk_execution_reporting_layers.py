from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from execution.broker import BrokerConfigError, validate_live_guard
from execution.costs import slippage_bps, slippage_summary
from execution.executor import execute_trades
from reporting.letter import generate_daily_lp_letter
from risk.circuit_breakers import clear_halt, evaluate_circuit_breakers
from risk.correlation_monitor import correlation_alerts
from risk.factor_monitor import factor_spread_alerts
from risk.factor_risk_model import build_factor_risk_model
from risk.pre_trade import Trade, check_pre_trade
from risk.stress_test import stress_test_portfolio
from risk.tail_risk import gross_exposure_multiplier
from run_risk_check import run_risk_pipeline


def test_pre_trade_halt_veto_allows_closing(tmp_path: Path) -> None:
    lock = tmp_path / 'halt.lock'
    lock.write_text('halted', encoding='utf-8')
    blocked = check_pre_trade(Trade('AAPL', 'buy', 10, 100, weight=0.01), halt_lock=lock)
    assert blocked.approved is False
    assert 'halt_lock_active' in blocked.reasons
    closing = check_pre_trade(Trade('AAPL', 'cover', 10, 100, weight=-0.01), halt_lock=lock)
    assert closing.approved is True


def test_circuit_breaker_lock_and_clear(tmp_path: Path) -> None:
    lock = tmp_path / 'halt.lock'
    result = evaluate_circuit_breakers({'daily_pnl_pct': -0.04, 'weekly_pnl_pct': 0, 'drawdown_pct': 0}, lock_file=lock)
    assert result['halted'] is True
    assert lock.exists()
    assert clear_halt(lock) is True
    assert not lock.exists()


def test_stress_test_outputs_requested_scenarios() -> None:
    portfolio = pd.DataFrame({'ticker': ['A', 'B'], 'weight': [0.5, -0.5], 'beta': [1.1, 0.9], 'side': ['long', 'short']})
    result = stress_test_portfolio(portfolio)
    assert {'2008_crisis', 'short_squeeze'}.issubset(set(result['scenario']))
    assert 'estimated_pnl' in result.columns


def test_factor_risk_model_outputs_covariance_decomposition_and_mctr() -> None:
    asset_returns = pd.DataFrame(
        {
            'AAA': [0.01, 0.02, -0.01, 0.00, 0.015],
            'BBB': [-0.005, -0.01, 0.015, 0.01, -0.002],
            'CCC': [0.004, 0.006, -0.003, 0.002, 0.001],
        },
        index=pd.date_range('2026-01-01', periods=5),
    )
    exposures = pd.DataFrame(
        {
            'momentum': [90, 15, 60],
            'value': [40, 85, 50],
            'quality': [80, 20, 55],
        },
        index=['AAA', 'BBB', 'CCC'],
    )
    weights = pd.Series({'AAA': 0.04, 'BBB': -0.03, 'CCC': 0.02})

    model = build_factor_risk_model(asset_returns, exposures, weights=weights)

    assert set(model) >= {'factor_returns', 'factor_covariance', 'specific_variance', 'predicted_covariance', 'risk_decomposition', 'mctr'}
    assert model['predicted_covariance'].shape == (3, 3)
    assert model['risk_decomposition']['total_variance'] >= 0
    assert {'weight', 'mctr', 'mctr_pct', 'mctr_flag'}.issubset(model['mctr'].columns)


def test_pre_trade_runs_eight_absolute_vetoes_and_logs_rejections(tmp_path: Path) -> None:
    lock = tmp_path / 'halt.lock'
    lock.write_text('halted', encoding='utf-8')
    log_path = tmp_path / 'rejections.jsonl'
    portfolio = pd.DataFrame(
        {
            'ticker': ['AAA', 'BBB'],
            'weight': [0.90, 0.70],
            'sector': ['Technology', 'Technology'],
            'beta': [0.25, 0.20],
        }
    )

    decision = check_pre_trade(
        Trade('CCC', 'buy', 10_000, 100, weight=0.06, sector='Technology', dollar_adv=1_000_000, beta=1.0, earnings_date='2026-05-09'),
        portfolio=portfolio,
        halt_lock=lock,
        pairwise_correlations={'AAA': 0.91},
        config={'risk': {'liquidity': {'min_dollar_adv': 10_000_000, 'max_trade_adv_pct': 0.05}}, 'portfolio': {}},
        log_path=log_path,
        as_of='2026-05-07',
    )

    assert decision.approved is False
    assert {
        'halt_lock_active',
        'earnings_blackout',
        'liquidity_min_adv',
        'liquidity_trade_adv_pct',
        'max_position_weight',
        'sector_cap',
        'gross_exposure',
        'net_exposure',
        'net_beta',
        'pairwise_correlation',
    }.issubset(set(decision.reasons))
    assert decision.checks['halt_lock_active'] is False
    log_entry = json.loads(log_path.read_text(encoding='utf-8').splitlines()[0])
    assert log_entry['ticker'] == 'CCC'
    assert 'pairwise_correlation' in log_entry['reasons']


def test_circuit_breakers_match_prompt_thresholds(tmp_path: Path) -> None:
    lock = tmp_path / 'halt.lock'
    config = {'risk': {'circuit_breakers': {'lock_file': str(lock)}, 'max_single_name_nav_loss': 0.03}}

    soft = evaluate_circuit_breakers({'daily_pnl_pct': -0.016, 'weekly_pnl_pct': 0, 'drawdown_pct': 0}, config=config, lock_file=lock)
    assert 'size_down_30' in soft['actions']
    assert soft['halted'] is False

    hard = evaluate_circuit_breakers({'daily_pnl_pct': -0.026, 'weekly_pnl_pct': 0, 'drawdown_pct': 0}, config=config, lock_file=lock)
    assert 'close_all_today' in hard['actions']
    assert hard['halted'] is True

    clear_halt(lock)
    drawdown = evaluate_circuit_breakers({'daily_pnl_pct': 0, 'weekly_pnl_pct': 0, 'drawdown_pct': -0.081, 'single_position_loss_pct': -0.031}, config=config, lock_file=lock)
    assert {'kill_switch', 'force_close_single_position'}.issubset(set(drawdown['actions']))


def test_factor_and_correlation_monitors_raise_prompt_alerts() -> None:
    alerts = factor_spread_alerts(pd.Series({'momentum': 1.6, 'value': 0.4}), crowding_warnings=[{'factor': 'momentum'}])
    assert alerts == [{'factor': 'momentum', 'zscore': 1.6, 'crowded': True, 'priority': 'HIGH'}]

    returns = pd.DataFrame(
        {
            'AAA': [0.01, 0.02, 0.03, 0.01],
            'BBB': [0.011, 0.021, 0.029, 0.012],
            'CCC': [-0.01, 0.00, 0.02, -0.01],
        }
    )
    corr_alerts = correlation_alerts(returns, {'long': ['AAA', 'BBB'], 'short': ['CCC']}, threshold=0.60)
    assert corr_alerts[0]['book'] == 'long'
    assert corr_alerts[0]['average_correlation'] > 0.60
    assert corr_alerts[0]['effective_bets'] > 0


def test_tail_risk_uses_prompt_reduction_levels() -> None:
    assert gross_exposure_multiplier(vix=24.9) == 1.0
    assert gross_exposure_multiplier(vix=25.0) == 0.80
    assert gross_exposure_multiplier(vix=35.0) == 0.50
    assert gross_exposure_multiplier(credit_spread_z=1.0) == 0.80


def test_stress_test_reports_dollars_and_percent_by_book() -> None:
    portfolio = pd.DataFrame({'ticker': ['A', 'B'], 'weight': [0.5, -0.5], 'beta': [1.1, 0.9], 'side': ['long', 'short']})
    result = stress_test_portfolio(portfolio, nav=1_000_000)
    assert {'long_pnl_usd', 'short_pnl_usd', 'estimated_pnl_usd', 'estimated_pnl_pct'}.issubset(result.columns)
    assert result.loc[result['scenario'] == 'short_squeeze', 'short_pnl_usd'].iloc[0] < 0


def test_run_risk_pipeline_writes_risk_state(tmp_path: Path) -> None:
    state_path = tmp_path / 'risk_state.json'
    lock = tmp_path / 'halt.lock'
    portfolio = pd.DataFrame(
        {
            'ticker': ['AAA', 'BBB'],
            'weight': [0.5, -0.5],
            'beta': [1.1, 0.9],
            'side': ['long', 'short'],
            'momentum_score': [80, 20],
            'quality_score': [70, 35],
        }
    )

    payload = run_risk_pipeline(
        portfolio,
        config={'risk': {'circuit_breakers': {'lock_file': str(lock)}}},
        state_path=state_path,
        daily_pnl_pct=-0.016,
        weekly_pnl_pct=0,
        drawdown_pct=0,
        include_stress=True,
        vix=25,
    )

    assert payload['tail_multiplier'] == 0.80
    assert 'size_down_30' in payload['circuit_breakers']['actions']
    persisted = json.loads(state_path.read_text(encoding='utf-8'))
    assert {'daily_pnl_pct', 'factor_exposures', 'risk_decomposition', 'alerts', 'top_mctr_positions'}.issubset(persisted)


def test_broker_live_guard_blocks_live_without_ack() -> None:
    with pytest.raises(BrokerConfigError):
        validate_live_guard('live', allow_live_trading=True, risk_acknowledgement=False)


def test_executor_dry_run_returns_no_side_effect_order() -> None:
    trades = [Trade('AAPL', 'buy', 1, 100, weight=0.01, dollar_adv=100_000_000)]
    result = execute_trades(trades, dry_run=True)
    assert result[0]['status'] == 'dry_run'
    assert result[0]['ticker'] == 'AAPL'


def test_slippage_stats() -> None:
    assert slippage_bps(100, 101, 'buy') == 100.0
    fills = pd.DataFrame({'signal_price': [100, 100], 'fill_price': [101, 99], 'side': ['buy', 'sell'], 'quantity': [10, 10]})
    summary = slippage_summary(fills)
    assert summary['average_bps'] == 100.0
    assert summary['total_dollar_cost'] > 0


def test_daily_lp_letter_created_under_project() -> None:
    path = generate_daily_lp_letter('Paper book was flat today.', path='output/reports/test_lp_letter.md')
    assert path.exists()
    text = path.read_text(encoding='utf-8')
    assert 'CONFIDENTIAL' in text
    assert 'Not investment advice' in text
