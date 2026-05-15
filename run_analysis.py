"""Layer 3 qualitative analysis command."""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from common.cli import add_common_arguments
from common.config import PROJECT_ROOT, ensure_project_path, load_config
from common.db import connect, default_database_path, table_exists
from analysis.api_client import create_analysis_client, estimate_call_cost
from analysis.cache import AnalysisCache
from analysis.combined_score import combine_scores
from analysis.cost_tracker import CostTracker
from analysis.earnings_analyzer import EarningsAnalyzer
from analysis.filing_analyzer import FilingAnalyzer
from analysis.insider_analyzer import InsiderAnalyzer
from analysis.report_generator import write_candidate_reports
from analysis.risk_analyzer import RiskAnalyzer


ANALYZER_CLASSES = {
    "earnings": EarningsAnalyzer,
    "filing": FilingAnalyzer,
    "risk": RiskAnalyzer,
    "insider": InsiderAnalyzer,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meridian Layer 3 qualitative analysis.")
    add_common_arguments(parser)
    parser.add_argument("--estimate-cost", action="store_true", help="Estimate selected analyzer prompt cost without running analysis.")
    parser.add_argument("--ticker", default=None, help="Analyze one ticker.")
    parser.add_argument("--sector", default=None, help="Analyze candidates in one sector.")
    parser.add_argument("--input", default="output/scored_universe_latest.csv", help="Layer 2 scored universe CSV.")
    parser.add_argument("--output", default="output/analysis_results_latest.csv", help="Layer 3 output CSV.")
    parser.add_argument("--reports-dir", default="output/reports", help="Directory for per-candidate markdown reports.")
    parser.add_argument("--limit", type=int, default=0, help="Optional selected-candidate limit.")
    parser.add_argument("--use-ai", action="store_true", help="Use the configured Codex analysis client instead of local heuristics.")
    return parser


def _display_path(path: str | Path | None) -> str | None:
    if path is None:
        return None
    resolved = Path(path)
    try:
        return resolved.resolve(strict=False).relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _read_csv(path: str | Path) -> pd.DataFrame:
    resolved = ensure_project_path(path, PROJECT_ROOT)
    if not resolved.exists():
        return pd.DataFrame()
    return pd.read_csv(resolved)


def _sector_col(frame: pd.DataFrame) -> str:
    return "gics_sector" if "gics_sector" in frame.columns else "sector"


def _score_col(frame: pd.DataFrame) -> str:
    for column in ["combined_score", "composite_score", "quant_score", "score"]:
        if column in frame.columns:
            return column
    return "ticker"


def select_analysis_candidates(
    frame: pd.DataFrame,
    *,
    ticker: str | None = None,
    sector: str | None = None,
    long_short_limit: int = 20,
    limit: int | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()

    selected = frame.copy()
    if "ticker" in selected.columns:
        selected["ticker"] = selected["ticker"].astype(str).str.upper().str.strip()
    if ticker and "ticker" in selected.columns:
        selected = selected[selected["ticker"] == str(ticker).upper().strip()]

    sector_column = _sector_col(selected)
    if sector and sector_column in selected.columns:
        selected = selected[selected[sector_column].astype(str).str.casefold() == str(sector).casefold()]

    score_column = _score_col(selected)
    if ticker:
        result = selected
    elif {"long_candidate", "short_candidate"}.issubset(selected.columns):
        longs = selected[selected["long_candidate"].astype(bool)].sort_values(score_column, ascending=False).head(long_short_limit)
        shorts = selected[selected["short_candidate"].astype(bool)].sort_values(score_column, ascending=True).head(long_short_limit)
        result = pd.concat([longs, shorts], ignore_index=True).drop_duplicates(subset=["ticker"], keep="first")
    else:
        result = selected.sort_values(score_column, ascending=False)

    if limit and limit > 0:
        result = result.head(limit)
    return result.reset_index(drop=True)


def _query_frame(connection, sql: str, params: tuple[object, ...], table_name: str) -> pd.DataFrame:
    if not table_exists(connection, table_name):
        return pd.DataFrame()
    return pd.read_sql_query(sql, connection, params=params)


def _latest_transcript(connection, ticker: str) -> str | None:
    frame = _query_frame(
        connection,
        """
        SELECT transcript
        FROM earnings_transcripts
        WHERE ticker = ?
        ORDER BY fiscal_year DESC, quarter DESC
        LIMIT 1
        """,
        (ticker,),
        "earnings_transcripts",
    )
    if frame.empty:
        return None
    text = str(frame.iloc[0].get("transcript") or "").strip()
    return text[:120_000] if text else None


def _fundamental_history(connection, ticker: str) -> list[dict[str, Any]]:
    frame = _query_frame(
        connection,
        """
        SELECT *
        FROM fundamental_ratios
        WHERE ticker = ?
        ORDER BY fiscal_date DESC
        LIMIT 8
        """,
        (ticker,),
        "fundamental_ratios",
    )
    return frame.to_dict(orient="records") if not frame.empty else []


def _risk_material(connection, ticker: str) -> dict[str, Any] | None:
    frame = _query_frame(
        connection,
        """
        SELECT form_type, filing_date, url
        FROM sec_filings
        WHERE ticker = ? AND form_type = '10-K'
        ORDER BY filing_date DESC
        LIMIT 2
        """,
        (ticker,),
        "sec_filings",
    )
    if frame.empty:
        return None
    return {"filings": frame.to_dict(orient="records")}


def _insider_activity(connection, ticker: str) -> list[dict[str, Any]]:
    frame = _query_frame(
        connection,
        """
        SELECT *
        FROM insider_transactions
        WHERE ticker = ?
        ORDER BY transaction_date DESC
        """,
        (ticker,),
        "insider_transactions",
    )
    if frame.empty:
        return []
    frame["transaction_date"] = pd.to_datetime(frame["transaction_date"], errors="coerce")
    max_date = frame["transaction_date"].max()
    if pd.isna(max_date):
        return frame.to_dict(orient="records")
    cutoff = max_date.date() - timedelta(days=90)
    frame = frame[frame["transaction_date"].dt.date >= cutoff]
    frame["transaction_date"] = frame["transaction_date"].dt.strftime("%Y-%m-%d")
    return frame.to_dict(orient="records")


def build_analysis_payloads(connection, candidate: Mapping[str, Any]) -> dict[str, Any]:
    ticker = str(candidate.get("ticker", "")).upper().strip()
    payloads: dict[str, Any] = {}
    transcript = _latest_transcript(connection, ticker)
    if transcript:
        payloads["earnings"] = transcript

    fundamentals = _fundamental_history(connection, ticker)
    if fundamentals:
        payloads["filing"] = {"candidate": dict(candidate), "fundamental_history": fundamentals}

    risk = _risk_material(connection, ticker)
    if risk:
        payloads["risk"] = risk

    insider = _insider_activity(connection, ticker)
    if insider:
        payloads["insider"] = insider
    return payloads


def materialize_analysis_jobs(connection, candidates: pd.DataFrame) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for candidate in candidates.to_dict(orient="records"):
        ticker = str(candidate.get("ticker", "")).upper().strip()
        if not ticker:
            continue
        jobs.append(
            {
                "ticker": ticker,
                "candidate": dict(candidate),
                "payloads": build_analysis_payloads(connection, candidate),
            }
        )
    return jobs


def _analysis_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    section = config.get("analysis", {}) if isinstance(config, Mapping) else {}
    return section if isinstance(section, Mapping) else {}


def _analysis_requires_ai(config: Mapping[str, Any]) -> bool:
    return bool(_analysis_config(config).get("require_ai", False))


def _cache_for_config(config: Mapping[str, Any], cache_db_path: str | Path | None = None) -> AnalysisCache:
    analysis = _analysis_config(config)
    db_path = cache_db_path or default_database_path(config)
    return AnalysisCache(
        db_path,
        table_name=str(analysis.get("cache_table", "analysis_results")),
        ttl_days=analysis.get("result_ttl_days", 30),
    )


def _candidate_quant_score(candidate: Mapping[str, Any]) -> float:
    for column in ["combined_score", "composite_score", "quant_score", "score"]:
        if column in candidate:
            value = pd.to_numeric(pd.Series([candidate[column]]), errors="coerce").iloc[0]
            if pd.notna(value):
                return float(value)
    return 50.0


def estimate_selected_cost(
    candidates: pd.DataFrame,
    *,
    config: Mapping[str, Any],
    connection,
) -> dict[str, Any]:
    estimates = []
    for candidate in candidates.to_dict(orient="records"):
        ticker = str(candidate.get("ticker", "")).upper()
        payloads = build_analysis_payloads(connection, candidate)
        for analyzer_name, payload in payloads.items():
            analyzer = ANALYZER_CLASSES[analyzer_name](config=config)
            estimates.append(
                estimate_call_cost(
                    analyzer.build_prompt(ticker, payload),
                    expected_output_tokens=800,
                    config=config,
                    analyzer=analyzer_name,
                    ticker=ticker,
                    artifact_id="latest",
                )
            )
    return {
        "candidate_count": int(len(candidates)),
        "planned_calls": len(estimates),
        "estimated_input_tokens": sum(item.input_tokens for item in estimates),
        "estimated_output_tokens": sum(item.output_tokens for item in estimates),
        "estimated_cost_usd": sum(item.total_cost_usd for item in estimates),
    }


def run_analysis_pipeline(
    candidates: pd.DataFrame,
    *,
    config: Mapping[str, Any],
    connection,
    cache_db_path: str | Path | None = None,
    output_path: str | Path = "output/analysis_results_latest.csv",
    reports_dir: str | Path = "output/reports",
    use_ai: bool = False,
) -> dict[str, Any]:
    jobs = materialize_analysis_jobs(connection, candidates)
    return run_materialized_analysis_pipeline(
        jobs,
        config=config,
        cache_db_path=cache_db_path,
        output_path=output_path,
        reports_dir=reports_dir,
        use_ai=use_ai,
    )


def run_materialized_analysis_pipeline(
    jobs: list[Mapping[str, Any]],
    *,
    config: Mapping[str, Any],
    cache_db_path: str | Path | None = None,
    output_path: str | Path = "output/analysis_results_latest.csv",
    reports_dir: str | Path = "output/reports",
    use_ai: bool = False,
) -> dict[str, Any]:
    analysis = _analysis_config(config)
    require_ai = _analysis_requires_ai(config)
    effective_use_ai = bool(use_ai or require_ai)
    cache = _cache_for_config(config, cache_db_path=cache_db_path)
    tracker = CostTracker(float(analysis.get("run_cost_ceiling_usd", 25.0) or 25.0))
    client = create_analysis_client(config) if effective_use_ai else None

    rows: list[dict[str, Any]] = []
    analyzer_results_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        ticker = str(job.get("ticker", "")).upper().strip()
        if not ticker:
            continue
        candidate = dict(job.get("candidate", {}))
        payloads = job.get("payloads", {})
        if not isinstance(payloads, Mapping):
            payloads = {}
        payloads = dict(payloads)
        if require_ai and not payloads:
            payloads = {
                "filing": {
                    "candidate": candidate,
                    "available_material": "No transcript, filing, risk, or insider payload was available; analyze the candidate snapshot only.",
                }
            }
        analyzer_results = []
        for analyzer_name, payload in payloads.items():
            analyzer = ANALYZER_CLASSES[analyzer_name](client=client, cache=cache, config=config, cost_tracker=tracker)
            result = analyzer.analyze(ticker, payload, artifact_id="latest", use_ai=effective_use_ai, require_ai=require_ai)
            analyzer_results.append(result.as_dict())

        qualitative_scores = [float(item["score"]) for item in analyzer_results if item.get("score") is not None]
        qualitative_score = sum(qualitative_scores) / len(qualitative_scores) if qualitative_scores else None
        row = dict(candidate)
        row["quant_score"] = _candidate_quant_score(candidate)
        row["qualitative_score"] = qualitative_score
        row["analysis_count"] = len(analyzer_results)
        row["analysis_summaries"] = " | ".join(str(item.get("summary", "")) for item in analyzer_results)
        row["analyzers_run"] = ",".join(str(item.get("analyzer", "")) for item in analyzer_results)
        rows.append(row)
        analyzer_results_by_ticker[ticker] = analyzer_results

    results = pd.DataFrame(rows)
    if not results.empty:
        sector_column = _sector_col(results)
        results = combine_scores(
            results,
            quant_col="quant_score",
            qualitative_col="qualitative_score",
            sector_col=sector_column,
            quantitative_weight=float(analysis.get("quantitative_weight", 0.60) or 0.60),
            qualitative_weight=float(analysis.get("qualitative_weight", 0.40) or 0.40),
        )

    resolved_output = ensure_project_path(output_path, PROJECT_ROOT)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(resolved_output, index=False)
    report_files = write_candidate_reports(results, analyzer_results_by_ticker, reports_dir=reports_dir) if not results.empty else []
    return {
        "rows_analyzed": int(len(results)),
        "output": _display_path(resolved_output),
        "report_files": [_display_path(path) for path in report_files],
        "cost": tracker.summary(),
        "ai_calls_executed": bool(effective_use_ai),
        "ai_required": bool(require_ai),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    candidates = select_analysis_candidates(
        _read_csv(args.input),
        ticker=args.ticker,
        sector=args.sector,
        limit=args.limit or None,
    )
    if args.estimate_cost:
        with connect(config=config) as connection:
            payload = {
                **estimate_selected_cost(candidates, config=config, connection=connection),
                "provider": config.get("analysis", {}).get("provider", "codex"),
                "ai_calls_executed": False,
                "analysis_enabled": config.get("analysis", {}).get("enabled", False),
                "ticker": args.ticker,
                "sector": args.sector,
            }
    else:
        if _analysis_requires_ai(config) and args.dry_run:
            raise SystemExit("analysis.require_ai=true requires --no-dry-run, or use --estimate-cost for a safe cost check.")
        with connect(config=config) as connection:
            jobs = materialize_analysis_jobs(connection, candidates)
        payload = run_materialized_analysis_pipeline(
            jobs,
            config=config,
            output_path=args.output,
            reports_dir=args.reports_dir,
            use_ai=bool((args.use_ai or _analysis_requires_ai(config)) and not args.dry_run),
        )
        payload["dry_run"] = bool(args.dry_run)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
