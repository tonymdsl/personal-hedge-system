"""Markdown report generation for local research artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping
from datetime import datetime, timezone

import pandas as pd

from common.config import PROJECT_ROOT, ensure_project_path


def _records(results: pd.DataFrame | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(results, pd.DataFrame):
        return results.to_dict(orient="records")
    return [dict(item) for item in results]


def generate_analysis_report(
    results: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    title: str = "Layer 3 Analysis Report",
    output_path: str | Path | None = None,
    compliance_footer: str = "For research and paper-trading use only. Not investment advice.",
) -> str:
    """Create a markdown report and optionally write it inside the project root."""

    rows = _records(results)
    lines = [f"# {title}", "", compliance_footer, ""]
    if not rows:
        lines.extend(["No analysis results available.", ""])
    else:
        lines.extend(["| Ticker | Score | Summary |", "| --- | ---: | --- |"])
        for row in rows:
            ticker = row.get("ticker", "")
            score = row.get("combined_score", row.get("score", ""))
            try:
                score_text = f"{float(score):.1f}"
            except (TypeError, ValueError):
                score_text = str(score)
            summary = str(row.get("summary", row.get("thesis", ""))).replace("\n", " ")
            lines.append(f"| {ticker} | {score_text} | {summary} |")
        lines.append("")
    markdown = "\n".join(lines)
    if output_path is not None:
        path = ensure_project_path(output_path, PROJECT_ROOT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    return markdown


def write_report(*args: Any, **kwargs: Any) -> str:
    return generate_analysis_report(*args, **kwargs)


def generate_candidate_report(row: Mapping[str, Any], analyzer_results: Iterable[Mapping[str, Any]] | None = None) -> str:
    """Create a per-candidate markdown report with quantitative and L3 context."""

    ticker = str(row.get("ticker", "UNKNOWN")).upper()
    side = "LONG" if bool(row.get("long_candidate", False)) else ("SHORT" if bool(row.get("short_candidate", False)) else "WATCH")
    score = row.get("combined_score", row.get("composite_score", row.get("quant_score", "")))
    quant = row.get("quant_score", row.get("composite_score", ""))
    qualitative = row.get("qualitative_score", "")
    sector = row.get("gics_sector", row.get("sector", ""))
    lines = [
        f"# {ticker} Layer 3 Research",
        "",
        f"- Side: {side}",
        f"- Sector: {sector}",
        f"- Combined score: {score}",
        f"- Quant score: {quant}",
        f"- Qualitative score: {qualitative}",
        "",
        "## Analyzer Summaries",
        "",
    ]
    results = list(analyzer_results or [])
    if not results:
        lines.extend(["No analyzer results available.", ""])
    for result in results:
        lines.extend(
            [
                f"### {str(result.get('analyzer', '')).title()}",
                "",
                f"- Score: {result.get('score', '')}",
                f"- Summary: {result.get('summary', '')}",
            ]
        )
        drivers = result.get("drivers") or []
        risks = result.get("risks") or []
        if drivers:
            lines.append(f"- Drivers: {', '.join(str(item) for item in drivers)}")
        if risks:
            lines.append(f"- Risks: {', '.join(str(item) for item in risks)}")
        raw = result.get("raw") or {}
        if isinstance(raw, Mapping):
            one_line = raw.get("one_line_summary") or raw.get("reasoning") or raw.get("risk_level")
            if one_line:
                lines.append(f"- Detail: {one_line}")
        lines.append("")
    lines.extend(["## Notes", "", "For research and paper-trading use only. Not investment advice.", ""])
    return "\n".join(str(line) for line in lines)


def write_candidate_reports(
    results: pd.DataFrame | Iterable[Mapping[str, Any]],
    analyzer_results_by_ticker: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    reports_dir: str | Path = "output/reports",
    timestamp: str | None = None,
) -> list[Path]:
    """Write one markdown report per analyzed candidate."""

    rows = _records(results)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_dir = ensure_project_path(reports_dir, PROJECT_ROOT) / stamp
    base_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for row in rows:
        ticker = str(row.get("ticker", "UNKNOWN")).upper()
        path = base_dir / f"{ticker}.md"
        markdown = generate_candidate_report(row, analyzer_results_by_ticker.get(ticker, []))
        path.write_text(markdown, encoding="utf-8")
        paths.append(path)
    return paths
