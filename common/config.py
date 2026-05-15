"""Configuration loading helpers.

The project is intentionally local-first. Relative paths are resolved from the
project root and can be validated so accidental writes outside the project are
caught early.
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

try:  # python-dotenv is a project dependency, but keep imports graceful.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - exercised only in incomplete envs.
    def load_dotenv(*_: Any, **__: Any) -> bool:
        return False

try:
    import yaml
except ImportError as exc:  # pragma: no cover - PyYAML is a required dependency.
    yaml = None  # type: ignore[assignment]
    _YAML_IMPORT_ERROR = exc
else:
    _YAML_IMPORT_ERROR = None

PROJECT_ROOT = Path(__file__).resolve(strict=False).parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
Config = dict[str, Any]


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or validated."""


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> Config:
    """Return a recursive merge of two mappings without mutating either input."""

    merged: Config = deepcopy(dict(base))
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _expand_env(value: Any) -> Any:
    """Recursively expand $VARS in string values."""

    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_expand_env(item) for item in value)
    if isinstance(value, Mapping):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def ensure_project_path(path: str | os.PathLike[str], root: Path | None = None) -> Path:
    """Resolve a path and ensure it stays inside the project root.

    This helper is meant for cache/output/database paths that the platform may
    create. It prevents a bad config value such as ``../../other-repo/file`` from
    sending generated artifacts outside this project.
    """

    root_path = (root or PROJECT_ROOT).resolve(strict=False)
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root_path / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ConfigError(f"Path escapes project root: {resolved}") from exc
    return resolved


def load_config(
    config_path: str | os.PathLike[str] | None = None,
    *,
    env_file: str | os.PathLike[str] | None = None,
    overrides: Mapping[str, Any] | None = None,
    load_environment: bool = True,
) -> Config:
    """Load ``config.yaml`` with optional dotenv support and overrides.

    Args:
        config_path: Optional YAML config path. Relative paths resolve from the
            project root. Defaults to ``CONFIG_PATH`` from the environment, then
            ``config.yaml``.
        env_file: Optional dotenv file. Defaults to ``.env`` in the project root.
        overrides: Optional mapping recursively merged on top of the file config.
        load_environment: Set false in tests if environment loading is not wanted.
    """

    if load_environment:
        load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    if yaml is None:
        raise ConfigError("PyYAML is required to load config.yaml") from _YAML_IMPORT_ERROR

    raw_path = config_path or os.getenv("CONFIG_PATH") or DEFAULT_CONFIG_PATH
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve(strict=False)

    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    if not isinstance(loaded, Mapping):
        raise ConfigError(f"Configuration root must be a mapping: {path}")

    config = _expand_env(dict(loaded))
    if overrides:
        config = deep_merge(config, overrides)
    return config


def get_section(config: Mapping[str, Any], section: str) -> Mapping[str, Any]:
    """Return a required top-level config section."""

    value = config.get(section)
    if not isinstance(value, Mapping):
        raise ConfigError(f"Missing or invalid config section: {section}")
    return value
