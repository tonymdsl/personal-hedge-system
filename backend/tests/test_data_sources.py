import pandas as pd

from app.database import add_watchlist_item, initialize_database, save_prices
from app.services.data_sources.sample_data import generate_sample_prices
from app.services.data_sources.stooq import fetch_prices_with_fallback, normalize_stooq_symbol
from app.services.data_sources.yahoo import fetch_yahoo_prices


def test_normalize_stooq_symbol_converts_us_tickers():
    assert normalize_stooq_symbol("AAPL") == "aapl.us"
    assert normalize_stooq_symbol("msft") == "msft.us"
    assert normalize_stooq_symbol("SPY.US") == "spy.us"
    assert normalize_stooq_symbol("BTC-USD") is None


def test_generate_sample_prices_returns_non_empty_ohlcv_rows():
    data = generate_sample_prices("SPY", periods=20)

    assert list(data.columns) == ["date", "open", "high", "low", "close", "volume", "source"]
    assert len(data) == 20
    assert data["close"].gt(0).all()
    assert data["source"].eq("sample").all()


def test_fetch_yahoo_prices_parses_chart_response(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1704067200, 1704153600],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [100.0, 101.0],
                                        "high": [102.0, 103.0],
                                        "low": [99.0, 100.0],
                                        "close": [101.5, None],
                                        "volume": [1000, 2000],
                                    }
                                ]
                            },
                        }
                    ],
                    "error": None,
                }
            }

    def fake_get(url, params, timeout, headers):
        assert "finance/chart/SPY" in url
        assert params["range"] == "3y"
        assert headers["User-Agent"]
        return FakeResponse()

    monkeypatch.setattr("app.services.data_sources.yahoo.requests.get", fake_get)

    data = fetch_yahoo_prices("SPY")

    assert list(data.columns) == ["date", "open", "high", "low", "close", "volume", "source"]
    assert len(data) == 1
    assert data.iloc[0]["close"] == 101.5
    assert data["source"].eq("yahoo").all()


def test_fetch_prices_with_fallback_prefers_yahoo_before_sample(monkeypatch):
    yahoo_prices = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=2).date,
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1000, 1100],
            "source": ["yahoo", "yahoo"],
        }
    )

    monkeypatch.setattr("app.services.data_sources.stooq.fetch_yahoo_prices", lambda symbol: yahoo_prices)
    monkeypatch.setattr("app.services.data_sources.stooq.fetch_stooq_prices", lambda symbol: pd.DataFrame())

    data = fetch_prices_with_fallback("SPY")

    assert data["source"].eq("yahoo").all()
    assert data.iloc[-1]["close"] == 101.5


def test_ensure_symbol_prices_refreshes_existing_sample_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("PHS_DB_PATH", str(tmp_path / "prices.duckdb"))

    import app.services.market_data as market_data

    initialize_database()
    add_watchlist_item({"symbol": "SPY", "name": "SPY", "asset_type": "ETF", "currency": "USD"})
    save_prices("SPY", generate_sample_prices("SPY", periods=3))

    yahoo_prices = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=3).date,
            "open": [700.0, 701.0, 702.0],
            "high": [701.0, 702.0, 703.0],
            "low": [699.0, 700.0, 701.0],
            "close": [700.5, 701.5, 702.5],
            "volume": [1000, 1100, 1200],
            "source": ["yahoo", "yahoo", "yahoo"],
        }
    )
    monkeypatch.setattr(market_data, "fetch_prices_with_fallback", lambda symbol: yahoo_prices)

    data = market_data.ensure_symbol_prices("SPY")

    assert data["source"].eq("yahoo").all()
    assert data.iloc[-1]["close"] == 702.5
