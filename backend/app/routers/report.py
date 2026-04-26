from __future__ import annotations

from fastapi import APIRouter

from app.services.reporting import generate_daily_report


router = APIRouter(prefix="/api/report", tags=["report"])


@router.get("/daily")
def get_daily_report() -> dict:
    """Return the daily report payload."""
    return generate_daily_report()
