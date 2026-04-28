# Plan: Daily Summary Notification

Spec: `docs/superpowers/specs/2026-04-28-daily-summary.md`

---

## Task 1 — Create `src/alpaca_bot/runtime/daily_summary.py`

**File:** `src/alpaca_bot/runtime/daily_summary.py` (new file)

```python
from __future__ import annotations

from datetime import date

from alpaca_bot.config import Settings


def build_daily_summary(
    *,
    settings: Settings,
    order_store: object,
    position_store: object,
    session_date: date,
    daily_loss_limit_breached: bool,
) -> tuple[str, str]:
    """Build (subject, body) for the end-of-session summary notification.

    Pure read — no writes, no side effects.
    """
    trades = order_store.list_closed_trades(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        session_date=session_date,
        market_timezone=str(settings.market_timezone),
    )
    total_pnl: float = order_store.daily_realized_pnl(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        session_date=session_date,
        market_timezone=str(settings.market_timezone),
    )
    open_positions = position_store.list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )

    subject = (
        f"Daily session summary \u2014 {session_date} [{settings.trading_mode.value}]"
    )
    body = _build_body(
        settings=settings,
        session_date=session_date,
        trades=trades,
        total_pnl=total_pnl,
        open_positions=open_positions,
        daily_loss_limit_breached=daily_loss_limit_breached,
    )
    return subject, body


def _build_body(
    *,
    settings: Settings,
    session_date: date,
    trades: list[dict],
    total_pnl: float,
    open_positions: list,
    daily_loss_limit_breached: bool,
) -> str:
    lines: list[str] = []

    lines.append(
        f"Session: {session_date}  "
        f"Mode: {settings.trading_mode.value}  "
        f"Strategy: {settings.strategy_version}"
    )
    lines.append("")

    # --- P&L ---
    lines.append("--- P&L ---")
    lines.append(f"Realized PnL : {_fmt_pnl(total_pnl)}")
    lines.append(f"Trades       : {len(trades)}")
    if trades:
        wins = sum(1 for t in trades if _is_win(t))
        losses = len(trades) - wins
        win_rate = wins / len(trades)
        lines.append(f"Win rate     : {win_rate:.1%}  ({wins}W / {losses}L)")
    lines.append("")

    # --- Strategy Breakdown ---
    if trades:
        lines.append("--- Strategy Breakdown ---")
        by_strategy: dict[str, list[dict]] = {}
        for t in trades:
            name = t.get("strategy_name") or "breakout"
            by_strategy.setdefault(name, []).append(t)
        for name, group in by_strategy.items():
            strat_pnl = sum(_trade_pnl(t) for t in group)
            lines.append(f"{name:<12}: {len(group)} trades  {_fmt_pnl(strat_pnl)} PnL")
        lines.append("")

    # --- Positions at Close ---
    lines.append("--- Positions at Close ---")
    lines.append(f"Open positions: {len(open_positions)}")
    for pos in open_positions:
        symbol = getattr(pos, "symbol", "?")
        qty = getattr(pos, "quantity", "?")
        entry = getattr(pos, "entry_price", 0.0)
        stop = getattr(pos, "stop_price", 0.0)
        lines.append(f"  {symbol} x{qty} @ {entry:.2f} (stop {stop:.2f})")
    lines.append("")

    # --- Risk ---
    lines.append("--- Risk ---")
    lines.append(
        f"Daily loss limit breached: {'Yes' if daily_loss_limit_breached else 'No'}"
    )

    return "\n".join(lines)


def _is_win(trade: dict) -> bool:
    entry = trade.get("entry_fill")
    exit_ = trade.get("exit_fill")
    if entry is None or exit_ is None:
        return False
    return float(exit_) > float(entry)


def _trade_pnl(trade: dict) -> float:
    entry = trade.get("entry_fill")
    exit_ = trade.get("exit_fill")
    qty = trade.get("qty", 0)
    if entry is None or exit_ is None:
        return 0.0
    return (float(exit_) - float(entry)) * int(qty)


def _fmt_pnl(v: float) -> str:
    """Format as $X.XX or -$X.XX (never $-X.XX)."""
    if v < 0:
        return f"-${abs(v):.2f}"
    return f"${v:.2f}"
```

**Test command:** `pytest tests/unit/test_daily_summary.py -v -k "build_daily_summary"`

