from __future__ import annotations

from fastapi import APIRouter

from app.database import add_watchlist_item, list_watchlist
from app.models.schemas import WatchlistCreate
from app.services.market_data import ensure_symbol_prices


router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("")
def get_watchlist() -> list[dict]:
    """Return watchlist assets."""
    return list_watchlist()


@router.post("")
def post_watchlist(item: WatchlistCreate) -> dict:
    """Add an asset to the watchlist."""
    created = add_watchlist_item(item.model_dump())
    ensure_symbol_prices(created["symbol"])
    return created
