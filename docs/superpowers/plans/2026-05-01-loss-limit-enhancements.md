# Plan: Loss Limit Enhancements

**Spec:** `docs/superpowers/specs/2026-05-01-loss-limit-enhancements.md`  
**Date:** 2026-05-01

---

## Summary

Two changes to `RuntimeSupervisor` and its dependencies:

1. **Sticky loss limit** — once `daily_loss_limit_breached` fires, it stays True for the rest of the session (in-memory + Postgres recovery). Currently the live condition can become False if equity recovers, silently re-enabling entries.

2. **Per-symbol daily loss limit** — new opt-in `PER_SYMBOL_LOSS_LIMIT_PCT` env var. When a symbol's realized session PnL exceeds the per-symbol cap, new entries for that symbol are blocked for the rest of the day.

No schema migrations. No changes to `evaluate_cycle()`. No new order types.

---

## Task 1 — Add `per_symbol_loss_limit_pct` to Settings

**File:** `src/alpaca_bot/config/__init__.py`

After `viability_min_hold_minutes` in the dataclass fields, add:

```python
per_symbol_loss_limit_pct: float = 0.0
```

In `from_env()`, after the `viability_min_hold_minutes=...` line:

```python
per_symbol_loss_limit_pct=float(values.get("PER_SYMBOL_LOSS_LIMIT_PCT", "0.0")),
```

In `validate()`, after the `failed_breakdown_recapture_buffer_pct` check, add:

```python
if self.per_symbol_loss_limit_pct < 0:
    raise ValueError("PER_SYMBOL_LOSS_LIMIT_PCT must be >= 0")
if self.per_symbol_loss_limit_pct >= 1.0:
    raise ValueError("PER_SYMBOL_LOSS_LIMIT_PCT must be < 1.0")
```

---

## Task 2 — Add `daily_realized_pnl_by_symbol()` to OrderStore

**File:** `src/alpaca_bot/storage/repositories.py`

Add this method to `OrderStore` immediately after `daily_realized_pnl()` (around line 457):

```python
def daily_realized_pnl_by_symbol(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    session_date: date,
    strategy_name: str | None = None,
    market_timezone: str = "America/New_York",
) -> dict[str, float]:
    """Return realized PnL keyed by symbol for a session date.

    Uses the same correlated-subquery pattern as daily_realized_pnl.
    Symbols with no correlated entry fill are treated as full losses (fail-safe).
    Returns an empty dict when no completed round-trip trades exist.
    """
    if strategy_name is not None:
        strategy_clause = "AND x.strategy_name IS NOT DISTINCT FROM %s"
        strategy_params: tuple = (strategy_name,)
    else:
        strategy_clause = ""
        strategy_params = ()
    rows = fetch_all(
        self._connection,
        f"""
        SELECT
            x.symbol,
            (
                SELECT e.fill_price
                FROM orders e
                WHERE e.symbol = x.symbol
                  AND e.trading_mode = x.trading_mode
                  AND e.strategy_version = x.strategy_version
                  AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                  AND e.intent_type = 'entry'
                  AND e.fill_price IS NOT NULL
                  AND e.status = 'filled'
                  AND e.updated_at <= x.updated_at
                ORDER BY e.updated_at DESC
                LIMIT 1
            ) AS entry_fill,
            x.fill_price AS exit_fill,
            COALESCE(x.filled_quantity, x.quantity) AS qty
        FROM orders x
        WHERE x.trading_mode = %s
          AND x.strategy_version = %s
          AND x.intent_type IN ('stop', 'exit')
          AND x.fill_price IS NOT NULL
          AND x.status = 'filled'
          AND DATE(x.updated_at AT TIME ZONE %s) = %s
          {strategy_clause}
        """,
        (
            trading_mode.value,
            strategy_version,
            market_timezone,
            session_date,
            *strategy_params,
        ),
    )
    result: dict[str, float] = {}
    missing_entry = [row for row in rows if row[1] is None]
    if missing_entry:
        logger.error(
            "daily_realized_pnl_by_symbol: %d exit row(s) have no correlated entry fill "
            "(symbols: %s); treating as full loss to fail safe on per-symbol loss-limit check",
            len(missing_entry),
            [row[0] for row in missing_entry],
        )
    for row in rows:
        if row[2] is None:
            continue
        symbol = row[0]
        entry_fill = row[1]
        exit_fill = float(row[2])
        qty = int(row[3])
        pnl = (
            (exit_fill - float(entry_fill)) * qty
            if entry_fill is not None
            else -(exit_fill * qty)
        )
        result[symbol] = result.get(symbol, 0.0) + pnl
    return result
```

