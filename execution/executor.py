"""Dry-run/paper executor that runs pre-trade vetoes before orders."""
from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from risk.pre_trade import Trade, check_pre_trade
from .broker import PaperBroker, get_broker
from .costs import slippage_bps
from .short_check import ShortAvailabilityCache


def build_limit_order(trade: Trade, *, limit_buffer_bps: float = 10.0) -> dict[str, object]:
    side = trade.side.lower()
    multiplier = 1 + (limit_buffer_bps / 10_000.0 if side in {'buy', 'cover'} else -limit_buffer_bps / 10_000.0)
    limit_price = float(trade.price) * multiplier
    limit_price_precision = 2 if limit_price >= 1 else 4
    return {
        'ticker': trade.ticker.upper(),
        'side': side,
        'quantity': abs(float(trade.quantity)),
        'limit_price': round(limit_price, limit_price_precision),
        'time_in_force': 'day',
        'timeout_seconds': 120,
        'poll_interval_seconds': 5,
        'max_retries': 3,
        'signal_price': float(trade.price),
    }


def chunk_trade_by_adv(trade: Trade, *, max_order_adv_pct: float = 0.02) -> list[Trade]:
    if trade.dollar_adv is None or trade.dollar_adv <= 0 or trade.price <= 0:
        return [trade]
    max_notional = float(trade.dollar_adv) * float(max_order_adv_pct)
    if trade.notional <= max_notional:
        return [trade]
    max_quantity = max_notional / float(trade.price)
    if max_quantity <= 0:
        return [trade]
    remaining = abs(float(trade.quantity))
    chunks: list[Trade] = []
    sign = -1.0 if float(trade.quantity) < 0 else 1.0
    while remaining > 1e-9:
        qty = min(max_quantity, remaining)
        chunks.append(replace(trade, quantity=sign * qty))
        remaining -= qty
    return chunks


def execute_trades(
    trades: Iterable[Trade | Mapping[str, object]],
    *,
    config: Mapping[str, object] | None = None,
    dry_run: bool = True,
    broker: PaperBroker | None = None,
    short_cache: ShortAvailabilityCache | None = None,
    order_log_path: str | Path | None = None,
    risk_acknowledgement: bool | str = False,
) -> list[dict[str, object]]:
    broker = broker or get_broker(config, risk_acknowledgement=risk_acknowledgement)
    short_cache = short_cache or ShortAvailabilityCache()
    execution_cfg = (config or {}).get('execution', {}) if isinstance(config, Mapping) else {}
    order_defaults = execution_cfg.get('order_defaults', {}) if isinstance(execution_cfg, Mapping) and isinstance(execution_cfg.get('order_defaults'), Mapping) else {}
    max_order_adv_pct = float(order_defaults.get('max_order_adv_pct', 0.02)) if isinstance(order_defaults, Mapping) else 0.02
    limit_buffer_bps = float(order_defaults.get('limit_buffer_bps', 10.0)) if isinstance(order_defaults, Mapping) else 10.0
    min_order_notional_usd = float(order_defaults.get('min_order_notional_usd', 1.0)) if isinstance(order_defaults, Mapping) else 1.0
    results: list[dict[str, object]] = []
    for item in trades:
        trade = item if isinstance(item, Trade) else Trade(**item)  # type: ignore[arg-type]
        decision = check_pre_trade(trade, config=config)
        if not decision.approved:
            rejected = {'ticker': trade.ticker, 'status': 'rejected', 'reasons': decision.reasons}
            _append_order_log(order_log_path, trade, rejected)
            results.append(rejected)
            continue
        if trade.side.lower() == 'short':
            availability = short_cache.get(trade.ticker)
            if not (availability.shortable and availability.easy_to_borrow):
                rejected = {'ticker': trade.ticker, 'status': 'rejected', 'reasons': ['short_unavailable']}
                _append_order_log(order_log_path, trade, rejected)
                results.append(rejected)
                continue
            whole_share_quantity = math.floor(abs(float(trade.quantity)))
            if whole_share_quantity < 1:
                rejected = {'ticker': trade.ticker, 'status': 'rejected', 'reasons': ['short_quantity_less_than_one_share']}
                _append_order_log(order_log_path, trade, rejected)
                results.append(rejected)
                continue
            trade = replace(trade, quantity=whole_share_quantity)
        for chunk in chunk_trade_by_adv(trade, max_order_adv_pct=max_order_adv_pct):
            if chunk.side.lower() == 'short':
                whole_share_quantity = math.floor(abs(float(chunk.quantity)))
                if whole_share_quantity < 1:
                    rejected = {'ticker': chunk.ticker, 'status': 'rejected', 'reasons': ['short_chunk_quantity_less_than_one_share']}
                    _append_order_log(order_log_path, chunk, rejected)
                    results.append(rejected)
                    continue
                chunk = replace(chunk, quantity=whole_share_quantity)
            if chunk.notional < min_order_notional_usd:
                rejected = {'ticker': chunk.ticker, 'status': 'rejected', 'reasons': ['order_notional_below_minimum']}
                _append_order_log(order_log_path, chunk, rejected)
                results.append(rejected)
                continue
            order = build_limit_order(chunk, limit_buffer_bps=limit_buffer_bps)
            if dry_run:
                order['status'] = 'dry_run'
                _append_order_log(order_log_path, chunk, order)
                results.append(order)
            else:
                try:
                    submitted = broker.submit_order(order)
                except Exception as exc:
                    rejected = _broker_rejection(order, exc, broker)
                    _append_order_log(order_log_path, chunk, rejected)
                    results.append(rejected)
                    continue
                _append_order_log(order_log_path, chunk, submitted)
                results.append(submitted)
    return results


def _broker_rejection(order: Mapping[str, object], exc: Exception, broker: PaperBroker) -> dict[str, object]:
    error = _safe_error_message(exc, broker)
    return {
        'ticker': str(order.get('ticker', order.get('symbol', ''))),
        'status': 'rejected',
        'reasons': ['broker_submit_failed'],
        'error': f'broker_submit_failed: {error}',
    }


def _safe_error_message(exc: Exception, broker: PaperBroker) -> str:
    message = str(exc)
    redactions = [
        getattr(broker, 'api_key', ''),
        getattr(broker, 'secret_key', ''),
        os.getenv('ALPACA_API_KEY', ''),
        os.getenv('ALPACA_SECRET_KEY', ''),
    ]
    for secret in redactions:
        if secret:
            message = message.replace(str(secret), '[redacted]')
    return message


def _append_order_log(log_path: str | Path | None, trade: Trade, result: Mapping[str, object]) -> None:
    if log_path is None:
        return
    output = Path(log_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fill_price = result.get('fill_price')
    slip = None
    if fill_price is not None:
        slip = slippage_bps(float(trade.price), float(fill_price), trade.side)
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'ticker': trade.ticker.upper(),
        'side': trade.side.lower(),
        'shares': abs(float(result.get('quantity', trade.quantity))),
        'limit_price': result.get('limit_price'),
        'signal_price': float(trade.price),
        'fill_price': fill_price,
        'slippage_bps': slip,
        'status': result.get('status'),
        'reasons': result.get('reasons', []),
    }
    if result.get('error') is not None:
        entry['error'] = result.get('error')
    with output.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, sort_keys=True, default=str) + '\n')
