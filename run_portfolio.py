"""Layer 4 portfolio construction command."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import requests

from common.cli import add_common_arguments
from common.config import PROJECT_ROOT, ensure_project_path, load_config
from common.db import default_database_path
from portfolio import preferences as portfolio_preferences
from portfolio.rebalance import plan_rebalance
from portfolio.rebalance_schedule import rebalance_advisories
from portfolio.state import PortfolioState


PAPER_ALPACA_BASE_URL = "https://paper-api.alpaca.markets"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meridian Layer 4 portfolio construction.")
    add_common_arguments(parser)
    parser.add_argument("--rebalance", action="store_true", help="Generate a rebalance plan.")
    parser.add_argument("--whatif", action="store_true", help="Show proposed changes without committing portfolio state.")
    parser.add_argument("--current", action="store_true", help="Print current portfolio positions.")
    parser.add_argument("--optimize-method", "--method", choices=["conviction_tilt", "mvo", "conviction"], default=None)
    parser.add_argument("--input", default="output/analysis_results_latest.csv")
    parser.add_argument("--output", default="output/target_portfolio_latest.csv")
    parser.add_argument("--orders-output", default="output/rebalance_orders_latest.csv")
    parser.add_argument("--nav", type=float, default=1_000_000.0)
    parser.add_argument(
        "--candidate-review-gate",
        choices=["config", "approved_only", "exclude_rejected", "off"],
        default="config",
        help="Override candidate review gate for this run.",
    )
    parser.add_argument(
        "--current-source",
        choices=["auto", "local", "alpaca-paper"],
        default="auto",
        help="Current portfolio source for rebalance deltas.",
    )
    return parser


def _read_csv(path: str | Path) -> pd.DataFrame:
    resolved = ensure_project_path(path, PROJECT_ROOT)
    if not resolved.exists():
        return pd.DataFrame()
    return pd.read_csv(resolved)


def _display_path(path: str | Path | None) -> str | None:
    if path is None:
        return None
    resolved = Path(path).resolve(strict=False)
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _score_col(frame: pd.DataFrame) -> str:
    for column in ["combined_score", "composite_score", "quant_score", "score"]:
        if column in frame.columns:
            return column
    raise ValueError("Candidates must contain combined_score, composite_score, quant_score, or score")


def _sector_col(frame: pd.DataFrame) -> str:
    return "gics_sector" if "gics_sector" in frame.columns else "sector"


def _normalize_method(method: str | None, config: Mapping[str, Any]) -> str:
    if method:
        return portfolio_preferences.normalize_optimizer_method(method, config=config)
    return portfolio_preferences.preferred_optimizer_method(config)


def _current_weights(state: PortfolioState) -> pd.Series:
    positions = state.get_positions()
    if positions.empty or "ticker" not in positions.columns or "weight" not in positions.columns:
        return pd.Series(dtype="float64")
    return positions.set_index("ticker")["weight"].astype("float64")


def _price_return_covariance(
    db_path: str | Path,
    tickers: pd.Series | list[object],
    lookback_days: int = 252,
) -> pd.DataFrame | None:
    normalized_tickers = sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()})
    if len(normalized_tickers) < 2:
        return None

    placeholders = ", ".join("?" for _ in normalized_tickers)
    query = f"""
        SELECT
            UPPER(TRIM(ticker)) AS ticker,
            date,
            COALESCE(NULLIF(adj_close, 0), NULLIF(close, 0)) AS price
        FROM daily_prices
        WHERE UPPER(TRIM(ticker)) IN ({placeholders})
        ORDER BY date, ticker
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(ensure_project_path(db_path, PROJECT_ROOT))
        prices = pd.read_sql_query(query, connection, params=normalized_tickers)
    except (sqlite3.Error, pd.errors.DatabaseError):
        return None
    finally:
        if connection is not None:
            connection.close()

    if prices.empty:
        return None
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["price"] = pd.to_numeric(prices["price"], errors="coerce")
    prices = prices.dropna(subset=["date", "ticker", "price"])
    prices = prices.loc[prices["price"] > 0]
    if prices.empty:
        return None

    price_panel = (
        prices.pivot_table(index="date", columns="ticker", values="price", aggfunc="last")
        .sort_index()
        .tail(max(int(lookback_days), 1) + 1)
    )
    returns = price_panel.pct_change(fill_method=None).tail(max(int(lookback_days), 1))
    valid_columns = returns.columns[returns.notna().sum() >= 2].tolist()
    if len(valid_columns) < 2:
        return None

    covariance = returns[valid_columns].cov(min_periods=2) * 252.0
    covariance = covariance.reindex(index=valid_columns, columns=valid_columns).fillna(0.0)
    if covariance.empty or len(covariance.columns) < 2:
        return None
    for ticker in covariance.index:
        covariance.loc[ticker, ticker] = float(covariance.loc[ticker, ticker]) + 1e-8
    return covariance


