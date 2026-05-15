"""Track order lifecycle and cancel pending orders on interruption."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


@dataclass
class OrderManager:
    orders: dict[str, dict[str, object]] = field(default_factory=dict)

    def record(self, order_id: str, order: Mapping[str, object]) -> None:
        self.orders[order_id] = dict(order)

    def by_status(self, status: str) -> list[dict[str, object]]:
        return [order for order in self.orders.values() if order.get('status') == status]

    def cancel_pending(self, *, broker: object | None = None, log_path: str | Path | None = None, reason: str = 'interrupt') -> int:
        count = 0
        for order_id, order in self.orders.items():
            if order.get('status') in {'pending', 'partial', 'accepted_paper'}:
                if broker is not None and hasattr(broker, 'cancel_order'):
                    broker.cancel_order(str(order.get('order_id', order_id)))
                order['status'] = 'cancelled'
                order['cancel_reason'] = reason
                _append_cancel_log(log_path, order_id, order, reason)
                count += 1
        return count


def _append_cancel_log(log_path: str | Path | None, order_id: str, order: Mapping[str, object], reason: str) -> None:
    if log_path is None:
        return
    output = Path(log_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'order_id': order_id,
        'ticker': order.get('ticker'),
        'status': 'cancelled',
        'reason': reason,
    }
    with output.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, sort_keys=True, default=str) + '\n')
