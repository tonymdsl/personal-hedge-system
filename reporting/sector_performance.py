"""Sector-relative stock selection performance."""
from __future__ import annotations

import pandas as pd


def sector_relative_performance(
    picks: pd.DataFrame,
    sector_etf_returns: pd.DataFrame,
    *,
    as_of: str | pd.Timestamp,
    lookback_days: int = 90,
) -> dict[str, object]:
    if picks.empty or sector_etf_returns.empty:
        return {'by_sector': pd.DataFrame(columns=['sector', 'pick_return', 'sector_return', 'selection_alpha']), 'total_alpha': 0.0, 'winner_sector_count': 0, 'loser_sector_count': 0}
    start = pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)
    end = pd.Timestamp(as_of)
    pick_frame = _window(picks, start, end)
    etf_frame = _window(sector_etf_returns, start, end)
    if pick_frame.empty or etf_frame.empty:
        return {'by_sector': pd.DataFrame(columns=['sector', 'pick_return', 'sector_return', 'selection_alpha']), 'total_alpha': 0.0, 'winner_sector_count': 0, 'loser_sector_count': 0}
    pick_summary = pick_frame.groupby('sector', dropna=False)['return'].mean().rename('pick_return')
    etf_summary = etf_frame.groupby('sector', dropna=False)['return'].mean().rename('sector_return')
    by_sector = pd.concat([pick_summary, etf_summary], axis=1).dropna().reset_index()
    by_sector['selection_alpha'] = (by_sector['pick_return'] - by_sector['sector_return']).round(10)
    return {
        'by_sector': by_sector,
        'total_alpha': round(float(by_sector['selection_alpha'].sum()), 10),
        'winner_sector_count': int((by_sector['selection_alpha'] > 0).sum()),
        'loser_sector_count': int((by_sector['selection_alpha'] < 0).sum()),
    }


def _window(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    result = frame.copy()
    result['date'] = pd.to_datetime(result['date'], errors='coerce')
    result['return'] = pd.to_numeric(result['return'], errors='coerce')
    return result.loc[(result['date'] >= start) & (result['date'] <= end)].dropna(subset=['sector', 'return'])
