"""Shared CLI helpers for scaffold scripts."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from .config import load_config
from .logging import setup_logging


def add_common_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add config and dry-run arguments used by all scaffold scripts."""

    parser.add_argument("--config", default=None, help="Path to config.yaml (default: project config.yaml).")
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep the command side-effect-safe. Defaults to true.",
    )
    return parser


def run_layer_stub(layer_name: str, args: argparse.Namespace, extra: dict[str, Any] | None = None) -> int:
    """Load config, log a safe placeholder message, and exit successfully."""

    config = load_config(args.config)
    logger = setup_logging(layer_name)
    execution_config = config.get("execution", {}) if isinstance(config.get("execution", {}), dict) else {}
    project_config = config.get("project", {}) if isinstance(config.get("project", {}), dict) else {}

    payload: dict[str, Any] = {
        "project": project_config.get("name", "Meridian Capital Partners"),
        "layer": layer_name,
        "mode": project_config.get("mode", "research_paper"),
        "dry_run": bool(getattr(args, "dry_run", True)),
        "execution_mode": execution_config.get("mode", "paper"),
        "allow_live_trading": bool(execution_config.get("allow_live_trading", False)),
        "status": "scaffold_only_no_layer_logic_executed",
    }
    if extra:
        payload["arguments"] = extra

    logger.info("Scaffold command invoked: %s", json.dumps(payload, sort_keys=True))
    print(json.dumps(payload, indent=2, sort_keys=True))
    print("No live orders, external writes, or layer-specific processing were performed.")
    return 0


def parse_and_run(
    layer_name: str,
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None = None,
    *,
    extra_from_args: callable | None = None,
) -> int:
    """Parse args for a scaffold command and run the shared placeholder."""

    args = parser.parse_args(argv)
    extra = extra_from_args(args) if extra_from_args else None
    return run_layer_stub(layer_name, args, extra=extra)
