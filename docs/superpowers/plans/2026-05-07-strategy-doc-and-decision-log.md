# STRATEGY.md + Trade Decision Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write STRATEGY.md (living reference for all trading logic) and instrument `evaluate_cycle()` to emit `DecisionRecord` objects captured per cycle, persisted best-effort to a new `decision_log` Postgres table.

**Architecture:** `DecisionRecord` is a frozen dataclass in `domain/`; `CycleResult` gains a `decision_records: tuple[DecisionRecord, ...]` field; `evaluate_cycle()` accumulates records in a local list and returns them without I/O; `DecisionLogStore.bulk_insert()` writes them best-effort in `run_cycle()` after `connection.commit()`, inside the `with _store_lock` block.

**Tech Stack:** Python frozen dataclasses, psycopg2, Postgres JSONB, SQL migration.

---

## File Map

| File | Action |
|---|---|
| `STRATEGY.md` | Create — root-level strategy reference |
| `migrations/015_add_decision_log.sql` | Create — new table + indexes |
| `src/alpaca_bot/domain/decision_record.py` | Create — `DecisionRecord` frozen dataclass |
| `src/alpaca_bot/domain/__init__.py` | Modify — export `DecisionRecord` |
| `src/alpaca_bot/core/engine.py` | Modify — add `decision_records` to `CycleResult`, instrument `evaluate_cycle()` |
| `src/alpaca_bot/storage/repositories.py` | Modify — add `DecisionLogStore.bulk_insert()` |
| `src/alpaca_bot/storage/__init__.py` | Modify — export `DecisionLogStore` |
| `src/alpaca_bot/runtime/cycle.py` | Modify — best-effort write in `run_cycle()` |
| `src/alpaca_bot/runtime/bootstrap.py` | Modify — add `decision_log_store` to `RuntimeContext`, `bootstrap_runtime()`, `reconnect_runtime_connection()` |
| `tests/unit/test_decision_log.py` | Create — tests for migration, dataclass, store, engine instrumentation, cycle wiring |

---

## Task 1: Write STRATEGY.md

**Files:**
- Create: `STRATEGY.md`

- [ ] **Step 1: Read all strategy signal files**

```bash
cat src/alpaca_bot/strategy/breakout.py | head -100
cat src/alpaca_bot/strategy/momentum.py | head -80
cat src/alpaca_bot/strategy/bull_flag.py | head -80
cat src/alpaca_bot/strategy/ema_pullback.py | head -80
cat src/alpaca_bot/strategy/orb.py | head -80
cat src/alpaca_bot/strategy/gap_and_go.py | head -80
cat src/alpaca_bot/strategy/vwap_reversion.py | head -80
cat src/alpaca_bot/strategy/vwap_cross.py | head -80
cat src/alpaca_bot/strategy/high_watermark.py | head -80
cat src/alpaca_bot/strategy/bb_squeeze.py | head -80
cat src/alpaca_bot/strategy/failed_breakdown.py | head -80
cat src/alpaca_bot/config/__init__.py | head -250
```

- [ ] **Step 2: Write STRATEGY.md**

Write a comprehensive document covering:
1. All active strategy signal definitions (read each strategy file)
2. Universal pre-filter logic (trend, session time, volume, regime, news, spread, bar age, already-traded) — from `engine.py` lines 303–480
3. Stop placement logic (ATR-based initial, trailing activation at 1R, profit trail) — from engine.py lines 200–270 and config
4. Exit logic (trailing stop, flatten time, daily loss, extended hours) — engine.py lines 120–234
5. Risk/sizing math — `risk/sizing.py` / `calculate_position_size()`
6. Strategy weighting regime — `risk/weighting.py` Sharpe-proportional with [1%, 40%] clip
7. Extended hours behaviour — `strategy/session.py`, engine.py `is_extended` logic
8. Parameter cross-reference table: every `Settings` field, its env var, default, and purpose

- [ ] **Step 3: Commit**

```bash
git add STRATEGY.md
git commit -m "docs: add STRATEGY.md — canonical reference for all trading logic"
```

---

## Task 2: Create migration 015

**Files:**
- Create: `migrations/015_add_decision_log.sql`

- [ ] **Step 1: Write the SQL migration**

```sql
CREATE TABLE decision_log (
    id              BIGSERIAL PRIMARY KEY,
    cycle_at        TIMESTAMPTZ NOT NULL,
    symbol          TEXT        NOT NULL,
    strategy_name   TEXT        NOT NULL,
    trading_mode    TEXT        NOT NULL,
    strategy_version TEXT       NOT NULL,
    decision        TEXT        NOT NULL,
    reject_stage    TEXT,
    reject_reason   TEXT,
    entry_level     NUMERIC(12,4),
    signal_bar_close NUMERIC(12,4),
    relative_volume NUMERIC(8,4),
    atr             NUMERIC(12,4),
    stop_price      NUMERIC(12,4),
    limit_price     NUMERIC(12,4),
    initial_stop_price NUMERIC(12,4),
    quantity        NUMERIC(12,4),
    risk_per_share  NUMERIC(12,4),
    equity          NUMERIC(14,2),
    filter_results  JSONB
);

CREATE INDEX ON decision_log (cycle_at DESC);
CREATE INDEX ON decision_log (symbol, cycle_at DESC);
CREATE INDEX ON decision_log (strategy_name, decision);
```

Save to `migrations/015_add_decision_log.sql`.

- [ ] **Step 2: Commit**

```bash
git add migrations/015_add_decision_log.sql
git commit -m "feat: add migration 015 — decision_log table"
```

---

## Task 3: `DecisionRecord` dataclass + tests

**Files:**
- Create: `src/alpaca_bot/domain/decision_record.py`
- Modify: `src/alpaca_bot/domain/__init__.py`
- Create: `tests/unit/test_decision_log.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_decision_log.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain.decision_record import DecisionRecord
from alpaca_bot.storage.migrations import discover_migrations, resolve_migrations_path


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
        "ATR_PERIOD": "14",
    }
    values.update(overrides)
    return Settings.from_env(values)


_NOW = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)


def _make_record(**overrides: Any) -> DecisionRecord:
    defaults: dict[str, Any] = {
        "cycle_at": _NOW,
        "symbol": "AAPL",
        "strategy_name": "breakout",
        "trading_mode": "paper",
        "strategy_version": "v1",
        "decision": "rejected",
        "reject_stage": "pre_filter",
        "reject_reason": "regime_blocked",
        "entry_level": None,
        "signal_bar_close": None,
        "relative_volume": None,
        "atr": None,
        "stop_price": None,
        "limit_price": None,
        "initial_stop_price": None,
        "quantity": None,
        "risk_per_share": None,
        "equity": None,
        "filter_results": {},
    }
    defaults.update(overrides)
    return DecisionRecord(**defaults)


# ── Migration exists ─────────────────────────────────────────────────────────

def test_migration_015_exists_and_contains_decision_log() -> None:
    migrations_dir = resolve_migrations_path(None)
    migrations = discover_migrations(migrations_dir)
    m015 = next((m for m in migrations if m.version == 15), None)
    assert m015 is not None, "015_add_decision_log.sql not found in migrations/"
    assert "decision_log" in m015.sql.lower()
    assert "filter_results" in m015.sql.lower()


# ── DecisionRecord frozen dataclass ─────────────────────────────────────────

def test_decision_record_is_frozen() -> None:
    rec = _make_record()
    with pytest.raises((AttributeError, TypeError)):
        rec.decision = "accepted"  # type: ignore[misc]


def test_decision_record_accepted_fields() -> None:
    rec = _make_record(
        decision="accepted",
        reject_stage=None,
        reject_reason=None,
        entry_level=150.25,
        signal_bar_close=151.0,
        relative_volume=2.5,
        quantity=10.0,
        stop_price=148.0,
        limit_price=151.0,
        initial_stop_price=148.0,
        risk_per_share=3.0,
        equity=100_000.0,
        filter_results={"regime": True, "news": True, "spread": True},
    )
    assert rec.decision == "accepted"
    assert rec.reject_stage is None
    assert rec.entry_level == 150.25
    assert rec.filter_results["regime"] is True


def test_decision_record_exported_from_domain() -> None:
    from alpaca_bot.domain import DecisionRecord as DR
    assert DR is DecisionRecord
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_decision_log.py -v
```

