from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tomllib

import pandas as pd

from data.transcripts import fetch_fmp_transcript, ingest_transcripts
from data.providers import select_provider_name
from data.sec_data import flag_cluster_buying, parse_form4_xml
from data.fundamentals import calculate_financial_ratios
from data.market_data import upsert_daily_prices
from data.universe import SP500_USER_AGENT, WIKIPEDIA_SP500_URL, load_sp500_universe, universe_cache_path, upsert_universe
from factors.inputs import build_factor_inputs_from_database
from run_data import run_refresh
import run_data as run_data_module


def test_pyproject_declares_lxml_for_sp500_html_ingestion() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [dependency.lower() for dependency in pyproject["project"]["dependencies"]]

    assert any(dependency.startswith("lxml") for dependency in dependencies)


def test_load_sp500_universe_refresh_fetches_with_user_agent_and_parses_html() -> None:
    config = {
        "data": {
            "universe": {
                "cache_path": "cache/test_universe_sp500_lxml_regression.csv",
                "cache_ttl_days": 0,
            }
        }
    }
    cache_path = universe_cache_path(config)
    cache_path.unlink(missing_ok=True)
    captured: dict[str, object] = {}
    html = "<html><body><table><tr><td>placeholder</td></tr></table></body></html>"

    def fake_fetch_html(url: str, *, headers: dict[str, str], timeout: float) -> str:
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return html

    def fake_read_html(source: object, **kwargs: object) -> list[pd.DataFrame]:
        captured["html"] = source.read()
        captured["kwargs"] = kwargs
        return [
            pd.DataFrame(
                [
                    {
                        "Symbol": "BRK.B",
                        "Security": "Berkshire Hathaway",
                        "GICS Sector": "Financials",
                        "GICS Sub-Industry": "Multi-Sector Holdings",
                    },
                    {
                        "Symbol": "AAPL",
                        "Security": "Apple Inc.",
                        "GICS Sector": "Information Technology",
                        "GICS Sub-Industry": "Technology Hardware",
                    },
                ]
            )
        ]

    try:
        result = load_sp500_universe(config, force_refresh=True, read_html=fake_read_html, fetch_html=fake_fetch_html)

        assert captured == {
            "url": WIKIPEDIA_SP500_URL,
            "headers": {"User-Agent": SP500_USER_AGENT},
            "timeout": 20.0,
            "html": html,
            "kwargs": {"flavor": "lxml"},
        }
        assert result.error is None
        assert result.refreshed is True
        assert result.from_cache is False
        assert result.cache_path == cache_path
        assert cache_path.exists()
        assert result.frame["ticker"].tolist() == ["BRK-B", "AAPL"]
    finally:
        cache_path.unlink(missing_ok=True)


def test_load_sp500_universe_read_html_injection_remains_local_only() -> None:
    config = {
        "data": {
            "universe": {
                "cache_path": "cache/test_universe_sp500_read_html_injection.csv",
                "cache_ttl_days": 0,
            }
        }
    }
    cache_path = universe_cache_path(config)
    cache_path.unlink(missing_ok=True)
    captured: dict[str, object] = {}

    def fake_read_html(url: str, **kwargs: object) -> list[pd.DataFrame]:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return [
            pd.DataFrame(
                [
                    {
                        "Symbol": "MSFT",
                        "Security": "Microsoft",
                        "GICS Sector": "Information Technology",
                        "GICS Sub-Industry": "Software",
                    }
                ]
            )
        ]

    try:
        result = load_sp500_universe(config, force_refresh=True, read_html=fake_read_html)

        assert captured == {"url": WIKIPEDIA_SP500_URL, "kwargs": {"flavor": "lxml"}}
        assert result.error is None
        assert result.refreshed is True
        assert result.from_cache is False
        assert result.frame["ticker"].tolist() == ["MSFT"]
    finally:
        cache_path.unlink(missing_ok=True)


def test_load_sp500_universe_dry_run_without_cache_does_not_read_html() -> None:
    config = {"data": {"universe": {"cache_path": "cache/test_universe_dry_run_empty.csv"}}}
    cache_path = universe_cache_path(config)
    cache_path.unlink(missing_ok=True)

    def fail_if_called(*_: object, **__: object) -> list[pd.DataFrame]:
        raise AssertionError("dry-run should not read remote HTML")

    result = load_sp500_universe(config, dry_run=True, read_html=fail_if_called)

    assert result.error is None
    assert result.from_cache is False
    assert result.refreshed is False
    assert result.frame.empty
    assert not cache_path.exists()


