from __future__ import annotations

from fastapi import APIRouter

from app.services.market_data import get_price_payload, refresh_watchlist


router = APIRouter(tags=["prices"])


@router.get("/api/prices/{symbol}")
def get_prices(symbol: str) -> dict:
    """Return historical prices for an asset."""
    return get_price_payload(symbol)


@router.post("/api/data/refresh")
def post_refresh() -> dict:
    """Refresh market data for the watchlist."""
    return refresh_watchlist()
