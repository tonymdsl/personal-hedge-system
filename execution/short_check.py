"""Short availability cache."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


@dataclass
class ShortAvailability:
    ticker: str
    shortable: bool
    easy_to_borrow: bool
    source: str = 'cache'
    as_of: str | None = None


class ShortAvailabilityCache:
    def __init__(self, *, ttl_days: int = 7, log_path: str | Path | None = None):
        self._cache: dict[str, ShortAvailability] = {}
        self.ttl_days = ttl_days
        self.log_path = Path(log_path) if log_path is not None else None

    def set(self, ticker: str, shortable: bool, easy_to_borrow: bool, *, as_of: str | date | datetime | None = None) -> None:
        self._cache[ticker.upper()] = ShortAvailability(ticker.upper(), shortable, easy_to_borrow, as_of=_date_string(as_of))

    def get(self, ticker: str, *, as_of: str | date | datetime | None = None) -> ShortAvailability:
        ticker_u = ticker.upper()
        value = self._cache.get(ticker_u)
        if value is None:
            return ShortAvailability(ticker_u, True, True, 'default_allow_paper', as_of=_date_string(as_of))
        if _is_expired(value.as_of, _date_string(as_of), self.ttl_days):
            return ShortAvailability(ticker_u, True, True, 'expired_default_allow_paper', as_of=_date_string(as_of))
        if not (value.shortable and value.easy_to_borrow):
            self._log_unavailable(value)
        return value

    def _log_unavailable(self, value: ShortAvailability) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'ticker': value.ticker,
            'status': 'short_unavailable',
            'shortable': value.shortable,
            'easy_to_borrow': value.easy_to_borrow,
        }
        with self.log_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, sort_keys=True) + '\n')


def _date_string(value: str | date | datetime | None) -> str:
    if value is None:
        return date.today().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _is_expired(stored: str | None, current: str, ttl_days: int) -> bool:
    if stored is None:
        return False
    try:
        stored_date = datetime.fromisoformat(stored).date()
        current_date = datetime.fromisoformat(current).date()
    except ValueError:
        return False
    return (current_date - stored_date).days > ttl_days
