from __future__ import annotations

from fastapi import APIRouter

from app.database import list_ft_notes
from app.services.market_data import get_dashboard_payload


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard() -> dict:
    """Return dashboard payload."""
    payload = get_dashboard_payload()
    payload["ft_notes"] = list_ft_notes()[:5]
    return payload
