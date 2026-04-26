from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as time_type
from typing import Any, Callable, Mapping, Protocol, Sequence, TypeVar

_logger = logging.getLogger(__name__)

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.domain import Bar

_T = TypeVar("_T")

_RETRY_WAIT_SECONDS = [1, 2]  # wait before attempt 2, then attempt 3
_MAX_ATTEMPTS = 3


def _is_transient_error(exc: BaseException) -> bool:
    """Return True if *exc* looks like a rate-limit or transient network/server error."""
    msg = str(exc).lower()
    # Rate-limit signals
    if any(token in msg for token in ("429", "rate", "too many")):
        return True
    # 5xx server errors
    for code in ("500", "502", "503", "504"):
        if code in msg:
            return True
    # Network-level errors
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return False


def _retry_with_backoff(fn: Callable[[], _T]) -> _T:
    """Call *fn* and retry up to _MAX_ATTEMPTS times on transient errors.

    - Retries on 429/rate-limit, 5xx, and connection-level errors.
    - Re-raises immediately on any other exception (4xx that aren't 429,
      ValueError, etc.).
    - Waits 1 s before the second attempt and 2 s before the third.
    """
    last_exc: BaseException | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return fn()
        except Exception as exc:
            if not _is_transient_error(exc):
                raise
            last_exc = exc
            if attempt < len(_RETRY_WAIT_SECONDS):
                wait = _RETRY_WAIT_SECONDS[attempt]
                _logger.warning("Alpaca API transient error (attempt %d/%d), retrying in %ds: %s", attempt + 1, _MAX_ATTEMPTS, wait, exc)
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


class AlpacaCredentialsError(ValueError):
    pass


@dataclass(frozen=True)
class MarketClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True)
class MarketCalendarDay:
    session_date: date
    open_at: datetime
    close_at: datetime


@dataclass(frozen=True)
class BrokerOrder:
    client_order_id: str
    broker_order_id: str | None
    symbol: str
    side: str
    status: str
    quantity: int


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: int
    entry_price: float | None = None
    market_value: float | None = None


@dataclass(frozen=True)
class BrokerAccount:
    equity: float
    buying_power: float
    trading_blocked: bool


class TradingClientProtocol(Protocol):
    def get_account(self) -> Any: ...

    def get_clock(self) -> Any: ...

    def get_calendar(self, filters: Any | None = None) -> list[Any]: ...

    def get_orders(self, filter: Any | None = None) -> list[Any]: ...

    def get_all_positions(self) -> list[Any]: ...

    def submit_order(self, order_data: Any) -> Any: ...

    def replace_order_by_id(self, order_id: str, order_data: Any) -> Any: ...

    def cancel_order_by_id(self, order_id: str) -> None: ...


class HistoricalDataClientProtocol(Protocol):
    def get_stock_bars(self, request_params: Any) -> Any: ...


class TradingStreamProtocol(Protocol):
    def subscribe_trade_updates(self, handler: Any) -> None: ...

    def run(self) -> None: ...

    def stop(self) -> None: ...


def resolve_alpaca_credentials(settings: Settings) -> tuple[str, str, bool]:
    if settings.trading_mode is TradingMode.PAPER:
        api_key = settings.alpaca_paper_api_key
        secret_key = settings.alpaca_paper_secret_key
        paper = True
        missing_names = [
            name
            for name, value in (
                ("ALPACA_PAPER_API_KEY", api_key),
                ("ALPACA_PAPER_SECRET_KEY", secret_key),
            )
            if not value
        ]
    else:
        api_key = settings.alpaca_live_api_key
        secret_key = settings.alpaca_live_secret_key
        paper = False
        missing_names = [
            name
            for name, value in (
                ("ALPACA_LIVE_API_KEY", api_key),
                ("ALPACA_LIVE_SECRET_KEY", secret_key),
            )
            if not value
        ]

    if missing_names:
        raise AlpacaCredentialsError(
            "Missing required Alpaca credential(s): " + ", ".join(missing_names)
        )
    return api_key, secret_key, paper


