from fastapi.testclient import TestClient


def test_api_bootstraps_watchlist_and_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("PHS_DB_PATH", str(tmp_path / "app.duckdb"))

    from app.main import app

    with TestClient(app) as client:
        health = client.get("/health")
        watchlist = client.get("/api/watchlist")
        dashboard = client.get("/api/dashboard")
        prices = client.get("/api/prices/SPY")
        regime = client.get("/api/regime")
        report = client.get("/api/report/daily")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert watchlist.status_code == 200
    symbols = [item["symbol"] for item in watchlist.json()]
    assert "SPY" in symbols
    assert "QQQ" in symbols
    assert dashboard.status_code == 200
    dashboard_payload = dashboard.json()
    assert dashboard_payload["watchlist"]
    assert dashboard_payload["watchlist"][0]["metadata"]["price_type"] == "close"
    assert "is_sample_data" in dashboard_payload["watchlist"][0]["metadata"]
    assert dashboard_payload["risk"]["assumption"] == "equal_weight_no_real_positions"
    assert dashboard_payload["regime"]["regime"] in {"risk_on", "risk_off", "market_stress"}

    assert prices.status_code == 200
    assert prices.json()["metadata"]["data_range_start"]
    assert prices.json()["metadata"]["data_range_end"]
    assert prices.json()["metadata"]["source"] in {"stooq", "yahoo", "sample", "mixed"}
    assert prices.json()["prices"]

    assert regime.status_code == 200
    regime_payload = regime.json()
    assert set(regime_payload) >= {"regime", "confidence", "evidence", "thresholds", "values", "updated_at"}
    assert "max_volatility_for_risk_on" in regime_payload["thresholds"]
    assert "spy_price" in regime_payload["values"]

    assert report.status_code == 200
    report_payload = report.json()
    assert set(report_payload) >= {
        "market_regime",
        "confidence",
        "top_movers",
        "risk_alerts",
        "watchlist_summary",
        "ft_notes",
        "portfolio_implications",
        "updated_at",
    }


def test_api_accepts_manual_ft_note(tmp_path, monkeypatch):
    monkeypatch.setenv("PHS_DB_PATH", str(tmp_path / "ft.duckdb"))

    from app.main import app

    payload = {
        "title": "Rates story",
        "url": "https://www.ft.com/content/example",
        "published_date": "2026-04-26",
        "summary": "Manual summary only.",
        "assets": ["SPY", "TLT"],
        "sectors": ["Financials"],
        "macro_themes": ["rates"],
        "sentiment": "mixed",
        "impact": "medium",
        "horizon": "weeks",
        "portfolio_relevance": "high",
        "notes": "Watch duration risk.",
    }

    with TestClient(app) as client:
        created = client.post("/api/ft-notes", json=payload)
        listed = client.get("/api/ft-notes")

    assert created.status_code == 200
    assert created.json()["title"] == "Rates story"
    assert created.json()["portfolio_relevance"] == "high"
    assert listed.status_code == 200
    assert listed.json()[0]["sentiment"] == "mixed"
    assert listed.json()[0]["portfolio_relevance"] == "high"