Expected: FAIL — `cannot import name 'DecisionRecord' from 'alpaca_bot.domain.decision_record'` (module doesn't exist yet).

- [ ] **Step 3: Create `domain/decision_record.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DecisionRecord:
    cycle_at: datetime
    symbol: str
    strategy_name: str
    trading_mode: str
    strategy_version: str
    decision: str
    reject_stage: str | None
    reject_reason: str | None
    entry_level: float | None
    signal_bar_close: float | None
    relative_volume: float | None
    atr: float | None
    stop_price: float | None
    limit_price: float | None
    initial_stop_price: float | None
    quantity: float | None
    risk_per_share: float | None
    equity: float | None
    filter_results: dict
```

- [ ] **Step 4: Export from `domain/__init__.py`**

In `src/alpaca_bot/domain/__init__.py`, add import and `__all__` entry:

Old:
```python
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import (
    Bar,
    EntrySignal,
    NewsItem,
    OpenPosition,
    Quote,
    ReplayEvent,
    ReplayResult,
    ReplayScenario,
    WorkingEntryOrder,
)

__all__ = [
    "Bar",
    "EntrySignal",
    "IntentType",
    "NewsItem",
    "OpenPosition",
    "Quote",
    "ReplayEvent",
    "ReplayResult",
    "ReplayScenario",
    "WorkingEntryOrder",
]
```

New:
```python
from alpaca_bot.domain.decision_record import DecisionRecord
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import (
    Bar,
    EntrySignal,
    NewsItem,
    OpenPosition,
    Quote,
    ReplayEvent,
    ReplayResult,
    ReplayScenario,
    WorkingEntryOrder,
)

__all__ = [
    "Bar",
    "DecisionRecord",
    "EntrySignal",
    "IntentType",
    "NewsItem",
    "OpenPosition",
    "Quote",
    "ReplayEvent",
    "ReplayResult",
    "ReplayScenario",
    "WorkingEntryOrder",
]
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_decision_log.py -v
```

Expected: PASS for all 4 tests.

- [ ] **Step 6: Run full suite to check no regressions**

```bash
pytest --tb=short -q
```

Expected: All existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/domain/decision_record.py src/alpaca_bot/domain/__init__.py tests/unit/test_decision_log.py
git commit -m "feat: add DecisionRecord frozen dataclass to domain/"
```

---

## Task 4: Extend `CycleResult` and instrument `evaluate_cycle()`

**Files:**
- Modify: `src/alpaca_bot/core/engine.py`
- Modify: `tests/unit/test_decision_log.py`

This task instruments `evaluate_cycle()` at all rejection/acceptance points. The pure function boundary is preserved — `_decision_records` is a local list populated during evaluation and returned as `tuple(...)` in `CycleResult`. No I/O.

- [ ] **Step 1: Write failing tests (append to `test_decision_log.py`)**

Add these imports and tests to `tests/unit/test_decision_log.py`:

```python
from datetime import date, timedelta

from alpaca_bot.core.engine import CycleResult, evaluate_cycle
from alpaca_bot.domain import Bar, OpenPosition


def make_daily_bars(symbol: str = "AAPL", count: int = 22) -> list[Bar]:
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(days=i),
            open=89.0 + i,
            high=90.0 + i,
            low=88.0 + i,
            close=90.0 + i,
            volume=1_000_000,
        )
        for i in range(count)
    ]


def make_intraday_bar(symbol: str = "AAPL", *, high: float = 151.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 5, 7, 14, 15, tzinfo=timezone.utc),
        open=149.0,
        high=high,
        low=148.0,
        close=150.0,
        volume=500_000,
    )


# ── CycleResult has decision_records field ───────────────────────────────────

def test_cycle_result_has_decision_records_field() -> None:
    result = CycleResult(as_of=_NOW)
    assert result.decision_records == ()


# ── evaluate_cycle decision records ─────────────────────────────────────────

def test_evaluate_cycle_regime_blocked_emits_records_per_symbol() -> None:
    settings = make_settings(
        SYMBOLS="AAPL,MSFT",
        ENABLE_REGIME_FILTER="true",
        REGIME_SMA_PERIOD="5",
    )
    # Regime bars: window[-1].close <= SMA → blocked
    regime_bars = [
        Bar(
            symbol="SPY",
            timestamp=datetime(2026, 5, 7 - i, 20, 0, tzinfo=timezone.utc),
            open=400.0,
            high=401.0,
            low=399.0,
            close=395.0 - i,  # declining — close <= SMA
            volume=50_000_000,
        )
        for i in range(7)
    ]
    result = evaluate_cycle(
        settings=settings,
        now=datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc),
        equity=100_000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        regime_bars=regime_bars,
    )
    assert result.regime_blocked is True
    regime_records = [r for r in result.decision_records if r.reject_reason == "regime_blocked"]
    assert len(regime_records) == 2  # one per symbol
    symbols = {r.symbol for r in regime_records}
    assert symbols == {"AAPL", "MSFT"}
    for r in regime_records:
        assert r.decision == "rejected"
        assert r.reject_stage == "pre_filter"


def test_evaluate_cycle_accepted_entry_emits_accepted_record() -> None:
    """A symbol that produces a valid signal and fits capacity → accepted record."""
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    daily_bars = make_daily_bars("AAPL", count=22)
    intraday_bars = [make_intraday_bar("AAPL", high=155.0)] * 21 + [
        # Signal bar: close > entry_level (breakout)
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 5, 7, 14, 15, tzinfo=timezone.utc),
            open=149.0,
            high=156.0,
            low=148.0,
            close=155.0,
            volume=2_000_000,
        )
    ]

    def fake_signal(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        from alpaca_bot.domain import EntrySignal
        bar = intraday_bars[signal_index]
        return EntrySignal(
            symbol=symbol,
            signal_bar=bar,
            entry_level=150.0,
            relative_volume=2.5,
            stop_price=148.0,
            limit_price=151.0,
            initial_stop_price=148.0,
        )

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": daily_bars},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=fake_signal,
    )

    accepted = [r for r in result.decision_records if r.decision == "accepted"]
    assert len(accepted) == 1
    rec = accepted[0]
    assert rec.symbol == "AAPL"
    assert rec.entry_level == 150.0
    assert rec.relative_volume == 2.5
    assert rec.limit_price == 151.0
    assert rec.quantity is not None and rec.quantity > 0


def test_evaluate_cycle_no_signal_emits_skipped_no_signal() -> None:
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)

    def no_signal(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return None

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [make_intraday_bar("AAPL")]},
        daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=no_signal,
    )

    no_sig = [r for r in result.decision_records if r.decision == "skipped_no_signal"]
    assert len(no_sig) == 1
    assert no_sig[0].symbol == "AAPL"


def test_evaluate_cycle_already_traded_emits_skipped_already_traded() -> None:
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    bar = make_intraday_bar("AAPL")
    from alpaca_bot.strategy.breakout import session_day
    already_traded = {("AAPL", session_day(bar.timestamp, settings))}

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=already_traded,
        entries_disabled=False,
    )

    records = [r for r in result.decision_records if r.decision == "skipped_already_traded"]
    assert len(records) == 1
    assert records[0].symbol == "AAPL"


def test_evaluate_cycle_open_position_emits_skipped_existing_position() -> None:
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    pos = OpenPosition(
        symbol="AAPL",
        quantity=10,
        entry_price=150.0,
        stop_price=148.0,
        entry_timestamp=now,
        risk_per_share=2.0,
    )

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [make_intraday_bar("AAPL")]},
        daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
        open_positions=[pos],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    records = [r for r in result.decision_records if r.decision == "skipped_existing_position"]
    assert len(records) == 1
    assert records[0].symbol == "AAPL"


