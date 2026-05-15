from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import dashboard.app as dashboard_app
from dashboard.app import (
    PAGES,
    _market_clock_status,
    _nav,
    _portfolio_chat_history_html,
    _portfolio_chat_prompt,
    _portfolio_metric_cards,
    _portfolio_snapshot,
    _topbar,
    _reporting_artifact_snapshot,
    _trim_chat_history,
)


class FakeStreamlit:
    def __init__(
        self,
        page: str = "Portfolio",
        query_params: dict[str, object] | None = None,
        *,
        secrets: dict[str, object] | None = None,
        user: object | None = None,
    ) -> None:
        self.query_params = {"page": page, **(query_params or {})}
        self.markdown_calls: list[tuple[str, bool | None]] = []
        self.error_calls: list[str] = []
        self.button_calls: list[dict[str, object]] = []
        self.components = FakeComponents()
        self.secrets = secrets if secrets is not None else {}
        self.user = user if user is not None else FakeUser()
        self.stop_called = False
        self.login_calls = 0
        self.logout_calls = 0

    def markdown(self, body: str, *, unsafe_allow_html: bool | None = None) -> None:
        self.markdown_calls.append((body, unsafe_allow_html))

    def error(self, body: str) -> None:
        self.error_calls.append(body)

    def button(self, label: str, **kwargs: object) -> bool:
        self.button_calls.append({"label": label, **kwargs})
        return False

    def login(self, *args: object, **kwargs: object) -> None:
        self.login_calls += 1

    def logout(self, *args: object, **kwargs: object) -> None:
        self.logout_calls += 1

    def stop(self) -> None:
        self.stop_called = True
        raise FakeStop()


class FakeStop(Exception):
    pass


class FakeUser:
    def __init__(self, *, is_logged_in: bool = False, email: str = "", name: str = "", email_verified: bool = True) -> None:
        self.is_logged_in = is_logged_in
        self.email = email
        self.name = name
        self.email_verified = email_verified


class MissingSecrets(dict):
    def get(self, key: str, default: object | None = None) -> object:
        raise RuntimeError("No secrets found")


class FakeComponentsV1:
    def __init__(self) -> None:
        self.html_calls: list[tuple[str, int]] = []

    def html(self, body: str, *, height: int = 0) -> None:
        self.html_calls.append((body, height))


class FakeComponents:
    def __init__(self) -> None:
        self.v1 = FakeComponentsV1()


VALID_AUTH_SECRETS = {
    "auth": {
        "redirect_uri": "http://localhost:8501/oauth2callback",
        "cookie_secret": "x" * 48,
        "client_id": "clerk-client-id",
        "client_secret": "clerk-client-secret",
        "server_metadata_url": "https://example.clerk.accounts.dev/.well-known/openid-configuration",
    }
}


def test_dashboard_auth_disabled_does_not_block_dashboard() -> None:
    fake_st = FakeStreamlit()

    user = dashboard_app._enforce_dashboard_auth(fake_st, {"dashboard": {"auth": {"enabled": False}}})

    assert user is None
    assert fake_st.stop_called is False
    assert fake_st.button_calls == []


def test_dashboard_auth_enabled_fails_closed_when_streamlit_secrets_are_missing() -> None:
    fake_st = FakeStreamlit(secrets={})

    with pytest.raises(FakeStop):
        dashboard_app._enforce_dashboard_auth(
            fake_st,
            {"dashboard": {"auth": {"enabled": True, "allowed_emails": ["pm@example.com"]}}},
        )

    html = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert fake_st.stop_called is True
    assert "Clerk setup required" in html
    assert "Create .streamlit/secrets.toml" in html
    assert "client_secret" in html
    assert fake_st.button_calls == []


def test_dashboard_auth_enabled_fails_closed_when_streamlit_secrets_object_raises() -> None:
    fake_st = FakeStreamlit(secrets=MissingSecrets())

    with pytest.raises(FakeStop):
        dashboard_app._enforce_dashboard_auth(
            fake_st,
            {"dashboard": {"auth": {"enabled": True, "allowed_emails": ["pm@example.com"]}}},
        )

    html = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert "Clerk setup required" in html
    assert "redirect_uri" in html
    assert fake_st.button_calls == []


def test_dashboard_auth_defaults_to_clerk_oidc_provider_and_button_label() -> None:
    settings = dashboard_app._dashboard_auth_settings({"dashboard": {"auth": {"enabled": True}}})

    assert settings["provider"] == "clerk_oidc"
    assert settings["login_button_label"] == "Log in with Clerk"


def test_dashboard_auth_enabled_renders_clerk_login_when_user_is_anonymous() -> None:
    fake_st = FakeStreamlit(secrets=VALID_AUTH_SECRETS, user=FakeUser(is_logged_in=False))

    with pytest.raises(FakeStop):
        dashboard_app._enforce_dashboard_auth(
            fake_st,
            {"dashboard": {"auth": {"enabled": True, "allowed_emails": ["pm@example.com"]}}},
        )

    assert fake_st.stop_called is True
    assert fake_st.button_calls[-1]["label"] == "Log in with Clerk"
    assert fake_st.button_calls[-1]["on_click"] == fake_st.login


def test_dashboard_auth_requires_verified_allowed_clerk_identity() -> None:
    fake_st = FakeStreamlit(
        secrets=VALID_AUTH_SECRETS,
        user=FakeUser(is_logged_in=True, email="pm@example.com", name="PM", email_verified=False),
    )

    with pytest.raises(FakeStop):
        dashboard_app._enforce_dashboard_auth(
            fake_st,
            {
                "dashboard": {
                    "auth": {
                        "enabled": True,
                        "allowed_emails": ["pm@example.com"],
                        "require_verified_email": True,
                    }
                }
            },
        )

    html = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert "Email is not verified" in html
    assert fake_st.button_calls[-1]["label"] == "Log out"
    assert fake_st.button_calls[-1]["on_click"] == fake_st.logout


