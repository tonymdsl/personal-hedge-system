"""Paper-first Alpaca broker abstraction."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import requests


class BrokerConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerSettings:
    mode: str = 'paper'
    paper_base_url: str = 'https://paper-api.alpaca.markets'
    live_base_url: str = 'https://api.alpaca.markets'
    allow_live_trading: bool = False


LIVE_RISK_ACKNOWLEDGEMENT = 'YES I UNDERSTAND THE RISKS'


def validate_live_guard(mode: str, *, allow_live_trading: bool = False, risk_acknowledgement: bool | str = False) -> None:
    acknowledged = risk_acknowledgement is True or risk_acknowledgement == LIVE_RISK_ACKNOWLEDGEMENT
    if mode.lower() == 'live' and (not allow_live_trading or not acknowledged):
        raise BrokerConfigError('Live trading is blocked unless config enables it and risk acknowledgement is explicit')


def settings_from_config(config: Mapping[str, object] | None = None) -> BrokerSettings:
    execution = (config or {}).get('execution', {}) if isinstance(config, Mapping) else {}
    alpaca = execution.get('alpaca', {}) if isinstance(execution, Mapping) else {}
    return BrokerSettings(
        mode=str(execution.get('mode', 'paper')) if isinstance(execution, Mapping) else 'paper',
        paper_base_url=str(alpaca.get('paper_base_url', 'https://paper-api.alpaca.markets')) if isinstance(alpaca, Mapping) else 'https://paper-api.alpaca.markets',
        live_base_url=str(alpaca.get('live_base_url', 'https://api.alpaca.markets')) if isinstance(alpaca, Mapping) else 'https://api.alpaca.markets',
        allow_live_trading=bool(execution.get('allow_live_trading', False)) if isinstance(execution, Mapping) else False,
    )


class PaperBroker:
    def __init__(self, settings: BrokerSettings | None = None):
        self.settings = settings or BrokerSettings()
        self.orders: list[dict[str, object]] = []

    def sync_positions(self) -> list[dict[str, object]]:
        return []

    def submit_order(self, order: Mapping[str, object]) -> dict[str, object]:
        payload = dict(order)
        payload.setdefault('status', 'accepted_paper')
        payload.setdefault('broker', 'paper')
        self.orders.append(payload)
        return payload

    def cancel_order(self, order_id: str) -> dict[str, object]:
        payload = {'order_id': order_id, 'status': 'cancelled_paper'}
        self.orders.append(payload)
        return payload


class AlpacaBroker(PaperBroker):
    def __init__(
        self,
        settings: BrokerSettings | None = None,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
        session: object | None = None,
        timeout: float = 20.0,
    ):
        super().__init__(settings)
        self.api_key = api_key or os.getenv('ALPACA_API_KEY', '')
        self.secret_key = secret_key or os.getenv('ALPACA_SECRET_KEY', '')
        self.session = session or requests.Session()
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return self.settings.live_base_url if self.settings.mode.lower() == 'live' else self.settings.paper_base_url

    @property
    def headers(self) -> dict[str, str]:
        return {'APCA-API-KEY-ID': self.api_key, 'APCA-API-SECRET-KEY': self.secret_key}

    def sync_positions(self) -> list[dict[str, object]]:
        payload = self._request('get', '/v2/positions')
        return list(payload) if isinstance(payload, list) else []

    def submit_order(self, order: Mapping[str, object]) -> dict[str, object]:
        payload = _alpaca_order_payload(order)
        response = self._request('post', '/v2/orders', json=payload)
        result = dict(response) if isinstance(response, Mapping) else {'status': 'submitted', 'raw': response}
        self.orders.append(result)
        return result

    def cancel_order(self, order_id: str) -> dict[str, object]:
        response = self._request('delete', f'/v2/orders/{order_id}')
        result = dict(response) if isinstance(response, Mapping) else {'order_id': order_id, 'status': 'cancelled'}
        self.orders.append(result)
        return result

    def get_asset(self, ticker: str) -> dict[str, object]:
        payload = self._request('get', f'/v2/assets/{ticker.upper()}')
        return dict(payload) if isinstance(payload, Mapping) else {}

    def _request(self, method: str, path: str, *, json: Mapping[str, object] | None = None, attempts: int = 3) -> object:
        url = self.base_url.rstrip('/') + path
        last_error: str | None = None
        for attempt in range(1, attempts + 1):
            try:
                request = getattr(self.session, method.lower())
                kwargs: dict[str, object] = {'headers': self.headers, 'timeout': self.timeout}
                if json is not None:
                    kwargs['json'] = dict(json)
                response = request(url, **kwargs)
                response.raise_for_status()
                if getattr(response, 'content', b'') == b'' and not hasattr(response, 'json'):
                    return {}
                return response.json()
            except Exception as exc:  # pragma: no cover - retry path depends on network client.
                last_error = _broker_error_detail(exc)
                if attempt == attempts:
                    break
        raise BrokerConfigError(f'Alpaca request failed after {attempts} attempts: {last_error}')


def _alpaca_order_payload(order: Mapping[str, object]) -> dict[str, object]:
    side = str(order.get('side', '')).lower()
    alpaca_side = 'sell' if side in {'sell', 'short'} else 'buy'
    payload = {
        'symbol': _normalize_alpaca_symbol(str(order.get('ticker', order.get('symbol', '')))),
        'side': alpaca_side,
        'qty': str(float(order.get('quantity', order.get('qty', 0)))),
        'type': 'limit',
        'time_in_force': str(order.get('time_in_force', 'day')),
        'limit_price': str(order.get('limit_price')),
    }
    return payload


def _normalize_alpaca_symbol(symbol: str) -> str:
    return symbol.upper().replace('-', '.')


def _broker_error_detail(exc: Exception) -> str:
    detail = str(exc)
    response = getattr(exc, 'response', None)
    body = getattr(response, 'text', None)
    if body:
        detail = f'{detail}; response_body={body}'
    return detail


def get_broker(config: Mapping[str, object] | None = None, *, risk_acknowledgement: bool | str = False) -> PaperBroker:
    settings = settings_from_config(config)
    validate_live_guard(settings.mode, allow_live_trading=settings.allow_live_trading, risk_acknowledgement=risk_acknowledgement)
    if not os.getenv('ALPACA_API_KEY') or not os.getenv('ALPACA_SECRET_KEY'):
        return PaperBroker(settings)
    return AlpacaBroker(settings)


def exponential_backoff(attempt: int, *, base: float = 0.25, cap: float = 5.0) -> float:
    return min(cap, base * (2 ** max(0, attempt - 1)))