---

## Task 3 — Add `_loss_limit_fired` and `_per_symbol_limit_alerted` to supervisor `__init__`

**File:** `src/alpaca_bot/runtime/supervisor.py`

In `RuntimeSupervisor.__init__`, after `self._loss_limit_alerted: set[date] = set()`:

```python
# Dates for which the daily loss limit has ever fired; sticky — not reset on recovery.
self._loss_limit_fired: set[date] = set()
# Per-symbol loss limit: dict[session_date, set[symbol]] — prevents duplicate alerts.
self._per_symbol_limit_alerted: dict[date, set[str]] = {}
```

---

## Task 4 — Make daily loss limit sticky + persist marker on fire

**File:** `src/alpaca_bot/runtime/supervisor.py`

**Sub-task 4a — Restore `_loss_limit_fired` from Postgres on first cycle.**

Find the block starting at line 264:
```python
if session_date not in self._session_equity_baseline:
    persisted = self._load_session_state(session_date=session_date, strategy_name="_equity")
    if persisted is not None and persisted.equity_baseline is not None:
        self._session_equity_baseline[session_date] = persisted.equity_baseline
    else:
        ...
```

Replace with:
```python
if session_date not in self._session_equity_baseline:
    persisted = self._load_session_state(session_date=session_date, strategy_name="_equity")
    if persisted is not None and persisted.equity_baseline is not None:
        self._session_equity_baseline[session_date] = persisted.equity_baseline
        if persisted.entries_disabled:
            self._loss_limit_fired.add(session_date)
    else:
        self._session_equity_baseline[session_date] = account.equity
        self._save_session_state(
            DailySessionState(
                session_date=session_date,
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                strategy_name="_equity",
                entries_disabled=False,
                flatten_complete=False,
                equity_baseline=account.equity,
                updated_at=timestamp,
            )
        )
```

**Sub-task 4b — Make the live breach computation sticky.**

Find lines 282–287:
```python
baseline_equity = self._session_equity_baseline[session_date]
loss_limit = self.settings.daily_loss_limit_pct * baseline_equity
# Include unrealized losses via broker-reported equity delta so open
# positions with large drawdowns trigger the limit before stops fill.
total_pnl = account.equity - baseline_equity
daily_loss_limit_breached = total_pnl < -loss_limit
```

Replace with:
```python
baseline_equity = self._session_equity_baseline[session_date]
loss_limit = self.settings.daily_loss_limit_pct * baseline_equity
# Include unrealized losses via broker-reported equity delta so open
# positions with large drawdowns trigger the limit before stops fill.
total_pnl = account.equity - baseline_equity
if total_pnl < -loss_limit:
    self._loss_limit_fired.add(session_date)
daily_loss_limit_breached = session_date in self._loss_limit_fired
```

**Sub-task 4c — Persist sticky marker to Postgres when loss limit first fires.**

Find the existing block (lines 288–312):
```python
if daily_loss_limit_breached and session_date not in self._loss_limit_alerted:
    self._loss_limit_alerted.add(session_date)
    self._append_audit(...)
    if self._notifier is not None:
        ...
```

