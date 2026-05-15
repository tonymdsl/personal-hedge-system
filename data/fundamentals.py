"""Fundamental statement storage and ratio calculations.

The ratio calculator is intentionally independent of network providers so it can
be unit-tested with plain dictionaries. Ingestion uses yfinance when enabled and
stores both normalized statement line items and derived ratios in SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

import pandas as pd

from .providers import select_provider

STATEMENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fundamental_statements (
    ticker TEXT NOT NULL,
    fiscal_date TEXT NOT NULL,
    period TEXT NOT NULL,
    statement_type TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, fiscal_date, period, statement_type, metric)
);

CREATE TABLE IF NOT EXISTS fundamental_ratios (
    ticker TEXT NOT NULL,
    fiscal_date TEXT NOT NULL,
    period TEXT NOT NULL,
    revenue REAL,
    revenue_growth REAL,
    gross_profit REAL,
    gross_profit_growth REAL,
    operating_income REAL,
    net_income REAL,
    net_income_growth REAL,
    gross_margin REAL,
    operating_margin REAL,
    net_margin REAL,
    ebitda_margin REAL,
    roe REAL,
    roa REAL,
    roic REAL,
    asset_turnover REAL,
    total_assets REAL,
    total_equity REAL,
    total_debt REAL,
    debt_to_equity REAL,
    debt_to_assets REAL,
    net_debt REAL,
    net_debt_to_ebitda REAL,
    current_ratio REAL,
    quick_ratio REAL,
    cash_ratio REAL,
    accounts_receivable REAL,
    ar_to_sales REAL,
    ar_growth REAL,
    operating_cash_flow REAL,
    cfo_growth REAL,
    capex REAL,
    capex_to_sales REAL,
    free_cash_flow REAL,
    fcf_growth REAL,
    fcf_margin REAL,
    fcf_to_net_income REAL,
    cfo_to_net_income REAL,
    accruals_ratio REAL,
    working_capital REAL,
    working_capital_to_assets REAL,
    working_capital_change REAL,
    working_capital_change_to_revenue REAL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, fiscal_date, period)
);
"""

RATIO_COLUMNS = [
    "revenue",
    "revenue_growth",
    "gross_profit",
    "gross_profit_growth",
    "operating_income",
    "net_income",
    "net_income_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "ebitda_margin",
    "roe",
    "roa",
    "roic",
    "asset_turnover",
    "total_assets",
    "total_equity",
    "total_debt",
    "debt_to_equity",
    "debt_to_assets",
    "net_debt",
    "net_debt_to_ebitda",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "accounts_receivable",
    "ar_to_sales",
    "ar_growth",
    "operating_cash_flow",
    "cfo_growth",
    "capex",
    "capex_to_sales",
    "free_cash_flow",
    "fcf_growth",
    "fcf_margin",
    "fcf_to_net_income",
    "cfo_to_net_income",
    "accruals_ratio",
    "working_capital",
    "working_capital_to_assets",
    "working_capital_change",
    "working_capital_change_to_revenue",
]

ALIASES: dict[str, Sequence[str]] = {
    "revenue": ("Total Revenue", "Revenue", "Revenues", "Sales", "totalRevenue"),
    "gross_profit": ("Gross Profit", "grossProfit"),
    "operating_income": ("Operating Income", "Operating Income Loss", "operatingIncome"),
    "net_income": ("Net Income", "Net Income Common Stockholders", "NetIncomeLoss", "netIncome"),
    "ebitda": ("EBITDA", "Normalized EBITDA", "Ebitda"),
    "ebit": ("EBIT", "Ebit"),
    "tax_expense": ("Tax Provision", "Income Tax Expense Benefit", "taxExpense"),
    "pretax_income": ("Pretax Income", "Income Before Tax", "pretaxIncome"),
    "total_assets": ("Total Assets", "Assets", "totalAssets"),
    "total_equity": (
        "Stockholders Equity",
        "Total Stockholder Equity",
        "Total Equity Gross Minority Interest",
        "Shareholders Equity",
        "totalStockholdersEquity",
    ),
    "total_liabilities": ("Total Liabilities Net Minority Interest", "Total Liab", "Liabilities", "totalLiabilities"),
    "total_debt": ("Total Debt", "Short Long Term Debt Total", "totalDebt"),
    "long_term_debt": ("Long Term Debt", "Long Term Debt And Capital Lease Obligation", "longTermDebt"),
    "current_debt": ("Current Debt", "Short Long Term Debt", "Current Debt And Capital Lease Obligation"),
    "current_assets": ("Current Assets", "Total Current Assets", "totalCurrentAssets"),
    "current_liabilities": ("Current Liabilities", "Total Current Liabilities", "totalCurrentLiabilities"),
    "cash": ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "Cash", "cash"),
    "inventory": ("Inventory", "inventory"),
    "accounts_receivable": ("Accounts Receivable", "Net Receivables", "Accounts Receivable Net", "netReceivables"),
    "accounts_payable": ("Accounts Payable", "accountsPayable"),
    "operating_cash_flow": (
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
        "Net Cash Provided By Operating Activities",
        "netCashProvidedByOperatingActivities",
    ),
    "capex": ("Capital Expenditure", "Capital Expenditures", "CapitalExpenditures", "capitalExpenditure"),
}


