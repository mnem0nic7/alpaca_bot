"""
Tests verifying that ReplayRunner delegates strategy decisions to evaluate_cycle()
rather than reimplementing the logic inline.

These tests focus on:
1. Entry signal detection comes from evaluate_cycle()
2. Stop-update decisions come from evaluate_cycle()
3. EOD exit decisions come from evaluate_cycle()
4. Simulation mechanics (fills, stop-hits, P&L) remain in runner.py
5. A custom signal_evaluator injected into ReplayRunner is forwarded to evaluate_cycle()
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import EntrySignal, ReplayScenario
from alpaca_bot.replay import ReplayRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        "ATR_PERIOD": "50",
    }
    values.update(overrides)
    return Settings.from_env(values)


def _make_daily_bars(symbol: str = "AAPL") -> list[Bar]:
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(days=i),
            open=89.0 + i,
            high=90.0 + i,
            low=88.0 + i,
            close=90.0 + i,
            volume=1_000_000 + i * 1000,
        )
        for i in range(20)
    ]


def _make_quiet_intraday_bars(symbol: str = "AAPL") -> list[Bar]:
    """20 flat intraday bars that will never trigger a breakout."""
    start = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(minutes=15 * i),
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=500,
        )
        for i in range(20)
    ]


def _make_scenario(
    intraday_bars: list[Bar],
    daily_bars: list[Bar] | None = None,
    symbol: str = "AAPL",
    starting_equity: float = 100_000.0,
) -> ReplayScenario:
    return ReplayScenario(
        name="test_scenario",
        symbol=symbol,
        starting_equity=starting_equity,
        daily_bars=daily_bars if daily_bars is not None else _make_daily_bars(symbol),
        intraday_bars=intraday_bars,
    )


# ---------------------------------------------------------------------------
# Test: custom signal_evaluator is respected (engine delegation)
# ---------------------------------------------------------------------------

def test_runner_uses_custom_signal_evaluator_to_suppress_all_entries() -> None:
    """
    If ReplayRunner accepts a signal_evaluator and passes it to evaluate_cycle(),
    then a no-op evaluator (always returns None) should produce no entries
    even when bars would otherwise trigger a signal.
    """
    def never_signals(
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> EntrySignal | None:
        return None

    # Build bars that would normally trigger a breakout (the golden scenario bars)
    from pathlib import Path
    golden_dir = Path(__file__).resolve().parent.parent / "golden"
    runner = ReplayRunner(make_settings(), signal_evaluator=never_signals)
    scenario = runner.load_scenario(golden_dir / "breakout_success.json")
    result = runner.run(scenario)

    entry_events = [e for e in result.events if e.event_type == IntentType.ENTRY_ORDER_PLACED]
    assert entry_events == [], (
        "Expected no entries when signal_evaluator always returns None, "
        f"got: {entry_events}"
    )


def test_runner_uses_custom_signal_evaluator_to_force_entry() -> None:
    """
    A signal_evaluator that always fires should produce an ENTRY_ORDER_PLACED
    event even when the bars do not have a natural breakout.
    """
    FIXED_SIGNAL_STOP = 101.01
    FIXED_SIGNAL_LIMIT = 101.12
    FIXED_INITIAL_STOP = 99.89

    bars = _make_quiet_intraday_bars()
    # We need at least 21 bars for signal_index >= 20 (BREAKOUT_LOOKBACK_BARS)
    extra_start = bars[-1].timestamp + timedelta(minutes=15)
    for i in range(5):
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=extra_start + timedelta(minutes=15 * i),
                open=100.0,
                high=100.5,
                low=99.5,
                close=100.0,
                volume=500,
            )
        )

    def always_signals(
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> EntrySignal | None:
        signal_bar = intraday_bars[signal_index]
        return EntrySignal(
            symbol=symbol,
            signal_bar=signal_bar,
            entry_level=100.0,
            relative_volume=3.0,
            stop_price=FIXED_SIGNAL_STOP,
            limit_price=FIXED_SIGNAL_LIMIT,
            initial_stop_price=FIXED_INITIAL_STOP,
        )

    runner = ReplayRunner(make_settings(), signal_evaluator=always_signals)
    scenario = _make_scenario(bars)
    result = runner.run(scenario)

    entry_events = [e for e in result.events if e.event_type == IntentType.ENTRY_ORDER_PLACED]
    assert len(entry_events) >= 1, (
        "Expected at least one ENTRY_ORDER_PLACED when signal_evaluator always fires"
    )
    assert entry_events[0].details["stop_price"] == FIXED_SIGNAL_STOP
    assert entry_events[0].details["limit_price"] == FIXED_SIGNAL_LIMIT
    assert entry_events[0].details["initial_stop_price"] == FIXED_INITIAL_STOP


# ---------------------------------------------------------------------------
# Test: stop-hit is still handled by simulation mechanics (not evaluate_cycle)
# ---------------------------------------------------------------------------

def test_runner_detects_stop_hit_via_simulation_mechanics() -> None:
    """
    Stop-hit exits are simulation mechanics, not engine decisions.
    When bar.low <= stop_price, runner should emit STOP_HIT regardless
    of what evaluate_cycle() returns.
    """
    # Create a scenario: one bar triggers an entry, next bar fills, then
    # a bar whose low dips below the initial stop.
    from pathlib import Path
    golden_dir = Path(__file__).resolve().parent.parent / "golden"
    runner = ReplayRunner(make_settings())
    scenario = runner.load_scenario(golden_dir / "breakout_success.json")
    result = runner.run(scenario)

    # The golden success scenario ends with EOD_EXIT, not STOP_HIT.
    # This test just confirms the mechanism exists.
    event_types = [e.event_type for e in result.events]
    # There should be fills and exits (either EOD or stop)
    assert IntentType.ENTRY_FILLED in event_types
    assert any(t in event_types for t in (IntentType.EOD_EXIT, IntentType.STOP_HIT))


def test_runner_emits_stop_hit_when_bar_low_crosses_stop_price() -> None:
    """
    Build a scenario where we can confirm STOP_HIT fires correctly.
    Use the one-trade-per-day test's bar structure which ends in a STOP_HIT.
    """
    from pathlib import Path
    golden_dir = Path(__file__).resolve().parent.parent / "golden"
    runner = ReplayRunner(make_settings())
    base_scenario = runner.load_scenario(golden_dir / "breakout_success.json")

    bars = []
    start = base_scenario.intraday_bars[0].timestamp.replace(hour=13, minute=30)
    for i in range(20):
        timestamp = start + i * (
            base_scenario.intraday_bars[1].timestamp - base_scenario.intraday_bars[0].timestamp
        )
        high = 108.5 + i * 0.08
        close = high - 0.2
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=timestamp,
                open=round(close - 0.1, 2),
                high=round(high, 2),
                low=round(close - 0.25, 2),
                close=round(close, 2),
                volume=1000 + i * 10,
            )
        )
    # Signal bar (index 19)
    bars[-1] = Bar(
        symbol="AAPL",
        timestamp=bars[-1].timestamp,
        open=109.55,
        high=110.0,
        low=109.35,
        close=109.75,
        volume=1190,
    )
    # Active/fill bar
    bars.append(
        Bar(
            symbol="AAPL",
            timestamp=bars[-1].timestamp.replace(hour=18, minute=30),
            open=109.8,
            high=111.0,
            low=109.7,
            close=110.8,
            volume=2000,
        )
    )
    # Fill happens here (open=111.02 > stop_price ~110.01)
    bars.append(
        Bar(
            symbol="AAPL",
            timestamp=bars[-1].timestamp.replace(hour=18, minute=45),
            open=111.02,
            high=111.2,
            low=110.95,
            close=111.05,
            volume=1800,
        )
    )
    # Stop hit: low crosses well below initial stop
    bars.append(
        Bar(
            symbol="AAPL",
            timestamp=bars[-1].timestamp.replace(hour=19, minute=0),
            open=110.0,
            high=110.3,
            low=109.5,
            close=109.8,
            volume=1900,
        )
    )

    scenario = ReplayScenario(
        name="stop_hit_test",
        symbol="AAPL",
        starting_equity=base_scenario.starting_equity,
        daily_bars=base_scenario.daily_bars,
        intraday_bars=bars,
    )
    result = runner.run(scenario)

    event_types = [e.event_type for e in result.events]
    assert IntentType.STOP_HIT in event_types, (
        f"Expected STOP_HIT in events, got: {event_types}"
    )


# ---------------------------------------------------------------------------
# Test: EOD exit comes from engine (evaluate_cycle EXIT intent)
# ---------------------------------------------------------------------------

def test_runner_emits_eod_exit_from_engine_decision() -> None:
    """
    EOD exit (FLATTEN_TIME reached) should come from the EXIT intent
    emitted by evaluate_cycle(). The runner processes it as an EOD_EXIT event.
    """
    from pathlib import Path
    golden_dir = Path(__file__).resolve().parent.parent / "golden"
    runner = ReplayRunner(make_settings())
    scenario = runner.load_scenario(golden_dir / "breakout_success.json")
    result = runner.run(scenario)

    event_types = [e.event_type for e in result.events]
    assert IntentType.EOD_EXIT in event_types, (
        f"Expected EOD_EXIT event from golden scenario, got: {event_types}"
    )
    eod_event = next(e for e in result.events if e.event_type == IntentType.EOD_EXIT)
    assert eod_event.details["exit_price"] == 112.5


# ---------------------------------------------------------------------------
# Test: stop update comes from engine (evaluate_cycle UPDATE_STOP intent)
# ---------------------------------------------------------------------------

def test_runner_emits_stop_updated_from_engine_decision() -> None:
    """
    Trailing stop updates should come from the UPDATE_STOP intent from
    evaluate_cycle(). Runner records these as STOP_UPDATED events.
    """
    from pathlib import Path
    golden_dir = Path(__file__).resolve().parent.parent / "golden"
    runner = ReplayRunner(make_settings())
    scenario = runner.load_scenario(golden_dir / "breakout_success.json")
    result = runner.run(scenario)

    event_types = [e.event_type for e in result.events]
    assert IntentType.STOP_UPDATED in event_types, (
        f"Expected STOP_UPDATED event, got: {event_types}"
    )
    stop_event = next(e for e in result.events if e.event_type == IntentType.STOP_UPDATED)
    assert stop_event.details["stop_price"] == 111.7


# ---------------------------------------------------------------------------
# Test: no duplicate inline logic — runner imports evaluate_cycle
# ---------------------------------------------------------------------------

def test_runner_module_imports_evaluate_cycle() -> None:
    """
    Verify the refactored runner imports and uses evaluate_cycle from core.engine.
    This is a structural test to ensure the delegation actually happens.
    """
    import inspect
    import alpaca_bot.replay.runner as runner_module

    source = inspect.getsource(runner_module)
    assert "evaluate_cycle" in source, (
        "runner.py should import and call evaluate_cycle from core.engine"
    )
    assert "from alpaca_bot.core.engine import" in source or (
        "from alpaca_bot.core" in source and "evaluate_cycle" in source
    ), "runner.py should import evaluate_cycle from alpaca_bot.core.engine"


# ---------------------------------------------------------------------------
# Test A1: gap-down stop-hit uses bar.open when open < stop_price
# ---------------------------------------------------------------------------

def test_stop_hit_gap_down_uses_bar_open_as_exit_price() -> None:
    """
    When bar.open < stop_price (gap-down through the stop), the exit price
    should be bar.open — not stop_price — because the first available fill
    is the open, which is already below the stop.

    The formula in _process_stop_hit is: exit_price = min(stop_price, bar.open)
    so a gap-down (bar.open < stop_price) yields bar.open.
    """
    from alpaca_bot.replay.runner import ReplayRunner, ReplayState, _simulate_buy_stop_limit_fill
    from alpaca_bot.domain.models import OpenPosition, ReplayEvent

    settings = make_settings()
    runner = ReplayRunner(settings)

    STOP_PRICE = 100.0
    OPEN_BELOW_STOP = 97.50  # gap-down through the stop
    BAR_LOW = 97.0           # also below stop

    # Build a position with stop_price=100.0
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
        entry_price=105.0,
        quantity=10,
        entry_level=104.0,
        initial_stop_price=STOP_PRICE,
        stop_price=STOP_PRICE,
        highest_price=105.0,
    )

    state = ReplayState(equity=100_000.0, position=position)
    events: list[ReplayEvent] = []

    # Bar that gaps down through the stop
    gap_down_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 14, 15, tzinfo=timezone.utc),
        open=OPEN_BELOW_STOP,
        high=98.0,
        low=BAR_LOW,
        close=97.8,
        volume=5000,
    )

    stop_was_hit = runner._process_stop_hit(bar=gap_down_bar, state=state, events=events)

    assert stop_was_hit is True, "Expected stop-hit to be detected"
    assert len(events) == 1
    stop_event = events[0]
    from alpaca_bot.domain.enums import IntentType
    assert stop_event.event_type == IntentType.STOP_HIT
    # Gap-down: open is below stop, so exit at open (not stop_price)
    assert stop_event.details["exit_price"] == round(OPEN_BELOW_STOP, 2), (
        f"Expected exit_price={OPEN_BELOW_STOP} (bar.open), "
        f"got {stop_event.details['exit_price']}"
    )
    assert stop_event.details["exit_price"] < STOP_PRICE, (
        "Gap-down exit price must be below the stop_price"
    )


# ---------------------------------------------------------------------------
# Test A2: multi-symbol — two runners produce independent event streams
# ---------------------------------------------------------------------------

def test_multi_symbol_runners_produce_independent_event_streams() -> None:
    """
    Two ReplayRunner instances for different symbols should produce events
    that reference only their own symbol — no cross-contamination.

    ReplayRunner handles one symbol at a time (the scenario holds one symbol).
    We create two separate runners with separate scenarios and verify isolation.
    """
    from alpaca_bot.domain.enums import IntentType

    settings = make_settings(SYMBOLS="AAPL,MSFT")

    # Build quiet bars for AAPL and MSFT independently
    aapl_bars = _make_quiet_intraday_bars(symbol="AAPL")
    msft_bars = _make_quiet_intraday_bars(symbol="MSFT")

    aapl_scenario = _make_scenario(aapl_bars, symbol="AAPL")
    msft_scenario = _make_scenario(msft_bars, daily_bars=_make_daily_bars("MSFT"), symbol="MSFT")

    aapl_runner = ReplayRunner(settings)
    msft_runner = ReplayRunner(settings)

    aapl_result = aapl_runner.run(aapl_scenario)
    msft_result = msft_runner.run(msft_scenario)

    # All AAPL events must reference AAPL only
    for event in aapl_result.events:
        assert event.symbol == "AAPL", (
            f"AAPL runner emitted event for symbol={event.symbol!r}"
        )

    # All MSFT events must reference MSFT only
    for event in msft_result.events:
        assert event.symbol == "MSFT", (
            f"MSFT runner emitted event for symbol={event.symbol!r}"
        )

    # Verify the runners share no state: running AAPL does not affect MSFT result
    # (re-run MSFT after AAPL and compare)
    msft_result2 = msft_runner.run(msft_scenario)
    assert [e.event_type for e in msft_result2.events] == [
        e.event_type for e in msft_result.events
    ], "Re-running MSFT scenario produced different events — state leaked from AAPL run"


# ---------------------------------------------------------------------------
# Test A3: entry signal on the last bar is silently skipped without error
# ---------------------------------------------------------------------------

def test_entry_signal_on_last_bar_is_skipped_silently() -> None:
    """
    When the final bar would trigger an entry signal, the runner should
    silently skip it (no ENTRY_ORDER_PLACED) because there is no next bar
    to act as the execution bar. No exception should be raised.
    """
    from typing import Sequence
    from alpaca_bot.domain.models import EntrySignal
    from alpaca_bot.domain.enums import IntentType

    # We need enough bars so signal_index qualifies, then force a signal on the
    # very last bar using always_signals.
    bars = _make_quiet_intraday_bars()
    # Add extra bars so signal_index can reach BREAKOUT_LOOKBACK_BARS (20)
    extra_start = bars[-1].timestamp + timedelta(minutes=15)
    for i in range(5):
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=extra_start + timedelta(minutes=15 * i),
                open=100.0,
                high=100.5,
                low=99.5,
                close=100.0,
                volume=500,
            )
        )

    last_signal_bar_timestamp = bars[-1].timestamp

    signal_count = {"n": 0}

    def signal_only_on_last_bar(
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> EntrySignal | None:
        bar = intraday_bars[signal_index]
        if bar.timestamp == last_signal_bar_timestamp:
            signal_count["n"] += 1
            return EntrySignal(
                symbol=symbol,
                signal_bar=bar,
                entry_level=100.0,
                relative_volume=3.0,
                stop_price=101.01,
                limit_price=101.12,
                initial_stop_price=99.89,
            )
        return None

    runner = ReplayRunner(make_settings(), signal_evaluator=signal_only_on_last_bar)
    scenario = _make_scenario(bars)

    # Must not raise
    result = runner.run(scenario)

    entry_events = [e for e in result.events if e.event_type == IntentType.ENTRY_ORDER_PLACED]
    assert entry_events == [], (
        "Entry signal on the last bar should be silently skipped — "
        f"got {entry_events}"
    )
    # The signal evaluator was actually called on the last bar
    assert signal_count["n"] >= 1, "signal_only_on_last_bar was never triggered"


# ---------------------------------------------------------------------------
# Test A4: stale WorkingEntryOrder persists silently without crash or fill
# ---------------------------------------------------------------------------

def test_stale_working_entry_order_persists_without_crash_or_fill() -> None:
    """
    A WorkingEntryOrder whose active_bar_timestamp doesn't match any
    subsequent bar should persist silently — no crash and no spurious fill.
    The order is simply skipped every bar because the timestamps never match.
    """
    from alpaca_bot.replay.runner import ReplayRunner, ReplayState
    from alpaca_bot.domain.models import WorkingEntryOrder, ReplayEvent
    from alpaca_bot.domain.enums import IntentType

    settings = make_settings()
    runner = ReplayRunner(settings)

    # A timestamp far in the future that will never match any bar
    stale_timestamp = datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc)

    stale_order = WorkingEntryOrder(
        symbol="AAPL",
        signal_timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
        active_bar_timestamp=stale_timestamp,
        stop_price=101.0,
        limit_price=101.25,
        initial_stop_price=99.5,
        entry_level=100.0,
        relative_volume=2.0,
    )

    state = ReplayState(equity=100_000.0, working_order=stale_order)
    events: list[ReplayEvent] = []

    # Run through several bars — none have timestamp matching stale_timestamp
    bars = _make_quiet_intraday_bars()

    for bar in bars:
        runner._process_existing_order(bar=bar, state=state, events=events)

    # Order must still be present (never expired, never filled)
    assert state.working_order is stale_order, (
        "Stale WorkingEntryOrder should persist when its active_bar_timestamp never matches"
    )
    # No events emitted
    fill_events = [e for e in events if e.event_type == IntentType.ENTRY_FILLED]
    expire_events = [e for e in events if e.event_type == IntentType.ENTRY_EXPIRED]
    assert fill_events == [], f"Unexpected fill events: {fill_events}"
    assert expire_events == [], f"Unexpected expire events: {expire_events}"
