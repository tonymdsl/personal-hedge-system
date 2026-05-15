"""Rebalance calendar helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

_WEEKDAY = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

FOMC_DATES_2026 = {
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
}


def as_date(value: date | datetime | str | None = None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def next_rebalance_date(
    current_date: date | datetime | str | None = None,
    *,
    frequency: str = "weekly",
    weekday: str = "Friday",
) -> date:
    """Return the next scheduled rebalance date."""

    current = as_date(current_date)
    freq = str(frequency).lower()
    if freq == "daily":
        return current + timedelta(days=1)
    if freq == "monthly":
        year = current.year + (1 if current.month == 12 else 0)
        month = 1 if current.month == 12 else current.month + 1
        return date(year, month, 1)
    target = _WEEKDAY.get(str(weekday).lower(), 4)
    days_ahead = (target - current.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return current + timedelta(days=days_ahead)


def is_earnings_blackout(
    current_date: date | datetime | str,
    earnings_date: date | datetime | str | None,
    *,
    advisory_days: int = 2,
) -> bool:
    if earnings_date is None:
        return False
    current = as_date(current_date)
    event = as_date(earnings_date)
    return abs((event - current).days) <= int(advisory_days)


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    days_until_friday = (4 - first.weekday()) % 7
    return first + timedelta(days=days_until_friday + 14)


def rebalance_advisories(
    positions: pd.DataFrame,
    *,
    current_date: date | datetime | str | None = None,
    earnings_days: int = 2,
    fomc_days: int = 5,
    options_days: int = 3,
) -> list[dict[str, object]]:
    """Return advisory warnings that do not block trading."""

    current = as_date(current_date)
    warnings: list[dict[str, object]] = []
    if not positions.empty and "earnings_date" in positions.columns:
        for row in positions.to_dict(orient="records"):
            event = row.get("earnings_date")
            if event and is_earnings_blackout(current, event, advisory_days=earnings_days):
                event_date = as_date(event)
                warnings.append(
                    {
                        "kind": "earnings_blackout",
                        "ticker": str(row.get("ticker", "")).upper(),
                        "event_date": event_date.isoformat(),
                        "days_until": (event_date - current).days,
                        "blocks_trading": False,
                    }
                )
    for fomc_date in sorted(FOMC_DATES_2026):
        if abs((fomc_date - current).days) <= int(fomc_days):
            warnings.append(
                {
                    "kind": "fomc_window",
                    "event_date": fomc_date.isoformat(),
                    "days_until": (fomc_date - current).days,
                    "blocks_trading": False,
                }
            )
            break
    expiry = third_friday(current.year, current.month)
    if abs((expiry - current).days) <= int(options_days):
        warnings.append(
            {
                "kind": "options_expiration_window",
                "event_date": expiry.isoformat(),
                "days_until": (expiry - current).days,
                "blocks_trading": False,
            }
        )
    return warnings
