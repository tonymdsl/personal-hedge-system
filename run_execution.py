"""Layer 6 paper execution command."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

import pandas as pd

from common.cli import add_common_arguments
from common.config import PROJECT_ROOT, load_config
from execution.executor import execute_trades
from risk.pre_trade import Trade


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Meridian Layer 6 paper execution.')
    add_common_arguments(parser)
    parser.add_argument('--execute', action='store_true', help='Submit paper orders instead of dry-run simulation.')
    parser.add_argument('--risk-acknowledgement', default='', help='Required exact phrase for live mode: YES I UNDERSTAND THE RISKS.')
    parser.add_argument('--orders-input', default='output/rebalance_orders_latest.csv')
    parser.add_argument('--orders-log', default='output/execution_orders.jsonl')
    parser.add_argument('--default-price', type=float, default=100.0)
    return parser


def _read_orders(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path
    if not input_path.exists():
        return pd.DataFrame()
    return pd.read_csv(input_path)


def _float(row: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = row.get(key, default)
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _has_number(row: Mapping[str, object], key: str) -> bool:
    value = row.get(key)
    try:
        if pd.isna(value):
            return False
    except TypeError:
        pass
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def trade_from_order_row(row: Mapping[str, object], *, default_price: float = 100.0) -> Trade:
    return trades_from_order_row(row, default_price=default_price)[0]


def trades_from_order_row(row: Mapping[str, object], *, default_price: float = 100.0) -> list[Trade]:
    ticker = str(row.get('ticker', '')).upper()
    price = _float(row, 'price', default_price)
    if price <= 0:
        price = default_price
    delta_notional = _float(row, 'delta_notional', 0.0)
    quantity = _float(row, 'shares', 0.0) or _float(row, 'quantity', 0.0)
    if quantity == 0 and delta_notional:
        quantity = abs(delta_notional) / price
    side = str(row.get('side', 'buy')).lower()
    target_weight = _float(row, 'target_weight', 0.0)
    current_weight = _float(row, 'current_weight', 0.0)
    delta_weight = _float(row, 'delta_weight', target_weight - current_weight)
    current_quantity = _float(row, 'current_quantity', 0.0) if _has_number(row, 'current_quantity') else None
    common = {
        'ticker': ticker,
        'price': price,
        'sector': str(row.get('sector')) if row.get('sector') is not None and not pd.isna(row.get('sector')) else None,
        'dollar_adv': _float(row, 'dollar_adv', 0.0) or None,
        'beta': _float(row, 'beta', 0.0) or None,
    }

    def make_trade(trade_side: str, trade_quantity: float, trade_weight: float, *, is_closing: bool) -> Trade:
        return Trade(
            side=trade_side,
            quantity=abs(float(trade_quantity)),
            weight=float(trade_weight),
            is_closing=is_closing,
            **common,
        )

    if current_weight > 0 and target_weight < 0 and side in {'sell', 'short'}:
        close_quantity = abs(float(current_quantity)) if current_quantity and current_quantity > 0 else quantity * abs(current_weight) / max(abs(delta_weight), 1e-12)
        close_quantity = min(abs(quantity), close_quantity)
        open_quantity = max(0.0, abs(quantity) - close_quantity)
        trades = [make_trade('sell', close_quantity, -abs(current_weight), is_closing=True)]
        if open_quantity > 1e-12:
            trades.append(make_trade('short', open_quantity, target_weight, is_closing=False))
        return trades

    if current_weight < 0 and target_weight > 0 and side in {'buy', 'cover'}:
        close_quantity = abs(float(current_quantity)) if current_quantity and current_quantity < 0 else quantity * abs(current_weight) / max(abs(delta_weight), 1e-12)
        close_quantity = min(abs(quantity), close_quantity)
        open_quantity = max(0.0, abs(quantity) - close_quantity)
        trades = [make_trade('cover', close_quantity, abs(current_weight), is_closing=True)]
        if open_quantity > 1e-12:
            trades.append(make_trade('buy', open_quantity, target_weight, is_closing=False))
        return trades

    is_closing = False
    if side == 'sell' and target_weight < 0 and target_weight < current_weight:
        side = 'short'
    elif side == 'buy' and current_weight < 0 and target_weight > current_weight:
        side = 'cover'
        is_closing = True
    elif side == 'sell' and current_weight > 0 and target_weight < current_weight:
        is_closing = True
    if _has_number(row, 'current_quantity'):
        if side == 'cover' and current_quantity is not None and current_quantity < 0:
            quantity = min(abs(quantity), abs(current_quantity))
        elif side == 'sell' and current_quantity is not None and current_quantity > 0:
            quantity = min(abs(quantity), current_quantity)
    return [make_trade(side, quantity, delta_weight, is_closing=is_closing)]


def trades_from_orders(orders: pd.DataFrame, *, default_price: float = 100.0) -> list[Trade]:
    if orders.empty:
        return []
    trades: list[Trade] = []
    for row in orders.itertuples(index=False):
        trades.extend(trades_from_order_row(row._asdict(), default_price=default_price))
    return trades


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    dry_run = bool(args.dry_run or not args.execute)
    trades = trades_from_orders(_read_orders(args.orders_input), default_price=args.default_price)
    results = execute_trades(trades, config=config, dry_run=dry_run, order_log_path=args.orders_log, risk_acknowledgement=args.risk_acknowledgement)
    payload = {
        'dry_run': dry_run,
        'orders': results,
        'orders_input': str(args.orders_input),
        'orders_log': str(args.orders_log),
        'mode': config.get('execution', {}).get('mode', 'paper'),
        'allow_live_trading': config.get('execution', {}).get('allow_live_trading', False),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    broker_submit_failed = any('broker_submit_failed' in result.get('reasons', []) for result in results)
    return 2 if broker_submit_failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
