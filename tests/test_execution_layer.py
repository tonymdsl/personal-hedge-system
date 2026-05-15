from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
import requests

import run_execution
from execution.broker import AlpacaBroker, BrokerConfigError, BrokerSettings, PaperBroker, _alpaca_order_payload, validate_live_guard
from execution.costs import slippage_rolling_summary, worst_fills
from execution.executor import build_limit_order, chunk_trade_by_adv, execute_trades
from execution.order_manager import OrderManager
from execution.short_check import ShortAvailabilityCache
from risk.pre_trade import Trade


def test_live_mode_requires_exact_risk_phrase() -> None:
    with pytest.raises(BrokerConfigError):
        validate_live_guard('live', allow_live_trading=True, risk_acknowledgement='YES')

    validate_live_guard('live', allow_live_trading=True, risk_acknowledgement='YES I UNDERSTAND THE RISKS')


def test_alpaca_broker_posts_limit_orders_to_paper_api() -> None:
    class Response:
        def __init__(self, payload: object):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Session:
        def __init__(self) -> None:
            self.posts: list[dict[str, object]] = []

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object], timeout: float) -> Response:
            self.posts.append({'url': url, 'headers': headers, 'json': json, 'timeout': timeout})
            return Response({'id': 'ord-1', 'status': 'accepted', **json})

    session = Session()
    broker = AlpacaBroker(BrokerSettings(mode='paper'), api_key='key', secret_key='secret', session=session)

    result = broker.submit_order({'ticker': 'AAPL', 'side': 'short', 'quantity': 5, 'limit_price': 99.9, 'time_in_force': 'day'})

    assert result['id'] == 'ord-1'
    assert session.posts[0]['url'].endswith('/v2/orders')
    assert session.posts[0]['headers']['APCA-API-KEY-ID'] == 'key'
    assert session.posts[0]['json'] == {
        'symbol': 'AAPL',
        'side': 'sell',
        'qty': '5.0',
        'type': 'limit',
        'time_in_force': 'day',
        'limit_price': '99.9',
    }


@pytest.mark.parametrize(
    ('ticker', 'expected_symbol'),
    [
        ('BF-B', 'BF.B'),
        ('BRK-B', 'BRK.B'),
        ('AAPL', 'AAPL'),
    ],
)
def test_alpaca_order_payload_normalizes_yahoo_class_share_symbols(ticker: str, expected_symbol: str) -> None:
    payload = _alpaca_order_payload({'ticker': ticker, 'side': 'buy', 'quantity': 1, 'limit_price': 10})

    assert payload['symbol'] == expected_symbol


def test_alpaca_broker_submits_normalized_class_share_symbol() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return {'id': 'ord-1', 'status': 'accepted'}

    class Session:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object], timeout: float) -> Response:
            self.payloads.append(json)
            return Response()

    session = Session()
    broker = AlpacaBroker(BrokerSettings(mode='paper'), api_key='key', secret_key='secret', session=session)

    broker.submit_order({'ticker': 'BF-B', 'side': 'buy', 'quantity': 1, 'limit_price': 10})

    assert session.payloads[0]['symbol'] == 'BF.B'


def test_alpaca_broker_error_includes_response_body_without_headers() -> None:
    class Response:
        content = b'{"code":42210000,"message":"fractional orders cannot be sold short"}'
        text = '{"code":42210000,"message":"fractional orders cannot be sold short"}'

        def raise_for_status(self) -> None:
            error = requests.HTTPError("422 Client Error")
            error.response = self
            raise error

    class Session:
        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object], timeout: float) -> Response:
            return Response()

    broker = AlpacaBroker(BrokerSettings(mode='paper'), api_key='key', secret_key='secret', session=Session())

    with pytest.raises(BrokerConfigError) as exc:
        broker.submit_order({'ticker': 'AAPL', 'side': 'short', 'quantity': 3.5, 'limit_price': 99.9})

    message = str(exc.value)
    assert "fractional orders cannot be sold short" in message
    assert "secret" not in message
    assert "APCA-API-SECRET-KEY" not in message


