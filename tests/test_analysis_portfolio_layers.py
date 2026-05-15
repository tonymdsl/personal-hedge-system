from __future__ import annotations

import json
import sys
import shutil
from pathlib import Path

import pandas as pd
import pytest

import run_analysis as run_analysis_module
from analysis.cache import AnalysisCache
from analysis.earnings_analyzer import EarningsAnalyzer
from analysis.filing_analyzer import FilingAnalyzer
from analysis.insider_analyzer import InsiderAnalyzer
from analysis.risk_analyzer import RiskAnalyzer
from data.fundamentals import ensure_fundamentals_schema
from data.sec_data import ensure_sec_schema, upsert_insider_transactions
from data.transcripts import ensure_transcripts_schema
from analysis.api_client import (
    APIClientConfigError,
    CodexAnalysisClient,
    OpenRouterAnalysisClient,
    create_analysis_client,
    extract_json,
    estimate_tokens,
    resolve_codex_model,
)
from analysis.combined_score import combine_scores
from analysis.cost_tracker import CostEstimate, CostLimitExceeded, CostTracker
from common.db import connect, get_connection
from common.config import PROJECT_ROOT
from portfolio.beta import calculate_beta, calculate_portfolio_beta
from portfolio.optimizer import build_conviction_tilt_portfolio
from portfolio.transaction_costs import estimate_transaction_cost
from run_analysis import run_analysis_pipeline, select_analysis_candidates


TEST_DB = PROJECT_ROOT / "cache" / "test_analysis_portfolio_layers.sqlite3"


def _cleanup(path: Path = TEST_DB) -> None:
    if path.exists():
        path.unlink()


def test_extract_json_handles_raw_fence_and_prose() -> None:
    assert extract_json('{"score": 73, "tag": "raw"}') == {"score": 73, "tag": "raw"}
    assert extract_json('```json\n{"score": 74, "tag": "fenced"}\n```') == {"score": 74, "tag": "fenced"}
    assert extract_json('Codex says:\nHere is the object {"score": 75, "drivers": ["a", "b"]} done.') == {
        "score": 75,
        "drivers": ["a", "b"],
    }


def test_codex_client_uses_output_last_message_from_cli(tmp_path: Path) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "args = sys.argv\n"
        "if args[1] != 'exec' or '--skip-git-repo-check' not in args:\n"
        "    print(args, file=sys.stderr)\n"
        "    raise SystemExit(2)\n"
        "out = Path(args[args.index('--output-last-message') + 1])\n"
        "out.write_text('{\"score\": 88, \"provider\": \"codex\"}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    client = CodexAnalysisClient(
        config={"analysis": {"codex_command": f"{sys.executable} {fake_codex}", "default_model": "gpt-5.5"}},
        timeout_seconds=5,
    )
    assert client.complete_json("return JSON") == {"score": 88, "provider": "codex"}
    assert client.model == "gpt-5.5"
    assert resolve_codex_model({"analysis": {"model": ""}}) is None


def test_codex_client_sends_prompt_to_cli_as_utf8(tmp_path: Path) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "raw = sys.stdin.buffer.read()\n"
        "try:\n"
        "    prompt = raw.decode('utf-8')\n"
        "except UnicodeDecodeError as exc:\n"
        "    print(f'invalid utf-8: {exc}', file=sys.stderr)\n"
        "    raise SystemExit(13)\n"
        "if 'posição' not in prompt:\n"
        "    print(prompt, file=sys.stderr)\n"
        "    raise SystemExit(14)\n"
        "args = sys.argv\n"
        "out = Path(args[args.index('--output-last-message') + 1])\n"
        "out.write_text('acentos ok', encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    client = CodexAnalysisClient(
        config={"analysis": {"codex_command": f"{sys.executable} {fake_codex}", "default_model": ""}},
        timeout_seconds=5,
    )

    assert client.complete("Já fechaste alguma posição até agora?") == "acentos ok"


