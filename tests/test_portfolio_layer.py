from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from common.config import PROJECT_ROOT
from common.db import table_exists
from portfolio.mvo_optimizer import score_to_expected_return
from portfolio.factor_exposure import factor_exposure_warnings
from portfolio.optimizer import build_conviction_tilt_portfolio
from portfolio import preferences as portfolio_preferences
from portfolio.rebalance import generate_rebalance_orders
from portfolio.rebalance_schedule import rebalance_advisories
from portfolio.state import PortfolioState
from portfolio.transaction_costs import estimate_transaction_cost
from run_portfolio import _configured_non_tradable_tickers, _normalize_method, run_portfolio_pipeline


def test_score_to_expected_return_maps_scores_to_prompt_range() -> None:
    assert score_to_expected_return(100) == pytest.approx(0.15)
    assert score_to_expected_return(50) == pytest.approx(0.0)
    assert score_to_expected_return(0) == pytest.approx(-0.15)


def test_portfolio_preferences_persist_normalized_optimizer_method(tmp_path: Path) -> None:
    preferences_path = tmp_path / "portfolio_preferences.json"

    saved = portfolio_preferences.save_portfolio_preferences(
        "conviction",
        config={"portfolio": {"optimizer": {"default_method": "mvo", "allow_mvo": True}}},
        path=preferences_path,
    )
    loaded = portfolio_preferences.load_portfolio_preferences(
        config={"portfolio": {"optimizer": {"default_method": "mvo", "allow_mvo": True}}},
        path=preferences_path,
    )

    assert saved["optimizer_method"] == "conviction_tilt"
    assert loaded["optimizer_method"] == "conviction_tilt"


def test_run_portfolio_uses_saved_optimizer_preference_when_method_omitted(tmp_path: Path, monkeypatch) -> None:
    preferences_path = tmp_path / "portfolio_preferences.json"
    monkeypatch.setattr(portfolio_preferences, "DEFAULT_PREFERENCES_PATH", preferences_path)
    portfolio_preferences.save_portfolio_preferences(
        "mvo",
        config={"portfolio": {"optimizer": {"default_method": "conviction_tilt", "allow_mvo": True}}},
    )

    method = _normalize_method(
        None,
        {"portfolio": {"optimizer": {"default_method": "conviction_tilt", "allow_mvo": True}}},
    )

    assert method == "mvo"


def test_run_portfolio_explicit_method_overrides_saved_optimizer_preference(tmp_path: Path, monkeypatch) -> None:
    preferences_path = tmp_path / "portfolio_preferences.json"
    monkeypatch.setattr(portfolio_preferences, "DEFAULT_PREFERENCES_PATH", preferences_path)
    portfolio_preferences.save_portfolio_preferences(
        "mvo",
        config={"portfolio": {"optimizer": {"default_method": "conviction_tilt", "allow_mvo": True}}},
    )

    method = _normalize_method(
        "conviction",
        {"portfolio": {"optimizer": {"default_method": "conviction_tilt", "allow_mvo": True}}},
    )

    assert method == "conviction_tilt"


def test_allow_mvo_false_blocks_saved_mvo_optimizer_preference(tmp_path: Path, monkeypatch) -> None:
    preferences_path = tmp_path / "portfolio_preferences.json"
    monkeypatch.setattr(portfolio_preferences, "DEFAULT_PREFERENCES_PATH", preferences_path)
    portfolio_preferences.save_portfolio_preferences(
        "mvo",
        config={"portfolio": {"optimizer": {"default_method": "mvo", "allow_mvo": True}}},
    )

    method = _normalize_method(
        None,
        {"portfolio": {"optimizer": {"default_method": "mvo", "allow_mvo": False}}},
    )
    saved = portfolio_preferences.save_portfolio_preferences(
        "mvo",
        config={"portfolio": {"optimizer": {"default_method": "mvo", "allow_mvo": False}}},
        path=preferences_path,
    )

    assert method == "conviction_tilt"
    assert saved["optimizer_method"] == "conviction_tilt"