---

## Task 2 — Modify `src/alpaca_bot/runtime/supervisor.py`

**File:** `src/alpaca_bot/runtime/supervisor.py`

### 2a. Add two sets to `RuntimeSupervisor.__init__`

After the existing `self._loss_limit_alerted: set[date] = set()` line, add:

```python
        # Dates for which the daily session summary has already been sent.
        self._summary_sent: set[date] = set()
        # Dates on which at least one active cycle ran this process lifetime.
        self._session_had_active_cycle: set[date] = set()
```

### 2b. Add `_send_daily_summary()` private method

Add this method after `_list_pending_submit_orders()`:

```python
    def _send_daily_summary(self, *, session_date: date, timestamp: datetime) -> None:
        """Send end-of-session summary notification. Failures are logged, never raised."""
        if self._notifier is None:
            return
        from alpaca_bot.runtime.daily_summary import build_daily_summary

        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            subject, body = build_daily_summary(
                settings=self.settings,
                order_store=self.runtime.order_store,
                position_store=self.runtime.position_store,
                session_date=session_date,
                daily_loss_limit_breached=session_date in self._loss_limit_alerted,
            )
        try:
            self._notifier.send(subject, body)
        except Exception:
            logger.exception("Notifier failed to send daily summary for %s", session_date)
            return
        self._append_audit(
            AuditEvent(
                event_type="daily_summary_sent",
                payload={
                    "session_date": session_date.isoformat(),
                    "timestamp": timestamp.isoformat(),
                },
                created_at=timestamp,
            )
        )
```

### 2c. Modify `run_forever()` — active branch

In `run_forever()`, immediately after `timestamp = _resolve_now(cycle_now)` and before `if self._market_is_open():`, add:

```python
                session_date = _session_date(timestamp, self.settings)
```

Then in the active (`True`) branch, immediately after `session_date = _session_date(...)` is available (i.e., right before `try: cycle_report = self.run_cycle_once(...)`), add:

```python
                    self._session_had_active_cycle.add(session_date)
```

Full revised active-branch opening (the try block inside `if self._market_is_open():`):

```python
                if self._market_is_open():
                    self._session_had_active_cycle.add(session_date)
                    try:
                        cycle_report = self.run_cycle_once(now=lambda: timestamp)
                        ...
```

### 2d. Modify `run_forever()` — idle branch

In the `else:` branch (market closed), after the `idle_iterations += 1` line and before the `self._append_audit(...)` call, add the gate check:

```python
                else:
                    idle_iterations += 1
                    if (
                        session_date not in self._summary_sent
                        and session_date in self._session_had_active_cycle
                    ):
                        self._send_daily_summary(
                            session_date=session_date, timestamp=timestamp
                        )
                        self._summary_sent.add(session_date)
                    self._append_audit(
                        AuditEvent(
                            event_type="supervisor_idle",
                            payload={
                                "reason": "market_closed",
                                "timestamp": timestamp.isoformat(),
                            },
                            created_at=timestamp,
                        )
                    )
```

**Complete revised `run_forever()` inner loop skeleton:**

```python
            while True:
                if should_stop is not None and should_stop():
                    break
                if max_iterations is not None and iterations >= max_iterations:
                    break

                timestamp = _resolve_now(cycle_now)
                session_date = _session_date(timestamp, self.settings)
                if self._market_is_open():
                    self._session_had_active_cycle.add(session_date)
                    try:
                        cycle_report = self.run_cycle_once(now=lambda: timestamp)
                        self._consecutive_cycle_failures = 0
                    except Exception as exc:
                        # ... existing error handling unchanged ...
                    # ... existing stream watchdog unchanged ...
                    active_iterations += 1
                    self._append_audit(
                        AuditEvent(
                            event_type="supervisor_cycle",
                            payload={
                                "entries_disabled": cycle_report.entries_disabled,
                                "timestamp": timestamp.isoformat(),
                            },
                            created_at=timestamp,
                        )
                    )
                else:
                    idle_iterations += 1
                    if (
                        session_date not in self._summary_sent
                        and session_date in self._session_had_active_cycle
                    ):
                        self._send_daily_summary(
                            session_date=session_date, timestamp=timestamp
                        )
                        self._summary_sent.add(session_date)
                    self._append_audit(
                        AuditEvent(
                            event_type="supervisor_idle",
                            payload={
                                "reason": "market_closed",
                                "timestamp": timestamp.isoformat(),
                            },
                            created_at=timestamp,
                        )
                    )
                iterations += 1
                ...
```