class AlpacaExecutionAdapter:
    def __init__(
        self,
        trading_client: TradingClientProtocol | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings
        self._trading = trading_client or self._build_trading_client(
            settings if settings is not None else _fallback_settings()
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        trading_client_factory: Any | None = None,
    ) -> "AlpacaExecutionAdapter":
        if trading_client_factory is None:
            trading_client = cls._build_trading_client(settings)
        else:
            api_key, secret_key, paper = resolve_alpaca_credentials(settings)
            trading_client = trading_client_factory(api_key, secret_key, paper=paper)
        return cls(trading_client=trading_client, settings=settings)

    def get_market_clock(self) -> MarketClock:
        raw = _retry_with_backoff(self._trading.get_clock)
        return MarketClock(
            timestamp=_as_datetime(raw.timestamp),
            is_open=bool(raw.is_open),
            next_open=_as_datetime(raw.next_open),
            next_close=_as_datetime(raw.next_close),
        )

    def get_market_calendar(self, *, start: date, end: date) -> list[MarketCalendarDay]:
        try:
            from alpaca.trading.requests import GetCalendarRequest
        except ModuleNotFoundError:
            filters = {"start": start, "end": end}
        else:
            filters = GetCalendarRequest(start=start, end=end)

        raw_days = self._trading.get_calendar(filters)
        return [
            MarketCalendarDay(
                session_date=_as_date(item.date),
                open_at=_calendar_time(item.date, item.open),
                close_at=_calendar_time(item.date, item.close),
            )
            for item in raw_days
        ]

    def list_open_orders(self) -> list[BrokerOrder]:
        try:
            from alpaca.trading.requests import GetOrdersRequest
        except ModuleNotFoundError:
            filters = {"status": "open"}
        else:
            filters = GetOrdersRequest(status="open")
        raw_orders = _retry_with_backoff(lambda: self._trading.get_orders(filter=filters))
        return [
            BrokerOrder(
                client_order_id=str(getattr(order, "client_order_id", "")),
                broker_order_id=str(getattr(order, "id", "")) or None,
                symbol=str(order.symbol).upper(),
                side=str(order.side),
                status=str(order.status),
                quantity=int(float(order.qty)),
            )
            for order in raw_orders
        ]

    def list_positions(self) -> list[BrokerPosition]:
        raw_positions = _retry_with_backoff(self._trading.get_all_positions)
        return [
            BrokerPosition(
                symbol=str(position.symbol).upper(),
                quantity=int(float(position.qty)),
                entry_price=float(position.avg_entry_price)
                if getattr(position, "avg_entry_price", None) is not None
                else None,
                market_value=float(position.market_value)
                if getattr(position, "market_value", None) is not None
                else None,
            )
            for position in raw_positions
        ]

    def get_account(self) -> BrokerAccount:
        raw = _retry_with_backoff(self._trading.get_account)
        return BrokerAccount(
            equity=float(raw.equity),
            buying_power=float(raw.buying_power),
            trading_blocked=bool(raw.trading_blocked),
        )

    def submit_stop_limit_entry(
        self,
        *,
        symbol: str,
        quantity: int | None = None,
        qty: int | None = None,
        stop_price: float,
        limit_price: float,
        client_order_id: str,
    ) -> BrokerOrder:
        resolved_qty = _resolve_order_quantity(quantity=quantity, qty=qty)
        request = _stop_limit_order_request(
            symbol=symbol,
            quantity=resolved_qty,
            stop_price=stop_price,
            limit_price=limit_price,
            client_order_id=client_order_id,
            side="buy",
        )
        return _parse_broker_order(
            _retry_with_backoff(lambda: self._trading.submit_order(request))
        )

    def submit_stop_order(
        self,
        *,
        symbol: str,
        quantity: int | None = None,
        qty: int | None = None,
        stop_price: float,
        client_order_id: str,
    ) -> BrokerOrder:
        resolved_qty = _resolve_order_quantity(quantity=quantity, qty=qty)
        request = _stop_order_request(
            symbol=symbol,
            quantity=resolved_qty,
            stop_price=stop_price,
            client_order_id=client_order_id,
            side="sell",
        )
        return _parse_broker_order(
            _retry_with_backoff(lambda: self._trading.submit_order(request))
        )

    def submit_market_exit(
        self,
        *,
        symbol: str,
        quantity: int | None = None,
        qty: int | None = None,
        client_order_id: str,
    ) -> BrokerOrder:
        resolved_qty = _resolve_order_quantity(quantity=quantity, qty=qty)
        request = _market_order_request(
            symbol=symbol,
            quantity=resolved_qty,
            client_order_id=client_order_id,
            side="sell",
        )
        return _parse_broker_order(
            _retry_with_backoff(lambda: self._trading.submit_order(request))
        )

    def replace_order(
        self,
        *,
        order_id: str,
        quantity: int | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        client_order_id: str | None = None,
    ) -> BrokerOrder:
        request = _replace_order_request(
            quantity=quantity,
            limit_price=limit_price,
            stop_price=stop_price,
            client_order_id=client_order_id,
        )
        return _parse_broker_order(
            _retry_with_backoff(
                lambda: self._trading.replace_order_by_id(order_id, request)
            )
        )

    def cancel_order(self, order_id: str) -> None:
        _retry_with_backoff(lambda: self._trading.cancel_order_by_id(order_id))

    def get_calendar(self, *, start: date, end: date) -> list[MarketCalendarDay]:
        return self.get_market_calendar(start=start, end=end)

    def list_open_positions(self) -> list[BrokerPosition]:
        return self.list_positions()

    @staticmethod
    def _build_trading_client(settings: Settings) -> TradingClientProtocol:
        api_key, secret_key, paper = resolve_alpaca_credentials(settings)
        try:
            from alpaca.trading.client import TradingClient
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "alpaca-py is required for runtime Alpaca access. Install dependencies first."
            ) from exc
        return TradingClient(api_key, secret_key, paper=paper)


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise TypeError(f"Unsupported datetime value: {value!r}")