def test_codex_client_preserves_quoted_executable_path() -> None:
    command = '"C:\\Users\\Tonym\\AppData\\Local\\OpenAI\\Codex\\bin\\codex.exe"'
    client = CodexAnalysisClient(config={"analysis": {"codex_command": command, "default_model": ""}})

    built = client._build_command(Path("response.txt"))

    assert built[0].endswith("codex.exe")
    assert built[1] == "exec"


def test_openrouter_client_posts_chat_completion_with_configured_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls: list[dict[str, object]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"score": 82, "provider": "openrouter"}'}}]}

    class Session:
        def post(self, url: str, **kwargs) -> Response:
            calls.append({"url": url, **kwargs})
            return Response()

    client = OpenRouterAnalysisClient(
        config={
            "analysis": {
                "default_model": "deepseek/deepseek-v4-flash:free",
                "openrouter_site_url": "https://meridian.example",
                "openrouter_app_name": "Meridian JARVIS",
            }
        },
        session=Session(),
    )

    assert client.complete_json("Return JSON", system_prompt="You are a financial analyst") == {
        "score": 82,
        "provider": "openrouter",
    }

    call = calls[0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["headers"]["HTTP-Referer"] == "https://meridian.example"
    assert call["headers"]["X-OpenRouter-Title"] == "Meridian JARVIS"
    assert call["json"]["model"] == "deepseek/deepseek-v4-flash:free"
    assert call["json"]["messages"][0]["role"] == "system"
    assert call["json"]["messages"][1]["content"] == "Return JSON"


def test_openrouter_client_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(APIClientConfigError, match="OPENROUTER_API_KEY"):
        OpenRouterAnalysisClient(config={"analysis": {"provider": "openrouter"}})


def test_create_analysis_client_supports_openrouter_provider(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    client = create_analysis_client({"analysis": {"provider": "openrouter"}})

    assert isinstance(client, OpenRouterAnalysisClient)


def test_cost_tracker_enforces_ceiling() -> None:
    estimate = CostEstimate(input_tokens=1_000, output_tokens=1_000, input_cost_usd=0.01, output_cost_usd=0.02)
    tracker = CostTracker(run_cost_ceiling_usd=0.05)
    tracker.reserve(estimate)
    assert tracker.projected_cost_usd == pytest.approx(0.03)

    with pytest.raises(CostLimitExceeded):
        tracker.reserve(estimate)

    assert estimate_tokens("abcd" * 25) > 0


def test_analysis_cache_ttl_round_trip_and_expiry(tmp_path: Path) -> None:
    test_db = PROJECT_ROOT / "cache" / f"analysis_cache_{tmp_path.name}.sqlite3"
    _cleanup(test_db)
    try:
        cache = AnalysisCache(test_db, ttl_days=1)
        payload = {"score": 61, "summary": "cached"}
        cache.set("earnings", "aapl", "fy2025q4", payload)
        assert cache.get("earnings", "AAPL", "fy2025q4") == payload

        expired_cache = AnalysisCache(test_db, ttl_days=-1)
        assert expired_cache.get("earnings", "AAPL", "fy2025q4") is None
    finally:
        _cleanup(test_db)


def test_combined_score_uses_codex_when_present_else_quant_and_sector_rerank() -> None:
    frame = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC"],
            "sector": ["Tech", "Tech", "Energy"],
            "quant_score": [80.0, 40.0, 55.0],
            "qualitative_score": [60.0, None, 95.0],
        }
    )
    combined = combine_scores(frame, quantitative_weight=0.60, qualitative_weight=0.40, sector_rerank=True)

    aaa = combined.loc[combined["ticker"] == "AAA"].iloc[0]
    bbb = combined.loc[combined["ticker"] == "BBB"].iloc[0]
    ccc = combined.loc[combined["ticker"] == "CCC"].iloc[0]

    assert aaa["combined_score"] == pytest.approx(72.0)
    assert bbb["combined_score"] == pytest.approx(40.0)
    assert ccc["combined_score"] == pytest.approx(71.0)
    assert "sector_rank" in combined.columns
    assert aaa["sector_rank"] < bbb["sector_rank"]


