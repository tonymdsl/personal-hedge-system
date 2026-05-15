# Paper Autopilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a permanent paper-only autonomous runner that chains research, scoring, LLM analysis, portfolio construction, risk checks, paper execution, and durable state/logging.

**Architecture:** Add a thin orchestration layer around the existing scripts (`run_data.py`, `run_scoring.py`, `run_analysis.py`, `run_portfolio.py`, `run_risk_check.py`, `run_execution.py`) without moving live trading into the flow. The runner persists idempotent state in `cache/autopilot_state.json`, exposes a CLI entrypoint, and publishes status for the dashboard to read.

**Tech Stack:** Python, existing project script modules, Alpaca paper account integration, JSON state files, pytest, existing dashboard stack.

---

## File Map

- Create: `autopilot/__init__.py` - package marker for the paper autopilot layer.
- Create: `autopilot/state.py` - load/save `cache/autopilot_state.json`, compute plan hash, manage run IDs, and append run history.
- Create: `autopilot/runner.py` - paper-only orchestration of the existing pipeline scripts.
- Create: `run_paper_autopilot.py` - CLI wrapper for one-shot and looped paper autopilot runs.
- Modify: dashboard status module or page that already reads Alpaca paper account data - add autopilot state/status display only.
- Create: `tests/test_autopilot_state.py` - state schema, idempotency, and corruption handling tests.
- Create: `tests/test_paper_autopilot_runner.py` - pipeline ordering, paper-only guardrails, and run result tests.
- Create/modify: dashboard tests near the existing dashboard test location - status rendering from `cache/autopilot_state.json`.
- Do not modify live trading entrypoints except to prove they are not imported or invoked by the autopilot runner.

## State File Contract

Path: `cache/autopilot_state.json`

```json
{
  "version": 1,
  "mode": "paper",
  "enabled": true,
  "last_run_id": "20260508T143000Z-4f9d1a2b",
  "last_plan_hash": "sha256:...",
  "last_started_at": "2026-05-08T14:30:00Z",
  "last_finished_at": "2026-05-08T14:34:12Z",
  "last_status": "success",
  "last_error": null,
  "current_step": null,
  "runs": [
    {
      "run_id": "20260508T143000Z-4f9d1a2b",
      "plan_hash": "sha256:...",
      "started_at": "2026-05-08T14:30:00Z",
      "finished_at": "2026-05-08T14:34:12Z",
      "status": "success",
      "steps": [
        {"name": "research", "status": "success", "started_at": "2026-05-08T14:30:00Z", "finished_at": "2026-05-08T14:31:00Z"},
        {"name": "scoring", "status": "success", "started_at": "2026-05-08T14:31:00Z", "finished_at": "2026-05-08T14:31:30Z"},
        {"name": "analysis", "status": "success", "started_at": "2026-05-08T14:31:30Z", "finished_at": "2026-05-08T14:32:30Z"},
        {"name": "portfolio", "status": "success", "started_at": "2026-05-08T14:32:30Z", "finished_at": "2026-05-08T14:33:00Z"},
        {"name": "risk", "status": "success", "started_at": "2026-05-08T14:33:00Z", "finished_at": "2026-05-08T14:33:30Z"},
        {"name": "paper_execution", "status": "success", "started_at": "2026-05-08T14:33:30Z", "finished_at": "2026-05-08T14:34:12Z"}
      ],
      "error": null
    }
  ]
}
```

Idempotency:
- `plan_hash` is a SHA-256 hash of the actionable portfolio/risk-approved paper order plan.
- `run_id` is generated once per accepted plan as `<UTC timestamp>-<first 8 chars of plan_hash>`.
- If the latest completed or in-progress run has the same `plan_hash`, the runner must not submit duplicate paper orders unless `--force` is passed.
- Failed runs keep their `run_id` and error details; a retry with the same plan may reuse the plan hash but must create a new `run_id`.

## Tasks

### Task 1: Core State Module

**Files:**
- Create: `autopilot/__init__.py`
- Create: `autopilot/state.py`
- Test: `tests/test_autopilot_state.py`