def test_transaction_cost_model_uses_spread_and_market_impact_inputs() -> None:
    cost = estimate_transaction_cost(
        quantity=100,
        price=100,
        commission_bps=0,
        avg_daily_range_bps=200,
        adv_notional=1_000_000,
        daily_vol_bps=300,
        market_impact_coef=0.10,
    )

    assert cost.spread_cost_bps == pytest.approx(10.0)
    assert cost.market_impact_bps == pytest.approx(3.0)
    assert cost.total_bps == pytest.approx(13.0)


def test_rebalance_advisories_include_earnings_fomc_and_options_expiry() -> None:
    positions = pd.DataFrame({"ticker": ["AAA"], "earnings_date": ["2026-06-17"]})

    warnings = rebalance_advisories(positions, current_date="2026-06-16")

    kinds = {warning["kind"] for warning in warnings}
    assert {"earnings_blackout", "fomc_window", "options_expiration_window"}.issubset(kinds)
    assert all(warning["blocks_trading"] is False for warning in warnings)


def test_portfolio_state_schema_tracks_prompt_fields(tmp_path: Path) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_state_{tmp_path.name}.sqlite3"
    if db_path.exists():
        db_path.unlink()
    try:
        state = PortfolioState(db_path)
        with state.connect() as connection:
            state.initialize(connection)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(portfolio_positions)").fetchall()}
            assert table_exists(connection, "position_approvals")

        assert {
            "entry_price",
            "entry_date",
            "current_price",
            "unrealized_pnl",
            "sector",
            "factor_scores_at_entry",
        }.issubset(columns)
    finally:
        if db_path.exists():
            db_path.unlink()


def test_portfolio_state_records_latest_candidate_review(tmp_path: Path) -> None:
    db_path = PROJECT_ROOT / "cache" / f"candidate_review_state_{tmp_path.name}.sqlite3"
    if db_path.exists():
        db_path.unlink()

    try:
        state = PortfolioState(db_path)
        state.record_candidate_review("aaa", "approved", side="long", reason="strong setup")
        state.record_candidate_review("AAA", "rejected", side="long", reason="crowded")

        reviews = state.get_candidate_reviews()

        assert len(reviews) == 1
        assert reviews.iloc[0]["ticker"] == "AAA"
        assert reviews.iloc[0]["status"] == "rejected"
        assert reviews.iloc[0]["reason"] == "crowded"
    finally:
        if db_path.exists():
            db_path.unlink()


def test_portfolio_state_clears_candidate_review(tmp_path: Path) -> None:
    db_path = PROJECT_ROOT / "cache" / f"candidate_review_clear_{tmp_path.name}.sqlite3"
    if db_path.exists():
        db_path.unlink()

    try:
        state = PortfolioState(db_path)
        state.record_candidate_review("aaa", "approved", side="long", reason="strong setup")
        deleted = state.clear_candidate_review("AAA")

        reviews = state.get_candidate_reviews()

        assert deleted == 1
        assert reviews.empty
    finally:
        if db_path.exists():
            db_path.unlink()


def test_run_portfolio_pipeline_persists_only_when_not_whatif(tmp_path: Path) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_pipeline_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"portfolio_pipeline_{tmp_path.name}.csv"
    if db_path.exists():
        db_path.unlink()
    if output_path.exists():
        output_path.unlink()

    candidates = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC", "DDD"],
            "combined_score": [95, 88, 12, 5],
            "gics_sector": ["Tech", "Health", "Tech", "Health"],
            "price": [100, 50, 25, 20],
            "beta": [1.0, 0.9, 1.1, 0.8],
        }
    )
    config = {
        "portfolio": {
            "target_longs": 1,
            "target_shorts": 1,
            "long_gross_exposure": 0.25,
            "short_gross_exposure": 0.25,
            "min_position_weight": 0.01,
            "max_position_weight": 0.30,
            "max_sector_gross_weight": 0.50,
            "beta_cap_abs": None,
        }
    }

    try:
        whatif_payload = run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            method="conviction_tilt",
            whatif=True,
        )
        assert whatif_payload["positions"] == 2
        assert PortfolioState(db_path).get_positions().empty

        live_payload = run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            method="conviction_tilt",
            whatif=False,
        )
        assert live_payload["positions"] == 2
        assert output_path.exists()
        assert len(PortfolioState(db_path).get_positions()) == 2
    finally:
        if db_path.exists():
            db_path.unlink()
        if output_path.exists():
            output_path.unlink()