def test_limit_order_uses_prompt_buffer_timeout_and_polling() -> None:
    buy = build_limit_order(Trade('AAPL', 'buy', 10, 100))
    sell = build_limit_order(Trade('MSFT', 'sell', 10, 100))

    assert buy['limit_price'] == 100.1
    assert sell['limit_price'] == 99.9
    assert buy['timeout_seconds'] == 120
    assert buy['poll_interval_seconds'] == 5
    assert buy['max_retries'] == 3
    assert buy['signal_price'] == 100


@pytest.mark.parametrize(
    ('side', 'price', 'expected_limit_price'),
    [
        ('buy', 292.5, 292.79),
        ('sell', 292.8, 292.51),
        ('short', 292.8, 292.51),
        ('buy', 0.9876, 0.9886),
        ('sell', 0.9876, 0.9866),
        ('short', 0.9876, 0.9866),
    ],
)
def test_limit_order_rounds_to_alpaca_equity_tick_precision(side: str, price: float, expected_limit_price: float) -> None:
    order = build_limit_order(Trade('TICK', side, 10, price))

    assert order['limit_price'] == expected_limit_price


def test_executor_rounds_opening_shorts_down_to_whole_shares() -> None:
    results = execute_trades(
        [Trade('AAPL', 'short', quantity=3.569, price=100, weight=-0.01, dollar_adv=100_000_000)],
        dry_run=True,
        config={'risk': {'liquidity': {'min_dollar_adv': 100_000, 'max_trade_adv_pct': 0.05}}},
    )

    assert results[0]['status'] == 'dry_run'
    assert results[0]['quantity'] == 3


def test_executor_rejects_sub_one_share_opening_shorts_before_broker(tmp_path: Path) -> None:
    log_path = tmp_path / 'short_reject.jsonl'
    broker = PaperBroker()

    results = execute_trades(
        [Trade('AAPL', 'short', quantity=0.75, price=100, weight=-0.01, dollar_adv=100_000_000)],
        dry_run=False,
        broker=broker,
        order_log_path=log_path,
        config={'risk': {'liquidity': {'min_dollar_adv': 100_000, 'max_trade_adv_pct': 0.05}}},
    )

    assert results == [{'ticker': 'AAPL', 'status': 'rejected', 'reasons': ['short_quantity_less_than_one_share']}]
    assert broker.orders == []
    assert 'short_quantity_less_than_one_share' in log_path.read_text(encoding='utf-8')


def test_executor_keeps_chunked_opening_short_submissions_whole_share() -> None:
    broker = PaperBroker()

    results = execute_trades(
        [Trade('AAPL', 'short', quantity=4.25, price=100, weight=-0.01, dollar_adv=7_500)],
        dry_run=False,
        broker=broker,
        config={
            'risk': {'liquidity': {'min_dollar_adv': 100, 'max_trade_adv_pct': 0.20}},
            'execution': {'order_defaults': {'max_order_adv_pct': 0.02}},
        },
    )

    assert [order['status'] for order in results] == ['accepted_paper', 'accepted_paper', 'accepted_paper']
    assert [order['quantity'] for order in broker.orders] == [1.0, 1.0, 1.0]
    assert all(float(order['quantity']).is_integer() for order in broker.orders)


def test_executor_rejects_sub_one_share_opening_short_chunks_after_adv_chunking(tmp_path: Path) -> None:
    log_path = tmp_path / 'short_chunk_reject.jsonl'
    broker = PaperBroker()

    results = execute_trades(
        [Trade('AAPL', 'short', quantity=2.25, price=100, weight=-0.01, dollar_adv=4_000)],
        dry_run=False,
        broker=broker,
        order_log_path=log_path,
        config={
            'risk': {'liquidity': {'min_dollar_adv': 100, 'max_trade_adv_pct': 0.20}},
            'execution': {'order_defaults': {'max_order_adv_pct': 0.02}},
        },
    )

    assert results == [
        {'ticker': 'AAPL', 'status': 'rejected', 'reasons': ['short_chunk_quantity_less_than_one_share']},
        {'ticker': 'AAPL', 'status': 'rejected', 'reasons': ['short_chunk_quantity_less_than_one_share']},
        {'ticker': 'AAPL', 'status': 'rejected', 'reasons': ['short_chunk_quantity_less_than_one_share']},
    ]
    assert broker.orders == []
    assert log_path.read_text(encoding='utf-8').count('short_chunk_quantity_less_than_one_share') == 3


