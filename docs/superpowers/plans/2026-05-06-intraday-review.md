# Intra-Day Trade Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add intra-day performance digests (periodic Notifier messages during REGULAR session) and a consecutive-loss entry gate (circuit-breaker that disables entries after N consecutive losing trades).

**Architecture:** Pure functions in `daily_summary.py`; private methods on `RuntimeSupervisor`; session-level state via `dict[date, X]` in-process pattern. No schema changes. Both features are gated to REGULAR session only.

**Tech Stack:** Python, pytest, existing `Notifier`, existing `OrderStore.list_closed_trades`.

---

## Files

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add `intraday_digest_interval_cycles: int = 0` and `intraday_consecutive_loss_gate: int = 0` fields, parse from env, validate |
| `src/alpaca_bot/runtime/daily_summary.py` | Add `trailing_consecutive_losses()` and `build_intraday_digest()` pure functions |
| `src/alpaca_bot/runtime/supervisor.py` | Add `_session_cycle_count`, `_digest_sent_count`, `_consecutive_loss_gate_fired` instance vars; add `_maybe_fire_consecutive_loss_gate()` and `_maybe_send_intraday_digest()` private methods; wire both into `run_cycle_once()` |
| `tests/unit/test_intraday_review.py` | New test file covering all pure functions and supervisor methods |

---

## Task 1: Add Settings Fields

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`
- Test: `tests/unit/test_intraday_review.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_intraday_review.py` with settings tests:

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.runtime.daily_summary import trailing_consecutive_losses, build_intraday_digest
from alpaca_bot.storage import AuditEvent, DailySessionState
from alpaca_bot.execution import BrokerAccount


# ── Shared helpers ────────────────────────────────────────────────────────────

_SESSION_DATE = date(2026, 5, 6)
_NOW = datetime(2026, 5, 6, 18, 30, tzinfo=timezone.utc)  # 14:30 ET


def _make_settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
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
    base.update(overrides)
    return Settings.from_env(base)


def _trade(
    exit_fill: float = 155.0,
    entry_fill: float = 150.0,
    qty: int = 10,
    exit_time: str = "2026-05-06T14:00:00+00:00",
) -> dict:
    return {
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "qty": qty,
        "exit_time": exit_time,
    }


# ── Settings tests ────────────────────────────────────────────────────────────


def test_settings_intraday_digest_interval_cycles_default_zero():
    s = _make_settings()
    assert s.intraday_digest_interval_cycles == 0


def test_settings_intraday_consecutive_loss_gate_default_zero():
    s = _make_settings()
    assert s.intraday_consecutive_loss_gate == 0


def test_settings_intraday_digest_interval_cycles_parsed():
    s = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
    assert s.intraday_digest_interval_cycles == 60


def test_settings_intraday_consecutive_loss_gate_parsed():
    s = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
    assert s.intraday_consecutive_loss_gate == 3


def test_settings_intraday_digest_interval_cycles_negative_raises():
    with pytest.raises(ValueError, match="INTRADAY_DIGEST_INTERVAL_CYCLES"):
        _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="-1")


def test_settings_intraday_consecutive_loss_gate_negative_raises():
    with pytest.raises(ValueError, match="INTRADAY_CONSECUTIVE_LOSS_GATE"):
        _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="-1")
```

- [ ] **Step 2: Run the failing tests**

```bash
pytest tests/unit/test_intraday_review.py -v -k "test_settings"
```

Expected: **6 FAILED** — `intraday_digest_interval_cycles` and `intraday_consecutive_loss_gate` attributes don't exist on `Settings` yet.

- [ ] **Step 3: Add fields to the Settings dataclass**

In `src/alpaca_bot/config/__init__.py`, locate the last two fields in the dataclass (around line 149):

```python
    fractionable_symbols: frozenset[str] = field(default_factory=frozenset)
    # From env — configurable threshold; 0.0 = disabled (default)
    min_position_notional: float = 0.0
```

Change to:

```python
    fractionable_symbols: frozenset[str] = field(default_factory=frozenset)
    # From env — configurable threshold; 0.0 = disabled (default)
    min_position_notional: float = 0.0
    # Intra-day review: 0 = disabled (default)
    intraday_digest_interval_cycles: int = 0
    intraday_consecutive_loss_gate: int = 0
```

- [ ] **Step 4: Add parsing to from_env()**

In `src/alpaca_bot/config/__init__.py`, locate the end of the `cls(...)` constructor call in `from_env()`. Find the last field being set (it ends with `min_position_notional=...`):