def test_run_portfolio_pipeline_uses_only_approved_candidate_reviews_when_present(tmp_path: Path) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_candidate_gate_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"portfolio_candidate_gate_{tmp_path.name}.csv"
    orders_path = PROJECT_ROOT / "output" / f"portfolio_candidate_gate_orders_{tmp_path.name}.csv"
    for path in [db_path, output_path, orders_path]:
        if path.exists():
            path.unlink()

    candidates = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC", "DDD"],
            "combined_score": [95, 88, 12, 5],
            "gics_sector": ["Tech", "Health", "Tech", "Health"],
            "price": [100, 50, 25, 20],
            "beta": [1.0, 0.9, 1.1, 0.8],
        }
    )
    config = {
        "portfolio": {
            "target_longs": 1,
            "target_shorts": 0,
            "long_gross_exposure": 0.25,
            "short_gross_exposure": 0.0,
            "min_position_weight": 0.01,
            "max_position_weight": 0.30,
            "max_sector_gross_weight": 0.50,
            "beta_cap_abs": None,
        }
    }

    try:
        state = PortfolioState(db_path)
        state.record_candidate_review("AAA", "approved", side="long")
        state.record_candidate_review("BBB", "rejected", side="long")
        state.record_candidate_review("CCC", "watch", side="short")

        payload = run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            orders_output_path=orders_path,
            method="conviction_tilt",
            whatif=True,
        )

        target = pd.read_csv(output_path)
        assert payload["positions"] == 1
        assert target["ticker"].tolist() == ["AAA"]
        assert set(target.get("candidate_review_status", [])) == {"approved"}
    finally:
        for path in [db_path, output_path, orders_path]:
            if path.exists():
                path.unlink()