def _paper_alpaca_enabled(config: Mapping[str, Any]) -> bool:
    execution = config.get("execution", {}) if isinstance(config, Mapping) else {}
    if not isinstance(execution, Mapping):
        return False
    return str(execution.get("mode", "paper")).lower() == "paper" and str(execution.get("broker", "")).lower() == "alpaca"


def _normalize_alpaca_base_url(base_url: str | None) -> str:
    normalized = (base_url or PAPER_ALPACA_BASE_URL).strip().rstrip("/")
    if normalized.endswith("/v2"):
        normalized = normalized[:-3].rstrip("/")
    return normalized or PAPER_ALPACA_BASE_URL


def _alpaca_paper_base_url(config: Mapping[str, Any]) -> str:
    execution = config.get("execution", {}) if isinstance(config, Mapping) else {}
    alpaca = execution.get("alpaca", {}) if isinstance(execution, Mapping) else {}
    if isinstance(alpaca, Mapping):
        return _normalize_alpaca_base_url(str(alpaca.get("paper_base_url", PAPER_ALPACA_BASE_URL)))
    return PAPER_ALPACA_BASE_URL


def _alpaca_headers() -> dict[str, str]:
    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        raise RuntimeError("Alpaca paper credentials unavailable; refusing to use local portfolio_positions as paper current state.")
    return {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}


def _alpaca_paper_current_weights(config: Mapping[str, Any], *, timeout: float = 20.0) -> pd.Series:
    base_url = _alpaca_paper_base_url(config)
    headers = _alpaca_headers()
    session = requests.Session()
    try:
        account_response = session.get(f"{base_url}/v2/account", headers=headers, timeout=timeout)
        account_response.raise_for_status()
        positions_response = session.get(f"{base_url}/v2/positions", headers=headers, timeout=timeout)
        positions_response.raise_for_status()
        account = account_response.json()
        positions = positions_response.json()
    except Exception as exc:
        raise RuntimeError(f"Unable to read Alpaca paper positions; refusing to use local portfolio_positions: {type(exc).__name__}") from exc

    if not isinstance(account, Mapping):
        raise RuntimeError("Alpaca paper account response was invalid; refusing to use local portfolio_positions.")
    if not isinstance(positions, list):
        raise RuntimeError("Alpaca paper positions response was invalid; refusing to use local portfolio_positions.")
    return _normalize_alpaca_position_weights(positions, account)