```python
            min_position_notional=float(values.get("MIN_POSITION_NOTIONAL", "0.0")),
```

Change to:

```python
            min_position_notional=float(values.get("MIN_POSITION_NOTIONAL", "0.0")),
            intraday_digest_interval_cycles=int(
                values.get("INTRADAY_DIGEST_INTERVAL_CYCLES", "0")
            ),
            intraday_consecutive_loss_gate=int(
                values.get("INTRADAY_CONSECUTIVE_LOSS_GATE", "0")
            ),
```

- [ ] **Step 5: Add validation to validate()**

In `src/alpaca_bot/config/__init__.py`, locate the end of the `validate()` method (around line 499–502):

```python
        if self.min_position_notional < 0:
            raise ValueError(
                f"MIN_POSITION_NOTIONAL must be >= 0, got {self.min_position_notional}"
            )
```

Change to:

```python
        if self.min_position_notional < 0:
            raise ValueError(
                f"MIN_POSITION_NOTIONAL must be >= 0, got {self.min_position_notional}"
            )
        if self.intraday_digest_interval_cycles < 0:
            raise ValueError("INTRADAY_DIGEST_INTERVAL_CYCLES must be >= 0")
        if self.intraday_consecutive_loss_gate < 0:
            raise ValueError("INTRADAY_CONSECUTIVE_LOSS_GATE must be >= 0")
```

- [ ] **Step 6: Run the settings tests to confirm green**

```bash
pytest tests/unit/test_intraday_review.py -v -k "test_settings"
```

Expected: **6 PASSED**.

- [ ] **Step 7: Run the full test suite to confirm no regressions**

```bash
pytest -x -q
```

Expected: all existing tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_intraday_review.py
git commit -m "feat: add intraday_digest_interval_cycles and intraday_consecutive_loss_gate settings"
```

---

## Task 2: Add Pure Functions to daily_summary.py

**Files:**
- Modify: `src/alpaca_bot/runtime/daily_summary.py`
- Test: `tests/unit/test_intraday_review.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_intraday_review.py`:

```python

# ── trailing_consecutive_losses tests ─────────────────────────────────────────