def test_executor_preserves_fractional_long_buys() -> None:
    results = execute_trades(
        [Trade('AAPL', 'buy', quantity=3.569, price=100, weight=0.01, dollar_adv=100_000_000)],
        dry_run=True,
        config={'risk': {'liquidity': {'min_dollar_adv': 100_000, 'max_trade_adv_pct': 0.05}}},
    )

    assert results[0]['status'] == 'dry_run'
    assert results[0]['quantity'] == pytest.approx(3.569)


def test_executor_rejects_buy_below_default_minimum_order_notional_before_broker(tmp_path: Path) -> None:
    log_path = tmp_path / 'tiny_order_reject.jsonl'
    broker = PaperBroker()

    results = execute_trades(
        [Trade('AAPL', 'buy', quantity=0.004, price=100, weight=0.0001, dollar_adv=100_000_000)],
        dry_run=False,
        broker=broker,
        order_log_path=log_path,
        config={'risk': {'liquidity': {'min_dollar_adv': 100_000, 'max_trade_adv_pct': 0.05}}},
    )

    assert results == [{'ticker': 'AAPL', 'status': 'rejected', 'reasons': ['order_notional_below_minimum']}]
    assert broker.orders == []
    assert 'order_notional_below_minimum' in log_path.read_text(encoding='utf-8')


def test_executor_uses_configured_minimum_order_notional_threshold() -> None:
    broker = PaperBroker()

    results = execute_trades(
        [Trade('AAPL', 'buy', quantity=1, price=4, weight=0.0001, dollar_adv=100_000_000)],
        dry_run=False,
        broker=broker,
        config={
            'risk': {'liquidity': {'min_dollar_adv': 100_000, 'max_trade_adv_pct': 0.05}},
            'execution': {'order_defaults': {'min_order_notional_usd': 5.0}},
        },
    )

    assert results == [{'ticker': 'AAPL', 'status': 'rejected', 'reasons': ['order_notional_below_minimum']}]
    assert broker.orders == []


@pytest.mark.parametrize(
    ('dry_run', 'expected_status', 'expected_submissions'),
    [
        (True, 'dry_run', 0),
        (False, 'accepted_paper', 1),
    ],
)
def test_executor_preserves_normal_order_submission_and_dry_run_semantics(
    dry_run: bool,
    expected_status: str,
    expected_submissions: int,
) -> None:
    broker = PaperBroker()

    results = execute_trades(
        [Trade('AAPL', 'buy', quantity=1, price=100, weight=0.01, dollar_adv=100_000_000)],
        dry_run=dry_run,
        broker=broker,
        config={'risk': {'liquidity': {'min_dollar_adv': 100_000, 'max_trade_adv_pct': 0.05}}},
    )

    assert results[0]['status'] == expected_status
    assert len(broker.orders) == expected_submissions


def test_executor_records_broker_rejection_and_continues_later_orders(tmp_path: Path) -> None:
    class FailingOnceBroker(PaperBroker):
        def __init__(self) -> None:
            super().__init__()
            self.secret_key = 'supersecret'

        def submit_order(self, order: dict[str, object]) -> dict[str, object]:
            if order['ticker'] == 'BF-B':
                raise BrokerConfigError('Alpaca request failed after 3 attempts: asset "BF.B" not found using supersecret')
            return super().submit_order(order)

    log_path = tmp_path / 'orders.jsonl'
    broker = FailingOnceBroker()

    results = execute_trades(
        [
            Trade('BF-B', 'buy', quantity=1, price=100, weight=0.01, dollar_adv=100_000_000),
            Trade('MSFT', 'buy', quantity=1, price=100, weight=0.01, dollar_adv=100_000_000),
        ],
        dry_run=False,
        broker=broker,
        order_log_path=log_path,
        config={'risk': {'liquidity': {'min_dollar_adv': 100_000, 'max_trade_adv_pct': 0.05}}},
    )

    assert [result['status'] for result in results] == ['rejected', 'accepted_paper']
    assert results[0]['ticker'] == 'BF-B'
    assert results[0]['reasons'] == ['broker_submit_failed']
    assert 'asset "BF.B" not found' in str(results[0]['error'])
    assert 'supersecret' not in str(results[0]['error'])
    assert results[1]['ticker'] == 'MSFT'
    log_entries = [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines()]
    assert [entry['status'] for entry in log_entries] == ['rejected', 'accepted_paper']
    assert log_entries[0]['reasons'] == ['broker_submit_failed']
    assert 'asset "BF.B" not found' in log_entries[0]['error']
    assert 'supersecret' not in log_entries[0]['error']