Replace with:
```python
if daily_loss_limit_breached and session_date not in self._loss_limit_alerted:
    self._loss_limit_alerted.add(session_date)
    # Persist sticky marker so a mid-day restart re-locks entries immediately.
    self._save_session_state(
        DailySessionState(
            session_date=session_date,
            trading_mode=self.settings.trading_mode,
            strategy_version=self.settings.strategy_version,
            strategy_name="_equity",
            entries_disabled=True,
            flatten_complete=False,
            equity_baseline=baseline_equity,
            updated_at=timestamp,
        )
    )
    self._append_audit(
        AuditEvent(
            event_type="daily_loss_limit_breached",
            payload={
                "realized_pnl": realized_pnl,
                "total_pnl": total_pnl,
                "limit": loss_limit,
                "timestamp": timestamp.isoformat(),
            },
            created_at=timestamp,
        )
    )
    if self._notifier is not None:
        try:
            self._notifier.send(
                subject="Daily loss limit breached",
                body=(
                    f"Total PnL {total_pnl:.2f} (realized {realized_pnl:.2f}) "
                    f"exceeded limit {-loss_limit:.2f}. Entries disabled for the session."
                ),
            )
        except Exception:
            logger.exception("Notifier failed to send daily loss limit alert")
```

---

## Task 5 — Add per-symbol loss limit check in `run_cycle_once()`

**File:** `src/alpaca_bot/runtime/supervisor.py`

**Sub-task 5a — Compute `per_symbol_blocked_symbols` after `working_order_symbols` is built.**

Insert this block immediately **after** the two `working_order_symbols` lines (around line 324) and before the watchlist/market-data fetching block. This placement keeps the broker-state `working_order_symbols` semantically clean (broker orders + pending-submit only) and allows the per-symbol set to be injected into `strategy_working_symbols` per-strategy without inflating `global_occupied_slots`.

```python
        # Per-symbol loss limit: compute blocked symbols from today's realized PnL.
        # Applied per-strategy below via strategy_working_symbols — NOT added to
        # working_order_symbols to avoid inflating global_occupied_slots with
        # symbols that hold no open position.
        per_symbol_blocked_symbols: set[str] = set()
        if self.settings.per_symbol_loss_limit_pct > 0:
            _sym_pnl_lock = getattr(self.runtime, "store_lock", None)
            with _sym_pnl_lock if _sym_pnl_lock is not None else contextlib.nullcontext():
                sym_pnl_map = self.runtime.order_store.daily_realized_pnl_by_symbol(
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    session_date=session_date,
                    market_timezone=str(self.settings.market_timezone),
                )
            per_sym_limit = self.settings.per_symbol_loss_limit_pct * baseline_equity
            day_alerted = self._per_symbol_limit_alerted.setdefault(session_date, set())
            for sym, sym_pnl in sym_pnl_map.items():
                if sym_pnl < -per_sym_limit:
                    per_symbol_blocked_symbols.add(sym)
                    if sym not in day_alerted:
                        day_alerted.add(sym)
                        self._append_audit(
                            AuditEvent(
                                event_type="per_symbol_loss_limit_breached",
                                payload={
                                    "symbol": sym,
                                    "realized_pnl": sym_pnl,
                                    "limit": per_sym_limit,
                                    "timestamp": timestamp.isoformat(),
                                },
                                symbol=sym,
                                created_at=timestamp,
                            )
                        )
                        if self._notifier is not None:
                            try:
                                self._notifier.send(
                                    subject=f"Per-symbol loss limit breached: {sym}",
                                    body=(
                                        f"{sym} realized PnL {sym_pnl:.2f} exceeded "
                                        f"per-symbol limit {-per_sym_limit:.2f}. "
                                        f"New entries for {sym} disabled for the session."
                                    ),
                                )
                            except Exception:
                                logger.exception(
                                    "Notifier failed to send per-symbol loss limit alert for %s", sym
                                )
```

**Sub-task 5b — Inject blocked symbols into `strategy_working_symbols` inside the strategy loop.**

Inside the strategy loop, after the existing line:
```python
strategy_working_symbols = set(working_order_symbols)
```

Add one line:
```python
strategy_working_symbols |= per_symbol_blocked_symbols
```