def test_run_portfolio_pipeline_mvo_uses_daily_price_covariance(tmp_path: Path) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_mvo_prices_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"portfolio_mvo_prices_{tmp_path.name}.csv"
    orders_path = PROJECT_ROOT / "output" / f"portfolio_mvo_prices_orders_{tmp_path.name}.csv"
    for path in [db_path, output_path, orders_path]:
        if path.exists():
            path.unlink()

    candidates = pd.DataFrame(
        {
            "ticker": ["aaa", "BBB", "CCC", "DDD"],
            "combined_score": [95, 88, 12, 5],
            "gics_sector": ["Tech", "Health", "Tech", "Health"],
            "price": [100, 50, 25, 20],
        }
    )
    config = {
        "portfolio": {
            "target_longs": 1,
            "target_shorts": 1,
            "long_gross_exposure": 0.25,
            "short_gross_exposure": 0.25,
            "min_position_weight": 0.01,
            "max_position_weight": 0.30,
            "max_sector_gross_weight": 0.50,
            "beta_cap_abs": None,
            "covariance_lookback_days": 8,
            "optimizer": {"allow_mvo": True, "mvo_risk_aversion": 3.0, "transaction_cost_bps": 10},
        }
    }

    try:
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                """
                CREATE TABLE daily_prices (
                    ticker TEXT,
                    date TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    adj_close REAL,
                    volume INTEGER,
                    source TEXT,
                    updated_at TEXT
                )
                """
            )
            rows = []
            base_prices = {"AAA": 100.0, "BBB": 50.0, "CCC": 25.0, "DDD": 20.0}
            for day in range(10):
                date = f"2026-01-{day + 1:02d}"
                for offset, (ticker, base_price) in enumerate(base_prices.items()):
                    close = base_price * (1.0 + 0.002 * day + 0.0003 * offset * day)
                    adj_close = close if ticker != "DDD" else None
                    rows.append((ticker, date, close, close, close, close, adj_close, 1000, "test", "2026-01-15"))
            connection.executemany(
                """
                INSERT INTO daily_prices(ticker, date, open, high, low, close, adj_close, volume, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        finally:
            connection.close()

        payload = run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            orders_output_path=orders_path,
            method="mvo",
            whatif=True,
        )

        assert payload["positions"] == 2
        target = pd.read_csv(output_path)
        assert "optimizer" in target.columns
        assert set(target["optimizer"]) == {"mvo"}
    finally:
        for path in [db_path, output_path, orders_path]:
            if path.exists():
                path.unlink()


def test_run_portfolio_pipeline_mvo_falls_back_when_selected_ticker_lacks_covariance(tmp_path: Path) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_mvo_missing_cov_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"portfolio_mvo_missing_cov_{tmp_path.name}.csv"
    orders_path = PROJECT_ROOT / "output" / f"portfolio_mvo_missing_cov_orders_{tmp_path.name}.csv"
    for path in [db_path, output_path, orders_path]:
        if path.exists():
            path.unlink()

    candidates = pd.DataFrame(
        {
            "ticker": ["AAA", "NOCOV", "BBB", "DDD"],
            "combined_score": [95, 90, 12, 5],
            "gics_sector": ["Tech", "Tech", "Health", "Health"],
            "price": [100, 80, 50, 20],
        }
    )
    config = {
        "portfolio": {
            "target_longs": 2,
            "target_shorts": 1,
            "long_gross_exposure": 0.30,
            "short_gross_exposure": 0.20,
            "min_position_weight": 0.01,
            "max_position_weight": 0.30,
            "max_sector_gross_weight": 0.50,
            "beta_cap_abs": None,
            "covariance_lookback_days": 8,
            "optimizer": {"allow_mvo": True, "mvo_risk_aversion": 3.0, "transaction_cost_bps": 10},
        }
    }

    try:
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                """
                CREATE TABLE daily_prices (
                    ticker TEXT,
                    date TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    adj_close REAL,
                    volume INTEGER,
                    source TEXT,
                    updated_at TEXT
                )
                """
            )
            rows = []
            base_prices = {"AAA": 100.0, "BBB": 50.0, "DDD": 20.0}
            for day in range(10):
                date = f"2026-01-{day + 1:02d}"
                for offset, (ticker, base_price) in enumerate(base_prices.items()):
                    close = base_price * (1.0 + 0.002 * day + 0.0003 * offset * day)
                    rows.append((ticker, date, close, close, close, close, close, 1000, "test", "2026-01-15"))
            connection.executemany(
                """
                INSERT INTO daily_prices(ticker, date, open, high, low, close, adj_close, volume, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        finally:
            connection.close()

        payload = run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            orders_output_path=orders_path,
            method="mvo",
            whatif=True,
        )

        assert payload["positions"] == 3
        target = pd.read_csv(output_path)
        assert set(target["ticker"]) == {"AAA", "NOCOV", "DDD"}
        assert set(target["optimizer"]) == {"conviction_tilt_fallback"}
        assert target["mvo_message"].str.contains("missing covariance coverage", case=False).all()
        assert target["mvo_message"].str.contains("NOCOV").all()
    finally:
        for path in [db_path, output_path, orders_path]:
            if path.exists():
                path.unlink()


def test_run_portfolio_pipeline_uses_alpaca_paper_positions_over_local_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_alpaca_current_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"portfolio_alpaca_current_{tmp_path.name}.csv"
    orders_path = PROJECT_ROOT / "output" / f"portfolio_alpaca_current_orders_{tmp_path.name}.csv"
    for path in [db_path, output_path, orders_path]:
        if path.exists():
            path.unlink()

    candidates = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC", "DDD"],
            "combined_score": [95, 88, 12, 5],
            "gics_sector": ["Tech", "Health", "Tech", "Health"],
            "price": [100, 50, 25, 20],
        }
    )
    config = {
        "execution": {"mode": "paper", "broker": "alpaca"},
        "portfolio": {
            "target_longs": 1,
            "target_shorts": 1,
            "long_gross_exposure": 0.25,
            "short_gross_exposure": 0.25,
            "min_position_weight": 0.01,
            "max_position_weight": 0.30,
            "max_sector_gross_weight": 0.50,
            "beta_cap_abs": None,
        },
    }

    try:
        PortfolioState(db_path).set_positions(pd.DataFrame({"ticker": ["GHOST"], "weight": [0.50], "price": [10.0]}))

        import run_portfolio

        monkeypatch.setattr(
            run_portfolio,
            "_alpaca_paper_current_weights",
            lambda config: pd.Series({"AAA": 0.10}, dtype="float64"),
        )

        run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            orders_output_path=orders_path,
            method="conviction_tilt",
            whatif=True,
            current_source="alpaca-paper",
        )

        orders = pd.read_csv(orders_path)
        assert "GHOST" not in set(orders["ticker"])
        aaa = orders.loc[orders["ticker"] == "AAA"].iloc[0]
        assert aaa["current_weight"] == pytest.approx(0.10)
    finally:
        for path in [db_path, output_path, orders_path]:
            if path.exists():
                path.unlink()