def _as_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(value, datetime):
        return value.date()
    raise TypeError(f"Unsupported date value: {value!r}")


def _calendar_time(raw_date: Any, raw_time: Any) -> datetime:
    if isinstance(raw_time, datetime):
        return raw_time
    if isinstance(raw_time, time_type):
        return datetime.combine(_as_date(raw_date), raw_time)
    if isinstance(raw_time, str):
        value = raw_time.strip()
        try:
            parsed_time = time_type.fromisoformat(value)
            return datetime.combine(_as_date(raw_date), parsed_time)
        except ValueError:
            return _as_datetime(value)
    raise TypeError(f"Unsupported calendar time value: {raw_time!r}")


class AlpacaBroker(AlpacaExecutionAdapter):
    pass


class AlpacaMarketDataAdapter:
    def __init__(
        self,
        historical_client: HistoricalDataClientProtocol | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings
        self._historical = historical_client or self._build_historical_client(
            settings if settings is not None else _fallback_settings()
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        historical_client_factory: Any | None = None,
    ) -> "AlpacaMarketDataAdapter":
        if historical_client_factory is None:
            historical_client = cls._build_historical_client(settings)
        else:
            api_key, secret_key, _paper = resolve_alpaca_credentials(settings)
            historical_client = historical_client_factory(api_key, secret_key)
        return cls(historical_client=historical_client, settings=settings)

    def get_stock_bars(
        self,
        *,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        timeframe_minutes: int,
    ) -> dict[str, list[Bar]]:
        request = _stock_bars_request(
            symbols=symbols,
            start=start,
            end=end,
            timeframe_minutes=timeframe_minutes,
            settings=self._settings if self._settings is not None else _fallback_settings(),
        )
        raw = _retry_with_backoff(lambda: self._historical.get_stock_bars(request))
        return _parse_barset(raw)

    def get_daily_bars(
        self,
        *,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[Bar]]:
        request = _stock_bars_request(
            symbols=symbols,
            start=start,
            end=end,
            timeframe_minutes=None,
            settings=self._settings if self._settings is not None else _fallback_settings(),
        )
        raw = _retry_with_backoff(lambda: self._historical.get_stock_bars(request))
        return _parse_barset(raw)

    @staticmethod
    def _build_historical_client(settings: Settings) -> HistoricalDataClientProtocol:
        api_key, secret_key, _paper = resolve_alpaca_credentials(settings)
        try:
            from alpaca.data.historical.stock import StockHistoricalDataClient
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "alpaca-py is required for runtime Alpaca market data access. Install dependencies first."
            ) from exc
        return StockHistoricalDataClient(api_key, secret_key)


class AlpacaTradingStreamAdapter:
    def __init__(self, stream: TradingStreamProtocol) -> None:
        self._stream = stream

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        stream_factory: Any | None = None,
    ) -> "AlpacaTradingStreamAdapter":
        api_key, secret_key, paper = resolve_alpaca_credentials(settings)
        if stream_factory is None:
            try:
                from alpaca.trading.stream import TradingStream
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "alpaca-py is required for runtime Alpaca trading stream access. Install dependencies first."
                ) from exc
            stream = TradingStream(api_key, secret_key, paper=paper)
        else:
            stream = stream_factory(api_key, secret_key, paper=paper)
        return cls(stream)

    def subscribe_trade_updates(self, handler: Any) -> None:
        self._stream.subscribe_trade_updates(handler)

    def run(self) -> None:
        self._stream.run()

    def stop(self) -> None:
        self._stream.stop()


def _fallback_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
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
        }
    )