def test_evaluate_cycle_capacity_full_emits_capacity_rejected() -> None:
    """When available_slots == 0 (max positions reached), symbols get capacity-rejected."""
    settings = make_settings(SYMBOLS="AAPL,MSFT", MAX_OPEN_POSITIONS="1")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    pos = OpenPosition(
        symbol="GOOGL",
        quantity=5,
        entry_price=200.0,
        stop_price=195.0,
        entry_timestamp=now,
        risk_per_share=5.0,
    )

    def fake_signal(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        from alpaca_bot.domain import EntrySignal
        bar = intraday_bars[signal_index]
        return EntrySignal(
            symbol=symbol,
            signal_bar=bar,
            entry_level=bar.close - 1.0,
            relative_volume=2.0,
            stop_price=bar.close - 3.0,
            limit_price=bar.close,
            initial_stop_price=bar.close - 3.0,
        )

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={
            "AAPL": [make_intraday_bar("AAPL")],
            "MSFT": [make_intraday_bar("MSFT")],
        },
        daily_bars_by_symbol={
            "AAPL": make_daily_bars("AAPL"),
            "MSFT": make_daily_bars("MSFT"),
        },
        open_positions=[pos],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=fake_signal,
        global_open_count=1,
    )

    capacity_recs = [r for r in result.decision_records if r.reject_stage == "capacity"]
    assert len(capacity_recs) >= 1


def test_evaluate_cycle_flatten_all_returns_empty_decision_records() -> None:
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    pos = OpenPosition(
        symbol="AAPL",
        quantity=10,
        entry_price=150.0,
        stop_price=148.0,
        entry_timestamp=now,
        risk_per_share=2.0,
    )

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[pos],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        flatten_all=True,
    )

    assert result.decision_records == ()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_decision_log.py -v
```

Expected: FAIL — `CycleResult has no field 'decision_records'`

- [ ] **Step 3: Add `decision_records` to `CycleResult` in `engine.py`**

In `src/alpaca_bot/core/engine.py`, add import of `DecisionRecord` at the top (with other domain imports):

Old:
```python
from alpaca_bot.domain import Bar, NewsItem, OpenPosition, Quote
```

New:
```python
from alpaca_bot.domain import Bar, DecisionRecord, NewsItem, OpenPosition, Quote
```

Then add `decision_records` field to `CycleResult` (it's a frozen dataclass):

Old:
```python
@dataclass(frozen=True)
class CycleResult:
    as_of: datetime
    intents: list[CycleIntent] = field(default_factory=list)
    regime_blocked: bool = False
    news_blocked_symbols: tuple[str, ...] = ()
    spread_blocked_symbols: tuple[str, ...] = ()
```

New:
```python
@dataclass(frozen=True)
class CycleResult:
    as_of: datetime
    intents: list[CycleIntent] = field(default_factory=list)
    regime_blocked: bool = False
    news_blocked_symbols: tuple[str, ...] = ()
    spread_blocked_symbols: tuple[str, ...] = ()
    decision_records: tuple[DecisionRecord, ...] = ()
```

- [ ] **Step 4: Instrument `evaluate_cycle()` — initialize accumulator and helpers**

After `intents: list[CycleIntent] = []` (line 111 in current engine.py), add:

```python
    _decision_records: list[DecisionRecord] = []
    _tm = settings.trading_mode.value
    _sv = settings.strategy_version
```

- [ ] **Step 5: Emit regime-blocked records per symbol**

The regime filter block currently sets `_regime_entries_blocked = True` and continues. After the check, add a per-symbol loop:

Old (lines 306–312):
```python
    _regime_entries_blocked = False
    if settings.enable_regime_filter and regime_bars is not None:
        if len(regime_bars) >= settings.regime_sma_period + 1:
            window = regime_bars[-settings.regime_sma_period - 1 : -1]
            sma = sum(b.close for b in window) / len(window)
            if window[-1].close <= sma:
                _regime_entries_blocked = True
```

New:
```python
    _regime_entries_blocked = False
    if settings.enable_regime_filter and regime_bars is not None:
        if len(regime_bars) >= settings.regime_sma_period + 1:
            window = regime_bars[-settings.regime_sma_period - 1 : -1]
            sma = sum(b.close for b in window) / len(window)
            if window[-1].close <= sma:
                _regime_entries_blocked = True
                for _sym in (symbols or settings.symbols):
                    if _sym not in open_position_symbols and _sym not in working_order_symbols:
                        _decision_records.append(DecisionRecord(
                            cycle_at=now, symbol=_sym, strategy_name=strategy_name,
                            trading_mode=_tm, strategy_version=_sv,
                            decision="rejected", reject_stage="pre_filter",
                            reject_reason="regime_blocked",
                            entry_level=None, signal_bar_close=None, relative_volume=None,
                            atr=None, stop_price=None, limit_price=None,
                            initial_stop_price=None, quantity=None, risk_per_share=None,
                            equity=equity, filter_results={"regime": False},
                        ))
```

- [ ] **Step 6: Instrument per-symbol rejection points in the entry loop**

The entry candidates `for symbol in (symbols or settings.symbols):` loop (starting line 332) needs per-symbol records at each early-continue.

Replace the body of the loop with instrumented version:

Old (lines 332–478):
```python
            for symbol in (symbols or settings.symbols):
                if symbol in open_position_symbols or symbol in working_order_symbols:
                    continue
                bars = intraday_bars_by_symbol.get(symbol, ())
                daily_bars = daily_bars_by_symbol.get(symbol, ())
                if not bars or not daily_bars:
                    continue
                latest_bar = bars[-1]
                if (symbol, session_day(latest_bar.timestamp, settings)) in traded_symbols_today:
                    continue

                if not is_extended:
                    bar_age_seconds = (now - latest_bar.timestamp.astimezone(timezone.utc)).total_seconds()
                    if bar_age_seconds > 2 * settings.entry_timeframe_minutes * 60:
                        continue

                # News filter: skip entry if catalyst headline detected for this symbol.
                if settings.enable_news_filter and news_by_symbol is not None:
                    symbol_news = news_by_symbol.get(symbol, [])
                    if any(
                        any(kw in item.headline.lower() for kw in settings.news_filter_keywords)
                        for item in symbol_news
                    ):
                        _news_blocked.append(symbol)
                        continue

                # Spread filter: skip entry if NBBO spread exceeds threshold.
                if settings.enable_spread_filter and quotes_by_symbol is not None:
                    quote = quotes_by_symbol.get(symbol)
                    spread_threshold = (
                        settings.extended_hours_max_spread_pct
                        if is_extended
                        else settings.max_spread_pct
                    )
                    if quote is not None and quote.spread_pct > spread_threshold:
                        _spread_blocked.append(symbol)
                        continue