def test_load_sp500_universe_download_failure_uses_existing_cache() -> None:
    config = {"data": {"universe": {"cache_path": "cache/test_universe_cached_fallback.csv"}}}
    cache_path = universe_cache_path(config)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "ticker": "MSFT",
                "name": "Microsoft",
                "sector": "Information Technology",
                "industry": "Software",
                "source": "wikipedia_sp500",
                "is_benchmark": False,
            }
        ]
    ).to_csv(cache_path, index=False)

    def fail_download(*_: object, **__: object) -> list[pd.DataFrame]:
        raise RuntimeError("parser unavailable")

    try:
        result = load_sp500_universe(config, force_refresh=True, read_html=fail_download)

        assert result.error == "parser unavailable"
        assert result.from_cache is True
        assert result.refreshed is False
        assert result.frame["ticker"].tolist() == ["MSFT"]
    finally:
        cache_path.unlink(missing_ok=True)


def test_load_sp500_universe_download_failure_without_cache_returns_empty_error() -> None:
    config = {"data": {"universe": {"cache_path": "cache/test_universe_missing_fallback.csv"}}}
    cache_path = universe_cache_path(config)
    cache_path.unlink(missing_ok=True)

    def fail_download(*_: object, **__: object) -> list[pd.DataFrame]:
        raise RuntimeError("parser unavailable")

    result = load_sp500_universe(config, force_refresh=True, read_html=fail_download)

    assert result.error == "parser unavailable"
    assert result.from_cache is False
    assert result.refreshed is False
    assert result.frame.empty
    assert not cache_path.exists()


def test_provider_selection_prefers_keyed_polygon() -> None:
    config = {'providers': {'priority': {'prices': ['polygon', 'yfinance']}, 'environment_variables': {'polygon': 'POLYGON_API_KEY'}}}
    assert select_provider_name('prices', config=config, environ={}) == 'yfinance'
    assert select_provider_name('prices', config=config, environ={'POLYGON_API_KEY': 'token'}) == 'polygon'


def test_ingest_transcripts_uses_configured_fmp_environment_variable(monkeypatch) -> None:
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setenv("CUSTOM_FMP_KEY", "test-key")
    config = {"providers": {"environment_variables": {"fmp": "CUSTOM_FMP_KEY"}}}
    sqlite_conn = sqlite3.connect(":memory:")

    result = ingest_transcripts(["AAPL"], sqlite_conn, config=config)

    assert result == {"skipped": True, "reason": "candidate_gate_not_available_yet", "transcripts": 0}
    sqlite_conn.close()


def test_form4_parser_flags_ceo_purchase() -> None:
    xml = (
        '<ownershipDocument><issuer><issuerTradingSymbol>ABC</issuerTradingSymbol></issuer>'
        '<reportingOwner><reportingOwnerId><rptOwnerName>Jane Doe</rptOwnerName></reportingOwnerId>'
        '<reportingOwnerRelationship><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship></reportingOwner>'
        '<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-01-05</value></transactionDate>'
        '<transactionCoding><transactionCode>P</transactionCode></transactionCoding>'
        '<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>12.5</value></transactionPricePerShare>'
        '<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>'
        '<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>'
        '</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>'
    )
    rows = parse_form4_xml(xml)
    assert rows[0]['ticker'] == 'ABC'
    assert rows[0]['is_open_market_purchase'] is True
    assert rows[0]['is_ceo_cfo'] is True
    assert rows[0]['dollar_value'] == 12500.0


def test_cluster_buying_requires_three_insiders() -> None:
    rows = flag_cluster_buying([
        {'insider_name': 'A', 'transaction_date': '2026-01-01', 'is_open_market_purchase': True},
        {'insider_name': 'B', 'transaction_date': '2026-01-10', 'is_open_market_purchase': True},
        {'insider_name': 'C', 'transaction_date': '2026-01-20', 'is_open_market_purchase': True},
    ])
    assert all(row['cluster_buy'] for row in rows)


def test_financial_ratio_calculation_core_fields() -> None:
    ratios = calculate_financial_ratios(
        {'Total Revenue': 1000, 'Gross Profit': 400, 'Net Income': 100, 'EBIT': 120},
        {'Total Assets': 2000, 'Stockholders Equity': 500, 'Total Debt': 250, 'Current Assets': 600, 'Current Liabilities': 300},
        {'Operating Cash Flow': 130, 'Capital Expenditure': -30},
        {'Total Revenue': 900, 'Net Income': 80},
        {'Total Assets': 1800, 'Stockholders Equity': 450},
        {'Operating Cash Flow': 100, 'Capital Expenditure': -25},
    )
    assert ratios['gross_margin'] == 0.4
    assert ratios['roe'] > 0
    assert ratios['current_ratio'] == 2
    assert ratios['cfo_to_net_income'] == 1.3


