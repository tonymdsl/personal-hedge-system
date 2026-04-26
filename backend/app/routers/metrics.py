from __future__ import annotations

from fastapi import APIRouter

from app.services.analytics.metrics import calculate_metrics
from app.services.market_data import ensure_symbol_prices


router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/{symbol}")
def get_metrics(symbol: str) -> dict:
    """Return market metrics for an asset."""
    return calculate_metrics(ensure_symbol_prices(symbol))
