from __future__ import annotations

import csv
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from common.config import PROJECT_ROOT

from .state import hash_plan, is_duplicate_plan, load_state, make_run_id, save_state

CommandResult = Mapping[str, Any] | subprocess.CompletedProcess[str] | Any
CommandRunner = Callable[[list[str]], CommandResult]
PAPER_ALPACA_BASE_URL = "https://paper-api.alpaca.markets"


class PaperAutopilotError(RuntimeError):
    """Raised when the paper autopilot cannot safely complete a run."""


class PaperAutopilotRunner:
    def __init__(
        self,
        config: Mapping[str, Any],
        state_path: str | Path | None = None,
        command_runner: CommandRunner | None = None,
        now: Callable[[], datetime] | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self.config = dict(config)
        self.state_path = state_path
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.project_root = Path(project_root or PROJECT_ROOT).resolve(strict=False)
        self.command_runner = command_runner or self._run_subprocess
        self._guard_paper_only()

    def _guard_paper_only(self) -> None:
        execution = self.config.get("execution", {})
        if not isinstance(execution, Mapping):
            raise PaperAutopilotError("Missing execution config.")
        if execution.get("mode") != "paper":
            raise PaperAutopilotError("Paper autopilot requires execution.mode == paper.")
        if execution.get("broker") != "alpaca":
            raise PaperAutopilotError("Paper autopilot requires execution.broker == alpaca.")
        if bool(execution.get("allow_live_trading", False)):
            raise PaperAutopilotError("Paper autopilot refuses allow_live_trading=True.")

    def _run_subprocess(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                cwd=self.project_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=self._step_timeout_seconds(),
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                command,
                124,
                stdout=str(exc.stdout or ""),
                stderr=f"Timed out after {self._step_timeout_seconds()} seconds",
            )

    def _step_timeout_seconds(self) -> float:
        autopilot = self.config.get("autopilot", {})
        if not isinstance(autopilot, Mapping):
            return 1800.0
        try:
            return max(1.0, float(autopilot.get("step_timeout_seconds", 1800.0)))
        except (TypeError, ValueError):
            return 1800.0

    def _timestamp(self) -> str:
        current = self.now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _base_command(self, script: str) -> list[str]:
        command = ["python", script, "--no-dry-run"]
        config_path = self.config.get("_config_path")
        if config_path:
            command.extend(["--config", str(config_path)])
        return command

    @staticmethod
    def _first_env(*names: str) -> str | None:
        for name in names:
            value = os.getenv(name)
            if value:
                return value
        return None

    @staticmethod
    def _normalize_alpaca_base_url(base_url: str | None) -> str:
        normalized = (base_url or PAPER_ALPACA_BASE_URL).strip().rstrip("/")
        if normalized.lower().endswith("/v2"):
            normalized = normalized[:-3].rstrip("/")
        return normalized or PAPER_ALPACA_BASE_URL

    def _alpaca_paper_base_url(self) -> str:
        execution = self.config.get("execution", {})
        alpaca = execution.get("alpaca", {}) if isinstance(execution, Mapping) else {}
        if isinstance(alpaca, Mapping):
            return self._normalize_alpaca_base_url(str(alpaca.get("paper_base_url", PAPER_ALPACA_BASE_URL)))
        return PAPER_ALPACA_BASE_URL

    def _alpaca_paper_url(self, endpoint: str) -> str:
        return f"{self._alpaca_paper_base_url()}/v2/{endpoint.lstrip('/')}"

    def _alpaca_paper_open_orders_count(self, *, timeout: float = 20.0) -> int:
        api_key = self._first_env("ALPACA_API_KEY", "APCA_API_KEY_ID")
        secret_key = self._first_env("ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY")
        if not api_key or not secret_key:
            raise PaperAutopilotError("Alpaca paper credentials unavailable; cannot check open orders.")

        session = requests.Session()
        headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}
        try:
            response = session.get(self._alpaca_paper_url("orders?status=open&limit=500"), headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise PaperAutopilotError(f"Unable to read Alpaca paper open orders: {type(exc).__name__}") from exc

        if not isinstance(payload, list):
            raise PaperAutopilotError("Alpaca paper open orders response was invalid.")
        return sum(1 for order in payload if isinstance(order, Mapping))

    def _alpaca_paper_clock(self, *, timeout: float = 20.0) -> Mapping[str, Any]:
        api_key = self._first_env("ALPACA_API_KEY", "APCA_API_KEY_ID")
        secret_key = self._first_env("ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY")
        if not api_key or not secret_key:
            raise PaperAutopilotError("Alpaca paper credentials unavailable; cannot check market clock.")

        session = requests.Session()
        headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}
        try:
            response = session.get(self._alpaca_paper_url("clock"), headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise PaperAutopilotError(f"Unable to read Alpaca paper market clock: {type(exc).__name__}") from exc

        if not isinstance(payload, Mapping):
            raise PaperAutopilotError("Alpaca paper market clock response was invalid.")
        return payload

    def _commands(self) -> list[tuple[str, list[str]]]:
        autopilot = self.config.get("autopilot", {})
        if not isinstance(autopilot, Mapping):
            autopilot = {}
        nav = autopilot.get("nav", 10000)
        optimize_method = autopilot.get("optimize_method")
        candidate_review_gate = str(autopilot.get("candidate_review_gate", "exclude_rejected")).strip() or "exclude_rejected"
        portfolio_command = self._base_command("run_portfolio.py") + [
            "--whatif",
            "--current-source",
            "alpaca-paper",
            "--candidate-review-gate",
            candidate_review_gate,
            "--rebalance",
            "--nav",
            str(nav),
        ]
        if optimize_method is not None and str(optimize_method).strip():
            portfolio_command.extend(["--optimize-method", str(optimize_method)])
        commands = [
            ("research", self._base_command("run_data.py")),
            ("scoring", self._base_command("run_scoring.py")),
            ("analysis", self._base_command("run_analysis.py")),
            ("portfolio", portfolio_command),
            ("risk", self._base_command("run_risk_check.py")),
            ("paper_execution", self._base_command("run_execution.py") + ["--execute"]),
        ]
        data_limit = autopilot.get("data_limit")
        if data_limit:
            commands[0][1].extend(["--limit", str(data_limit)])
        analysis_limit = autopilot.get("analysis_limit")
        if analysis_limit:
            commands[2][1].extend(["--limit", str(analysis_limit)])
        analysis = self.config.get("analysis", {})
        analysis_requires_ai = isinstance(analysis, Mapping) and bool(analysis.get("require_ai", False))
        if autopilot.get("use_ai") or analysis_requires_ai:
            commands[2][1].append("--use-ai")
        return commands

    def _min_portfolio_candidates(self) -> int:
        autopilot = self.config.get("autopilot", {})
        if not isinstance(autopilot, Mapping):
            return 30
        return int(autopilot.get("min_portfolio_candidates", 30))

    def _candidate_count(self, relative_path: str) -> int:
        path = self.project_root / relative_path
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8", newline="") as handle:
            return sum(1 for _row in csv.DictReader(handle))

    def _select_portfolio_input(self) -> tuple[str | None, str | None]:
        min_candidates = self._min_portfolio_candidates()
        analysis_path = "output/analysis_results_latest.csv"
        scored_path = "output/scored_universe_latest.csv"
        analysis_count = self._candidate_count(analysis_path)
        if analysis_count >= min_candidates:
            return analysis_path, None
        scored_count = self._candidate_count(scored_path)
        if scored_count >= min_candidates:
            return scored_path, None
        detail = (
            f"Insufficient portfolio candidates: {analysis_path} has {analysis_count} candidates, "
            f"{scored_path} has {scored_count} candidates, minimum required is {min_candidates}."
        )
        return None, detail

    @staticmethod
    def _returncode(result: CommandResult) -> int:
        if isinstance(result, Mapping):
            return int(result.get("returncode", 0))
        return int(getattr(result, "returncode", 0))

    @staticmethod
    def _result_payload(result: CommandResult) -> dict[str, Any]:
        if isinstance(result, Mapping):
            return dict(result)
        payload = {
            "returncode": getattr(result, "returncode", 0),
            "stdout": getattr(result, "stdout", ""),
            "stderr": getattr(result, "stderr", ""),
        }
        return payload

    @staticmethod
    def _error_text(result: CommandResult) -> str:
        payload = PaperAutopilotRunner._result_payload(result)
        return str(payload.get("stderr") or payload.get("stdout") or f"returncode={payload.get('returncode')}")

    def _plan_payload(self, step_outputs: Mapping[str, Any]) -> dict[str, Any]:
        orders_path = self.project_root / "output" / "rebalance_orders_latest.csv"
        if orders_path.exists():
            return {"orders_csv": orders_path.read_text(encoding="utf-8")}
        return {"steps": dict(step_outputs)}

    def run_once(self, *, force: bool = False) -> dict[str, Any]:
        state = load_state(self.state_path)
        if bool(state.get("paused", False)):
            state.update({"current_step": None, "last_status": "operator_paused", "last_error": None})
            save_state(state, self.state_path)
            return {
                "status": "operator_paused",
                "run_id": state.get("last_run_id"),
                "plan_hash": state.get("last_plan_hash"),
                "current_step": None,
            }
        if not bool(state.get("enabled", True)):
            state.update({"current_step": None, "last_status": "operator_disabled", "last_error": None})
            save_state(state, self.state_path)
            return {
                "status": "operator_disabled",
                "run_id": state.get("last_run_id"),
                "plan_hash": state.get("last_plan_hash"),
                "current_step": None,
            }
        started_at = self._timestamp()
        state.update(
            {
                "mode": "paper",
                "enabled": True,
                "last_started_at": started_at,
                "last_finished_at": None,
                "last_status": "running",
                "last_error": None,
                "last_open_orders_count": None,
                "current_step": "open_order_check",
            }
        )
        save_state(state, self.state_path)

        run: dict[str, Any] = {
            "run_id": None,
            "plan_hash": None,
            "started_at": started_at,
            "finished_at": None,
            "status": "running",
            "steps": [],
            "error": None,
        }
        step_outputs: dict[str, Any] = {}

        try:
            open_orders_count = self._alpaca_paper_open_orders_count()
            if open_orders_count > 0:
                finished_at = self._timestamp()
                detail = (
                    f"{open_orders_count} open Alpaca paper orders pending; skipping paper autopilot cycle before "
                    "research, rebalance, and execution."
                )
                run.update(
                    {
                        "status": "open_orders_pending",
                        "finished_at": finished_at,
                        "error": detail,
                        "open_orders_count": open_orders_count,
                    }
                )
                state["runs"].append(run)
                state.update(
                    {
                        "last_status": "open_orders_pending",
                        "last_finished_at": finished_at,
                        "current_step": None,
                        "last_error": detail,
                        "last_run_id": run.get("run_id"),
                        "last_plan_hash": run.get("plan_hash"),
                        "last_open_orders_count": open_orders_count,
                    }
                )
                save_state(state, self.state_path)
                return {
                    "status": "open_orders_pending",
                    "run_id": run["run_id"],
                    "plan_hash": run["plan_hash"],
                    "current_step": None,
                    "open_orders_count": open_orders_count,
                    "message": detail,
                }

            state["current_step"] = "market_clock_check"
            save_state(state, self.state_path)
            market_clock = self._alpaca_paper_clock()
            if not bool(market_clock.get("is_open", False)):
                next_open = market_clock.get("next_open")
                finished_at = self._timestamp()
                detail = "Alpaca paper market is closed; skipping paper autopilot cycle before research, rebalance, and execution."
                if next_open:
                    detail = f"{detail} Next open: {next_open}."
                run.update(
                    {
                        "status": "market_closed",
                        "finished_at": finished_at,
                        "error": detail,
                        "market_clock": dict(market_clock),
                    }
                )
                state["runs"].append(run)
                state.update(
                    {
                        "last_status": "market_closed",
                        "last_finished_at": finished_at,
                        "current_step": None,
                        "last_error": detail,
                        "last_run_id": run.get("run_id"),
                        "last_plan_hash": run.get("plan_hash"),
                    }
                )
                save_state(state, self.state_path)
                return {
                    "status": "market_closed",
                    "run_id": run["run_id"],
                    "plan_hash": run["plan_hash"],
                    "current_step": None,
                    "market_clock": dict(market_clock),
                    "message": detail,
                }

            for name, command in self._commands():
                if name == "portfolio":
                    portfolio_input, detail = self._select_portfolio_input()
                    if portfolio_input is None:
                        finished_at = self._timestamp()
                        run.update({"status": "insufficient_candidates", "finished_at": finished_at, "error": detail})
                        if run not in state["runs"]:
                            state["runs"].append(run)
                        state.update(
                            {
                                "last_status": "insufficient_candidates",
                                "last_finished_at": finished_at,
                                "current_step": None,
                                "last_error": detail,
                                "last_run_id": run.get("run_id"),
                                "last_plan_hash": run.get("plan_hash"),
                            }
                        )
                        save_state(state, self.state_path)
                        return {
                            "status": "insufficient_candidates",
                            "run_id": run["run_id"],
                            "plan_hash": run["plan_hash"],
                            "current_step": None,
                            "error": detail,
                        }
                    command = command[:3] + ["--input", portfolio_input] + command[3:]

                if name == "paper_execution":
                    plan_payload = self._plan_payload(step_outputs)
                    plan_hash = hash_plan(plan_payload)
                    run_id = make_run_id(plan_hash, now=self.now())
                    run["run_id"] = run_id
                    run["plan_hash"] = plan_hash
                    state["last_run_id"] = run_id
                    state["last_plan_hash"] = plan_hash
                    if is_duplicate_plan(state, plan_hash) and not force:
                        finished_at = self._timestamp()
                        run.update({"status": "skipped_duplicate", "finished_at": finished_at})
                        state["runs"].append(run)
                        state.update(
                            {
                                "last_status": "skipped_duplicate",
                                "last_finished_at": finished_at,
                                "current_step": None,
                                "last_error": None,
                            }
                        )
                        save_state(state, self.state_path)
                        return {
                            "status": "skipped_duplicate",
                            "run_id": run_id,
                            "plan_hash": plan_hash,
                            "current_step": None,
                        }
                    state["runs"].append(run)
                    save_state(state, self.state_path)

                step_started = self._timestamp()
                state["current_step"] = name
                save_state(state, self.state_path)
                result = self.command_runner(command)
                step_finished = self._timestamp()
                step = {
                    "name": name,
                    "status": "success" if self._returncode(result) == 0 else "failed",
                    "started_at": step_started,
                    "finished_at": step_finished,
                }
                run["steps"].append(step)
                step_outputs[name] = {"command": command, "result": self._result_payload(result)}
                save_state(state, self.state_path)
                if self._returncode(result) != 0:
                    raise PaperAutopilotError(f"{name} failed: {self._error_text(result)}")

            if run not in state["runs"]:
                plan_payload = self._plan_payload(step_outputs)
                plan_hash = hash_plan(plan_payload)
                run["plan_hash"] = plan_hash
                run["run_id"] = make_run_id(plan_hash, now=self.now())
                state["runs"].append(run)
                state["last_plan_hash"] = plan_hash
                state["last_run_id"] = run["run_id"]

            finished_at = self._timestamp()
            run.update({"status": "success", "finished_at": finished_at})
            state.update(
                {
                    "last_status": "success",
                    "last_finished_at": finished_at,
                    "current_step": None,
                    "last_error": None,
                }
            )
            save_state(state, self.state_path)
            return {
                "status": "success",
                "run_id": run["run_id"],
                "plan_hash": run["plan_hash"],
                "current_step": None,
            }
        except Exception as exc:
            finished_at = self._timestamp()
            run.update({"status": "failed", "finished_at": finished_at, "error": str(exc)})
            if run not in state["runs"]:
                state["runs"].append(run)
            state.update(
                {
                    "last_status": "failed",
                    "last_finished_at": finished_at,
                    "current_step": None,
                    "last_error": str(exc),
                    "last_run_id": run.get("run_id"),
                    "last_plan_hash": run.get("plan_hash"),
                }
            )
            save_state(state, self.state_path)
            if isinstance(exc, PaperAutopilotError):
                raise
            raise PaperAutopilotError(str(exc)) from exc
