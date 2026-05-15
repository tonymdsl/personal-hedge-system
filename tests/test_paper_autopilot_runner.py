from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from autopilot.runner import PaperAutopilotError, PaperAutopilotRunner
from autopilot.state import hash_plan, load_state, save_state


def paper_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "execution": {
            "mode": "paper",
            "broker": "alpaca",
            "allow_live_trading": False,
        },
        "autopilot": {"nav": 12345},
    }
    config.update(overrides)
    return config


class Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current = self.current + timedelta(seconds=1)
        return value


def write_candidates(project_root: Path, filename: str, count: int) -> Path:
    path = project_root / "output" / filename
    path.parent.mkdir(exist_ok=True)
    rows = ["ticker,score"] + [f"TICKER{i},{i}" for i in range(count)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def no_open_orders_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PaperAutopilotRunner, "_alpaca_paper_open_orders_count", lambda self: 0, raising=False)
    monkeypatch.setattr(
        PaperAutopilotRunner,
        "_alpaca_paper_clock",
        lambda self: {"is_open": True, "next_open": "2026-05-08T09:30:00-04:00"},
        raising=False,
    )


def test_runner_skips_before_research_when_alpaca_paper_has_open_orders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    checks: list[dict[str, object]] = []

    def open_orders_count(runner: PaperAutopilotRunner) -> int:
        checks.append(dict(runner.config))
        return 3

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0}

    monkeypatch.setattr(PaperAutopilotRunner, "_alpaca_paper_open_orders_count", open_orders_count, raising=False)
    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    result = runner.run_once()

    assert result["status"] == "open_orders_pending"
    assert result["open_orders_count"] == 3
    assert result["current_step"] is None
    assert checks == [paper_config()]
    assert commands == []
    state = load_state(tmp_path / "state.json")
    assert state["last_status"] == "open_orders_pending"
    assert state["current_step"] is None
    assert state["last_run_id"] is None
    assert state["last_plan_hash"] is None
    assert "3 open Alpaca paper orders pending" in state["last_error"]
    assert "ALPACA" not in state["last_error"]
    assert state["runs"][-1]["status"] == "open_orders_pending"
    assert state["runs"][-1]["steps"] == []
    assert state["runs"][-1]["plan_hash"] is None


def test_runner_respects_operator_disabled_state_before_any_broker_or_pipeline_call(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = load_state(state_path)
    state.update({"enabled": False, "paused": False, "last_status": "operator_disabled"})
    save_state(state, state_path)
    commands: list[list[str]] = []

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=state_path,
        command_runner=lambda command: commands.append(command) or {"returncode": 0},
        now=Clock(),
        project_root=tmp_path,
    )

    result = runner.run_once()

    assert result["status"] == "operator_disabled"
    assert result["current_step"] is None
    assert commands == []
    state = load_state(state_path)
    assert state["last_status"] == "operator_disabled"
    assert state["current_step"] is None
    assert state["runs"] == []


def test_runner_respects_operator_paused_state_before_any_broker_or_pipeline_call(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = load_state(state_path)
    state.update({"enabled": False, "paused": True, "last_status": "operator_paused"})
    save_state(state, state_path)
    commands: list[list[str]] = []

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=state_path,
        command_runner=lambda command: commands.append(command) or {"returncode": 0},
        now=Clock(),
        project_root=tmp_path,
    )

    result = runner.run_once()

    assert result["status"] == "operator_paused"
    assert result["current_step"] is None
    assert commands == []
    state = load_state(state_path)
    assert state["last_status"] == "operator_paused"
    assert state["runs"] == []


def test_runner_skips_before_research_when_alpaca_paper_market_is_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        PaperAutopilotRunner,
        "_alpaca_paper_clock",
        lambda self: {"is_open": False, "next_open": "2026-05-15T09:30:00-04:00"},
        raising=False,
    )
    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=tmp_path / "state.json",
        command_runner=lambda command: commands.append(command) or {"returncode": 0},
        now=Clock(),
        project_root=tmp_path,
    )

    result = runner.run_once()

    assert result["status"] == "market_closed"
    assert result["current_step"] is None
    assert result["market_clock"]["next_open"] == "2026-05-15T09:30:00-04:00"
    assert commands == []
    state = load_state(tmp_path / "state.json")
    assert state["last_status"] == "market_closed"
    assert state["current_step"] is None
    assert state["last_run_id"] is None
    assert state["last_plan_hash"] is None
    assert "market is closed" in state["last_error"]
    assert state["runs"][-1]["status"] == "market_closed"
    assert state["runs"][-1]["steps"] == []


