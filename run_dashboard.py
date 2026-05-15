"""Layer 7 Streamlit dashboard launcher."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from common.cli import add_common_arguments
from common.config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Meridian Streamlit dashboard launcher.')
    add_common_arguments(parser)
    parser.add_argument('--serve', action='store_true', help='Actually launch streamlit; otherwise print command only.')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    dashboard = config.get('dashboard', {})
    port = os.getenv('PORT') or dashboard.get('port', 8501)
    host = os.getenv('STREAMLIT_SERVER_ADDRESS') or os.getenv('HOST') or dashboard.get('host', '127.0.0.1')
    if host == 'localhost':
        host = '127.0.0.1'
    cmd = [
        sys.executable,
        '-m',
        'streamlit',
        'run',
        'dashboard/app.py',
        '--server.port',
        str(port),
        '--server.address',
        str(host),
        '--server.headless',
        'true',
        '--browser.gatherUsageStats',
        'false',
        '--server.fileWatcherType',
        'none',
    ]
    if args.serve:
        return subprocess.call(cmd)
    print(json.dumps({'serve': False, 'command': cmd}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