def test_select_analysis_candidates_prefers_longs_and_shorts() -> None:
    frame = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC"],
            "gics_sector": ["Tech", "Tech", "Energy"],
            "composite_score": [90, 10, 50],
            "long_candidate": [True, False, False],
            "short_candidate": [False, True, False],
        }
    )

    selected = select_analysis_candidates(frame)

    assert selected["ticker"].tolist() == ["AAA", "BBB"]


def test_run_analysis_pipeline_writes_results_cache_and_candidate_reports(tmp_path: Path) -> None:
    test_db = PROJECT_ROOT / "cache" / f"analysis_pipeline_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"analysis_pipeline_{tmp_path.name}.csv"
    reports_dir = PROJECT_ROOT / "output" / "reports" / f"analysis_pipeline_{tmp_path.name}"
    _cleanup(test_db)
    if output_path.exists():
        output_path.unlink()
    if reports_dir.exists():
        shutil.rmtree(reports_dir)

    try:
        with connect(test_db) as connection:
            ensure_transcripts_schema(connection)
            ensure_fundamentals_schema(connection)
            ensure_sec_schema(connection)
            connection.execute(
                """
                INSERT INTO earnings_transcripts(ticker, fiscal_year, quarter, transcript)
                VALUES ('AAA', 2026, 1, 'beat raised guidance margin expansion strong demand')
                """
            )
            connection.execute(
                """
                INSERT INTO fundamental_ratios(ticker, fiscal_date, period, revenue, revenue_growth, gross_margin,
                    net_income, operating_cash_flow, free_cash_flow, total_debt, total_equity, source)
                VALUES ('AAA', '2026-03-31', 'quarterly', 100, 0.2, 0.5, 10, 12, 8, 20, 100, 'test')
                """
            )
            upsert_insider_transactions(
                connection,
                [
                    {
                        "ticker": "AAA",
                        "insider_name": "Jane",
                        "insider_title": "Chief Executive Officer",
                        "transaction_code": "P",
                        "transaction_type": "open_market_purchase",
                        "shares": 100,
                        "price": 10,
                        "transaction_date": "2026-04-20",
                        "ownership_type": "D",
                        "is_open_market_purchase": True,
                        "is_ceo_cfo": True,
                        "cluster_buy": False,
                    }
                ],
            )
            candidates = pd.DataFrame(
                {
                    "ticker": ["AAA"],
                    "gics_sector": ["Tech"],
                    "composite_score": [80.0],
                    "long_candidate": [True],
                    "short_candidate": [False],
                }
            )

            payload = run_analysis_pipeline(
                candidates,
                config={"analysis": {"cache_table": "analysis_results", "result_ttl_days": 30}},
                connection=connection,
                cache_db_path=test_db,
                output_path=output_path,
                reports_dir=reports_dir,
                use_ai=False,
            )

        assert payload["rows_analyzed"] == 1
        assert output_path.exists()
        results = pd.read_csv(output_path)
        assert results.loc[0, "ticker"] == "AAA"
        assert results.loc[0, "qualitative_score"] > 50
        assert list(reports_dir.rglob("*.md"))
        assert AnalysisCache(test_db, table_name="analysis_results").get("earnings", "AAA", "latest") is not None
    finally:
        _cleanup(test_db)
        if output_path.exists():
            output_path.unlink()
        if reports_dir.exists():
            shutil.rmtree(reports_dir)


