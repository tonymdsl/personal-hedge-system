"""Persist risk state to JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from common.config import PROJECT_ROOT, ensure_project_path


def write_risk_state(state: Mapping[str, object], path: str | Path = 'cache/risk_state.json') -> Path:
    output = ensure_project_path(path, PROJECT_ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(state), indent=2, sort_keys=True, default=str), encoding='utf-8')
    return output


def read_risk_state(path: str | Path = 'cache/risk_state.json') -> dict[str, object]:
    input_path = ensure_project_path(path, PROJECT_ROOT)
    if not input_path.exists():
        return {}
    return json.loads(input_path.read_text(encoding='utf-8'))
