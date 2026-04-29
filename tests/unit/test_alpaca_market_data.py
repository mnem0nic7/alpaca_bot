from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar
from alpaca_bot.execution.alpaca import AlpacaMarketDataAdapter


@dataclass
class RawBarStub:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class BarSetStub:
    def __init__(self, data: dict[str, list[RawBarStub]]) -> None:
        self.data = data


def _normalize_request(request_params: Any) -> Any:
    if isinstance(request_params, dict):
        return request_params
    tf = getattr(request_params, "timeframe", None)
    feed = getattr(request_params, "feed", None)
    start = getattr(request_params, "start", None)
    end = getattr(request_params, "end", None)
    return {
        "symbol_or_symbols": getattr(request_params, "symbol_or_symbols", None),
        "start": start.replace(tzinfo=timezone.utc) if start is not None and start.tzinfo is None else start,
        "end": end.replace(tzinfo=timezone.utc) if end is not None and end.tzinfo is None else end,
        "timeframe": {"amount": tf.amount, "unit": tf.unit.value} if tf is not None and not isinstance(tf, dict) else tf,
        "feed": feed.value if hasattr(feed, "value") else feed,
    }


class HistoricalClientStub:
    def __init__(self) -> None:
        self.requests: list[object] = []
        self.latest_trades: dict[str, object] = {}

    def get_stock_bars(self, request_params: object) -> BarSetStub:
        self.requests.append(_normalize_request(request_params))
        return BarSetStub(
            {
                "AAPL": [
                    RawBarStub(
                        symbol="AAPL",
                        timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
                        open=109.8,
                        high=111.0,
                        low=109.7,
                        close=110.8,
                        volume=2000,
                    )
                ]
            }
        )

    def get_stock_latest_trade(self, request_params: object) -> SimpleNamespace:
        self.requests.append(request_params)
        return SimpleNamespace(
            data={
                symbol: SimpleNamespace(price=trade.price)
                for symbol, trade in self.latest_trades.items()
            }
        )


def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MSFT,SPY",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "ALPACA_PAPER_API_KEY": "paper-key",
        "ALPACA_PAPER_SECRET_KEY": "paper-secret",
        "ALPACA_LIVE_API_KEY": "live-key",
        "ALPACA_LIVE_SECRET_KEY": "live-secret",
    }
    values.update(overrides)
    return Settings.from_env(values)


def test_market_data_adapter_from_settings_selects_credentials() -> None:
    seen: dict[str, object] = {}

    def factory(api_key: str, secret_key: str) -> HistoricalClientStub:
        seen.update(api_key=api_key, secret_key=secret_key)
        return HistoricalClientStub()

    adapter = AlpacaMarketDataAdapter.from_settings(
        make_settings(),
        historical_client_factory=factory,
    )

    assert isinstance(adapter, AlpacaMarketDataAdapter)
    assert seen == {"api_key": "paper-key", "secret_key": "paper-secret"}


def test_get_latest_prices_returns_prices_for_symbols() -> None:
    client = HistoricalClientStub()
    client.latest_trades = {
        "AAPL": SimpleNamespace(price=175.50),
        "MSFT": SimpleNamespace(price=420.00),
    }
    adapter = AlpacaMarketDataAdapter(client, settings=make_settings())
    prices = adapter.get_latest_prices(["AAPL", "MSFT"])
    assert prices == {"AAPL": 175.50, "MSFT": 420.00}


def test_get_latest_prices_empty_symbols_returns_empty_dict_without_network_call() -> None:
    client = HistoricalClientStub()
    adapter = AlpacaMarketDataAdapter(client, settings=make_settings())
    prices = adapter.get_latest_prices([])
    assert prices == {}
    assert client.requests == []


def test_get_latest_prices_symbol_absent_from_response_is_skipped() -> None:
    client = HistoricalClientStub()
    client.latest_trades = {"AAPL": SimpleNamespace(price=100.0)}
    adapter = AlpacaMarketDataAdapter(client, settings=make_settings())
    prices = adapter.get_latest_prices(["AAPL", "TSLA"])
    assert prices == {"AAPL": 100.0}


def test_get_stock_bars_returns_domain_bars_and_builds_request() -> None:
    client = HistoricalClientStub()
    adapter = AlpacaMarketDataAdapter(client, settings=make_settings())
    start = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    bars = adapter.get_stock_bars(
        symbols=["AAPL"],
        start=start,
        end=end,
        timeframe_minutes=15,
    )

    assert bars == {
        "AAPL": [
            Bar(
                symbol="AAPL",
                timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
                open=109.8,
                high=111.0,
                low=109.7,
                close=110.8,
                volume=2000,
            )
        ]
    }
    assert client.requests == [
        {
            "symbol_or_symbols": ["AAPL"],
            "start": start,
            "end": end,
            "timeframe": {"amount": 15, "unit": "Min"},
            "feed": "sip",
        }
    ]