def test_open_order_check_uses_configured_paper_base_url_not_generic_live_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets/v2")
    runner = PaperAutopilotRunner(
        paper_config(
            execution={
                "mode": "paper",
                "broker": "alpaca",
                "allow_live_trading": False,
                "alpaca": {"paper_base_url": "https://paper.example/v2"},
            }
        ),
        state_path=tmp_path / "state.json",
        command_runner=lambda command: {"returncode": 0},
        now=Clock(),
        project_root=tmp_path,
    )

    assert (
        runner._alpaca_paper_url("orders?status=open&limit=500")
        == "https://paper.example/v2/orders?status=open&limit=500"
    )


def test_runner_executes_default_steps_in_strict_order_with_required_flags(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 30)
    commands: list[list[str]] = []

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0, "stdout": command[0], "stderr": ""}

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    result = runner.run_once()

    assert result["status"] == "success"
    assert commands == [
        ["python", "run_data.py", "--no-dry-run"],
        ["python", "run_scoring.py", "--no-dry-run"],
        ["python", "run_analysis.py", "--no-dry-run"],
        [
            "python",
            "run_portfolio.py",
            "--no-dry-run",
            "--input",
            "output/analysis_results_latest.csv",
            "--whatif",
            "--current-source",
            "alpaca-paper",
            "--candidate-review-gate",
            "exclude_rejected",
            "--rebalance",
            "--nav",
            "12345",
        ],
        ["python", "run_risk_check.py", "--no-dry-run"],
        ["python", "run_execution.py", "--no-dry-run", "--execute"],
    ]
    assert all("YES I UNDERSTAND THE RISKS" not in command for command in commands)
    portfolio_command = next(command for command in commands if command[1] == "run_portfolio.py")
    assert portfolio_command[portfolio_command.index("--candidate-review-gate") + 1] == "exclude_rejected"
    state = load_state(tmp_path / "state.json")
    assert state["last_status"] == "success"
    assert state["current_step"] is None
    assert [step["name"] for step in state["runs"][-1]["steps"]] == [
        "research",
        "scoring",
        "analysis",
        "portfolio",
        "risk",
        "paper_execution",
    ]


def test_runner_omits_portfolio_optimizer_when_autopilot_method_is_not_configured(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 30)
    commands: list[list[str]] = []

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0}

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    runner.run_once()

    portfolio_command = next(command for command in commands if command[1] == "run_portfolio.py")
    assert "--optimize-method" not in portfolio_command


def test_runner_adds_use_ai_when_analysis_requires_ai(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 30)
    commands: list[list[str]] = []

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0}

    runner = PaperAutopilotRunner(
        paper_config(analysis={"require_ai": True}),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    runner.run_once()

    analysis_command = next(command for command in commands if command[1] == "run_analysis.py")
    assert "--use-ai" in analysis_command


def test_runner_portfolio_command_uses_whatif_and_alpaca_current_source(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 30)
    commands: list[list[str]] = []

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0}

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    runner.run_once()

    portfolio_command = next(command for command in commands if command[1] == "run_portfolio.py")
    assert "--whatif" in portfolio_command
    assert portfolio_command[portfolio_command.index("--current-source") + 1] == "alpaca-paper"


