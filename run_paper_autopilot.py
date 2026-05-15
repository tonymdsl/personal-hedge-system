from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from typing import Any

from autopilot.runner import PaperAutopilotError, PaperAutopilotRunner
from common.config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the paper-only autopilot pipeline.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one paper autopilot cycle.")
    mode.add_argument("--loop", action="store_true", help="Run continuously with a sleep interval.")
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--force", action="store_true", help="Execute even when the plan hash is a duplicate.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--nav", type=float, default=None)
    parser.add_argument("--data-limit", type=int, default=None)
    parser.add_argument("--analysis-limit", type=int, default=None)
    parser.add_argument("--use-ai", action="store_true")
    return parser


def _apply_cli_overrides(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    config = dict(config)
    config["_config_path"] = args.config
    autopilot = dict(config.get("autopilot", {}) or {})
    if args.nav is not None:
        autopilot["nav"] = args.nav
    if args.data_limit is not None:
        autopilot["data_limit"] = args.data_limit
    if args.analysis_limit is not None:
        autopilot["analysis_limit"] = args.analysis_limit
    if args.use_ai:
        autopilot["use_ai"] = True
    config["autopilot"] = autopilot
    return config


def _print_result(result: dict[str, Any]) -> None:
    payload = {
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "plan_hash": result.get("plan_hash"),
        "current_step": result.get("current_step"),
    }
    if result.get("error"):
        payload["error"] = result.get("error")
    print(json.dumps(payload, sort_keys=True, default=str))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = _apply_cli_overrides(load_config(args.config), args)
    runner = PaperAutopilotRunner(config, state_path=args.state_path)

    if args.loop:
        while True:
            try:
                result = runner.run_once(force=args.force)
            except PaperAutopilotError as exc:
                result = {
                    "status": "failed",
                    "run_id": None,
                    "plan_hash": None,
                    "current_step": None,
                    "error": str(exc),
                }
            _print_result(result)
            time.sleep(args.interval_seconds)

    _print_result(runner.run_once(force=args.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
