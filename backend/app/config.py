from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent

load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")

DEFAULT_WATCHLIST = [
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "asset_type": "ETF", "currency": "USD"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust", "asset_type": "ETF", "currency": "USD"},
    {"symbol": "TLT", "name": "iShares 20+ Year Treasury Bond ETF", "asset_type": "ETF", "currency": "USD"},
    {"symbol": "GLD", "name": "SPDR Gold Shares", "asset_type": "ETF", "currency": "USD"},
    {"symbol": "AAPL", "name": "Apple Inc.", "asset_type": "Equity", "currency": "USD"},
    {"symbol": "MSFT", "name": "Microsoft Corp.", "asset_type": "Equity", "currency": "USD"},
    {"symbol": "NVDA", "name": "NVIDIA Corp.", "asset_type": "Equity", "currency": "USD"},
    {"symbol": "AMZN", "name": "Amazon.com Inc.", "asset_type": "Equity", "currency": "USD"},
    {"symbol": "GOOGL", "name": "Alphabet Inc.", "asset_type": "Equity", "currency": "USD"},
    {"symbol": "META", "name": "Meta Platforms Inc.", "asset_type": "Equity", "currency": "USD"},
]

DEFAULT_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def get_db_path() -> Path:
    """Return the DuckDB path from env or the backend data directory."""
    configured = os.getenv("PHS_DB_PATH")
    if configured:
        return Path(configured)
    return BACKEND_DIR / "data" / "personal_hedge.duckdb"


def get_allowed_origins() -> list[str]:
    """Return CORS origins from env or local defaults."""
    configured = os.getenv("PHS_ALLOWED_ORIGINS")
    if not configured:
        return DEFAULT_ALLOWED_ORIGINS
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def ensure_runtime_dirs() -> None:
    """Create backend runtime directories."""
    get_db_path().parent.mkdir(parents=True, exist_ok=True)
