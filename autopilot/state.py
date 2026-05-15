from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from common.config import PROJECT_ROOT

DEFAULT_STATE_PATH = Path("cache/autopilot_state.json")


def default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "mode": "paper",
        "enabled": True,
        "last_run_id": None,
        "last_plan_hash": None,
        "last_started_at": None,
        "last_finished_at": None,
        "last_status": "never_run",
        "last_error": None,
        "current_step": None,
        "runs": [],
    }


def _resolve_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    candidate = Path(path) if path is not None else DEFAULT_STATE_PATH
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def _with_defaults(value: Mapping[str, Any]) -> dict[str, Any]:
    state = default_state()
    state.update(dict(value))
    if not isinstance(state.get("runs"), list):
        state["runs"] = []
    return state


def load_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    resolved = _resolve_state_path(path)
    if not resolved.exists():
        return default_state()
    try:
        with resolved.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default_state()
    if not isinstance(payload, Mapping):
        return default_state()
    return _with_defaults(payload)


def save_state(state: Mapping[str, Any], path: str | os.PathLike[str] | None = None) -> None:
    resolved = _resolve_state_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp_path = resolved.with_name(f".{resolved.name}.{os.getpid()}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(dict(state), handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    temp_path.replace(resolved)


def hash_plan(plan_payload: Any) -> str:
    import hashlib

    encoded = json.dumps(plan_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def make_run_id(plan_hash: str, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    suffix = plan_hash.removeprefix("sha256:")[:8]
    return f"{current.strftime('%Y%m%dT%H%M%SZ')}-{suffix}"


def is_duplicate_plan(state: Mapping[str, Any], plan_hash: str) -> bool:
    runs = state.get("runs", [])
    if not isinstance(runs, list) or not runs:
        return False
    latest = runs[-1]
    if not isinstance(latest, Mapping):
        return False
    if latest.get("plan_hash") != plan_hash:
        return False
    return latest.get("status") in {"success", "running", "in_progress", "skipped_duplicate"}


def clone_default_state() -> dict[str, Any]:
    return deepcopy(default_state())