```

New (replace with):
```python
            _candidate_signals: dict[str, tuple] = {}
            for symbol in (symbols or settings.symbols):
                if symbol in open_position_symbols or symbol in working_order_symbols:
                    _decision_records.append(DecisionRecord(
                        cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                        trading_mode=_tm, strategy_version=_sv,
                        decision="skipped_existing_position", reject_stage=None,
                        reject_reason=None,
                        entry_level=None, signal_bar_close=None, relative_volume=None,
                        atr=None, stop_price=None, limit_price=None,
                        initial_stop_price=None, quantity=None, risk_per_share=None,
                        equity=equity, filter_results={"regime": True},
                    ))
                    continue
                bars = intraday_bars_by_symbol.get(symbol, ())
                daily_bars = daily_bars_by_symbol.get(symbol, ())
                if not bars or not daily_bars:
                    continue
                latest_bar = bars[-1]
                if (symbol, session_day(latest_bar.timestamp, settings)) in traded_symbols_today:
                    _decision_records.append(DecisionRecord(
                        cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                        trading_mode=_tm, strategy_version=_sv,
                        decision="skipped_already_traded", reject_stage=None,
                        reject_reason=None,
                        entry_level=None, signal_bar_close=None, relative_volume=None,
                        atr=None, stop_price=None, limit_price=None,
                        initial_stop_price=None, quantity=None, risk_per_share=None,
                        equity=equity, filter_results={"regime": True},
                    ))
                    continue

                if not is_extended:
                    bar_age_seconds = (now - latest_bar.timestamp.astimezone(timezone.utc)).total_seconds()
                    if bar_age_seconds > 2 * settings.entry_timeframe_minutes * 60:
                        _decision_records.append(DecisionRecord(
                            cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                            trading_mode=_tm, strategy_version=_sv,
                            decision="rejected", reject_stage="pre_filter",
                            reject_reason="bar_too_old",
                            entry_level=None, signal_bar_close=None, relative_volume=None,
                            atr=None, stop_price=None, limit_price=None,
                            initial_stop_price=None, quantity=None, risk_per_share=None,
                            equity=equity, filter_results={"regime": True},
                        ))
                        continue

                # News filter: skip entry if catalyst headline detected for this symbol.
                if settings.enable_news_filter and news_by_symbol is not None:
                    symbol_news = news_by_symbol.get(symbol, [])
                    if any(
                        any(kw in item.headline.lower() for kw in settings.news_filter_keywords)
                        for item in symbol_news
                    ):
                        _news_blocked.append(symbol)
                        _decision_records.append(DecisionRecord(
                            cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                            trading_mode=_tm, strategy_version=_sv,
                            decision="rejected", reject_stage="pre_filter",
                            reject_reason="news_blocked",
                            entry_level=None, signal_bar_close=None, relative_volume=None,
                            atr=None, stop_price=None, limit_price=None,
                            initial_stop_price=None, quantity=None, risk_per_share=None,
                            equity=equity, filter_results={"regime": True, "news": False},
                        ))
                        continue

                # Spread filter: skip entry if NBBO spread exceeds threshold.
                if settings.enable_spread_filter and quotes_by_symbol is not None:
                    quote = quotes_by_symbol.get(symbol)
                    spread_threshold = (
                        settings.extended_hours_max_spread_pct
                        if is_extended
                        else settings.max_spread_pct
                    )
                    if quote is not None and quote.spread_pct > spread_threshold:
                        _spread_blocked.append(symbol)
                        _decision_records.append(DecisionRecord(
                            cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                            trading_mode=_tm, strategy_version=_sv,
                            decision="rejected", reject_stage="pre_filter",
                            reject_reason="spread_too_wide",
                            entry_level=None, signal_bar_close=None, relative_volume=None,
                            atr=None, stop_price=None, limit_price=None,
                            initial_stop_price=None, quantity=None, risk_per_share=None,
                            equity=equity, filter_results={"regime": True, "news": True, "spread": False},
                        ))
                        continue
```

- [ ] **Step 7: Instrument signal evaluation and sizing rejections**

After the spread filter block, the code computes the signal. Instrument signal=None and sizing failures.

Find this block (lines 370–478 approximately) and add decision records:

Old (signal evaluation and option/equity sizing — lines ~384–478):
```python
                signal = signal_evaluator(
                    symbol=symbol,
                    intraday_bars=bars,
                    signal_index=signal_index,
                    daily_bars=daily_bars,
                    settings=settings,
                )
                if signal is None:
                    continue

                if signal.option_contract is not None:
                    # Option entry: defined risk = premium; no stop needed
                    quantity = calculate_option_position_size(
                        equity=equity,
                        ask=signal.option_contract.ask,
                        settings=settings,
                    )
                    if quantity < 1:
                        continue
                    contract = signal.option_contract
                    entry_candidates.append(
                        (
                            round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                            round(signal.relative_volume, 6),
                            CycleIntent(
                                ...
                                symbol=contract.occ_symbol,
                                ...
                            ),
                        )
                    )
                else:
                    # Equity entry: stop-based sizing
                    if signal.initial_stop_price >= signal.limit_price:
                        continue
                    if signal.limit_price - signal.initial_stop_price < 0.01:
                        continue
                    cap_stop = round(signal.limit_price * (1 - settings.max_stop_pct), 2)
                    effective_initial_stop = max(signal.initial_stop_price, cap_stop)
                    fractionable = signal.symbol in settings.fractionable_symbols
                    quantity = calculate_position_size(
                        equity=equity,
                        entry_price=signal.limit_price,
                        stop_price=effective_initial_stop,
                        settings=settings,
                        fractionable=fractionable,
                    )
                    if quantity <= 0.0:
                        continue
                    if (
                        settings.min_position_notional > 0
                        and quantity * signal.limit_price < settings.min_position_notional
                    ):
                        continue
                    entry_candidates.append(...)