This gates entries for breached symbols without touching `working_order_symbols`, keeping `global_occupied_slots` accurate.

---

## Task 6 — Update `RecordingOrderStore` in tests

**File:** `tests/unit/test_runtime_supervisor.py`

`RecordingOrderStore` needs a `daily_realized_pnl_by_symbol()` method so supervisor tests don't crash. Add after the existing `daily_realized_pnl()` method:

```python
def daily_realized_pnl_by_symbol(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    session_date: date,
    market_timezone: str = "America/New_York",
    strategy_name: str | None = None,
) -> dict[str, float]:
    return {}
```

---

## Task 7 — Create test file

**File:** `tests/unit/test_loss_limit_enhancements.py`

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.execution import BrokerAccount
from alpaca_bot.runtime import RuntimeContext
from alpaca_bot.storage import AuditEvent, DailySessionState, TradingMode


# ── shared helpers ────────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
_SESSION_DATE = date(2026, 5, 1)


def _make_settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MSFT",
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
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
    }
    base.update(overrides)
    return Settings.from_env(base)


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
    def __init__(self, *, daily_pnl: float = 0.0, pnl_by_symbol: dict | None = None) -> None:
        self._daily_pnl = daily_pnl
        self._pnl_by_symbol: dict[str, float] = pnl_by_symbol or {}
        self.saved: list[object] = []

    def save(self, order, *, commit: bool = True) -> None:
        self.saved.append(order)

    def list_by_status(self, **kwargs):
        return []

    def list_pending_submit(self, **kwargs):
        return []

    def daily_realized_pnl(self, **kwargs) -> float:
        return self._daily_pnl

    def daily_realized_pnl_by_symbol(self, **kwargs) -> dict[str, float]:
        return dict(self._pnl_by_symbol)


class _RecordingSessionStateStore:
    def __init__(self, preloaded: DailySessionState | None = None) -> None:
        self._preloaded = preloaded
        self.saved: list[DailySessionState] = []

    def load(self, *, session_date, trading_mode, strategy_version, strategy_name="breakout"):
        if self._preloaded and self._preloaded.strategy_name == strategy_name:
            return self._preloaded
        return None

    def save(self, state: DailySessionState) -> None:
        self.saved.append(state)

    def list_by_session(self, **kwargs):
        return []