def test_dashboard_auth_allows_verified_clerk_user_from_email_or_domain_allowlist() -> None:
    exact_match = FakeStreamlit(
        secrets=VALID_AUTH_SECRETS,
        user=FakeUser(is_logged_in=True, email="pm@example.com", name="PM", email_verified=True),
    )
    domain_match = FakeStreamlit(
        secrets=VALID_AUTH_SECRETS,
        user=FakeUser(is_logged_in=True, email="analyst@meridian.example", name="Analyst", email_verified=True),
    )
    config = {
        "dashboard": {
            "auth": {
                "enabled": True,
                "allowed_emails": ["pm@example.com"],
                "allowed_domains": ["meridian.example"],
                "require_verified_email": True,
            }
        }
    }

    exact_user = dashboard_app._enforce_dashboard_auth(exact_match, config)
    domain_user = dashboard_app._enforce_dashboard_auth(domain_match, config)

    assert exact_user == {"email": "pm@example.com", "name": "PM"}
    assert domain_user == {"email": "analyst@meridian.example", "name": "Analyst"}
    assert exact_match.stop_called is False
    assert domain_match.stop_called is False


class FakeClockResponse:
    def __init__(self, payload: object, *, status_error: Exception | None = None) -> None:
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self) -> None:
        if self.status_error is not None:
            raise self.status_error

    def json(self) -> object:
        return self.payload