def test_fetch_fmp_transcript_uses_stable_endpoint_and_parses_list_response(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict[str, str]]:
            return [{"content": "Prepared remarks and Q&A"}]

    def fake_get(url, *, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("data.transcripts.requests.get", fake_get)

    result = fetch_fmp_transcript("aapl", 2020, 3, "test-key", timeout=7.5)

    assert captured == {
        "url": "https://financialmodelingprep.com/stable/earning-call-transcript",
        "params": {"symbol": "AAPL", "year": 2020, "quarter": 3, "apikey": "test-key"},
        "timeout": 7.5,
    }
    assert result == {
        "ticker": "AAPL",
        "fiscal_year": 2020,
        "quarter": 3,
        "transcript": "Prepared remarks and Q&A",
        "source": "fmp",
    }


def test_fetch_fmp_transcript_strips_and_uppercases_ticker(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"content": "Prepared remarks"}

    def fake_get(url, *, params, timeout):
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr("data.transcripts.requests.get", fake_get)

    result = fetch_fmp_transcript(" msft ", 2021, 2, "test-key")

    assert captured["params"] == {"symbol": "MSFT", "year": 2021, "quarter": 2, "apikey": "test-key"}
    assert result == {
        "ticker": "MSFT",
        "fiscal_year": 2021,
        "quarter": 2,
        "transcript": "Prepared remarks",
        "source": "fmp",
    }


def test_fetch_fmp_transcript_parses_dict_response(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"transcript": "Management discussion"}

    monkeypatch.setattr("data.transcripts.requests.get", lambda *args, **kwargs: FakeResponse())

    result = fetch_fmp_transcript("MSFT", 2021, 4, "test-key")

    assert result == {
        "ticker": "MSFT",
        "fiscal_year": 2021,
        "quarter": 4,
        "transcript": "Management discussion",
        "source": "fmp",
    }


def test_fetch_fmp_transcript_returns_none_when_no_data(monkeypatch) -> None:
    payloads = iter([[], {}])

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return self._payload

    monkeypatch.setattr("data.transcripts.requests.get", lambda *args, **kwargs: FakeResponse(next(payloads)))

    assert fetch_fmp_transcript("AAPL", 2020, 3, "test-key") is None
    assert fetch_fmp_transcript("AAPL", 2020, 3, "test-key") is None


def test_fetch_fmp_transcript_returns_none_for_unusable_payloads(monkeypatch) -> None:
    payloads = iter([[{}], {"foo": "bar"}, ["not-a-mapping"]])

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return self._payload

    monkeypatch.setattr("data.transcripts.requests.get", lambda *args, **kwargs: FakeResponse(next(payloads)))

    assert fetch_fmp_transcript("AAPL", 2020, 3, "test-key") is None
    assert fetch_fmp_transcript("AAPL", 2020, 3, "test-key") is None
    assert fetch_fmp_transcript("AAPL", 2020, 3, "test-key") is None


def test_fetch_fmp_transcript_returns_none_for_non_text_transcripts(monkeypatch) -> None:
    payloads = iter([
        {"content": 123},
        {"transcript": ["not", "text"]},
        {"content": "   "},
        {"transcript": "\n\t"},
    ])

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return self._payload

    monkeypatch.setattr("data.transcripts.requests.get", lambda *args, **kwargs: FakeResponse(next(payloads)))

    assert fetch_fmp_transcript(" AAPL ", 2020, 3, "test-key") is None
    assert fetch_fmp_transcript(" AAPL ", 2020, 3, "test-key") is None
    assert fetch_fmp_transcript(" AAPL ", 2020, 3, "test-key") is None
    assert fetch_fmp_transcript(" AAPL ", 2020, 3, "test-key") is None


def test_build_factor_inputs_from_database_adds_price_history_and_forward_return() -> None:
    sqlite_conn = sqlite3.connect(":memory:")
    upsert_universe(
        sqlite_conn,
        pd.DataFrame([
            {"ticker": "AAA", "name": "AAA Corp", "sector": "Tech", "industry": "Software", "source": "test", "is_benchmark": False},
            {"ticker": "BBB", "name": "BBB Corp", "sector": "Tech", "industry": "Hardware", "source": "test", "is_benchmark": False},
        ]),
    )
    prices = []
    for ticker, start in [("AAA", 100.0), ("BBB", 50.0)]:
        for idx, date in enumerate(pd.date_range("2026-01-01", periods=30, freq="D")):
            prices.append(
                {
                    "ticker": ticker,
                    "date": date.strftime("%Y-%m-%d"),
                    "open": start + idx,
                    "high": start + idx + 1,
                    "low": start + idx - 1,
                    "close": start + idx,
                    "adj_close": start + idx,
                    "volume": 1000 + idx,
                }
            )
    upsert_daily_prices(sqlite_conn, pd.DataFrame(prices), source="test")

    frame = build_factor_inputs_from_database(sqlite_conn)

    latest = frame[(frame["ticker"] == "AAA") & (frame["date"] == "2026-01-22")].iloc[0]
    assert latest["gics_sector"] == "Tech"
    assert latest["price"] == 121.0
    assert latest["price_21d_ago"] == 100.0
    assert round(latest["return_1m"], 4) == 0.21
    assert round(latest["forward_return"], 4) == round(122.0 / 121.0 - 1.0, 4)
    sqlite_conn.close()


def test_run_data_dry_run_initializes_without_network() -> None:
    args = Namespace(config=None, dry_run=True, no_filings=True, no_13f=True, limit=0)
    result = run_refresh(args)
    assert result['dry_run'] is True
    assert result['sec']['skipped'] is True
    assert result['institutional']['skipped'] is True


def test_run_data_full_refresh_passes_tickers_in_layer_order(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []
    connection = object()

    @contextmanager
    def fake_connect(*, config):
        yield connection

    def record(name):
        def inner(tickers, conn, **kwargs):
            assert conn is connection
            calls.append((name, list(tickers)))
            return {"count": len(list(tickers)), **kwargs}

        return inner

    def record_connection_first(name):
        def inner(conn, tickers, **kwargs):
            assert conn is connection
            calls.append((name, list(tickers)))
            return {"count": len(list(tickers)), **kwargs}

        return inner

    monkeypatch.setattr(run_data_module, "load_config", lambda _path=None: {})
    monkeypatch.setattr(run_data_module, "connect", fake_connect)
    monkeypatch.setattr(run_data_module, "setup_logging", lambda _name: type("Logger", (), {"info": lambda *a, **k: None})())
    monkeypatch.setattr(
        run_data_module,
        "ingest_universe",
        lambda conn, config, force_refresh: {"rows_written": 3, "count": 3, "source": "test"},
    )
    monkeypatch.setattr(run_data_module, "get_universe_tickers", lambda conn: ["AAA", "BBB", "CCC"])
    monkeypatch.setattr(run_data_module, "ingest_market_data", record_connection_first("prices"))
    monkeypatch.setattr(run_data_module, "ingest_fundamentals", record_connection_first("fundamentals"))
    monkeypatch.setattr(run_data_module, "ingest_short_interest", record("short_interest"))
    monkeypatch.setattr(run_data_module, "ingest_estimates", record("estimates"))
    monkeypatch.setattr(run_data_module, "ingest_earnings_calendar", record("calendar"))
    monkeypatch.setattr(run_data_module, "ingest_transcripts", record("transcripts"))
    monkeypatch.setattr(run_data_module, "ingest_sec_filings", record("sec"))
    monkeypatch.setattr(run_data_module, "ingest_13f", record("institutional"))
    monkeypatch.setattr(run_data_module, "build_factor_inputs_from_database", lambda conn: pd.DataFrame({"ticker": ["AAA", "BBB"]}))
    monkeypatch.setattr(run_data_module, "export_factor_inputs", lambda frame: "output/factor_inputs.csv")

    args = Namespace(config=None, dry_run=False, no_filings=False, no_13f=False, limit=2)

    result = run_refresh(args)

    assert result["universe"] == {"rows": 3, "source": "test"}
    assert calls == [
        ("prices", ["AAA", "BBB"]),
        ("fundamentals", ["AAA", "BBB"]),
        ("short_interest", ["AAA", "BBB"]),
        ("estimates", ["AAA", "BBB"]),
        ("calendar", ["AAA", "BBB"]),
        ("transcripts", ["AAA", "BBB"]),
        ("sec", ["AAA", "BBB"]),
        ("institutional", ["AAA", "BBB"]),
    ]
    assert result["factor_inputs"] == {"rows": 2, "output": "output/factor_inputs.csv"}
