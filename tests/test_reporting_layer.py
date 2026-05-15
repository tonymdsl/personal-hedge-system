from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

import run_reporting
from reporting.commentary import generate_weekly_commentary, should_generate_weekly_commentary
from reporting.letter import generate_daily_lp_letter
from reporting.performance import generate_institutional_tear_sheet, turnover_analytics, win_loss_analysis
from reporting.pnl_attribution import daily_pnl_attribution
from reporting.position_attribution import fifo_round_trips, position_attribution_summary
from reporting.sector_performance import sector_relative_performance


def test_daily_pnl_attribution_decomposes_and_persists(tmp_path: Path) -> None:
    positions = pd.DataFrame(
        {
            'ticker': ['AAA', 'BBB'],
            'weight': [0.5, -0.3],
            'beta': [1.0, 0.8],
            'sector': ['Tech', 'Energy'],
            'momentum_exposure': [0.2, -0.1],
        }
    )
    returns = pd.Series({'AAA': 0.02, 'BBB': -0.01})
    output = tmp_path / 'daily_attribution.csv'

    attribution = daily_pnl_attribution(
        positions,
        returns,
        attribution_date='2026-05-07',
        spy_return=0.01,
        sector_returns={'Tech': 0.015, 'Energy': -0.005},
        factor_returns={'momentum': 0.02},
        output_path=output,
    )

    row = attribution.iloc[0]
    assert row['total_return'] == 0.013
    assert round(row['beta_return'], 4) == 0.0026
    assert round(row['sector_return'], 4) == 0.0070
    assert round(row['factor_return'], 4) == 0.0026
    assert round(row['alpha_residual'], 4) == 0.0008
    assert output.exists()


def test_position_attribution_fifo_best_worst_and_predictive_power() -> None:
    trades = pd.DataFrame(
        [
            {'ticker': 'AAA', 'date': '2026-01-01', 'side': 'buy', 'quantity': 10, 'price': 100, 'entry_score': 80, 'sector': 'Tech'},
            {'ticker': 'AAA', 'date': '2026-01-05', 'side': 'sell', 'quantity': 10, 'price': 110},
            {'ticker': 'BBB', 'date': '2026-01-02', 'side': 'short', 'quantity': 10, 'price': 50, 'entry_score': 20, 'sector': 'Energy'},
            {'ticker': 'BBB', 'date': '2026-01-10', 'side': 'cover', 'quantity': 10, 'price': 55},
        ]
    )

    round_trips = fifo_round_trips(trades)
    summary = position_attribution_summary(round_trips)

    assert round_trips['realized_pnl'].tolist() == [100.0, -50.0]
    assert round_trips['holding_days'].tolist() == [4, 8]
    assert summary['best_long']['ticker'] == 'AAA'
    assert summary['worst_short']['ticker'] == 'BBB'
    assert summary['spearman_entry_score_realized_return'] == 1.0


def test_win_loss_analysis_slices_and_streaks() -> None:
    round_trips = pd.DataFrame(
        [
            {'ticker': 'AAA', 'side': 'long', 'realized_pnl': 100, 'holding_days': 4, 'sector': 'Tech', 'vix_regime': 'low', 'factor_quintile': 5},
            {'ticker': 'BBB', 'side': 'short', 'realized_pnl': -50, 'holding_days': 12, 'sector': 'Energy', 'vix_regime': 'high', 'factor_quintile': 1},
            {'ticker': 'CCC', 'side': 'long', 'realized_pnl': 25, 'holding_days': 45, 'sector': 'Tech', 'vix_regime': 'low', 'factor_quintile': 4},
        ]
    )

    analysis = win_loss_analysis(round_trips)

    assert analysis['overall']['win_rate'] == 2 / 3
    assert analysis['overall']['pl_ratio'] == 1.25
    assert analysis['by_side']['long']['win_rate'] == 1.0
    assert analysis['by_holding_period']['1-5d']['trades'] == 1
    assert analysis['by_sector']['Tech']['trades'] == 2
    assert analysis['by_vix_regime']['high']['win_rate'] == 0.0
    assert analysis['streaks']['max_win_streak'] == 1