def test_runner_uses_configured_autopilot_optimizer(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 30)
    commands: list[list[str]] = []

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0}

    runner = PaperAutopilotRunner(
        paper_config(autopilot={"nav": 12345, "optimize_method": "mvo"}),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    runner.run_once()

    portfolio_command = next(command for command in commands if command[1] == "run_portfolio.py")
    assert "--optimize-method" in portfolio_command
    assert portfolio_command[portfolio_command.index("--optimize-method") + 1] == "mvo"


def test_runner_allows_configured_autopilot_candidate_review_gate(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 30)
    commands: list[list[str]] = []

    runner = PaperAutopilotRunner(
        paper_config(autopilot={"nav": 12345, "candidate_review_gate": "approved_only"}),
        state_path=tmp_path / "state.json",
        command_runner=lambda command: commands.append(command) or {"returncode": 0},
        now=Clock(),
        project_root=tmp_path,
    )

    runner.run_once()

    portfolio_command = next(command for command in commands if command[1] == "run_portfolio.py")
    assert portfolio_command[portfolio_command.index("--candidate-review-gate") + 1] == "approved_only"


def test_default_subprocess_runner_uses_configured_step_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    completed = Mock(returncode=0, stdout="", stderr="")
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls.append({"command": command, **kwargs})
        return completed

    monkeypatch.setattr("autopilot.runner.subprocess.run", fake_run)
    runner = PaperAutopilotRunner(
        paper_config(autopilot={"step_timeout_seconds": 42}),
        state_path=tmp_path / "state.json",
        project_root=tmp_path,
    )

    assert runner._run_subprocess(["python", "run_data.py"]) is completed
    assert calls[0]["timeout"] == 42.0


@pytest.mark.parametrize(
    "config",
    [
        {"execution": {"mode": "live", "broker": "alpaca", "allow_live_trading": False}},
        {"execution": {"mode": "paper", "broker": "other", "allow_live_trading": False}},
        {"execution": {"mode": "paper", "broker": "alpaca", "allow_live_trading": True}},
    ],
)
def test_runner_rejects_non_paper_or_live_enabled_config(config: dict[str, object], tmp_path: Path) -> None:
    with pytest.raises(PaperAutopilotError):
        PaperAutopilotRunner(config, state_path=tmp_path / "state.json")


def test_duplicate_plan_skips_execution_unless_forced(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 30)
    orders = tmp_path / "output" / "rebalance_orders_latest.csv"
    orders.parent.mkdir(exist_ok=True)
    orders.write_text("ticker,side,quantity\nAAPL,buy,1\n", encoding="utf-8")
    plan_hash = hash_plan({"orders_csv": orders.read_text(encoding="utf-8")})
    state_path = tmp_path / "state.json"
    state = load_state(state_path)
    state["runs"].append({"run_id": "prior", "plan_hash": plan_hash, "status": "success", "steps": []})
    save_state(state, state_path)

    commands: list[list[str]] = []

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0}

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=state_path,
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    result = runner.run_once()

    assert result["status"] == "skipped_duplicate"
    assert [command[1] for command in commands] == [
        "run_data.py",
        "run_scoring.py",
        "run_analysis.py",
        "run_portfolio.py",
        "run_risk_check.py",
    ]
    assert "run_execution.py" not in [command[1] for command in commands]

    commands.clear()
    forced = runner.run_once(force=True)

    assert forced["status"] == "success"
    assert "run_execution.py" in [command[1] for command in commands]
    assert forced["run_id"] != "prior"


def test_runner_records_failure_and_raises(tmp_path: Path) -> None:
    def command_runner(command: list[str]) -> dict[str, object]:
        if command[1] == "run_scoring.py":
            return {"returncode": 2, "stderr": "bad scoring"}
        return {"returncode": 0}

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
    )

    with pytest.raises(PaperAutopilotError):
        runner.run_once()

    state = load_state(tmp_path / "state.json")
    assert state["last_status"] == "failed"
    assert state["last_error"]
    assert state["runs"][-1]["status"] == "failed"
    assert state["runs"][-1]["steps"][-1]["name"] == "scoring"


def test_portfolio_command_uses_analysis_when_analysis_has_enough_candidates(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 30)
    write_candidates(tmp_path, "scored_universe_latest.csv", 50)
    commands: list[list[str]] = []

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0}

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    assert runner.run_once()["status"] == "success"

    portfolio_command = next(command for command in commands if command[1] == "run_portfolio.py")
    assert portfolio_command[portfolio_command.index("--input") + 1] == "output/analysis_results_latest.csv"


def test_portfolio_command_uses_scored_universe_when_analysis_insufficient(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 4)
    write_candidates(tmp_path, "scored_universe_latest.csv", 30)
    commands: list[list[str]] = []

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0}

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    assert runner.run_once()["status"] == "success"

    portfolio_command = next(command for command in commands if command[1] == "run_portfolio.py")
    assert portfolio_command[portfolio_command.index("--input") + 1] == "output/scored_universe_latest.csv"