class FakeClockSession:
    def __init__(self, response: FakeClockResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeClockResponse:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeAlpacaSession:
    def __init__(self, responses: dict[str, FakeClockResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeClockResponse:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def test_nav_links_stay_in_current_tab() -> None:
    fake_st = FakeStreamlit("Risk")

    page = _nav(fake_st)

    assert page == "Risk"
    nav_html = fake_st.markdown_calls[-1][0]
    assert nav_html.count('target="_self"') == len(PAGES)
    assert 'target="_blank"' not in nav_html


def test_auto_refresh_interval_uses_dashboard_config_with_fallback_and_bounds() -> None:
    assert dashboard_app._auto_refresh_interval_seconds({"dashboard": {"auto_refresh_seconds_market_hours": 75}}) == 75
    assert dashboard_app._auto_refresh_interval_seconds({}) == 60
    assert dashboard_app._auto_refresh_interval_seconds({"dashboard": {"auto_refresh_seconds_market_hours": 2}}) == 10
    assert dashboard_app._auto_refresh_interval_seconds({"dashboard": {"auto_refresh_seconds_market_hours": 900}}) == 300


def test_inject_auto_refresh_renders_manual_badge_without_component_iframe() -> None:
    fake_st = FakeStreamlit(
        "Execution",
        {"autopilot": "paused", "mode": "paper", "refresh": "on", "view": ["compact", "orders"]},
    )

    dashboard_app._inject_auto_refresh(
        fake_st,
        "Execution",
        {"dashboard": {"auto_refresh_seconds_market_hours": 60}},
    )

    html = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert "auto-refresh-badge" in html
    assert "Manual refresh" in html
    assert fake_st.components.v1.html_calls == []
    assert fake_st.query_params == {
        "page": "Execution",
        "autopilot": "paused",
        "mode": "paper",
        "refresh": "on",
        "view": ["compact", "orders"],
    }


def test_inject_auto_refresh_query_param_off_renders_off_badge_without_script() -> None:
    fake_st = FakeStreamlit("Execution", {"refresh": "off", "autopilot": "on"})

    dashboard_app._inject_auto_refresh(
        fake_st,
        "Execution",
        {"dashboard": {"auto_refresh_seconds_market_hours": 60}},
    )

    html = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert "Manual refresh" in html
    assert fake_st.components.v1.html_calls == []


def test_market_clock_status_reads_alpaca_clock_without_v2_duplication(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
    session = FakeClockSession(
        FakeClockResponse(
            {
                "is_open": True,
                "next_open": "2026-05-08T13:30:00Z",
                "next_close": "2026-05-07T20:00:00Z",
            }
        )
    )

    status = _market_clock_status({}, session=session, timeout=1.25)

    assert status == {"label": "Open", "detail": "Next close 2026-05-07T20:00:00Z", "tone": "ok"}
    assert session.calls == [
        {
            "url": "https://paper-api.alpaca.markets/v2/clock",
            "headers": {"APCA-API-KEY-ID": "test-key", "APCA-API-SECRET-KEY": "test-secret"},
            "timeout": 1.25,
        }
    ]


def test_market_clock_status_uses_configured_paper_url_when_env_base_url_is_live(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets/v2")
    session = FakeClockSession(
        FakeClockResponse(
            {
                "is_open": False,
                "next_open": "2026-05-08T13:30:00Z",
                "next_close": "2026-05-07T20:00:00Z",
            }
        )
    )

    status = _market_clock_status(
        {
            "execution": {
                "mode": "paper",
                "broker": "alpaca",
                "alpaca": {"paper_base_url": "https://configured-paper.example/v2"},
            }
        },
        session=session,
        timeout=1.25,
    )

    assert status == {"label": "Closed", "detail": "Next open 2026-05-08T13:30:00Z", "tone": "warn"}
    assert [call["url"] for call in session.calls] == ["https://configured-paper.example/v2/clock"]


def test_market_clock_status_falls_back_to_unknown_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    session = FakeClockSession(RuntimeError("network should not be called"))

    status = _market_clock_status({}, session=session)

    assert status == {"label": "Unknown", "detail": "Clock unavailable", "tone": "warn"}
    assert session.calls == []


def test_alpaca_paper_snapshot_reads_account_positions_orders_and_fills_without_v2_duplication(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
    session = FakeAlpacaSession(
        {
            "https://paper-api.alpaca.markets/v2/account": FakeClockResponse(
                {"portfolio_value": "100000", "cash": "25000", "buying_power": "50000"}
            ),
            "https://paper-api.alpaca.markets/v2/positions": FakeClockResponse(
                [
                    {
                        "symbol": "AAA",
                        "qty": "10",
                        "market_value": "1000",
                        "side": "long",
                        "current_price": "100",
                        "avg_entry_price": "90",
                        "unrealized_plpc": "0.1111",
                    }
                ]
            ),
            "https://paper-api.alpaca.markets/v2/orders?status=all&limit=25": FakeClockResponse(
                [{"symbol": "AAA", "side": "buy", "qty": "10", "status": "filled"}]
            ),
            "https://paper-api.alpaca.markets/v2/account/activities/FILL?page_size=100&direction=desc": FakeClockResponse(
                [
                    {
                        "activity_type": "FILL",
                        "symbol": "AAA",
                        "side": "buy",
                        "qty": "10",
                        "price": "90",
                        "transaction_time": "2026-05-14T14:30:00Z",
                        "order_id": "order-1",
                        "type": "fill",
                    }
                ]
            ),
        }
    )

    snapshot = dashboard_app._alpaca_paper_snapshot(
        {"execution": {"mode": "paper", "broker": "alpaca"}},
        session=session,
        timeout=1.25,
    )

    assert snapshot["available"] is True
    assert snapshot["account"]["portfolio_value"] == "100000"
    assert snapshot["positions"].iloc[0].to_dict() == {
        "ticker": "AAA",
        "quantity": 10.0,
        "market_value": 1000.0,
        "weight": 0.01,
        "side": "long",
        "current_price": 100.0,
        "avg_entry_price": 90.0,
        "unrealized_plpc": 0.1111,
    }
    assert snapshot["orders"].iloc[0]["status"] == "filled"
    assert snapshot["activities"].iloc[0].to_dict() == {
        "ticker": "AAA",
        "side": "buy",
        "quantity": 10.0,
        "price": 90.0,
        "date": "2026-05-14T14:30:00Z",
        "activity_type": "FILL",
        "type": "fill",
        "order_id": "order-1",
    }
    assert [call["url"] for call in session.calls] == [
        "https://paper-api.alpaca.markets/v2/account",
        "https://paper-api.alpaca.markets/v2/positions",
        "https://paper-api.alpaca.markets/v2/orders?status=all&limit=25",
        "https://paper-api.alpaca.markets/v2/account/activities/FILL?page_size=100&direction=desc",
    ]
    assert session.calls[0]["headers"] == {"APCA-API-KEY-ID": "test-key", "APCA-API-SECRET-KEY": "test-secret"}
    assert session.calls[0]["timeout"] == 1.25


def test_alpaca_paper_snapshot_uses_configured_paper_url_when_env_base_url_is_live(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets/v2")
    session = FakeAlpacaSession(
        {
            "https://configured-paper.example/v2/account": FakeClockResponse(
                {"portfolio_value": "100000", "cash": "25000", "buying_power": "50000"}
            ),
            "https://configured-paper.example/v2/positions": FakeClockResponse([]),
            "https://configured-paper.example/v2/orders?status=all&limit=25": FakeClockResponse([]),
        }
    )

    snapshot = dashboard_app._alpaca_paper_snapshot(
        {
            "execution": {
                "mode": "paper",
                "broker": "alpaca",
                "alpaca": {"paper_base_url": "https://configured-paper.example/v2"},
            }
        },
        session=session,
        timeout=1.25,
    )

    assert snapshot["available"] is True
    assert [call["url"] for call in session.calls] == [
        "https://configured-paper.example/v2/account",
        "https://configured-paper.example/v2/positions",
        "https://configured-paper.example/v2/orders?status=all&limit=25",
        "https://configured-paper.example/v2/account/activities/FILL?page_size=100&direction=desc",
    ]


def test_alpaca_positions_normalize_weights_with_short_positions_negative() -> None:
    positions = dashboard_app._normalize_alpaca_positions(
        [
            {
                "symbol": "AAA",
                "qty": "10",
                "market_value": "1000",
                "side": "long",
                "current_price": "100",
                "avg_entry_price": "90",
                "unrealized_plpc": "0.10",
            },
            {
                "symbol": "BBB",
                "qty": "5",
                "market_value": "500",
                "side": "short",
                "current_price": "50",
                "avg_entry_price": "55",
                "unrealized_plpc": "-0.05",
            },
        ],
        {"portfolio_value": "10000"},
    )

    assert positions.to_dict(orient="records") == [
        {
            "ticker": "AAA",
            "quantity": 10.0,
            "market_value": 1000.0,
            "weight": 0.1,
            "side": "long",
            "current_price": 100.0,
            "avg_entry_price": 90.0,
            "unrealized_plpc": 0.1,
        },
        {
            "ticker": "BBB",
            "quantity": 5.0,
            "market_value": 500.0,
            "weight": -0.05,
            "side": "short",
            "current_price": 50.0,
            "avg_entry_price": 55.0,
            "unrealized_plpc": -0.05,
        },
    ]


def test_topbar_uses_paper_first_wording_when_paper_snapshot_available() -> None:
    fake_st = FakeStreamlit("Portfolio")
    paper_snapshot = {
        "available": True,
        "account": {"portfolio_value": "100000"},
        "positions": pd.DataFrame(),
        "orders": pd.DataFrame(),
        "detail": "Alpaca paper account snapshot",
    }

    _topbar(
        fake_st,
        "Portfolio",
        {"execution": {"mode": "paper", "broker": "alpaca", "allow_live_trading": False}},
        paper_snapshot=paper_snapshot,
    )

    html = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert "Paper first" in html
    assert "Alpaca paper" in html
    assert "Local first" not in html
    assert 'class="product"' in html
    assert 'class="topbar-status-strip"' in html


def test_autopilot_state_missing_returns_on_never_run_without_exception(tmp_path: Path) -> None:
    state = dashboard_app._autopilot_state(tmp_path / "missing" / "autopilot_state.json")

    assert state["available"] is True
    assert state["enabled"] is True
    assert state["mode"] == "paper"
    assert state["last_status"] == "never_run"
    assert state["current_step"] is None
    assert "not run yet" in state["detail"].lower()


def test_autopilot_state_corrupt_returns_unavailable_off_warning_without_exception(tmp_path: Path) -> None:
    path = tmp_path / "autopilot_state.json"
    path.write_text("{not-json", encoding="utf-8")

    state = dashboard_app._autopilot_state(path)

    assert state["available"] is False
    assert state["enabled"] is False
    assert state["mode"] == "paper"
    assert state["last_status"] == "unavailable"
    assert state["current_step"] is None
    assert "unavailable" in state["detail"].lower()


def test_topbar_renders_paper_autopilot_on_and_current_status(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "_market_clock_status",
        lambda _config: {"label": "Unknown", "detail": "Clock unavailable", "tone": "warn"},
    )
    fake_st = FakeStreamlit("Execution")

    _topbar(
        fake_st,
        "Execution",
        {"execution": {"mode": "paper", "broker": "alpaca", "allow_live_trading": False}},
        autopilot_state={
            "available": True,
            "enabled": True,
            "mode": "paper",
            "last_status": "running",
            "current_step": "submitting paper orders",
            "last_run_id": "run-1",
            "last_finished_at": None,
            "last_error": None,
            "detail": "Run in progress",
        },
    )

    html = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert 'class="autopilot"' in html
    assert 'class="state-button active" data-state="on" href="?page=Execution&amp;autopilot=on" target="_self"' in html
    assert 'class="state-button" data-state="off" href="?page=Execution&amp;autopilot=off" target="_self"' in html
    assert 'class="state-button" data-state="paused" href="?page=Execution&amp;autopilot=paused" target="_self"' in html
    assert "<span>Autopilot</span>" in html
    assert ">ON</b>" in html
    assert "submitting paper orders" in html


def test_autopilot_pending_orders_display_as_waiting_not_failure() -> None:
    pending_error = (
        "18 open Alpaca paper orders pending; waiting for broker fills before submitting another batch "
        "so duplicate paper orders are not created."
    )
    state = {
        "available": True,
        "enabled": True,
        "paused": False,
        "mode": "paper",
        "last_status": "open_orders_pending",
        "current_step": None,
        "last_run_id": "run-pending",
        "last_finished_at": None,
        "last_error": pending_error,
        "detail": pending_error,
    }

    assert dashboard_app._autopilot_active_state(state) == "on"
    assert dashboard_app._autopilot_tone(state) == "amber"

    by_label = {
        label: (value, detail, tone)
        for label, value, detail, tone in dashboard_app._autopilot_metric_cards(state)
    }
    assert by_label["Paper Autopilot"] == ("ON", "Paper autonomous execution", "amber")
    assert by_label["Last status"][0] == "Pending orders"
    assert by_label["Last status"][2] == "amber"
    assert by_label["Error"] == ("None", "No autopilot error", "green")
    assert pending_error not in by_label["Error"][0]

    assert dashboard_app._autopilot_active_state({**state, "enabled": False}) == "off"
    assert dashboard_app._autopilot_active_state({**state, "paused": True}) == "paused"


def test_topbar_renders_open_design_mode_switch_with_live_locked(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "_market_clock_status",
        lambda _config: {"label": "Closed", "detail": "Clock unavailable", "tone": "warn"},
    )
    fake_st = FakeStreamlit("Risk")

    _topbar(
        fake_st,
        "Risk",
        {"execution": {"mode": "paper", "broker": "alpaca", "allow_live_trading": False}},
        autopilot_state={
            "available": True,
            "enabled": False,
            "mode": "paper",
            "last_status": "paused",
            "current_step": None,
            "last_run_id": None,
            "last_finished_at": None,
            "last_error": None,
            "detail": "Paused by operator",
        },
    )

    html = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert 'class="mode-switch"' in html
    assert 'class="mode-button active" data-mode="paper" href="?page=Risk&amp;mode=paper" target="_self"' in html
    assert 'class="mode-button locked" data-mode="live" aria-disabled="true"' in html
    assert 'href="?page=Risk&amp;mode=live"' not in html
    assert ">Paper</span>" in html
    assert ">Live locked</span>" in html
    assert 'class="last-updated"' in html


def test_topbar_action_handler_updates_temp_autopilot_state_without_real_cache(tmp_path: Path) -> None:
    path = tmp_path / "autopilot_state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "paper",
                "enabled": True,
                "last_status": "running",
                "current_step": "submitting paper orders",
                "last_error": None,
                "runs": [],
            }
        ),
        encoding="utf-8",
    )

    off_notice = dashboard_app._apply_autopilot_action("off", path)
    off_state = json.loads(path.read_text(encoding="utf-8"))

    assert "off" in off_notice.lower()
    assert off_state["enabled"] is False
    assert off_state["last_status"] == "operator_disabled"
    assert off_state["current_step"] is None
    assert "dashboard" in off_state["manual_detail"].lower()

    paused_notice = dashboard_app._apply_autopilot_action("paused", path)
    paused_state = json.loads(path.read_text(encoding="utf-8"))

    assert "paused" in paused_notice.lower()
    assert paused_state["enabled"] is False
    assert paused_state["paused"] is True
    assert paused_state["last_status"] == "operator_paused"

    on_notice = dashboard_app._apply_autopilot_action("on", path)
    on_state = json.loads(path.read_text(encoding="utf-8"))

    assert "on" in on_notice.lower()
    assert on_state["enabled"] is True
    assert on_state["paused"] is False
    assert on_state["last_status"] == "never_run"

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "paper",
                "enabled": False,
                "last_status": "failed",
                "last_error": "risk gate blocked order",
                "runs": [],
            }
        ),
        encoding="utf-8",
    )

    dashboard_app._apply_autopilot_action("on", path)
    failed_state = json.loads(path.read_text(encoding="utf-8"))

    assert failed_state["enabled"] is True
    assert failed_state["last_status"] == "failed"


def test_injected_css_ports_open_design_metric_and_table_guards() -> None:
    fake_st = FakeStreamlit()

    dashboard_app._inject_css(fake_st)

    css = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert "--fg: #E7EDF5;" in css
    assert "--surface-2: #0B0F16;" in css
    assert ".metric-card {" in css
    assert "min-height: 74px;" in css
    assert "padding: 11px 12px 10px;" in css
    assert "overflow: visible;" in css
    assert "overflow-wrap: break-word;" in css
    assert "word-break: keep-all;" in css
    assert ".dark-table-wrap {" in css
    assert "table-layout: fixed;" in css
    assert "position: sticky;" in css


def test_injected_css_stacks_dashboard_chrome_on_mobile() -> None:
    fake_st = FakeStreamlit()

    dashboard_app._inject_css(fake_st)

    css = "\n".join(body for body, _unsafe in fake_st.markdown_calls)
    assert re.search(
        r"@media \(max-width: 980px\).*?\.topbar\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);",
        css,
        re.S,
    )
    assert re.search(
        r"@media \(max-width: 980px\).*?\.topbar-status-strip\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap;[^}]*overflow:\s*visible;",
        css,
        re.S,
    )
    assert re.search(
        r"@media \(max-width: 980px\).*?\.mode-switch\s*\{[^}]*width:\s*100%;",
        css,
        re.S,
    )
    assert re.search(
        r"@media \(max-width: 980px\).*?\.status-strip\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);",
        css,
        re.S,
    )
    assert re.search(
        r"@media \(max-width: 980px\).*?div\[data-testid=\"stHorizontalBlock\"\]\s*\{[^}]*display:\s*grid !important;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) !important;",
        css,
        re.S,
    )
    assert re.search(
        r"@media \(max-width: 640px\).*?\.topbar-status-strip\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap;",
        css,
        re.S,
    )


def test_execution_metric_helpers_group_cards_and_compact_status_text() -> None:
    long_error = (
        "open_orders_pending: broker returned an intentionally long detail string that would otherwise "
        "make a narrow metric card unreadable in the execution dashboard"
    )
    cards = dashboard_app._autopilot_metric_cards(
        {
            "available": True,
            "enabled": True,
            "mode": "paper",
            "last_status": "open_orders_pending",
            "current_step": None,
            "last_run_id": "run-789",
            "last_finished_at": "2026-05-08T15:00:00Z",
            "last_error": long_error,
            "detail": long_error,
        }
    )
    by_label = {label: (value, detail, tone) for label, value, detail, tone in cards}

    assert by_label["Last status"][0] == "Pending orders"
    assert "_" not in by_label["Last status"][0]
    assert by_label["Error"][0] == "None"
    assert long_error not in by_label["Error"][0]

    rows = dashboard_app._metric_card_rows(
        [(f"Card {index}", str(index), "detail", "blue") for index in range(11)],
        max_per_row=4,
    )

    assert [len(row) for row in rows] == [4, 4, 3]


def test_factor_heatmap_uses_all_factor_labels_and_readable_layout() -> None:
    frame = pd.DataFrame(
        [
            {
                "ticker": f"T{index:02d}",
                "composite_score": 100 - index,
                "momentum_score": index / 10,
                "value_score": index / 11,
                "quality_score": index / 12,
                "growth_score": index / 13,
                "revisions_score": index / 14,
                "short_interest_score": index / 15,
                "insider_score": index / 16,
                "institutional_score": index / 17,
            }
            for index in range(80)
        ]
    )

    fig = dashboard_app._factor_heatmap(frame)

    assert fig is not None
    heatmap = fig.data[0]
    assert list(heatmap.x) == [
        "Momentum",
        "Value",
        "Quality",
        "Growth",
        "Revisions",
        "Short Interest",
        "Insider",
        "Institutional",
    ]
    assert len(heatmap.y) <= 40
    assert heatmap.xgap <= 2
    assert heatmap.ygap <= 2
    assert fig.layout.height >= 620
    assert fig.layout.margin.l >= 72
    assert fig.layout.margin.b >= 72
    assert fig.layout.xaxis.tickangle == -25
    assert fig.layout.yaxis.automargin is True


def test_autopilot_metric_cards_return_running_labels_values_and_tones() -> None:
    cards = dashboard_app._autopilot_metric_cards(
        {
            "available": True,
            "enabled": True,
            "mode": "paper",
            "last_status": "running",
            "current_step": "analyzing candidates",
            "last_run_id": "run-123",
            "last_finished_at": None,
            "last_error": None,
            "detail": "Run in progress",
        }
    )

    by_label = {label: (value, detail, tone) for label, value, detail, tone in cards}
    assert by_label["Paper Autopilot"] == ("ON", "Paper autonomous execution", "amber")
    assert by_label["Last status"] == ("Running", "Run in progress", "amber")
    assert by_label["Current step"] == ("Analyzing candidates", "Active autopilot step", "amber")
    assert by_label["Last run"] == ("run-123", "No finish timestamp", "amber")
    assert by_label["Error"] == ("None", "No autopilot error", "green")


def test_autopilot_metric_cards_return_failed_labels_values_and_tones() -> None:
    cards = dashboard_app._autopilot_metric_cards(
        {
            "available": True,
            "enabled": True,
            "mode": "paper",
            "last_status": "failed",
            "current_step": None,
            "last_run_id": "run-456",
            "last_finished_at": "2026-05-08T14:30:00Z",
            "last_error": "risk gate blocked order",
            "detail": "Last run failed",
        }
    )

    by_label = {label: (value, detail, tone) for label, value, detail, tone in cards}
    assert by_label["Paper Autopilot"] == ("ON", "Paper autonomous execution", "pink")
    assert by_label["Last status"] == ("Failed", "Last run failed", "pink")
    assert by_label["Current step"] == ("Idle", "No active autopilot step", "pink")
    assert by_label["Last run"] == ("run-456", "Finished 2026-05-08T14:30:00Z", "pink")
    assert by_label["Error"] == ("Risk gate blocked order", "Last autopilot error", "pink")


def test_portfolio_snapshot_summarizes_local_book(tmp_path) -> None:
    db_path = tmp_path / "portfolio.sqlite3"
    with sqlite3.connect(db_path) as conn:
        pd.DataFrame(
            [
                {"ticker": "AAA", "weight": 0.25, "side": "long", "sector": "Tech"},
                {"ticker": "BBB", "weight": -0.15, "side": "short", "sector": "Energy"},
            ]
        ).to_sql("portfolio_positions", conn, index=False)
        pd.DataFrame(
            [
                {"ticker": "AAA", "status": "pending"},
                {"ticker": "BBB", "status": "approved"},
            ]
        ).to_sql("portfolio_approvals", conn, index=False)

    snapshot = _portfolio_snapshot({}, db_path)

    assert snapshot["metrics"]["positions"] == 2
    assert snapshot["metrics"]["long_positions"] == 1
    assert snapshot["metrics"]["short_positions"] == 1
    assert snapshot["metrics"]["gross_exposure"] == 0.4
    assert snapshot["metrics"]["net_exposure"] == 0.1
    assert snapshot["metrics"]["pending_approvals"] == 1
    assert snapshot["positions"][0]["ticker"] == "AAA"


def test_portfolio_snapshot_gives_jarvis_paper_orders_fills_and_realized_pnl(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_app, "_candidate_universe", lambda: pd.DataFrame())
    monkeypatch.setattr(
        dashboard_app,
        "_autopilot_state",
        lambda: {
            "available": True,
            "enabled": True,
            "mode": "paper",
            "last_status": "open_orders_pending",
            "last_error": "2 open Alpaca paper orders pending",
        },
    )
    db_path = tmp_path / "portfolio.sqlite3"
    paper_snapshot = {
        "available": True,
        "account": {"portfolio_value": "100000", "cash": "25000", "buying_power": "50000"},
        "positions": pd.DataFrame([{"ticker": "BBB", "weight": -0.05, "side": "short"}]),
        "orders": pd.DataFrame(
            [
                {"ticker": "CCC", "side": "buy", "qty": "3", "filled_qty": "0", "status": "accepted"},
                {"ticker": "AAA", "side": "sell", "qty": "10", "filled_qty": "10", "status": "filled"},
            ]
        ),
        "activities": pd.DataFrame(
            [
                {"ticker": "AAA", "side": "sell", "quantity": 10.0, "price": 100.0, "date": "2026-05-15T14:30:00Z"},
                {"ticker": "AAA", "side": "buy", "quantity": 10.0, "price": 90.0, "date": "2026-05-14T14:30:00Z"},
            ]
        ),
    }

    snapshot = _portfolio_snapshot(
        {"execution": {"mode": "paper", "broker": "alpaca"}},
        db_path,
        paper_snapshot=paper_snapshot,
    )

    assert snapshot["positions_source"] == "Alpaca paper positions"
    assert snapshot["paper_account"]["portfolio_value"] == 100000.0
    assert snapshot["paper_orders"][0]["ticker"] == "CCC"
    assert snapshot["paper_open_orders"][0]["ticker"] == "CCC"
    assert snapshot["paper_fills"][0]["ticker"] == "AAA"
    assert snapshot["paper_round_trips"][0]["ticker"] == "AAA"
    assert snapshot["paper_round_trips"][0]["realized_pnl"] == 100.0
    assert snapshot["paper_trade_metrics"]["fill_count"] == 2
    assert snapshot["paper_trade_metrics"]["round_trip_count"] == 1
    assert snapshot["paper_trade_metrics"]["total_realized_pnl"] == 100.0
    assert snapshot["autopilot"]["last_status"] == "open_orders_pending"


def test_portfolio_chat_prompt_uses_snapshot_and_history() -> None:
    snapshot = {"metrics": {"positions": 2}, "positions": [{"ticker": "AAA", "weight": 0.25}]}
    history = [{"role": "user", "content": "Resumo"}, {"role": "assistant", "content": "Portfolio net long."}]

    prompt = _portfolio_chat_prompt("Why is AAA in the book?", snapshot, history)

    assert "Why is AAA in the book?" in prompt
    assert '"ticker": "AAA"' in prompt
    assert "Portfolio net long." in prompt
    assert "If the snapshot does not contain enough data" in prompt


def test_trim_chat_history_preserves_last_six_turns() -> None:
    history = [{"role": "user", "content": str(i)} for i in range(14)]

    trimmed = _trim_chat_history(history)

    assert len(trimmed) == 12
    assert trimmed[0]["content"] == "2"
    assert trimmed[-1]["content"] == "13"


def test_portfolio_chat_history_renders_reference_style_response() -> None:
    html = _portfolio_chat_history_html(
        [
            {"role": "user", "content": "Why is AEP my top long?"},
            {"role": "assistant", "content": "AEP has <strong>quality</strong> and momentum.\nUse local data."},
        ]
    )

    assert 'class="jarvis-response"' in html
    assert "> WHY IS AEP MY TOP LONG?" in html
    assert "&lt;strong&gt;quality&lt;/strong&gt;" in html
    assert "<br>" in html


def test_candidate_review_frame_merges_latest_decisions() -> None:
    candidates = pd.DataFrame(
        [
            {"ticker": "AAA", "combined_score": 95, "gics_sector": "Tech", "long_candidate": True},
            {"ticker": "BBB", "combined_score": 12, "gics_sector": "Health", "short_candidate": True},
        ]
    )
    reviews = pd.DataFrame(
        [
            {"ticker": "AAA", "status": "approved", "reason": "quality", "decided_at": "2026-05-09 10:00:00"},
        ]
    )

    frame = dashboard_app._candidate_review_frame(candidates, reviews)

    by_ticker = frame.set_index("ticker")
    assert by_ticker.loc["AAA", "review_status"] == "approved"
    assert by_ticker.loc["AAA", "review_reason"] == "quality"
    assert by_ticker.loc["BBB", "review_status"] == "pending"
    assert by_ticker.loc["BBB", "suggested_side"] == "short"


def test_candidate_analysis_prompt_includes_selected_candidate_and_decision_rules() -> None:
    prompt = dashboard_app._candidate_analysis_prompt(
        {"ticker": "AAA", "combined_score": 95, "suggested_side": "long", "review_status": "pending"},
        "Should I approve it?",
    )

    assert '"ticker": "AAA"' in prompt
    assert "Approve, reject, or watch" in prompt
    assert "Should I approve it?" in prompt


def test_candidate_analysis_ui_copy_uses_codex_not_claude() -> None:
    assert dashboard_app._candidate_analysis_expander_label(
        {"ticker": "AAA", "analysis_summaries": "Cached forensic view"}
    ) == "AAA - Codex analysis"
    assert dashboard_app._candidate_analysis_expander_label({"ticker": "BBB"}) == "BBB - no Codex analysis yet"
    assert dashboard_app._candidate_analysis_button_label({"ticker": "AAA"}) == "Generate Codex analysis"
    assert (
        dashboard_app._candidate_analysis_button_label({"ticker": "AAA", "analysis_summaries": "Cached forensic view"})
        == "Regenerate Codex analysis"
    )

    html = dashboard_app._candidate_analysis_html({"ticker": "AAA", "display_score": 95})

    assert "Codex analysis" in html
    assert "Claude" not in html


def test_generate_candidate_analysis_persists_result_for_board(tmp_path, monkeypatch) -> None:
    output_path = tmp_path / "analysis_results_latest.csv"
    candidate = {"ticker": "AES", "combined_score": 5, "short_candidate": True, "gics_sector": "Utilities"}

    def fake_ask(config, selected, question):
        assert config == {"analysis": {"provider": "codex"}}
        assert selected["ticker"] == "AES"
        assert "AES" in question
        return "AES Codex generated forensic analysis."

    monkeypatch.setattr(dashboard_app, "_ask_candidate_agent", fake_ask)

    result = dashboard_app._generate_candidate_analysis(
        {"analysis": {"provider": "codex"}},
        candidate,
        output_path=output_path,
    )

    persisted = pd.read_csv(output_path)

    assert result == "AES Codex generated forensic analysis."
    assert persisted.loc[0, "ticker"] == "AES"
    assert persisted.loc[0, "analysis_summaries"] == "AES Codex generated forensic analysis."
    assert persisted.loc[0, "analysis_count"] == 1
    assert persisted.loc[0, "analyzers_run"] == "codex"


def test_generate_letter_summary_sends_letter_to_codex_and_writes_markdown(tmp_path) -> None:
    letter = tmp_path / "lp_letter_2026-05-15.md"
    letter.write_text("# Letter\n\nBook was net flat and risk was reduced.", encoding="utf-8")
    output_path = tmp_path / "jarvis_summary_lp_report_2026-05-15.md"
    calls: list[str] = []

    class FakeClient:
        def complete(self, prompt: str) -> str:
            calls.append(prompt)
            return "Resumo: risco reduzido e livro neutro."

    summary_path = dashboard_app._generate_letter_summary(
        {"analysis": {"provider": "codex"}},
        letter,
        output_path=output_path,
        client=FakeClient(),
    )

    assert summary_path == output_path
    assert summary_path.read_text(encoding="utf-8") == "Resumo: risco reduzido e livro neutro.\n"
    assert "Book was net flat and risk was reduced." in calls[0]
    assert "Nao inventes factos" in calls[0]


def test_merge_candidate_analysis_adds_cached_summary_without_overriding_rank_score() -> None:
    scored = pd.DataFrame(
        [
            {"ticker": "AAA", "composite_score": 100, "long_candidate": True},
            {"ticker": "BBB", "composite_score": 5, "short_candidate": True},
        ]
    )
    analysis = pd.DataFrame(
        [
            {"ticker": "aaa", "combined_score": 88, "analysis_summaries": "Cached forensic view"},
        ]
    )

    merged = dashboard_app._merge_candidate_analysis(scored, analysis)

    by_ticker = merged.set_index("ticker")
    assert by_ticker.loc["AAA", "analysis_summaries"] == "Cached forensic view"
    assert by_ticker.loc["AAA", "composite_score"] == 100
    assert "combined_score" not in merged.columns


def test_candidate_board_groups_rank_top_longs_and_lowest_shorts() -> None:
    candidates = pd.DataFrame(
        [
            {"ticker": "L2", "combined_score": 80, "long_candidate": True},
            {"ticker": "S2", "combined_score": 10, "short_candidate": True},
            {"ticker": "L1", "combined_score": 95, "long_candidate": True},
            {"ticker": "S1", "combined_score": 1, "short_candidate": True},
            {"ticker": "M1", "combined_score": 50},
        ]
    )
    frame = dashboard_app._candidate_review_frame(candidates, pd.DataFrame())

    groups = dashboard_app._candidate_board_groups(frame, limit=1)

    assert groups["long"]["ticker"].tolist() == ["L1"]
    assert groups["short"]["ticker"].tolist() == ["S1"]


def test_candidate_card_html_renders_reference_style_snapshot() -> None:
    html = dashboard_app._candidate_card_html(
        {
            "ticker": "AEP",
            "gics_sector": "Utilities",
            "display_score": 100,
            "price": 130.16,
            "weight": 0.038,
            "beta": 0.20,
            "quality_piotroski": 6,
            "quality_altman_z": 1.0,
            "review_status": "approved",
        },
        side="long",
    )

    assert 'class="candidate-card candidate-card-long status-approved"' in html
    assert ">AEP<" in html
    assert "Utilities" in html
    assert "$130" in html
    assert "3.8%" in html
    assert "beta 0.20" in html
    assert "Piotroski 6/9" in html
    assert "Altman-Z 1.0" in html


def test_portfolio_metric_cards_match_reference_data_fields(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "portfolio.sqlite3"
    with sqlite3.connect(db_path) as conn:
        pd.DataFrame(
            [
                {"ticker": "AAA", "weight": 0.10},
                {"ticker": "BBB", "weight": -0.05},
            ]
        ).to_sql("portfolio_positions", conn, index=False)
        pd.DataFrame(
            [
                {"ticker": "AAA", "is_ceo_cfo": 1, "is_open_market_purchase": 1, "cluster_buy": 1},
                {"ticker": "BBB", "is_ceo_cfo": 0, "is_open_market_purchase": 1, "cluster_buy": 0},
            ]
        ).to_sql("insider_transactions", conn, index=False)
        pd.DataFrame([{"ticker": "^VIX", "date": "2026-05-05", "close": 16.9}]).to_sql(
            "daily_prices", conn, index=False
        )
        pd.DataFrame(
            [
                {"ticker": "AAA", "earnings_date": (date.today() + timedelta(days=3)).isoformat()},
                {"ticker": "BBB", "earnings_date": "2026-06-20"},
            ]
        ).to_sql("earnings_calendar", conn, index=False)

    monkeypatch.setattr(
        dashboard_app,
        "_candidate_universe",
        lambda: pd.DataFrame(
            [
                {"ticker": "AAA", "side": "long", "crowding_zscore": 0.5},
                {"ticker": "BBB", "side": "short", "crowding_zscore": 2.5},
                {"ticker": "CCC", "side": "long", "crowding_zscore": 0.1},
            ]
        ),
    )

    cards = _portfolio_metric_cards({"scoring": {"crowding": {"zscore_warning_threshold": 2.0}}}, db_path)
    values = {label: value for label, value, _detail, _tone in cards}

    assert list(values) == [
        "Universe",
        "Long cand.",
        "Short cand.",
        "Positions",
        "Crowding",
        "Insider events",
        "CEO/CFO buys",
        "Cluster buys",
        "VIX",
        "Earnings 7d",
    ]
    assert values["Universe"] == "3"
    assert values["Long cand."] == "2"
    assert values["Short cand."] == "1"
    assert values["Positions"] == "2"
    assert values["Crowding"] == "1"
    assert values["Insider events"] == "2"
    assert values["CEO/CFO buys"] == "1"
    assert values["Cluster buys"] == "1"
    assert values["VIX"] == "16.9"
    assert values["Earnings 7d"] == "1"


def test_portfolio_metric_cards_count_paper_positions_when_snapshot_available(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "portfolio.sqlite3"
    with sqlite3.connect(db_path) as conn:
        pd.DataFrame([{"ticker": "LOCAL", "weight": 0.10}]).to_sql("portfolio_positions", conn, index=False)

    monkeypatch.setattr(dashboard_app, "_candidate_universe", lambda: pd.DataFrame())
    paper_snapshot = {
        "available": True,
        "positions": pd.DataFrame(
            [
                {"ticker": "AAA", "weight": 0.10},
                {"ticker": "BBB", "weight": -0.05},
                {"ticker": "CCC", "weight": 0.03},
            ]
        ),
    }

    cards = _portfolio_metric_cards(
        {"execution": {"mode": "paper", "broker": "alpaca"}},
        db_path,
        paper_snapshot=paper_snapshot,
    )
    values = {label: (value, detail) for label, value, detail, _tone in cards}

    assert values["Positions"] == ("3", "Alpaca paper positions")


def test_reporting_artifact_snapshot_reads_l7_outputs(tmp_path) -> None:
    output = tmp_path / "output"
    reports = output / "reports"
    reports.mkdir(parents=True)
    pd.DataFrame(
        [{"date": "2026-05-07", "total_return": 0.01, "alpha_residual": 0.002}]
    ).to_csv(output / "daily_attribution.csv", index=False)
    (reports / "tear_sheet.md").write_text("# Tear Sheet\n", encoding="utf-8")

    snapshot = _reporting_artifact_snapshot(tmp_path)

    assert snapshot["daily_attribution_rows"] == 1
    assert snapshot["latest_alpha_residual"] == 0.002
    assert snapshot["report_count"] == 1
    assert snapshot["latest_report"] == "tear_sheet.md"
