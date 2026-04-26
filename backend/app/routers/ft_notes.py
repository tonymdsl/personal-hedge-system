from __future__ import annotations

from fastapi import APIRouter

from app.database import create_ft_note, list_ft_notes
from app.models.schemas import FTNoteCreate


router = APIRouter(prefix="/api/ft-notes", tags=["ft-notes"])


@router.post("")
def post_ft_note(note: FTNoteCreate) -> dict:
    """Create a manual FT research note."""
    return create_ft_note(note.model_dump())


@router.get("")
def get_ft_notes() -> list[dict]:
    """List manual FT research notes."""
    return list_ft_notes()