def test_generate_rebalance_orders_uses_current_position_price_when_closing_removed_name() -> None:
    from portfolio.rebalance import generate_rebalance_orders

    current = pd.Series({"AAPL": 0.20}, dtype="float64")
    current.attrs["quantities"] = {"AAPL": 40}
    current.attrs["prices"] = {"AAPL": 50}

    orders = generate_rebalance_orders(current, pd.DataFrame(), nav=10_000)

    row = orders.iloc[0]
    assert row["ticker"] == "AAPL"
    assert row["side"] == "sell"
    assert row["price"] == pytest.approx(50)
    assert row["shares"] == pytest.approx(40)
    assert row["current_quantity"] == pytest.approx(40)


def test_run_portfolio_pipeline_can_override_candidate_review_gate_for_autopilot(tmp_path: Path) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_gate_override_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"portfolio_gate_override_{tmp_path.name}.csv"
    orders_path = PROJECT_ROOT / "output" / f"portfolio_gate_override_orders_{tmp_path.name}.csv"
    for path in [db_path, output_path, orders_path]:
        if path.exists():
            path.unlink()

    candidates = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC", "DDD"],
            "combined_score": [95, 88, 12, 5],
            "gics_sector": ["Tech", "Health", "Tech", "Health"],
            "price": [100, 50, 25, 20],
        }
    )
    config = {
        "execution": {"mode": "paper", "broker": "alpaca"},
        "portfolio": {
            "target_longs": 1,
            "target_shorts": 1,
            "long_gross_exposure": 0.25,
            "short_gross_exposure": 0.25,
            "min_position_weight": 0.01,
            "max_position_weight": 0.30,
            "max_sector_gross_weight": 0.50,
            "beta_cap_abs": None,
        },
    }

    try:
        state = PortfolioState(db_path)
        state.record_candidate_review("AAA", "approved", side="long")

        approved_only = run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            orders_output_path=orders_path,
            method="conviction_tilt",
            whatif=True,
            current_source="local",
        )
        assert approved_only["positions"] == 1

        exclude_rejected = run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            orders_output_path=orders_path,
            method="conviction_tilt",
            whatif=True,
            current_source="local",
            candidate_review_gate="exclude_rejected",
        )
        assert exclude_rejected["positions"] == 2
    finally:
        for path in [db_path, output_path, orders_path]:
            if path.exists():
                path.unlink()


def test_alpaca_paper_current_weights_uses_configured_paper_url_when_env_base_url_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import run_portfolio

    class FakeResponse:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.responses = {
                "https://configured-paper.example/v2/account": FakeResponse({"portfolio_value": "100000"}),
                "https://configured-paper.example/v2/positions": FakeResponse(
                    [{"symbol": "AAA", "market_value": "1000", "side": "long"}]
                ),
            }

        def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
            self.calls.append({"url": url, "headers": headers, "timeout": timeout})
            return self.responses[url]

    fake_session = FakeSession()
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets/v2")
    monkeypatch.setattr(run_portfolio.requests, "Session", lambda: fake_session)

    weights = run_portfolio._alpaca_paper_current_weights(
        {
            "execution": {
                "mode": "paper",
                "broker": "alpaca",
                "alpaca": {"paper_base_url": "https://configured-paper.example/v2"},
            }
        },
        timeout=1.25,
    )

    assert weights["AAA"] == pytest.approx(0.01)
    assert [call["url"] for call in fake_session.calls] == [
        "https://configured-paper.example/v2/account",
        "https://configured-paper.example/v2/positions",
    ]
    assert fake_session.calls[0]["headers"] == {
        "APCA-API-KEY-ID": "test-key",
        "APCA-API-SECRET-KEY": "test-secret",
    }
    assert fake_session.calls[0]["timeout"] == 1.25


