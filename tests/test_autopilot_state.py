from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from autopilot.state import (
    DEFAULT_STATE_PATH,
    hash_plan,
    is_duplicate_plan,
    load_state,
    make_run_id,
    save_state,
)


def test_load_state_returns_default_for_missing_file(tmp_path: Path) -> None:
    state = load_state(tmp_path / "missing" / "autopilot_state.json")

    assert DEFAULT_STATE_PATH == Path("cache/autopilot_state.json")
    assert state == {
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


def test_save_state_creates_parent_and_loads_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "autopilot_state.json"
    state = load_state(path)
    state["last_status"] = "success"
    state["runs"].append({"run_id": "run-1", "status": "success"})

    save_state(state, path)

    assert path.exists()
    assert list(path.parent.glob("*.tmp")) == []
    assert load_state(path)["runs"] == [{"run_id": "run-1", "status": "success"}]


def test_load_state_recovers_from_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")

    state = load_state(path)

    assert state["last_status"] == "never_run"
    assert state["runs"] == []


def test_hash_plan_is_stable_with_sorted_json_and_default_str() -> None:
    now = datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc)

    first = hash_plan({"b": now, "a": [2, 1]})
    second = hash_plan({"a": [2, 1], "b": now})

    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_make_run_id_uses_utc_timestamp_and_first_hash_chars() -> None:
    now = datetime(2026, 5, 8, 14, 30, 1, tzinfo=timezone.utc)

    assert make_run_id("sha256:abcdef123456", now=now) == "20260508T143001Z-abcdef12"


def test_duplicate_plan_only_when_latest_success_running_or_skipped_duplicate() -> None:
    plan_hash = "sha256:abcdef123456"

    assert is_duplicate_plan({"runs": []}, plan_hash) is False
    assert is_duplicate_plan({"runs": [{"plan_hash": plan_hash, "status": "failed"}]}, plan_hash) is False
    assert is_duplicate_plan({"runs": [{"plan_hash": plan_hash, "status": "success"}]}, plan_hash) is True
    assert is_duplicate_plan({"runs": [{"plan_hash": plan_hash, "status": "running"}]}, plan_hash) is True
    assert is_duplicate_plan({"runs": [{"plan_hash": plan_hash, "status": "in_progress"}]}, plan_hash) is True
    assert is_duplicate_plan({"runs": [{"plan_hash": plan_hash, "status": "skipped_duplicate"}]}, plan_hash) is True


def test_duplicate_plan_checks_latest_run_only() -> None:
    plan_hash = "sha256:abcdef123456"
    state = {
        "runs": [
            {"plan_hash": plan_hash, "status": "success"},
            {"plan_hash": plan_hash, "status": "failed"},
        ]
    }

    assert is_duplicate_plan(state, plan_hash) is False
