"""SEC EDGAR ingestion helpers, including Form 4 parsing.

Network-facing functions require a SEC User-Agent. Tests use the pure XML/parser
helpers and do not call EDGAR.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Sequence

import requests

OPEN_MARKET_PURCHASE_CODES = {"P"}
GRANT_OR_EXERCISE_CODES = {"A", "M", "F"}


@dataclass(frozen=True)
class FilingFetchResult:
    ticker: str
    filings_cached: int = 0
    insider_transactions: int = 0
    skipped: bool = False
    reason: str = ""


def ensure_sec_schema(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sec_filings (
            ticker TEXT NOT NULL,
            form_type TEXT NOT NULL,
            accession_number TEXT NOT NULL,
            filing_date TEXT,
            url TEXT,
            cached_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (ticker, accession_number)
        );
        CREATE TABLE IF NOT EXISTS insider_transactions (
            ticker TEXT NOT NULL,
            insider_name TEXT,
            insider_title TEXT,
            transaction_code TEXT,
            transaction_type TEXT,
            shares REAL,
            price REAL,
            transaction_date TEXT,
            ownership_type TEXT,
            is_open_market_purchase INTEGER NOT NULL DEFAULT 0,
            is_ceo_cfo INTEGER NOT NULL DEFAULT 0,
            cluster_buy INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'sec_form4'
        );
        """
    )
    connection.commit()


def _text(root: ET.Element, tag: str) -> str | None:
    for element in root.iter():
        if element.tag.split('}')[-1] == tag and element.text is not None:
            value = element.text.strip()
            if value:
                return value
    return None


def _children_text(element: ET.Element, tag: str) -> str | None:
    for child in element.iter():
        if child.tag.split('}')[-1] != tag:
            continue
        if child.text is not None:
            value = child.text.strip()
            if value:
                return value
        for grandchild in child.iter():
            if grandchild.tag.split('}')[-1] == 'value' and grandchild.text is not None:
                value = grandchild.text.strip()
                if value:
                    return value
    return None


def _float(value: str | None) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value.replace(',', ''))
    except ValueError:
        return None


def _is_ceo_cfo(title: str | None) -> bool:
    title_l = (title or '').lower()
    return any(token in title_l for token in ('ceo', 'cfo', 'chief executive', 'chief financial', 'principal executive', 'principal financial'))


def parse_form4_xml(xml_text: str, ticker: str | None = None) -> list[dict[str, object]]:
    """Parse Form 4 XML into normalized insider transaction dictionaries."""
    root = ET.fromstring(xml_text)
    resolved_ticker = (ticker or _text(root, 'issuerTradingSymbol') or '').upper().replace('.', '-')
    owner_name = _text(root, 'rptOwnerName')
    owner_title = _text(root, 'officerTitle') or _text(root, 'rptOwnerTitle')
    is_ceo_cfo = _is_ceo_cfo(owner_title)

    transactions: list[dict[str, object]] = []
    for tx in root.iter():
        if tx.tag.split('}')[-1] not in {'nonDerivativeTransaction', 'derivativeTransaction'}:
            continue
        code = _children_text(tx, 'transactionCode')
        shares = _float(_children_text(tx, 'transactionShares'))
        price = _float(_children_text(tx, 'transactionPricePerShare'))
        tx_date = _children_text(tx, 'transactionDate')
        acquired_disposed = (_children_text(tx, 'transactionAcquiredDisposedCode') or '').upper()
        ownership = _children_text(tx, 'directOrIndirectOwnership')
        open_market_purchase = code in OPEN_MARKET_PURCHASE_CODES and acquired_disposed == 'A'
        noise = code in GRANT_OR_EXERCISE_CODES and not open_market_purchase
        transaction_type = 'open_market_purchase' if open_market_purchase else ('grant_or_exercise' if noise else 'other')
        transactions.append({
            'ticker': resolved_ticker,
            'insider_name': owner_name,
            'insider_title': owner_title,
            'transaction_code': code,
            'transaction_type': transaction_type,
            'shares': shares,
            'price': price,
            'transaction_date': tx_date,
            'ownership_type': ownership,
            'is_open_market_purchase': bool(open_market_purchase),
            'is_ceo_cfo': bool(is_ceo_cfo),
            'cluster_buy': False,
            'dollar_value': (shares or 0.0) * (price or 0.0),
        })
    return flag_cluster_buying(transactions)


