"""Streamlit dashboard with a JARVIS-style long/short fund cockpit."""
from __future__ import annotations

import sqlite3
import json
import os
import subprocess
from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlencode

import pandas as pd
import plotly.graph_objects as go
import requests

from common.config import PROJECT_ROOT, ensure_project_path, load_config
from portfolio import preferences as portfolio_preferences
from portfolio.state import PortfolioState
from reporting.position_attribution import fifo_round_trips


PAGES = ["Portfolio", "Research", "Risk", "Performance", "Execution", "Letter"]
NAV_NUMERALS = {
    "Portfolio": "I",
    "Research": "II",
    "Risk": "III",
    "Performance": "IV",
    "Execution": "V",
    "Letter": "VI",
}

THEME = {
    "background": "#0E1117",
    "panel": "#10141D",
    "border": "#1A1C24",
    "text": "#E7EDF5",
    "muted": "#8B95A5",
    "green": "#00FF41",
    "blue": "#4EA1FF",
    "purple": "#5F6877",
    "pink": "#FF3131",
    "amber": "#E8B931",
}

PAPER_ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_ALPACA_BASE_URL = "https://api.alpaca.markets"

DATA_TABLES = [
    "universe",
    "daily_prices",
    "fundamental_statements",
    "fundamental_ratios",
    "sec_filings",
    "earnings_calendar",
    "analyst_estimates",
    "earnings_transcripts",
    "insider_transactions",
    "institutional_holdings",
    "short_interest_snapshots",
    "run_log",
]

MAX_CHAT_TURNS = 6
SNAPSHOT_MAX_ROWS = 20
LETTER_SUMMARY_MAX_CHARS = 20_000
FACTOR_COLUMN_GROUPS = [
    ("Momentum", ("momentum", "momentum_score")),
    ("Value", ("value", "value_score")),
    ("Quality", ("quality", "quality_score")),
    ("Growth", ("growth", "growth_score")),
    ("Revisions", ("revisions", "revisions_score")),
    ("Short Interest", ("short_interest", "short_interest_score")),
    ("Insider", ("insider", "insider_score")),
    ("Institutional", ("institutional", "institutional_score")),
]

AUTOPILOT_PAUSED_STATUSES = {"paused", "manual_pause", "manual_paused", "manually_paused", "operator_paused", "suspended"}
AUTOPILOT_FAILURE_STATUSES = {"failed", "error", "unavailable"}
AUTOPILOT_WAITING_STATUSES = {"running", "in_progress", "never_run", "skipped_duplicate", "open_orders_pending"}
AUTOPILOT_NON_ERROR_STATUSES = {"open_orders_pending"}
AUTH_SECRET_KEYS = ("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url")