def test_sector_relative_performance_computes_selection_alpha() -> None:
    picks = pd.DataFrame(
        [
            {'date': '2026-02-01', 'sector': 'Tech', 'return': 0.05},
            {'date': '2026-02-01', 'sector': 'Energy', 'return': -0.01},
        ]
    )
    sector_etfs = pd.DataFrame(
        [
            {'date': '2026-02-01', 'sector': 'Tech', 'return': 0.03},
            {'date': '2026-02-01', 'sector': 'Energy', 'return': 0.01},
        ]
    )

    result = sector_relative_performance(picks, sector_etfs, as_of='2026-05-01', lookback_days=90)

    assert result['total_alpha'] == 0.0
    assert result['winner_sector_count'] == 1
    assert result['loser_sector_count'] == 1
    assert result['by_sector'].set_index('sector').loc['Tech', 'selection_alpha'] == 0.02


def test_turnover_analytics_tracks_windows_budget_and_fifo_tax() -> None:
    trades = pd.DataFrame(
        [
            {'date': '2026-05-01', 'notional': 100_000, 'realized_pnl': 10_000, 'holding_days': 20},
            {'date': '2026-03-01', 'notional': 50_000, 'realized_pnl': 5_000, 'holding_days': 500},
        ]
    )

    analytics = turnover_analytics(trades, nav=1_000_000, as_of='2026-05-07', budget=0.30)

    assert analytics['trailing_30_turnover'] == 0.10
    assert analytics['trailing_90_turnover'] == 0.15
    assert round(analytics['annualized_30_turnover'], 3) == 1.217
    assert analytics['budget'] == 0.30
    assert analytics['tax_estimate'] == 4_700


def test_institutional_tear_sheet_contains_required_sections(tmp_path: Path) -> None:
    path = tmp_path / 'tear_sheet.md'

    output = generate_institutional_tear_sheet(
        metrics={'fund_return': 0.12, 'spy_return': 0.08},
        monthly_returns=pd.DataFrame({'month': ['2026-01'], 'return': [0.02]}),
        equity_curve=pd.DataFrame({'date': ['2026-01-31'], 'nav': [1_020_000], 'drawdown': [-0.01]}),
        factor_exposures={'momentum': 0.2},
        sector_exposures={'Tech': 0.3},
        turnover={'trailing_30_turnover': 0.1},
        path=path,
    )

    text = output.read_text(encoding='utf-8')
    assert 'Metrics vs SPY' in text
    assert 'Monthly Returns' in text
    assert 'Equity Curve' in text
    assert 'Drawdown' in text
    assert 'Rolling 12mo Sharpe' in text
    assert 'Factor Exposures' in text
    assert 'Sector Exposures' in text
    assert 'Turnover' in text


def test_weekly_commentary_and_daily_letter_outputs(tmp_path: Path) -> None:
    assert should_generate_weekly_commentary(date(2026, 5, 8), {'reporting': {'weekly_commentary_day': 'Friday'}})
    assert not should_generate_weekly_commentary(date(2026, 5, 7), {'reporting': {'weekly_commentary_day': 'Friday'}})

    commentary = generate_weekly_commentary({'weekly_return': 0.01}, as_of=date(2026, 5, 8), path=tmp_path / 'weekly.md')
    assert 'JARVIS Weekly Commentary' in commentary.read_text(encoding='utf-8')

    letter = generate_daily_lp_letter(
        ['Paragraph one.', 'Paragraph two.', 'Paragraph three.'],
        letter_date=date(2026, 5, 7),
        path=tmp_path / 'letter.md',
    )
    text = letter.read_text(encoding='utf-8')
    assert text.count('\n\nParagraph') == 3
    assert 'Meridian Capital Partners Research Desk' in text
    assert 'Not investment advice' in text


def test_run_reporting_pipeline_writes_l7_outputs(tmp_path: Path) -> None:
    positions = pd.DataFrame({'ticker': ['AAA'], 'weight': [1.0], 'beta': [1.0], 'sector': ['Tech']})
    returns = pd.DataFrame({'ticker': ['AAA'], 'return': [0.01]})
    trades = pd.DataFrame({'date': ['2026-05-01'], 'notional': [10_000], 'realized_pnl': [100], 'holding_days': [5]})

    payload = run_reporting.run_reporting_pipeline(
        positions=positions,
        returns=returns,
        trades=trades,
        output_dir=tmp_path,
        as_of=date(2026, 5, 7),
        nav=1_000_000,
        config={'reporting': {'daily_lp_letter': {'enabled': True}}},
    )

    assert Path(payload['daily_attribution']).exists()
    assert Path(payload['tear_sheet']).exists()
    assert Path(payload['daily_letter']).exists()
    assert json.loads(Path(payload['summary']).read_text(encoding='utf-8'))['reporting_layer'] == 'L7'