```

New (same code with decision records added at each rejection):
```python
                signal = signal_evaluator(
                    symbol=symbol,
                    intraday_bars=bars,
                    signal_index=signal_index,
                    daily_bars=daily_bars,
                    settings=settings,
                )
                if signal is None:
                    _decision_records.append(DecisionRecord(
                        cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                        trading_mode=_tm, strategy_version=_sv,
                        decision="skipped_no_signal", reject_stage=None, reject_reason=None,
                        entry_level=None, signal_bar_close=None, relative_volume=None,
                        atr=None, stop_price=None, limit_price=None,
                        initial_stop_price=None, quantity=None, risk_per_share=None,
                        equity=equity, filter_results={"regime": True, "news": True, "spread": True},
                    ))
                    continue

                if signal.option_contract is not None:
                    # Option entry: defined risk = premium; no stop needed
                    quantity = calculate_option_position_size(
                        equity=equity,
                        ask=signal.option_contract.ask,
                        settings=settings,
                    )
                    if quantity < 1:
                        _decision_records.append(DecisionRecord(
                            cycle_at=now, symbol=signal.option_contract.occ_symbol,
                            strategy_name=strategy_name,
                            trading_mode=_tm, strategy_version=_sv,
                            decision="rejected", reject_stage="sizing", reject_reason="qty_zero",
                            entry_level=signal.entry_level,
                            signal_bar_close=signal.signal_bar.close,
                            relative_volume=signal.relative_volume,
                            atr=None, stop_price=None,
                            limit_price=signal.option_contract.ask,
                            initial_stop_price=None, quantity=0.0,
                            risk_per_share=None, equity=equity,
                            filter_results={"regime": True, "news": True, "spread": True},
                        ))
                        continue
                    contract = signal.option_contract
                    _candidate_signals[contract.occ_symbol] = (
                        signal.entry_level, signal.signal_bar.close,
                        signal.relative_volume, None,
                    )
                    entry_candidates.append(
                        (
                            round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                            round(signal.relative_volume, 6),
                            CycleIntent(
                                intent_type=CycleIntentType.ENTRY,
                                symbol=contract.occ_symbol,
                                timestamp=signal.signal_bar.timestamp,
                                quantity=quantity,
                                stop_price=None,
                                limit_price=contract.ask,
                                initial_stop_price=None,
                                client_order_id=_client_order_id(
                                    settings=settings,
                                    symbol=contract.occ_symbol,
                                    signal_timestamp=signal.signal_bar.timestamp,
                                    strategy_name=strategy_name,
                                    is_option=True,
                                ),
                                signal_timestamp=signal.signal_bar.timestamp,
                                strategy_name=strategy_name,
                                underlying_symbol=symbol,
                                is_option=True,
                                option_strike=contract.strike,
                                option_expiry=contract.expiry,
                                option_type_str=contract.option_type,
                            ),
                        )
                    )
                else:
                    # Equity entry: stop-based sizing
                    if signal.initial_stop_price >= signal.limit_price:
                        _decision_records.append(DecisionRecord(
                            cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                            trading_mode=_tm, strategy_version=_sv,
                            decision="rejected", reject_stage="invalid_signal",
                            reject_reason="stop_at_or_above_limit",
                            entry_level=signal.entry_level,
                            signal_bar_close=signal.signal_bar.close,
                            relative_volume=signal.relative_volume,
                            atr=None, stop_price=signal.stop_price,
                            limit_price=signal.limit_price,
                            initial_stop_price=signal.initial_stop_price,
                            quantity=None, risk_per_share=None, equity=equity,
                            filter_results={"regime": True, "news": True, "spread": True},
                        ))
                        continue
                    if signal.limit_price - signal.initial_stop_price < 0.01:
                        _decision_records.append(DecisionRecord(
                            cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                            trading_mode=_tm, strategy_version=_sv,
                            decision="rejected", reject_stage="invalid_signal",
                            reject_reason="risk_too_narrow",
                            entry_level=signal.entry_level,
                            signal_bar_close=signal.signal_bar.close,
                            relative_volume=signal.relative_volume,
                            atr=None, stop_price=signal.stop_price,
                            limit_price=signal.limit_price,
                            initial_stop_price=signal.initial_stop_price,
                            quantity=None, risk_per_share=None, equity=equity,
                            filter_results={"regime": True, "news": True, "spread": True},
                        ))
                        continue
                    cap_stop = round(signal.limit_price * (1 - settings.max_stop_pct), 2)
                    effective_initial_stop = max(signal.initial_stop_price, cap_stop)
                    fractionable = signal.symbol in settings.fractionable_symbols
                    quantity = calculate_position_size(
                        equity=equity,
                        entry_price=signal.limit_price,
                        stop_price=effective_initial_stop,
                        settings=settings,
                        fractionable=fractionable,
                    )
                    if quantity <= 0.0:
                        _decision_records.append(DecisionRecord(
                            cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                            trading_mode=_tm, strategy_version=_sv,
                            decision="rejected", reject_stage="sizing", reject_reason="qty_zero",
                            entry_level=signal.entry_level,
                            signal_bar_close=signal.signal_bar.close,
                            relative_volume=signal.relative_volume,
                            atr=None, stop_price=signal.stop_price,
                            limit_price=signal.limit_price,
                            initial_stop_price=effective_initial_stop,
                            quantity=0.0, risk_per_share=None, equity=equity,
                            filter_results={"regime": True, "news": True, "spread": True},
                        ))
                        continue
                    if (
                        settings.min_position_notional > 0
                        and quantity * signal.limit_price < settings.min_position_notional
                    ):
                        _decision_records.append(DecisionRecord(
                            cycle_at=now, symbol=symbol, strategy_name=strategy_name,
                            trading_mode=_tm, strategy_version=_sv,
                            decision="rejected", reject_stage="sizing",
                            reject_reason="below_min_notional",
                            entry_level=signal.entry_level,
                            signal_bar_close=signal.signal_bar.close,
                            relative_volume=signal.relative_volume,
                            atr=None, stop_price=signal.stop_price,
                            limit_price=signal.limit_price,
                            initial_stop_price=effective_initial_stop,
                            quantity=quantity,
                            risk_per_share=signal.limit_price - effective_initial_stop,
                            equity=equity,
                            filter_results={"regime": True, "news": True, "spread": True},
                        ))
                        continue
                    _candidate_signals[symbol] = (
                        signal.entry_level, signal.signal_bar.close,
                        signal.relative_volume, None,
                    )
                    entry_candidates.append(
                        (
                            round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                            round(signal.relative_volume, 6),
                            CycleIntent(
                                intent_type=CycleIntentType.ENTRY,
                                symbol=symbol,
                                timestamp=signal.signal_bar.timestamp,
                                quantity=quantity,
                                stop_price=signal.stop_price,
                                limit_price=signal.limit_price,
                                initial_stop_price=effective_initial_stop,
                                client_order_id=_client_order_id(
                                    settings=settings,
                                    symbol=symbol,
                                    signal_timestamp=signal.signal_bar.timestamp,
                                    strategy_name=strategy_name,
                                ),
                                signal_timestamp=signal.signal_bar.timestamp,
                                strategy_name=strategy_name,
                            ),
                        )
                    )
```

- [ ] **Step 8: Instrument post-selection loop**

Replace current post-selection logic (lines 480–496) with version that emits accepted/capacity-rejected records:

Old:
```python
            entry_candidates.sort(
                key=lambda item: (-item[0], -item[1], item[2].symbol),
            )
            selected: list[CycleIntent] = []
            for *_rank, candidate in entry_candidates:
                if len(selected) >= available_slots:
                    break
                candidate_exposure = (
                    (candidate.limit_price or 0.0) * (candidate.quantity or 0) / equity
                    if equity > 0
                    else 0.0
                )
                if current_exposure + candidate_exposure > settings.max_portfolio_exposure_pct:
                    continue
                selected.append(candidate)
                current_exposure += candidate_exposure
            intents.extend(selected)
```

New:
```python
            entry_candidates.sort(
                key=lambda item: (-item[0], -item[1], item[2].symbol),
            )
            selected: list[CycleIntent] = []
            for *_rank, candidate in entry_candidates:
                entry_level, signal_bar_close, relative_volume, atr = _candidate_signals.get(
                    candidate.symbol, (None, None, None, None)
                )
                if len(selected) >= available_slots:
                    _decision_records.append(DecisionRecord(
                        cycle_at=now, symbol=candidate.symbol,
                        strategy_name=strategy_name,
                        trading_mode=_tm, strategy_version=_sv,
                        decision="rejected", reject_stage="capacity",
                        reject_reason="slots_full",
                        entry_level=entry_level,
                        signal_bar_close=signal_bar_close,
                        relative_volume=relative_volume,
                        atr=atr,
                        stop_price=candidate.stop_price,
                        limit_price=candidate.limit_price,
                        initial_stop_price=candidate.initial_stop_price,
                        quantity=candidate.quantity,
                        risk_per_share=(
                            (candidate.limit_price or 0.0) - (candidate.initial_stop_price or 0.0)
                            if candidate.limit_price and candidate.initial_stop_price else None
                        ),
                        equity=equity,
                        filter_results={"regime": True, "news": True, "spread": True},
                    ))
                    continue
                candidate_exposure = (
                    (candidate.limit_price or 0.0) * (candidate.quantity or 0) / equity
                    if equity > 0
                    else 0.0
                )
                if current_exposure + candidate_exposure > settings.max_portfolio_exposure_pct:
                    _decision_records.append(DecisionRecord(
                        cycle_at=now, symbol=candidate.symbol,
                        strategy_name=strategy_name,
                        trading_mode=_tm, strategy_version=_sv,
                        decision="rejected", reject_stage="capacity",
                        reject_reason="exposure_exceeded",
                        entry_level=entry_level,
                        signal_bar_close=signal_bar_close,
                        relative_volume=relative_volume,
                        atr=atr,
                        stop_price=candidate.stop_price,
                        limit_price=candidate.limit_price,
                        initial_stop_price=candidate.initial_stop_price,
                        quantity=candidate.quantity,
                        risk_per_share=(
                            (candidate.limit_price or 0.0) - (candidate.initial_stop_price or 0.0)
                            if candidate.limit_price and candidate.initial_stop_price else None
                        ),
                        equity=equity,
                        filter_results={"regime": True, "news": True, "spread": True},
                    ))
                    continue
                selected.append(candidate)
                current_exposure += candidate_exposure
                _decision_records.append(DecisionRecord(
                    cycle_at=now, symbol=candidate.symbol,
                    strategy_name=strategy_name,
                    trading_mode=_tm, strategy_version=_sv,
                    decision="accepted", reject_stage=None, reject_reason=None,
                    entry_level=entry_level,
                    signal_bar_close=signal_bar_close,
                    relative_volume=relative_volume,
                    atr=atr,
                    stop_price=candidate.stop_price,
                    limit_price=candidate.limit_price,
                    initial_stop_price=candidate.initial_stop_price,
                    quantity=candidate.quantity,
                    risk_per_share=(
                        (candidate.limit_price or 0.0) - (candidate.initial_stop_price or 0.0)
                        if candidate.limit_price and candidate.initial_stop_price else None
                    ),
                    equity=equity,
                    filter_results={"regime": True, "news": True, "spread": True},
                ))
            intents.extend(selected)