def test_executor_chunks_orders_above_two_percent_adv_and_logs(tmp_path: Path) -> None:
    log_path = tmp_path / 'orders.jsonl'
    trade = Trade('AAPL', 'buy', quantity=1_000, price=10, weight=0.01, dollar_adv=100_000_000)

    chunks = chunk_trade_by_adv(trade, max_order_adv_pct=0.02)
    assert len(chunks) == 1

    small_adv_trade = Trade('MSFT', 'buy', quantity=1_000, price=10, weight=0.01, dollar_adv=250_000)
    assert [chunk.quantity for chunk in chunk_trade_by_adv(small_adv_trade, max_order_adv_pct=0.02)] == [500, 500]

    results = execute_trades(
        [small_adv_trade],
        dry_run=True,
        order_log_path=log_path,
        config={'risk': {'liquidity': {'min_dollar_adv': 100_000, 'max_trade_adv_pct': 0.05}}},
    )

    assert len(results) == 2
    assert {row['status'] for row in results} == {'dry_run'}
    first_log = json.loads(log_path.read_text(encoding='utf-8').splitlines()[0])
    assert {'timestamp', 'ticker', 'side', 'shares', 'limit_price', 'fill_price', 'slippage_bps', 'status'}.issubset(first_log)


def test_short_availability_cache_has_ttl_and_executor_skips_unavailable(tmp_path: Path) -> None:
    log_path = tmp_path / 'shorts.jsonl'
    cache = ShortAvailabilityCache(ttl_days=7, log_path=log_path)
    cached_as_of = date.today().isoformat()
    still_valid_as_of = (date.today() + timedelta(days=3)).isoformat()
    expired_as_of = (date.today() + timedelta(days=8)).isoformat()
    cache.set('XYZ', shortable=False, easy_to_borrow=False, as_of=cached_as_of)

    results = execute_trades(
        [Trade('XYZ', 'short', quantity=100, price=10, weight=-0.01, dollar_adv=100_000_000)],
        dry_run=True,
        short_cache=cache,
    )

    assert results == [{'ticker': 'XYZ', 'status': 'rejected', 'reasons': ['short_unavailable']}]
    assert cache.get('XYZ', as_of=still_valid_as_of).shortable is False
    assert cache.get('XYZ', as_of=expired_as_of).source == 'expired_default_allow_paper'
    assert 'short_unavailable' in log_path.read_text(encoding='utf-8')


def test_slippage_rolling_summary_and_worst_fills() -> None:
    fills = pd.DataFrame(
        {
            'timestamp': ['2026-04-01', '2026-05-01', '2026-05-07'],
            'ticker': ['OLD', 'AAPL', 'MSFT'],
            'signal_price': [100, 100, 100],
            'fill_price': [102, 101, 103],
            'side': ['buy', 'buy', 'buy'],
            'quantity': [10, 10, 10],
        }
    )

    summary = slippage_rolling_summary(fills, as_of='2026-05-07', window_days=30)
    assert summary['average_bps'] == 200.0
    assert summary['total_dollar_cost'] == 40.0
    assert worst_fills(fills, n=2)['ticker'].tolist() == ['MSFT', 'OLD']


def test_order_manager_cancels_pending_with_broker_and_log(tmp_path: Path) -> None:
    broker = PaperBroker()
    manager = OrderManager()
    manager.record('ord-1', {'order_id': 'ord-1', 'status': 'pending'})
    manager.record('ord-2', {'order_id': 'ord-2', 'status': 'filled'})

    cancelled = manager.cancel_pending(broker=broker, log_path=tmp_path / 'orders.jsonl', reason='sigint')

    assert cancelled == 1
    assert manager.orders['ord-1']['status'] == 'cancelled'
    assert broker.orders[-1]['status'] == 'cancelled_paper'
    assert 'sigint' in (tmp_path / 'orders.jsonl').read_text(encoding='utf-8')


