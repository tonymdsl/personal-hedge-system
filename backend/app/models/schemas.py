from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


Sentiment = Literal["positive", "neutral", "negative", "mixed"]
Impact = Literal["low", "medium", "high"]
Horizon = Literal["days", "weeks", "months"]
PortfolioRelevance = Literal["low", "medium", "high"]


class WatchlistCreate(BaseModel):
    """Payload for adding an asset to the watchlist."""

    symbol: str = Field(min_length=1)
    name: str | None = None
    asset_type: str = "Equity"
    currency: str = "USD"


class WatchlistItem(BaseModel):
    """Watchlist response item."""

    symbol: str
    name: str | None = None
    asset_type: str
    currency: str
    created_at: datetime | None = None


class PricePoint(BaseModel):
    """Historical OHLCV point."""

    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str
    updated_at: datetime | None = None


class DataMetadata(BaseModel):
    """Price data provenance metadata."""

    source: str
    last_updated: datetime | None = None
    data_range_start: date | None = None
    data_range_end: date | None = None
    price_type: str = "close"
    is_sample_data: bool = False


class FTNoteCreate(BaseModel):
    """Payload for manual Financial Times research notes."""

    title: str = Field(min_length=1)
    url: str | None = None
    published_date: date
    summary: str = Field(min_length=1)
    assets: list[str] = []
    sectors: list[str] = []
    macro_themes: list[str] = []
    sentiment: Sentiment
    impact: Impact
    horizon: Horizon
    portfolio_relevance: PortfolioRelevance = "medium"
    notes: str | None = None


class FTNote(FTNoteCreate):
    """Stored FT note response."""

    id: str
    created_at: datetime | None = None