class TestTrailingConsecutiveLosses:
    def test_empty_list_returns_zero(self):
        assert trailing_consecutive_losses([]) == 0

    def test_all_wins_returns_zero(self):
        trades = [
            _trade(exit_fill=155.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=156.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 0

    def test_all_losses_returns_count(self):
        trades = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            _trade(exit_fill=146.0, entry_fill=150.0, exit_time="2026-05-06T14:03:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 3

    def test_win_after_losses_returns_zero(self):
        """Most recent trade is a win — streak resets."""
        trades = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            _trade(exit_fill=160.0, entry_fill=150.0, exit_time="2026-05-06T14:03:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 0

    def test_losses_after_win_returns_loss_count(self):
        """Most recent trades are losses — streak counts them, stops at earlier win."""
        trades = [
            _trade(exit_fill=160.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:03:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 2

    def test_trades_missing_fills_are_skipped(self):
        """Trades without entry_fill or exit_fill are ignored (not counted as win or loss)."""
        trades = [
            {"entry_fill": None, "exit_fill": None, "qty": 10, "exit_time": "2026-05-06T14:01:00+00:00"},
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        # Only one scoreable trade, which is a loss → streak = 1
        assert trailing_consecutive_losses(trades) == 1

    def test_only_missing_fills_returns_zero(self):
        trades = [
            {"entry_fill": None, "exit_fill": None, "qty": 10, "exit_time": "2026-05-06T14:01:00+00:00"},
        ]
        assert trailing_consecutive_losses(trades) == 0

    def test_sorted_by_exit_time(self):
        """Streak is computed from exit_time order, not list order."""
        trades = [
            # This loss appears first in the list but has a later exit_time
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            # This win appears second but has an earlier exit_time
            _trade(exit_fill=160.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
        ]
        # After sorting by exit_time: win (14:01), loss (14:02) → most recent is loss → streak=1
        assert trailing_consecutive_losses(trades) == 1


# ── build_intraday_digest tests ───────────────────────────────────────────────


class TestBuildIntradayDigest:
    def _call(self, **overrides):
        defaults = dict(
            settings=_make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60"),
            trades=[],
            open_positions=[],
            baseline_equity=46_100.0,
            current_equity=46_242.80,
            cycle_num=60,
            timestamp=_NOW,
            session_date=_SESSION_DATE,
        )
        defaults.update(overrides)
        return build_intraday_digest(**defaults)

    def test_subject_contains_session_date_mode_and_time(self):
        subject, _ = self._call()
        assert "2026-05-06" in subject
        assert "paper" in subject
        assert "14:30" in subject  # _NOW is 18:30 UTC = 14:30 ET

    def test_subject_is_intraday_digest(self):
        subject, _ = self._call()
        assert "Intra-day digest" in subject

    def test_body_contains_cycle_info(self):
        _, body = self._call(cycle_num=60)
        assert "Cycle: 60/60" in body

    def test_body_with_zero_trades(self):
        _, body = self._call(trades=[])
        assert "Trades: 0" in body
        assert "Win rate" not in body

    def test_body_with_trades_shows_win_rate(self):
        trades = [
            _trade(exit_fill=155.0, entry_fill=150.0),  # win
            _trade(exit_fill=155.0, entry_fill=150.0),  # win
            _trade(exit_fill=155.0, entry_fill=150.0),  # win
            _trade(exit_fill=148.0, entry_fill=150.0),  # loss
        ]
        _, body = self._call(trades=trades)
        assert "75.0%" in body
        assert "3W / 1L" in body

    def test_body_shows_pnl(self):
        trades = [_trade(exit_fill=155.0, entry_fill=150.0, qty=10)]  # $50 gain
        _, body = self._call(trades=trades)
        assert "$50.00" in body

    def test_loss_limit_headroom_calculation(self):
        # baseline=46100, daily_loss_limit_pct=0.01 → limit=461.00
        # current=45900 → session_pnl=-200 → headroom=461-200=261.00
        _, body = self._call(
            settings=_make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60", DAILY_LOSS_LIMIT_PCT="0.01"),
            baseline_equity=46_100.0,
            current_equity=45_900.0,
        )
        assert "$261.00" in body
        assert "$461.00" in body

    def test_open_positions_listed(self):
        positions = [
            SimpleNamespace(symbol="AAPL", quantity=10, entry_price=182.50),
            SimpleNamespace(symbol="MSFT", quantity=5, entry_price=415.20),
        ]
        _, body = self._call(open_positions=positions)
        assert "Open positions: 2" in body
        assert "AAPL x10 @ 182.50" in body
        assert "MSFT x5 @ 415.20" in body

    def test_no_open_positions(self):
        _, body = self._call(open_positions=[])
        assert "Open positions: 0" in body
```

- [ ] **Step 2: Run the failing tests**

```bash
pytest tests/unit/test_intraday_review.py -v -k "TestTrailingConsecutiveLosses or TestBuildIntradayDigest"
```

Expected: **all FAILED** — `trailing_consecutive_losses` and `build_intraday_digest` don't exist yet.

- [ ] **Step 3: Implement the pure functions**

In `src/alpaca_bot/runtime/daily_summary.py`, change the first line from:

```python
from datetime import date
```

To:

```python
from datetime import date, datetime
```

Then append the two new functions at the end of the file (after `_fmt_pnl`):

```python


def trailing_consecutive_losses(trades: list[dict]) -> int:
    """Return the current trailing consecutive-loss count from today's closed trades.

    Trades without fill prices are skipped. Returns 0 if no trades or if the
    most-recent trade was a win.
    """
    scored = [t for t in trades if t.get("entry_fill") and t.get("exit_fill")]
    scored.sort(key=lambda t: t.get("exit_time") or "")
    streak = 0
    for t in reversed(scored):
        if not _is_win(t):
            streak += 1
        else:
            break
    return streak


def build_intraday_digest(
    *,
    settings: Settings,
    trades: list[dict],
    open_positions: list,
    baseline_equity: float,
    current_equity: float,
    cycle_num: int,
    timestamp: datetime,
    session_date: date,
) -> tuple[str, str]:
    """Build (subject, body) for the intra-day performance digest notification.

    Pure — no I/O, no side effects.
    """
    local_ts = timestamp.astimezone(settings.market_timezone)
    time_str = local_ts.strftime("%H:%M")
    mode = settings.trading_mode.value
    interval = settings.intraday_digest_interval_cycles

    subject = f"Intra-day digest — {session_date} {time_str} ET [{mode}]"

    scored = [t for t in trades if t.get("entry_fill") and t.get("exit_fill")]
    total_pnl = sum(_trade_pnl(t) for t in scored)
    wins = sum(1 for t in scored if _is_win(t))
    losses = len(scored) - wins

    lines: list[str] = []
    lines.append(f"Session: {session_date}  Cycle: {cycle_num}/{interval}")
    lines.append("")

    if scored:
        win_rate = wins / len(scored)
        lines.append(
            f"P&L: {_fmt_pnl(total_pnl)}  |  Trades: {len(trades)}  |  "
            f"Win rate: {win_rate:.1%}  ({wins}W / {losses}L)"
        )
    else:
        lines.append(f"P&L: {_fmt_pnl(0.0)}  |  Trades: {len(trades)}")

    loss_limit = settings.daily_loss_limit_pct * baseline_equity
    session_pnl = current_equity - baseline_equity
    headroom = max(0.0, loss_limit + session_pnl)
    lines.append(
        f"Loss limit headroom: {_fmt_pnl(headroom)} of {_fmt_pnl(loss_limit)} remaining"
    )
    lines.append("")

    if open_positions:
        parts = []
        for pos in open_positions:
            sym = getattr(pos, "symbol", "?")
            qty = getattr(pos, "quantity", "?")
            entry = getattr(pos, "entry_price", 0.0)
            parts.append(f"{sym} x{qty} @ {entry:.2f}")
        lines.append(f"Open positions: {len(open_positions)} ({', '.join(parts)})")
    else:
        lines.append("Open positions: 0")

    return subject, "\n".join(lines)
```

- [ ] **Step 4: Run the pure function tests to confirm green**

```bash
pytest tests/unit/test_intraday_review.py -v -k "TestTrailingConsecutiveLosses or TestBuildIntradayDigest"
```

Expected: **all PASSED**.

- [ ] **Step 5: Run the full test suite**

```bash
pytest -x -q
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/daily_summary.py tests/unit/test_intraday_review.py
git commit -m "feat: add trailing_consecutive_losses and build_intraday_digest pure functions"
```

---

## Task 3: Add Supervisor Instance Vars and Private Methods

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Test: `tests/unit/test_intraday_review.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_intraday_review.py`:

```python

# ── Supervisor test helpers ───────────────────────────────────────────────────


def _load_supervisor_api():
    module = import_module("alpaca_bot.runtime.supervisor")
    return module, module.RuntimeSupervisor


class _FakeConn:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _RecordingAuditStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)

    def load_latest(self, **kwargs) -> AuditEvent | None:
        return None

    def list_recent(self, **kwargs) -> list[AuditEvent]:
        return []

    def list_by_event_types(self, **kwargs) -> list[AuditEvent]:
        return []


class _RecordingOrderStore:
    def __init__(self, *, daily_pnl: float = 0.0, closed_trades: list | None = None) -> None:
        self._daily_pnl = daily_pnl
        self._closed_trades: list[dict] = closed_trades or []

    def daily_realized_pnl(self, **kwargs) -> float:
        return self._daily_pnl

    def daily_realized_pnl_by_symbol(self, **kwargs) -> dict[str, float]:
        return {}

    def list_by_status(self, **kwargs) -> list:
        return []

    def list_pending_submit(self, **kwargs) -> list:
        return []

    def list_closed_trades(self, **kwargs) -> list[dict]:
        return list(self._closed_trades)


class _RecordingSessionStateStore:
    def __init__(self) -> None:
        self.saved: list[DailySessionState] = []

    def load(self, *, session_date, trading_mode, strategy_version, strategy_name="breakout"):
        return None

    def save(self, state: DailySessionState) -> None:
        self.saved.append(state)

    def list_by_session(self, **kwargs) -> list:
        return []


class _RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


class _FakeTradingStatusStore:
    def load(self, **kwargs):
        return None


class _FakePositionStore:
    def list_all(self, **kwargs) -> list:
        return []

    def replace_all(self, **kwargs) -> None:
        pass


class _FakeStrategyFlagStore:
    def list_all(self, **kwargs) -> list:
        return []

    def load(self, **kwargs):
        return None


class _FakeWatchlistStore:
    def list_enabled(self, *args) -> list[str]:
        return ["AAPL"]

    def list_ignored(self, *args) -> list[str]:
        return []


def _make_supervisor(
    *,
    settings: Settings,
    closed_trades: list[dict] | None = None,
    notifier=None,
    session_state_store=None,
):
    _, RuntimeSupervisor = _load_supervisor_api()

    class _FakeBroker:
        def get_account(self):
            return BrokerAccount(equity=10_000.0, buying_power=20_000.0, trading_blocked=False)

        def list_open_orders(self):
            return []

    class _FakeMarketData:
        def get_stock_bars(self, **kwargs):
            return {}

        def get_daily_bars(self, **kwargs):
            return {}

    _order_store = _RecordingOrderStore(closed_trades=closed_trades or [])
    _sess_store = session_state_store or _RecordingSessionStateStore()
    _audit_store = _RecordingAuditStore()

    class _FakeRuntimeContext:
        connection = _FakeConn()
        store_lock = None
        order_store = _order_store
        trading_status_store = _FakeTradingStatusStore()
        position_store = _FakePositionStore()
        daily_session_state_store = _sess_store
        audit_event_store = _audit_store
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = _FakeWatchlistStore()

        def commit(self):
            pass

    sup = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntimeContext(),
        broker=_FakeBroker(),
        market_data=_FakeMarketData(),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0
        ),
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )
    sup._session_equity_baseline[_SESSION_DATE] = 10_000.0
    if notifier is not None:
        sup._notifier = notifier
    return sup, _FakeRuntimeContext()


# ── _maybe_fire_consecutive_loss_gate tests ───────────────────────────────────


class TestMaybeFireConsecutiveLossGate:
    def test_gate_disabled_does_not_fire(self):
        """INTRADAY_CONSECUTIVE_LOSS_GATE=0 → gate never fires."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="0")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE,
            consecutive_losses=5,
            timestamp=_NOW,
        )

        assert _SESSION_DATE not in sup._consecutive_loss_gate_fired
        assert notifier.sent == []

    def test_gate_fires_when_threshold_met(self):
        """consecutive_losses >= gate → adds to fired set, notifies."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE,
            consecutive_losses=3,
            timestamp=_NOW,
        )

        assert _SESSION_DATE in sup._consecutive_loss_gate_fired
        assert len(notifier.sent) == 1
        assert "3 consecutive losses" in notifier.sent[0][1]

    def test_gate_does_not_fire_below_threshold(self):
        """consecutive_losses < gate → no fire."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE,
            consecutive_losses=2,
            timestamp=_NOW,
        )

        assert _SESSION_DATE not in sup._consecutive_loss_gate_fired
        assert notifier.sent == []

    def test_gate_does_not_fire_twice(self):
        """Gate fires at most once per session date."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=3, timestamp=_NOW
        )
        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=4, timestamp=_NOW
        )

        assert len(notifier.sent) == 1

    def test_gate_appends_audit_event(self):
        """Firing the gate appends an intraday_consecutive_loss_gate audit event."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        sup, ctx = _make_supervisor(settings=settings)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=3, timestamp=_NOW
        )

        gate_events = [
            e for e in ctx.audit_event_store.appended
            if e.event_type == "intraday_consecutive_loss_gate"
        ]
        assert len(gate_events) == 1
        assert gate_events[0].payload["consecutive_losses"] == 3
        assert gate_events[0].payload["threshold"] == 3

    def test_gate_saves_entries_disabled_session_state(self):
        """Firing the gate saves DailySessionState with entries_disabled=True."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        sess_store = _RecordingSessionStateStore()
        sup, _ = _make_supervisor(settings=settings, session_state_store=sess_store)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=3, timestamp=_NOW
        )

        assert len(sess_store.saved) == 1
        assert sess_store.saved[0].entries_disabled is True
        assert sess_store.saved[0].session_date == _SESSION_DATE


# ── _maybe_send_intraday_digest tests ────────────────────────────────────────


class TestMaybeSendIntradayDigest:
    def test_digest_disabled_does_not_send(self):
        """INTRADAY_DIGEST_INTERVAL_CYCLES=0 → digest never sends."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="0")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_142.80,
            timestamp=_NOW,
        )

        assert notifier.sent == []

    def test_digest_sends_at_interval(self):
        """Sends when cycle_num % interval == 0 and cycle_num > 0 and trades present."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        assert len(notifier.sent) == 1
        assert "Intra-day digest" in notifier.sent[0][0]

    def test_digest_does_not_send_between_intervals(self):
        """No send when cycle_num % interval != 0."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 45  # not a multiple of 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        assert notifier.sent == []

    def test_digest_does_not_send_with_zero_trades(self):
        """No send when closed_trades is empty."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[],
            baseline_equity=10_000.0,
            current_equity=10_000.0,
            timestamp=_NOW,
        )

        assert notifier.sent == []

    def test_digest_does_not_send_at_cycle_zero(self):
        """No send at cycle 0 even if interval divides it."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 0

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        assert notifier.sent == []

    def test_digest_appends_audit_event(self):
        """Successful send appends intraday_digest_sent audit event."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, ctx = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        digest_events = [
            e for e in ctx.audit_event_store.appended
            if e.event_type == "intraday_digest_sent"
        ]
        assert len(digest_events) == 1
        assert digest_events[0].payload["cycle"] == 60
        assert digest_events[0].payload["digest_num"] == 1

    def test_digest_notifier_failure_does_not_raise(self):
        """Notifier failure is logged, not propagated."""
        class _FailingNotifier:
            def send(self, *args, **kwargs):
                raise RuntimeError("SMTP down")

        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        sup, _ = _make_supervisor(settings=settings, notifier=_FailingNotifier())
        sup._session_cycle_count[_SESSION_DATE] = 60

        # Must not raise
        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )
```

- [ ] **Step 2: Run the failing tests**

```bash
pytest tests/unit/test_intraday_review.py -v -k "TestMaybe"
```

Expected: **all FAILED** — `_maybe_fire_consecutive_loss_gate`, `_maybe_send_intraday_digest`, `_consecutive_loss_gate_fired`, `_session_cycle_count`, `_digest_sent_count` don't exist on `RuntimeSupervisor` yet.

- [ ] **Step 3: Add instance variables to RuntimeSupervisor.__init__()**

In `src/alpaca_bot/runtime/supervisor.py`, locate the `__init__` instance vars block (around lines 120-139). Find the last line:

```python
        self._shutdown_requested: bool = False
```

Change to:

```python
        self._shutdown_requested: bool = False
        # Intra-day review: keyed by session_date; reset to new dict each process lifetime.
        self._session_cycle_count: dict[date, int] = {}
        self._digest_sent_count: dict[date, int] = {}
        self._consecutive_loss_gate_fired: set[date] = set()
```

Note: `date` is already imported in supervisor.py (`from datetime import date, datetime, ...`).

- [ ] **Step 4: Add _maybe_fire_consecutive_loss_gate() method**

In `src/alpaca_bot/runtime/supervisor.py`, locate the `_send_daily_summary()` method (around line 1287). Insert the new method immediately before it:

```python
    def _maybe_fire_consecutive_loss_gate(
        self,
        *,
        session_date: date,
        consecutive_losses: int,
        timestamp: datetime,
    ) -> None:
        """Fire the consecutive-loss entry gate if threshold is met and not yet fired today."""
        gate = self.settings.intraday_consecutive_loss_gate
        if gate == 0:
            return
        if session_date in self._consecutive_loss_gate_fired:
            return
        if consecutive_losses < gate:
            return
        self._consecutive_loss_gate_fired.add(session_date)
        baseline_equity = self._session_equity_baseline.get(session_date, 0.0)
        try:
            self._save_session_state(
                DailySessionState(
                    session_date=session_date,
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
                    entries_disabled=True,
                    flatten_complete=False,
                    equity_baseline=baseline_equity,
                    updated_at=timestamp,
                )
            )
        except Exception:
            logger.exception("Failed to save session state after consecutive-loss gate fired")
        self._append_audit(
            AuditEvent(
                event_type="intraday_consecutive_loss_gate",
                payload={
                    "consecutive_losses": consecutive_losses,
                    "threshold": gate,
                    "timestamp": timestamp.isoformat(),
                },
                created_at=timestamp,
            )
        )
        if self._notifier is not None:
            try:
                self._notifier.send(
                    subject="Entries disabled — consecutive losses",
                    body=(
                        f"Entries disabled — {consecutive_losses} consecutive losses this session. "
                        "Resume manually to re-enable."
                    ),
                )
            except Exception:
                logger.exception("Notifier failed to send consecutive-loss gate alert")