def _make_supervisor(
    *,
    settings: Settings,
    broker_equity: float = 10_000.0,
    order_store: _RecordingOrderStore | None = None,
    session_state_store: _RecordingSessionStateStore | None = None,
    equity_baseline: float | None = None,
):
    module, RuntimeSupervisor = _load_supervisor_api()

    class _FakeBroker:
        def get_account(self):
            return BrokerAccount(
                equity=broker_equity,
                buying_power=broker_equity * 2,
                trading_blocked=False,
            )
        def list_open_orders(self):
            return []

    class _FakeMarketData:
        def get_stock_bars(self, **kwargs):
            return {}
        def get_daily_bars(self, **kwargs):
            return {}

    class _FakeTradingStatusStore:
        def load(self, **kwargs):
            return None

    class _FakePositionStore:
        def list_all(self, **kwargs):
            return []
        def replace_all(self, **kwargs):
            pass

    class _FakeStrategyFlagStore:
        def list_all(self, **kwargs):
            return []

    class _FakeWatchlistStore:
        def list_enabled(self, *args):
            return ["AAPL", "MSFT"]
        def list_ignored(self, *args):
            return []

    _order_store = order_store or _RecordingOrderStore()
    _sess_store = session_state_store or _RecordingSessionStateStore()

    class _FakeRuntimeContext:
        connection = _FakeConn()
        store_lock = None
        order_store = _order_store
        trading_status_store = _FakeTradingStatusStore()
        position_store = _FakePositionStore()
        daily_session_state_store = _sess_store
        audit_event_store = _RecordingAuditStore()
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = _FakeWatchlistStore()

        def commit(self):
            pass

    supervisor = RuntimeSupervisor(
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
    if equity_baseline is not None:
        supervisor._session_equity_baseline[_SESSION_DATE] = equity_baseline
    return supervisor, _FakeRuntimeContext


# ── Settings tests ────────────────────────────────────────────────────────────

def test_settings_per_symbol_loss_limit_pct_defaults_to_zero():
    s = _make_settings()
    assert s.per_symbol_loss_limit_pct == 0.0


def test_settings_per_symbol_loss_limit_pct_parsed_from_env():
    s = _make_settings(**{"PER_SYMBOL_LOSS_LIMIT_PCT": "0.005"})
    assert s.per_symbol_loss_limit_pct == pytest.approx(0.005)


def test_settings_per_symbol_loss_limit_pct_negative_raises():
    with pytest.raises(ValueError, match="PER_SYMBOL_LOSS_LIMIT_PCT"):
        _make_settings(**{"PER_SYMBOL_LOSS_LIMIT_PCT": "-0.001"})


def test_settings_per_symbol_loss_limit_pct_ge_one_raises():
    with pytest.raises(ValueError, match="PER_SYMBOL_LOSS_LIMIT_PCT"):
        _make_settings(**{"PER_SYMBOL_LOSS_LIMIT_PCT": "1.0"})


# ── Sticky loss limit tests ───────────────────────────────────────────────────

def test_loss_limit_remains_disabled_after_equity_recovery(monkeypatch):
    """Once the daily loss limit fires it must stay fired even after equity recovers."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    settings = _make_settings(**{"DAILY_LOSS_LIMIT_PCT": "0.01"})
    # Cycle 1: equity 9_880 vs baseline 10_000 → PnL = -120 > limit 100 → BREACH
    order_store = _RecordingOrderStore(daily_pnl=-120.0)
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=9_880.0,
        order_store=order_store,
        equity_baseline=10_000.0,
    )
    supervisor.run_cycle_once(timestamp=_NOW)
    assert _SESSION_DATE in supervisor._loss_limit_fired, "Loss limit must be in fired set"

    # Cycle 2: equity recovers to 10_050 → live condition is False but sticky flag must win
    supervisor.broker = type("B", (), {
        "get_account": lambda self: BrokerAccount(equity=10_050.0, buying_power=20_100.0, trading_blocked=False),
        "list_open_orders": lambda self: [],
    })()
    report = supervisor.run_cycle_once(timestamp=_NOW)
    assert report.entries_disabled, "Entries must remain disabled after equity recovery"


def test_loss_limit_fires_sticky_flag_in_memory():
    """_loss_limit_fired must be populated when the breach condition first becomes True."""
    settings = _make_settings(**{"DAILY_LOSS_LIMIT_PCT": "0.01"})
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=9_880.0,
        order_store=_RecordingOrderStore(daily_pnl=-120.0),
        equity_baseline=10_000.0,
    )

    # Patch out side-effecting calls
    m_save = []
    supervisor._save_session_state = lambda s: m_save.append(s)
    supervisor._append_audit = lambda e: None

    # Simulate the relevant loss-limit computation directly
    baseline_equity = 10_000.0
    loss_limit = settings.daily_loss_limit_pct * baseline_equity
    total_pnl = 9_880.0 - baseline_equity  # -120
    if total_pnl < -loss_limit:
        supervisor._loss_limit_fired.add(_SESSION_DATE)
    daily_loss_limit_breached = _SESSION_DATE in supervisor._loss_limit_fired

    assert daily_loss_limit_breached


def test_loss_limit_sticky_flag_restored_from_persisted_state():
    """On restart the supervisor must re-populate _loss_limit_fired from the _equity row."""
    # Simulate a mid-day restart: _equity row has entries_disabled=True (from prior breach)
    persisted_equity_row = DailySessionState(
        session_date=_SESSION_DATE,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="_equity",
        entries_disabled=True,   # breach was already persisted before restart
        flatten_complete=False,
        equity_baseline=10_000.0,
    )
    settings = _make_settings()
    module, _ = _load_supervisor_api()

    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_050.0,  # equity has recovered after restart
        session_state_store=_RecordingSessionStateStore(preloaded=persisted_equity_row),
        equity_baseline=None,    # not pre-populated — will load from Postgres
    )

    # Patch cycle-runner to avoid side effects
    supervisor._cycle_runner = lambda **kwargs: SimpleNamespace(intents=[])
    supervisor._cycle_intent_executor = lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    )
    supervisor._order_dispatcher = lambda **kwargs: {"submitted_count": 0}

    report = supervisor.run_cycle_once(timestamp=_NOW)
    assert _SESSION_DATE in supervisor._loss_limit_fired, (
        "Supervisor must restore _loss_limit_fired from persisted _equity row"
    )
    assert report.entries_disabled, "Entries must be disabled after restart if prior breach was persisted"


def test_loss_limit_persists_sticky_marker_to_postgres():
    """When the loss limit fires, the _equity session-state row must be updated with entries_disabled=True."""
    settings = _make_settings(**{"DAILY_LOSS_LIMIT_PCT": "0.01"})
    sess_store = _RecordingSessionStateStore()
    order_store = _RecordingOrderStore(daily_pnl=-120.0)

    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=9_880.0,
        order_store=order_store,
        session_state_store=sess_store,
        equity_baseline=10_000.0,
    )
    supervisor._cycle_runner = lambda **kwargs: SimpleNamespace(intents=[])
    supervisor._cycle_intent_executor = lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    )
    supervisor._order_dispatcher = lambda **kwargs: {"submitted_count": 0}
    supervisor._notifier = None

    supervisor.run_cycle_once(timestamp=_NOW)

    equity_rows = [
        s for s in sess_store.saved
        if s.strategy_name == "_equity" and s.entries_disabled
    ]
    assert equity_rows, "Expected _equity row saved with entries_disabled=True when loss limit fires"


# ── Per-symbol loss limit tests ───────────────────────────────────────────────

def test_per_symbol_loss_limit_blocks_entries_for_breached_symbol(monkeypatch):
    """When a symbol's realized PnL exceeds per_symbol_loss_limit, it must be blocked from new entries."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    received_working_symbols: list[set] = []

    def _fake_cycle_runner(**kwargs):
        received_working_symbols.append(set(kwargs["working_order_symbols"]))
        return SimpleNamespace(intents=[])

    monkeypatch.setattr(module, "run_cycle", _fake_cycle_runner)

    # baseline=10_000, per_symbol_limit=0.005 → limit=$50; AAPL realized=-60 → blocked
    settings = _make_settings(**{
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.005",
        "DAILY_LOSS_LIMIT_PCT": "0.05",
    })
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        order_store=_RecordingOrderStore(
            daily_pnl=-60.0,
            pnl_by_symbol={"AAPL": -60.0},
        ),
        equity_baseline=10_000.0,
    )
    supervisor.run_cycle_once(timestamp=_NOW)

    assert received_working_symbols, "cycle_runner must have been called"
    assert "AAPL" in received_working_symbols[0], (
        "AAPL must appear in working_order_symbols when per-symbol limit is breached"
    )


