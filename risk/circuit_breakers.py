"""Loss-triggered circuit breakers and halt lock files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping


def _risk(config: Mapping[str, object] | None = None) -> Mapping[str, object]:
    value = (config or {}).get('risk', {}) if isinstance(config, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def _thresholds(config: Mapping[str, object] | None = None) -> Mapping[str, object]:
    risk = _risk(config)
    breakers = risk.get('circuit_breakers', {})
    return breakers if isinstance(breakers, Mapping) else {}


def create_halt(lock_file: str | Path, reason: str) -> Path:
    path = Path(lock_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'halted': True, 'reason': reason}, indent=2), encoding='utf-8')
    return path


def clear_halt(lock_file: str | Path) -> bool:
    path = Path(lock_file)
    if path.exists():
        path.unlink()
        return True
    return False


def evaluate_circuit_breakers(state: Mapping[str, float], *, config: Mapping[str, object] | None = None, lock_file: str | Path | None = None) -> dict[str, object]:
    cfg = _thresholds(config)
    risk = _risk(config)
    lock = Path(lock_file or cfg.get('lock_file', 'cache/trading_halt.lock'))
    daily = float(state.get('daily_pnl_pct', 0.0))
    weekly = float(state.get('weekly_pnl_pct', 0.0))
    drawdown = abs(float(state.get('drawdown_pct', 0.0)))
    single = abs(float(state.get('single_position_loss_pct', 0.0)))
    actions: list[str] = []
    halted = False
    if daily <= -float(cfg.get('daily_loss_soft', 0.015)) or weekly <= -float(cfg.get('weekly_loss_soft', 0.04)):
        actions.append('size_down_30')
    if daily <= -float(cfg.get('daily_loss_hard', 0.025)):
        actions.append('close_all_today')
        create_halt(lock, 'daily_loss_hard')
        halted = True
    if drawdown >= float(cfg.get('max_drawdown_halt', 0.08)):
        actions.append('kill_switch')
        create_halt(lock, 'max_drawdown_halt')
        halted = True
    if single >= float(risk.get('max_single_name_nav_loss', 0.03)):
        actions.append('force_close_single_position')
    return {'halted': halted or lock.exists(), 'actions': actions, 'lock_file': str(lock)}
