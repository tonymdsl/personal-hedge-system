from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from common.config import ConfigError, ensure_project_path, load_config


def test_load_config_has_required_sections() -> None:
    config = load_config(load_environment=False)

    for section in (
        "project",
        "data",
        "providers",
        "scoring",
        "analysis",
        "portfolio",
        "risk",
        "execution",
        "reporting",
        "dashboard",
    ):
        assert section in config

    assert config["project"]["name"] == "Meridian Capital Partners"
    assert config["project"]["mode"] == "research_paper"
    assert config["execution"]["mode"] == "paper"
    assert config["execution"]["dry_run_default"] is True
    assert config["execution"]["allow_live_trading"] is False


def test_scoring_weights_sum_to_one() -> None:
    config = load_config(load_environment=False)
    weights = config["scoring"]["default_weights"]
    assert sum(weights.values()) == pytest.approx(1.0)


def test_ensure_project_path_blocks_parent_escape() -> None:
    with pytest.raises(ConfigError):
        ensure_project_path("../outside-this-project.txt")


def test_streamlit_auth_runtime_dependency_is_declared() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [dependency.lower() for dependency in pyproject["project"]["dependencies"]]

    assert any(dependency.startswith("authlib>=") for dependency in dependencies)


def test_clerk_secrets_example_overrides_streamlit_default_prompt() -> None:
    example = tomllib.loads(Path(".streamlit/secrets.example.toml").read_text(encoding="utf-8"))

    assert example["auth"]["client_kwargs"]["prompt"] == "consent"


def test_container_config_excludes_local_secrets_and_runs_streamlit() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    entrypoint = Path("ops/docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "run_dashboard.py" in dockerfile
    assert "pip install -e" in dockerfile
    assert "ops/docker-entrypoint.sh" in dockerfile
    assert ".streamlit/secrets.toml" not in dockerfile
    assert "STREAMLIT_AUTH_REDIRECT_URI" in entrypoint
    assert "CLERK_OAUTH_CLIENT_SECRET" in entrypoint
    assert ".env" in dockerignore
    assert ".streamlit/secrets.toml" in dockerignore
    assert "cache/" in dockerignore
    assert "output/" in dockerignore