```

- [ ] **Step 9: Update return statement to include decision_records**

Old:
```python
    intents.sort(key=lambda intent: (intent.timestamp, intent.symbol, intent.intent_type.value))
    return CycleResult(
        as_of=now,
        intents=intents,
        regime_blocked=_regime_entries_blocked,
        news_blocked_symbols=tuple(sorted(_news_blocked)),
        spread_blocked_symbols=tuple(sorted(_spread_blocked)),
    )
```

New:
```python
    intents.sort(key=lambda intent: (intent.timestamp, intent.symbol, intent.intent_type.value))
    return CycleResult(
        as_of=now,
        intents=intents,
        regime_blocked=_regime_entries_blocked,
        news_blocked_symbols=tuple(sorted(_news_blocked)),
        spread_blocked_symbols=tuple(sorted(_spread_blocked)),
        decision_records=tuple(_decision_records),
    )
```

- [ ] **Step 10: Run decision log tests**

```bash
pytest tests/unit/test_decision_log.py -v
```

Expected: All tests PASS.

- [ ] **Step 11: Run full suite**

```bash
pytest --tb=short -q
```

Expected: All tests pass. Pay attention to any engine tests that construct `CycleResult` directly — they should still work since `decision_records` has a default value of `()`.

- [ ] **Step 12: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_decision_log.py
git commit -m "feat: add decision_records to CycleResult, instrument evaluate_cycle() at all rejection points"
```

---

## Task 5: `DecisionLogStore.bulk_insert()` + storage exports

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py`
- Modify: `src/alpaca_bot/storage/__init__.py`
- Modify: `tests/unit/test_decision_log.py`

- [ ] **Step 1: Write failing tests (append to `test_decision_log.py`)**

```python
from alpaca_bot.storage.repositories import DecisionLogStore


class _TrackingConnection:
    def __init__(self) -> None:
        self.commit_count = 0
        self.execute_calls: list[tuple] = []

    def commit(self) -> None:
        self.commit_count += 1

    def cursor(self):
        conn = self

        class _Cursor:
            def executemany(self, sql: str, params) -> None:
                conn.execute_calls.append(("executemany", sql, list(params)))

        return _Cursor()


def test_decision_log_store_bulk_insert_calls_executemany() -> None:
    conn = _TrackingConnection()
    store = DecisionLogStore(conn)
    records = [
        _make_record(decision="accepted", reject_stage=None, reject_reason=None),
        _make_record(decision="rejected", reject_stage="pre_filter", reject_reason="regime_blocked"),
    ]
    store.bulk_insert(records, conn)
    assert len(conn.execute_calls) == 1
    _, sql, params = conn.execute_calls[0]
    assert "decision_log" in sql.lower()
    assert len(params) == 2


def test_decision_log_store_bulk_insert_empty_is_noop() -> None:
    conn = _TrackingConnection()
    store = DecisionLogStore(conn)
    store.bulk_insert([], conn)
    assert conn.execute_calls == []
    assert conn.commit_count == 0


def test_decision_log_store_exported_from_storage() -> None:
    from alpaca_bot.storage import DecisionLogStore as DLS
    assert DLS is DecisionLogStore
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_decision_log.py::test_decision_log_store_bulk_insert_calls_executemany tests/unit/test_decision_log.py::test_decision_log_store_bulk_insert_empty_is_noop tests/unit/test_decision_log.py::test_decision_log_store_exported_from_storage -v
```

Expected: FAIL — `cannot import name 'DecisionLogStore'`

- [ ] **Step 3: Add `DecisionLogStore` to `repositories.py`**

Add the following class at the end of `src/alpaca_bot/storage/repositories.py`:

```python
class DecisionLogStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def bulk_insert(self, records: "list", conn: ConnectionProtocol) -> None:
        from alpaca_bot.domain.decision_record import DecisionRecord
        if not records:
            return
        sql = """
            INSERT INTO decision_log (
                cycle_at, symbol, strategy_name, trading_mode, strategy_version,
                decision, reject_stage, reject_reason,
                entry_level, signal_bar_close, relative_volume, atr,
                stop_price, limit_price, initial_stop_price,
                quantity, risk_per_share, equity, filter_results
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s
            )
        """
        params = [
            (
                r.cycle_at, r.symbol, r.strategy_name, r.trading_mode, r.strategy_version,
                r.decision, r.reject_stage, r.reject_reason,
                r.entry_level, r.signal_bar_close, r.relative_volume, r.atr,
                r.stop_price, r.limit_price, r.initial_stop_price,
                r.quantity, r.risk_per_share, r.equity,
                json.dumps(r.filter_results),
            )
            for r in records
        ]
        with conn.cursor() as cur:
            cur.executemany(sql, params)
```

Note: `json` is already imported at the top of `repositories.py` (line 3).

Note: `bulk_insert` takes a `conn` parameter (the current connection) so the write goes to the same connection as the rest of the cycle — consistent with the best-effort pattern in `run_cycle()`.

- [ ] **Step 4: Export from `storage/__init__.py`**

Old:
```python
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    DailySessionStateStore,
    OptionOrderRepository,
    OrderStore,
    PositionStore,
    StrategyFlagStore,
    StrategyWeightStore,
    TradingStatusStore,
    WatchlistRecord,
    WatchlistStore,
)

__all__ = [
    ...
]
```

New (add `DecisionLogStore` alphabetically):
```python
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    DailySessionStateStore,
    DecisionLogStore,
    OptionOrderRepository,
    OrderStore,
    PositionStore,
    StrategyFlagStore,
    StrategyWeightStore,
    TradingStatusStore,
    WatchlistRecord,
    WatchlistStore,
)

__all__ = [
    "AuditEvent",
    "AuditEventStore",
    "DailySessionState",
    "DailySessionStateStore",
    "DecisionLogStore",
    "discover_migrations",
    "EQUITY_SESSION_STATE_STRATEGY_NAME",
    "GLOBAL_SESSION_STATE_STRATEGY_NAME",
    "Migration",
    "MigrationRunner",
    "OptionOrderRecord",
    "OptionOrderRepository",
    "OrderRecord",
    "OrderStore",
    "PositionRecord",
    "PositionStore",
    "PostgresAdvisoryLock",
    "resolve_migrations_path",
    "StrategyFlag",
    "StrategyFlagStore",
    "StrategyWeight",
    "StrategyWeightStore",
    "TradingStatus",
    "TradingStatusStore",
    "TradingStatusValue",
    "WatchlistRecord",
    "WatchlistStore",
    "advisory_lock_key",
]
```

- [ ] **Step 5: Fix the `cursor()` context manager usage**

`psycopg2` connection cursors can be used as context managers. But in the test's `_TrackingConnection`, `cursor()` returns an object without `__enter__`/`__exit__`. Check whether other stores use `with conn.cursor()` or `conn.cursor()` directly.

Looking at `storage/db.py`'s `execute()` function — it uses `conn.cursor()` directly without context manager. Use the same pattern to match the codebase:

Replace the `bulk_insert` implementation with:
```python
    def bulk_insert(self, records: "list", conn: ConnectionProtocol) -> None:
        if not records:
            return
        sql = """
            INSERT INTO decision_log (
                cycle_at, symbol, strategy_name, trading_mode, strategy_version,
                decision, reject_stage, reject_reason,
                entry_level, signal_bar_close, relative_volume, atr,
                stop_price, limit_price, initial_stop_price,
                quantity, risk_per_share, equity, filter_results
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s
            )
        """
        params = [
            (
                r.cycle_at, r.symbol, r.strategy_name, r.trading_mode, r.strategy_version,
                r.decision, r.reject_stage, r.reject_reason,
                r.entry_level, r.signal_bar_close, r.relative_volume, r.atr,
                r.stop_price, r.limit_price, r.initial_stop_price,
                r.quantity, r.risk_per_share, r.equity,
                json.dumps(r.filter_results),
            )
            for r in records
        ]
        cur = conn.cursor()
        cur.executemany(sql, params)
