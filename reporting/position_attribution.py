"""Position-level mark-to-market and FIFO attribution."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass
class Lot:
    ticker: str
    side: str
    quantity: float
    price: float
    date: pd.Timestamp
    entry_score: float | None = None
    sector: str | None = None
    vix_regime: str | None = None
    factor_quintile: int | None = None


def fifo_round_trips(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=_ROUND_TRIP_COLUMNS)
    frame = trades.copy()
    frame['_date'] = pd.to_datetime(frame['date'], errors='coerce')
    frame = frame.sort_values('_date')
    long_lots: dict[str, list[Lot]] = defaultdict(list)
    short_lots: dict[str, list[Lot]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict(orient='records'):
        ticker = str(record.get('ticker', '')).upper()
        side = str(record.get('side', '')).lower()
        quantity = abs(_float(record.get('quantity')))
        price = _float(record.get('price'))
        trade_date = pd.Timestamp(record.get('_date'))
        if not ticker or quantity <= 0 or price <= 0 or pd.isna(trade_date):
            continue
        if side in {'buy', 'cover'} and short_lots[ticker]:
            rows.extend(_close_lots(short_lots[ticker], ticker, 'short', quantity, price, trade_date))
        elif side in {'sell'} and long_lots[ticker]:
            rows.extend(_close_lots(long_lots[ticker], ticker, 'long', quantity, price, trade_date))
        elif side in {'short', 'sell_short'}:
            short_lots[ticker].append(_lot(record, ticker, 'short', quantity, price, trade_date))
        elif side in {'buy'}:
            long_lots[ticker].append(_lot(record, ticker, 'long', quantity, price, trade_date))
        elif side in {'sell'}:
            short_lots[ticker].append(_lot(record, ticker, 'short', quantity, price, trade_date))
    return pd.DataFrame(rows, columns=_ROUND_TRIP_COLUMNS)


def position_attribution_summary(round_trips: pd.DataFrame) -> dict[str, Any]:
    if round_trips.empty:
        return {
            'best_long': {},
            'worst_long': {},
            'best_short': {},
            'worst_short': {},
            'spearman_entry_score_realized_return': 0.0,
        }
    frame = round_trips.copy()
    frame['_pnl'] = pd.to_numeric(frame['realized_pnl'], errors='coerce').fillna(0.0)
    summary = {
        'best_long': _extreme(frame, 'long', True),
        'worst_long': _extreme(frame, 'long', False),
        'best_short': _extreme(frame, 'short', True),
        'worst_short': _extreme(frame, 'short', False),
        'spearman_entry_score_realized_return': _spearman(frame),
    }
    return summary


def _close_lots(lots: list[Lot], ticker: str, side: str, quantity: float, exit_price: float, exit_date: pd.Timestamp) -> list[dict[str, Any]]:
    remaining = quantity
    rows: list[dict[str, Any]] = []
    while remaining > 1e-9 and lots:
        lot = lots[0]
        qty = min(remaining, lot.quantity)
        pnl = (exit_price - lot.price) * qty if side == 'long' else (lot.price - exit_price) * qty
        notional = lot.price * qty
        rows.append(
            {
                'ticker': ticker,
                'side': side,
                'quantity': qty,
                'entry_date': lot.date.date().isoformat(),
                'exit_date': exit_date.date().isoformat(),
                'entry_price': lot.price,
                'exit_price': exit_price,
                'realized_pnl': round(float(pnl), 10),
                'realized_return': round(float(pnl / notional), 10) if notional else 0.0,
                'holding_days': int((exit_date - lot.date).days),
                'entry_score': lot.entry_score,
                'sector': lot.sector,
                'vix_regime': lot.vix_regime,
                'factor_quintile': lot.factor_quintile,
            }
        )
        lot.quantity -= qty
        remaining -= qty
        if lot.quantity <= 1e-9:
            lots.pop(0)
    return rows


def _lot(record: dict[str, Any], ticker: str, side: str, quantity: float, price: float, trade_date: pd.Timestamp) -> Lot:
    return Lot(
        ticker=ticker,
        side=side,
        quantity=quantity,
        price=price,
        date=trade_date,
        entry_score=_optional_float(record.get('entry_score')),
        sector=_optional_str(record.get('sector')),
        vix_regime=_optional_str(record.get('vix_regime')),
        factor_quintile=_optional_int(record.get('factor_quintile')),
    )


def _extreme(frame: pd.DataFrame, side: str, best: bool) -> dict[str, Any]:
    subset = frame[frame['side'].astype(str).str.lower() == side]
    if subset.empty:
        return {}
    row = subset.sort_values('_pnl', ascending=not best).iloc[0]
    return {key: _jsonable(value) for key, value in row.drop(labels=['_pnl'], errors='ignore').items()}


def _spearman(frame: pd.DataFrame) -> float:
    scores = pd.to_numeric(frame.get('entry_score'), errors='coerce')
    realized = pd.to_numeric(frame.get('realized_return'), errors='coerce')
    valid = pd.DataFrame({'score': scores, 'realized': realized}).dropna()
    if len(valid) < 2:
        return 0.0
    return round(float(valid['score'].corr(valid['realized'], method='spearman')), 10)


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(result) else result


def _optional_int(value: object) -> int | None:
    number = _optional_float(value)
    return int(number) if number is not None else None


def _optional_str(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


def _jsonable(value: object) -> object:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


_ROUND_TRIP_COLUMNS = [
    'ticker',
    'side',
    'quantity',
    'entry_date',
    'exit_date',
    'entry_price',
    'exit_price',
    'realized_pnl',
    'realized_return',
    'holding_days',
    'entry_score',
    'sector',
    'vix_regime',
    'factor_quintile',
]
