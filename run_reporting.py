"""Layer 7 reporting command."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Mapping

import pandas as pd

from common.cli import add_common_arguments
from common.config import PROJECT_ROOT, load_config
from reporting.commentary import generate_weekly_commentary, should_generate_weekly_commentary
from reporting.letter import generate_daily_lp_letter
from reporting.performance import generate_institutional_tear_sheet, turnover_analytics
from reporting.pnl_attribution import daily_pnl_attribution


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Meridian Layer 7 reporting.')
    add_common_arguments(parser)
    parser.add_argument('--letter', action='store_true')
    parser.add_argument('--tear-sheet', action='store_true')
    parser.add_argument('--weekly-commentary', action='store_true')
    parser.add_argument('--positions-input', default='output/portfolio_positions_latest.csv')
    parser.add_argument('--returns-input', default='output/daily_returns_latest.csv')
    parser.add_argument('--trades-input', default='output/execution_trades_latest.csv')
    parser.add_argument('--output-dir', default='output')
    parser.add_argument('--nav', type=float, default=1_000_000.0)
    return parser


def run_reporting_pipeline(
    *,
    positions: pd.DataFrame,
    returns: pd.DataFrame | pd.Series,
    trades: pd.DataFrame,
    output_dir: str | Path = 'output',
    as_of: date | None = None,
    nav: float = 1_000_000.0,
    config: Mapping[str, object] | None = None,
) -> dict[str, str]:
    as_of = as_of or date.today()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    reports = output / 'reports'
    reports.mkdir(parents=True, exist_ok=True)

    returns_series = _returns_series(returns)
    attribution_path = output / 'daily_attribution.csv'
    attribution = daily_pnl_attribution(
        positions,
        returns_series,
        attribution_date=as_of.isoformat(),
        spy_return=_spy_return(returns),
        sector_returns=_sector_returns(returns),
        output_path=attribution_path,
    )

    turnover = turnover_analytics(
        trades,
        nav=nav,
        as_of=as_of,
        budget=_turnover_budget(config),
    )
    tear_path = generate_institutional_tear_sheet(
        metrics={
            'fund_return': float(attribution.iloc[-1]['total_return']) if not attribution.empty else 0.0,
            'spy_return': _spy_return(returns),
            'alpha_residual': float(attribution.iloc[-1]['alpha_residual']) if not attribution.empty else 0.0,
            'rolling_12mo_sharpe': 'N/A',
        },
        monthly_returns=_monthly_returns_frame(returns),
        equity_curve=_equity_curve_frame(as_of, nav, attribution),
        factor_exposures=_exposures(positions, '_score'),
        sector_exposures=_sector_exposures(positions),
        turnover=turnover,
        path=reports / f'tear_sheet_{as_of.isoformat()}.md',
    )

    letter_path = generate_daily_lp_letter(
        _letter_paragraphs(attribution, turnover),
        letter_date=as_of,
        path=reports / f'lp_letter_{as_of.isoformat()}.md',
    )
    summary_path = output / 'reporting_summary.json'
    summary = {
        'reporting_layer': 'L7',
        'as_of': as_of.isoformat(),
        'daily_attribution': str(attribution_path),
        'tear_sheet': str(tear_path),
        'daily_letter': str(letter_path),
        'turnover': turnover,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding='utf-8')

    payload = {
        'daily_attribution': str(attribution_path),
        'tear_sheet': str(tear_path),
        'daily_letter': str(letter_path),
        'summary': str(summary_path),
    }
    if should_generate_weekly_commentary(as_of, config):
        payload['weekly_commentary'] = str(generate_weekly_commentary(summary, as_of=as_of, path=reports / f'weekly_commentary_{as_of.isoformat()}.md'))
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    positions = _read_frame(args.positions_input)
    returns = _read_frame(args.returns_input)
    trades = _read_frame(args.trades_input)
    payload = run_reporting_pipeline(
        positions=positions,
        returns=returns,
        trades=trades,
        output_dir=_resolve_output_dir(args.output_dir),
        as_of=date.today(),
        nav=args.nav,
        config=config,
    )
    requested = []
    if args.letter:
        requested.append(payload['daily_letter'])
    if args.tear_sheet:
        requested.append(payload['tear_sheet'])
    if args.weekly_commentary and 'weekly_commentary' in payload:
        requested.append(payload['weekly_commentary'])
    payload['outputs'] = requested or list(payload.values())
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def _resolve_output_dir(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _read_frame(path: str | Path) -> pd.DataFrame:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    if not candidate.exists():
        return pd.DataFrame()
    return pd.read_csv(candidate)


def _returns_series(returns: pd.DataFrame | pd.Series) -> pd.Series:
    if isinstance(returns, pd.Series):
        return returns
    if {'ticker', 'return'}.issubset(returns.columns):
        return pd.to_numeric(returns.set_index(returns['ticker'].astype(str))['return'], errors='coerce')
    if returns.empty:
        return pd.Series(dtype=float)
    return pd.to_numeric(returns.iloc[-1], errors='coerce')


def _spy_return(returns: pd.DataFrame | pd.Series) -> float:
    series = _returns_series(returns)
    for ticker in ('SPY', '^SPY'):
        if ticker in series.index:
            return float(series.loc[ticker])
    return 0.0


def _sector_returns(returns: pd.DataFrame | pd.Series) -> dict[str, float]:
    if not isinstance(returns, pd.DataFrame) or not {'sector', 'return'}.issubset(returns.columns):
        return {}
    return pd.to_numeric(returns.groupby('sector')['return'].mean(), errors='coerce').dropna().to_dict()


def _turnover_budget(config: Mapping[str, object] | None) -> float:
    portfolio = (config or {}).get('portfolio', {}) if isinstance(config, Mapping) else {}
    if isinstance(portfolio, Mapping):
        return float(portfolio.get('turnover_budget_per_rebalance', 0.30) or 0.30)
    return 0.30


def _monthly_returns_frame(returns: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(returns, pd.DataFrame) and {'date', 'return'}.issubset(returns.columns):
        frame = returns.copy()
        frame['month'] = pd.to_datetime(frame['date'], errors='coerce').dt.to_period('M').astype(str)
        return frame.groupby('month', as_index=False)['return'].sum()
    return pd.DataFrame(columns=['month', 'return'])


def _equity_curve_frame(as_of: date, nav: float, attribution: pd.DataFrame) -> pd.DataFrame:
    daily_return = float(attribution.iloc[-1]['total_return']) if not attribution.empty else 0.0
    end_nav = nav * (1.0 + daily_return)
    return pd.DataFrame({'date': [as_of.isoformat()], 'nav': [round(end_nav, 2)], 'drawdown': [min(0.0, daily_return)]})


def _exposures(positions: pd.DataFrame, suffix: str) -> dict[str, float]:
    if positions.empty or 'weight' not in positions.columns:
        return {}
    weights = pd.to_numeric(positions['weight'], errors='coerce').fillna(0.0)
    gross = float(weights.abs().sum()) or 1.0
    values: dict[str, float] = {}
    for column in positions.columns:
        if str(column).endswith(suffix):
            values[str(column).removesuffix(suffix)] = float((weights * pd.to_numeric(positions[column], errors='coerce').fillna(0.0)).sum() / gross)
    return values


def _sector_exposures(positions: pd.DataFrame) -> dict[str, float]:
    if positions.empty or 'sector' not in positions.columns or 'weight' not in positions.columns:
        return {}
    return pd.to_numeric(positions.groupby('sector')['weight'].sum(), errors='coerce').fillna(0.0).to_dict()


def _letter_paragraphs(attribution: pd.DataFrame, turnover: Mapping[str, object]) -> list[str]:
    total = float(attribution.iloc[-1]['total_return']) if not attribution.empty else 0.0
    alpha = float(attribution.iloc[-1]['alpha_residual']) if not attribution.empty else 0.0
    return [
        f'Today, the paper portfolio return was {total:.2%}, with alpha residual of {alpha:.2%} after local beta, sector, and factor attribution.',
        f'Trailing 30-day turnover is {float(turnover.get("trailing_30_turnover", 0.0)):.2%} versus the configured budget of {float(turnover.get("budget", 0.0)):.2%}.',
        'The reporting package was generated from local project artifacts and should be read as research context for the paper book.',
    ]


if __name__ == '__main__':
    raise SystemExit(main())