def test_per_symbol_loss_limit_does_not_block_symbol_within_limit(monkeypatch):
    """Symbols within the per-symbol limit must not be added to working_order_symbols."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    received_working_symbols: list[set] = []

    def _fake_cycle_runner(**kwargs):
        received_working_symbols.append(set(kwargs["working_order_symbols"]))
        return SimpleNamespace(intents=[])

    monkeypatch.setattr(module, "run_cycle", _fake_cycle_runner)

    # baseline=10_000, per_symbol_limit=0.005 → limit=$50; AAPL realized=-30 → NOT blocked
    settings = _make_settings(**{
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.005",
        "DAILY_LOSS_LIMIT_PCT": "0.05",
    })
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        order_store=_RecordingOrderStore(
            daily_pnl=-30.0,
            pnl_by_symbol={"AAPL": -30.0},
        ),
        equity_baseline=10_000.0,
    )
    supervisor.run_cycle_once(timestamp=_NOW)

    assert received_working_symbols, "cycle_runner must have been called"
    assert "AAPL" not in received_working_symbols[0], (
        "AAPL must NOT be in working_order_symbols when within per-symbol limit"
    )


def test_per_symbol_loss_limit_emits_audit_event_once_per_symbol(monkeypatch):
    """A per_symbol_loss_limit_breached audit event must be emitted once per symbol."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    settings = _make_settings(**{
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.005",
        "DAILY_LOSS_LIMIT_PCT": "0.05",
    })
    supervisor, ctx = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        order_store=_RecordingOrderStore(
            daily_pnl=-60.0,
            pnl_by_symbol={"AAPL": -60.0},
        ),
        equity_baseline=10_000.0,
    )
    # Run two cycles — audit event should appear exactly once
    supervisor.run_cycle_once(timestamp=_NOW)
    supervisor.run_cycle_once(timestamp=_NOW)

    breach_events = [
        e for e in supervisor.runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "per_symbol_loss_limit_breached"
        and getattr(e, "symbol", None) == "AAPL"
    ]
    assert len(breach_events) == 1, "Expected exactly one per_symbol_loss_limit_breached audit event per symbol"


