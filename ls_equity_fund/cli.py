"""Tiny package-level CLI for sanity checks."""

from __future__ import annotations

import json

from common.config import load_config


def main() -> int:
    config = load_config()
    project = config.get("project", {})
    print(json.dumps({"project": project.get("name"), "mode": project.get("mode")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
