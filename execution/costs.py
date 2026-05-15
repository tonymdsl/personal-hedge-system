"""Execution slippage analytics."""
from __future__ import annotations

import numpy as np
import pandas as pd


def slippage_bps(signal_price: float, fill_price: float, side: str) -> float:
    if signal_price <= 0:
        return 0.0
    side_l = side.lower()
    if side_l in {'buy', 'cover'}:
        return ((fill_price - signal_price) / signal_price) * 10_000
    return ((signal_price - fill_price) / signal_price) * 10_000


def slippage_summary(fills: pd.DataFrame) -> dict[str, float]:
    if fills.empty:
        return {'average_bps': 0.0, 'median_bps': 0.0, 'p95_bps': 0.0, 'total_dollar_cost': 0.0}
    df = fills.copy()
    if 'slippage_bps' not in df.columns:
        df['slippage_bps'] = [slippage_bps(r.signal_price, r.fill_price, r.side) for r in df.itertuples()]
    bps = pd.to_numeric(df['slippage_bps'], errors='coerce').fillna(0.0)
    notional = (pd.to_numeric(df.get('quantity', 0), errors='coerce').abs() * pd.to_numeric(df.get('signal_price', 0), errors='coerce')).fillna(0.0)
    dollar_cost = (bps / 10_000.0) * notional
    return {'average_bps': float(bps.mean()), 'median_bps': float(bps.median()), 'p95_bps': float(np.percentile(bps, 95)), 'total_dollar_cost': float(dollar_cost.sum())}


def _with_slippage(fills: pd.DataFrame) -> pd.DataFrame:
    df = fills.copy()
    if df.empty:
        return df
    if 'slippage_bps' not in df.columns:
        df['slippage_bps'] = [slippage_bps(float(r.signal_price), float(r.fill_price), str(r.side)) for r in df.itertuples()]
    return df


def slippage_rolling_summary(fills: pd.DataFrame, *, as_of: str | pd.Timestamp | None = None, window_days: int = 30) -> dict[str, float]:
    if fills.empty:
        return slippage_summary(fills)
    df = _with_slippage(fills)
    if 'timestamp' in df.columns:
        dates = pd.to_datetime(df['timestamp'], errors='coerce')
        end = pd.Timestamp(as_of) if as_of is not None else dates.max()
        start = end - pd.Timedelta(days=window_days)
        df = df.loc[(dates >= start) & (dates <= end)]
    return slippage_summary(df)


def worst_fills(fills: pd.DataFrame, *, n: int = 5) -> pd.DataFrame:
    if fills.empty:
        return fills.copy()
    df = _with_slippage(fills)
    return df.sort_values('slippage_bps', ascending=False).head(n).reset_index(drop=True)
