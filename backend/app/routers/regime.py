from __future__ import annotations

from fastapi import APIRouter

from app.services.market_data import get_regime


router = APIRouter(prefix="/api/regime", tags=["regime"])


@router.get("")
def get_market_regime() -> dict:
    """Return current market regime."""
    return get_regime()