**Test command:** `pytest tests/unit/test_daily_summary.py tests/unit/test_runtime_supervisor.py -v`

---

## Task 3 — Add `"daily_summary_sent"` to `ALL_AUDIT_EVENT_TYPES`

**File:** `src/alpaca_bot/web/service.py`

In `ALL_AUDIT_EVENT_TYPES`, append `"daily_summary_sent"` after `"stream_restart_failed"`:

```python
ALL_AUDIT_EVENT_TYPES = [
    "trading_status_changed",
    "strategy_flag_changed",
    "strategy_entries_changed",
    "supervisor_cycle",
    "supervisor_idle",
    "supervisor_cycle_error",
    "strategy_cycle_error",
    "trader_startup_completed",
    "daily_loss_limit_breached",
    "postgres_reconnected",
    "runtime_reconciliation_detected",
    "trade_update_stream_started",
    "trade_update_stream_stopped",
    "trade_update_stream_failed",
    "trade_update_stream_restarted",
    "stream_restart_failed",
    "daily_summary_sent",          # end-of-session summary notification
    "WATCHLIST_ADD",
    "WATCHLIST_REMOVE",
    "WATCHLIST_IGNORE",
    "WATCHLIST_UNIGNORE",
]
```

**Test command:** `pytest tests/unit/ -q -k "audit"` (verify no regressions in audit log tests)

---

## Task 4 — Create `tests/unit/test_daily_summary.py`

**File:** `tests/unit/test_daily_summary.py` (new file)

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.runtime.daily_summary import build_daily_summary


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://test/db",
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
        }
    )


SESSION_DATE = date(2026, 4, 28)


class FakeOrderStore:
    def __init__(self, *, trades=None, pnl: float = 0.0):
        self._trades = trades or []
        self._pnl = pnl

    def list_closed_trades(
        self,
        *,
        trading_mode,
        strategy_version,
        session_date,
        market_timezone="America/New_York",
    ) -> list[dict]:
        return list(self._trades)

    def daily_realized_pnl(
        self,
        *,
        trading_mode,
        strategy_version,
        session_date,
        market_timezone="America/New_York",
    ) -> float:
        return self._pnl


class FakePositionStore:
    def __init__(self, *, positions=None):
        self._positions = positions or []

    def list_all(self, *, trading_mode, strategy_version) -> list:
        return list(self._positions)


def _trade(
    symbol: str = "AAPL",
    strategy_name: str = "breakout",
    entry_fill: float = 150.0,
    exit_fill: float = 155.0,
    qty: int = 10,
) -> dict:
    return {
        "symbol": symbol,
        "strategy_name": strategy_name,
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "qty": qty,
    }


def _position(
    symbol: str = "AAPL",
    quantity: int = 10,
    entry_price: float = 150.0,
    stop_price: float = 148.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        quantity=quantity,
        entry_price=entry_price,
        stop_price=stop_price,
    )


# ---------------------------------------------------------------------------
# build_daily_summary tests
# ---------------------------------------------------------------------------


