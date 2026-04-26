from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import bootstrap_database
from app.routers import dashboard, ft_notes, health, metrics, prices, regime, report, watchlist


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the local database on startup."""
    bootstrap_database()
    yield


app = FastAPI(title="Personal Hedge System API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(watchlist.router)
app.include_router(prices.router)
app.include_router(metrics.router)
app.include_router(regime.router)
app.include_router(dashboard.router)
app.include_router(ft_notes.router)
app.include_router(report.router)
