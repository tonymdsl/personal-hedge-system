"""Persistent local portfolio preferences."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from common.config import PROJECT_ROOT


DEFAULT_PREFERENCES_PATH = PROJECT_ROOT / "cache" / "portfolio_preferences.json"
VALID_OPTIMIZER_METHODS = ("mvo", "conviction_tilt")
CONVICTION_METHOD = "conviction_tilt"


def _optimizer_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    portfolio = config.get("portfolio", {}) if isinstance(config, Mapping) else {}
    optimizer = portfolio.get("optimizer", {}) if isinstance(portfolio, Mapping) else {}
    return optimizer if isinstance(optimizer, Mapping) else {}


def _bool_config(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def allow_mvo(config: Mapping[str, Any] | None) -> bool:
    return _bool_config(_optimizer_config(config).get("allow_mvo"), default=True)


def _canonical_method(method: Any) -> str | None:
    if method is None:
        return None
    candidate = str(method).strip().lower()
    if not candidate:
        return None
    if candidate == "conviction":
        return CONVICTION_METHOD
    return candidate


def default_optimizer_method(config: Mapping[str, Any] | None = None) -> str:
    default_method = _canonical_method(_optimizer_config(config).get("default_method"))
    if default_method == "mvo" and not allow_mvo(config):
        return CONVICTION_METHOD
    if default_method in VALID_OPTIMIZER_METHODS:
        return default_method
    return CONVICTION_METHOD


def normalize_optimizer_method(
    method: Any,
    *,
    config: Mapping[str, Any] | None = None,
    fallback: Any = None,
) -> str:
    candidate = _canonical_method(method)
    if candidate == "mvo" and not allow_mvo(config):
        candidate = None
    if candidate in VALID_OPTIMIZER_METHODS:
        return candidate

    fallback_candidate = _canonical_method(fallback)
    if fallback_candidate == "mvo" and not allow_mvo(config):
        fallback_candidate = None
    if fallback_candidate in VALID_OPTIMIZER_METHODS:
        return fallback_candidate

    return default_optimizer_method(config)


def allowed_optimizer_methods(config: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    if allow_mvo(config):
        return VALID_OPTIMIZER_METHODS
    return (CONVICTION_METHOD,)


def _preferences_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else DEFAULT_PREFERENCES_PATH


def _read_preferences(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def load_portfolio_preferences(
    *,
    config: Mapping[str, Any] | None = None,
    path: str | Path | None = None,
) -> dict[str, str]:
    raw = _read_preferences(_preferences_path(path))
    return {
        "optimizer_method": normalize_optimizer_method(
            raw.get("optimizer_method"),
            config=config,
        )
    }


def save_portfolio_preferences(
    optimizer_method: Any,
    *,
    config: Mapping[str, Any] | None = None,
    path: str | Path | None = None,
) -> dict[str, str]:
    preferences_path = _preferences_path(path)
    current = _read_preferences(preferences_path)
    next_preferences = {
        **current,
        "optimizer_method": normalize_optimizer_method(
            optimizer_method,
            config=config,
            fallback=current.get("optimizer_method"),
        ),
    }

    if current != next_preferences:
        preferences_path.parent.mkdir(parents=True, exist_ok=True)
        preferences_path.write_text(
            json.dumps(next_preferences, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return {"optimizer_method": str(next_preferences["optimizer_method"])}


def preferred_optimizer_method(
    config: Mapping[str, Any] | None = None,
    *,
    path: str | Path | None = None,
) -> str:
    return load_portfolio_preferences(config=config, path=path)["optimizer_method"]