class TestBuildDailySummary:
    def test_zero_trades_no_positions(self):
        """Empty session: 0 trades, no positions, no loss limit breach."""
        settings = make_settings()
        subject, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(pnl=0.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "2026-04-28" in subject
        assert "Trades       : 0" in body
        assert "Win rate" not in body
        assert "Daily loss limit breached: No" in body
        assert "Open positions: 0" in body

    def test_positive_pnl_multiple_trades(self):
        """3W/1L → 75.0%  (3W / 1L)."""
        trades = [
            _trade(exit_fill=155.0),   # win
            _trade(exit_fill=155.0),   # win
            _trade(exit_fill=155.0),   # win
            _trade(exit_fill=148.0),   # loss
        ]
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(trades=trades, pnl=142.50),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "Trades       : 4" in body
        assert "75.0%" in body
        assert "3W / 1L" in body
        assert "$142.50" in body

    def test_per_strategy_breakdown(self):
        """Two strategies appear separately in the breakdown."""
        trades = [
            _trade(strategy_name="breakout"),
            _trade(strategy_name="breakout"),
            _trade(strategy_name="momentum"),
        ]
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(trades=trades, pnl=50.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "breakout" in body
        assert "momentum" in body

    def test_open_positions_at_close(self):
        """Two open positions are listed with symbol, qty, entry, stop."""
        positions = [
            _position("AAPL", quantity=10, entry_price=150.0, stop_price=148.0),
            _position("MSFT", quantity=5, entry_price=420.0, stop_price=415.0),
        ]
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(),
            position_store=FakePositionStore(positions=positions),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "Open positions: 2" in body
        assert "AAPL" in body
        assert "MSFT" in body

    def test_loss_limit_breached_true(self):
        """daily_loss_limit_breached=True shows 'Yes' in body."""
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=True,
        )
        assert "Daily loss limit breached: Yes" in body

    def test_subject_contains_session_date_and_mode(self):
        """Subject includes ISO date and trading_mode value."""
        settings = make_settings()
        subject, _ = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "2026-04-28" in subject
        assert "paper" in subject

    def test_negative_pnl_formats_correctly(self):
        """Negative PnL shows as -$42.00, not $-42.00."""
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(pnl=-42.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "-$42.00" in body
        assert "$-" not in body


# ---------------------------------------------------------------------------
# Supervisor trigger tests
# ---------------------------------------------------------------------------
#
# These tests use the fake-callable DI pattern: monkeypatching supervisor
# methods (startup, run_cycle_once, close) and controlling broker clock via
# a SequencedFakeBroker that returns open/closed states per call.
# ---------------------------------------------------------------------------


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.calls.append((subject, body))


class FailingNotifier:
    def send(self, subject: str, body: str) -> None:
        raise RuntimeError("notifier down")


class SequencedFakeBroker:
    """Returns is_open values from a fixed sequence; repeats last value when exhausted."""

    def __init__(self, open_sequence: list[bool]) -> None:
        self._seq = list(open_sequence)
        self._idx = 0

    def get_clock(self):
        is_open = self._seq[min(self._idx, len(self._seq) - 1)]
        self._idx += 1
        return SimpleNamespace(is_open=is_open)


def _make_supervisor(
    *,
    open_sequence: list[bool],
    notifier=None,
    cycle_timestamps: list[datetime] | None = None,
    runtime_context=None,
):
    from importlib import import_module
    from alpaca_bot.config import Settings
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor, SupervisorCycleReport

    settings = make_settings()

    if runtime_context is None:
        # Minimal runtime with recording audit store
        events: list = []
        runtime_context = SimpleNamespace(
            store_lock=None,
            order_store=FakeOrderStore(),
            position_store=FakePositionStore(),
            audit_event_store=SimpleNamespace(
                append=lambda e, *, commit=True: events.append(e)
            ),
            daily_session_state_store=None,
            trading_status_store=None,
            connection=SimpleNamespace(commit=lambda: None, rollback=lambda: None),
            _events=events,
        )

    broker = SequencedFakeBroker(open_sequence)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime_context,
        broker=broker,
        market_data=None,
        stream=None,
        close_runtime_fn=lambda _r: None,
        connection_checker=lambda _c: True,
        notifier=notifier,
    )
    return supervisor, runtime_context


_BASE_TS = datetime(2026, 4, 28, 19, 0, tzinfo=timezone.utc)  # after-close ET


class TestSupervisorDailySummaryTrigger:
    def _noop_cycle_report(self):
        from alpaca_bot.runtime.supervisor import SupervisorCycleReport
        from types import SimpleNamespace as SN
        return SupervisorCycleReport(
            entries_disabled=False,
            cycle_result=SN(intents=[]),
            dispatch_report={"submitted_count": 0},
        )

    def test_summary_sent_once_after_active_session_closes(self, monkeypatch):
        """open ×2 then closed ×2 → exactly one notifier call."""
        notifier = RecordingNotifier()
        supervisor, _ = _make_supervisor(
            open_sequence=[True, True, False, False],
            notifier=notifier,
        )
        report = self._noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        supervisor.run_forever(
            max_iterations=4,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        assert len(notifier.calls) == 1
        subject, _ = notifier.calls[0]
        assert "2026-04-28" in subject

    def test_summary_not_sent_if_market_never_opened(self, monkeypatch):
        """Supervisor started after close — no active cycle → no summary."""
        notifier = RecordingNotifier()
        supervisor, _ = _make_supervisor(
            open_sequence=[False, False, False],
            notifier=notifier,
        )
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)

        supervisor.run_forever(
            max_iterations=3,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        assert notifier.calls == []

    def test_summary_not_sent_twice_same_day(self, monkeypatch):
        """open→closed→open→closed on same session_date → exactly 1 call."""
        notifier = RecordingNotifier()
        supervisor, _ = _make_supervisor(
            open_sequence=[True, False, True, False],
            notifier=notifier,
        )
        report = self._noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        supervisor.run_forever(
            max_iterations=4,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        assert len(notifier.calls) == 1

    def test_summary_sent_per_day_on_multi_day_run(self, monkeypatch):
        """Two trading days → exactly 2 notifier calls with distinct dates in subjects."""
        notifier = RecordingNotifier()
        supervisor, _ = _make_supervisor(
            open_sequence=[True, False, True, False],
            notifier=notifier,
        )
        report = self._noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        # Two different session dates: iter 0+1 on day 1, iter 2+3 on day 2
        _day1 = datetime(2026, 4, 28, 19, 0, tzinfo=timezone.utc)
        _day2 = datetime(2026, 4, 29, 19, 0, tzinfo=timezone.utc)
        call_counter = [0]

        def controlled_now():
            idx = call_counter[0]
            call_counter[0] += 1
            return _day1 if idx < 2 else _day2

        supervisor.run_forever(
            max_iterations=4,
            sleep_fn=lambda _: None,
            cycle_now=controlled_now,
        )

        assert len(notifier.calls) == 2
        subjects = [s for s, _ in notifier.calls]
        assert "2026-04-28" in subjects[0]
        assert "2026-04-29" in subjects[1]

    def test_summary_not_sent_when_notifier_is_none(self, monkeypatch):
        """notifier=None → no exception raised even after active session closes."""
        supervisor, _ = _make_supervisor(
            open_sequence=[True, False],
            notifier=None,
        )
        report = self._noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        # Should not raise
        supervisor.run_forever(
            max_iterations=2,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

    def test_notifier_failure_does_not_abort_loop(self, monkeypatch):
        """Notifier raising on send() → loop continues, audit event not appended."""
        notifier = FailingNotifier()
        supervisor, ctx = _make_supervisor(
            open_sequence=[True, False, False],
            notifier=notifier,
        )
        report = self._noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        # Should not raise despite notifier failure
        supervisor.run_forever(
            max_iterations=3,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        # No daily_summary_sent audit event appended
        assert not any(
            getattr(e, "event_type", None) == "daily_summary_sent"
            for e in ctx._events
        )

    def test_audit_event_appended_on_success(self, monkeypatch):
        """Successful send → daily_summary_sent audit event with session_date payload."""
        notifier = RecordingNotifier()
        supervisor, ctx = _make_supervisor(
            open_sequence=[True, False],
            notifier=notifier,
        )
        report = self._noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        supervisor.run_forever(
            max_iterations=2,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        summary_events = [
            e for e in ctx._events
            if getattr(e, "event_type", None) == "daily_summary_sent"
        ]
        assert len(summary_events) == 1
        assert summary_events[0].payload["session_date"] == "2026-04-28"
```

**Test command:** `pytest tests/unit/test_daily_summary.py -v`

---

## Task 5 — Run full test suite

```bash
pytest tests/unit/ -q
```

All pre-existing tests must pass. No regressions.

---

## Notes

- `build_daily_summary` is a pure function — no writes, no side effects. It's separately testable with `FakeOrderStore` / `FakePositionStore`.
- `_send_daily_summary()` uses an inline import of `build_daily_summary` to avoid a circular import at module load time (supervisor ← runtime/daily_summary ← config only, so no circle — but inline is cleaner anyway since it mirrors `recover_startup_state` usage).
- The `_session_had_active_cycle.add(session_date)` call is placed before the `try:` block so a cycle exception does not prevent the session from being registered as active. This means a day with only failed cycles still gets a summary (which is desirable — the operator should know cycles were failing).
- `_summary_sent` is in-memory. A supervisor restart mid-session-close will re-send. This is intentional per the spec; no DB flag or migration is needed.
- `daily_loss_limit_breached` is derived from `self._loss_limit_alerted` — no extra store query.