```

- [ ] **Step 5: Add _maybe_send_intraday_digest() method**

In `src/alpaca_bot/runtime/supervisor.py`, insert the following method immediately after `_maybe_fire_consecutive_loss_gate()` and before `_send_daily_summary()`:

```python
    def _maybe_send_intraday_digest(
        self,
        *,
        session_date: date,
        closed_trades: list[dict],
        baseline_equity: float,
        current_equity: float,
        timestamp: datetime,
    ) -> None:
        """Send intra-day performance digest at configured cycle intervals."""
        if self._notifier is None:
            return
        interval = self.settings.intraday_digest_interval_cycles
        if interval == 0:
            return
        cycle_num = self._session_cycle_count.get(session_date, 0)
        if cycle_num == 0 or cycle_num % interval != 0:
            return
        if not closed_trades:
            return
        from alpaca_bot.runtime.daily_summary import build_intraday_digest

        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            open_positions = self.runtime.position_store.list_all(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )
        try:
            subject, body = build_intraday_digest(
                settings=self.settings,
                trades=closed_trades,
                open_positions=open_positions,
                baseline_equity=baseline_equity,
                current_equity=current_equity,
                cycle_num=cycle_num,
                timestamp=timestamp,
                session_date=session_date,
            )
            self._notifier.send(subject, body)
        except Exception:
            logger.exception("Notifier failed to send intraday digest for %s", session_date)
            return
        digest_num = cycle_num // interval
        self._digest_sent_count[session_date] = digest_num
        self._append_audit(
            AuditEvent(
                event_type="intraday_digest_sent",
                payload={
                    "cycle": cycle_num,
                    "digest_num": digest_num,
                    "timestamp": timestamp.isoformat(),
                },
                created_at=timestamp,
            )
        )