def flag_cluster_buying(transactions: Sequence[Mapping[str, object]], *, window_days: int = 30, min_insiders: int = 3) -> list[dict[str, object]]:
    """Flag open-market buys when at least N distinct insiders buy inside a rolling window."""
    rows = [dict(row) for row in transactions]
    purchases: list[tuple[int, date, str]] = []
    for idx, row in enumerate(rows):
        if not row.get('is_open_market_purchase'):
            continue
        try:
            tx_date = datetime.fromisoformat(str(row.get('transaction_date'))[:10]).date()
        except ValueError:
            continue
        insider = str(row.get('insider_name') or f'unknown-{idx}')
        purchases.append((idx, tx_date, insider))
    for idx, tx_date, _ in purchases:
        start = tx_date - timedelta(days=window_days)
        end = tx_date + timedelta(days=window_days)
        insiders = {insider for _, other_date, insider in purchases if start <= other_date <= end}
        rows[idx]['cluster_buy'] = len(insiders) >= min_insiders
    return rows


def upsert_insider_transactions(connection, transactions: Sequence[Mapping[str, object]]) -> int:
    ensure_sec_schema(connection)
    count = 0
    for row in transactions:
        connection.execute(
            """
            INSERT INTO insider_transactions(
                ticker, insider_name, insider_title, transaction_code, transaction_type,
                shares, price, transaction_date, ownership_type, is_open_market_purchase,
                is_ceo_cfo, cluster_buy, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get('ticker'), row.get('insider_name'), row.get('insider_title'), row.get('transaction_code'),
                row.get('transaction_type'), row.get('shares'), row.get('price'), row.get('transaction_date'),
                row.get('ownership_type'), int(bool(row.get('is_open_market_purchase'))),
                int(bool(row.get('is_ceo_cfo'))), int(bool(row.get('cluster_buy'))), row.get('source', 'sec_form4'),
            ),
        )
        count += 1
    connection.commit()
    return count


def sec_user_agent(config: Mapping[str, object] | None = None) -> str:
    data = (config or {}).get('data', {}) if isinstance(config, Mapping) else {}
    sec = data.get('sec', {}) if isinstance(data, Mapping) else {}
    env_name = str(sec.get('user_agent_env', 'SEC_USER_AGENT')) if isinstance(sec, Mapping) else 'SEC_USER_AGENT'
    user_agent = os.getenv(env_name, '').strip()
    if not user_agent:
        raise RuntimeError(f'Set {env_name} before calling SEC EDGAR endpoints')
    return user_agent


def sec_get_json(url: str, *, config: Mapping[str, object] | None = None, timeout: float = 20.0) -> dict[str, object]:
    headers = {'User-Agent': sec_user_agent(config), 'Accept-Encoding': 'gzip, deflate', 'Host': 'data.sec.gov'}
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def ingest_sec_filings(tickers: Iterable[str], connection, *, config: Mapping[str, object] | None = None, no_filings: bool = False) -> dict[str, int | bool | str]:
    """Create schema and return safe counts; real filing downloads are opt-in via configured UA."""
    ensure_sec_schema(connection)
    if no_filings:
        return {'skipped': True, 'reason': 'no_filings', 'filings_cached': 0, 'insider_transactions': 0}
    data = (config or {}).get('data', {}) if isinstance(config, Mapping) else {}
    sec = data.get('sec', {}) if isinstance(data, Mapping) else {}
    env_name = str(sec.get('user_agent_env', 'SEC_USER_AGENT')) if isinstance(sec, Mapping) else 'SEC_USER_AGENT'
    if not os.getenv(env_name, '').strip():
        return {'skipped': True, 'reason': 'missing_sec_user_agent', 'filings_cached': 0, 'insider_transactions': 0}
    return {'skipped': False, 'filings_cached': 0, 'insider_transactions': 0}