@pytest.mark.parametrize(
    ('current_weight', 'target_weight', 'side', 'expected_side'),
    [
        pytest.param(0.0, -0.05, 'sell', 'short', id='open_short_from_flat'),
        pytest.param(-0.02, -0.05, 'sell', 'short', id='increase_existing_short'),
        pytest.param(0.05, 0.02, 'sell', 'sell', id='sell_down_existing_long'),
        pytest.param(-0.05, -0.02, 'buy', 'cover', id='reduce_existing_short'),
    ],
)
def test_run_execution_maps_order_side_from_exposure_transition(
    current_weight: float,
    target_weight: float,
    side: str,
    expected_side: str,
) -> None:
    trade = run_execution.trade_from_order_row(
        {
            'ticker': 'AAPL',
            'current_weight': current_weight,
            'target_weight': target_weight,
            'delta_weight': target_weight - current_weight,
            'side': side,
            'shares': 3.5,
            'price': 100,
        }
    )

    assert trade.side == expected_side


def test_run_execution_splits_long_to_short_reversal_into_close_then_open_short() -> None:
    orders = pd.DataFrame(
        [
            {
                'ticker': 'AAPL',
                'current_weight': 0.05,
                'target_weight': -0.03,
                'delta_weight': -0.08,
                'side': 'sell',
                'shares': 8,
                'current_quantity': 5,
                'price': 100,
            }
        ]
    )

    trades = run_execution.trades_from_orders(orders)

    assert [(trade.side, trade.quantity, trade.weight, trade.is_closing) for trade in trades] == [
        ('sell', 5.0, pytest.approx(-0.05), True),
        ('short', 3.0, pytest.approx(-0.03), False),
    ]


def test_run_execution_splits_short_to_long_reversal_into_cover_then_buy() -> None:
    orders = pd.DataFrame(
        [
            {
                'ticker': 'AAPL',
                'current_weight': -0.05,
                'target_weight': 0.03,
                'delta_weight': 0.08,
                'side': 'buy',
                'shares': 8,
                'current_quantity': -5,
                'price': 100,
            }
        ]
    )

    trades = run_execution.trades_from_orders(orders)

    assert [(trade.side, trade.quantity, trade.weight, trade.is_closing) for trade in trades] == [
        ('cover', 5.0, pytest.approx(0.05), True),
        ('buy', 3.0, pytest.approx(0.03), False),
    ]


def test_executor_allows_large_closing_long_sell_without_position_opening_vetoes() -> None:
    broker = PaperBroker()

    results = execute_trades(
        [Trade('AAPL', 'sell', quantity=10, price=100, weight=-0.25, is_closing=True)],
        dry_run=False,
        broker=broker,
        config={'portfolio': {'max_position_weight': 0.05}},
    )

    assert results[0]['status'] == 'accepted_paper'
    assert broker.orders[0]['side'] == 'sell'


def test_run_execution_reads_order_csv_and_writes_dry_run_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    orders = tmp_path / 'orders.csv'
    log_path = tmp_path / 'execution.jsonl'
    orders.write_text(
        'ticker,current_weight,target_weight,delta_weight,delta_notional,side,price,dollar_adv\n'
        'AAA,0,0.02,0.02,2000,buy,100,100000000\n',
        encoding='utf-8',
    )

    exit_code = run_execution.main(['--orders-input', str(orders), '--orders-log', str(log_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['dry_run'] is True
    assert payload['orders'][0]['status'] == 'dry_run'
    assert log_path.exists()


def test_run_execution_returns_nonzero_when_broker_submission_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    orders = tmp_path / 'orders.csv'
    orders.write_text(
        'ticker,current_weight,target_weight,delta_weight,delta_notional,side,price,shares,dollar_adv\n'
        'AAPL,0,-0.02,-0.02,-2000,sell,100,20,100000000\n',
        encoding='utf-8',
    )

    def fake_execute_trades(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [{'ticker': 'AAPL', 'status': 'rejected', 'reasons': ['broker_submit_failed']}]

    monkeypatch.setattr(run_execution, 'execute_trades', fake_execute_trades)

    exit_code = run_execution.main(['--execute', '--orders-input', str(orders)])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['orders'][0]['reasons'] == ['broker_submit_failed']
