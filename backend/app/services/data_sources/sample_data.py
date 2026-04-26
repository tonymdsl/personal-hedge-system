from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd


def _seed_for_symbol(symbol: str) -> int:
    """Build a stable numeric seed for a symbol."""
    digest = hashlib.sha256(symbol.upper().encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def generate_sample_prices(symbol: str, periods: int = 320) -> pd.DataFrame:
    """Generate deterministic OHLCV sample prices."""
    rng = np.random.default_rng(_seed_for_symbol(symbol))
    end_date = pd.Timestamp.now("UTC").normalize().tz_localize(None)
    if end_date.weekday() >= 5:
        end_date = end_date - pd.offsets.BDay(1)
    dates = pd.bdate_range(end=end_date, periods=periods)
    drift = 0.00035
    shock = rng.normal(drift, 0.012, periods)
    close = 100 * np.cumprod(1 + shock)
    open_ = close * (1 + rng.normal(0, 0.0025, periods))
    high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.012, periods))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.012, periods))
    volume = rng.integers(500_000, 20_000_000, periods)
    return pd.DataFrame(
        {
            "date": dates.date,
            "open": open_.round(4),
            "high": high.round(4),
            "low": low.round(4),
            "close": close.round(4),
            "volume": volume.astype(int),
            "source": ["sample"] * periods,
        }
    )