def test_run_analysis_pipeline_materializes_all_db_payloads_before_ai_calls(tmp_path: Path, monkeypatch) -> None:
    test_db = PROJECT_ROOT / "cache" / f"analysis_pipeline_ai_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"analysis_pipeline_ai_{tmp_path.name}.csv"
    reports_dir = PROJECT_ROOT / "output" / "reports" / f"analysis_pipeline_ai_{tmp_path.name}"
    _cleanup(test_db)
    if output_path.exists():
        output_path.unlink()
    if reports_dir.exists():
        shutil.rmtree(reports_dir)

    class ClosingClient:
        model = "fake-ai"

        def __init__(self, connection) -> None:
            self.connection = connection
            self.calls = 0

        def complete_json(self, prompt: str) -> dict[str, object]:
            self.calls += 1
            self.connection.close()
            return {"score": 77, "summary": "fake ai", "drivers": ["materialized"], "risks": []}

    try:
        with connect(test_db) as setup_connection:
            ensure_transcripts_schema(setup_connection)
            setup_connection.executemany(
                """
                INSERT INTO earnings_transcripts(ticker, fiscal_year, quarter, transcript)
                VALUES (?, 2026, 1, ?)
                """,
                [
                    ("AAA", "first candidate strong demand"),
                    ("BBB", "second candidate strong demand"),
                ],
            )

        connection = get_connection(test_db)
        try:
            candidates = pd.DataFrame(
                {
                    "ticker": ["AAA", "BBB"],
                    "gics_sector": ["Tech", "Tech"],
                    "composite_score": [80.0, 75.0],
                    "long_candidate": [True, True],
                    "short_candidate": [False, False],
                }
            )
            client = ClosingClient(connection)
            monkeypatch.setattr("run_analysis.create_analysis_client", lambda config: client)

            payload = run_analysis_pipeline(
                candidates,
                config={"analysis": {"cache_table": "analysis_results", "result_ttl_days": 30}},
                connection=connection,
                cache_db_path=test_db,
                output_path=output_path,
                reports_dir=reports_dir,
                use_ai=True,
            )
        finally:
            try:
                connection.close()
            except Exception:
                pass

        assert payload["rows_analyzed"] == 2
        assert client.calls == 2
        results = pd.read_csv(output_path)
        assert results["ticker"].tolist() == ["AAA", "BBB"]
    finally:
        _cleanup(test_db)
        if output_path.exists():
            output_path.unlink()
        if reports_dir.exists():
            shutil.rmtree(reports_dir)


def test_run_analysis_requires_ai_from_config_even_without_cli_flag(tmp_path: Path, monkeypatch) -> None:
    test_db = PROJECT_ROOT / "cache" / f"analysis_required_ai_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"analysis_required_ai_{tmp_path.name}.csv"
    reports_dir = PROJECT_ROOT / "output" / "reports" / f"analysis_required_ai_{tmp_path.name}"
    _cleanup(test_db)
    if output_path.exists():
        output_path.unlink()
    if reports_dir.exists():
        shutil.rmtree(reports_dir)

    class RequiredClient:
        model = "fake-ai"

        def __init__(self) -> None:
            self.calls = 0

        def complete_json(self, prompt: str) -> dict[str, object]:
            self.calls += 1
            return {"score": 91, "summary": "mandatory Codex analysis", "drivers": ["required"], "risks": []}

    client = RequiredClient()

    try:
        with connect(test_db) as connection:
            ensure_transcripts_schema(connection)
            connection.execute(
                """
                INSERT INTO earnings_transcripts(ticker, fiscal_year, quarter, transcript)
                VALUES ('AAA', 2026, 1, 'required AI should analyze this ticker')
                """
            )
            candidates = pd.DataFrame(
                {
                    "ticker": ["AAA"],
                    "gics_sector": ["Tech"],
                    "composite_score": [80.0],
                    "long_candidate": [True],
                    "short_candidate": [False],
                }
            )
            connection.commit()
            monkeypatch.setattr("run_analysis.create_analysis_client", lambda config: client)

            payload = run_analysis_pipeline(
                candidates,
                config={"analysis": {"require_ai": True, "cache_table": "analysis_results", "result_ttl_days": 30}},
                connection=connection,
                cache_db_path=test_db,
                output_path=output_path,
                reports_dir=reports_dir,
                use_ai=False,
            )

        results = pd.read_csv(output_path)
        assert payload["ai_calls_executed"] is True
        assert payload["ai_required"] is True
        assert client.calls == 1
        assert results.loc[0, "analysis_summaries"] == "mandatory Codex analysis"
    finally:
        _cleanup(test_db)
        if output_path.exists():
            output_path.unlink()
        if reports_dir.exists():
            shutil.rmtree(reports_dir)