def test_run_portfolio_pipeline_keeps_local_current_state_by_default_for_non_paper(
    tmp_path: Path,
) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_local_current_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"portfolio_local_current_{tmp_path.name}.csv"
    orders_path = PROJECT_ROOT / "output" / f"portfolio_local_current_orders_{tmp_path.name}.csv"
    for path in [db_path, output_path, orders_path]:
        if path.exists():
            path.unlink()

    candidates = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC", "DDD"],
            "combined_score": [95, 88, 12, 5],
            "sector": ["Tech", "Health", "Tech", "Health"],
            "price": [100, 50, 25, 20],
        }
    )
    config = {
        "execution": {"mode": "paper", "broker": "paper"},
        "portfolio": {
            "target_longs": 1,
            "target_shorts": 1,
            "long_gross_exposure": 0.25,
            "short_gross_exposure": 0.25,
            "min_position_weight": 0.01,
            "max_position_weight": 0.30,
            "max_sector_gross_weight": 0.50,
            "beta_cap_abs": None,
        },
    }

    try:
        PortfolioState(db_path).set_positions(pd.DataFrame({"ticker": ["GHOST"], "weight": [0.50], "price": [10.0]}))

        run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            orders_output_path=orders_path,
            method="conviction_tilt",
            whatif=True,
        )

        orders = pd.read_csv(orders_path)
        ghost = orders.loc[orders["ticker"] == "GHOST"].iloc[0]
        assert ghost["current_weight"] == pytest.approx(0.50)
    finally:
        for path in [db_path, output_path, orders_path]:
            if path.exists():
                path.unlink()


def test_run_portfolio_pipeline_forced_alpaca_source_refuses_missing_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_alpaca_missing_creds_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"portfolio_alpaca_missing_creds_{tmp_path.name}.csv"
    orders_path = PROJECT_ROOT / "output" / f"portfolio_alpaca_missing_creds_orders_{tmp_path.name}.csv"
    for path in [db_path, output_path, orders_path]:
        if path.exists():
            path.unlink()
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    candidates = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC", "DDD"],
            "combined_score": [95, 88, 12, 5],
            "sector": ["Tech", "Health", "Tech", "Health"],
            "price": [100, 50, 25, 20],
        }
    )
    config = {
        "execution": {"mode": "paper", "broker": "alpaca"},
        "portfolio": {
            "target_longs": 1,
            "target_shorts": 1,
            "long_gross_exposure": 0.25,
            "short_gross_exposure": 0.25,
            "min_position_weight": 0.01,
            "max_position_weight": 0.30,
            "max_sector_gross_weight": 0.50,
            "beta_cap_abs": None,
        },
    }

    try:
        PortfolioState(db_path).set_positions(pd.DataFrame({"ticker": ["GHOST"], "weight": [0.50], "price": [10.0]}))

        with pytest.raises(RuntimeError, match="credentials unavailable"):
            run_portfolio_pipeline(
                candidates,
                config=config,
                db_path=db_path,
                output_path=output_path,
                orders_output_path=orders_path,
                method="conviction_tilt",
                whatif=True,
                current_source="alpaca-paper",
            )

        assert not orders_path.exists()
    finally:
        for path in [db_path, output_path, orders_path]:
            if path.exists():
                path.unlink()


def test_rebalance_orders_preserve_target_metadata_and_price_based_shares() -> None:
    target = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "weight": [0.02, -0.015],
            "price": [50.0, 25.0],
            "sector": ["Tech", "Health"],
            "quantity": [40.0, -60.0],
        }
    )

    orders = generate_rebalance_orders({}, target, nav=100_000)

    aaa = orders.loc[orders["ticker"] == "AAA"].iloc[0]
    bbb = orders.loc[orders["ticker"] == "BBB"].iloc[0]
    assert aaa["price"] == pytest.approx(50.0)
    assert aaa["sector"] == "Tech"
    assert aaa["shares"] == pytest.approx(40.0)
    assert bbb["price"] == pytest.approx(25.0)
    assert bbb["shares"] == pytest.approx(60.0)


def test_configured_non_tradable_tickers_accepts_sector_etfs_mapping() -> None:
    tickers = _configured_non_tradable_tickers(
        {
            "data": {
                "universe": {
                    "benchmark_tickers": ["QQQ", "SPY"],
                    "sector_etfs": {"communication_services": "XLC", "technology": "XLK"},
                }
            }
        }
    )

    assert {"QQQ", "SPY", "XLC", "XLK"}.issubset(tickers)