def _normalize_alpaca_position_weights(positions: list[object], account: Mapping[str, Any]) -> pd.Series:
    equity = pd.to_numeric(
        pd.Series([account.get("portfolio_value", account.get("equity", account.get("cash", 0)))]),
        errors="coerce",
    ).iloc[0]
    if pd.isna(equity) or float(equity) == 0.0:
        return pd.Series(dtype="float64")

    weights: dict[str, float] = {}
    quantities: dict[str, float] = {}
    prices: dict[str, float] = {}
    for position in positions:
        if not isinstance(position, Mapping):
            continue
        ticker = str(position.get("symbol", position.get("ticker", ""))).strip().upper().replace(".", "-")
        if not ticker:
            continue
        market_value = pd.to_numeric(pd.Series([position.get("market_value")]), errors="coerce").iloc[0]
        if pd.isna(market_value):
            qty = pd.to_numeric(pd.Series([position.get("qty")]), errors="coerce").iloc[0]
            price = pd.to_numeric(pd.Series([position.get("current_price", position.get("avg_entry_price"))]), errors="coerce").iloc[0]
            if pd.isna(qty) or pd.isna(price):
                continue
            market_value = float(qty) * float(price)
        market_value = float(market_value)
        qty = pd.to_numeric(pd.Series([position.get("qty")]), errors="coerce").iloc[0]
        price = pd.to_numeric(pd.Series([position.get("current_price", position.get("avg_entry_price"))]), errors="coerce").iloc[0]
        quantity = float(qty) if not pd.isna(qty) else 0.0
        if str(position.get("side", "")).lower() == "short" and market_value > 0:
            market_value = -market_value
        if str(position.get("side", "")).lower() == "short" and quantity > 0:
            quantity = -quantity
        weights[ticker] = market_value / float(equity)
        quantities[ticker] = quantity
        if not pd.isna(price) and float(price) > 0:
            prices[ticker] = float(price)
    series = pd.Series(weights, dtype="float64")
    series.attrs["quantities"] = quantities
    series.attrs["prices"] = prices
    return series


def _resolve_current_weights(
    state: PortfolioState,
    config: Mapping[str, Any],
    *,
    current_source: str = "auto",
) -> pd.Series:
    if current_source == "local":
        return _current_weights(state)
    if current_source == "alpaca-paper":
        return _alpaca_paper_current_weights(config)
    if _paper_alpaca_enabled(config) and os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"):
        return _alpaca_paper_current_weights(config)
    return _current_weights(state)


def _configured_non_tradable_tickers(config: Mapping[str, Any]) -> set[str]:
    data = config.get("data", {}) if isinstance(config, Mapping) else {}
    universe = data.get("universe", {}) if isinstance(data, Mapping) else {}
    if not isinstance(universe, Mapping):
        return set()

    tickers: set[str] = set()
    for key in ["benchmark_tickers", "sector_etfs"]:
        values = universe.get(key, [])
        if isinstance(values, Mapping):
            values = list(values.values())
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple, set)):
            continue
        tickers.update(str(value).strip().upper() for value in values if str(value).strip())
    return tickers


def _tradable_candidates(candidates: pd.DataFrame, *, config: Mapping[str, Any] | None = None) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    frame = candidates.copy()
    ticker = frame["ticker"].astype(str).str.strip() if "ticker" in frame.columns else pd.Series("", index=frame.index)
    normalized_ticker = ticker.str.upper()
    mask = ~ticker.str.startswith("^", na=False)
    if config is not None:
        mask &= ~normalized_ticker.isin(_configured_non_tradable_tickers(config))
    if "is_benchmark" in frame.columns:
        benchmark = frame["is_benchmark"]
        if benchmark.dtype == object:
            benchmark = benchmark.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
        else:
            benchmark = benchmark.fillna(False).astype(bool)
        mask &= ~benchmark
    if "industry" in frame.columns:
        industry = frame["industry"].astype(str).str.strip().str.lower()
        mask &= ~industry.isin({"benchmark", "sector etf"})
    if "sector" in frame.columns:
        sector = frame["sector"].astype(str).str.strip().str.lower()
        mask &= sector.ne("benchmark")
    return frame.loc[mask].copy()