def ensure_fundamentals_schema(connection) -> None:
    """Create local fundamentals tables."""

    connection.executescript(STATEMENT_SCHEMA_SQL)


def _normalize_key(key: object) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _as_mapping(data: Mapping[str, object] | pd.Series | pd.DataFrame | None) -> dict[object, object]:
    if data is None:
        return {}
    if isinstance(data, pd.Series):
        return data.to_dict()
    if isinstance(data, pd.DataFrame):
        if data.empty:
            return {}
        return data.iloc[:, 0].to_dict()
    return dict(data)


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        if cleaned in {"", "None", "nan", "NaN", "—", "-"}:
            return None
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        value = cleaned
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _lookup(mapping: Mapping[object, object], aliases: Sequence[str]) -> float | None:
    if not mapping:
        return None
    for alias in aliases:
        if alias in mapping:
            value = _to_float(mapping[alias])
            if value is not None:
                return value
    normalized = {_normalize_key(key): value for key, value in mapping.items()}
    for alias in aliases:
        value = normalized.get(_normalize_key(alias))
        converted = _to_float(value)
        if converted is not None:
            return converted
    return None


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    """Return numerator / denominator with ``None`` for unavailable/zero values."""

    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def growth_rate(current: float | None, previous: float | None) -> float | None:
    """Return period-over-period growth using ``abs(previous)`` as denominator."""

    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


def _free_cash_flow(cfo: float | None, capex: float | None) -> float | None:
    if cfo is None or capex is None:
        return None
    # Yahoo reports capex as a negative cash-flow line. If a provider reports a
    # positive cash outflow, subtract it instead.
    return cfo + capex if capex < 0 else cfo - capex


def extract_financial_values(
    income_statement: Mapping[str, object] | pd.Series | pd.DataFrame | None,
    balance_sheet: Mapping[str, object] | pd.Series | pd.DataFrame | None,
    cash_flow: Mapping[str, object] | pd.Series | pd.DataFrame | None,
) -> dict[str, float | None]:
    """Extract canonical financial values from statement mappings."""

    income = _as_mapping(income_statement)
    balance = _as_mapping(balance_sheet)
    cash = _as_mapping(cash_flow)

    values = {name: _lookup(income, aliases) for name, aliases in ALIASES.items() if name in {
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "ebitda",
        "ebit",
        "tax_expense",
        "pretax_income",
    }}
    values.update(
        {name: _lookup(balance, aliases) for name, aliases in ALIASES.items() if name in {
            "total_assets",
            "total_equity",
            "total_liabilities",
            "total_debt",
            "long_term_debt",
            "current_debt",
            "current_assets",
            "current_liabilities",
            "cash",
            "inventory",
            "accounts_receivable",
            "accounts_payable",
        }}
    )
    values.update(
        {name: _lookup(cash, aliases) for name, aliases in ALIASES.items() if name in {"operating_cash_flow", "capex"}}
    )

    if values.get("total_debt") is None:
        long_debt = values.get("long_term_debt") or 0.0
        current_debt = values.get("current_debt") or 0.0
        values["total_debt"] = long_debt + current_debt if (long_debt or current_debt) else None
    values["free_cash_flow"] = _free_cash_flow(values.get("operating_cash_flow"), values.get("capex"))
    current_assets = values.get("current_assets")
    current_liabilities = values.get("current_liabilities")
    values["working_capital"] = (
        current_assets - current_liabilities
        if current_assets is not None and current_liabilities is not None
        else None
    )
    return values