def test_required_ai_ignores_local_heuristic_cache(tmp_path: Path, monkeypatch) -> None:
    test_db = PROJECT_ROOT / "cache" / f"analysis_required_ai_cache_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"analysis_required_ai_cache_{tmp_path.name}.csv"
    reports_dir = PROJECT_ROOT / "output" / "reports" / f"analysis_required_ai_cache_{tmp_path.name}"
    _cleanup(test_db)
    if output_path.exists():
        output_path.unlink()
    if reports_dir.exists():
        shutil.rmtree(reports_dir)

    class RequiredClient:
        model = "fake-ai"

        def __init__(self) -> None:
            self.calls = 0

        def complete_json(self, prompt: str) -> dict[str, object]:
            self.calls += 1
            return {"score": 88, "summary": "fresh Codex analysis", "drivers": ["fresh"], "risks": []}

    client = RequiredClient()

    try:
        AnalysisCache(test_db, table_name="analysis_results").set(
            "earnings",
            "AAA",
            "latest",
            {"score": 50, "summary": "Local heuristic earnings analysis for AAA.", "drivers": [], "risks": []},
            model=None,
        )
        with connect(test_db) as connection:
            ensure_transcripts_schema(connection)
            connection.execute(
                """
                INSERT INTO earnings_transcripts(ticker, fiscal_year, quarter, transcript)
                VALUES ('AAA', 2026, 1, 'cached local heuristic must not satisfy required AI')
                """
            )
            candidates = pd.DataFrame(
                {
                    "ticker": ["AAA"],
                    "gics_sector": ["Tech"],
                    "composite_score": [80.0],
                    "long_candidate": [True],
                    "short_candidate": [False],
                }
            )
            connection.commit()
            monkeypatch.setattr("run_analysis.create_analysis_client", lambda config: client)

            run_analysis_pipeline(
                candidates,
                config={"analysis": {"require_ai": True, "cache_table": "analysis_results", "result_ttl_days": 30}},
                connection=connection,
                cache_db_path=test_db,
                output_path=output_path,
                reports_dir=reports_dir,
                use_ai=False,
            )

        results = pd.read_csv(output_path)
        assert client.calls == 1
        assert results.loc[0, "analysis_summaries"] == "fresh Codex analysis"
    finally:
        _cleanup(test_db)
        if output_path.exists():
            output_path.unlink()
        if reports_dir.exists():
            shutil.rmtree(reports_dir)


def test_run_analysis_cli_invokes_ai_after_main_db_context_closes(tmp_path: Path, monkeypatch) -> None:
    test_db = PROJECT_ROOT / "cache" / f"analysis_cli_ai_{tmp_path.name}.sqlite3"
    input_path = PROJECT_ROOT / "output" / f"analysis_cli_ai_{tmp_path.name}_input.csv"
    output_path = PROJECT_ROOT / "output" / f"analysis_cli_ai_{tmp_path.name}.csv"
    reports_dir = PROJECT_ROOT / "output" / "reports" / f"analysis_cli_ai_{tmp_path.name}"
    _cleanup(test_db)
    for path in [input_path, output_path]:
        if path.exists():
            path.unlink()
    if reports_dir.exists():
        shutil.rmtree(reports_dir)

    original_connect = run_analysis_module.connect
    state = {"main_context_open": False, "calls": 0}

    class AssertingClient:
        model = "fake-ai"

        def complete_json(self, prompt: str) -> dict[str, object]:
            assert state["main_context_open"] is False
            state["calls"] += 1
            return {"score": 81, "summary": "fake ai", "drivers": ["after-close"], "risks": []}

    from contextlib import contextmanager

    @contextmanager
    def tracking_connect(*args, **kwargs):
        with original_connect(*args, **kwargs) as connection:
            state["main_context_open"] = True
            try:
                yield connection
            finally:
                state["main_context_open"] = False

    try:
        with connect(test_db) as connection:
            ensure_transcripts_schema(connection)
            connection.execute(
                """
                INSERT INTO earnings_transcripts(ticker, fiscal_year, quarter, transcript)
                VALUES ('AAA', 2026, 1, 'CLI candidate strong demand')
                """
            )
        pd.DataFrame(
            {
                "ticker": ["AAA"],
                "gics_sector": ["Tech"],
                "composite_score": [80.0],
                "long_candidate": [True],
                "short_candidate": [False],
            }
        ).to_csv(input_path, index=False)

        monkeypatch.setattr(run_analysis_module, "connect", tracking_connect)
        monkeypatch.setattr(
            run_analysis_module,
            "load_config",
            lambda config_path: {
                "project": {"default_db_path": str(test_db)},
                "analysis": {"cache_table": "analysis_results", "result_ttl_days": 30},
            },
        )
        monkeypatch.setattr(run_analysis_module, "create_analysis_client", lambda config: AssertingClient())

        assert run_analysis_module.main(
            [
                "--no-dry-run",
                "--use-ai",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--reports-dir",
                str(reports_dir),
            ]
        ) == 0

        assert state["calls"] == 1
    finally:
        _cleanup(test_db)
        for path in [input_path, output_path]:
            if path.exists():
                path.unlink()
        if reports_dir.exists():
            shutil.rmtree(reports_dir)