def _candidate_review_gate_mode(config: Mapping[str, Any], override: str | None = None) -> str:
    if override and str(override).strip().lower() not in {"config", "default"}:
        mode = str(override).strip().lower()
        if mode in {"off", "none", "disabled", "false", "0"}:
            return "off"
        if mode in {"exclude_rejected", "soft"}:
            return "exclude_rejected"
        return "approved_only"
    portfolio_config = config.get("portfolio", {}) if isinstance(config, Mapping) else {}
    if not isinstance(portfolio_config, Mapping):
        return "approved_only"
    mode = str(portfolio_config.get("candidate_review_gate", "approved_only")).strip().lower()
    if mode in {"off", "none", "disabled", "false", "0"}:
        return "off"
    if mode in {"exclude_rejected", "soft"}:
        return "exclude_rejected"
    return "approved_only"


def _apply_candidate_review_gate(
    candidates: pd.DataFrame,
    state: PortfolioState,
    *,
    config: Mapping[str, Any],
    gate_mode: str | None = None,
) -> pd.DataFrame:
    if candidates.empty or "ticker" not in candidates.columns:
        return candidates
    mode = _candidate_review_gate_mode(config, gate_mode)
    if mode == "off":
        return candidates
    reviews = state.get_candidate_reviews()
    if reviews.empty or "ticker" not in reviews.columns or "status" not in reviews.columns:
        return candidates

    latest = reviews.copy()
    latest["ticker"] = latest["ticker"].astype(str).str.strip().str.upper()
    latest["status"] = latest["status"].astype(str).str.strip().str.lower()
    status_by_ticker = latest.drop_duplicates("ticker", keep="first").set_index("ticker")["status"]
    reason_by_ticker = (
        latest.drop_duplicates("ticker", keep="first").set_index("ticker")["reason"]
        if "reason" in latest.columns
        else pd.Series(dtype=object)
    )

    frame = candidates.copy()
    normalized_ticker = frame["ticker"].astype(str).str.strip().str.upper()
    frame["candidate_review_status"] = normalized_ticker.map(status_by_ticker)
    if not reason_by_ticker.empty:
        frame["candidate_review_reason"] = normalized_ticker.map(reason_by_ticker)
    actionable = frame["candidate_review_status"].isin(["approved", "rejected", "watch"])
    if not bool(actionable.any()):
        return frame
    if mode == "exclude_rejected":
        mask = ~frame["candidate_review_status"].isin(["rejected", "watch"])
    else:
        mask = frame["candidate_review_status"].eq("approved")
    return frame.loc[mask].copy()


def _prepare_target_for_state(target: pd.DataFrame, *, nav: float) -> pd.DataFrame:
    if target.empty:
        return target
    frame = target.copy()
    if "price" not in frame.columns:
        frame["price"] = 1.0
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce").replace(0, pd.NA).fillna(1.0)
    frame["quantity"] = (frame["weight"] * float(nav)) / frame["price"]
    frame["current_price"] = frame["price"]
    frame["entry_price"] = frame["price"]
    factor_columns = [column for column in frame.columns if column.endswith("_score")]
    frame["factor_scores_at_entry"] = [
        {column: row.get(column) for column in factor_columns if pd.notna(row.get(column))}
        for row in frame.to_dict(orient="records")
    ]
    return frame