def calculate_financial_ratios(
    income_statement: Mapping[str, object] | pd.Series | pd.DataFrame | None,
    balance_sheet: Mapping[str, object] | pd.Series | pd.DataFrame | None,
    cash_flow: Mapping[str, object] | pd.Series | pd.DataFrame | None,
    previous_income_statement: Mapping[str, object] | pd.Series | pd.DataFrame | None = None,
    previous_balance_sheet: Mapping[str, object] | pd.Series | pd.DataFrame | None = None,
    previous_cash_flow: Mapping[str, object] | pd.Series | pd.DataFrame | None = None,
) -> dict[str, float | None]:
    """Calculate quality, growth, leverage, cash-flow, and working-capital ratios."""

    current = extract_financial_values(income_statement, balance_sheet, cash_flow)
    previous = extract_financial_values(previous_income_statement, previous_balance_sheet, previous_cash_flow)

    revenue = current.get("revenue")
    gross_profit = current.get("gross_profit")
    operating_income = current.get("operating_income")
    net_income = current.get("net_income")
    ebitda = current.get("ebitda")
    total_assets = current.get("total_assets")
    total_equity = current.get("total_equity")
    total_debt = current.get("total_debt")
    cash = current.get("cash") or 0.0
    current_assets = current.get("current_assets")
    current_liabilities = current.get("current_liabilities")
    inventory = current.get("inventory") or 0.0
    accounts_receivable = current.get("accounts_receivable")
    operating_cash_flow = current.get("operating_cash_flow")
    capex = current.get("capex")
    free_cash_flow = current.get("free_cash_flow")
    working_capital = current.get("working_capital")

    previous_assets = previous.get("total_assets")
    previous_equity = previous.get("total_equity")
    average_assets = (
        (total_assets + previous_assets) / 2
        if total_assets is not None and previous_assets is not None
        else total_assets
    )
    average_equity = (
        (total_equity + previous_equity) / 2
        if total_equity is not None and previous_equity is not None
        else total_equity
    )

    pretax_income = current.get("pretax_income")
    tax_expense = current.get("tax_expense")
    tax_rate = safe_divide(tax_expense, pretax_income)
    if tax_rate is None or tax_rate < 0 or tax_rate > 0.5:
        tax_rate = 0.21
    ebit = current.get("ebit") if current.get("ebit") is not None else operating_income
    nopat = ebit * (1 - tax_rate) if ebit is not None else None
    invested_capital = None
    if total_debt is not None and total_equity is not None:
        invested_capital = total_debt + total_equity - cash

    previous_working_capital = previous.get("working_capital")
    working_capital_change = (
        working_capital - previous_working_capital
        if working_capital is not None and previous_working_capital is not None
        else None
    )
    net_debt = (total_debt - cash) if total_debt is not None else None

    ratios: dict[str, float | None] = {
        "revenue": revenue,
        "revenue_growth": growth_rate(revenue, previous.get("revenue")),
        "gross_profit": gross_profit,
        "gross_profit_growth": growth_rate(gross_profit, previous.get("gross_profit")),
        "operating_income": operating_income,
        "net_income": net_income,
        "net_income_growth": growth_rate(net_income, previous.get("net_income")),
        "gross_margin": safe_divide(gross_profit, revenue),
        "operating_margin": safe_divide(operating_income, revenue),
        "net_margin": safe_divide(net_income, revenue),
        "ebitda_margin": safe_divide(ebitda, revenue),
        "roe": safe_divide(net_income, average_equity),
        "roa": safe_divide(net_income, average_assets),
        "roic": safe_divide(nopat, invested_capital),
        "asset_turnover": safe_divide(revenue, average_assets),
        "total_assets": total_assets,
        "total_equity": total_equity,
        "total_debt": total_debt,
        "debt_to_equity": safe_divide(total_debt, total_equity),
        "debt_to_assets": safe_divide(total_debt, total_assets),
        "net_debt": net_debt,
        "net_debt_to_ebitda": safe_divide(net_debt, ebitda),
        "current_ratio": safe_divide(current_assets, current_liabilities),
        "quick_ratio": safe_divide((current_assets - inventory) if current_assets is not None else None, current_liabilities),
        "cash_ratio": safe_divide(cash, current_liabilities),
        "accounts_receivable": accounts_receivable,
        "ar_to_sales": safe_divide(accounts_receivable, revenue),
        "ar_growth": growth_rate(accounts_receivable, previous.get("accounts_receivable")),
        "operating_cash_flow": operating_cash_flow,
        "cfo_growth": growth_rate(operating_cash_flow, previous.get("operating_cash_flow")),
        "capex": capex,
        "capex_to_sales": safe_divide(abs(capex) if capex is not None else None, revenue),
        "free_cash_flow": free_cash_flow,
        "fcf_growth": growth_rate(free_cash_flow, previous.get("free_cash_flow")),
        "fcf_margin": safe_divide(free_cash_flow, revenue),
        "fcf_to_net_income": safe_divide(free_cash_flow, net_income),
        "cfo_to_net_income": safe_divide(operating_cash_flow, net_income),
        "accruals_ratio": safe_divide((net_income - operating_cash_flow) if net_income is not None and operating_cash_flow is not None else None, average_assets),
        "working_capital": working_capital,
        "working_capital_to_assets": safe_divide(working_capital, total_assets),
        "working_capital_change": working_capital_change,
        "working_capital_change_to_revenue": safe_divide(working_capital_change, revenue),
    }
    return {column: ratios.get(column) for column in RATIO_COLUMNS}