```

- [ ] **Step 6: Run the supervisor method tests to confirm green**

```bash
pytest tests/unit/test_intraday_review.py -v -k "TestMaybe"
```

Expected: **all PASSED**.

- [ ] **Step 7: Run the full test suite**

```bash
pytest -x -q
```

Expected: all existing tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_intraday_review.py
git commit -m "feat: add _maybe_fire_consecutive_loss_gate and _maybe_send_intraday_digest methods"
```

---

## Task 4: Wire Both Methods into run_cycle_once()

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Test: `tests/unit/test_intraday_review.py`

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/unit/test_intraday_review.py`:

```python

# ── run_cycle_once integration tests ─────────────────────────────────────────


from alpaca_bot.strategy.session import SessionType


class TestRunCycleOnceIntegration:
    def _run(self, *, settings, closed_trades=None, notifier=None):
        sup, _ = _make_supervisor(
            settings=settings,
            closed_trades=closed_trades or [],
            notifier=notifier,
        )
        sup.run_cycle_once(
            now=lambda: _NOW,
            session_type=SessionType.REGULAR,
        )
        return sup

    def test_gate_fires_and_entries_disabled_in_cycle(self):
        """After gate fires, run_cycle_once returns entries_disabled=True."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="2")
        losses = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        module, RuntimeSupervisor = _load_supervisor_api()
        sup, ctx = _make_supervisor(
            settings=settings,
            closed_trades=losses,
        )
        report = sup.run_cycle_once(
            now=lambda: _NOW,
            session_type=SessionType.REGULAR,
        )
        assert report.entries_disabled is True
        assert _SESSION_DATE in sup._consecutive_loss_gate_fired

    def test_gate_does_not_fire_during_non_regular_session(self):
        """Gate is skipped when session_type is AFTER_HOURS (non-REGULAR)."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="2")
        losses = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        sup, _ = _make_supervisor(settings=settings, closed_trades=losses)
        sup.run_cycle_once(
            now=lambda: _NOW,
            session_type=SessionType.AFTER_HOURS,
        )
        assert _SESSION_DATE not in sup._consecutive_loss_gate_fired

    def test_session_cycle_count_increments_only_during_regular(self):
        """_session_cycle_count increments each REGULAR cycle, not for AFTER_HOURS."""
        settings = _make_settings()
        sup, _ = _make_supervisor(settings=settings)

        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.REGULAR)
        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.REGULAR)
        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.AFTER_HOURS)

        assert sup._session_cycle_count.get(_SESSION_DATE, 0) == 2

    def test_digest_sends_at_interval_via_run_cycle_once(self):
        """Digest sends when session_cycle_count hits the interval."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="2")
        notifier = _RecordingNotifier()
        trades = [_trade()]
        sup, _ = _make_supervisor(settings=settings, closed_trades=trades, notifier=notifier)

        # First regular cycle: cycle_num=1, not a multiple of 2 → no send
        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.REGULAR)
        assert len(notifier.sent) == 0

        # Second regular cycle: cycle_num=2, 2 % 2 == 0 → send
        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.REGULAR)
        assert len(notifier.sent) == 1
        assert "Intra-day digest" in notifier.sent[0][0]
```

- [ ] **Step 2: Run the failing integration tests**

```bash
pytest tests/unit/test_intraday_review.py -v -k "TestRunCycleOnceIntegration"
```

Expected: **all FAILED** — the wiring into `run_cycle_once()` hasn't been added yet.

- [ ] **Step 3: Wire the gate and digest into run_cycle_once()**

In `src/alpaca_bot/runtime/supervisor.py`, locate the end of the daily loss limit notifier block
(around line 394) and the `status = self._effective_trading_status(...)` call immediately after it.
The code at the insertion point looks like:

```python
                except Exception:
                    logger.exception("Notifier failed to send daily loss limit alert")

        status = self._effective_trading_status(
            session_date=session_date, session_state=session_state
        )
        entries_disabled = (
```

Insert the following block between the `logger.exception("Notifier failed...")` line and
`status = self._effective_trading_status(...)` — this ensures the gate fires **before**
`entries_disabled` is computed, so `report.entries_disabled` reflects the gate state in the
same cycle it fires:

```python
        # Intra-day consecutive-loss gate and digest (REGULAR session only)
        _trades_for_review: list[dict] | None = None
        if session_type is SessionType.REGULAR:
            _intraday_lock = getattr(self.runtime, "store_lock", None)
            try:
                with _intraday_lock if _intraday_lock is not None else contextlib.nullcontext():
                    _trades_for_review = self.runtime.order_store.list_closed_trades(
                        trading_mode=self.settings.trading_mode,
                        strategy_version=self.settings.strategy_version,
                        session_date=session_date,
                        market_timezone=str(self.settings.market_timezone),
                    )
            except Exception:
                logger.exception(
                    "run_cycle_once: list_closed_trades raised — skipping gate and digest"
                )
        if _trades_for_review is not None:
            from alpaca_bot.runtime.daily_summary import trailing_consecutive_losses
            self._session_cycle_count.setdefault(session_date, 0)
            self._session_cycle_count[session_date] += 1
            cl_streak = trailing_consecutive_losses(_trades_for_review)
            if not daily_loss_limit_breached:
                self._maybe_fire_consecutive_loss_gate(
                    session_date=session_date,
                    consecutive_losses=cl_streak,
                    timestamp=timestamp,
                )
                self._maybe_send_intraday_digest(
                    session_date=session_date,
                    closed_trades=_trades_for_review,
                    baseline_equity=baseline_equity,
                    current_equity=account.equity,
                    timestamp=timestamp,
                )
```

- [ ] **Step 4: Add consecutive_loss_gate_fired to entries_disabled**

In the same file, locate the `entries_disabled` expression (around line 399–403):

```python
        entries_disabled = (
            status in {TradingStatusValue.CLOSE_ONLY, TradingStatusValue.HALTED}
            or bool(recovery_report.mismatches)
            or daily_loss_limit_breached
        )
```

Change to:

```python
        entries_disabled = (
            status in {TradingStatusValue.CLOSE_ONLY, TradingStatusValue.HALTED}
            or bool(recovery_report.mismatches)
            or daily_loss_limit_breached
            or session_date in self._consecutive_loss_gate_fired
        )
```

- [ ] **Step 5: Run the integration tests to confirm green**

```bash
pytest tests/unit/test_intraday_review.py -v -k "TestRunCycleOnceIntegration"
```

Expected: **all PASSED**.

- [ ] **Step 6: Run the full test suite**

```bash
pytest -x -q
```

Expected: all existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_intraday_review.py
git commit -m "feat: wire intraday consecutive-loss gate and digest into run_cycle_once"
```

---

## Verification

After all four tasks, run the complete test suite:

```bash
pytest -v
```

Expected: all existing tests pass plus the new `tests/unit/test_intraday_review.py` tests.

**Configuration to enable in production (add to `/etc/alpaca_bot/alpaca-bot.env`):**

```
# Send a digest every ~60 minutes (at the default 60s supervisor poll)
INTRADAY_DIGEST_INTERVAL_CYCLES=60

# Disable entries after 3 consecutive losses (0 = disabled, safe default for first deploy)
INTRADAY_CONSECUTIVE_LOSS_GATE=3
```

**Signal to watch after deploying:** `intraday_digest_sent` and `intraday_consecutive_loss_gate` events should appear in the audit log on the next trading session.

```sql
SELECT event_type, created_at, payload
FROM audit_events
WHERE event_type IN ('intraday_digest_sent', 'intraday_consecutive_loss_gate')
ORDER BY created_at DESC
LIMIT 20;
```