- [ ] Add tests for default state creation, atomic save/load, state schema fields, plan hash stability, duplicate-plan detection, and invalid JSON recovery behavior.
- [ ] Implement `load_state(path=Path("cache/autopilot_state.json"))`, `save_state(state, path=...)`, `hash_plan(plan_payload)`, `make_run_id(plan_hash, now)`, and `is_duplicate_plan(state, plan_hash)`.
- [ ] Ensure `save_state` creates `cache/` if missing and writes via a temporary file plus replace.
- [ ] Run `pytest tests/test_autopilot_state.py -v`; expected result: all tests pass.

### Task 2: Paper-Only Autopilot Runner

**Files:**
- Create: `autopilot/runner.py`
- Test: `tests/test_paper_autopilot_runner.py`

- [ ] Add tests that stub each existing layer and assert strict order: research -> scoring -> analysis -> portfolio -> risk -> paper execution.
- [ ] Add tests that assert the runner passes an explicit paper mode/account flag into execution and rejects any live mode, live account, or live endpoint configuration.
- [ ] Add tests for idempotency: same approved plan hash skips execution; `force=True` permits a new paper execution run.
- [ ] Implement `PaperAutopilotRunner` with injected step callables so tests do not call Alpaca or LLM services.
- [ ] Map steps to existing scripts without changing their ownership boundaries:
  - `run_data.py` for research/data refresh.
  - `run_scoring.py` for scoring.
  - `run_analysis.py` for LLM analysis.
  - `run_portfolio.py` for proposed portfolio/order plan.
  - `run_risk_check.py` for risk approval.
  - `run_execution.py` for paper execution only.
- [ ] Persist state before run start, after each step, and after final success/failure.
- [ ] Run `pytest tests/test_paper_autopilot_runner.py -v`; expected result: all tests pass.

### Task 3: CLI Entrypoint

**Files:**
- Create: `run_paper_autopilot.py`
- Test: extend `tests/test_paper_autopilot_runner.py` or create `tests/test_run_paper_autopilot_cli.py`

- [ ] Add tests for `--once`, `--loop`, `--interval-seconds`, `--force`, and explicit refusal of live mode arguments.
- [ ] Implement CLI defaults as paper-only: `python run_paper_autopilot.py --once`.
- [ ] For loop mode, sleep between completed runs and preserve idempotency on unchanged plans.
- [ ] Print concise status lines containing `run_id`, `plan_hash`, `status`, and current step.
- [ ] Run the CLI tests and one dry/stubbed one-shot command; expected result: no live trading path is reachable.

### Task 4: Dashboard Status Integration

**Files:**
- Modify: existing dashboard status component/page that currently reads Alpaca paper account, positions, and orders.
- Test: dashboard test file nearest that component/page.

- [ ] Locate the existing dashboard paper account status reader and add a read-only autopilot status panel backed by `cache/autopilot_state.json`.
- [ ] Display `enabled`, `last_status`, `last_run_id`, `last_finished_at`, `current_step`, and `last_error`.
- [ ] If the state file is missing, render an inactive/never-run status rather than failing the dashboard.
- [ ] Add tests for missing state, successful last run, in-progress run, and failed run.
- [ ] Run the dashboard test command used by the project; expected result: existing dashboard behavior plus autopilot status tests pass.

### Task 5: Final Validation

**Files:**
- No additional source files unless tests expose a defect in files from Tasks 1-4.

- [ ] Run targeted Python tests:
  - `pytest tests/test_autopilot_state.py tests/test_paper_autopilot_runner.py -v`
  - dashboard-specific test command identified in Task 4.
- [ ] Run a stubbed or dry-run paper autopilot invocation and confirm `cache/autopilot_state.json` is written with `mode: "paper"` and a non-empty run history.
- [ ] Inspect imports/configuration to confirm `run_paper_autopilot.py` and `autopilot/runner.py` do not call live trading configuration.
- [ ] Confirm dashboard still reads Alpaca paper account/positions/orders and only adds autopilot status.
- [ ] Leave live trading outside this flow; no live execution command should be introduced, linked, or documented as part of Paper Autopilot.