def _stock_bars_request(
    *,
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
    timeframe_minutes: int | None,
    settings: Settings,
) -> Any:
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    except ModuleNotFoundError:
        timeframe = (
            {"amount": timeframe_minutes, "unit": "Min"}
            if timeframe_minutes is not None
            else {"amount": 1, "unit": "Day"}
        )
        return {
            "symbol_or_symbols": list(symbols),
            "start": start,
            "end": end,
            "timeframe": timeframe,
            "feed": settings.market_data_feed.value,
        }

    feed = {
        "iex": DataFeed.IEX,
        "sip": DataFeed.SIP,
    }[settings.market_data_feed.value]
    timeframe = (
        TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)
        if timeframe_minutes is not None
        else TimeFrame.Day
    )
    return StockBarsRequest(
        symbol_or_symbols=list(symbols),
        start=start,
        end=end,
        timeframe=timeframe,
        feed=feed,
    )


def _parse_barset(raw: Any) -> dict[str, list[Bar]]:
    data = raw.data if hasattr(raw, "data") else raw
    if not isinstance(data, Mapping):
        raise TypeError(f"Unsupported stock bar response: {type(raw)!r}")
    parsed: dict[str, list[Bar]] = {}
    for symbol, bars in data.items():
        parsed[str(symbol).upper()] = [
            Bar(
                symbol=str(getattr(bar, "symbol", symbol)).upper(),
                timestamp=_as_datetime(getattr(bar, "timestamp")),
                open=float(getattr(bar, "open")),
                high=float(getattr(bar, "high")),
                low=float(getattr(bar, "low")),
                close=float(getattr(bar, "close")),
                volume=float(getattr(bar, "volume")),
            )
            for bar in bars
        ]
    return parsed


def _parse_broker_order(raw: Any) -> BrokerOrder:
    return BrokerOrder(
        client_order_id=str(getattr(raw, "client_order_id", "")),
        broker_order_id=str(getattr(raw, "id", "")) or None,
        symbol=str(getattr(raw, "symbol")).upper(),
        side=str(getattr(raw, "side")),
        status=str(getattr(raw, "status")),
        quantity=int(float(getattr(raw, "qty"))),
    )


def _stop_limit_order_request(
    *,
    symbol: str,
    quantity: int,
    stop_price: float,
    limit_price: float,
    client_order_id: str,
    side: str,
) -> Any:
    try:
        from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
        from alpaca.trading.requests import StopLimitOrderRequest
    except ModuleNotFoundError:
        return {
            "symbol": symbol,
            "qty": quantity,
            "side": side,
            "type": "stop_limit",
            "time_in_force": "day",
            "client_order_id": client_order_id,
            "stop_price": stop_price,
            "limit_price": limit_price,
        }

    return StopLimitOrderRequest(
        symbol=symbol,
        qty=quantity,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        type=OrderType.STOP_LIMIT,
        time_in_force=TimeInForce.DAY,
        client_order_id=client_order_id,
        stop_price=stop_price,
        limit_price=limit_price,
    )


def _stop_order_request(
    *,
    symbol: str,
    quantity: int,
    stop_price: float,
    client_order_id: str,
    side: str,
) -> Any:
    try:
        from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
        from alpaca.trading.requests import StopOrderRequest
    except ModuleNotFoundError:
        return {
            "symbol": symbol,
            "qty": quantity,
            "side": side,
            "type": "stop",
            "time_in_force": "day",
            "client_order_id": client_order_id,
            "stop_price": stop_price,
        }

    return StopOrderRequest(
        symbol=symbol,
        qty=quantity,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        type=OrderType.STOP,
        time_in_force=TimeInForce.DAY,
        client_order_id=client_order_id,
        stop_price=stop_price,
    )


def _replace_order_request(
    *,
    quantity: int | None,
    limit_price: float | None,
    stop_price: float | None,
    client_order_id: str | None,
) -> Any:
    try:
        from alpaca.trading.requests import ReplaceOrderRequest
    except ModuleNotFoundError:
        payload: dict[str, Any] = {}
        if quantity is not None:
            payload["qty"] = quantity
        if limit_price is not None:
            payload["limit_price"] = limit_price
        if stop_price is not None:
            payload["stop_price"] = stop_price
        if client_order_id is not None:
            payload["client_order_id"] = client_order_id
        return payload

    return ReplaceOrderRequest(
        qty=quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        client_order_id=client_order_id,
    )


def _market_order_request(
    *,
    symbol: str,
    quantity: int,
    client_order_id: str,
    side: str,
) -> Any:
    try:
        from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
    except ModuleNotFoundError:
        return {
            "symbol": symbol,
            "qty": quantity,
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": client_order_id,
        }

    return MarketOrderRequest(
        symbol=symbol,
        qty=quantity,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        client_order_id=client_order_id,
    )


def _resolve_order_quantity(*, quantity: int | None, qty: int | None) -> int:
    resolved = quantity if quantity is not None else qty
    if resolved is None:
        raise ValueError("quantity/qty is required")
    return resolved