def test_run_portfolio_pipeline_excludes_configured_and_metadata_benchmark_etfs_without_flag(tmp_path: Path) -> None:
    db_path = PROJECT_ROOT / "cache" / f"portfolio_tradable_{tmp_path.name}.sqlite3"
    output_path = PROJECT_ROOT / "output" / f"portfolio_tradable_{tmp_path.name}.csv"
    orders_path = PROJECT_ROOT / "output" / f"portfolio_tradable_orders_{tmp_path.name}.csv"
    for path in [db_path, output_path, orders_path]:
        if path.exists():
            path.unlink()

    candidates = pd.DataFrame(
        {
            "ticker": ["AAA", "QQQ", "SPY", "XLC", "^VIX", "BBB"],
            "source": ["yfinance", "yfinance", "yfinance", "yfinance", "yfinance", "yfinance"],
            "industry": ["Software", "Benchmark", "Benchmark", "Interactive Media", "Benchmark", "Medical Devices"],
            "combined_score": [95, 99, 98, 97, 96, 5],
            "sector": ["Tech", "Benchmark", "Benchmark", "Communication Services", "Benchmark", "Health"],
            "price": [100, 400, 500, 90, 20, 50],
        }
    )
    config = {
        "data": {
            "universe": {
                "benchmark_tickers": ["QQQ", "SPY", "^VIX"],
                "sector_etfs": {"communication_services": "XLC"},
            }
        },
        "portfolio": {
            "target_longs": 1,
            "target_shorts": 1,
            "long_gross_exposure": 0.2,
            "short_gross_exposure": 0.2,
            "min_position_weight": 0.01,
            "max_position_weight": 0.25,
            "max_sector_gross_weight": None,
            "beta_cap_abs": None,
        }
    }

    try:
        payload = run_portfolio_pipeline(
            candidates,
            config=config,
            db_path=db_path,
            output_path=output_path,
            orders_output_path=orders_path,
            method="conviction_tilt",
            whatif=True,
        )

        assert payload["positions"] == 2
        target = pd.read_csv(output_path)
        orders = pd.read_csv(orders_path)
        assert set(target["ticker"]) == {"AAA", "BBB"}
        assert set(orders["ticker"]) == {"AAA", "BBB"}
        assert target.loc[target["ticker"] == "AAA", "source"].iloc[0] == "yfinance"
    finally:
        for path in [db_path, output_path, orders_path]:
            if path.exists():
                path.unlink()


def test_conviction_tilt_applies_score_tiers_liquidity_and_earnings_adjustments() -> None:
    candidates = pd.DataFrame(
        {
            "ticker": [f"T{i:02d}" for i in range(20)],
            "combined_score": list(range(100, 80, -1)),
            "sector": ["Tech"] * 20,
            "avg_dollar_volume": [1_000_000] + [50_000_000] * 19,
            "earnings_date": ["2026-06-20"] + [None] * 19,
        }
    )

    portfolio = build_conviction_tilt_portfolio(
        candidates,
        target_longs=10,
        target_shorts=0,
        long_gross_exposure=0.5,
        short_gross_exposure=0.0,
        max_position_weight=0.20,
        min_position_weight=0.0,
        max_sector_gross_weight=None,
        beta_cap_abs=None,
        nav=1_000_000,
        current_date="2026-06-16",
    )

    top = portfolio.loc[portfolio["ticker"] == "T00"].iloc[0]
    second = portfolio.loc[portfolio["ticker"] == "T01"].iloc[0]
    assert top["conviction_multiplier"] == pytest.approx(1.5)
    assert second["conviction_multiplier"] == pytest.approx(1.25)
    assert top["liquidity_cap_weight"] == pytest.approx(0.05)
    assert top["earnings_size_multiplier"] == pytest.approx(0.5)
    assert abs(top["weight"]) <= 0.025 + 1e-12


def test_factor_exposure_warnings_flag_one_std_dev_spreads() -> None:
    warnings = factor_exposure_warnings(
        pd.Series({"momentum_score": 80.0}),
        pd.DataFrame({"momentum_score": [45.0, 50.0, 55.0]}),
    )

    assert warnings[0]["factor"] == "momentum_score"
    assert warnings[0]["warning"] == "factor_exposure_deviation"
