"""Layer 1 data ingestion command."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common.cli import add_common_arguments
from common.config import PROJECT_ROOT, load_config
from common.db import connect
from common.logging import setup_logging
from data.universe import ingest_universe, get_universe_tickers
from data.market_data import ingest_market_data
from data.fundamentals import ingest_fundamentals
from data.short_interest import ingest_short_interest
from data.estimates import ingest_estimates
from data.earnings_calendar import ingest_earnings_calendar
from data.transcripts import ingest_transcripts
from data.sec_data import ingest_sec_filings
from data.institutional import ingest_13f
from factors.inputs import build_factor_inputs_from_database, export_factor_inputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Meridian Layer 1 data refresh.')
    add_common_arguments(parser)
    parser.add_argument('--no-filings', action='store_true', help='Skip SEC filings/Form 4 for faster daily runs.')
    parser.add_argument('--no-13f', action='store_true', help='Skip 13F institutional filings for faster daily runs.')
    parser.add_argument('--limit', type=int, default=0, help='Optional ticker limit for smoke runs.')
    return parser


def run_refresh(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    logger = setup_logging('data')
    counts: dict[str, object] = {'dry_run': bool(args.dry_run)}
    with connect(config=config) as connection:
        if args.dry_run:
            tickers: list[str] = []
            counts['universe'] = {'skipped': True, 'reason': 'dry_run'}
            counts['short_interest'] = ingest_short_interest([], connection, dry_run=True)
            counts['estimates'] = ingest_estimates([], connection, dry_run=True)
            counts['calendar'] = ingest_earnings_calendar([], connection, dry_run=True)
            counts['transcripts'] = ingest_transcripts([], connection, config=config)
        else:
            universe_result = ingest_universe(connection, config=config, force_refresh=False)
            counts['universe'] = {
                'rows': universe_result.get('rows_written', universe_result.get('count', 0)),
                'source': universe_result.get('source', config.get('data', {}).get('universe', {}).get('source', 'wikipedia_sp500')),
            }
            tickers = get_universe_tickers(connection)
            if args.limit:
                tickers = tickers[: args.limit]
            counts['prices'] = ingest_market_data(connection, tickers, config=config)
            counts['fundamentals'] = ingest_fundamentals(connection, tickers, config=config)
            counts['short_interest'] = ingest_short_interest(tickers, connection, dry_run=False)
            counts['estimates'] = ingest_estimates(tickers, connection, dry_run=False)
            counts['calendar'] = ingest_earnings_calendar(tickers, connection, dry_run=False)
            counts['transcripts'] = ingest_transcripts(tickers, connection, config=config)
        counts['sec'] = ingest_sec_filings(tickers, connection, config=config, no_filings=args.no_filings or args.dry_run)
        counts['institutional'] = ingest_13f(tickers, connection, config=config, no_13f=args.no_13f or args.dry_run)
        if args.dry_run:
            counts['factor_inputs'] = {'skipped': True, 'reason': 'dry_run', 'rows': 0}
        else:
            factor_inputs = build_factor_inputs_from_database(connection)
            output_path = export_factor_inputs(factor_inputs) if not factor_inputs.empty else None
            if output_path:
                output_path_obj = Path(output_path)
                relative_output = output_path_obj.relative_to(PROJECT_ROOT) if output_path_obj.is_absolute() else output_path_obj
            else:
                relative_output = None
            counts['factor_inputs'] = {
                'rows': int(len(factor_inputs)),
                'output': relative_output.as_posix() if relative_output else None,
            }
    logger.info('Data refresh counts: %s', json.dumps(counts, sort_keys=True, default=str))
    return counts


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    counts = run_refresh(args)
    print(json.dumps(counts, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