def run_portfolio_pipeline(
    candidates: pd.DataFrame,
    *,
    config: Mapping[str, Any],
    db_path: str | Path | None = None,
    output_path: str | Path = "output/target_portfolio_latest.csv",
    orders_output_path: str | Path = "output/rebalance_orders_latest.csv",
    method: str | None = None,
    whatif: bool = True,
    nav: float = 1_000_000.0,
    current_source: str = "auto",
    candidate_review_gate: str | None = None,
) -> dict[str, Any]:
    state = PortfolioState(db_path or default_database_path(config))
    candidates = _tradable_candidates(candidates, config=config)
    candidates = _apply_candidate_review_gate(candidates, state, config=config, gate_mode=candidate_review_gate)
    if candidates.empty:
        return {"positions": 0, "orders": 0, "gross": 0.0, "net": 0.0, "whatif": bool(whatif)}

    score_col = _score_col(candidates)
    sector_col = _sector_col(candidates)
    resolved_method = _normalize_method(method, config)
    current = _resolve_current_weights(state, config, current_source=current_source)
    portfolio_config = config.get("portfolio", {}) if isinstance(config, Mapping) else {}
    optimizer_config = portfolio_config.get("optimizer", {}) if isinstance(portfolio_config, Mapping) else {}
    transaction_cost_bps = float(optimizer_config.get("transaction_cost_bps", 10.0) if isinstance(optimizer_config, Mapping) else 10.0)
    optimizer_kwargs: dict[str, object] = {"config": config, "score_col": score_col, "sector_col": sector_col}
    if resolved_method == "mvo":
        lookback_days = int(portfolio_config.get("covariance_lookback_days", 252) if isinstance(portfolio_config, Mapping) else 252)
        covariance = _price_return_covariance(state.db_path, candidates["ticker"].tolist(), lookback_days)
        if covariance is not None:
            optimizer_kwargs["covariance"] = covariance
        optimizer_kwargs["risk_aversion"] = float(
            optimizer_config.get("mvo_risk_aversion", 5.0) if isinstance(optimizer_config, Mapping) else 5.0
        )

    plan = plan_rebalance(
        candidates,
        current,
        method=resolved_method,
        nav=nav,
        transaction_cost_bps=transaction_cost_bps,
        **optimizer_kwargs,
    )
    target = _prepare_target_for_state(plan["target_portfolio"], nav=nav)
    orders = plan["orders"]
    resolved_output = ensure_project_path(output_path, PROJECT_ROOT)
    resolved_orders_output = ensure_project_path(orders_output_path, PROJECT_ROOT)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_orders_output.parent.mkdir(parents=True, exist_ok=True)
    target.to_csv(resolved_output, index=False)
    orders.to_csv(resolved_orders_output, index=False)

    advisory_input = target
    if "earnings_date" not in advisory_input.columns and "earnings_date" in candidates.columns:
        advisory_input = advisory_input.merge(candidates[["ticker", "earnings_date"]], on="ticker", how="left")
    advisories = rebalance_advisories(advisory_input)

    approval_id = None
    if not whatif:
        state.set_positions(target)
        state.record_history(target)
        approval_id = state.request_approval(
            "rebalance",
            {
                "method": resolved_method,
                "positions": int(len(target)),
                "orders": int(len(orders)),
                "target_output": _display_path(resolved_output),
            },
        )

    weights = target.get("weight", pd.Series(dtype="float64")).astype("float64")
    return {
        "method": resolved_method,
        "whatif": bool(whatif),
        "current_source": current_source,
        "positions": int(len(target)),
        "orders": int(len(orders)),
        "gross": float(weights.abs().sum()),
        "net": float(weights.sum()),
        "target_output": _display_path(resolved_output),
        "orders_output": _display_path(resolved_orders_output),
        "transaction_costs": plan["transaction_costs"],
        "advisories": advisories,
        "approval_id": approval_id,
        "target_optimizer": str(target["optimizer"].iloc[0]) if "optimizer" in target.columns and not target.empty else resolved_method,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    state = PortfolioState(default_database_path(config))
    if args.current:
        positions = state.get_positions()
        payload = {
            "current": True,
            "positions": int(len(positions)),
            "gross": float(positions.get("weight", pd.Series(dtype="float64")).abs().sum()) if not positions.empty else 0.0,
            "net": float(positions.get("weight", pd.Series(dtype="float64")).sum()) if not positions.empty else 0.0,
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    candidates = _read_csv(args.input)
    payload = run_portfolio_pipeline(
        candidates,
        config=config,
        output_path=args.output,
        orders_output_path=args.orders_output,
        method=args.optimize_method,
        whatif=bool(args.whatif or args.dry_run),
        nav=float(args.nav),
        current_source=str(args.current_source),
        candidate_review_gate=None if args.candidate_review_gate == "config" else str(args.candidate_review_gate),
    )
    payload["rebalance"] = bool(args.rebalance)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