def _as_sequence(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(value)
    return (value,)


def _normalized_emails(value: Any) -> tuple[str, ...]:
    return tuple(
        item
        for item in (str(raw).strip().lower() for raw in _as_sequence(value))
        if item and "@" in item
    )


def _normalized_domains(value: Any) -> tuple[str, ...]:
    return tuple(
        item
        for item in (str(raw).strip().lower().lstrip("@") for raw in _as_sequence(value))
        if item and "." in item
    )


def _dashboard_auth_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    dashboard = config.get("dashboard", {}) if isinstance(config.get("dashboard"), Mapping) else {}
    auth = dashboard.get("auth", {}) if isinstance(dashboard.get("auth"), Mapping) else {}
    return {
        "enabled": bool(auth.get("enabled", False)),
        "provider": str(auth.get("provider", "clerk_oidc") or "clerk_oidc"),
        "allowed_emails": _normalized_emails(auth.get("allowed_emails")),
        "allowed_domains": _normalized_domains(auth.get("allowed_domains")),
        "require_verified_email": bool(auth.get("require_verified_email", True)),
        "login_button_label": str(auth.get("login_button_label", "Log in with Clerk") or "Log in with Clerk"),
    }


def _mapping_get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        try:
            return source.get(key, default)
        except Exception:
            return default
    getter = getattr(source, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            return default
    return getattr(source, key, default)


def _missing_auth_secret_keys(st_module) -> list[str]:
    try:
        secrets = getattr(st_module, "secrets")
    except Exception:
        return list(AUTH_SECRET_KEYS)
    auth = _mapping_get(secrets, "auth", {})
    return [key for key in AUTH_SECRET_KEYS if not _mapping_get(auth, key)]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _authenticated_dashboard_user(st_module) -> dict[str, str | bool] | None:
    user = getattr(st_module, "user", None)
    if not _truthy(_mapping_get(user, "is_logged_in", False)):
        return None
    email = str(_mapping_get(user, "email", "") or "").strip().lower()
    name = str(_mapping_get(user, "name", "") or "").strip()
    return {
        "email": email,
        "name": name or email,
        "email_verified": _truthy(_mapping_get(user, "email_verified", False)),
    }


def _dashboard_auth_denial_reason(user: Mapping[str, Any], settings: Mapping[str, Any]) -> str | None:
    email = str(user.get("email", "") or "").strip().lower()
    if not email:
        return "No email claim was returned by Clerk."
    if settings.get("require_verified_email", True) and not _truthy(user.get("email_verified")):
        return "Email is not verified by Clerk."
    allowed_emails = set(settings.get("allowed_emails", ()))
    allowed_domains = set(settings.get("allowed_domains", ()))
    if not allowed_emails and not allowed_domains:
        return "No dashboard auth allowlist is configured."
    if email in allowed_emails:
        return None
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain in allowed_domains:
        return None
    return "Clerk account is not authorized for this dashboard."


def _auth_panel(title: str, body: str, detail: str = "") -> str:
    detail_html = f"<small>{escape(detail)}</small>" if detail else ""
    return f"""
    <div class="auth-shell">
        <div class="auth-panel">
            <div class="auth-kicker">Private dashboard</div>
            <h1>{escape(title)}</h1>
            <p>{escape(body)}</p>
            {detail_html}
        </div>
    </div>
    """


def _render_auth_setup_error(st_module, missing_keys: list[str]) -> None:
    st_module.markdown(
        _auth_panel(
            "Clerk setup required",
            "Create .streamlit/secrets.toml with the Clerk OAuth application values before opening the private dashboard.",
            f"Required [auth] keys: {', '.join(missing_keys)}",
        ),
        unsafe_allow_html=True,
    )


def _render_clerk_login(st_module, settings: Mapping[str, Any]) -> None:
    st_module.markdown(
        _auth_panel(
            "Meridian JARVIS is private",
            "Use an authorized Clerk account to open the dashboard.",
            "Access is denied unless the Clerk email is verified and present in the configured allowlist.",
        ),
        unsafe_allow_html=True,
    )
    st_module.button(
        str(settings.get("login_button_label", "Log in with Clerk")),
        on_click=st_module.login,
        type="primary",
        use_container_width=True,
    )


def _render_auth_denied(st_module, reason: str) -> None:
    st_module.markdown(
        _auth_panel(
            "Access denied",
            reason,
            "Use a permitted Clerk account or update dashboard.auth.allowed_emails / allowed_domains in config.yaml.",
        ),
        unsafe_allow_html=True,
    )
    st_module.button("Log out", on_click=st_module.logout, use_container_width=True)


def _enforce_dashboard_auth(st_module, config: Mapping[str, Any]) -> dict[str, str] | None:
    settings = _dashboard_auth_settings(config)
    if not settings["enabled"]:
        return None
    if not callable(getattr(st_module, "login", None)) or not callable(getattr(st_module, "logout", None)):
        st_module.markdown(
            _auth_panel(
                "Authentication requires a newer Streamlit",
                "Upgrade Streamlit to a version with st.login, st.user, and st.logout before exposing this dashboard.",
                "The project pins streamlit>=1.42 for built-in OIDC support.",
            ),
            unsafe_allow_html=True,
        )
        st_module.stop()

    missing_keys = _missing_auth_secret_keys(st_module)
    if missing_keys:
        _render_auth_setup_error(st_module, missing_keys)
        st_module.stop()

    user = _authenticated_dashboard_user(st_module)
    if user is None:
        _render_clerk_login(st_module, settings)
        st_module.stop()

    denial_reason = _dashboard_auth_denial_reason(user, settings)
    if denial_reason:
        _render_auth_denied(st_module, denial_reason)
        st_module.stop()

    return {"email": str(user["email"]), "name": str(user["name"])}


def _inject_css(st_module) -> None:
    st_module.markdown(
        """
        <style>
        :root {
            --bg: #0E1117;
            --bg-deep: #080B10;
            --surface: #10141D;
            --surface-2: #0B0F16;
            --surface-3: #141925;
            --fg: #E7EDF5;
            --muted: #8B95A5;
            --muted-2: #5F6877;
            --border: #1A1C24;
            --border-strong: #2B3443;
            --green: #00FF41;
            --red: #FF3131;
            --amber: #E8B931;
            --blue: #4EA1FF;
            --green-bg: rgba(0, 255, 65, 0.085);
            --red-bg: rgba(255, 49, 49, 0.09);
            --amber-bg: rgba(232, 185, 49, 0.09);
            --blue-bg: rgba(78, 161, 255, 0.09);
            --shell: var(--bg-deep);
            --panel: var(--surface);
            --panel-2: var(--surface-2);
            --panel-3: var(--surface-3);
            --text: var(--fg);
            --pink: var(--red);
            --purple: var(--muted-2);
            --slate: #343B49;
            --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
            --font-mono: "SFMono-Regular", "Cascadia Mono", "IBM Plex Mono", "JetBrains Mono", Consolas, "Liberation Mono", ui-monospace, monospace;
        }

        #MainMenu, footer, header, [data-testid="stToolbar"],
        [data-testid="stSidebar"], [data-testid="collapsedControl"] {
            display: none !important;
            visibility: hidden;
            height: 0;
        }

        html, body, [class*="css"] {
            font-family: var(--font-ui);
            letter-spacing: 0;
        }

        .stApp {
            background: var(--bg);
            color: var(--text);
        }

        [data-testid="stAppViewContainer"] > .main {
            background: transparent;
        }

        .block-container {
            max-width: 1540px;
            padding: 1.05rem 1.2rem 2.2rem;
        }

        .auth-shell {
            min-height: calc(100vh - 6rem);
            display: grid;
            place-items: center;
        }

        .auth-panel {
            width: min(520px, 100%);
            border: 1px solid var(--border-strong);
            background: rgba(9, 12, 18, 0.96);
            padding: 1.35rem;
        }

        .auth-kicker {
            color: var(--green);
            font-family: var(--font-mono);
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.08rem;
            margin-bottom: 0.7rem;
            text-transform: uppercase;
        }

        .auth-panel h1 {
            color: var(--fg);
            font-size: 1.35rem;
            line-height: 1.2;
            margin: 0 0 0.7rem;
        }

        .auth-panel p {
            color: var(--muted);
            margin: 0;
            line-height: 1.55;
        }

        .auth-panel small {
            display: block;
            color: var(--amber);
            font-family: var(--font-mono);
            margin-top: 0.85rem;
            overflow-wrap: anywhere;
        }

        .brand {
            display: grid;
            grid-template-columns: 46px minmax(0, 1fr);
            grid-template-areas:
                "mark kicker"
                "mark title"
                "mark subtitle";
            align-items: center;
            column-gap: 0.88rem;
            min-height: 72px;
            padding: 0.85rem 1rem;
            border: 1px solid var(--border);
            background: rgba(9, 12, 18, 0.92);
        }

        .brand:before {
            content: "M";
            grid-area: mark;
            width: 38px;
            height: 38px;
            display: grid;
            place-items: center;
            border: 1px solid rgba(0, 255, 65, 0.55);
            background: rgba(0, 255, 65, 0.05);
            color: var(--green);
            font-family: var(--font-mono);
            font-size: 1.15rem;
            font-weight: 800;
            line-height: 1;
        }

        .brand-kicker {
            grid-area: kicker;
            color: var(--green);
            font-family: var(--font-mono);
            font-size: 0.62rem;
            font-weight: 700;
            letter-spacing: 0.08rem;
            text-transform: uppercase;
        }

        .brand-title {
            grid-area: title;
            color: var(--text);
            font-size: 1.02rem;
            line-height: 1;
            font-weight: 800;
            letter-spacing: 0.04rem;
            text-transform: uppercase;
        }

        .brand-subtitle {
            grid-area: subtitle;
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 0.66rem;
            letter-spacing: 0.04rem;
            text-transform: uppercase;
            margin-top: 0.18rem;
        }

        .jarvis-nav {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            align-items: center;
            width: 100%;
            margin: 0.7rem 0 1rem;
            border: 1px solid var(--border);
            background: rgba(11, 14, 20, 0.95);
            overflow-x: auto;
            scrollbar-width: none;
        }

        .jarvis-nav::-webkit-scrollbar { display: none; }

        .jarvis-nav a {
            display: inline-flex;
            align-items: center;
            justify-content: flex-start;
            gap: 0.7rem;
            min-height: 3rem;
            padding: 0 1rem;
            border-right: 1px solid var(--border);
            color: var(--muted);
            text-decoration: none;
            font-family: var(--font-mono);
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.08rem;
            text-transform: uppercase;
            white-space: nowrap;
            position: relative;
        }

        .jarvis-nav a:last-child { border-right: 0; }
        .jarvis-nav a:hover {
            color: var(--green);
            background: rgba(0, 255, 65, 0.045);
        }

        .jarvis-nav a.active {
            color: var(--green);
            background: rgba(0, 255, 65, 0.07);
            box-shadow: inset 0 -2px 0 var(--green);
        }

        .jarvis-nav .roman {
            color: currentColor;
            min-width: 1.65rem;
            font-weight: 800;
            opacity: 0.82;
            letter-spacing: 0;
        }

        .topbar {
            min-width: 0;
            display: grid;
            grid-template-columns: minmax(260px, 360px) minmax(0, 1fr) minmax(280px, 360px);
            align-items: stretch;
            gap: 0;
            min-height: 58px;
            padding: 0;
            margin-bottom: 1rem;
            border: 1px solid var(--border);
            background: var(--bg-deep);
        }

        .product {
            min-width: 0;
            display: grid;
            align-content: center;
            padding: 0 16px;
            border-right: 1px solid var(--border);
        }

        .product strong {
            display: block;
            margin: 0;
            color: var(--fg);
            font-size: 13px;
            line-height: 1.1;
            letter-spacing: 0;
            text-transform: uppercase;
        }

        .product span {
            display: block;
            margin-top: 5px;
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 10px;
            line-height: 1;
            letter-spacing: 0;
            text-transform: uppercase;
        }

        .topbar-status-strip {
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            overflow: hidden;
            border-right: 1px solid var(--border);
        }

        .autopilot {
            flex: 0 0 auto;
            display: inline-grid;
            grid-template-columns: repeat(3, auto);
            border: 1px solid var(--border);
            background: var(--surface-2);
        }

        .state-button {
            min-width: 54px;
            min-height: 28px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0 9px;
            border-right: 1px solid var(--border);
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 10px;
            line-height: 1;
            letter-spacing: 0;
            text-transform: uppercase;
            text-decoration: none;
            white-space: nowrap;
        }

        .state-button:last-child { border-right: 0; }
        .state-button:hover { color: var(--fg); background: rgba(231, 237, 245, 0.04); }
        .state-button.active[data-state="on"] { color: var(--green); background: var(--green-bg); }
        .state-button.active[data-state="off"] { color: var(--red); background: var(--red-bg); }
        .state-button.active[data-state="paused"] { color: var(--amber); background: var(--amber-bg); }

        .badge {
            flex: 0 1 auto;
            min-width: 0;
            min-height: 24px;
            max-width: 190px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 0 8px;
            border: 1px solid var(--border-strong);
            background: rgba(20, 25, 37, 0.72);
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 10px;
            line-height: 1.1;
            letter-spacing: 0;
            text-transform: uppercase;
        }

        .badge .dot {
            width: 6px;
            height: 6px;
            flex: 0 0 6px;
            background: currentColor;
        }

        .badge span:last-child {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .badge.green { color: var(--green); border-color: rgba(0, 255, 65, 0.36); background: var(--green-bg); }
        .badge.red { color: var(--red); border-color: rgba(255, 49, 49, 0.38); background: var(--red-bg); }
        .badge.amber { color: var(--amber); border-color: rgba(232, 185, 49, 0.38); background: var(--amber-bg); }
        .badge.blue { color: var(--blue); border-color: rgba(78, 161, 255, 0.38); background: var(--blue-bg); }

        .auto-refresh-row {
            display: flex;
            justify-content: flex-end;
            margin: -0.55rem 0 0.72rem;
        }

        .auto-refresh-badge {
            display: inline-flex;
            align-items: center;
            min-height: 20px;
            padding: 0 7px;
            border: 1px solid rgba(78, 161, 255, 0.28);
            background: rgba(78, 161, 255, 0.055);
            color: var(--blue);
            font-family: var(--font-mono);
            font-size: 9px;
            line-height: 1;
            letter-spacing: 0;
            text-transform: uppercase;
        }

        .auto-refresh-badge.off {
            border-color: rgba(95, 104, 119, 0.36);
            background: rgba(95, 104, 119, 0.08);
            color: var(--muted);
        }

        .mode-switch {
            min-width: 0;
            display: grid;
            align-content: center;
            gap: 4px;
            padding: 0 12px;
        }

        .mode-buttons {
            min-width: 0;
            display: grid;
            grid-template-columns: 1fr 1fr;
            border: 1px solid var(--border);
            background: var(--surface-2);
        }

        .mode-button {
            min-width: 0;
            min-height: 28px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0 8px;
            border-right: 1px solid var(--border);
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 10px;
            line-height: 1.1;
            letter-spacing: 0;
            text-transform: uppercase;
            overflow-wrap: anywhere;
            text-align: center;
            text-decoration: none;
        }

        .mode-button:last-child { border-right: 0; }
        .mode-button:hover { color: var(--fg); background: rgba(231, 237, 245, 0.04); }
        .mode-button.active { color: var(--green); background: var(--green-bg); }
        .mode-button.locked { color: var(--blue); background: var(--blue-bg); }
        .mode-button.live-enabled { color: var(--amber); background: var(--amber-bg); }

        .last-updated {
            min-width: 0;
            color: var(--muted-2);
            font-family: var(--font-mono);
            font-size: 9px;
            line-height: 1;
            letter-spacing: 0;
            text-align: right;
            text-transform: uppercase;
            overflow-wrap: anywhere;
        }

        .topbar-notice {
            margin: -0.72rem 0 1rem;
            padding: 0.48rem 0.68rem;
            border: 1px solid rgba(232, 185, 49, 0.34);
            background: var(--amber-bg);
            color: var(--amber);
            font-family: var(--font-mono);
            font-size: 10px;
            line-height: 1.25;
            text-transform: uppercase;
        }

        .status-strip {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0;
            margin: -0.3rem 0 1rem;
            border: 1px solid var(--border);
            background: rgba(9, 12, 18, 0.88);
        }

        .status-cell {
            min-height: 3.1rem;
            padding: 0.72rem 0.9rem;
            border-right: 1px solid var(--border);
            font-family: var(--font-mono);
        }

        .status-cell:last-child { border-right: 0; }

        .status-cell span {
            display: block;
            color: var(--muted);
            font-size: 0.61rem;
            letter-spacing: 0.08rem;
            text-transform: uppercase;
        }

        .status-cell b {
            display: block;
            margin-top: 0.35rem;
            color: var(--text);
            font-size: 0.78rem;
            font-weight: 700;
        }

        .status-cell small {
            display: block;
            margin-top: 0.26rem;
            color: var(--muted);
            font-size: 0.59rem;
            line-height: 1.22;
            overflow-wrap: anywhere;
        }

        .status-cell b.ok { color: var(--green); }
        .status-cell b.warn { color: var(--amber); }

        .metric-card {
            --metric-rail: var(--slate);
            box-sizing: border-box;
            min-width: 0;
            min-height: 74px;
            margin-bottom: 0.62rem;
            border: 1px solid var(--border);
            border-left: 2px solid var(--metric-rail);
            border-radius: 0;
            padding: 11px 12px 10px;
            background: var(--surface);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.025);
            position: relative;
            overflow: visible;
            display: grid;
            grid-template-rows: auto minmax(0, 1fr) auto;
            gap: 8px;
            align-content: stretch;
        }

        .metric-card:before {
            content: none;
        }

        .metric-green { --metric-rail: var(--green); }
        .metric-pink { --metric-rail: var(--pink); }
        .metric-blue { --metric-rail: var(--blue); }
        .metric-amber { --metric-rail: var(--amber); }

        .metric-label {
            min-width: 0;
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 10px;
            font-weight: 700;
            line-height: 1.15;
            letter-spacing: 0;
            text-transform: uppercase;
            overflow-wrap: break-word;
        }

        .metric-value {
            min-width: 0;
            align-self: center;
            color: var(--text);
            font-family: var(--font-mono);
            font-size: 1.28rem;
            line-height: 1;
            font-weight: 700;
            letter-spacing: 0;
            overflow-wrap: normal;
            word-break: keep-all;
            white-space: normal;
        }

        .metric-detail {
            min-width: 0;
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 10px;
            line-height: 1.15;
            overflow-wrap: break-word;
        }

        .research-lower-spacer {
            height: 0.9rem;
        }

        .section-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 0.8rem;
            min-height: 2.4rem;
            margin: -0.25rem -0.25rem 0.75rem;
            padding: 0 0.2rem 0.62rem;
            border-bottom: 1px solid var(--border);
        }

        .section-head h3 {
            margin: 0;
            color: var(--text);
            font-family: var(--font-mono);
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.08rem;
            text-transform: uppercase;
        }

        .section-head span {
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 0.64rem;
            font-weight: 700;
            letter-spacing: 0.04rem;
            text-transform: uppercase;
        }

        [data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--border) !important;
            background: rgba(16, 20, 29, 0.9) !important;
            border-radius: 0 !important;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.025);
        }

        .dark-table-wrap {
            width: 100%;
            max-width: 100%;
            overflow: auto;
            border: 1px solid var(--border);
            background: var(--surface);
        }

        .dark-table {
            width: 100%;
            min-width: 720px;
            border-collapse: collapse;
            table-layout: fixed;
            color: #cbd3df;
            font-family: var(--font-mono);
            font-size: 11px;
            font-variant-numeric: tabular-nums;
        }

        .dark-table th,
        .dark-table td {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            text-align: left;
            vertical-align: middle;
        }

        .dark-table th {
            color: var(--muted);
            position: sticky;
            top: 0;
            z-index: 5;
            background: var(--bg-deep);
            font-size: 10px;
            letter-spacing: 0;
            text-transform: uppercase;
            font-weight: 600;
            padding: 8px 9px;
            border-bottom: 1px solid var(--border-strong);
            white-space: nowrap;
        }

        .dark-table td {
            color: #cbd3df;
            padding: 8px 9px;
            border-bottom: 1px solid rgba(42, 49, 64, 0.72);
            white-space: nowrap;
        }

        .dark-table .cell-text {
            display: block;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .dark-table .cell-wrap {
            white-space: normal;
            overflow-wrap: anywhere;
        }

        .dark-table tr:hover td {
            background: rgba(0, 255, 65, 0.035);
        }

        .candidate-board-title {
            margin: 0.15rem 0 0.78rem;
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 0.16rem;
            text-transform: uppercase;
        }

        .candidate-card {
            box-sizing: border-box;
            min-width: 0;
            min-height: 108px;
            border: 1px solid rgba(58, 66, 88, 0.82);
            border-radius: 8px;
            padding: 1rem 1rem 0.9rem;
            background:
                linear-gradient(135deg, rgba(25, 31, 47, 0.96), rgba(18, 22, 35, 0.96)),
                var(--surface);
            box-shadow: 0 14px 34px rgba(0, 0, 0, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.035);
            overflow: hidden;
        }

        .candidate-card + div[data-testid="stHorizontalBlock"] {
            margin-top: 0.55rem;
        }

        .candidate-card-top {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 0.8rem;
        }

        .candidate-identity {
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 0.65rem;
            flex-wrap: wrap;
        }

        .candidate-ticker {
            color: var(--fg);
            font-family: var(--font-mono);
            font-size: 1.22rem;
            font-weight: 800;
            line-height: 1;
            letter-spacing: 0.04rem;
        }

        .candidate-sector {
            display: inline-flex;
            align-items: center;
            min-height: 22px;
            padding: 0 0.48rem;
            border: 1px solid rgba(98, 91, 255, 0.22);
            border-radius: 999px;
            background: rgba(98, 91, 255, 0.12);
            color: #b8b3ff;
            font-family: var(--font-mono);
            font-size: 0.62rem;
            font-weight: 800;
            line-height: 1;
            letter-spacing: 0.02rem;
        }

        .candidate-score {
            flex: 0 0 auto;
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 1.1rem;
            font-weight: 800;
            line-height: 1;
        }

        .candidate-card-long .candidate-score { color: #35d6a0; }
        .candidate-card-short .candidate-score { color: #ff5b8f; }
        .candidate-card.status-approved { border-color: rgba(53, 214, 160, 0.52); }
        .candidate-card.status-rejected { border-color: rgba(255, 91, 143, 0.46); }
        .candidate-card.status-watch { border-color: rgba(232, 185, 49, 0.42); }

        .candidate-meta,
        .candidate-health {
            min-width: 0;
            margin-top: 0.68rem;
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 0.78rem;
            line-height: 1.35;
            overflow-wrap: anywhere;
        }

        .candidate-health {
            margin-top: 0.45rem;
            color: #d49c38;
            font-size: 0.72rem;
            font-weight: 800;
        }

        .candidate-health .muted {
            color: var(--muted);
            font-weight: 700;
        }

        div[data-testid="stButton"] button[kind="primary"] {
            border-color: rgba(107, 102, 255, 0.72) !important;
            border-radius: 8px !important;
            background: #6667f1 !important;
            color: #ffffff !important;
            box-shadow: 0 10px 26px rgba(98, 91, 255, 0.25) !important;
        }

        div[data-testid="stExpander"] {
            margin: 0.72rem 0 1.35rem;
            border-color: var(--border-strong) !important;
            border-radius: 8px !important;
            background: rgba(8, 11, 16, 0.68) !important;
        }

        div[data-testid="stExpander"] summary {
            color: var(--fg) !important;
            font-family: var(--font-mono) !important;
            font-weight: 700 !important;
        }

        .candidate-analysis-copy {
            color: #d7deea;
            font-family: var(--font-ui);
            font-size: 0.86rem;
            line-height: 1.58;
        }

        .candidate-analysis-copy h4 {
            margin: 0.4rem 0 0.75rem;
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.12rem;
            text-transform: uppercase;
        }

        .candidate-analysis-copy p {
            margin: 0 0 0.8rem;
        }

        .candidate-detail {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0;
            margin: 0.8rem 0;
            border: 1px solid var(--border);
            background: var(--surface-2);
        }

        .candidate-detail div {
            min-width: 0;
            padding: 0.72rem 0.78rem;
            border-right: 1px solid var(--border);
            border-bottom: 1px solid var(--border);
        }

        .candidate-detail div:nth-child(4n) {
            border-right: 0;
        }

        .candidate-detail span {
            display: block;
            margin-bottom: 0.42rem;
            color: var(--muted);
            font-family: var(--font-mono);
            font-size: 0.62rem;
            font-weight: 700;
            letter-spacing: 0.04rem;
            text-transform: uppercase;
        }

        .candidate-detail strong {
            display: block;
            min-width: 0;
            color: var(--fg);
            font-family: var(--font-mono);
            font-size: 0.82rem;
            line-height: 1.25;
            overflow-wrap: anywhere;
        }

        .terminal-panel {
            border: 1px solid var(--border);
            border-radius: 0;
            background: #080b10;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.025);
            padding: 0.92rem;
        }

        .terminal-panel pre {
            color: #cbd3df;
            font-family: var(--font-mono);
            font-size: 0.68rem;
            line-height: 1.62;
            white-space: pre-wrap;
            margin: 0;
        }

        .jarvis-history {
            margin-top: 1rem;
            display: grid;
            gap: 0.95rem;
        }

        .jarvis-response {
            border-left: 2px solid var(--green);
            background: #0b0e14;
            padding: 0.86rem 1rem 1rem;
        }

        .jarvis-question {
            color: var(--green);
            font-family: var(--font-mono);
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.08rem;
            text-transform: uppercase;
            margin-bottom: 0.72rem;
        }

        .jarvis-answer {
            color: #cbd3df;
            font-family: var(--font-mono);
            font-size: 0.78rem;
            line-height: 1.55;
        }

        div[data-testid="stForm"] {
            border: 0 !important;
            padding: 0 !important;
            background: transparent !important;
        }

        div[data-testid="stTextInput"] label {
            display: none !important;
        }

        div[data-testid="stTextInput"] input {
            min-height: 3rem;
            border: 1px solid var(--border-strong) !important;
            border-radius: 0 !important;
            background: #080b10 !important;
            color: var(--text) !important;
            font-family: var(--font-mono) !important;
            font-size: 0.82rem !important;
            box-shadow: none !important;
        }

        div[data-testid="stSelectbox"] label,
        div[data-testid="stTextArea"] label {
            color: var(--muted) !important;
            font-family: var(--font-mono) !important;
            font-size: 0.64rem !important;
            letter-spacing: 0.04rem !important;
        }

        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            min-height: 3rem;
            border: 1px solid var(--border-strong) !important;
            border-radius: 0 !important;
            background: #080b10 !important;
            color: var(--fg) !important;
            box-shadow: none !important;
        }

        div[data-testid="stSelectbox"] div[data-baseweb="select"] span,
        div[data-testid="stSelectbox"] div[data-baseweb="select"] svg {
            color: var(--fg) !important;
            fill: var(--fg) !important;
        }

        div[data-testid="stTextArea"] textarea {
            min-height: 4.2rem !important;
            border: 1px solid var(--border-strong) !important;
            border-radius: 0 !important;
            background: #080b10 !important;
            color: var(--fg) !important;
            font-family: var(--font-mono) !important;
            font-size: 0.78rem !important;
            box-shadow: none !important;
        }

        div[data-testid="stButton"] button {
            width: 100%;
            min-height: 2.9rem !important;
            border: 1px solid var(--border-strong) !important;
            border-radius: 0 !important;
            background: #0b0f16 !important;
            color: var(--fg) !important;
            font-family: var(--font-mono) !important;
            font-size: 0.74rem !important;
            font-weight: 800 !important;
            letter-spacing: 0.04rem !important;
            text-transform: uppercase !important;
            box-shadow: none !important;
        }

        div[data-testid="stButton"] button:hover {
            border-color: rgba(0, 255, 65, 0.45) !important;
            color: var(--green) !important;
            background: rgba(0, 255, 65, 0.075) !important;
        }

        div[data-testid="stFormSubmitButton"] button {
            border: 1px solid rgba(0, 255, 65, 0.42) !important;
            border-radius: 0 !important;
            background: rgba(0, 255, 65, 0.075) !important;
            color: var(--green) !important;
            font-family: var(--font-mono) !important;
            font-weight: 800 !important;
            letter-spacing: 0.1rem !important;
            text-transform: uppercase !important;
            min-height: 3rem !important;
            padding: 0 1.7rem !important;
            box-shadow: none !important;
        }

        .stPlotlyChart {
            border-radius: 0;
            overflow: hidden;
        }

        @media (max-width: 1180px) {
            .topbar {
                grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
                grid-template-rows: auto auto;
            }
            .topbar-status-strip {
                grid-column: 1 / -1;
                grid-row: 2;
                min-height: 42px;
                border-top: 1px solid var(--border);
                border-right: 0;
            }
            .badge { max-width: none; }
        }

        @media (max-width: 980px) {
            [data-testid="stAppViewContainer"] > .main {
                max-width: 100%;
                overflow-x: hidden;
            }
            .block-container {
                width: 100%;
                max-width: 100%;
                padding: 0.82rem 0.82rem 1.5rem;
                overflow-x: clip;
            }
            .block-container > div,
            div[data-testid="stVerticalBlock"],
            div[data-testid="stHorizontalBlock"],
            div[data-testid="column"] {
                min-width: 0 !important;
                max-width: 100% !important;
            }
            .topbar {
                width: 100%;
                max-width: 100%;
                grid-template-columns: minmax(0, 1fr);
                grid-template-rows: auto;
                overflow: visible;
            }
            .product {
                min-height: 54px;
                border-right: 0;
                border-bottom: 1px solid var(--border);
            }
            .topbar-status-strip {
                display: flex;
                grid-column: auto;
                grid-row: auto;
                width: 100%;
                max-width: 100%;
                flex-wrap: wrap;
                align-items: flex-start;
                min-height: auto;
                overflow: visible;
                border-top: 0;
                border-bottom: 1px solid var(--border);
            }
            .mode-switch {
                width: 100%;
                padding: 10px 12px;
            }
            .mode-button,
            .state-button {
                min-height: 30px;
                height: auto;
                white-space: normal;
            }
            .status-strip {
                width: 100%;
                max-width: 100%;
                grid-template-columns: minmax(0, 1fr);
                margin: -0.1rem 0 0.9rem;
            }
            .status-cell {
                border-right: 0;
                border-bottom: 1px solid var(--border);
            }
            .status-cell:nth-child(2n) { border-right: 0; }
            .status-cell:last-child { border-bottom: 0; }
            .metric-card {
                width: 100%;
                max-width: 100%;
                min-height: 78px;
                padding: 12px;
            }
            div[data-testid="stHorizontalBlock"] {
                display: grid !important;
                grid-template-columns: minmax(0, 1fr) !important;
                gap: 0.75rem !important;
            }
            div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
                width: 100% !important;
                min-width: 0 !important;
                flex: 1 1 auto !important;
            }
            .dark-table { min-width: 640px; }
            .candidate-detail {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .candidate-detail div:nth-child(4n) { border-right: 1px solid var(--border); }
            .candidate-detail div:nth-child(2n) { border-right: 0; }
            .jarvis-nav {
                max-width: 100%;
                grid-template-columns: repeat(6, minmax(132px, 1fr));
                justify-content: flex-start;
                margin: 0.2rem 0 0.85rem;
                overscroll-behavior-x: contain;
            }
            .jarvis-nav a {
                min-height: 2.75rem;
                padding: 0 0.85rem;
                font-size: 0.68rem;
            }
        }

        @media (max-width: 640px) {
            .block-container { padding: 0.72rem 0.68rem 1.35rem; }
            .brand {
                min-height: 64px;
                padding: 0.75rem 0.78rem;
            }
            .topbar { margin-bottom: 0.85rem; }
            .topbar-status-strip {
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
                padding: 8px;
            }
            .auto-refresh-row { justify-content: flex-start; margin-top: -0.48rem; }
            .status-strip { grid-template-columns: minmax(0, 1fr); }
            .status-cell { border-right: 0; }
            .autopilot { width: 100%; grid-template-columns: repeat(3, minmax(0, 1fr)); }
            .badge { flex: 1 1 150px; max-width: 100%; }
            .mode-switch { width: 100%; }
            .metric-value { font-size: 1.14rem; }
            .dark-table { min-width: 560px; }
            .candidate-detail { grid-template-columns: minmax(0, 1fr); }
            .candidate-detail div { border-right: 0 !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _config() -> dict[str, Any]:
    try:
        return load_config()
    except Exception:
        return {}


def _db_path(config: Mapping[str, Any]) -> Path:
    raw = config.get("project", {}).get("default_db_path", "cache/meridian.sqlite3")
    try:
        return ensure_project_path(raw, PROJECT_ROOT)
    except Exception:
        return PROJECT_ROOT / "cache" / "meridian.sqlite3"


def _table_exists(db_path: Path, table: str) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _table_count(db_path: Path, table: str) -> int:
    if not _table_exists(db_path, table):
        return 0
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _read_table(db_path: Path, table: str, *, limit: int = 200) -> pd.DataFrame:
    if not _table_exists(db_path, table):
        return pd.DataFrame()
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(f'SELECT * FROM "{table}" LIMIT {int(limit)}', conn)


def _read_csv(path: str) -> pd.DataFrame:
    resolved = PROJECT_ROOT / path
    if not resolved.exists():
        return pd.DataFrame()
    return pd.read_csv(resolved)


def _positions(db_path: Path) -> pd.DataFrame:
    return _read_table(db_path, "portfolio_positions")


def _dashboard_positions(
    config: Mapping[str, Any],
    db_path: Path,
    paper_snapshot: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, str, bool]:
    if _paper_alpaca_enabled(config) and _paper_snapshot_available(paper_snapshot):
        positions = paper_snapshot.get("positions")
        if isinstance(positions, pd.DataFrame):
            return positions, "Alpaca paper positions", True
    return _positions(db_path), "portfolio_positions", False


def _merge_candidate_analysis(candidates: pd.DataFrame, analysis: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty or analysis.empty or "ticker" not in candidates.columns or "ticker" not in analysis.columns:
        return candidates

    analysis_columns = [
        column
        for column in [
            "ticker",
            "analysis_summaries",
            "analysis_summary",
            "qualitative_score",
            "analysis_count",
            "analyzers_run",
            "qualitative_used",
        ]
        if column in analysis.columns
    ]
    if len(analysis_columns) <= 1:
        return candidates

    base = candidates.copy()
    base["ticker"] = base["ticker"].astype(str).str.strip().str.upper()
    enrich = analysis[analysis_columns].copy()
    enrich["ticker"] = enrich["ticker"].astype(str).str.strip().str.upper()
    enrich = enrich.drop_duplicates("ticker", keep="first")
    merged = base.merge(enrich, on="ticker", how="left", suffixes=("", "_analysis"))
    for column in analysis_columns:
        if column == "ticker":
            continue
        analysis_column = f"{column}_analysis"
        if analysis_column in merged.columns:
            merged[column] = merged[column].combine_first(merged[analysis_column])
            merged = merged.drop(columns=[analysis_column])
    return merged


def _candidate_universe() -> pd.DataFrame:
    analysis = _read_csv("output/analysis_results_latest.csv")
    for path in ["output/scored_universe_latest.csv", "output/factor_inputs.csv"]:
        frame = _read_csv(path)
        if not frame.empty:
            return _merge_candidate_analysis(frame, analysis)
    if not analysis.empty:
        return analysis
    return pd.DataFrame()


def _first_column(frame: pd.DataFrame, candidates: list[str] | tuple[str, ...]) -> str | None:
    columns = {str(column).lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
        resolved = columns.get(candidate.lower())
        if resolved:
            return resolved
    return None


def _score_column(frame: pd.DataFrame) -> str | None:
    return _first_column(frame, ("combined_score", "composite_score", "quant_score", "score"))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "long", "short", "approved"}


def _truthy_count(series: pd.Series) -> int:
    return int(series.map(_truthy).sum())


def _fmt_count(value: int) -> str:
    absolute = abs(int(value))
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(int(value))


def _candidate_side_counts(candidates: pd.DataFrame, config: Mapping[str, Any]) -> tuple[int, int]:
    if candidates.empty:
        return 0, 0

    side_col = _first_column(candidates, ("side", "candidate_side", "recommendation"))
    if side_col:
        sides = candidates[side_col].fillna("").astype(str).str.lower()
        return int(sides.str.contains("long").sum()), int(sides.str.contains("short").sum())

    long_col = _first_column(candidates, ("long_candidate", "is_long_candidate"))
    short_col = _first_column(candidates, ("short_candidate", "is_short_candidate"))
    if long_col or short_col:
        return (
            _truthy_count(candidates[long_col]) if long_col else 0,
            _truthy_count(candidates[short_col]) if short_col else 0,
        )

    score_col = _score_column(candidates)
    if not score_col:
        return 0, 0

    scoring = config.get("scoring", {}) if isinstance(config.get("scoring"), Mapping) else {}
    long_quantile = float(scoring.get("long_candidate_quantile", 0.80) or 0.80)
    short_quantile = float(scoring.get("short_candidate_quantile", 0.20) or 0.20)
    frame = candidates.copy()
    frame["_score"] = pd.to_numeric(frame[score_col], errors="coerce")
    frame = frame.dropna(subset=["_score"])
    if frame.empty:
        return 0, 0

    sector_col = _first_column(frame, ("sector", "gics_sector"))
    long_mask = pd.Series(False, index=frame.index)
    short_mask = pd.Series(False, index=frame.index)
    groups = frame.groupby(sector_col, dropna=False) if sector_col else [(None, frame)]
    for _name, group in groups:
        long_cutoff = group["_score"].quantile(long_quantile)
        short_cutoff = group["_score"].quantile(short_quantile)
        long_mask.loc[group.index] = group["_score"] >= long_cutoff
        short_mask.loc[group.index] = group["_score"] <= short_cutoff
    return int(long_mask.sum()), int(short_mask.sum())


def _crowding_warning_count(candidates: pd.DataFrame, config: Mapping[str, Any]) -> int:
    if candidates.empty:
        return 0
    flag_col = _first_column(candidates, ("crowding_warning", "is_crowded", "crowded"))
    if flag_col:
        return _truthy_count(candidates[flag_col])
    z_col = _first_column(candidates, ("crowding_zscore", "crowding_z", "crowding"))
    if not z_col:
        return 0
    scoring = config.get("scoring", {}) if isinstance(config.get("scoring"), Mapping) else {}
    crowding = scoring.get("crowding", {}) if isinstance(scoring.get("crowding"), Mapping) else {}
    threshold = float(crowding.get("zscore_warning_threshold", 2.0) or 2.0)
    values = pd.to_numeric(candidates[z_col], errors="coerce").abs()
    return int((values >= threshold).sum())


def _sql_count(db_path: Path, table: str, where: str = "", params: tuple[object, ...] = ()) -> int:
    if not _table_exists(db_path, table):
        return 0
    sql = f'SELECT COUNT(*) FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def _latest_vix(db_path: Path) -> str:
    if not _table_exists(db_path, "daily_prices"):
        return "N/A"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT close
            FROM daily_prices
            WHERE UPPER(ticker) IN ('^VIX', 'VIX') AND close IS NOT NULL
            ORDER BY date DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return "N/A"
    try:
        return f"{float(row[0]):.1f}"
    except (TypeError, ValueError):
        return "N/A"


def _earnings_next_7d(db_path: Path) -> int:
    if not _table_exists(db_path, "earnings_calendar"):
        return 0
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=7)).isoformat()
    return _sql_count(db_path, "earnings_calendar", "earnings_date BETWEEN ? AND ?", (start, end))


def _factor_columns(frame: pd.DataFrame) -> list[tuple[str, str]]:
    columns: list[tuple[str, str]] = []
    for label, aliases in FACTOR_COLUMN_GROUPS:
        column = _first_column(frame, aliases)
        if column:
            columns.append((label, column))
    return columns


def _highest_dispersion_factor(candidates: pd.DataFrame) -> tuple[str, str]:
    factor_cols = _factor_columns(candidates)
    if candidates.empty or not factor_cols:
        return "N/A", "No factor score output"
    dispersions: dict[str, float] = {}
    for label, column in factor_cols:
        values = pd.to_numeric(candidates[column], errors="coerce")
        if values.notna().sum() >= 2:
            dispersions[label] = float(values.std())
    if not dispersions:
        return "N/A", "Insufficient factor values"
    label, value = max(dispersions.items(), key=lambda item: item[1])
    return label.lower(), f"sigma {value:.1f}"


def _portfolio_metric_cards(
    config: Mapping[str, Any],
    db_path: Path,
    *,
    paper_snapshot: Mapping[str, Any] | None = None,
) -> list[tuple[str, str, str, str]]:
    candidates = _candidate_universe()
    long_candidates, short_candidates = _candidate_side_counts(candidates, config)
    universe_size = len(candidates) if not candidates.empty else _table_count(db_path, "universe")
    crowding = _crowding_warning_count(candidates, config)
    if _paper_snapshot_available(paper_snapshot):
        positions = paper_snapshot.get("positions")
        positions_count = len(positions) if isinstance(positions, pd.DataFrame) else 0
        positions_detail = "Alpaca paper positions"
    else:
        positions_count = _table_count(db_path, "portfolio_positions")
        positions_detail = "portfolio_positions"
    return [
        ("Universe", _fmt_count(universe_size), "scored universe rows", "blue"),
        ("Long cand.", _fmt_count(long_candidates), "top quintile per sector", "green"),
        ("Short cand.", _fmt_count(short_candidates), "bottom quintile per sector", "pink"),
        ("Positions", _fmt_count(positions_count), positions_detail, "purple"),
        ("Crowding", _fmt_count(crowding), "factor pairs flagged", "amber" if crowding else "green"),
        ("Insider events", _fmt_count(_table_count(db_path, "insider_transactions")), "Form 4 transactions", "blue"),
        (
            "CEO/CFO buys",
            _fmt_count(
                _sql_count(
                    db_path,
                    "insider_transactions",
                    "is_ceo_cfo = 1 AND is_open_market_purchase = 1",
                )
            ),
            "open-market purchases",
            "green",
        ),
        (
            "Cluster buys",
            _fmt_count(_sql_count(db_path, "insider_transactions", "cluster_buy = 1")),
            "3+ insiders / 30d",
            "green",
        ),
        ("VIX", _latest_vix(db_path), "latest daily_prices close", "purple"),
        ("Earnings 7d", _fmt_count(_earnings_next_7d(db_path)), "upcoming events", "amber"),
    ]


def _research_metric_cards(config: Mapping[str, Any], db_path: Path) -> list[tuple[str, str, str, str]]:
    candidates = _candidate_universe()
    long_candidates, short_candidates = _candidate_side_counts(candidates, config)
    universe_size = len(candidates) if not candidates.empty else _table_count(db_path, "universe")
    dispersion_factor, dispersion_detail = _highest_dispersion_factor(candidates)
    crowding = _crowding_warning_count(candidates, config)
    return [
        ("Universe size", _fmt_count(universe_size), "11 sectors" if universe_size else "scored universe rows", "blue"),
        ("Long candidates", _fmt_count(long_candidates), "top quintile per sector", "green"),
        ("Short candidates", _fmt_count(short_candidates), "bottom quintile per sector", "pink"),
        ("Highest-dispersion factor", dispersion_factor, dispersion_detail, "purple"),
        ("Crowding warnings", _fmt_count(crowding), "factor pairs flagged", "green" if crowding == 0 else "amber"),
    ]


def _latest_report(name_contains: str | None = None) -> Path | None:
    reports = sorted((PROJECT_ROOT / "output" / "reports").glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if name_contains:
        reports = [path for path in reports if name_contains.lower() in path.name.lower()]
    return reports[0] if reports else None


def _reporting_artifact_snapshot(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    output = Path(project_root) / "output"
    reports_dir = output / "reports"
    attribution_path = output / "daily_attribution.csv"
    attribution = pd.read_csv(attribution_path) if attribution_path.exists() else pd.DataFrame()
    reports = sorted(reports_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if reports_dir.exists() else []
    latest_alpha = 0.0
    latest_total = 0.0
    if not attribution.empty:
        latest = attribution.iloc[-1]
        latest_alpha = float(pd.to_numeric(pd.Series([latest.get("alpha_residual", 0.0)]), errors="coerce").fillna(0.0).iloc[0])
        latest_total = float(pd.to_numeric(pd.Series([latest.get("total_return", 0.0)]), errors="coerce").fillna(0.0).iloc[0])
    return {
        "daily_attribution_rows": int(len(attribution)),
        "latest_alpha_residual": latest_alpha,
        "latest_total_return": latest_total,
        "report_count": int(len(reports)),
        "latest_report": reports[0].name if reports else None,
        "daily_attribution_path": str(attribution_path),
    }


def _fmt_pct(value: float) -> str:
    return f"{value:.1%}"


def _fmt_money(value: Any) -> str:
    return f"${_to_float(value):,.0f}"


def _metric_card(label: str, value: str, detail: str, tone: str = "neutral") -> str:
    return f"""
    <div class="metric-card metric-{tone}">
        <div class="metric-label">{escape(label)}</div>
        <div class="metric-value">{escape(value)}</div>
        <div class="metric-detail">{escape(detail)}</div>
    </div>
    """


def _metric_card_rows(
    cards: list[tuple[str, str, str, str]] | tuple[tuple[str, str, str, str], ...],
    *,
    max_per_row: int,
) -> list[list[tuple[str, str, str, str]]]:
    row_size = max(1, int(max_per_row))
    return [list(cards[start : start + row_size]) for start in range(0, len(cards), row_size)]


def _render_metric_card_rows(
    st_module,
    cards: list[tuple[str, str, str, str]],
    *,
    max_per_row: int,
) -> None:
    for row in _metric_card_rows(cards, max_per_row=max_per_row):
        for col, card in zip(st_module.columns(len(row)), row, strict=True):
            with col:
                st_module.markdown(_metric_card(*card), unsafe_allow_html=True)


def _compact_dashboard_text(value: Any, *, max_chars: int = 80) -> str:
    text = " ".join(str(value or "").replace("_", " ").split())
    if not text:
        return ""
    text = text[:1].upper() + text[1:]
    if len(text) <= max_chars:
        return text
    clipped = text[: max(1, max_chars - 3)].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return f"{clipped.rstrip()}..."


def _autopilot_state(path: Path | None = None) -> dict[str, Any]:
    resolved = path or PROJECT_ROOT / "cache" / "autopilot_state.json"
    try:
        from autopilot.state import load_state

        if not resolved.exists():
            state = load_state(resolved)
            return {
                "available": True,
                "enabled": bool(state.get("enabled", True)),
                "mode": str(state.get("mode", "paper") or "paper"),
                "last_status": str(state.get("last_status", "never_run") or "never_run"),
                "current_step": state.get("current_step"),
                "last_run_id": state.get("last_run_id"),
                "last_finished_at": state.get("last_finished_at"),
                "last_error": state.get("last_error"),
                "paused": bool(state.get("paused", False)),
                "manual_detail": state.get("manual_detail"),
                "detail": "Autopilot has not run yet",
            }

        with resolved.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, Mapping):
            raise ValueError("Autopilot state payload is not an object")
        state = load_state(resolved)
    except Exception as exc:
        return {
            "available": False,
            "enabled": False,
            "mode": "paper",
            "last_status": "unavailable",
            "current_step": None,
            "last_run_id": None,
            "last_finished_at": None,
            "last_error": type(exc).__name__,
            "detail": f"Autopilot state unavailable: {type(exc).__name__}",
        }

    last_status = str(state.get("last_status", "never_run") or "never_run")
    last_error = state.get("last_error")
    current_step = state.get("current_step")
    manual_detail = state.get("manual_detail")
    detail = str(
        last_error
        or current_step
        or manual_detail
        or ("Autopilot has not run yet" if last_status == "never_run" else last_status)
    )
    return {
        "available": True,
        "enabled": bool(state.get("enabled", True)),
        "mode": str(state.get("mode", "paper") or "paper"),
        "last_status": last_status,
        "current_step": current_step,
        "last_run_id": state.get("last_run_id"),
        "last_finished_at": state.get("last_finished_at"),
        "last_error": last_error,
        "paused": bool(state.get("paused", False)),
        "manual_detail": manual_detail,
        "detail": detail,
    }


def _apply_autopilot_action(action: str, path: Path | None = None) -> str:
    normalized = str(action or "").strip().lower()
    if normalized not in {"on", "off", "paused"}:
        return "Autopilot action ignored."

    from autopilot.state import load_state, save_state

    state = load_state(path)
    state["mode"] = "paper"
    state["current_step"] = None

    if normalized == "on":
        previous_status = str(state.get("last_status", "") or "").lower()
        state["enabled"] = True
        state["paused"] = False
        if previous_status in {"manually_paused", "operator_paused"}:
            state["last_status"] = "never_run"
        state["manual_detail"] = "Autopilot enabled from dashboard"
        notice = "Autopilot ON applied."
    elif normalized == "off":
        state["enabled"] = False
        state["paused"] = False
        state["last_status"] = "operator_disabled"
        state["manual_detail"] = "Autopilot turned off from dashboard"
        notice = "Autopilot OFF applied."
    else:
        state["enabled"] = False
        state["paused"] = True
        state["last_status"] = "operator_paused"
        state["manual_detail"] = "Autopilot paused from dashboard"
        notice = "Autopilot PAUSED applied."

    save_state(state, path)
    return notice


def _query_param_value(params: Any, name: str) -> str | None:
    try:
        value = params.get(name)
    except AttributeError:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    return str(value)


def _auto_refresh_interval_seconds(config: Mapping[str, Any]) -> int:
    dashboard = config.get("dashboard", {}) if isinstance(config.get("dashboard"), Mapping) else {}
    raw_interval = dashboard.get("auto_refresh_seconds_market_hours", 60)
    try:
        interval = int(raw_interval)
    except (TypeError, ValueError):
        interval = 60
    return max(10, min(300, interval))


def _inject_auto_refresh(st_module, page: str, config: Mapping[str, Any]) -> None:
    params = getattr(st_module, "query_params", {})
    refresh = (_query_param_value(params, "refresh") or "").strip().lower()
    _ = page, config, refresh
    st_module.markdown(
        '<div class="auto-refresh-row"><span class="auto-refresh-badge off">Manual refresh</span></div>',
        unsafe_allow_html=True,
    )


def _handle_dashboard_actions(st_module, config: Mapping[str, Any], *, state_path: Path | None = None) -> str | None:
    notices: list[str] = []
    params = getattr(st_module, "query_params", {})
    autopilot_action = (_query_param_value(params, "autopilot") or "").strip().lower()
    if autopilot_action in {"on", "off", "paused"}:
        notices.append(_apply_autopilot_action(autopilot_action, state_path))

    mode_action = (_query_param_value(params, "mode") or "").strip().lower()
    execution = config.get("execution", {}) if isinstance(config.get("execution"), Mapping) else {}
    if mode_action == "paper":
        notices.append("Paper mode selected.")
    elif mode_action == "live" and not bool(execution.get("allow_live_trading", False)):
        notices.append("Live mode is locked by config; paper mode remains protected.")

    if not notices:
        return None
    return " ".join(notices)


def _autopilot_tone(autopilot_state: Mapping[str, Any]) -> str:
    status = str(autopilot_state.get("last_status", "never_run") or "never_run").lower()
    has_error = bool(autopilot_state.get("last_error"))
    if bool(autopilot_state.get("paused", False)) or status in AUTOPILOT_PAUSED_STATUSES:
        return "amber"
    if not autopilot_state.get("available", True) or not autopilot_state.get("enabled", True):
        return "pink"
    if status in AUTOPILOT_FAILURE_STATUSES:
        return "pink"
    if status in AUTOPILOT_NON_ERROR_STATUSES:
        return "amber"
    if has_error:
        return "pink"
    if status in AUTOPILOT_WAITING_STATUSES:
        return "amber"
    return "green"


def _autopilot_metric_cards(autopilot_state: Mapping[str, Any]) -> list[tuple[str, str, str, str]]:
    tone = _autopilot_tone(autopilot_state)
    enabled = bool(autopilot_state.get("enabled", True)) and bool(autopilot_state.get("available", True))
    status = str(autopilot_state.get("last_status", "never_run") or "never_run")
    status_key = status.lower()
    current_step = autopilot_state.get("current_step")
    last_run_id = autopilot_state.get("last_run_id")
    last_finished_at = autopilot_state.get("last_finished_at")
    last_error = autopilot_state.get("last_error")
    status_label = "Pending orders" if status_key == "open_orders_pending" else _compact_dashboard_text(status)
    detail_label = _compact_dashboard_text(autopilot_state.get("detail", status), max_chars=96)
    current_step_label = _compact_dashboard_text(current_step) if current_step else "Idle"
    show_error = bool(last_error) and status_key not in AUTOPILOT_NON_ERROR_STATUSES
    error_label = _compact_dashboard_text(last_error, max_chars=80) if show_error else "None"
    return [
        (
            "Paper Autopilot",
            "ON" if enabled else "OFF",
            "Paper autonomous execution" if enabled else detail_label or "Autopilot unavailable",
            tone,
        ),
        ("Last status", status_label, detail_label or status_label, tone),
        (
            "Current step",
            current_step_label,
            "Active autopilot step" if current_step else "No active autopilot step",
            tone,
        ),
        (
            "Last run",
            str(last_run_id) if last_run_id else "None",
            f"Finished {last_finished_at}" if last_finished_at else "No finish timestamp",
            tone,
        ),
        (
            "Error",
            error_label,
            "Last autopilot error" if show_error else "No autopilot error",
            "pink" if show_error else "green",
        ),
    ]


def _section_header(title: str, subtitle: str | None = None) -> str:
    subtitle_html = f"<span>{escape(subtitle)}</span>" if subtitle else ""
    return f"""
    <div class="section-head">
        <h3>{escape(title)}</h3>
        {subtitle_html}
    </div>
    """


def _empty_panel(title: str, body: str) -> str:
    return f"""
    <div class="terminal-panel">
        <pre>{escape(title)}

{escape(body)}</pre>
    </div>
    """


def _render_table(st_module, frame: pd.DataFrame, *, empty_title: str, empty_body: str, max_rows: int = 12) -> None:
    if frame.empty:
        st_module.markdown(_empty_panel(empty_title, empty_body), unsafe_allow_html=True)
        return
    display = frame.head(max_rows).copy()
    rows = []
    for row in display.astype(object).where(pd.notna(display), "").to_dict(orient="records"):
        cells = "".join(
            f'<td><span class="cell-text" title="{escape(str(value))}">{escape(str(value))}</span></td>'
            for value in row.values()
        )
        rows.append(f"<tr>{cells}</tr>")
    headers = "".join(
        f'<th><span class="cell-text" title="{escape(str(column))}">{escape(str(column))}</span></th>'
        for column in display.columns
    )
    st_module.markdown(
        f'<div class="dark-table-wrap"><table class="dark-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def _records(frame: pd.DataFrame, *, max_rows: int = SNAPSHOT_MAX_ROWS) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    display = frame.head(max_rows).copy()
    display = display.astype(object).where(pd.notna(display), None)
    return display.to_dict(orient="records")


def _paper_frame(snapshot: Mapping[str, Any] | None, key: str) -> pd.DataFrame:
    value = snapshot.get(key) if isinstance(snapshot, Mapping) else None
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _paper_account_summary(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not _paper_snapshot_available(snapshot):
        return {}
    account = snapshot.get("account") if isinstance(snapshot, Mapping) else None
    if not isinstance(account, Mapping):
        return {}
    numeric_keys = [
        "portfolio_value",
        "equity",
        "last_equity",
        "cash",
        "buying_power",
        "long_market_value",
        "short_market_value",
        "daytrade_count",
    ]
    boolean_keys = ["pattern_day_trader", "trading_blocked", "account_blocked"]
    summary: dict[str, Any] = {}
    for key in numeric_keys:
        if key in account:
            summary[key] = _to_float(account.get(key))
    for key in boolean_keys:
        if key in account:
            summary[key] = bool(account.get(key))
    return summary


def _paper_open_orders(orders: pd.DataFrame) -> pd.DataFrame:
    if orders.empty or "status" not in orders.columns:
        return pd.DataFrame(columns=orders.columns)
    terminal_statuses = {"filled", "canceled", "cancelled", "expired", "rejected"}
    statuses = orders["status"].fillna("").astype(str).str.lower()
    return orders.loc[~statuses.isin(terminal_statuses)].copy()


def _paper_round_trips(activities: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker", "side", "quantity", "price", "date"}
    if activities.empty or not required.issubset(set(activities.columns)):
        return fifo_round_trips(pd.DataFrame(columns=sorted(required)))
    return fifo_round_trips(activities)


def _paper_trade_metrics(orders: pd.DataFrame, open_orders: pd.DataFrame, activities: pd.DataFrame, round_trips: pd.DataFrame) -> dict[str, Any]:
    realized = pd.to_numeric(round_trips.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    wins = int((realized > 0).sum())
    losses = int((realized < 0).sum())
    round_trip_count = int(len(round_trips))
    return {
        "order_count": int(len(orders)),
        "open_order_count": int(len(open_orders)),
        "fill_count": int(len(activities)),
        "round_trip_count": round_trip_count,
        "total_realized_pnl": round(float(realized.sum()), 6),
        "winning_round_trips": wins,
        "losing_round_trips": losses,
        "win_rate": round(float(wins / round_trip_count), 6) if round_trip_count else 0.0,
    }


def _recent_execution_orders(
    path: Path | None = None,
    *,
    max_rows: int = SNAPSHOT_MAX_ROWS,
) -> list[dict[str, Any]]:
    resolved = path or PROJECT_ROOT / "output" / "execution_orders.jsonl"
    if not resolved.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = resolved.read_text(encoding="utf-8").splitlines()[-max_rows:]
    except OSError:
        return []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            rows.append(dict(payload))
    return rows


def _candidate_reviews(db_path: Path) -> pd.DataFrame:
    try:
        return PortfolioState(db_path).get_candidate_reviews()
    except sqlite3.Error:
        return pd.DataFrame()


def _candidate_suggested_side(row: Mapping[str, Any], score_col: str | None = None) -> str:
    for key in ("side", "candidate_side", "recommendation"):
        value = row.get(key)
        if value is None or pd.isna(value):
            continue
        text = str(value).strip().lower()
        if "long" in text:
            return "long"
        if "short" in text:
            return "short"
    if _truthy(row.get("long_candidate")):
        return "long"
    if _truthy(row.get("short_candidate")):
        return "short"
    if score_col and row.get(score_col) is not None:
        score = pd.to_numeric(pd.Series([row.get(score_col)]), errors="coerce").iloc[0]
        if pd.notna(score):
            return "long" if float(score) >= 50.0 else "short"
    return "watch"


def _candidate_review_frame(candidates: pd.DataFrame, reviews: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty or "ticker" not in candidates.columns:
        return pd.DataFrame()
    score_col = _score_column(candidates)
    frame = candidates.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
    frame = frame.drop_duplicates("ticker", keep="first")
    frame["suggested_side"] = [
        _candidate_suggested_side(row, score_col=score_col)
        for row in frame.to_dict(orient="records")
    ]
    if score_col:
        frame["display_score"] = pd.to_numeric(frame[score_col], errors="coerce")
    else:
        frame["display_score"] = pd.NA

    if reviews.empty or "ticker" not in reviews.columns:
        frame["review_status"] = "pending"
        frame["review_reason"] = ""
        frame["review_decided_at"] = ""
    else:
        latest = reviews.copy()
        latest["ticker"] = latest["ticker"].astype(str).str.strip().str.upper()
        latest["status"] = latest.get("status", pd.Series(dtype=str)).astype(str).str.strip().str.lower()
        latest = latest.drop_duplicates("ticker", keep="first").set_index("ticker")
        frame["review_status"] = frame["ticker"].map(latest["status"]).fillna("pending")
        if "reason" in latest.columns:
            frame["review_reason"] = frame["ticker"].map(latest["reason"]).fillna("")
        else:
            frame["review_reason"] = ""
        if "decided_at" in latest.columns:
            frame["review_decided_at"] = frame["ticker"].map(latest["decided_at"]).fillna("")
        else:
            frame["review_decided_at"] = ""

    status_order = {"pending": 0, "approved": 1, "watch": 2, "rejected": 3}
    frame["_status_order"] = frame["review_status"].map(status_order).fillna(9)
    frame = frame.sort_values(["_status_order", "suggested_side", "display_score"], ascending=[True, True, False])
    return frame.drop(columns=["_status_order"])


def _candidate_review_counts(review_frame: pd.DataFrame) -> dict[str, int]:
    if review_frame.empty or "review_status" not in review_frame.columns:
        return {"pending": 0, "approved": 0, "watch": 0, "rejected": 0}
    statuses = review_frame["review_status"].astype(str).str.lower()
    return {status: int((statuses == status).sum()) for status in ["pending", "approved", "watch", "rejected"]}


def _candidate_board_groups(review_frame: pd.DataFrame, *, limit: int = 10) -> dict[str, pd.DataFrame]:
    empty = pd.DataFrame()
    if review_frame.empty or "suggested_side" not in review_frame.columns:
        return {"long": empty, "short": empty}

    frame = review_frame.copy()
    if "display_score" in frame.columns:
        frame["_candidate_score"] = pd.to_numeric(frame["display_score"], errors="coerce")
    else:
        score_col = _score_column(frame)
        frame["_candidate_score"] = pd.to_numeric(frame[score_col], errors="coerce") if score_col else pd.NA
    if "ticker" not in frame.columns:
        frame["ticker"] = ""

    side = frame["suggested_side"].astype(str).str.lower()
    long_frame = (
        frame.loc[side == "long"]
        .sort_values(["_candidate_score", "ticker"], ascending=[False, True], na_position="last")
        .head(max(1, int(limit)))
        .drop(columns=["_candidate_score"])
    )
    short_frame = (
        frame.loc[side == "short"]
        .sort_values(["_candidate_score", "ticker"], ascending=[True, True], na_position="last")
        .head(max(1, int(limit)))
        .drop(columns=["_candidate_score"])
    )
    return {"long": long_frame, "short": short_frame}


def _candidate_is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if missing is pd.NA:
        return True
    try:
        return bool(missing)
    except (TypeError, ValueError):
        return False


def _candidate_first_value(candidate: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = candidate.get(key)
        if _candidate_is_missing(value):
            continue
        if str(value).strip() == "":
            continue
        return value
    return None


def _candidate_score_text(candidate: Mapping[str, Any]) -> str:
    value = _candidate_first_value(candidate, ("display_score", "combined_score", "composite_score", "score"))
    if value is None:
        return "N/A"
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return str(value)
    number = float(numeric)
    return str(int(round(number))) if number.is_integer() else f"{number:.1f}"


def _candidate_sector_text(candidate: Mapping[str, Any]) -> str:
    value = _candidate_first_value(candidate, ("gics_sector", "sector", "industry"))
    return _compact_dashboard_text(value or "Unknown", max_chars=28)


def _candidate_pct_text(value: Any) -> str:
    number = _to_float(value)
    pct = number * 100.0 if abs(number) <= 1.0 else number
    return f"{pct:.1f}%"


def _candidate_meta_line(candidate: Mapping[str, Any]) -> str:
    metrics: list[str] = []
    shares = _candidate_first_value(candidate, ("target_shares", "shares", "quantity", "qty"))
    if shares is not None and abs(_to_float(shares)) > 0:
        metrics.append(f"{_to_float(shares):.0f} sh")

    value = _candidate_first_value(candidate, ("target_value", "market_value", "notional", "price", "close"))
    if value is not None:
        metrics.append(_fmt_money(value))

    weight = _candidate_first_value(candidate, ("target_weight", "weight", "expected_weight"))
    if weight is not None:
        metrics.append(_candidate_pct_text(weight))

    beta = _candidate_first_value(candidate, ("beta", "portfolio_beta", "market_beta"))
    if beta is not None:
        metrics.append(f"beta {_to_float(beta):.2f}")

    if not metrics:
        metrics.append(f"score {_candidate_score_text(candidate)}")
    return " &middot; ".join(escape(metric) for metric in metrics)


def _candidate_quality_line(candidate: Mapping[str, Any]) -> str:
    metrics: list[str] = []
    piotroski = _candidate_first_value(candidate, ("quality_piotroski", "piotroski", "piotroski_score"))
    if piotroski is not None:
        metrics.append(f"Piotroski {int(round(_to_float(piotroski)))}/9")

    altman = _candidate_first_value(candidate, ("quality_altman_z", "altman_z", "altman_z_score"))
    if altman is not None:
        altman_number = _to_float(altman)
        metrics.append(f"Altman-Z {altman_number:.1f}")
        if altman_number < 1.8:
            metrics.append("distress")
        elif altman_number < 3.0:
            metrics.append("grey zone")
        else:
            metrics.append("safe")

    if not metrics:
        status = _compact_dashboard_text(candidate.get("review_status", "pending"), max_chars=24)
        return f'<span class="muted">Status</span> {escape(status)}'
    first, *rest = metrics
    rest_html = f" - {escape(' - '.join(rest))}" if rest else ""
    return f'<span class="muted">{escape(first)}</span>{rest_html}'


def _candidate_card_html(candidate: Mapping[str, Any], *, side: str) -> str:
    normalized_side = "short" if str(side).lower() == "short" else "long"
    raw_status = str(candidate.get("review_status", "pending") or "pending").strip().lower()
    status = raw_status if raw_status in {"pending", "approved", "watch", "rejected"} else "pending"
    ticker = _compact_dashboard_text(candidate.get("ticker", "N/A"), max_chars=12).upper()
    sector = _candidate_sector_text(candidate)
    score = _candidate_score_text(candidate)
    meta = _candidate_meta_line(candidate)
    quality = _candidate_quality_line(candidate)
    return f"""
    <div class="candidate-card candidate-card-{normalized_side} status-{status}">
        <div class="candidate-card-top">
            <div class="candidate-identity">
                <span class="candidate-ticker">{escape(ticker)}</span>
                <span class="candidate-sector">{escape(sector)}</span>
            </div>
            <span class="candidate-score">{escape(score)}</span>
        </div>
        <div class="candidate-meta">{meta}</div>
        <div class="candidate-health">{quality}</div>
    </div>
    """


def _candidate_analysis_html(candidate: Mapping[str, Any]) -> str:
    summary = _candidate_first_value(
        candidate,
        ("analysis_summaries", "analysis_summary", "qualitative_summary", "summary"),
    )
    if summary is None:
        summary = "No Codex analysis is available yet. Run the analysis layer for this ticker."
    summary_text = " ".join(str(summary).split())
    health = _candidate_quality_line(candidate)
    return f"""
    <div class="candidate-analysis-copy">
        <h4>Forensic financial - health {_candidate_score_text(candidate)}/100</h4>
        <p>{escape(summary_text)}</p>
        <p>{health}</p>
    </div>
    """


def _candidate_has_cached_analysis(candidate: Mapping[str, Any]) -> bool:
    return _candidate_first_value(
        candidate,
        ("analysis_summaries", "analysis_summary", "qualitative_summary", "summary"),
    ) is not None


def _candidate_analysis_expander_label(candidate: Mapping[str, Any]) -> str:
    normalized_ticker = str(candidate.get("ticker") or "N/A").strip().upper() or "N/A"
    action = "Codex analysis" if _candidate_has_cached_analysis(candidate) else "no Codex analysis yet"
    return f"{normalized_ticker} - {action}"


def _candidate_analysis_button_label(candidate: Mapping[str, Any]) -> str:
    return "Regenerate Codex analysis" if _candidate_has_cached_analysis(candidate) else "Generate Codex analysis"


def _candidate_generation_question(candidate: Mapping[str, Any]) -> str:
    ticker = str(candidate.get("ticker") or "this ticker").strip().upper() or "this ticker"
    return (
        f"Generate a concise forensic financial analysis for {ticker}. "
        "Use only the candidate snapshot. Cover earnings quality, balance-sheet risk, cash-flow quality, "
        "key long/short drivers, and an approve/watch/reject lean for this paper-trading research workflow. "
        "Do not invent live market data."
    )


def _persist_generated_candidate_analysis(
    candidate: Mapping[str, Any],
    analysis_text: str,
    *,
    output_path: Path | None = None,
) -> Path:
    ticker = str(candidate.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("Cannot persist Codex analysis without a ticker.")

    path = output_path or PROJECT_ROOT / "output" / "analysis_results_latest.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        frame = pd.read_csv(path)
    else:
        frame = pd.DataFrame()

    row_update: dict[str, Any] = {
        "ticker": ticker,
        "analysis_summaries": " ".join(str(analysis_text).split()),
        "analyzers_run": "codex",
        "qualitative_used": True,
        "updated_at": pd.Timestamp.now(tz="UTC").tz_localize(None).isoformat(sep=" ", timespec="seconds"),
    }

    if frame.empty:
        row = {str(key): value for key, value in dict(candidate).items()}
        row.update(row_update)
        row["analysis_count"] = 1
        frame = pd.DataFrame([row])
    else:
        if "ticker" not in frame.columns:
            frame["ticker"] = ""
        ticker_series = frame["ticker"].astype(str).str.strip().str.upper()
        mask = ticker_series == ticker
        for column in row_update:
            if column not in frame.columns:
                frame[column] = pd.NA
        if "analysis_count" not in frame.columns:
            frame["analysis_count"] = pd.NA
        if mask.any():
            prior_count = pd.to_numeric(frame.loc[mask, "analysis_count"], errors="coerce").max()
            next_count = 1 if pd.isna(prior_count) else int(prior_count) + 1
            for column, value in row_update.items():
                frame.loc[mask, column] = value
            frame.loc[mask, "analysis_count"] = next_count
        else:
            row = {column: pd.NA for column in frame.columns}
            row.update({str(key): value for key, value in dict(candidate).items() if str(key) in row})
            row.update(row_update)
            row["analysis_count"] = 1
            frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)

    frame.to_csv(path, index=False)
    return path


def _generate_candidate_analysis(
    config: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    output_path: Path | None = None,
) -> str:
    answer = _ask_candidate_agent(config, candidate, _candidate_generation_question(candidate)).strip()
    if not answer:
        raise RuntimeError("Codex returned an empty analysis.")
    if answer.startswith("JARVIS is unavailable:"):
        raise RuntimeError(answer)
    _persist_generated_candidate_analysis(candidate, answer, output_path=output_path)
    return answer


def _candidate_analysis_prompt(candidate: Mapping[str, Any], question: str) -> str:
    candidate_json = json.dumps(dict(candidate), indent=2, sort_keys=True, default=str)
    return f"""You are JARVIS, the Codex analyst inside the Research Phase 2 candidate review screen.
Use only the selected candidate snapshot below. Do not invent live market data.
Approve, reject, or watch is a research workflow decision for paper trading; this is not financial advice.

SELECTED_CANDIDATE:
{candidate_json}

USER_QUESTION:
{question}
"""


def _ask_candidate_agent(config: Mapping[str, Any], candidate: Mapping[str, Any], question: str) -> str:
    from analysis.api_client import APIClientError, create_analysis_client

    prompt = _candidate_analysis_prompt(candidate, question)
    try:
        client = create_analysis_client(config)
        return client.complete(prompt)
    except (APIClientError, FileNotFoundError, TimeoutError, subprocess.SubprocessError) as exc:
        return f"JARVIS is unavailable: {exc}"


def _candidate_detail_html(candidate: Mapping[str, Any]) -> str:
    fields = [
        ("ticker", "Ticker"),
        ("suggested_side", "Side"),
        ("display_score", "Score"),
        ("gics_sector", "Sector"),
        ("sector", "Sector"),
        ("industry", "Industry"),
        ("review_status", "Status"),
        ("review_reason", "Reason"),
    ]
    rows = []
    seen: set[str] = set()
    for key, label in fields:
        if label in seen:
            continue
        value = candidate.get(key)
        if value is None or (isinstance(value, float) and pd.isna(value)) or str(value) == "<NA>":
            continue
        seen.add(label)
        rows.append(
            f'<div><span>{escape(label)}</span><strong>{escape(_compact_dashboard_text(value, max_chars=80))}</strong></div>'
        )
    return f'<div class="candidate-detail">{"".join(rows)}</div>'


def _candidate_agent_history_html(history: list[dict[str, str]]) -> str:
    if not history:
        return ""
    return _portfolio_chat_history_html(history[-6:])


def _render_candidate_review_panel(
    st_module,
    config: Mapping[str, Any],
    db_path: Path,
    candidates: pd.DataFrame,
) -> None:
    reviews = _candidate_reviews(db_path)
    review_frame = _candidate_review_frame(candidates, reviews)
    with st_module.container(border=True):
        st_module.markdown(_section_header("Phase 2 candidate review", "Approve / Reject / Codex"), unsafe_allow_html=True)
        if review_frame.empty:
            st_module.markdown(
                _empty_panel(
                    "No candidates",
                    "Run scoring/research first so Phase 2 can review candidates.",
                ),
                unsafe_allow_html=True,
            )
            return

        counts = _candidate_review_counts(review_frame)
        count_cols = st_module.columns(4)
        count_cards = [
            ("Pending", counts["pending"], "Review queue", "blue"),
            ("Approved", counts["approved"], "Eligible for portfolio", "green"),
            ("Watch", counts["watch"], "Held out of execution", "amber"),
            ("Rejected", counts["rejected"], "Blocked from portfolio", "pink"),
        ]
        for col, card in zip(count_cols, count_cards, strict=True):
            with col:
                st_module.markdown(_metric_card(card[0], str(card[1]), card[2], card[3]), unsafe_allow_html=True)

        filter_left, filter_right = st_module.columns([1, 1], gap="large")
        with filter_left:
            status_filter = st_module.selectbox(
                "Review status",
                ["pending", "approved", "watch", "rejected", "all"],
                index=0,
                key="candidate_review_status_filter",
            )
        with filter_right:
            side_filter = st_module.selectbox(
                "Candidate side",
                ["all", "long", "short", "watch"],
                index=0,
                key="candidate_review_side_filter",
            )

        filtered = review_frame.copy()
        if status_filter != "all":
            filtered = filtered.loc[filtered["review_status"].astype(str).str.lower() == status_filter]
        if side_filter != "all":
            filtered = filtered.loc[filtered["suggested_side"].astype(str).str.lower() == side_filter]
        if filtered.empty:
            st_module.markdown(_empty_panel("No matching candidates", "Change the Phase 2 filters."), unsafe_allow_html=True)
            return

        options = filtered["ticker"].tolist()
        selected_ticker = st_module.selectbox("Candidate", options, key="candidate_review_selected_ticker")
        selected = filtered.loc[filtered["ticker"] == selected_ticker].iloc[0].to_dict()
        st_module.markdown(_candidate_detail_html(selected), unsafe_allow_html=True)

        note_key = f"candidate_review_note_{selected_ticker}"
        note = st_module.text_area(
            "Decision note",
            value=str(selected.get("review_reason", "") or ""),
            key=note_key,
            height=80,
        )

        action_cols = st_module.columns(3)
        actions = [("Approve", "approved"), ("Watch", "watch"), ("Reject", "rejected")]
        for col, (label, status) in zip(action_cols, actions, strict=True):
            with col:
                if st_module.button(label, key=f"candidate_review_{status}_{selected_ticker}", use_container_width=True):
                    PortfolioState(db_path).record_candidate_review(
                        selected_ticker,
                        status,
                        side=str(selected.get("suggested_side", "")),
                        reason=note.strip() or None,
                        payload=selected,
                    )
                    st_module.success(f"{selected_ticker} marked {status}.")
                    if hasattr(st_module, "rerun"):
                        st_module.rerun()

        visible_cols = [
            column
            for column in ["ticker", "suggested_side", "display_score", "review_status", "review_reason"]
            if column in filtered.columns
        ]
        _render_table(
            st_module,
            filtered[visible_cols],
            empty_title="No review queue",
            empty_body="No candidates match the active filters.",
            max_rows=12,
        )

        history_key = f"candidate_agent_history_{selected_ticker}"
        if history_key not in st_module.session_state:
            st_module.session_state[history_key] = []
        history = list(st_module.session_state[history_key])
        with st_module.form(f"candidate_agent_form_{selected_ticker}", clear_on_submit=True):
            question = st_module.text_input(
                "Ask Codex",
                placeholder=f"Ask Codex to analyze {selected_ticker}...",
                label_visibility="collapsed",
            )
            submitted = st_module.form_submit_button("Ask Codex")
        if submitted:
            request = question.strip() or f"Analyze {selected_ticker} and say whether this should be approved, rejected, or watched."
            history.append({"role": "user", "content": request})
            with st_module.spinner(f"Codex is reviewing {selected_ticker}..."):
                answer = _ask_candidate_agent(config, selected, request)
            history.append({"role": "assistant", "content": answer})
            st_module.session_state[history_key] = _trim_chat_history(history)
        st_module.markdown(_candidate_agent_history_html(list(st_module.session_state[history_key])), unsafe_allow_html=True)


def _record_candidate_board_decision(
    st_module,
    db_path: Path,
    candidate: Mapping[str, Any],
    *,
    side: str,
    status: str,
) -> None:
    ticker = str(candidate.get("ticker", "")).strip().upper()
    if not ticker:
        return
    state = PortfolioState(db_path)
    if status == "reset":
        state.clear_candidate_review(ticker)
        st_module.success(f"{ticker} reset to pending.")
    else:
        state.record_candidate_review(
            ticker,
            status,
            side=side,
            reason=f"{status} from Top 10 {side} board",
            payload=dict(candidate),
        )
        st_module.success(f"{ticker} marked {status}.")
    if hasattr(st_module, "rerun"):
        st_module.rerun()


def _render_candidate_board_actions(
    st_module,
    db_path: Path,
    candidate: Mapping[str, Any],
    *,
    side: str,
) -> None:
    ticker = str(candidate.get("ticker", "")).strip().upper()
    action_cols = st_module.columns(3)
    actions = [("Approve", "approved", "primary"), ("Reject", "rejected", "secondary"), ("Reset", "reset", "secondary")]
    for col, (label, status, button_type) in zip(action_cols, actions, strict=True):
        with col:
            if st_module.button(
                label,
                key=f"candidate_board_{side}_{status}_{ticker}",
                use_container_width=True,
                type=button_type,
            ):
                _record_candidate_board_decision(st_module, db_path, candidate, side=side, status=status)


def _render_candidate_analysis_button(
    st_module,
    config: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    side: str,
) -> None:
    ticker = str(candidate.get("ticker", "")).strip().upper()
    if not ticker:
        return
    label = _candidate_analysis_button_label(candidate)
    if st_module.button(
        label,
        key=f"candidate_board_{side}_generate_analysis_{ticker}",
        use_container_width=True,
        type="secondary",
    ):
        with st_module.spinner(f"Codex is analyzing {ticker}..."):
            try:
                _generate_candidate_analysis(config, candidate)
            except Exception as exc:
                st_module.error(str(exc))
                return
        st_module.success(f"{ticker} Codex analysis generated.")
        if hasattr(st_module, "rerun"):
            st_module.rerun()


def _render_candidate_board_column(
    st_module,
    config: Mapping[str, Any],
    db_path: Path,
    frame: pd.DataFrame,
    *,
    side: str,
) -> None:
    title = "Top 10 long candidates" if side == "long" else "Top 10 short candidates"
    st_module.markdown(f'<div class="candidate-board-title">{escape(title)}</div>', unsafe_allow_html=True)
    if frame.empty:
        st_module.markdown(
            _empty_panel(
                "No candidates",
                "Run scoring and analysis to populate this side.",
            ),
            unsafe_allow_html=True,
        )
        return

    for position, candidate in enumerate(frame.to_dict(orient="records")):
        ticker = str(candidate.get("ticker", "N/A")).strip().upper() or "N/A"
        st_module.markdown(_candidate_card_html(candidate, side=side), unsafe_allow_html=True)
        _render_candidate_board_actions(st_module, db_path, candidate, side=side)
        _render_candidate_analysis_button(st_module, config, candidate, side=side)
        expanded = position == 0 and side == "long"
        label = _candidate_analysis_expander_label(candidate)
        with st_module.expander(label, expanded=expanded):
            st_module.markdown(_candidate_analysis_html(candidate), unsafe_allow_html=True)


def _render_candidate_recommendation_board(
    st_module,
    config: Mapping[str, Any],
    db_path: Path,
    candidates: pd.DataFrame,
) -> None:
    reviews = _candidate_reviews(db_path)
    review_frame = _candidate_review_frame(candidates, reviews)
    with st_module.container(border=True):
        groups = _candidate_board_groups(review_frame)
        left, right = st_module.columns([1, 1], gap="large")
        with left:
            _render_candidate_board_column(st_module, config, db_path, groups["long"], side="long")
        with right:
            _render_candidate_board_column(st_module, config, db_path, groups["short"], side="short")


def _portfolio_snapshot(
    config: Mapping[str, Any],
    db_path: Path,
    *,
    paper_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    positions, positions_source, _using_paper_positions = _dashboard_positions(config, db_path, paper_snapshot)
    candidates = _candidate_universe()
    approvals = _read_table(db_path, "portfolio_approvals", limit=SNAPSHOT_MAX_ROWS)
    history = _read_table(db_path, "portfolio_history", limit=SNAPSHOT_MAX_ROWS)
    weights = pd.to_numeric(positions.get("weight", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    pending = int((approvals.get("status", pd.Series(dtype=str)).astype(str) == "pending").sum()) if not approvals.empty else 0
    score_col = _score_column(candidates)
    long_candidates, short_candidates = _candidate_side_counts(candidates, config)
    ranked_candidates = candidates.copy()
    if score_col:
        ranked_candidates[score_col] = pd.to_numeric(ranked_candidates[score_col], errors="coerce")
        ranked_candidates = ranked_candidates.sort_values(score_col, ascending=False)
    paper_orders = _paper_frame(paper_snapshot, "orders")
    paper_fills = _paper_frame(paper_snapshot, "activities")
    paper_open_orders = _paper_open_orders(paper_orders)
    paper_round_trips = _paper_round_trips(paper_fills)
    paper_trade_metrics = _paper_trade_metrics(paper_orders, paper_open_orders, paper_fills, paper_round_trips)
    reports_dir = PROJECT_ROOT / "output" / "reports"
    reports = sorted(reports_dir.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)[:6] if reports_dir.exists() else []

    return {
        "as_of": date.today().isoformat(),
        "database": str(db_path.relative_to(PROJECT_ROOT)) if db_path.is_relative_to(PROJECT_ROOT) else str(db_path),
        "positions_source": positions_source,
        "metrics": {
            "positions": int(len(positions)),
            "long_positions": int((weights > 0).sum()),
            "short_positions": int((weights < 0).sum()),
            "gross_exposure": round(float(weights.abs().sum()) if not weights.empty else 0.0, 6),
            "net_exposure": round(float(weights.sum()) if not weights.empty else 0.0, 6),
            "candidates": int(len(candidates)),
            "long_candidates": long_candidates,
            "short_candidates": short_candidates,
            "crowding_warnings": _crowding_warning_count(candidates, config),
            "insider_events": _table_count(db_path, "insider_transactions"),
            "ceo_cfo_buys": _sql_count(
                db_path,
                "insider_transactions",
                "is_ceo_cfo = 1 AND is_open_market_purchase = 1",
            ),
            "cluster_buys": _sql_count(db_path, "insider_transactions", "cluster_buy = 1"),
            "vix": _latest_vix(db_path),
            "earnings_next_7d": _earnings_next_7d(db_path),
            "pending_approvals": pending,
            "paper_open_orders": paper_trade_metrics["open_order_count"],
            "paper_fills": paper_trade_metrics["fill_count"],
            "paper_round_trips": paper_trade_metrics["round_trip_count"],
            "paper_realized_pnl": paper_trade_metrics["total_realized_pnl"],
        },
        "positions": _records(positions),
        "paper_account": _paper_account_summary(paper_snapshot),
        "paper_orders": _records(paper_orders),
        "paper_open_orders": _records(paper_open_orders),
        "paper_fills": _records(paper_fills),
        "paper_round_trips": _records(paper_round_trips),
        "paper_closed_positions": _records(paper_round_trips),
        "paper_trade_metrics": paper_trade_metrics,
        "autopilot": _autopilot_state(),
        "local_execution_orders": _recent_execution_orders(),
        "top_candidates": _records(ranked_candidates),
        "bottom_candidates": _records(ranked_candidates.sort_values(score_col, ascending=True)) if score_col else [],
        "approvals": _records(approvals),
        "portfolio_history": _records(history),
        "table_counts": {table: _table_count(db_path, table) for table in DATA_TABLES if _table_exists(db_path, table)},
        "risk_config": config.get("risk", {}) if isinstance(config.get("risk"), Mapping) else {},
        "execution_config": config.get("execution", {}) if isinstance(config.get("execution"), Mapping) else {},
        "reports": [{"file": path.name, "bytes": path.stat().st_size} for path in reports],
    }


def _trim_chat_history(history: list[dict[str, str]], *, max_turns: int = MAX_CHAT_TURNS) -> list[dict[str, str]]:
    return history[-max_turns * 2 :]


def _portfolio_chat_prompt(question: str, snapshot: Mapping[str, Any], history: list[dict[str, str]]) -> str:
    snapshot_json = json.dumps(snapshot, indent=2, sort_keys=True, default=str)
    history_json = json.dumps(_trim_chat_history(history), indent=2, default=str)
    return f"""You are JARVIS, the portfolio analyst for this local-first long/short equity dashboard.
Answer the user's portfolio question using the JSON snapshot and recent chat history below.
If the snapshot does not contain enough data, say exactly what is missing and do not invent live market data.
Keep the answer concise, concrete, and in the same language as the user. This is research and paper-trading context, not financial advice.

RECENT_CHAT:
{history_json}

SYSTEM_SNAPSHOT:
{snapshot_json}

USER_QUESTION:
{question}
"""


def _portfolio_chat_history_html(history: list[dict[str, str]]) -> str:
    responses: list[str] = []
    current_question: str | None = None
    for message in _trim_chat_history(history):
        role = message.get("role")
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if role == "user":
            current_question = content
            continue
        if role != "assistant":
            continue
        question = (current_question or "JARVIS").upper()
        answer = escape(content).replace("\n", "<br>")
        responses.append(
            f"""
            <div class="jarvis-response">
                <div class="jarvis-question">> {escape(question)}</div>
                <div class="jarvis-answer">{answer}</div>
            </div>
            """
        )
        current_question = None
    if not responses:
        return ""
    return f'<div class="jarvis-history">{"".join(responses)}</div>'


def _ask_portfolio_agent(
    config: Mapping[str, Any],
    db_path: Path,
    question: str,
    history: list[dict[str, str]],
    *,
    paper_snapshot: Mapping[str, Any] | None = None,
) -> str:
    from analysis.api_client import APIClientError, create_analysis_client

    snapshot = _portfolio_snapshot(config, db_path, paper_snapshot=paper_snapshot)
    prompt = _portfolio_chat_prompt(question, snapshot, history)
    try:
        client = create_analysis_client(config)
        return client.complete(prompt)
    except (APIClientError, FileNotFoundError, TimeoutError, subprocess.SubprocessError) as exc:
        return f"JARVIS is unavailable: {exc}"


def _portfolio_chat_panel(
    st_module,
    config: Mapping[str, Any],
    db_path: Path,
    *,
    paper_snapshot: Mapping[str, Any] | None = None,
) -> None:
    history_key = "portfolio_agent_history"
    if history_key not in st_module.session_state:
        st_module.session_state[history_key] = []
    history = list(st_module.session_state[history_key])

    with st_module.container(border=True):
        st_module.markdown(_section_header("Ask JARVIS", "Portfolio analyst"), unsafe_allow_html=True)
        with st_module.form("portfolio_agent_form", clear_on_submit=True):
            question = st_module.text_input(
                "Ask JARVIS",
                placeholder="Ask anything...",
                label_visibility="collapsed",
            )
            submitted = st_module.form_submit_button("Ask JARVIS")
        st_module.markdown(_portfolio_chat_history_html(history), unsafe_allow_html=True)
        if submitted and question.strip():
            history.append({"role": "user", "content": question.strip()})
            with st_module.spinner("JARVIS is reading the portfolio snapshot..."):
                answer = _ask_portfolio_agent(config, db_path, question.strip(), history, paper_snapshot=paper_snapshot)
            history.append({"role": "assistant", "content": answer})
            st_module.session_state[history_key] = _trim_chat_history(history)
            st_module.rerun()


def _nav(st_module) -> str:
    page = st_module.query_params.get("page", PAGES[0])
    if isinstance(page, list):
        page = page[0]
    page = page if page in PAGES else PAGES[0]
    links = []
    for item in PAGES:
        active = " active" if item == page else ""
        links.append(
            f'<a class="nav-item{active}" href="?page={quote(item)}" target="_self">'
            f'<span class="roman">{NAV_NUMERALS[item]}</span>'
            f"<span>{escape(item)}</span>"
            "</a>"
        )
    st_module.markdown(
        f"""
        <div class="brand">
            <div class="brand-kicker">Meridian Capital</div>
            <div class="brand-title">JARVIS Terminal</div>
            <div class="brand-subtitle">Long / short equity fund cockpit</div>
        </div>
        <nav class="jarvis-nav">{"".join(links)}</nav>
        """,
        unsafe_allow_html=True,
    )
    return page


def _unknown_market_clock() -> dict[str, str]:
    return {"label": "Unknown", "detail": "Clock unavailable", "tone": "warn"}


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _normalize_alpaca_base_url(base_url: str | None) -> str:
    normalized = (base_url or PAPER_ALPACA_BASE_URL).strip().rstrip("/")
    if normalized.lower().endswith("/v2"):
        normalized = normalized[:-3].rstrip("/")
    return normalized or PAPER_ALPACA_BASE_URL


def _paper_alpaca_enabled(config: Mapping[str, Any]) -> bool:
    execution = config.get("execution", {}) if isinstance(config.get("execution"), Mapping) else {}
    mode = str(execution.get("mode", "paper")).strip().lower()
    broker = str(execution.get("broker", "")).strip().lower()
    return mode == "paper" and broker == "alpaca"


def _alpaca_paper_base_url(config: Mapping[str, Any]) -> str:
    execution = config.get("execution", {}) if isinstance(config.get("execution"), Mapping) else {}
    alpaca = execution.get("alpaca", {}) if isinstance(execution.get("alpaca"), Mapping) else {}
    return _normalize_alpaca_base_url(str(alpaca.get("paper_base_url", PAPER_ALPACA_BASE_URL)))


def _alpaca_paper_url(config: Mapping[str, Any], endpoint: str) -> str:
    return f"{_alpaca_paper_base_url(config)}/v2/{endpoint.lstrip('/')}"


def _empty_paper_snapshot(detail: str) -> dict[str, Any]:
    return {
        "available": False,
        "account": {},
        "positions": pd.DataFrame(),
        "orders": pd.DataFrame(),
        "activities": pd.DataFrame(),
        "detail": detail,
    }


def _paper_snapshot_available(snapshot: Mapping[str, Any] | None) -> bool:
    return bool(snapshot and snapshot.get("available"))


def _to_float(value: Any) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce")
    if numeric.isna().iloc[0]:
        return 0.0
    return float(numeric.iloc[0])


def _normalize_alpaca_positions(positions: Any, account: Mapping[str, Any]) -> pd.DataFrame:
    if not isinstance(positions, list):
        return pd.DataFrame(
            columns=[
                "ticker",
                "quantity",
                "market_value",
                "weight",
                "side",
                "current_price",
                "avg_entry_price",
                "unrealized_plpc",
            ]
        )
    portfolio_value = abs(_to_float(account.get("portfolio_value")))
    rows = []
    for position in positions:
        if not isinstance(position, Mapping):
            continue
        side = str(position.get("side", "")).lower()
        market_value = abs(_to_float(position.get("market_value")))
        signed_weight = market_value / portfolio_value if portfolio_value else 0.0
        if side == "short":
            signed_weight *= -1
        rows.append(
            {
                "ticker": str(position.get("symbol") or position.get("ticker") or "").upper(),
                "quantity": _to_float(position.get("qty")),
                "market_value": market_value,
                "weight": signed_weight,
                "side": side,
                "current_price": _to_float(position.get("current_price")),
                "avg_entry_price": _to_float(position.get("avg_entry_price")),
                "unrealized_plpc": _to_float(position.get("unrealized_plpc")),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "quantity",
            "market_value",
            "weight",
            "side",
            "current_price",
            "avg_entry_price",
            "unrealized_plpc",
        ],
    )


def _normalize_alpaca_orders(orders: Any) -> pd.DataFrame:
    if not isinstance(orders, list):
        return pd.DataFrame()
    frame = pd.DataFrame([order for order in orders if isinstance(order, Mapping)])
    if frame.empty:
        return frame
    rename = {"symbol": "ticker", "submitted_at": "submitted", "filled_avg_price": "avg_fill_price"}
    frame = frame.rename(columns={source: target for source, target in rename.items() if source in frame.columns})
    preferred = [
        "ticker",
        "side",
        "qty",
        "filled_qty",
        "type",
        "status",
        "limit_price",
        "avg_fill_price",
        "submitted",
        "filled_at",
    ]
    return frame[[column for column in preferred if column in frame.columns]]


def _normalize_alpaca_activities(activities: Any) -> pd.DataFrame:
    columns = ["ticker", "side", "quantity", "price", "date", "activity_type", "type", "order_id"]
    if not isinstance(activities, list):
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame([activity for activity in activities if isinstance(activity, Mapping)])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    rename = {"symbol": "ticker", "qty": "quantity", "transaction_time": "date"}
    frame = frame.rename(columns={source: target for source, target in rename.items() if source in frame.columns})
    if "ticker" in frame.columns:
        frame["ticker"] = frame["ticker"].astype(str).str.upper()
    for column in ("quantity", "price"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return frame[[column for column in columns if column in frame.columns]]


def _alpaca_paper_snapshot(
    config: Mapping[str, Any],
    *,
    session: Any | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    if not _paper_alpaca_enabled(config):
        return _empty_paper_snapshot("Paper Alpaca mode is not enabled")

    api_key = _first_env("ALPACA_API_KEY", "APCA_API_KEY_ID")
    secret_key = _first_env("ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        return _empty_paper_snapshot("Alpaca paper credentials unavailable")

    client = session or requests.Session()
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}
    try:
        account_response = client.get(_alpaca_paper_url(config, "account"), headers=headers, timeout=timeout)
        account_response.raise_for_status()
        account = account_response.json()

        positions_response = client.get(_alpaca_paper_url(config, "positions"), headers=headers, timeout=timeout)
        positions_response.raise_for_status()
        positions_payload = positions_response.json()

        orders_response = client.get(
            _alpaca_paper_url(config, "orders?status=all&limit=25"),
            headers=headers,
            timeout=timeout,
        )
        orders_response.raise_for_status()
        orders_payload = orders_response.json()
    except Exception as exc:
        return _empty_paper_snapshot(f"Alpaca paper unavailable: {type(exc).__name__}")

    activities_payload: Any = []
    activities_detail = "Alpaca paper fill activities"
    try:
        activities_response = client.get(
            _alpaca_paper_url(config, "account/activities/FILL?page_size=100&direction=desc"),
            headers=headers,
            timeout=timeout,
        )
        activities_response.raise_for_status()
        activities_payload = activities_response.json()
    except Exception as exc:
        activities_detail = f"Alpaca fill activities unavailable: {type(exc).__name__}"

    if not isinstance(account, Mapping):
        return _empty_paper_snapshot("Alpaca paper account response was invalid")

    return {
        "available": True,
        "account": dict(account),
        "positions": _normalize_alpaca_positions(positions_payload, account),
        "orders": _normalize_alpaca_orders(orders_payload),
        "activities": _normalize_alpaca_activities(activities_payload),
        "detail": "Alpaca paper account snapshot",
        "activities_detail": activities_detail,
    }


def _alpaca_clock_url(config: Mapping[str, Any]) -> str:
    execution = config.get("execution", {}) if isinstance(config.get("execution"), Mapping) else {}
    alpaca = execution.get("alpaca", {}) if isinstance(execution.get("alpaca"), Mapping) else {}
    mode = str(execution.get("mode", "paper")).lower()
    if mode == "live":
        env_base_url = _first_env("ALPACA_BASE_URL")
        if env_base_url:
            return f"{_normalize_alpaca_base_url(env_base_url)}/v2/clock"
        return f"{_normalize_alpaca_base_url(str(alpaca.get('live_base_url', LIVE_ALPACA_BASE_URL)))}/v2/clock"
    return f"{_alpaca_paper_base_url(config)}/v2/clock"


def _market_clock_detail(prefix: str, value: Any) -> str:
    if value:
        return f"{prefix} {value}"
    return "Clock unavailable"


def _market_clock_status(
    config: Mapping[str, Any],
    *,
    session: Any | None = None,
    timeout: float = 2.0,
) -> dict[str, str]:
    api_key = _first_env("ALPACA_API_KEY", "APCA_API_KEY_ID")
    secret_key = _first_env("ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        return _unknown_market_clock()

    client = session or requests.Session()
    try:
        response = client.get(
            _alpaca_clock_url(config),
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return _unknown_market_clock()

    if not isinstance(payload, Mapping):
        return _unknown_market_clock()

    is_open = payload.get("is_open")
    if is_open is True:
        return {"label": "Open", "detail": _market_clock_detail("Next close", payload.get("next_close")), "tone": "ok"}
    if is_open is False:
        return {"label": "Closed", "detail": _market_clock_detail("Next open", payload.get("next_open")), "tone": "warn"}
    return _unknown_market_clock()


def _dashboard_href(page: str, **updates: str) -> str:
    params = {"page": page}
    params.update({key: value for key, value in updates.items() if value})
    return f"?{urlencode(params)}"


def _autopilot_active_state(autopilot: Mapping[str, Any]) -> str:
    autopilot_status = str(autopilot.get("last_status", "never_run") or "never_run").lower()
    if bool(autopilot.get("paused", False)) or autopilot_status in AUTOPILOT_PAUSED_STATUSES:
        return "paused"
    autopilot_on = bool(autopilot.get("available", True)) and bool(autopilot.get("enabled", True))
    if not autopilot_on or autopilot_status == "unavailable":
        return "off"
    return "on"


def _topbar(
    st_module,
    page: str,
    config: Mapping[str, Any],
    *,
    paper_snapshot: Mapping[str, Any] | None = None,
    autopilot_state: Mapping[str, Any] | None = None,
    action_notice: str | None = None,
) -> None:
    today = date.today().strftime("%b %d, %Y")
    execution = config.get("execution", {}) if isinstance(config.get("execution"), Mapping) else {}
    mode_raw = str(execution.get("mode", "paper") or "paper").lower()
    live_enabled = bool(execution.get("allow_live_trading", False))
    live = "Live off" if not live_enabled else "Live enabled"
    live_tone = "warn" if live_enabled else "ok"
    market = _market_clock_status(config)
    data_plane = "Local DB"
    data_tone = "ok"
    data_detail = ""
    paper_account_available = False
    if _paper_alpaca_enabled(config):
        if _paper_snapshot_available(paper_snapshot):
            paper_account_available = True
            data_plane = "Alpaca paper"
            data_detail = str(paper_snapshot.get("detail", "Account snapshot"))
        else:
            data_plane = "Paper unavailable"
            data_tone = "warn"
            data_detail = str((paper_snapshot or {}).get("detail", "Alpaca paper snapshot unavailable"))
    primary_mode = "Paper first" if paper_account_available else "Local first"
    autopilot = dict(autopilot_state or _autopilot_state())
    autopilot_on = bool(autopilot.get("available", True)) and bool(autopilot.get("enabled", True))
    autopilot_active_state = _autopilot_active_state(autopilot)
    autopilot_label = "PAUSED" if autopilot_active_state == "paused" else autopilot_active_state.upper()
    autopilot_tone = "ok" if _autopilot_tone(autopilot) == "green" else "warn"
    autopilot_detail = str(autopilot.get("current_step") or autopilot.get("last_status", "never_run"))
    autopilot_buttons = "".join(
        f'<a class="state-button{" active" if state == autopilot_active_state else ""}" '
        f'data-state="{state}" href="{escape(_dashboard_href(page, autopilot=state))}" target="_self">{label}</a>'
        for state, label in (("on", "ON"), ("off", "OFF"), ("paused", "PAUSED"))
    )
    primary_badge_tone = "green" if paper_account_available else "blue"
    data_badge_tone = "green" if data_tone == "ok" else "amber"
    market_badge_tone = "green" if market["tone"] == "ok" else "amber"
    paper_mode_active = mode_raw == "paper" or not live_enabled
    live_mode_active = mode_raw == "live" and live_enabled
    paper_button_class = "mode-button active" if paper_mode_active else "mode-button"
    paper_button_html = (
        f'<a class="{paper_button_class}" data-mode="paper" '
        f'href="{escape(_dashboard_href(page, mode="paper"))}" target="_self"><span>Paper</span></a>'
    )
    if not live_enabled:
        live_button_html = '<span class="mode-button locked" data-mode="live" aria-disabled="true"><span>Live locked</span></span>'
    elif live_mode_active:
        live_button_class = "mode-button active"
        live_button_label = "Live"
        live_button_html = (
            f'<a class="{live_button_class}" data-mode="live" '
            f'href="{escape(_dashboard_href(page, mode="live"))}" target="_self"><span>{escape(live_button_label)}</span></a>'
        )
    else:
        live_button_class = "mode-button live-enabled"
        live_button_label = "Live enabled"
        live_button_html = (
            f'<a class="{live_button_class}" data-mode="live" '
            f'href="{escape(_dashboard_href(page, mode="live"))}" target="_self"><span>{escape(live_button_label)}</span></a>'
        )
    notice_html = f'<div class="topbar-notice">{escape(action_notice)}</div>' if action_notice else ""
    topbar_detail = (
        "Paper-first Alpaca account state, paper execution, risk control, and LP reporting from project data."
        if paper_account_available
        else "Local-first research, paper execution, risk control, and LP reporting from project data."
    )
    st_module.markdown(
        f"""
        <div class="topbar">
            <div class="product">
                <strong>{escape(page)}</strong>
                <span>Meridian Capital / Jarvis Terminal</span>
            </div>
            <div class="topbar-status-strip" aria-label="Dashboard state" title="{escape(topbar_detail)}">
                <div class="autopilot" aria-label="Paper Autopilot state">{autopilot_buttons}</div>
                <span class="badge {primary_badge_tone}"><span class="dot"></span><span>{escape(primary_mode)}</span></span>
                <span class="badge {data_badge_tone}"><span class="dot"></span><span>{escape(data_plane)}</span></span>
                <span class="badge {market_badge_tone}"><span class="dot"></span><span>Market {escape(market["label"])}</span></span>
            </div>
            <div class="mode-switch" aria-label="Execution mode">
                <div class="mode-buttons">
                    {paper_button_html}
                    {live_button_html}
                </div>
                <span class="last-updated">Last updated {escape(today)}</span>
            </div>
        </div>
        {notice_html}
        <div class="status-strip">
            <div class="status-cell"><span>Data plane</span><b class="{escape(data_tone)}">{escape(data_plane)}</b><small>{escape(data_detail)}</small></div>
            <div class="status-cell"><span>Market</span><b class="{escape(market["tone"])}">{escape(market["label"])}</b><small>{escape(market["detail"])}</small></div>
            <div class="status-cell"><span>Risk policy</span><b class="ok">Config enforced</b></div>
            <div class="status-cell"><span>Execution</span><b class="{live_tone}">{escape(live)}</b></div>
            <div class="status-cell"><span>Autopilot</span><b class="{autopilot_tone}">{escape(autopilot_label)}</b><small>{escape(autopilot_detail)}</small></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _chart_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=12, b=8),
        paper_bgcolor=THEME["panel"],
        plot_bgcolor=THEME["panel"],
        font=dict(color="#cbd3df", family="SFMono-Regular, Cascadia Mono, Consolas, monospace", size=11),
        xaxis=dict(gridcolor="rgba(42,49,64,0.72)", zerolinecolor="rgba(42,49,64,0.9)"),
        yaxis=dict(gridcolor="rgba(42,49,64,0.72)", zerolinecolor="rgba(42,49,64,0.9)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _positions_donut(positions: pd.DataFrame) -> go.Figure:
    weights = pd.to_numeric(positions.get("weight", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    long_gross = float(weights[weights > 0].sum())
    short_gross = float(weights[weights < 0].abs().sum())
    cash = max(0.0, 1.0 - min(1.0, long_gross))
    fig = go.Figure(
        go.Pie(
            labels=["Long book", "Short book", "Cash"],
            values=[long_gross, short_gross, cash],
            hole=0.66,
            marker=dict(colors=[THEME["green"], THEME["blue"], THEME["purple"]], line=dict(color=THEME["panel"], width=4)),
            textinfo="label+percent",
        )
    )
    fig.update_traces(textfont=dict(color=THEME["text"], size=11))
    return _chart_layout(fig, 290)


def _weights_bar(positions: pd.DataFrame) -> go.Figure:
    frame = positions.copy()
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce").fillna(0.0)
    frame = frame.sort_values("weight")
    colors = [THEME["green"] if value >= 0 else THEME["pink"] for value in frame["weight"]]
    fig = go.Figure(go.Bar(x=frame["weight"], y=frame["ticker"], orientation="h", marker=dict(color=colors), text=[_fmt_pct(v) for v in frame["weight"]], textposition="auto"))
    fig.update_xaxes(tickformat=".0%")
    return _chart_layout(fig, 300)


def _factor_heatmap(frame: pd.DataFrame) -> go.Figure | None:
    factor_cols = _factor_columns(frame)
    ticker_col = "ticker" if "ticker" in frame.columns else None
    if not factor_cols or ticker_col is None:
        return None
    score_col = _score_column(frame)
    ranked = frame.copy()
    if score_col:
        ranked[score_col] = pd.to_numeric(ranked[score_col], errors="coerce")
        ranked = ranked.sort_values(score_col, ascending=False)
        plot = pd.concat([ranked.head(20), ranked.tail(20)], ignore_index=False).drop_duplicates()
    else:
        plot = ranked.head(40)
    factor_labels = [label for label, _column in factor_cols]
    factor_names = [column for _label, column in factor_cols]
    plot = plot[[ticker_col, *factor_names]].copy()
    plot[factor_names] = plot[factor_names].apply(pd.to_numeric, errors="coerce")
    height = min(920, max(620, 180 + len(plot) * 14 + len(factor_names) * 4))
    fig = go.Figure(
        data=go.Heatmap(
            z=plot[factor_names].fillna(0).values,
            x=factor_labels,
            y=plot[ticker_col].astype(str).str.upper(),
            colorscale=[[0, THEME["pink"]], [0.5, "#1d2638"], [1, THEME["green"]]],
            xgap=1,
            ygap=1,
            colorbar=dict(thickness=10, outlinewidth=0, tickfont=dict(color=THEME["muted"])),
        )
    )
    fig = _chart_layout(fig, height)
    fig.update_layout(margin=dict(l=82, r=20, t=48, b=78))
    fig.update_xaxes(side="top", tickangle=-25, automargin=True, tickfont=dict(size=11))
    fig.update_yaxes(automargin=True, tickfont=dict(size=10))
    return fig


def _portfolio_page(
    st_module,
    config: Mapping[str, Any],
    db_path: Path,
    *,
    paper_snapshot: Mapping[str, Any] | None = None,
) -> None:
    positions, positions_source, using_paper_positions = _dashboard_positions(config, db_path, paper_snapshot)
    candidates = _candidate_universe()
    cards = _portfolio_metric_cards(config, db_path, paper_snapshot=paper_snapshot)
    for start in range(0, len(cards), 5):
        for col, card in zip(st_module.columns(5), cards[start : start + 5], strict=True):
            with col:
                st_module.markdown(_metric_card(*card), unsafe_allow_html=True)

    _portfolio_chat_panel(st_module, config, db_path, paper_snapshot=paper_snapshot)

    left, right = st_module.columns([1.25, 1], gap="large")
    with left:
        with st_module.container(border=True):
            title = "Paper account positions" if using_paper_positions else "Current book"
            st_module.markdown(_section_header(title, positions_source), unsafe_allow_html=True)
            _render_table(
                st_module,
                positions,
                empty_title="No paper positions" if using_paper_positions else "No portfolio positions",
                empty_body="Alpaca paper has no open positions." if using_paper_positions else "Run portfolio construction and persist positions into portfolio_positions to populate this page.",
            )
    with right:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Book decomposition", "From Alpaca paper weights" if using_paper_positions else "From local weights"), unsafe_allow_html=True)
            if positions.empty:
                st_module.markdown(
                    _empty_panel(
                        "No chart available",
                        "There are no Alpaca paper positions yet." if using_paper_positions else "There are no local positions yet, so gross/net exposure charts would be fabricated.",
                    ),
                    unsafe_allow_html=True,
                )
            else:
                st_module.plotly_chart(_positions_donut(positions), use_container_width=True, config={"displayModeBar": False})

    lower_left, lower_right = st_module.columns([1.15, 1.35], gap="large")
    with lower_left:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Position weights", positions_source), unsafe_allow_html=True)
            if positions.empty:
                st_module.markdown(
                    _empty_panel("No exposure chart", "No Alpaca paper position weights were found." if using_paper_positions else "No local position weights were found."),
                    unsafe_allow_html=True,
                )
            else:
                st_module.plotly_chart(_weights_bar(positions), use_container_width=True, config={"displayModeBar": False})
    with lower_right:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Scored universe", "From output CSV"), unsafe_allow_html=True)
            _render_table(st_module, candidates, empty_title="No scored universe", empty_body="output/scored_universe_latest.csv and output/factor_inputs.csv are not present yet.")


def _research_page(st_module, config: Mapping[str, Any], db_path: Path) -> None:
    candidates = _candidate_universe()
    analysis_count = _table_count(db_path, str(config.get("analysis", {}).get("cache_table", "analysis_results")))
    cards = _research_metric_cards(config, db_path)
    for col, card in zip(st_module.columns(5), cards, strict=True):
        with col:
            st_module.markdown(_metric_card(*card), unsafe_allow_html=True)

    optimizer_cfg = config.get("portfolio", {}).get("optimizer", {}) if isinstance(config.get("portfolio"), Mapping) else {}
    methods = list(portfolio_preferences.allowed_optimizer_methods(config))
    saved_method = portfolio_preferences.preferred_optimizer_method(config)
    method_index = methods.index(saved_method) if saved_method in methods else 0
    top_left, top_right = st_module.columns([1, 1], gap="large")
    with top_left:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Active method", "Portfolio optimizer"), unsafe_allow_html=True)
            active_method = st_module.radio(
                "Active method",
                methods,
                index=method_index,
                horizontal=True,
                format_func=lambda value: "MVO" if value == "mvo" else "Conviction",
                label_visibility="collapsed",
            )
            if active_method != saved_method:
                saved_method = portfolio_preferences.save_portfolio_preferences(active_method, config=config)["optimizer_method"]
            st_module.markdown(
                _empty_panel(
                    "MVO" if saved_method == "mvo" else "Conviction",
                    "Saved for L4 portfolio",
                ),
                unsafe_allow_html=True,
            )
    with top_right:
        with st_module.container(border=True):
            cost_bps = float(optimizer_cfg.get("transaction_cost_bps", 0) or 0)
            st_module.markdown(_section_header("Avg est. trade cost", "Config"), unsafe_allow_html=True)
            st_module.markdown(_empty_panel(f"{cost_bps:.1f} bps", "spread + market impact, per-name"), unsafe_allow_html=True)

    _render_candidate_recommendation_board(st_module, config, db_path, candidates)

    with st_module.container(border=True):
        st_module.markdown(_section_header("Factor scoring heatmap", "top + bottom by composite"), unsafe_allow_html=True)
        heatmap = _factor_heatmap(candidates)
        if heatmap is None:
            st_module.markdown(
                _empty_panel(
                    "No factor heatmap",
                    "output/scored_universe_latest.csv or output/factor_inputs.csv needs factor score columns.",
                ),
                unsafe_allow_html=True,
            )
        else:
            st_module.plotly_chart(heatmap, use_container_width=True, config={"displayModeBar": False})

    st_module.markdown('<div class="research-lower-spacer"></div>', unsafe_allow_html=True)
    left, right = st_module.columns([1.1, 1], gap="large")
    with left:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Latest filings", "sec_filings"), unsafe_allow_html=True)
            _render_table(st_module, _read_table(db_path, "sec_filings"), empty_title="No SEC filings", empty_body="The sec_filings table is empty.")
    with right:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Analysis cache", str(config.get("analysis", {}).get("provider", "codex")).upper()), unsafe_allow_html=True)
            _render_table(st_module, _read_table(db_path, str(config.get("analysis", {}).get("cache_table", "analysis_results"))), empty_title="No AI analysis cache", empty_body="Run analysis jobs to populate analysis_results.")


def _risk_page(
    st_module,
    config: Mapping[str, Any],
    db_path: Path,
    *,
    paper_snapshot: Mapping[str, Any] | None = None,
) -> None:
    positions, positions_source, using_paper_positions = _dashboard_positions(config, db_path, paper_snapshot)
    risk_cfg = config.get("risk", {}) if isinstance(config.get("risk"), Mapping) else {}
    breakers = risk_cfg.get("circuit_breakers", {}) if isinstance(risk_cfg.get("circuit_breakers"), Mapping) else {}
    lock = ensure_project_path(str(breakers.get("lock_file", "cache/trading_halt.lock")), PROJECT_ROOT)
    weights = pd.to_numeric(positions.get("weight", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    gross = float(weights.abs().sum()) if not weights.empty else 0.0
    net = float(weights.sum()) if not weights.empty else 0.0
    cards = [
        ("Halt lock", "Active" if lock.exists() else "Clear", str(lock.relative_to(PROJECT_ROOT)), "pink" if lock.exists() else "green"),
        ("Daily hard stop", _fmt_pct(float(breakers.get("daily_loss_hard", 0.03))), "Configured circuit breaker", "pink"),
        ("Max drawdown", _fmt_pct(float(breakers.get("max_drawdown_halt", 0.10))), "Configured halt threshold", "amber"),
        ("Gross", _fmt_pct(gross), f"From {positions_source}", "blue"),
        ("Net", _fmt_pct(net), f"From {positions_source}", "green" if abs(net) <= 0.1 else "amber"),
    ]
    for col, card in zip(st_module.columns(5), cards, strict=True):
        with col:
            st_module.markdown(_metric_card(*card), unsafe_allow_html=True)
    left, right = st_module.columns([1.1, 1], gap="large")
    with left:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Risk configuration", "config.yaml"), unsafe_allow_html=True)
            rows = pd.DataFrame(
                [
                    ("single_name_nav_loss", risk_cfg.get("max_single_name_nav_loss")),
                    ("pairwise_correlation_limit", risk_cfg.get("pairwise_correlation_limit")),
                    ("min_dollar_adv", risk_cfg.get("liquidity", {}).get("min_dollar_adv") if isinstance(risk_cfg.get("liquidity"), Mapping) else None),
                    ("max_trade_adv_pct", risk_cfg.get("liquidity", {}).get("max_trade_adv_pct") if isinstance(risk_cfg.get("liquidity"), Mapping) else None),
                ],
                columns=["check", "configured_value"],
            )
            _render_table(st_module, rows, empty_title="No risk config", empty_body="Risk config section is missing.")
    with right:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Position risk", positions_source), unsafe_allow_html=True)
            if positions.empty:
                st_module.markdown(
                    _empty_panel(
                        "No paper positions" if using_paper_positions else "No local positions",
                        "Alpaca paper has no open positions." if using_paper_positions else "Risk exposure charts are intentionally blank until portfolio_positions has rows.",
                    ),
                    unsafe_allow_html=True,
                )
            else:
                st_module.plotly_chart(_weights_bar(positions), use_container_width=True, config={"displayModeBar": False})


def _performance_page(st_module, config: Mapping[str, Any], db_path: Path) -> None:
    tear = _latest_report("tear")
    reports = sorted((PROJECT_ROOT / "output" / "reports").glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    history = _read_table(db_path, "portfolio_history")
    reporting_snapshot = _reporting_artifact_snapshot()
    cards = [
        ("History rows", str(len(history)), "portfolio_history table", "blue"),
        ("Reports", str(reporting_snapshot["report_count"]), "Markdown files under output/reports", "purple"),
        ("Tear sheet", "Found" if tear else "Missing", tear.name if tear else "No tear sheet file", "green" if tear else "amber"),
        ("Alpha", _fmt_pct(float(reporting_snapshot["latest_alpha_residual"])), "Latest output/daily_attribution.csv", "green" if reporting_snapshot["daily_attribution_rows"] else "amber"),
    ]
    for col, card in zip(st_module.columns(4), cards, strict=True):
        with col:
            st_module.markdown(_metric_card(*card), unsafe_allow_html=True)
    left, right = st_module.columns([1, 1], gap="large")
    with left:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Portfolio history", "portfolio_history"), unsafe_allow_html=True)
            _render_table(st_module, history, empty_title="No performance time series", empty_body="portfolio_history is empty, so the dashboard will not draw a fake equity curve.")
    with right:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Latest tear sheet", tear.name if tear else "Not generated"), unsafe_allow_html=True)
            text = tear.read_text(encoding="utf-8") if tear else "No tear sheet report exists yet."
            st_module.markdown(f'<div class="terminal-panel"><pre>{escape(text)}</pre></div>', unsafe_allow_html=True)


def _execution_page(
    st_module,
    config: Mapping[str, Any],
    db_path: Path,
    *,
    paper_snapshot: Mapping[str, Any] | None = None,
    autopilot_state: Mapping[str, Any] | None = None,
) -> None:
    execution = config.get("execution", {}) if isinstance(config.get("execution"), Mapping) else {}
    approvals = _read_table(db_path, "portfolio_approvals")
    pending = int((approvals.get("status", pd.Series(dtype=str)).astype(str) == "pending").sum()) if not approvals.empty else 0
    autopilot_cards = _autopilot_metric_cards(autopilot_state or _autopilot_state())
    if _paper_snapshot_available(paper_snapshot):
        account = paper_snapshot.get("account", {})
        orders = paper_snapshot.get("orders")
        order_count = len(orders) if isinstance(orders, pd.DataFrame) else 0
        cards = autopilot_cards + [
            ("Mode", str(execution.get("mode", "paper")).title(), "Alpaca paper account", "green"),
            ("Equity", _fmt_money(account.get("equity", account.get("portfolio_value"))), "Paper account", "blue"),
            ("Cash", _fmt_money(account.get("cash")), "Paper account", "green"),
            ("Buying power", _fmt_money(account.get("buying_power")), "Paper account", "purple"),
            ("Live trading", str(execution.get("allow_live_trading", False)), "Live orders disabled unless explicitly enabled", "pink" if execution.get("allow_live_trading", False) else "green"),
            ("Recent orders", str(order_count), "Alpaca paper orders", "amber" if order_count else "green"),
        ]
    else:
        cards = autopilot_cards + [
            ("Mode", str(execution.get("mode", "paper")).title(), "From config.yaml", "green"),
            ("Dry run", str(execution.get("dry_run_default", True)), "Default execution setting", "blue"),
            ("Live trading", str(execution.get("allow_live_trading", False)), "Live orders disabled unless explicitly enabled", "pink" if execution.get("allow_live_trading", False) else "green"),
            ("Pending approvals", str(pending), "portfolio_approvals table", "amber"),
        ]
    _render_metric_card_rows(st_module, cards, max_per_row=4)
    left, right = st_module.columns([1.1, 1], gap="large")
    with left:
        with st_module.container(border=True):
            if _paper_snapshot_available(paper_snapshot):
                orders = paper_snapshot.get("orders")
                orders_frame = orders if isinstance(orders, pd.DataFrame) else pd.DataFrame()
                st_module.markdown(_section_header("Recent paper orders", "Alpaca paper"), unsafe_allow_html=True)
                _render_table(st_module, orders_frame, empty_title="No paper orders", empty_body="Alpaca paper returned no recent orders.")
            else:
                st_module.markdown(_section_header("Approval queue", "portfolio_approvals"), unsafe_allow_html=True)
                _render_table(st_module, approvals, empty_title="No execution approvals", empty_body="There are no local approval records. The dashboard will not invent order state.")
    with right:
        with st_module.container(border=True):
            title = "Local approval queue" if _paper_snapshot_available(paper_snapshot) else "Execution guardrails"
            subtitle = "portfolio_approvals" if _paper_snapshot_available(paper_snapshot) else "config.yaml"
            st_module.markdown(_section_header(title, subtitle), unsafe_allow_html=True)
            if _paper_snapshot_available(paper_snapshot):
                _render_table(st_module, approvals, empty_title="No local approvals", empty_body="No local approval records are queued.")
                return
            rows = pd.DataFrame(
                [
                    ("broker", execution.get("broker")),
                    ("order_type", execution.get("order_defaults", {}).get("type") if isinstance(execution.get("order_defaults"), Mapping) else None),
                    ("time_in_force", execution.get("order_defaults", {}).get("time_in_force") if isinstance(execution.get("order_defaults"), Mapping) else None),
                    ("limit_buffer_bps", execution.get("order_defaults", {}).get("limit_buffer_bps") if isinstance(execution.get("order_defaults"), Mapping) else None),
                    ("require_live_ack", execution.get("require_live_risk_acknowledgement")),
                ],
                columns=["setting", "value"],
            )
            _render_table(st_module, rows, empty_title="No execution config", empty_body="Execution config section is missing.")


def _letter_summary_prompt(letter_path: Path, letter_text: str) -> str:
    clipped = letter_text.strip()
    if len(clipped) > LETTER_SUMMARY_MAX_CHARS:
        clipped = clipped[:LETTER_SUMMARY_MAX_CHARS].rstrip() + "\n\n[Letter text truncated for prompt size.]"
    return f"""You are JARVIS, the Codex analyst for Meridian Capital Partners.
Create a concise summary of the investor letter below.
Responde em portugues de Portugal.
Use only the letter content. Nao inventes factos, trades, PnL, dates, or decisions that are not present in the letter.
If a detail is absent, say that it is not stated in the letter.

Include:
- estado do portfolio e exposicao se estiverem descritos
- entradas, saidas, posicoes fechadas, lucro/prejuizo e riscos se estiverem descritos
- pontos que o gestor deve rever

LETTER_FILE: {letter_path.name}

LETTER_TEXT:
{clipped}
"""


def _letter_summary_output_path(letter_path: Path, output_path: str | Path | None = None) -> Path:
    if output_path is not None:
        explicit = Path(output_path)
        return explicit if explicit.is_absolute() else ensure_project_path(explicit, PROJECT_ROOT)
    safe_stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in letter_path.stem).strip("_")
    safe_stem = safe_stem.replace("letter", "report").replace("Letter", "Report") or date.today().isoformat()
    return letter_path.parent / f"jarvis_summary_{safe_stem}.md"


def _latest_letter_summary(letter_path: Path | None) -> Path | None:
    if letter_path is None:
        return None
    summary_path = _letter_summary_output_path(letter_path)
    return summary_path if summary_path.exists() else None


def _generate_letter_summary(
    config: Mapping[str, Any],
    letter_path: str | Path,
    *,
    output_path: str | Path | None = None,
    client: Any | None = None,
) -> Path:
    resolved_letter = Path(letter_path)
    if not resolved_letter.is_absolute():
        resolved_letter = ensure_project_path(resolved_letter, PROJECT_ROOT)
    if not resolved_letter.exists():
        raise FileNotFoundError(f"Letter not found: {resolved_letter}")

    text = resolved_letter.read_text(encoding="utf-8")
    prompt = _letter_summary_prompt(resolved_letter, text)
    if client is None:
        from analysis.api_client import create_analysis_client

        client = create_analysis_client(config)
    summary = str(client.complete(prompt)).strip()
    if not summary:
        raise RuntimeError("Codex returned an empty letter summary.")

    resolved_output = _letter_summary_output_path(resolved_letter, output_path)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(summary + "\n", encoding="utf-8")
    return resolved_output


def _letter_page(st_module, config: Mapping[str, Any], db_path: Path) -> None:
    letter = _latest_report("letter") or _latest_report()
    reports = sorted((PROJECT_ROOT / "output" / "reports").glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    cards = [
        ("Report files", str(len(reports)), "Markdown reports under output/reports", "blue"),
        ("Latest", letter.name if letter else "None", "Most recent report file", "purple"),
        ("Daily LP letter", "Enabled" if config.get("reporting", {}).get("daily_lp_letter", {}).get("enabled", False) else "Disabled", "From config.yaml", "green"),
    ]
    for col, card in zip(st_module.columns(3), cards, strict=True):
        with col:
            st_module.markdown(_metric_card(*card), unsafe_allow_html=True)
    left, right = st_module.columns([1.2, 0.8], gap="large")
    with left:
        with st_module.container(border=True):
            st_module.markdown(_section_header("Latest letter", letter.name if letter else "No report"), unsafe_allow_html=True)
            text = letter.read_text(encoding="utf-8") if letter else "No markdown report exists yet."
            st_module.markdown(f'<div class="terminal-panel"><pre>{escape(text)}</pre></div>', unsafe_allow_html=True)
    with right:
        with st_module.container(border=True):
            st_module.markdown(_section_header("JARVIS summary", "Manual Codex"), unsafe_allow_html=True)
            summary_path = _latest_letter_summary(letter)
            if letter is None:
                st_module.markdown(_empty_panel("No letter", "Generate a markdown letter before asking JARVIS for a summary."), unsafe_allow_html=True)
            else:
                clicked = st_module.button(
                    "Generate JARVIS summary",
                    key=f"letter_summary_{letter.name}",
                    type="primary",
                    use_container_width=True,
                )
                if clicked:
                    try:
                        with st_module.spinner("JARVIS is summarizing the letter..."):
                            summary_path = _generate_letter_summary(config, letter)
                        st_module.success(f"JARVIS summary saved: {summary_path.name}")
                    except Exception as exc:
                        st_module.error(f"JARVIS summary failed: {exc}")
                if summary_path is not None and summary_path.exists():
                    summary_text = summary_path.read_text(encoding="utf-8")
                    st_module.markdown(f'<div class="terminal-panel"><pre>{escape(summary_text)}</pre></div>', unsafe_allow_html=True)
                else:
                    st_module.markdown(_empty_panel("No JARVIS summary", "Use the manual button to create one from the current letter."), unsafe_allow_html=True)
        with st_module.container(border=True):
            st_module.markdown(_section_header("Report index", "output/reports"), unsafe_allow_html=True)
            index = pd.DataFrame(
                [{"file": path.name, "bytes": path.stat().st_size, "modified": date.fromtimestamp(path.stat().st_mtime).isoformat()} for path in reports]
            )
            _render_table(st_module, index, empty_title="No reports", empty_body="output/reports has no markdown files.")


def render() -> None:
    import streamlit as st

    st.set_page_config(page_title="Meridian JARVIS", page_icon="J", layout="wide", initial_sidebar_state="collapsed")
    _inject_css(st)
    config = _config()
    _enforce_dashboard_auth(st, config)
    db_path = _db_path(config)
    page = _nav(st)
    action_notice = _handle_dashboard_actions(st, config)
    paper_snapshot = _alpaca_paper_snapshot(config) if _paper_alpaca_enabled(config) else None
    autopilot_state = _autopilot_state()
    _topbar(st, page, config, paper_snapshot=paper_snapshot, autopilot_state=autopilot_state, action_notice=action_notice)
    _inject_auto_refresh(st, page, config)

    if page == "Portfolio":
        _portfolio_page(st, config, db_path, paper_snapshot=paper_snapshot)
    elif page == "Research":
        _research_page(st, config, db_path)
    elif page == "Risk":
        _risk_page(st, config, db_path, paper_snapshot=paper_snapshot)
    elif page == "Performance":
        _performance_page(st, config, db_path)
    elif page == "Execution":
        _execution_page(st, config, db_path, paper_snapshot=paper_snapshot, autopilot_state=autopilot_state)
    else:
        _letter_page(st, config, db_path)


def main() -> None:
    render()


if __name__ == "__main__":
    main()
