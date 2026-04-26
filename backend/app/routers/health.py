from __future__ import annotations

from fastapi import APIRouter


router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Return service health."""
    return {"status": "ok"}
