from __future__ import annotations

from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain import Bar, OpenPosition


def _make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://localhost/test",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "5",
        "BREAKOUT_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.01",
        "MAX_POSITION_PCT": "0.1",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.05",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "ATR_PERIOD": "5",
    }
    values.update(overrides)
    return Settings.from_env(values)


def _make_position(symbol: str = "AAPL") -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10,
        entry_level=100.0,
        initial_stop_price=98.0,
        stop_price=98.0,
    )


# 2026-05-01 10:15 ET = 14:15 UTC — within entry window, well before flatten time
_NOW = datetime(2026, 5, 1, 14, 15, tzinfo=timezone.utc)


def _fresh_bar(
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    volume: float = 10_000.0,
) -> Bar:
    """A today bar fresh relative to _NOW (10:00 ET = 14:00 UTC, 15 min old)."""
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=99.0,
        high=high if high is not None else close + 1.0,
        low=low if low is not None else close - 1.0,
        close=close,
        volume=volume,
    )


def _falling_daily_bars(n: int = 6) -> list[Bar]:
    """n daily bars with declining closes — trend filter fails (close < SMA)."""
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 1 + i, 21, 0, tzinfo=timezone.utc),
            open=100.0 - i,
            high=100.5 - i,
            low=99.0 - i,
            close=100.0 - i,
            volume=1_000_000,
        )
        for i in range(n)
    ]


def _rising_daily_bars(n: int = 6) -> list[Bar]:
    """n daily bars with rising closes — trend filter passes (close > SMA)."""
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 1 + i, 21, 0, tzinfo=timezone.utc),
            open=90.0 + i,
            high=91.0 + i,
            low=89.0 + i,
            close=90.0 + i,
            volume=1_000_000,
        )
        for i in range(n)
    ]


# ─── Trend filter reversal tests ──────────────────────────────────────────────

def test_trend_filter_exit_fires_when_filter_fails() -> None:
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={"AAPL": _falling_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].symbol == "AAPL"
    assert exits[0].reason == "viability_trend_filter_failed"


def test_trend_filter_exit_does_not_fire_when_disabled() -> None:
    settings = _make_settings()  # ENABLE_TREND_FILTER_EXIT defaults to false
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={"AAPL": _falling_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_trend_filter_exit_does_not_fire_when_filter_passes() -> None:
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_trend_filter_exit_does_not_fire_when_insufficient_daily_bars() -> None:
    # daily_sma_period=5 needs len>=6; only 3 bars → guard prevents exit
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={"AAPL": _falling_daily_bars(n=3)},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_trend_filter_exit_does_not_fire_when_no_daily_bars() -> None:
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_trend_filter_exit_does_not_double_emit_past_flatten_time() -> None:
    # At 15:46 ET the EOD flatten block fires first; viability check must not also fire.
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    flatten_now = datetime(2026, 5, 1, 19, 46, tzinfo=timezone.utc)  # 15:46 ET
    fresh_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 19, 30, tzinfo=timezone.utc),  # 15:30 ET, 16 min old
        open=99.0, high=102.0, low=98.0, close=101.0, volume=10_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=flatten_now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [fresh_bar]},
        daily_bars_by_symbol={"AAPL": _falling_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].reason == "eod_flatten"


# ─── VWAP breakdown exit tests ────────────────────────────────────────────────

def test_vwap_breakdown_exit_fires_when_close_below_vwap() -> None:
    # bar1: TP=(101+99+102)/3≈100.67, vol=50000 → dominates VWAP ~100.63
    # bar2: TP=(101+97+98)/3≈98.67, vol=1000 → close=98 < VWAP ≈100.63 → fires
    settings = _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true")
    bar1 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=99.0, close=102.0, volume=50_000.0,
    )
    bar2 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 15, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=97.0, close=98.0, volume=1_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar1, bar2]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].symbol == "AAPL"
    assert exits[0].reason == "viability_vwap_breakdown"


def test_vwap_breakdown_exit_does_not_fire_when_close_above_vwap() -> None:
    # single bar: TP=(102+100+103)/3≈101.67; close=103 > VWAP → no exit
    settings = _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true")
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=101.0, high=102.0, low=100.0, close=103.0, volume=10_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_vwap_breakdown_exit_does_not_fire_when_disabled() -> None:
    settings = _make_settings()  # ENABLE_VWAP_BREAKDOWN_EXIT defaults to false
    bar1 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=99.0, close=102.0, volume=50_000.0,
    )
    bar2 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 15, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=97.0, close=98.0, volume=1_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar1, bar2]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_vwap_breakdown_exit_does_not_fire_when_no_today_bars() -> None:
    # Yesterday bar is stale → stale bar guard fires before viability checks
    settings = _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true")
    yesterday_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 30, 14, 0, tzinfo=timezone.utc),
        open=99.0, high=101.0, low=97.0, close=95.0, volume=10_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [yesterday_bar]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_vwap_breakdown_exit_does_not_double_emit_past_flatten_time() -> None:
    settings = _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true")
    flatten_now = datetime(2026, 5, 1, 19, 46, tzinfo=timezone.utc)  # 15:46 ET
    bar1 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 19, 30, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=99.0, close=102.0, volume=50_000.0,
    )
    bar2 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 19, 45, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=97.0, close=98.0, volume=1_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=flatten_now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar1, bar2]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].reason == "eod_flatten"


# ─── Settings defaults ────────────────────────────────────────────────────────

def test_settings_enable_trend_filter_exit_defaults_false() -> None:
    assert _make_settings().enable_trend_filter_exit is False


def test_settings_enable_vwap_breakdown_exit_defaults_false() -> None:
    assert _make_settings().enable_vwap_breakdown_exit is False


def test_settings_enable_trend_filter_exit_can_be_enabled() -> None:
    assert _make_settings(ENABLE_TREND_FILTER_EXIT="true").enable_trend_filter_exit is True


def test_settings_enable_vwap_breakdown_exit_can_be_enabled() -> None:
    assert _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true").enable_vwap_breakdown_exit is True
