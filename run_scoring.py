"""Layer 2 scoring command."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from common.cli import add_common_arguments
from common.config import PROJECT_ROOT, load_config
from common.db import connect
from factors.composite import export_scored_universe, score_composite, top_longs_shorts
from factors.crowding import daily_factor_return_spreads, detect_crowding
from factors.growth import score_growth
from factors.inputs import build_factor_inputs_from_database, export_factor_inputs
from factors.insider import score_insider
from factors.institutional import score_institutional
from factors.momentum import score_momentum
from factors.quality import score_quality
from factors.revisions import score_revisions
from factors.short_interest import score_short_interest
from factors.value import score_value


FACTOR_SCORERS = (
    score_momentum,
    score_value,
    score_quality,
    score_growth,
    score_revisions,
    score_short_interest,
    score_insider,
    score_institutional,
)

FACTOR_SCORE_COLUMNS = (
    "momentum_score",
    "value_score",
    "quality_score",
    "growth_score",
    "revisions_score",
    "short_interest_score",
    "insider_score",
    "institutional_score",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Meridian Layer 2 factor scoring.')
    add_common_arguments(parser)
    parser.add_argument('--ticker', default=None, help='Optional single ticker filter.')
    parser.add_argument('--input', default='output/factor_inputs.csv', help='CSV of factor inputs/scores.')
    parser.add_argument('--vix', type=float, default=None, help='Optional VIX value for regime-conditioned weights.')
    return parser


def _load_input(path: str, *, config: dict | None = None) -> pd.DataFrame:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if p.exists():
        return pd.read_csv(p)
    with connect(config=config) as connection:
        frame = build_factor_inputs_from_database(connection)
    if not frame.empty:
        export_factor_inputs(frame, p)
    return frame if not frame.empty else pd.DataFrame(columns=['ticker', 'gics_sector'])


def latest_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or 'date' not in frame.columns:
        return frame
    dates = pd.to_datetime(frame['date'], errors='coerce')
    if dates.notna().sum() == 0:
        return frame
    latest_date = dates.max()
    return frame[dates == latest_date].copy()


def score_all_factors(frame: pd.DataFrame, *, config: dict | None = None, vix: float | None = None) -> pd.DataFrame:
    """Score all eight Layer 2 factors before computing composite candidates."""

    scored = frame.copy()
    for scorer in FACTOR_SCORERS:
        scored = scorer(scored)
    return score_composite(scored, config=config, vix=vix)


def build_crowding_warnings(scored: pd.DataFrame, config: dict) -> list[dict[str, object]]:
    if scored.empty:
        return []
    crowding_config = config.get('scoring', {}).get('crowding', {})
    spreads = daily_factor_return_spreads(scored, FACTOR_SCORE_COLUMNS)
    return detect_crowding(
        spreads,
        window=int(crowding_config.get('rolling_window_days', 60)),
        min_periods=crowding_config.get('min_periods'),
        zscore_threshold=float(crowding_config.get('zscore_warning_threshold', 2.0)),
        deviation_threshold=float(crowding_config.get('correlation_deviation_threshold', 0.40)),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    frame = _load_input(args.input, config=config)
    if args.ticker and not frame.empty and 'ticker' in frame.columns:
        frame = frame[frame['ticker'].astype(str).str.upper() == args.ticker.upper()]
    scored = score_all_factors(frame, config=config, vix=args.vix) if not frame.empty else frame
    latest_scored = latest_snapshot(scored)
    output_path = export_scored_universe(latest_scored) if not latest_scored.empty else None
    longs, shorts = top_longs_shorts(latest_scored, n=5) if not latest_scored.empty else (pd.DataFrame(), pd.DataFrame())
    warnings = build_crowding_warnings(scored, config) if not scored.empty else []
    payload = {
        'rows_scored': int(len(latest_scored)),
        'history_rows_scored': int(len(scored)),
        'output': str(output_path) if output_path else None,
        'top_5_longs': longs.get('ticker', pd.Series(dtype=str)).tolist(),
        'top_5_shorts': shorts.get('ticker', pd.Series(dtype=str)).tolist(),
        'crowding_warnings': warnings,
        'degenerate_factor_warnings': [
            column for column in FACTOR_SCORE_COLUMNS if column in scored.columns and scored[column].nunique(dropna=True) <= 1
        ] if not scored.empty else [],
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
