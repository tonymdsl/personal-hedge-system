"""Performance, turnover, and tear-sheet helpers."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Mapping

import pandas as pd

from common.config import PROJECT_ROOT, ensure_project_path


def win_loss_summary(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty or 'pnl' not in trades.columns:
        return {'win_rate': 0.0, 'pl_ratio': 0.0}
    pnl = pd.to_numeric(trades['pnl'], errors='coerce').dropna()
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    return {'win_rate': float((pnl > 0).mean()) if len(pnl) else 0.0, 'pl_ratio': float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else 0.0}


def win_loss_analysis(round_trips: pd.DataFrame) -> dict[str, object]:
    frame = round_trips.copy()
    if frame.empty:
        return {'overall': {'win_rate': 0.0, 'pl_ratio': 0.0, 'trades': 0}, 'by_side': {}, 'by_holding_period': {}, 'by_sector': {}, 'by_vix_regime': {}, 'by_factor_quintile': {}, 'streaks': {'max_win_streak': 0, 'max_loss_streak': 0}}
    frame['realized_pnl'] = pd.to_numeric(frame.get('realized_pnl'), errors='coerce').fillna(0.0)
    frame['holding_period_bucket'] = pd.to_numeric(frame.get('holding_days'), errors='coerce').fillna(0).map(_holding_bucket)
    return {
        'overall': _group_metrics(frame),
        'by_side': _slice_metrics(frame, 'side'),
        'by_holding_period': _slice_metrics(frame, 'holding_period_bucket'),
        'by_sector': _slice_metrics(frame, 'sector'),
        'by_vix_regime': _slice_metrics(frame, 'vix_regime'),
        'by_factor_quintile': _slice_metrics(frame, 'factor_quintile'),
        'streaks': _streaks(frame['realized_pnl']),
    }


def turnover(trades: pd.DataFrame, nav: float) -> float:
    if trades.empty or nav <= 0:
        return 0.0
    if 'notional' in trades.columns:
        notional = pd.to_numeric(trades['notional'], errors='coerce').abs().fillna(0.0)
    else:
        notional = pd.to_numeric(trades.get('quantity', 0), errors='coerce').abs() * pd.to_numeric(trades.get('price', 0), errors='coerce')
    return float(notional.sum() / nav)


def turnover_analytics(trades: pd.DataFrame, *, nav: float, as_of: str | date | pd.Timestamp, budget: float) -> dict[str, float]:
    if trades.empty or nav <= 0:
        return {'trailing_30_turnover': 0.0, 'trailing_90_turnover': 0.0, 'annualized_30_turnover': 0.0, 'annualized_90_turnover': 0.0, 'budget': float(budget), 'tax_estimate': 0.0}
    frame = trades.copy()
    frame['date'] = pd.to_datetime(frame['date'], errors='coerce')
    end = pd.Timestamp(as_of)
    trailing_30 = _turnover_window(frame, nav, end, 30)
    trailing_90 = _turnover_window(frame, nav, end, 90)
    gains = pd.to_numeric(frame.get('realized_pnl'), errors='coerce').fillna(0.0).clip(lower=0)
    holding = pd.to_numeric(frame.get('holding_days'), errors='coerce').fillna(0.0)
    tax = float((gains[holding < 365] * 0.37).sum() + (gains[holding >= 365] * 0.20).sum())
    return {
        'trailing_30_turnover': round(trailing_30, 10),
        'trailing_90_turnover': round(trailing_90, 10),
        'annualized_30_turnover': trailing_30 * 365.0 / 30.0,
        'annualized_90_turnover': trailing_90 * 365.0 / 90.0,
        'budget': float(budget),
        'tax_estimate': round(tax, 10),
    }


def generate_tear_sheet(metrics: dict[str, object], path: str | Path = 'output/reports/tear_sheet.md') -> Path:
    output = ensure_project_path(path, PROJECT_ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ['# Meridian Capital Partners Tear Sheet', '', '| Metric | Value |', '|---|---|']
    for key, value in metrics.items():
        lines.append(f'| {key} | {value} |')
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return output


def generate_institutional_tear_sheet(
    *,
    metrics: Mapping[str, object],
    monthly_returns: pd.DataFrame,
    equity_curve: pd.DataFrame,
    factor_exposures: Mapping[str, object],
    sector_exposures: Mapping[str, object],
    turnover: Mapping[str, object],
    path: str | Path = 'output/reports/tear_sheet.md',
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '# Meridian Capital Partners Tear Sheet',
        '',
        '## Metrics vs SPY',
        _mapping_table(metrics),
        '',
        '## Monthly Returns',
        _frame_table(monthly_returns),
        '',
        '## Equity Curve',
        _frame_table(equity_curve[['date', 'nav']] if {'date', 'nav'}.issubset(equity_curve.columns) else equity_curve),
        '',
        '## Drawdown',
        _frame_table(equity_curve[['date', 'drawdown']] if {'date', 'drawdown'}.issubset(equity_curve.columns) else pd.DataFrame()),
        '',
        '## Rolling 12mo Sharpe',
        str(metrics.get('rolling_12mo_sharpe', 'N/A')),
        '',
        '## Factor Exposures',
        _mapping_table(factor_exposures),
        '',
        '## Sector Exposures',
        _mapping_table(sector_exposures),
        '',
        '## Turnover',
        _mapping_table(turnover),
    ]
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return output


def _group_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    pnl = pd.to_numeric(frame['realized_pnl'], errors='coerce').fillna(0.0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    return {
        'trades': int(len(pnl)),
        'win_rate': float((pnl > 0).mean()) if len(pnl) else 0.0,
        'pl_ratio': float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else 0.0,
    }


def _slice_metrics(frame: pd.DataFrame, column: str) -> dict[str, dict[str, float | int]]:
    if column not in frame.columns:
        return {}
    return {str(key): _group_metrics(group) for key, group in frame.groupby(column, dropna=True)}


def _holding_bucket(days: float) -> str:
    if days <= 5:
        return '1-5d'
    if days <= 20:
        return '5-20d'
    if days <= 60:
        return '20-60d'
    return '60d+'


def _streaks(pnl: pd.Series) -> dict[str, int]:
    max_win = max_loss = current_win = current_loss = 0
    for value in pnl:
        if value > 0:
            current_win += 1
            current_loss = 0
        elif value < 0:
            current_loss += 1
            current_win = 0
        else:
            current_win = current_loss = 0
        max_win = max(max_win, current_win)
        max_loss = max(max_loss, current_loss)
    return {'max_win_streak': max_win, 'max_loss_streak': max_loss}


def _turnover_window(frame: pd.DataFrame, nav: float, end: pd.Timestamp, days: int) -> float:
    start = end - pd.Timedelta(days=days)
    subset = frame.loc[(frame['date'] >= start) & (frame['date'] <= end)]
    return turnover(subset, nav)


def _mapping_table(values: Mapping[str, object]) -> str:
    lines = ['| Metric | Value |', '|---|---|']
    for key, value in values.items():
        lines.append(f'| {key} | {value} |')
    return '\n'.join(lines)


def _frame_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return '_No local data._'
    display = frame.astype(object).where(pd.notna(frame), '')
    headers = [str(column) for column in display.columns]
    lines = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    for row in display.to_dict(orient='records'):
        lines.append('| ' + ' | '.join(str(row[column]) for column in display.columns) + ' |')
    return '\n'.join(lines)
