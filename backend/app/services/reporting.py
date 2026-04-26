from __future__ import annotations

from datetime import datetime, timezone

from app.database import list_ft_notes
from app.services.market_data import get_dashboard_payload


def _portfolio_implications(regime: str, risk_alerts: list[str]) -> list[str]:
    """Translate regime and risk state into paper analytics implications."""
    implications = []
    if regime == "risk_on":
        implications.append("Rules favor normal equity exposure under the equal-weight research assumption.")
    elif regime == "risk_off":
        implications.append("Rules favor reduced equity exposure and closer monitoring of defensive proxies.")
    else:
        implications.append("Rules favor stress posture: lower equity exposure and review drawdown controls.")
    if risk_alerts:
        implications.append("Risk alerts are active; review concentration and drawdown before adding exposure.")
    else:
        implications.append("No active risk alerts under current equal-weight research assumptions.")
    return implications


def generate_daily_report() -> dict:
    """Generate a daily report payload for the frontend."""
    dashboard = get_dashboard_payload()
    risk_alerts = dashboard["risk"].get("alerts", [])
    return {
        "market_regime": dashboard["regime"]["regime"],
        "confidence": dashboard["regime"]["confidence"],
        "regime": dashboard["regime"],
        "top_movers": dashboard["movers"][:5],
        "risk_alerts": risk_alerts,
        "watchlist_summary": dashboard["watchlist"],
        "ft_notes": list_ft_notes()[:5],
        "portfolio_implications": _portfolio_implications(dashboard["regime"]["regime"], risk_alerts),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