```

Update `_TrackingConnection` in the test accordingly (no context manager needed).

- [ ] **Step 6: Run storage tests**

```bash
pytest tests/unit/test_decision_log.py -v
```

Expected: All tests PASS.

- [ ] **Step 7: Run full suite**

```bash
pytest --tb=short -q
```

Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py src/alpaca_bot/storage/__init__.py tests/unit/test_decision_log.py
git commit -m "feat: add DecisionLogStore.bulk_insert() for best-effort decision log writes"
```

---

## Task 6: Wire `DecisionLogStore` into `run_cycle()` (best-effort)

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle.py`
- Modify: `tests/unit/test_decision_log.py`

The decision log write goes inside the `with _store_lock` block, AFTER `runtime.connection.commit()`, in a separate `try/except` that catches and logs failures without re-raising. A decision log failure never rollbacks committed trade orders.

- [ ] **Step 1: Write failing tests (append to `test_decision_log.py`)**

```python
import logging
from unittest.mock import MagicMock

from alpaca_bot.runtime.cycle import run_cycle
from alpaca_bot.core.engine import CycleResult


class _FakeOrderStore:
    def save(self, order, *, commit=True):
        pass


class _FakeAuditStore:
    def append(self, event, *, commit=True):
        pass


class _FakeConnection:
    def commit(self):
        pass

    def rollback(self):
        pass


def _make_runtime(*, decision_log_store=None):
    import threading
    from types import SimpleNamespace
    return SimpleNamespace(
        order_store=_FakeOrderStore(),
        audit_event_store=_FakeAuditStore(),
        connection=_FakeConnection(),
        store_lock=threading.Lock(),
        decision_log_store=decision_log_store,
    )


def _make_cycle_result(*, decision_records=()):
    return CycleResult(
        as_of=_NOW,
        intents=[],
        decision_records=decision_records,
    )


def test_run_cycle_calls_bulk_insert_when_store_present() -> None:
    inserted: list = []

    class _FakeDecisionLogStore:
        def bulk_insert(self, records, conn):
            inserted.extend(records)

    records = [_make_record()]
    fake_result = _make_cycle_result(decision_records=tuple(records))
    runtime = _make_runtime(decision_log_store=_FakeDecisionLogStore())

    run_cycle(
        settings=make_settings(),
        runtime=runtime,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        _evaluate_fn=lambda **_: fake_result,
    )

    assert inserted == records


def test_run_cycle_skips_bulk_insert_when_no_store() -> None:
    fake_result = _make_cycle_result(decision_records=(_make_record(),))
    runtime = _make_runtime(decision_log_store=None)

    # Should complete without error
    run_cycle(
        settings=make_settings(),
        runtime=runtime,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        _evaluate_fn=lambda **_: fake_result,
    )