def _statement_rows(ticker: str, fiscal_date: str, period: str, statement_type: str, series: pd.Series, source: str) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for metric, value in series.items():
        converted = _to_float(value)
        if converted is None:
            continue
        rows.append((ticker, fiscal_date, period, statement_type, str(metric), converted, source))
    return rows


def upsert_statement_rows(connection, rows: Sequence[tuple[object, ...]]) -> int:
    ensure_fundamentals_schema(connection)
    connection.executemany(
        """
        INSERT INTO fundamental_statements(ticker, fiscal_date, period, statement_type, metric, value, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(ticker, fiscal_date, period, statement_type, metric) DO UPDATE SET
            value = excluded.value,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        list(rows),
    )
    return len(rows)


def upsert_fundamental_ratios(
    connection,
    ticker: str,
    fiscal_date: str,
    period: str,
    ratios: Mapping[str, float | None],
    *,
    source: str = "yfinance",
) -> int:
    """Upsert one ratio row."""

    ensure_fundamentals_schema(connection)
    columns = RATIO_COLUMNS
    placeholders = ", ".join(["?"] * (3 + len(columns) + 1))
    update_clause = ",\n            ".join([f"{column} = excluded.{column}" for column in columns])
    sql = f"""
        INSERT INTO fundamental_ratios(ticker, fiscal_date, period, {', '.join(columns)}, source, updated_at)
        VALUES ({placeholders}, datetime('now'))
        ON CONFLICT(ticker, fiscal_date, period) DO UPDATE SET
            {update_clause},
            source = excluded.source,
            updated_at = excluded.updated_at
    """
    values: list[object] = [ticker.upper(), fiscal_date, period]
    values.extend(ratios.get(column) for column in columns)
    values.append(source)
    connection.execute(sql, values)
    return 1


def _date_to_string(value: object) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


def _get_yfinance_frame(ticker_obj: object, names: Sequence[str]) -> pd.DataFrame:
    for name in names:
        value = getattr(ticker_obj, name, None)
        if isinstance(value, pd.DataFrame) and not value.empty:
            return value
    return pd.DataFrame()


def fetch_yfinance_statements(ticker: str, *, period: str = "quarterly") -> list[dict[str, object]]:
    """Fetch yfinance statements and calculate ratios for available columns."""

    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("yfinance is required for fundamentals ingestion") from exc

    ticker_obj = yf.Ticker(ticker)
    if period == "annual":
        income = _get_yfinance_frame(ticker_obj, ("income_stmt", "financials"))
        balance = _get_yfinance_frame(ticker_obj, ("balance_sheet",))
        cash = _get_yfinance_frame(ticker_obj, ("cashflow",))
    else:
        income = _get_yfinance_frame(ticker_obj, ("quarterly_income_stmt", "quarterly_financials"))
        balance = _get_yfinance_frame(ticker_obj, ("quarterly_balance_sheet",))
        cash = _get_yfinance_frame(ticker_obj, ("quarterly_cashflow",))

    if income.empty or balance.empty or cash.empty:
        return []

    columns = list(income.columns)
    results: list[dict[str, object]] = []
    for index, column in enumerate(columns):
        previous_column = columns[index + 1] if index + 1 < len(columns) else None
        fiscal_date = _date_to_string(column)
        income_current = income[column]
        balance_current = balance[column] if column in balance.columns else pd.Series(dtype="float64")
        cash_current = cash[column] if column in cash.columns else pd.Series(dtype="float64")
        income_previous = income[previous_column] if previous_column is not None else None
        balance_previous = balance[previous_column] if previous_column is not None and previous_column in balance.columns else None
        cash_previous = cash[previous_column] if previous_column is not None and previous_column in cash.columns else None
        ratios = calculate_financial_ratios(
            income_current,
            balance_current,
            cash_current,
            income_previous,
            balance_previous,
            cash_previous,
        )
        results.append(
            {
                "ticker": ticker.upper(),
                "fiscal_date": fiscal_date,
                "period": period,
                "income": income_current,
                "balance": balance_current,
                "cash": cash_current,
                "ratios": ratios,
            }
        )
    return results


def _configured_periods(config: Mapping[str, object] | None) -> list[str]:
    data = config.get("data") if config else None
    if not isinstance(data, Mapping):
        return ["quarterly", "annual"]
    fundamentals = data.get("fundamentals")
    if not isinstance(fundamentals, Mapping):
        return ["quarterly", "annual"]
    statements = fundamentals.get("statements", ["quarterly", "annual"])
    if not isinstance(statements, (list, tuple)):
        return ["quarterly", "annual"]
    return [str(item) for item in statements if str(item) in {"quarterly", "annual"}]


def ingest_fundamentals(
    connection,
    tickers: Iterable[str],
    config: Mapping[str, object] | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Fetch statements and derived ratios for tickers."""

    ensure_fundamentals_schema(connection)
    ticker_list = [str(ticker).upper().strip() for ticker in tickers if str(ticker).strip()]
    selection = select_provider("fundamentals", config=config)
    if dry_run:
        return {
            "status": "skipped",
            "reason": "dry_run",
            "provider": selection.provider,
            "tickers": len(ticker_list),
            "statement_rows": 0,
            "ratio_rows": 0,
            "count": 0,
        }
    if selection.provider not in {"yfinance", "fmp"}:
        return {
            "status": "skipped",
            "reason": selection.reason,
            "provider": selection.provider,
            "count": 0,
        }

    statement_count = 0
    ratio_count = 0
    errors: dict[str, str] = {}
    for ticker in ticker_list:
        try:
            for period in _configured_periods(config):
                for item in fetch_yfinance_statements(ticker, period=period):
                    fiscal_date = str(item["fiscal_date"])
                    statement_rows = []
                    statement_rows.extend(_statement_rows(ticker, fiscal_date, period, "income", item["income"], "yfinance"))
                    statement_rows.extend(_statement_rows(ticker, fiscal_date, period, "balance", item["balance"], "yfinance"))
                    statement_rows.extend(_statement_rows(ticker, fiscal_date, period, "cash_flow", item["cash"], "yfinance"))
                    statement_count += upsert_statement_rows(connection, statement_rows)
                    ratio_count += upsert_fundamental_ratios(
                        connection,
                        ticker,
                        fiscal_date,
                        period,
                        item["ratios"],  # type: ignore[arg-type]
                        source="yfinance",
                    )
        except Exception as exc:
            errors[ticker] = str(exc)

    return {
        "status": "partial" if errors else "ok",
        "provider": selection.provider,
        "tickers": len(ticker_list),
        "statement_rows": statement_count,
        "ratio_rows": ratio_count,
        "count": ratio_count,
        "errors": errors,
    }
