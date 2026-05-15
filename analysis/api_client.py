"""Optional Codex CLI analysis wrapper plus JSON and cost utilities.

The default qualitative provider is the local Codex CLI, which can use the
user's ChatGPT/Codex subscription via ``~/.codex/auth.json``.  No model name is
invented: ``CODEX_MODEL``/config may override Codex CLI defaults, otherwise the
CLI profile decides.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests

from .cost_tracker import CostEstimate


class APIClientError(RuntimeError):
    """Base error for qualitative API work."""


class APIClientConfigError(APIClientError):
    """Raised when optional qualitative provider configuration is incomplete."""


class JSONExtractionError(APIClientError, ValueError):
    """Raised when a JSON object/array cannot be extracted from model text."""


def _split_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        return [part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part for part in parts]
    return parts


_CODE_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _json_loads(candidate: str) -> Any:
    return json.loads(candidate.strip())


def _balanced_json_candidates(text: str) -> list[str]:
    """Return balanced object/array substrings found in text."""

    candidates: list[str] = []
    pairs = {"{": "}", "[": "]"}
    for start, opening in enumerate(text):
        if opening not in pairs:
            continue
        stack = [pairs[opening]]
        in_string = False
        escape = False
        for idx in range(start + 1, len(text)):
            char = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char in pairs:
                stack.append(pairs[char])
                continue
            if stack and char == stack[-1]:
                stack.pop()
                if not stack:
                    candidates.append(text[start : idx + 1])
                    break
    return candidates


def extract_json(raw: str | bytes | Mapping[str, Any] | Sequence[Any]) -> Any:
    """Extract JSON from raw JSON, markdown code fences, or prose.

    The return value is the parsed Python object.  Existing dict/list objects are
    returned unchanged, which makes the helper convenient in tests and cached
    paths.
    """

    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    text = text.strip()
    if not text:
        raise JSONExtractionError("Cannot extract JSON from empty response")

    # Fast path: the whole response is valid JSON.
    try:
        return _json_loads(text)
    except json.JSONDecodeError:
        pass

    # Prefer explicit code fences, especially ```json blocks.
    for match in _CODE_FENCE_RE.finditer(text):
        candidate = match.group(1).strip()
        if not candidate:
            continue
        try:
            return _json_loads(candidate)
        except json.JSONDecodeError:
            continue

    # Finally scan prose for balanced objects/arrays and parse the first valid one.
    for candidate in _balanced_json_candidates(text):
        try:
            return _json_loads(candidate)
        except json.JSONDecodeError:
            continue

    raise JSONExtractionError("No valid JSON object or array found in response")


# Backwards-compatible/common alias for callers that prefer a longer name.
extract_json_from_response = extract_json


def estimate_tokens(text: str | bytes | Mapping[str, Any] | Sequence[Any]) -> int:
    """Return a conservative local token estimate without provider calls.

    Provider tokenization is model-specific; for offline planning we use a
    simple character/word heuristic and take the larger estimate.
    """

    if isinstance(text, bytes):
        rendered = text.decode("utf-8", errors="replace")
    elif isinstance(text, str):
        rendered = text
    else:
        rendered = json.dumps(text, sort_keys=True, ensure_ascii=False)
    stripped = rendered.strip()
    if not stripped:
        return 0
    char_estimate = (len(stripped) + 3) // 4
    word_estimate = len(stripped.split())
    return max(1, int(max(char_estimate, word_estimate)))


def _analysis_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    section = (config or {}).get("analysis", {}) if isinstance(config, Mapping) else {}
    return section if isinstance(section, Mapping) else {}


def resolve_codex_model(config: Mapping[str, Any] | None = None, *, model: str | None = None) -> str | None:
    """Resolve an optional Codex model override.

    A blank result is valid: the Codex CLI then uses the user's configured
    subscription/profile default.  No model name is invented here.
    """

    if model and str(model).strip():
        return str(model).strip()
    analysis = _analysis_config(config)
    model_env = str(analysis.get("model_env") or "CODEX_MODEL")
    env_model = os.getenv(model_env) or os.getenv("CODEX_MODEL")
    configured_model = analysis.get("model") or analysis.get("default_model")
    resolved = str(env_model or configured_model or "").strip()
    return resolved or None


def resolve_openrouter_model(config: Mapping[str, Any] | None = None, *, model: str | None = None) -> str:
    """Resolve the OpenRouter model, defaulting to a free finance-capable generalist."""

    if model and str(model).strip():
        return str(model).strip()
    analysis = _analysis_config(config)
    model_env = str(analysis.get("model_env") or "OPENROUTER_MODEL")
    env_model = os.getenv(model_env) or os.getenv("OPENROUTER_MODEL")
    configured_model = analysis.get("model") or analysis.get("default_model")
    return str(env_model or configured_model or "deepseek/deepseek-v4-flash:free").strip()



def estimate_call_cost(
    prompt: str | Mapping[str, Any] | Sequence[Any],
    *,
    expected_output_tokens: int = 1_000,
    config: Mapping[str, Any] | None = None,
    model: str | None = None,
    analyzer: str | None = None,
    ticker: str | None = None,
    artifact_id: str | None = None,
) -> CostEstimate:
    """Estimate tokens and dollar cost using config-supplied pricing if present.

    Supported config keys under ``analysis``:
    - ``pricing.input_cost_per_million_tokens`` / ``pricing.output_cost_per_million_tokens``
    - or ``input_cost_per_million_tokens`` / ``output_cost_per_million_tokens``.

    If pricing is absent, token counts are still estimated and dollar cost is 0,
    avoiding invented provider pricing.
    """

    analysis = _analysis_config(config)
    pricing = analysis.get("pricing", {}) if isinstance(analysis.get("pricing", {}), Mapping) else {}
    input_per_million = float(
        pricing.get("input_cost_per_million_tokens", analysis.get("input_cost_per_million_tokens", 0.0)) or 0.0
    )
    output_per_million = float(
        pricing.get("output_cost_per_million_tokens", analysis.get("output_cost_per_million_tokens", 0.0)) or 0.0
    )
    input_tokens = estimate_tokens(prompt)
    output_tokens = max(0, int(expected_output_tokens))
    return CostEstimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cost_usd=input_tokens * input_per_million / 1_000_000.0,
        output_cost_usd=output_tokens * output_per_million / 1_000_000.0,
        model=model,
        analyzer=analyzer,
        ticker=ticker,
        artifact_id=artifact_id,
    )



@dataclass
class CodexAnalysisClient:
    """Non-interactive Codex CLI wrapper for qualitative analysis.

    This uses `codex exec` so the user's local Codex/ChatGPT subscription can be
    used without storing API keys in the project.  The command is read-only by
    default and writes the final answer to a temporary file via
    `--output-last-message`.
    """

    config: Mapping[str, Any] | None = None
    model: str | None = None
    codex_command: str = "codex"
    timeout_seconds: int | None = None
    sandbox: str = "read-only"
    skip_git_repo_check: bool = True

    def __post_init__(self) -> None:
        analysis = _analysis_config(self.config)
        self.model = resolve_codex_model(self.config, model=self.model)
        self.codex_command = str(analysis.get("codex_command", self.codex_command) or "codex")
        self.timeout_seconds = int(
            self.timeout_seconds
            if self.timeout_seconds is not None
            else analysis.get("codex_timeout_seconds", os.getenv("CODEX_TIMEOUT_SECONDS", 300))
        )
        self.sandbox = str(analysis.get("codex_sandbox", self.sandbox) or "read-only")
        self.skip_git_repo_check = bool(analysis.get("codex_skip_git_repo_check", self.skip_git_repo_check))

    def _build_command(self, output_path: Path) -> list[str]:
        command_prefix = _split_command(self.codex_command)
        if not command_prefix:
            raise APIClientConfigError("analysis.codex_command cannot be empty")
        command = [*command_prefix, "exec"]
        if self.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        command.extend(
            [
                "--sandbox",
                self.sandbox,
                "--config",
                'approval_policy="never"',
                "--config",
                'shell_environment_policy.inherit="none"',
                "--output-last-message",
                str(output_path),
                "--color",
                "never",
            ]
        )
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")
        return command

    @staticmethod
    def _compose_prompt(prompt: str, system_prompt: str | None = None) -> str:
        if not system_prompt:
            return prompt
        return f"SYSTEM:\n{system_prompt}\n\nUSER:\n{prompt}"

    def complete(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        use_prompt_cache: bool | None = None,
    ) -> str:
        """Run Codex CLI and return its final message text."""

        del max_tokens, temperature, use_prompt_cache  # Codex CLI profile controls these.
        with tempfile.TemporaryDirectory(prefix="meridian_codex_") as tmpdir:
            output_path = Path(tmpdir) / "codex-response.txt"
            command = self._build_command(output_path)
            result = subprocess.run(
                command,
                input=self._compose_prompt(prompt, system_prompt),
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip() or result.stdout.strip()
                raise APIClientError(f"Codex CLI failed with exit code {result.returncode}: {stderr}")
            if output_path.exists():
                text = output_path.read_text(encoding="utf-8").strip()
                if text:
                    return text
            stdout = result.stdout.strip()
            if stdout:
                return stdout
            raise APIClientError("Codex CLI completed without producing a response")

    def complete_json(self, prompt: str, **kwargs: Any) -> Any:
        """Run Codex and parse/extract JSON from the returned text."""

        return extract_json(self.complete(prompt, **kwargs))


CodexClient = CodexAnalysisClient


@dataclass
class OpenRouterAnalysisClient:
    """OpenRouter chat-completions client for cloud deployments.

    OpenRouter is OpenAI-compatible enough for this project, but using
    ``requests`` keeps the deployment small and avoids adding another SDK.
    """

    config: Mapping[str, Any] | None = None
    model: str | None = None
    api_key: str | None = None
    session: Any | None = None
    timeout_seconds: int | None = None
    base_url: str = "https://openrouter.ai/api/v1"

    def __post_init__(self) -> None:
        analysis = _analysis_config(self.config)
        api_key_env = str(analysis.get("openrouter_api_key_env") or "OPENROUTER_API_KEY")
        self.api_key = str(self.api_key or os.getenv(api_key_env) or os.getenv("OPENROUTER_API_KEY") or "").strip()
        if not self.api_key:
            raise APIClientConfigError(f"{api_key_env} is required for analysis.provider=openrouter")
        self.model = resolve_openrouter_model(self.config, model=self.model)
        self.timeout_seconds = int(
            self.timeout_seconds
            if self.timeout_seconds is not None
            else analysis.get("openrouter_timeout_seconds", analysis.get("codex_timeout_seconds", 300))
        )
        self.base_url = str(analysis.get("openrouter_base_url", self.base_url) or self.base_url).rstrip("/")
        self.session = self.session or requests.Session()

    def _headers(self) -> dict[str, str]:
        analysis = _analysis_config(self.config)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        site_url = str(os.getenv("OPENROUTER_SITE_URL") or analysis.get("openrouter_site_url") or "").strip()
        app_name = str(os.getenv("OPENROUTER_APP_NAME") or analysis.get("openrouter_app_name") or "").strip()
        if site_url:
            headers["HTTP-Referer"] = site_url
        if app_name:
            headers["X-OpenRouter-Title"] = app_name
        return headers

    @staticmethod
    def _messages(prompt: str, system_prompt: str | None = None) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    def complete(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        use_prompt_cache: bool | None = None,
    ) -> str:
        del use_prompt_cache
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(prompt, system_prompt),
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature

        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise APIClientError(f"OpenRouter request failed: {exc}") from exc
        except ValueError as exc:
            raise APIClientError("OpenRouter returned invalid JSON") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise APIClientError("OpenRouter response did not include a chat message") from exc
        text = str(content).strip()
        if not text:
            raise APIClientError("OpenRouter returned an empty response")
        return text

    def complete_json(self, prompt: str, **kwargs: Any) -> Any:
        """Run OpenRouter and parse/extract JSON from the returned text."""

        return extract_json(self.complete(prompt, **kwargs))


def create_analysis_client(config: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
    """Create the configured qualitative analysis client."""

    analysis = _analysis_config(config)
    provider = str(analysis.get("provider", "codex")).strip().lower()
    if provider in {"codex", "openai_codex", "codex_cli"}:
        return CodexAnalysisClient(config=config, **kwargs)
    if provider in {"openrouter", "open_router"}:
        return OpenRouterAnalysisClient(config=config, **kwargs)
    raise APIClientConfigError(f"Unsupported analysis provider: {provider}; expected codex or openrouter")


# Short aliases used by scripts/tests.
AnalysisClient = CodexAnalysisClient
