"""Provider discovery and selection for Layer 1 data ingestion.

Selection is intentionally deterministic and environment-driven: paid providers are
used only when their configured API key exists; local/free providers such as
``yfinance`` and ``sec_edgar`` remain available without secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_ENVIRONMENT_VARIABLES: dict[str, str] = {
    "polygon": "POLYGON_API_KEY",
    "fmp": "FMP_API_KEY",
    "fred": "FRED_API_KEY",
    "codex_model": "CODEX_MODEL",
    "alpaca_key": "ALPACA_API_KEY",
    "alpaca_secret": "ALPACA_SECRET_KEY",
}

DEFAULT_PRIORITIES: dict[str, list[str]] = {
    "prices": ["polygon", "yfinance"],
    "fundamentals": ["fmp", "yfinance"],
    "transcripts": ["fmp"],
    "macro": ["fred"],
    "filings": ["sec_edgar"],
    "sec": ["sec_edgar"],
    "universe": ["wikipedia_sp500"],
    "short_interest": ["yfinance"],
    "estimates": ["yfinance"],
    "earnings_calendar": ["yfinance"],
}

KEYLESS_PROVIDERS = {
    "yfinance",
    "sec_edgar",
    "wikipedia_sp500",
    "local_cache",
}

PROVIDER_ALIASES = {
    "sec": "sec_edgar",
    "sec-edgar": "sec_edgar",
    "sec_edgar": "sec_edgar",
    "wiki": "wikipedia_sp500",
    "wikipedia": "wikipedia_sp500",
    "wikipedia_sp500": "wikipedia_sp500",
    "yahoo": "yfinance",
    "yf": "yfinance",
    "yfinance": "yfinance",
    "polygon": "polygon",
    "fmp": "fmp",
    "fred": "fred",
    "local": "local_cache",
    "local_cache": "local_cache",
}


@dataclass(frozen=True)
class ProviderSelection:
    """Result returned by :func:`select_provider`."""

    category: str
    provider: str | None
    available: bool
    env_var: str | None = None
    api_key: str | None = None
    reason: str = ""

    @property
    def name(self) -> str | None:
        """Alias for callers that prefer ``selection.name``."""

        return self.provider


@dataclass(frozen=True)
class ProviderStatus:
    """Availability details for a configured provider."""

    provider: str
    available: bool
    env_var: str | None = None
    reason: str = ""


def canonical_provider_name(provider: str) -> str:
    """Return a normalized provider name."""

    key = str(provider).strip().lower().replace(" ", "_")
    return PROVIDER_ALIASES.get(key, key)


def _providers_section(config: Mapping[str, object] | None) -> Mapping[str, object]:
    if not config:
        return {}
    value = config.get("providers")
    return value if isinstance(value, Mapping) else {}


def environment_variables(config: Mapping[str, object] | None = None) -> dict[str, str]:
    """Return provider -> env var mapping merged with project config."""

    merged = dict(DEFAULT_ENVIRONMENT_VARIABLES)
    section = _providers_section(config)
    configured = section.get("environment_variables")
    if isinstance(configured, Mapping):
        for provider, env_var in configured.items():
            if provider and env_var:
                merged[canonical_provider_name(str(provider))] = str(env_var)
    return merged


def provider_priorities(config: Mapping[str, object] | None = None) -> dict[str, list[str]]:
    """Return category -> ordered provider list merged with project config."""

    merged = {category: list(values) for category, values in DEFAULT_PRIORITIES.items()}
    section = _providers_section(config)
    configured = section.get("priority")
    if isinstance(configured, Mapping):
        for category, providers in configured.items():
            if isinstance(providers, (list, tuple)):
                merged[str(category)] = [canonical_provider_name(str(item)) for item in providers]
    default_market = section.get("default_market_data")
    if default_market and "prices" not in merged:
        merged["prices"] = [canonical_provider_name(str(default_market))]
    default_filings = section.get("default_filings")
    if default_filings and "filings" not in merged:
        merged["filings"] = [canonical_provider_name(str(default_filings))]
    return merged


def get_api_key(
    provider: str,
    config: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the configured API key for ``provider`` if present and non-empty."""

    provider_name = canonical_provider_name(provider)
    env_var = environment_variables(config).get(provider_name)
    if not env_var:
        return None
    env = environ if environ is not None else os.environ
    value = env.get(env_var)
    if value is None or str(value).strip() == "":
        return None
    return str(value)


def provider_status(
    provider: str,
    config: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProviderStatus:
    """Return availability details for a provider."""

    provider_name = canonical_provider_name(provider)
    if provider_name in KEYLESS_PROVIDERS:
        return ProviderStatus(provider=provider_name, available=True, reason="keyless_provider")

    env_var = environment_variables(config).get(provider_name)
    if not env_var:
        return ProviderStatus(
            provider=provider_name,
            available=False,
            reason="no_environment_variable_configured",
        )

    key = get_api_key(provider_name, config=config, environ=environ)
    if key:
        return ProviderStatus(
            provider=provider_name,
            available=True,
            env_var=env_var,
            reason=f"{env_var}_is_configured",
        )
    return ProviderStatus(
        provider=provider_name,
        available=False,
        env_var=env_var,
        reason=f"missing_{env_var}",
    )


def list_provider_statuses(
    category: str,
    config: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[ProviderStatus]:
    """List availability for providers configured for ``category``."""

    priorities = provider_priorities(config).get(category, [])
    return [provider_status(provider, config=config, environ=environ) for provider in priorities]


def select_provider(
    category: str,
    config: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
    preferred: str | None = None,
) -> ProviderSelection:
    """Select the first available provider for a data category.

    Args:
        category: Provider category such as ``prices`` or ``fundamentals``.
        config: Optional loaded project config.
        environ: Optional environment mapping, useful for tests.
        preferred: Optional provider to try before configured priorities.

    Returns:
        A :class:`ProviderSelection`. ``available`` is false when no provider can
        be used (for example FMP transcripts without ``FMP_API_KEY``).
    """

    seen: set[str] = set()
    ordered: list[str] = []
    if preferred:
        ordered.append(canonical_provider_name(preferred))
    ordered.extend(provider_priorities(config).get(category, []))

    for provider in ordered:
        provider_name = canonical_provider_name(provider)
        if provider_name in seen:
            continue
        seen.add(provider_name)
        status = provider_status(provider_name, config=config, environ=environ)
        if status.available:
            return ProviderSelection(
                category=category,
                provider=status.provider,
                available=True,
                env_var=status.env_var,
                api_key=get_api_key(status.provider, config=config, environ=environ),
                reason=status.reason,
            )

    return ProviderSelection(
        category=category,
        provider=None,
        available=False,
        reason=f"no_available_provider_for_{category}",
    )


def select_provider_name(
    category: str,
    config: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
    preferred: str | None = None,
) -> str | None:
    """Convenience wrapper returning only the provider name."""

    return select_provider(category, config=config, environ=environ, preferred=preferred).provider