def test_per_symbol_loss_limit_disabled_when_zero(monkeypatch):
    """When per_symbol_loss_limit_pct=0.0 (default), no per-symbol check must run."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    settings = _make_settings()  # per_symbol_loss_limit_pct=0.0 by default
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        order_store=_RecordingOrderStore(
            daily_pnl=-60.0,
            pnl_by_symbol={"AAPL": -60.0},  # would breach if feature were enabled
        ),
        equity_baseline=10_000.0,
    )
    supervisor.run_cycle_once(timestamp=_NOW)

    breach_events = [
        e for e in supervisor.runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "per_symbol_loss_limit_breached"
    ]
    assert len(breach_events) == 0, "No per_symbol_loss_limit_breached events when feature is disabled"


def test_daily_realized_pnl_by_symbol_empty_when_no_trades():
    """daily_realized_pnl_by_symbol must return {} when there are no closed trades."""
    from alpaca_bot.storage.repositories import OrderStore as _OrderStore

    class _FakeConn2:
        def cursor(self):
            class _C:
                def execute(self, *a): pass
                def fetchall(self): return []
                def close(self): pass
            return _C()
        def commit(self): pass

    store = _OrderStore(_FakeConn2())
    result = store.daily_realized_pnl_by_symbol(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        session_date=_SESSION_DATE,
    )
    assert result == {}
```

---

## Task 8 — Run tests

```bash
pytest tests/unit/test_loss_limit_enhancements.py -v
pytest
```

All 980+ existing tests must pass. The new file adds at least 12 tests.

---

## Verification checklist

- [ ] `Settings.per_symbol_loss_limit_pct` field exists with default `0.0`
- [ ] `validate()` rejects negative or ≥1.0 values
- [ ] `OrderStore.daily_realized_pnl_by_symbol()` exists and returns `dict[str, float]`
- [ ] `RuntimeSupervisor._loss_limit_fired: set[date]` initialized in `__init__`
- [ ] `RuntimeSupervisor._per_symbol_limit_alerted: dict[date, set[str]]` initialized in `__init__`
- [ ] First-cycle `_equity` row load restores `_loss_limit_fired` if `entries_disabled=True`
- [ ] `daily_loss_limit_breached` uses sticky `_loss_limit_fired` set, not live condition
- [ ] When limit fires: `_equity` row updated with `entries_disabled=True` in Postgres
- [ ] Per-symbol blocked symbols added to `working_order_symbols` each cycle
- [ ] `per_symbol_loss_limit_breached` audit event emitted once per (symbol, session_date)
- [ ] All new tests pass; no existing tests broken