def test_runner_stops_when_both_candidate_files_are_insufficient(tmp_path: Path) -> None:
    write_candidates(tmp_path, "analysis_results_latest.csv", 4)
    write_candidates(tmp_path, "scored_universe_latest.csv", 18)
    stale_orders = tmp_path / "output" / "rebalance_orders_latest.csv"
    stale_orders.write_text("ticker,side,quantity\nSTALE,buy,1\n", encoding="utf-8")
    stale_hash = hash_plan({"orders_csv": stale_orders.read_text(encoding="utf-8")})
    commands: list[list[str]] = []

    def command_runner(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"returncode": 0}

    runner = PaperAutopilotRunner(
        paper_config(),
        state_path=tmp_path / "state.json",
        command_runner=command_runner,
        now=Clock(),
        project_root=tmp_path,
    )

    result = runner.run_once()

    assert result["status"] == "insufficient_candidates"
    assert [command[1] for command in commands] == ["run_data.py", "run_scoring.py", "run_analysis.py"]
    state = load_state(tmp_path / "state.json")
    assert state["last_status"] == "insufficient_candidates"
    assert "analysis_results_latest.csv has 4 candidates" in state["last_error"]
    assert "scored_universe_latest.csv has 18 candidates" in state["last_error"]
    assert state["last_plan_hash"] != stale_hash
    assert state["runs"][-1]["plan_hash"] is None


def test_cli_one_shot_uses_runner_without_real_commands(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import run_paper_autopilot

    calls: list[dict[str, object]] = []

    class StubRunner:
        def __init__(self, config: dict[str, object], **kwargs: object) -> None:
            calls.append({"config": config, "kwargs": kwargs})

        def run_once(self, *, force: bool = False) -> dict[str, object]:
            calls.append({"force": force})
            return {"status": "success", "run_id": "run-1", "plan_hash": "sha256:abc", "current_step": None}

    monkeypatch.setattr(run_paper_autopilot, "PaperAutopilotRunner", StubRunner)

    exit_code = run_paper_autopilot.main(["--once", "--force", "--nav", "777", "--data-limit", "5", "--analysis-limit", "3", "--use-ai"])

    assert exit_code == 0
    autopilot_config = calls[0]["config"]["autopilot"]
    assert autopilot_config["nav"] == 777.0
    assert autopilot_config["data_limit"] == 5
    assert autopilot_config["analysis_limit"] == 3
    assert autopilot_config["use_ai"] is True
    assert autopilot_config["candidate_review_gate"] == "exclude_rejected"
    assert autopilot_config["step_timeout_seconds"] == 1800
    assert calls[1] == {"force": True}
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"


def test_cli_defaults_to_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import run_paper_autopilot

    runs: list[bool] = []

    class StubRunner:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run_once(self, *, force: bool = False) -> dict[str, object]:
            runs.append(force)
            return {"status": "success"}

    monkeypatch.setattr(run_paper_autopilot, "PaperAutopilotRunner", StubRunner)

    assert run_paper_autopilot.main([]) == 0
    assert runs == [False]


def test_cli_loop_records_cycle_error_and_keeps_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import run_paper_autopilot

    class StopLoop(Exception):
        pass

    runs: list[bool] = []
    sleeps: list[float] = []

    class StubRunner:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run_once(self, *, force: bool = False) -> dict[str, object]:
            runs.append(force)
            if len(runs) == 1:
                raise PaperAutopilotError("portfolio failed")
            return {"status": "success", "run_id": "run-2", "plan_hash": "sha256:def", "current_step": None}

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise StopLoop()

    monkeypatch.setattr(run_paper_autopilot, "PaperAutopilotRunner", StubRunner)
    monkeypatch.setattr(run_paper_autopilot.time, "sleep", fake_sleep)

    with pytest.raises(StopLoop):
        run_paper_autopilot.main(["--loop", "--interval-seconds", "0"])

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["status"] for line in lines] == ["failed", "success"]
    assert lines[0]["error"] == "portfolio failed"
    assert runs == [False, False]
    assert sleeps == [0.0, 0.0]