def test_run_cycle_decision_log_failure_does_not_raise(caplog) -> None:
    class _FailingStore:
        def bulk_insert(self, records, conn):
            raise RuntimeError("DB write failed")

    fake_result = _make_cycle_result(decision_records=(_make_record(),))
    runtime = _make_runtime(decision_log_store=_FailingStore())

    with caplog.at_level(logging.WARNING, logger="alpaca_bot.runtime.cycle"):
        result = run_cycle(
            settings=make_settings(),
            runtime=runtime,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            _evaluate_fn=lambda **_: fake_result,
        )

    assert result is fake_result
    assert any("decision" in rec.message.lower() for rec in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_decision_log.py::test_run_cycle_calls_bulk_insert_when_store_present tests/unit/test_decision_log.py::test_run_cycle_skips_bulk_insert_when_no_store tests/unit/test_decision_log.py::test_run_cycle_decision_log_failure_does_not_raise -v
```

Expected: FAIL — `run_cycle` doesn't call `bulk_insert`.

- [ ] **Step 3: Add logger and best-effort write to `run_cycle()` in `cycle.py`**

Add logger at the top of `cycle.py` (after the imports):

Old (near top of file, after imports):
```python
from alpaca_bot.strategy import StrategySignalEvaluator
```

New (add logger after imports):
```python
from alpaca_bot.strategy import StrategySignalEvaluator

import logging
logger = logging.getLogger(__name__)
```

Then add the best-effort decision log write inside `run_cycle()`, inside the `with _store_lock` block, after `runtime.connection.commit()` (after line 153):

Old (lines 153–160):
```python
            runtime.connection.commit()
        except Exception:
            try:
                runtime.connection.rollback()
            except Exception:
                pass
            raise

    return result
```

New:
```python
            runtime.connection.commit()
        except Exception:
            try:
                runtime.connection.rollback()
            except Exception:
                pass
            raise

        decision_log_store = getattr(runtime, "decision_log_store", None)
        if decision_log_store is not None and result.decision_records:
            try:
                decision_log_store.bulk_insert(result.decision_records, runtime.connection)
                runtime.connection.commit()
            except Exception:
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
                logger.warning("decision_log bulk_insert failed — continuing without logging")

    return result
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_decision_log.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest --tb=short -q
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/cycle.py tests/unit/test_decision_log.py
git commit -m "feat: wire DecisionLogStore.bulk_insert() into run_cycle() as best-effort post-commit write"
```

---

## Task 7: Wire `DecisionLogStore` into `RuntimeContext` and `bootstrap.py`

**Files:**
- Modify: `src/alpaca_bot/runtime/bootstrap.py`
- Modify: `tests/unit/test_decision_log.py`

Three changes to `bootstrap.py`:
1. Add `decision_log_store: DecisionLogStore | None = None` field to `RuntimeContext`
2. Instantiate it in `bootstrap_runtime()`
3. Add `"decision_log_store"` to the rewire loop in `reconnect_runtime_connection()`

If `decision_log_store` is not rewired after a DB reconnect, the store keeps a dead connection and all writes silently fail.

- [ ] **Step 1: Write failing tests (append to `test_decision_log.py`)**

```python
from alpaca_bot.runtime.bootstrap import RuntimeContext


def test_runtime_context_has_decision_log_store_field() -> None:
    from dataclasses import fields
    field_names = {f.name for f in fields(RuntimeContext)}
    assert "decision_log_store" in field_names


def test_reconnect_rewires_decision_log_store() -> None:
    from alpaca_bot.runtime.bootstrap import reconnect_runtime_connection
    from alpaca_bot.storage.repositories import DecisionLogStore
    from types import SimpleNamespace

    class _FakeConn:
        pass

    new_conn = _FakeConn()

    class _FakeStore:
        _connection = object()

    class _FakeLock:
        _connection = object()
        def try_acquire(self):
            return True

    store = _FakeStore()
    ctx = SimpleNamespace(
        connection=object(),
        decision_log_store=store,
        trading_status_store=None,
        audit_event_store=None,
        order_store=None,
        daily_session_state_store=None,
        position_store=None,
        strategy_flag_store=None,
        watchlist_store=None,
        option_order_store=None,
        strategy_weight_store=None,
        settings=make_settings(),
        lock=_FakeLock(),
    )

    reconnect_runtime_connection(ctx, _new_conn=new_conn)

    assert store._connection is new_conn
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_decision_log.py::test_runtime_context_has_decision_log_store_field tests/unit/test_decision_log.py::test_reconnect_rewires_decision_log_store -v
```

Expected: FAIL — `RuntimeContext` has no `decision_log_store` field.

- [ ] **Step 3: Update `bootstrap.py`**

Add `DecisionLogStore` to the imports at the top of `bootstrap.py`:

Old:
```python
from alpaca_bot.storage import (
    AuditEventStore,
    DailySessionStateStore,
    MigrationRunner,
    OptionOrderRepository,
    OrderStore,
    PostgresAdvisoryLock,
    PositionStore,
    StrategyFlagStore,
    StrategyWeightStore,
    TradingStatusStore,
    WatchlistStore,
    resolve_migrations_path,
)
```

New:
```python
from alpaca_bot.storage import (
    AuditEventStore,
    DailySessionStateStore,
    DecisionLogStore,
    MigrationRunner,
    OptionOrderRepository,
    OrderStore,
    PostgresAdvisoryLock,
    PositionStore,
    StrategyFlagStore,
    StrategyWeightStore,
    TradingStatusStore,
    WatchlistStore,
    resolve_migrations_path,
)
```

Add `decision_log_store` field to `RuntimeContext` (after `strategy_weight_store`):

Old:
```python
    strategy_weight_store: StrategyWeightStore | None = None
    # Protects all store operations against concurrent access from the trade update stream thread
    store_lock: threading.Lock = field(default_factory=threading.Lock)
```

New:
```python
    strategy_weight_store: StrategyWeightStore | None = None
    decision_log_store: DecisionLogStore | None = None
    # Protects all store operations against concurrent access from the trade update stream thread
    store_lock: threading.Lock = field(default_factory=threading.Lock)
```

Add instantiation in `bootstrap_runtime()` return:

Old:
```python
    return RuntimeContext(
        settings=settings,
        connection=runtime_connection,
        lock=lock,
        trading_status_store=TradingStatusStore(runtime_connection),
        audit_event_store=AuditEventStore(runtime_connection),
        order_store=OrderStore(runtime_connection),
        position_store=PositionStore(runtime_connection),
        daily_session_state_store=DailySessionStateStore(runtime_connection),
        strategy_flag_store=StrategyFlagStore(runtime_connection),
        watchlist_store=watchlist_store,
        option_order_store=OptionOrderRepository(runtime_connection),
        strategy_weight_store=StrategyWeightStore(runtime_connection),
    )
```

New:
```python
    return RuntimeContext(
        settings=settings,
        connection=runtime_connection,
        lock=lock,
        trading_status_store=TradingStatusStore(runtime_connection),
        audit_event_store=AuditEventStore(runtime_connection),
        order_store=OrderStore(runtime_connection),
        position_store=PositionStore(runtime_connection),
        daily_session_state_store=DailySessionStateStore(runtime_connection),
        strategy_flag_store=StrategyFlagStore(runtime_connection),
        watchlist_store=watchlist_store,
        option_order_store=OptionOrderRepository(runtime_connection),
        strategy_weight_store=StrategyWeightStore(runtime_connection),
        decision_log_store=DecisionLogStore(runtime_connection),
    )
```

Add `"decision_log_store"` to the rewire loop in `reconnect_runtime_connection()`:

Old:
```python
    for attr in (
        "trading_status_store",
        "audit_event_store",
        "order_store",
        "daily_session_state_store",
        "position_store",
        "strategy_flag_store",
        "watchlist_store",
        "option_order_store",
        "strategy_weight_store",
    ):
```

New:
```python
    for attr in (
        "trading_status_store",
        "audit_event_store",
        "order_store",
        "daily_session_state_store",
        "position_store",
        "strategy_flag_store",
        "watchlist_store",
        "option_order_store",
        "strategy_weight_store",
        "decision_log_store",
    ):
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_decision_log.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest --tb=short -q
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/bootstrap.py tests/unit/test_decision_log.py
git commit -m "feat: wire DecisionLogStore into RuntimeContext — bootstrap, reconnect rewire"
```

---

## Task 8: Final verification

**Files:** None new; verification only.

- [ ] **Step 1: Run full test suite**

```bash
pytest --tb=short -q
```

Expected: All tests pass.

- [ ] **Step 2: Verify migration file exists and is syntactically valid SQL**

```bash
cat migrations/015_add_decision_log.sql
```

Expected: Shows CREATE TABLE + 3 CREATE INDEX statements.

- [ ] **Step 3: Verify `decision_records` field is immutable**

```bash
python -c "
from alpaca_bot.domain.decision_record import DecisionRecord
from datetime import datetime, timezone
r = DecisionRecord(
    cycle_at=datetime.now(timezone.utc), symbol='AAPL', strategy_name='breakout',
    trading_mode='paper', strategy_version='v1', decision='accepted',
    reject_stage=None, reject_reason=None, entry_level=None, signal_bar_close=None,
    relative_volume=None, atr=None, stop_price=None, limit_price=None,
    initial_stop_price=None, quantity=None, risk_per_share=None, equity=None,
    filter_results={},
)
try:
    r.decision = 'rejected'
    print('FAIL: should be frozen')
except (AttributeError, TypeError) as e:
    print('PASS: frozen —', e)
"
```

Expected: `PASS: frozen — ...`

- [ ] **Step 4: Verify the `evaluate_cycle()` pure function boundary — no I/O in engine**

```bash
grep -n "import\|open\|write\|cursor\|execute\|connect" src/alpaca_bot/core/engine.py | grep -v "^.*#" | grep -v "from alpaca_bot"
```

Expected: No references to I/O operations (cursor, execute, connect, open, write).

- [ ] **Step 5: Verify `decision_log_store` is in the reconnect rewire list**

```bash
grep "decision_log_store" src/alpaca_bot/runtime/bootstrap.py
```

Expected: Appears in `RuntimeContext` field definition, `bootstrap_runtime()` return, and `reconnect_runtime_connection()` loop.

- [ ] **Step 6: Final commit if any cleanup was needed**

```bash
git status
```

If clean, no commit needed. Otherwise add/commit.

---

## Grilling notes (resolved during plan refinement)

- **`Migration.version` is `int`**: `discover_migrations()` parses prefix as `int(prefix)`. Test uses `m.version == 15` (not `"015"`).
- **No `migration_db` fixture**: All storage tests use fake connections. Migration existence test uses `discover_migrations()` directly.
- **`_evaluate_fn=` DI seam**: `run_cycle()` already has `_evaluate_fn=None`. Tests use this parameter instead of monkey-patching.
- **`EntrySignal` has no `atr` field**: `DecisionRecord.atr` is always `None` in first pass.
- **Regime records placement**: The regime-blocked per-symbol loop goes inside the `if _regime_entries_blocked:` branch, using `open_position_symbols` (defined at line 112) to skip symbols that already have positions.
- **`_decision_records` initialization**: Placed after `intents: list[CycleIntent] = []` (line 111), so it's always initialized. The `flatten_all` early return happens before, so `flatten_all` paths return `decision_records=()` naturally (empty since the accumulator was never populated before the `flatten_all` branch).

Wait — rechecking: `flatten_all` early return is at lines 92–109. `_decision_records` must be initialized BEFORE line 92 if we want it available everywhere. But since `flatten_all` returns directly, we don't need `_decision_records` in that path — it just returns `CycleResult(as_of=now, intents=intents)` without `decision_records`, which means `decision_records` defaults to `()`. **Correction**: place `_decision_records = []` and `_tm`, `_sv` after `intents: list[CycleIntent] = []` at line 111 (AFTER the `flatten_all` early return block). The `flatten_all` path returns `CycleResult` without `decision_records=`, so it gets the default `()`.

- **`cursor().executemany()` vs `with conn.cursor()`**: Use `conn.cursor()` directly (no context manager) to match the existing `storage/db.py` `execute()` helper pattern.
- **Best-effort write placement**: Inside `with _store_lock`, AFTER the inner `try/except` block's commit but OUTSIDE the inner try. A decision log failure warns and continues — never rollbacks committed orders.
- **`decision_log_store` reconnect**: Added to `reconnect_runtime_connection()` attribute loop — critical for production uptime after a DB reconnect event.