def test_l3_local_analyzers_emit_prompt_schema_fields() -> None:
    earnings = EarningsAnalyzer().local_payload("AAA", "beat raised guidance margin expansion")
    filing = FilingAnalyzer().local_payload("AAA", {"fundamental_history": [{"gross_margin": 0.5, "free_cash_flow": 10}]})
    risk = RiskAnalyzer().local_payload("AAA", "regulatory high leverage liquidity risk")
    insider = InsiderAnalyzer().local_payload("AAA", [{"transaction_type": "open_market_purchase", "is_ceo_cfo": 1}])

    assert {"management_confidence", "bull_case", "bear_case", "one_line_summary"}.issubset(earnings)
    assert {"earnings_quality_score", "balance_sheet_score", "red_flags", "green_flags", "risk_level"}.issubset(filing)
    assert {"new_risks", "material_risks", "boilerplate_percentage", "risk_severity", "one_line_summary"}.issubset(risk)
    assert {"signal_strength", "confidence", "key_transactions", "reasoning", "one_line_summary"}.issubset(insider)


def test_transaction_costs_are_positive_and_scale_with_side() -> None:
    buy = estimate_transaction_cost(quantity=100, price=10.0, side="buy", commission_bps=1, spread_bps=4, slippage_bps=5)
    sell = estimate_transaction_cost(quantity=-100, price=10.0, side="sell", commission_bps=1, spread_bps=4, slippage_bps=5)

    assert buy.notional == pytest.approx(1_000.0)
    assert buy.total_cost_usd == pytest.approx(1.0)
    assert sell.notional == pytest.approx(1_000.0)
    assert sell.total_cost_usd == pytest.approx(1.0)


def test_beta_calculation_and_portfolio_beta() -> None:
    benchmark = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02], name="SPY")
    asset = 1.5 * benchmark

    assert calculate_beta(asset, benchmark) == pytest.approx(1.5)
    assert calculate_portfolio_beta({"AAA": 0.5, "BBB": -0.25}, {"AAA": 1.2, "BBB": 0.8}) == pytest.approx(0.4)


def test_conviction_tilt_sizing_respects_basic_constraints() -> None:
    candidates = pd.DataFrame(
        {
            "ticker": ["A", "B", "C", "D", "E", "F"],
            "combined_score": [95, 88, 70, 35, 15, 5],
            "sector": ["Tech", "Tech", "Health", "Health", "Energy", "Energy"],
            "beta": [1.1, 1.0, 0.9, 0.8, 1.2, 1.1],
        }
    )
    portfolio = build_conviction_tilt_portfolio(
        candidates,
        target_longs=2,
        target_shorts=2,
        long_gross_exposure=0.4,
        short_gross_exposure=0.4,
        max_position_weight=0.25,
        min_position_weight=0.05,
        max_sector_gross_weight=0.5,
        beta_cap_abs=None,
    )

    assert set(portfolio["side"]) == {"long", "short"}
    assert portfolio["weight"].abs().max() <= 0.25 + 1e-12
    assert portfolio.loc[portfolio["weight"] > 0, "weight"].sum() == pytest.approx(0.4)
    assert portfolio.loc[portfolio["weight"] < 0, "weight"].abs().sum() == pytest.approx(0.4)
    assert len(portfolio) == 4
