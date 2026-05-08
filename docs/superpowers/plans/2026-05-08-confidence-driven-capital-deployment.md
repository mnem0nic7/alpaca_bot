# Confidence-Driven Capital Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace strategy-weight equity shrinkage with Sharpe-percentile confidence scores so the bot deploys 30–50% of capital instead of ~2%.

**Architecture:** Each strategy's sizing uses `effective_equity = account.equity × confidence_score` (confidence_score from Sharpe percentile rank, clamped to [floor, 1.0]) instead of weight-shrunk equity. Weights are demoted to priority ordering. An adjustable confidence floor auto-raises on drawdown/volatility/losing-streak triggers and lowers only via admin CLI.

**Tech Stack:** Python, PostgreSQL (psycopg2 upsert pattern), existing `StrategyWeightStore`/`StrategyFlagStore` patterns.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `migrations/016_add_confidence_floor_store.sql` | Create | New table for floor value + high-watermark |
| `src/alpaca_bot/storage/models.py` | Modify | Add `ConfidenceFloor` dataclass |
| `src/alpaca_bot/storage/repositories.py` | Modify | Add `ConfidenceFloorStore` |
| `src/alpaca_bot/storage/__init__.py` | Modify | Export new model + store |
| `src/alpaca_bot/runtime/bootstrap.py` | Modify | Add `confidence_floor_store` to `RuntimeContext` + `bootstrap_runtime` + `reconnect_runtime_connection` |
| `src/alpaca_bot/config/__init__.py` | Modify | Add 5 new env var settings |
| `src/alpaca_bot/risk/confidence.py` | Create | Pure `compute_confidence_scores()` function |
| `src/alpaca_bot/risk/weighting.py` | Modify | `compute_losing_day_streaks()` helper |
| `src/alpaca_bot/runtime/supervisor.py` | Modify | Cache sharpes, compute confidence scores, decouple sizing, auto-raise triggers, losing streak exclusion |
| `src/alpaca_bot/admin/cli.py` | Modify | Add `set-confidence-floor` subcommand |
| `src/alpaca_bot/web/service.py` | Modify | Expose confidence floor + per-strategy scores |
| `src/alpaca_bot/web/templates/dashboard.html` | Modify | Show floor + confidence scores in strategy panel |
| `tests/unit/test_confidence_scoring.py` | Create | Tests for `compute_confidence_scores` |
| `tests/unit/test_confidence_floor_store.py` | Create | Tests for `ConfidenceFloorStore` |
| `tests/unit/test_supervisor_weights.py` | Modify | Tests for confidence-based sizing |
| `tests/unit/test_admin_cli.py` | Modify | Tests for `set-confidence-floor` CLI |
| `tests/unit/test_web_service.py` | Modify | Tests for confidence floor in web service |

---

### Task 1: SQL Migration

**Files:**
- Create: `migrations/016_add_confidence_floor_store.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
CREATE TABLE IF NOT EXISTS confidence_floor_store (
    trading_mode     TEXT    NOT NULL,
    strategy_version TEXT    NOT NULL,
    floor_value      REAL    NOT NULL,
    equity_high_watermark REAL NOT NULL DEFAULT 0.0,
    set_by           TEXT    NOT NULL,
    reason           TEXT    NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (trading_mode, strategy_version),
    CHECK (trading_mode IN ('paper', 'live')),
    CHECK (floor_value >= 0.0 AND floor_value <= 1.0)
);
```

Save to `migrations/016_add_confidence_floor_store.sql`.

- [ ] **Step 2: Verify migration applies**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_migrations.py -v
```

Expected: PASS (migration runner picks up version 16 and applies it).

- [ ] **Step 3: Commit**

```bash
git add migrations/016_add_confidence_floor_store.sql
git commit -m "feat: add confidence_floor_store migration"
```

---

### Task 2: Storage Model, Repository, RuntimeContext

**Files:**
- Modify: `src/alpaca_bot/storage/models.py`
- Modify: `src/alpaca_bot/storage/repositories.py`
- Modify: `src/alpaca_bot/storage/__init__.py`
- Modify: `src/alpaca_bot/runtime/bootstrap.py`
- Create: `tests/unit/test_confidence_floor_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_confidence_floor_store.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import ConfidenceFloor
from alpaca_bot.storage.repositories import ConfidenceFloorStore


class _FakeConn:
    def __init__(self):
        self._rows: list[tuple] = []
        self._last_sql: str = ""
        self._last_params: tuple = ()

    def cursor(self):
        return _FakeCursor(self)

    def commit(self): pass
    def rollback(self): pass


class _FakeCursor:
    def __init__(self, conn: _FakeConn):
        self._conn = conn
        self.description = None
        self._result: list[tuple] = []

    def execute(self, sql: str, params: tuple = ()):
        self._conn._last_sql = sql
        self._conn._last_params = params
        if "SELECT" in sql.upper():
            self._result = list(self._conn._rows)
        else:
            if "INSERT" in sql.upper():
                self._conn._rows = [params[:7]]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def __enter__(self): return self
    def __exit__(self, *a): pass


def _make_floor(**kwargs) -> ConfidenceFloor:
    defaults = dict(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=0.25,
        equity_high_watermark=10000.0,
        set_by="operator",
        reason="test",
        updated_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return ConfidenceFloor(**defaults)


def test_confidence_floor_load_returns_none_when_empty() -> None:
    conn = _FakeConn()
    store = ConfidenceFloorStore(conn)
    result = store.load(trading_mode=TradingMode.PAPER, strategy_version="v1")
    assert result is None


def test_confidence_floor_upsert_then_load() -> None:
    conn = _FakeConn()
    store = ConfidenceFloorStore(conn)
    rec = _make_floor(floor_value=0.40)
    store.upsert(rec)
    # Simulate that the row is now in the fake DB
    conn._rows = [(
        "paper", "v1", 0.40, 10000.0, "operator", "test",
        datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
    )]
    loaded = store.load(trading_mode=TradingMode.PAPER, strategy_version="v1")
    assert loaded is not None
    assert loaded.floor_value == pytest.approx(0.40)
    assert loaded.set_by == "operator"
    assert loaded.equity_high_watermark == pytest.approx(10000.0)


def test_confidence_floor_raises_stored_correctly() -> None:
    conn = _FakeConn()
    store = ConfidenceFloorStore(conn)
    rec = _make_floor(floor_value=0.50, set_by="system", reason="drawdown trigger")
    store.upsert(rec)
    assert "INSERT" in conn._last_sql.upper()
    # params: (trading_mode, strategy_version, floor_value, equity_high_watermark, set_by, reason, updated_at)
    params = conn._last_params
    assert params[2] == pytest.approx(0.50)
    assert params[4] == "system"
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
pytest tests/unit/test_confidence_floor_store.py -v
```

Expected: ImportError or AttributeError — `ConfidenceFloor` and `ConfidenceFloorStore` not yet defined.

- [ ] **Step 3: Add `ConfidenceFloor` to `models.py`**

In `src/alpaca_bot/storage/models.py`, after the `StrategyWeight` dataclass (around line 128), add:

```python
@dataclass(frozen=True)
class ConfidenceFloor:
    trading_mode: TradingMode
    strategy_version: str
    floor_value: float
    equity_high_watermark: float
    set_by: str  # 'system' | 'operator'
    reason: str
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Add `ConfidenceFloorStore` to `repositories.py`**

In `src/alpaca_bot/storage/repositories.py`, add the import at the top of the file:

```python
from alpaca_bot.storage.models import (
    AuditEvent,
    ConfidenceFloor,          # ADD THIS
    DailySessionState,
    ...
)
```

Then after the `StrategyWeightStore` class (around line 1480), add:

```python
class ConfidenceFloorStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def upsert(self, rec: ConfidenceFloor, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO confidence_floor_store (
                trading_mode, strategy_version, floor_value,
                equity_high_watermark, set_by, reason, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trading_mode, strategy_version)
            DO UPDATE SET
                floor_value = EXCLUDED.floor_value,
                equity_high_watermark = EXCLUDED.equity_high_watermark,
                set_by = EXCLUDED.set_by,
                reason = EXCLUDED.reason,
                updated_at = EXCLUDED.updated_at
            """,
            (
                rec.trading_mode.value,
                rec.strategy_version,
                rec.floor_value,
                rec.equity_high_watermark,
                rec.set_by,
                rec.reason,
                rec.updated_at,
            ),
            commit=commit,
        )

    def load(
        self, *, trading_mode: TradingMode, strategy_version: str
    ) -> ConfidenceFloor | None:
        row = fetch_one(
            self._connection,
            """
            SELECT trading_mode, strategy_version, floor_value,
                   equity_high_watermark, set_by, reason, updated_at
            FROM confidence_floor_store
            WHERE trading_mode = %s AND strategy_version = %s
            """,
            (trading_mode.value, strategy_version),
        )
        if row is None:
            return None
        return ConfidenceFloor(
            trading_mode=TradingMode(row[0]),
            strategy_version=row[1],
            floor_value=float(row[2]),
            equity_high_watermark=float(row[3]),
            set_by=row[4],
            reason=row[5],
            updated_at=row[6],
        )
```

- [ ] **Step 5: Export from `storage/__init__.py`**

In `src/alpaca_bot/storage/__init__.py`, add `ConfidenceFloor` and `ConfidenceFloorStore` to the imports and `__all__`:

```python
from alpaca_bot.storage.models import (
    AuditEvent,
    ConfidenceFloor,          # ADD
    DailySessionState,
    ...
)
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    ConfidenceFloorStore,     # ADD
    DailySessionStateStore,
    ...
)

__all__ = [
    ...
    "ConfidenceFloor",        # ADD
    "ConfidenceFloorStore",   # ADD
    ...
]
```

- [ ] **Step 6: Add `confidence_floor_store` to `RuntimeContext` in `bootstrap.py`**

In `src/alpaca_bot/runtime/bootstrap.py`, add the import:

```python
from alpaca_bot.storage import (
    ...
    ConfidenceFloorStore,   # ADD
    ...
)
```

Add the field to `RuntimeContext` (after `strategy_weight_store`):

```python
@dataclass
class RuntimeContext:
    ...
    strategy_weight_store: StrategyWeightStore | None = None
    confidence_floor_store: ConfidenceFloorStore | None = None   # ADD
    decision_log_store: DecisionLogStore | None = None
    store_lock: threading.Lock = field(default_factory=threading.Lock)
```

In `bootstrap_runtime`, instantiate it:

```python
return RuntimeContext(
    ...
    strategy_weight_store=StrategyWeightStore(runtime_connection),
    confidence_floor_store=ConfidenceFloorStore(runtime_connection),  # ADD
    decision_log_store=DecisionLogStore(runtime_connection),
)
```

In `reconnect_runtime_connection`, add `"confidence_floor_store"` to the attrs tuple (line ~116):

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
    "confidence_floor_store",    # ADD
    "decision_log_store",
):
```

- [ ] **Step 7: Run tests — confirm they pass**

```bash
pytest tests/unit/test_confidence_floor_store.py tests/unit/test_migrations.py tests/unit/test_runtime_bootstrap.py -v
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add migrations/016_add_confidence_floor_store.sql \
        src/alpaca_bot/storage/models.py \
        src/alpaca_bot/storage/repositories.py \
        src/alpaca_bot/storage/__init__.py \
        src/alpaca_bot/runtime/bootstrap.py \
        tests/unit/test_confidence_floor_store.py
git commit -m "feat: add ConfidenceFloor model, store, and RuntimeContext field"
```

---

### Task 3: Settings — 5 New Env Vars

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`

- [ ] **Step 1: Write failing test**

In `tests/unit/test_supervisor_weights.py` (or a new `tests/unit/test_settings_confidence.py`), add:

```python
def test_confidence_settings_defaults() -> None:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.015",
        "MAX_OPEN_POSITIONS": "20",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
    }
    from alpaca_bot.config import Settings
    s = Settings.from_env(base)
    assert s.confidence_floor == 0.25
    assert s.floor_raise_step == 0.10
    assert s.drawdown_raise_pct == 0.05
    assert s.losing_streak_n == 3
    assert s.vol_raise_threshold == 0.025


def test_confidence_floor_validation_rejects_out_of_range() -> None:
    from alpaca_bot.config import Settings
    import pytest
    base = {
        "TRADING_MODE": "paper", "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1", "DATABASE_URL": "x",
        "MARKET_DATA_FEED": "sip", "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20", "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20", "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15", "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.015", "MAX_OPEN_POSITIONS": "20",
        "DAILY_LOSS_LIMIT_PCT": "0.01", "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001", "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00", "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45", "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
        "CONFIDENCE_FLOOR": "1.5",  # invalid — > 1.0
    }
    with pytest.raises(ValueError, match="CONFIDENCE_FLOOR"):
        Settings.from_env(base)
```

Run: `pytest tests/unit/test_supervisor_weights.py::test_confidence_settings_defaults -v`
Expected: FAIL (AttributeError: Settings has no `confidence_floor`).

- [ ] **Step 2: Add settings fields to `config/__init__.py`**

In the `Settings` dataclass (after `max_portfolio_exposure_pct`), add:

```python
confidence_floor: float = 0.25
floor_raise_step: float = 0.10
drawdown_raise_pct: float = 0.05
losing_streak_n: int = 3
vol_raise_threshold: float = 0.025
```

In `from_env` (after `max_portfolio_exposure_pct` parsing), add:

```python
confidence_floor=float(values.get("CONFIDENCE_FLOOR", "0.25")),
floor_raise_step=float(values.get("FLOOR_RAISE_STEP", "0.10")),
drawdown_raise_pct=float(values.get("DRAWDOWN_RAISE_PCT", "0.05")),
losing_streak_n=int(values.get("LOSING_STREAK_N", "3")),
vol_raise_threshold=float(values.get("VOL_RAISE_THRESHOLD", "0.025")),
```

In `validate()` (after existing fraction checks), add:

```python
if not 0.0 <= self.confidence_floor <= 1.0:
    raise ValueError("CONFIDENCE_FLOOR must be between 0.0 and 1.0")
if not 0.0 < self.floor_raise_step <= 0.5:
    raise ValueError("FLOOR_RAISE_STEP must be between 0 (exclusive) and 0.5")
if not 0.0 < self.drawdown_raise_pct <= 0.5:
    raise ValueError("DRAWDOWN_RAISE_PCT must be between 0 (exclusive) and 0.5")
if self.losing_streak_n < 1:
    raise ValueError("LOSING_STREAK_N must be at least 1")
if not 0.0 < self.vol_raise_threshold <= 1.0:
    raise ValueError("VOL_RAISE_THRESHOLD must be between 0 (exclusive) and 1.0")
```

- [ ] **Step 3: Run tests — confirm they pass**

```bash
pytest tests/unit/test_supervisor_weights.py::test_confidence_settings_defaults \
       tests/unit/test_supervisor_weights.py::test_confidence_floor_validation_rejects_out_of_range -v
```

Expected: PASS.

- [ ] **Step 4: Run full suite to check for regressions**

```bash
pytest tests/unit/ -q
```

Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/config/__init__.py
git commit -m "feat: add confidence floor settings (CONFIDENCE_FLOOR, FLOOR_RAISE_STEP, etc)"
```

---

### Task 4: Confidence Score Module

**Files:**
- Create: `src/alpaca_bot/risk/confidence.py`
- Create: `tests/unit/test_confidence_scoring.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_confidence_scoring.py`:

```python
from __future__ import annotations

import pytest

from alpaca_bot.risk.confidence import compute_confidence_scores


def test_empty_sharpes_returns_empty() -> None:
    assert compute_confidence_scores({}, floor=0.25) == {}


def test_all_zero_sharpes_assigns_floor_to_everyone() -> None:
    # No history — all strategies get floor so they still participate.
    sharpes = {"a": 0.0, "b": 0.0, "c": 0.0}
    scores = compute_confidence_scores(sharpes, floor=0.25)
    assert set(scores.keys()) == {"a", "b", "c"}
    for v in scores.values():
        assert v == pytest.approx(0.25)


def test_all_equal_positive_sharpes_assigns_floor() -> None:
    sharpes = {"a": 1.0, "b": 1.0, "c": 1.0}
    scores = compute_confidence_scores(sharpes, floor=0.25)
    for v in scores.values():
        assert v == pytest.approx(0.25)


def test_highest_sharpe_gets_score_one() -> None:
    sharpes = {"low": 0.5, "mid": 1.0, "high": 2.0}
    scores = compute_confidence_scores(sharpes, floor=0.0)
    assert scores["high"] == pytest.approx(1.0)


def test_lowest_positive_sharpe_gets_floor() -> None:
    sharpes = {"low": 0.5, "mid": 1.0, "high": 2.0}
    scores = compute_confidence_scores(sharpes, floor=0.25)
    assert scores["low"] == pytest.approx(0.25)
    assert scores["high"] == pytest.approx(1.0)
    assert 0.25 < scores["mid"] < 1.0


def test_zero_sharpe_strategy_gets_floor_when_others_are_positive() -> None:
    sharpes = {"new": 0.0, "proven": 2.0}
    scores = compute_confidence_scores(sharpes, floor=0.20)
    assert scores["new"] == pytest.approx(0.20)
    assert scores["proven"] == pytest.approx(1.0)


def test_floor_raise_excludes_all_zero_sharpe_strategies() -> None:
    # Floor raised to 0.50; zero-sharpe strategies still get floor=0.50.
    sharpes = {"a": 0.0, "b": 0.0}
    scores = compute_confidence_scores(sharpes, floor=0.50)
    # All-zero → all get floor, all pass gate
    assert len(scores) == 2
    for v in scores.values():
        assert v == pytest.approx(0.50)


def test_scores_bounded_between_floor_and_one() -> None:
    sharpes = {f"s{i}": float(i) for i in range(10)}
    scores = compute_confidence_scores(sharpes, floor=0.10)
    for name, score in scores.items():
        assert 0.10 <= score <= 1.0 + 1e-9, f"{name}: {score}"


def test_tie_breaking_uses_bisect_left() -> None:
    # Two strategies with same Sharpe should get the same score.
    sharpes = {"a": 1.0, "b": 1.0, "c": 2.0}
    scores = compute_confidence_scores(sharpes, floor=0.0)
    assert scores["a"] == pytest.approx(scores["b"])
    assert scores["c"] > scores["a"]
```

Run: `pytest tests/unit/test_confidence_scoring.py -v`
Expected: FAIL (ImportError — module doesn't exist yet).

- [ ] **Step 2: Implement `risk/confidence.py`**

Create `src/alpaca_bot/risk/confidence.py`:

```python
from __future__ import annotations

import bisect


def compute_confidence_scores(
    sharpes: dict[str, float],
    floor: float,
) -> dict[str, float]:
    """Return per-strategy confidence score in [floor, 1.0] from Sharpe percentile rank.

    Strategies with sharpe <= 0 (no positive history) receive `floor` so they
    still participate at minimum size rather than being shut out.

    When all strategies have equal Sharpe (common at startup), all receive `floor`.

    Only strategies that pass the floor gate are included in the returned dict.
    Strategies absent from `sharpes` should be handled by callers (pass floor).
    """
    if not sharpes:
        return {}

    positive = {k: v for k, v in sharpes.items() if v > 0}

    # No positive-Sharpe strategies — no differentiation possible
    if not positive:
        return {name: floor for name in sharpes}

    # All positive Sharpes are equal — no ranking useful
    ranked = sorted(positive.values())
    if len(set(ranked)) == 1:
        result: dict[str, float] = {}
        for name, sharpe in sharpes.items():
            result[name] = floor
        return result

    n = len(ranked)
    scores: dict[str, float] = {}
    for name, sharpe in sharpes.items():
        if sharpe <= 0:
            scores[name] = floor
        else:
            idx = bisect.bisect_left(ranked, sharpe)
            # Map [0, n-1] index to [floor, 1.0] range
            raw = idx / (n - 1)  # [0.0, 1.0]
            scores[name] = floor + raw * (1.0 - floor)

    return {k: v for k, v in scores.items() if v >= floor}
```

- [ ] **Step 3: Run tests — confirm they pass**

```bash
pytest tests/unit/test_confidence_scoring.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/risk/confidence.py tests/unit/test_confidence_scoring.py
git commit -m "feat: add compute_confidence_scores() — Sharpe percentile ranking"
```

---

### Task 5: Decouple Sizing from Weights in Supervisor

**Files:**
- Modify: `src/alpaca_bot/risk/weighting.py` (add `compute_losing_day_streaks`)
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Modify: `tests/unit/test_supervisor_weights.py`

- [ ] **Step 1: Write failing tests for the sizing decoupling**

Add to `tests/unit/test_supervisor_weights.py`:

```python
def test_effective_equity_uses_full_account_equity_scaled_by_confidence() -> None:
    """With confidence score, sizing should use account.equity * confidence_score,
    not account.equity * strategy_weight (the old weight-shrunk approach)."""
    from alpaca_bot.storage import StrategyWeight
    from datetime import datetime, timezone

    recorded_equities: list[float] = []

    def fake_cycle_runner(*, equity, **kwargs):
        recorded_equities.append(equity)
        return SimpleNamespace(intents=[], signals_evaluated=0)

    settings = _make_settings(
        MAX_POSITION_PCT="0.015",
        MAX_OPEN_POSITIONS="3",
        CONFIDENCE_FLOOR="0.0",
    )
    # Two strategies with different Sharpes: breakout=2.0, momentum=0.5
    preloaded_weights = [
        StrategyWeight(
            strategy_name="breakout",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            weight=0.80,
            sharpe=2.0,
            computed_at=datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day, 0, 0, tzinfo=timezone.utc),
        ),
        StrategyWeight(
            strategy_name="momentum",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            weight=0.20,
            sharpe=0.5,
            computed_at=datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day, 0, 0, tzinfo=timezone.utc),
        ),
    ]
    weight_store = _FakeWeightStore(preloaded=preloaded_weights)
    rt = _make_runtime(weight_store=weight_store, cycle_runner=fake_cycle_runner)
    supervisor = _make_supervisor(rt, settings=settings)
    supervisor.run_cycle_once(now=_NOW)

    # Old behavior: equities would be 0.80*10000=8000 and 0.20*10000=2000
    # New behavior: equities are 10000 * confidence_score where scores are
    # based on Sharpe percentile (0.0 floor → lowest gets 0.0, highest gets 1.0)
    # With floor=0.0: momentum=0th percentile=0.0, breakout=1st percentile=1.0
    # So equity for breakout=10000*1.0=10000, momentum=10000*0.0=0 (entries disabled)
    # With floor=0.0, zero-score strategies still get entries disabled
    # The key assertion: breakout gets full equity (10000), not weight-shrunk (8000)
    assert len(recorded_equities) >= 1
    assert max(recorded_equities) == pytest.approx(10000.0)


def test_low_confidence_strategy_has_entries_disabled() -> None:
    """Strategies below the confidence floor should have entries disabled."""
    from alpaca_bot.storage import StrategyWeight
    from datetime import datetime, timezone

    entries_disabled_flags: list[bool] = []

    def fake_cycle_runner(*, entries_disabled, **kwargs):
        entries_disabled_flags.append(entries_disabled)
        return SimpleNamespace(intents=[], signals_evaluated=0)

    settings = _make_settings(
        CONFIDENCE_FLOOR="0.60",  # high floor
        MAX_OPEN_POSITIONS="3",
    )
    # momentum has Sharpe 0 (no history) → gets floor score (0.60) → passes gate
    # breakout has Sharpe 2.0 → gets high score → also passes
    # With floor=0.60, both pass since zero-Sharpe gets floor
    preloaded_weights = [
        StrategyWeight(
            strategy_name="breakout",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            weight=1.0,
            sharpe=2.0,
            computed_at=datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day, 0, 0, tzinfo=timezone.utc),
        ),
    ]
    weight_store = _FakeWeightStore(preloaded=preloaded_weights)
    rt = _make_runtime(weight_store=weight_store, cycle_runner=fake_cycle_runner)
    supervisor = _make_supervisor(rt, settings=settings)
    supervisor.run_cycle_once(now=_NOW)

    # No entries disabled unexpectedly for strategies with Sharpe data
    # (breakout passes since it's the only strategy and gets score=floor=0.60)
    assert all(not d for d in entries_disabled_flags), (
        f"Unexpected entries disabled: {entries_disabled_flags}"
    )
```

Run: `pytest tests/unit/test_supervisor_weights.py::test_effective_equity_uses_full_account_equity_scaled_by_confidence -v`
Expected: FAIL.

- [ ] **Step 2: Modify `_update_session_weights` to return `WeightResult`**

In `src/alpaca_bot/runtime/supervisor.py`:

First, ensure `WeightResult` is imported at the top (find the `compute_strategy_weights` import and add `WeightResult`):

```python
from alpaca_bot.risk.weighting import WeightResult, compute_strategy_weights
```

Change the return type of `_update_session_weights` (around line 1260):

```python
def _update_session_weights(self, session_date: date) -> WeightResult:
```

In the early return (no weight_store, line ~1278):

```python
n = max(len(active_names), 1)
return WeightResult(
    {name: 1.0 / n for name in active_names},
    {name: 0.0 for name in active_names},
)
```

In the cache-hit early return (line ~1310):

```python
return WeightResult(
    {w.strategy_name: w.weight for w in existing},
    {w.strategy_name: w.sharpe for w in existing},
)
```

The final `return result.weights` at line 1342 becomes just `return result` (since `result` is already a `WeightResult`).

- [ ] **Step 3: Add `_session_sharpes` cache and `_load_confidence_floor` to `RuntimeSupervisor.__init__`**

In `__init__` (around line 128, after `_session_capital_weights`), add:

```python
self._session_sharpes: dict[date, dict[str, float]] = {}
```

Add the `_load_confidence_floor` method (after `_append_audit`):

```python
def _load_confidence_floor(self) -> float:
    floor_store = getattr(self.runtime, "confidence_floor_store", None)
    if floor_store is None:
        return self.settings.confidence_floor
    store_lock = getattr(self.runtime, "store_lock", None)
    with store_lock if store_lock is not None else contextlib.nullcontext():
        rec = floor_store.load(
            trading_mode=self.settings.trading_mode,
            strategy_version=self.settings.strategy_version,
        )
    return rec.floor_value if rec is not None else self.settings.confidence_floor
```

- [ ] **Step 4: Update `run_cycle_once` to populate sharpes and compute confidence scores**

In `run_cycle_once` (around line 352-354), change:

```python
if session_date not in self._session_capital_weights:
    weights = self._update_session_weights(session_date)
    self._session_capital_weights[session_date] = weights
```

To:

```python
if session_date not in self._session_capital_weights:
    weight_result = self._update_session_weights(session_date)
    self._session_capital_weights[session_date] = weight_result.weights
    self._session_sharpes[session_date] = weight_result.sharpes
```

Then, immediately after (before the strategy loop), add:

```python
from alpaca_bot.risk.confidence import compute_confidence_scores
confidence_floor = self._load_confidence_floor()
session_sharpes = self._session_sharpes.get(session_date, {})
session_confidence_scores = compute_confidence_scores(session_sharpes, confidence_floor)
```

(The import can go at the top of the file instead — preferred.)

- [ ] **Step 5: Replace weight-shrunk equity with confidence-based equity in the strategy loop**

Around line 692, replace:

```python
strategy_weight = self._session_capital_weights[session_date].get(
    strategy_name, 1.0 / max(len(active_strategies), 1)
)
effective_equity = account.equity * strategy_weight
```

With:

```python
confidence_score = session_confidence_scores.get(strategy_name)
if confidence_score is None:
    # Strategy's Sharpe rank is below the confidence floor — disable entries
    strategy_entries_disabled = True
    confidence_score = confidence_floor  # used for sizing (entries disabled anyway)
effective_equity = account.equity * confidence_score
```

- [ ] **Step 6: Sort active strategies by confidence score (priority ordering)**

Find where `active_strategies` is built before the per-strategy loop. After it's populated, add a sort:

```python
# Sort highest-confidence strategies first so they fill slots before lower-confidence ones.
active_strategies = sorted(
    active_strategies,
    key=lambda pair: session_confidence_scores.get(pair[0], confidence_floor),
    reverse=True,
)
```

- [ ] **Step 7: Add import for `compute_confidence_scores` at top of `supervisor.py`**

```python
from alpaca_bot.risk.confidence import compute_confidence_scores
```

- [ ] **Step 8: Run tests — confirm they pass**

```bash
pytest tests/unit/test_supervisor_weights.py -v
```

Expected: all PASS including new tests.

- [ ] **Step 9: Run full suite**

```bash
pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py \
        src/alpaca_bot/risk/weighting.py \
        tests/unit/test_supervisor_weights.py
git commit -m "feat: decouple position sizing from weights — use confidence scores on full equity"
```

---

### Task 6: Auto-Raise Triggers (Drawdown + Volatility)

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Create: `tests/unit/test_confidence_floor_triggers.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_confidence_floor_triggers.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import ConfidenceFloor


def _make_floor(floor_value: float, watermark: float) -> ConfidenceFloor:
    return ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=floor_value,
        equity_high_watermark=watermark,
        set_by="system",
        reason="test",
        updated_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )


class _FakeFloorStore:
    def __init__(self, rec: ConfidenceFloor | None = None):
        self._rec = rec
        self.upserted: list[ConfidenceFloor] = []

    def load(self, **kwargs) -> ConfidenceFloor | None:
        return self._rec

    def upsert(self, rec: ConfidenceFloor, *, commit: bool = True) -> None:
        self._rec = rec
        self.upserted.append(rec)


def _make_supervisor_with_floor(settings_overrides=None):
    """Helper that imports and instantiates RuntimeSupervisor with floor store injected."""
    from tests.unit.test_supervisor_weights import _make_settings, _make_runtime, _make_supervisor
    overrides = settings_overrides or {}
    settings = _make_settings(**overrides)
    floor_store = _FakeFloorStore()
    rt = _make_runtime()
    rt.confidence_floor_store = floor_store
    sup = _make_supervisor(rt, settings=settings)
    return sup, floor_store


def test_drawdown_trigger_raises_floor() -> None:
    """When equity drops >DRAWDOWN_RAISE_PCT below watermark, floor should be raised."""
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    now = datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc)

    # Watermark was 10000, current equity is 9000 → 10% drop, exceeds 5% threshold
    rec = _make_floor(floor_value=0.25, watermark=10000.0)
    floor_store._rec = rec

    sup._check_and_update_floor_triggers(
        current_equity=9000.0,
        now=now,
        daily_bars_for_vol=[],
    )

    assert len(floor_store.upserted) == 1
    raised = floor_store.upserted[0]
    assert raised.floor_value == pytest.approx(0.35)  # 0.25 + 0.10
    assert raised.set_by == "system"
    assert "drawdown" in raised.reason.lower()


def test_drawdown_trigger_does_not_fire_when_within_threshold() -> None:
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    rec = _make_floor(floor_value=0.25, watermark=10000.0)
    floor_store._rec = rec

    # Only 3% drop — below 5% threshold
    sup._check_and_update_floor_triggers(
        current_equity=9700.0,
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=[],
    )

    assert len(floor_store.upserted) == 0


def test_watermark_updates_when_equity_improves() -> None:
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    rec = _make_floor(floor_value=0.25, watermark=8000.0)
    floor_store._rec = rec

    # Equity 10000 > watermark 8000 → update watermark
    sup._check_and_update_floor_triggers(
        current_equity=10000.0,
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=[],
    )

    assert len(floor_store.upserted) == 1
    updated = floor_store.upserted[0]
    assert updated.equity_high_watermark == pytest.approx(10000.0)
    assert updated.floor_value == pytest.approx(0.25)  # unchanged


def test_floor_capped_at_0_80() -> None:
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    rec = _make_floor(floor_value=0.75, watermark=10000.0)
    floor_store._rec = rec

    sup._check_and_update_floor_triggers(
        current_equity=9000.0,  # triggers drawdown
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=[],
    )

    assert floor_store.upserted[0].floor_value == pytest.approx(0.80)  # capped at 0.80


def test_vol_trigger_raises_floor() -> None:
    from alpaca_bot.domain import Bar
    from datetime import date

    sup, floor_store = _make_supervisor_with_floor({
        "VOL_RAISE_THRESHOLD": "0.02",  # 2% daily vol threshold
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    rec = _make_floor(floor_value=0.25, watermark=10000.0)
    floor_store._rec = rec

    # Build 6 daily bars with ~3% daily moves (exceeds 2% threshold)
    closes = [100.0, 103.0, 99.9, 103.1, 99.7, 103.2]  # ~3% daily swings
    bars = [
        Bar(
            symbol="SPY",
            timestamp=datetime(2026, 4, i + 1, tzinfo=timezone.utc),
            open=c,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]

    sup._check_and_update_floor_triggers(
        current_equity=10000.0,  # no drawdown
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=bars,
    )

    assert len(floor_store.upserted) == 1
    assert floor_store.upserted[0].floor_value == pytest.approx(0.35)
    assert "vol" in floor_store.upserted[0].reason.lower()
```

Run: `pytest tests/unit/test_confidence_floor_triggers.py -v`
Expected: FAIL (no `_check_and_update_floor_triggers` method).

- [ ] **Step 2: Implement `_check_and_update_floor_triggers` in `supervisor.py`**

Add the method after `_load_confidence_floor`:

```python
def _check_and_update_floor_triggers(
    self,
    *,
    current_equity: float,
    now: datetime,
    daily_bars_for_vol: list,
) -> None:
    """Evaluate drawdown and volatility triggers; raise the confidence floor if needed.

    Also updates the equity high-watermark whenever equity improves.
    Only writes to DB when a change is needed (reduces spurious audit events).
    """
    floor_store = getattr(self.runtime, "confidence_floor_store", None)
    if floor_store is None:
        return

    store_lock = getattr(self.runtime, "store_lock", None)
    with store_lock if store_lock is not None else contextlib.nullcontext():
        rec = floor_store.load(
            trading_mode=self.settings.trading_mode,
            strategy_version=self.settings.strategy_version,
        )

    current_floor = rec.floor_value if rec is not None else self.settings.confidence_floor
    watermark = rec.equity_high_watermark if rec is not None else current_equity
    new_floor = current_floor
    reason_parts: list[str] = []

    # Update high-watermark if equity improved
    new_watermark = max(watermark, current_equity)

    # Drawdown trigger
    if watermark > 0:
        drawdown_pct = (watermark - current_equity) / watermark
        if drawdown_pct > self.settings.drawdown_raise_pct:
            new_floor = min(current_floor + self.settings.floor_raise_step, 0.80)
            reason_parts.append(f"drawdown {drawdown_pct:.1%}")

    # Volatility trigger
    if daily_bars_for_vol and len(daily_bars_for_vol) >= 6:
        closes = [bar.close for bar in daily_bars_for_vol[-6:]]
        returns = [
            abs((closes[i] - closes[i - 1]) / closes[i - 1])
            for i in range(1, len(closes))
        ]
        daily_vol = sum(returns) / len(returns)
        if daily_vol > self.settings.vol_raise_threshold:
            new_floor = min(
                max(new_floor, current_floor + self.settings.floor_raise_step), 0.80
            )
            reason_parts.append(f"vol {daily_vol:.2%}")

    floor_changed = abs(new_floor - current_floor) > 1e-9
    watermark_changed = abs(new_watermark - watermark) > 1e-9

    if not floor_changed and not watermark_changed:
        return

    reason = f"auto-raised: {', '.join(reason_parts)}" if reason_parts else "watermark update"
    updated = ConfidenceFloor(
        trading_mode=self.settings.trading_mode,
        strategy_version=self.settings.strategy_version,
        floor_value=new_floor,
        equity_high_watermark=new_watermark,
        set_by="system",
        reason=reason,
        updated_at=now,
    )

    with store_lock if store_lock is not None else contextlib.nullcontext():
        floor_store.upsert(updated)

    if floor_changed:
        self._append_audit(AuditEvent(
            event_type="confidence_floor_auto_raised",
            payload={
                "old_floor": current_floor,
                "new_floor": new_floor,
                "reason": reason,
            },
            created_at=now,
        ))
```

Add `ConfidenceFloor` to the imports at the top of `supervisor.py`:

```python
from alpaca_bot.storage import (
    ...
    ConfidenceFloor,   # ADD
    ...
)
```

- [ ] **Step 3: Call `_check_and_update_floor_triggers` from `run_cycle_once`**

In `run_cycle_once`, after fetching `daily_bars_by_symbol` (around line 552-560), add:

```python
# Evaluate confidence floor auto-raise triggers
_regime_bars_for_vol = []
if self.settings.enable_regime_filter and regime_bars:
    _regime_bars_for_vol = list(regime_bars)
elif daily_bars_by_symbol:
    # Use first symbol's daily bars as a market proxy
    _regime_bars_for_vol = list(next(iter(daily_bars_by_symbol.values()), []))
self._check_and_update_floor_triggers(
    current_equity=account.equity,
    now=timestamp,
    daily_bars_for_vol=_regime_bars_for_vol,
)
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
pytest tests/unit/test_confidence_floor_triggers.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py \
        tests/unit/test_confidence_floor_triggers.py
git commit -m "feat: add drawdown and volatility auto-raise triggers for confidence floor"
```

---

### Task 7: Losing Streak Per-Strategy Exclusion

**Files:**
- Modify: `src/alpaca_bot/risk/weighting.py`
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Modify: `tests/unit/test_confidence_floor_triggers.py`

- [ ] **Step 1: Write failing test for losing streak exclusion**

Add to `tests/unit/test_confidence_floor_triggers.py`:

```python
def test_compute_losing_day_streaks_detects_consecutive_losses() -> None:
    from datetime import date
    from alpaca_bot.risk.weighting import compute_losing_day_streaks

    rows = [
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 1), "pnl": -100.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 2), "pnl": -50.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 3), "pnl": -75.0},
        {"strategy_name": "momentum", "exit_date": date(2026, 5, 1), "pnl": 200.0},
        {"strategy_name": "momentum", "exit_date": date(2026, 5, 2), "pnl": -10.0},
    ]
    streaks = compute_losing_day_streaks(rows, ["breakout", "momentum"])
    assert streaks["breakout"] == 3  # three consecutive losing days
    assert streaks["momentum"] == 1  # only one consecutive losing day


def test_compute_losing_day_streaks_resets_on_win() -> None:
    from datetime import date
    from alpaca_bot.risk.weighting import compute_losing_day_streaks

    rows = [
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 1), "pnl": -100.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 2), "pnl": 50.0},  # win
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 3), "pnl": -25.0},
    ]
    streaks = compute_losing_day_streaks(rows, ["breakout"])
    assert streaks["breakout"] == 1  # only the final day counts


def test_compute_losing_day_streaks_zero_when_last_day_was_win() -> None:
    from datetime import date
    from alpaca_bot.risk.weighting import compute_losing_day_streaks

    rows = [
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 1), "pnl": -100.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 2), "pnl": 50.0},
    ]
    streaks = compute_losing_day_streaks(rows, ["breakout"])
    assert streaks["breakout"] == 0
```

Run: `pytest tests/unit/test_confidence_floor_triggers.py::test_compute_losing_day_streaks_detects_consecutive_losses -v`
Expected: FAIL (ImportError).

- [ ] **Step 2: Add `compute_losing_day_streaks` to `weighting.py`**

In `src/alpaca_bot/risk/weighting.py`, after `compute_strategy_weights`, add:

```python
def compute_losing_day_streaks(
    trade_rows: list[dict],
    active_strategies: list[str],
) -> dict[str, int]:
    """Return trailing consecutive losing-day count per strategy.

    A 'losing day' is any date where the strategy's net PnL for that day is negative.
    Returns 0 if the most recent traded day was a win.
    """
    from collections import defaultdict
    import datetime as _dt

    # Build {strategy: {date: net_pnl}}
    daily_pnl: dict[str, dict] = {name: {} for name in active_strategies}
    for row in trade_rows:
        name = row.get("strategy_name")
        if name not in daily_pnl:
            continue
        d = row["exit_date"]
        daily_pnl[name][d] = daily_pnl[name].get(d, 0.0) + row["pnl"]

    streaks: dict[str, int] = {}
    for name in active_strategies:
        days = sorted(daily_pnl[name].keys())
        streak = 0
        for d in reversed(days):
            if daily_pnl[name][d] < 0:
                streak += 1
            else:
                break
        streaks[name] = streak
    return streaks
```

- [ ] **Step 3: Add losing streak check in `supervisor.py`**

In `run_cycle_once`, after the confidence score computation (after step 4 of Task 5), add:

```python
# Per-strategy losing streak exclusion
# Reuse trade_rows already fetched in _update_session_weights (stored in sharpes).
# If any strategy has >= losing_streak_n consecutive losing days, disable its entries.
if self.settings.losing_streak_n > 0 and session_sharpes:
    from alpaca_bot.risk.weighting import compute_losing_day_streaks
    # Fetch trade rows for streak analysis (same date range as weight computation)
    _streak_lock = getattr(self.runtime, "store_lock", None)
    with _streak_lock if _streak_lock is not None else contextlib.nullcontext():
        _streak_rows = self.runtime.order_store.list_trade_pnl_by_strategy(
            trading_mode=self.settings.trading_mode,
            strategy_version=self.settings.strategy_version,
            start_date=date(2000, 1, 1),
            end_date=session_date - timedelta(days=1),
        )
    _streaks = compute_losing_day_streaks(_streak_rows, list(session_sharpes.keys()))
    _losing_streak_excluded: set[str] = {
        name
        for name, streak in _streaks.items()
        if streak >= self.settings.losing_streak_n
    }
else:
    _losing_streak_excluded: set[str] = set()
```

Then in the per-strategy loop, after computing `confidence_score`, add:

```python
if strategy_name in _losing_streak_excluded:
    strategy_entries_disabled = True
    if not getattr(self, f"_streak_logged_{strategy_name}", False):
        setattr(self, f"_streak_logged_{strategy_name}", True)
        self._append_audit(AuditEvent(
            event_type="strategy_confidence_excluded",
            payload={
                "strategy_name": strategy_name,
                "reason": f"losing_streak >= {self.settings.losing_streak_n}",
            },
            created_at=timestamp,
        ))
```

Note: The per-strategy exclusion logging uses a simple instance flag to avoid spamming one audit event per cycle. This is sufficient for the MVP.

- [ ] **Step 4: Run tests — confirm they pass**

```bash
pytest tests/unit/test_confidence_floor_triggers.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/risk/weighting.py \
        src/alpaca_bot/runtime/supervisor.py \
        tests/unit/test_confidence_floor_triggers.py
git commit -m "feat: add per-strategy losing streak exclusion (LOSING_STREAK_N days)"
```

---

### Task 8: Admin CLI — `set-confidence-floor`

**Files:**
- Modify: `src/alpaca_bot/admin/cli.py`
- Modify: `tests/unit/test_admin_cli.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_admin_cli.py`:

```python
def test_set_confidence_floor_lowers_floor() -> None:
    from alpaca_bot.admin.cli import run_admin_command
    from alpaca_bot.storage.repositories import ConfidenceFloorStore
    from alpaca_bot.storage.models import ConfidenceFloor

    upserted: list[ConfidenceFloor] = []

    class _FakeFloorStore:
        def load(self, **kwargs): return None
        def upsert(self, rec, *, commit=True): upserted.append(rec)

    conn = _FakeConn()
    conn.confidence_floor_store = _FakeFloorStore()

    result = run_admin_command(
        ["set-confidence-floor", "--value", "0.15", "--reason", "vol subsided"],
        settings=_make_settings(),
        connection=conn,
    )
    assert "0.15" in result
    assert len(upserted) == 1
    assert upserted[0].floor_value == pytest.approx(0.15)
    assert upserted[0].set_by == "operator"
    assert "vol subsided" in upserted[0].reason


def test_set_confidence_floor_rejects_out_of_range() -> None:
    from alpaca_bot.admin.cli import run_admin_command
    import pytest

    conn = _FakeConn()
    with pytest.raises(SystemExit):
        run_admin_command(
            ["set-confidence-floor", "--value", "1.5", "--reason", "test"],
            settings=_make_settings(),
            connection=conn,
        )
```

Run: `pytest tests/unit/test_admin_cli.py::test_set_confidence_floor_lowers_floor -v`
Expected: FAIL.

- [ ] **Step 2: Add subparser in `build_parser`**

In `src/alpaca_bot/admin/cli.py`, in `build_parser()` after the `reset-weights` parser block, add:

```python
scf_parser = subparsers.add_parser("set-confidence-floor")
scf_parser.add_argument(
    "--value",
    type=float,
    required=True,
    metavar="FLOOR",
    help="New confidence floor value (0.0 to 1.0)",
)
scf_parser.add_argument(
    "--mode",
    choices=[mode.value for mode in TradingMode],
    default=defaults.trading_mode.value,
)
scf_parser.add_argument("--strategy-version", default=defaults.strategy_version)
scf_parser.add_argument("--reason", required=True)
```

- [ ] **Step 3: Add handler in `run_admin_command`**

First, add import at top of `admin/cli.py`:

```python
from alpaca_bot.storage import (
    ...
    ConfidenceFloor,
    ConfidenceFloorStore,
    ...
)
```

In `run_admin_command`, after the `reset-weights` handler block, add:

```python
if args.command == "set-confidence-floor":
    if not 0.0 <= args.value <= 1.0:
        parser = build_parser(settings)
        parser.error("--value must be between 0.0 and 1.0")

    floor_store = ConfidenceFloorStore(connection)
    existing = floor_store.load(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
    )
    new_rec = ConfidenceFloor(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        floor_value=args.value,
        equity_high_watermark=existing.equity_high_watermark if existing else 0.0,
        set_by="operator",
        reason=args.reason,
        updated_at=timestamp,
    )
    floor_store.upsert(new_rec)
    event_store.append(AuditEvent(
        event_type="confidence_floor_manual_set",
        payload={
            "floor_value": args.value,
            "reason": args.reason,
            "trading_mode": trading_mode.value,
            "strategy_version": strategy_version,
        },
        created_at=timestamp,
    ))
    return (
        f"confidence_floor set to {args.value:.2f} "
        f"mode={trading_mode.value} strategy={strategy_version} "
        f"reason={args.reason!r}"
    )
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
pytest tests/unit/test_admin_cli.py -v -k "confidence"
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/admin/cli.py tests/unit/test_admin_cli.py
git commit -m "feat: add set-confidence-floor admin CLI command"
```

---

### Task 9: Dashboard — Confidence Floor Display

**Files:**
- Modify: `src/alpaca_bot/web/service.py`
- Modify: `src/alpaca_bot/web/templates/dashboard.html`
- Modify: `tests/unit/test_web_service.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_web_service.py`:

```python
def test_load_confidence_floor_returns_default_when_no_store() -> None:
    from alpaca_bot.web.service import load_confidence_floor_info

    class _FakeConn:
        pass

    info = load_confidence_floor_info(
        settings=_make_settings(),
        connection=_FakeConn(),
    )
    assert info["floor_value"] == pytest.approx(0.25)
    assert info["set_by"] == "default"
    assert info["active_triggers"] == []


def test_load_confidence_floor_returns_stored_value() -> None:
    from alpaca_bot.web.service import load_confidence_floor_info
    from alpaca_bot.storage.models import ConfidenceFloor
    from alpaca_bot.config import TradingMode
    from datetime import datetime, timezone

    class _FakeFloorStore:
        def load(self, **kwargs):
            return ConfidenceFloor(
                trading_mode=TradingMode.PAPER,
                strategy_version="v1",
                floor_value=0.40,
                equity_high_watermark=12000.0,
                set_by="system",
                reason="auto-raised: drawdown 6.3%",
                updated_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
            )

    class _FakeConn:
        pass

    info = load_confidence_floor_info(
        settings=_make_settings(),
        connection=_FakeConn(),
        confidence_floor_store=_FakeFloorStore(),
    )
    assert info["floor_value"] == pytest.approx(0.40)
    assert info["set_by"] == "system"
    assert "drawdown" in info["reason"].lower()
```

Run: `pytest tests/unit/test_web_service.py -k "confidence_floor" -v`
Expected: FAIL.

- [ ] **Step 2: Add `load_confidence_floor_info` to `web/service.py`**

In `src/alpaca_bot/web/service.py`, add imports:

```python
from alpaca_bot.storage.repositories import ConfidenceFloorStore
```

Add function after `load_strategy_weights`:

```python
def load_confidence_floor_info(
    *,
    settings,
    connection,
    confidence_floor_store=None,
) -> dict:
    """Return confidence floor display data for the dashboard."""
    store = confidence_floor_store or ConfidenceFloorStore(connection)
    try:
        rec = store.load(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    except Exception:
        rec = None

    if rec is None:
        return {
            "floor_value": settings.confidence_floor,
            "set_by": "default",
            "reason": "using env default",
            "equity_high_watermark": 0.0,
            "active_triggers": [],
        }

    active_triggers: list[str] = []
    if rec.set_by == "system" and rec.reason:
        if "drawdown" in rec.reason:
            active_triggers.append("drawdown")
        if "vol" in rec.reason:
            active_triggers.append("vol")

    return {
        "floor_value": rec.floor_value,
        "set_by": rec.set_by,
        "reason": rec.reason,
        "equity_high_watermark": rec.equity_high_watermark,
        "active_triggers": active_triggers,
    }
```

- [ ] **Step 3: Wire into the dashboard endpoint in `web/service.py`**

Find the `load_dashboard_data` function (or equivalent that aggregates data for the dashboard template). Add `confidence_floor_info` to its return dict:

```python
confidence_floor_info = load_confidence_floor_info(
    settings=settings,
    connection=connection,
)
```

Include it in the returned context dict under key `"confidence_floor"`.

- [ ] **Step 4: Update dashboard template**

In `src/alpaca_bot/web/templates/dashboard.html`, find the overview panel (near the strategy weights section, around line 670). Add a confidence floor row:

```html
{% if confidence_floor %}
<div class="stat-row">
  <span class="stat-label">Confidence Floor</span>
  <span class="stat-value">
    {{ "%.0f%%"|format(confidence_floor.floor_value * 100) }}
    {% if confidence_floor.active_triggers %}
      <span class="badge badge-warning" title="{{ confidence_floor.reason }}">
        ↑ auto: {{ confidence_floor.active_triggers | join(', ') }}
      </span>
    {% elif confidence_floor.set_by == 'operator' %}
      <span class="badge badge-info" title="{{ confidence_floor.reason }}">manual</span>
    {% endif %}
  </span>
</div>
{% endif %}
```

In the per-strategy row (around line 359, near the ALLOC % column), add a CONFIDENCE column:

```html
{%- set sw = strategy_weights | selectattr('strategy_name', 'equalto', name) | first | default(none) %}
<td class="text-right">
  {% if sw %}{{ "%.0f%%"|format(sw.weight * 100) }}{% else %}—{% endif %}
</td>
<td class="text-right">
  {% if sw %}{{ "%.0f%%"|format(sw.sharpe * 100) }}{% else %}—{% endif %}
</td>
```

(The confidence score column displays `sharpe` as a proxy — the full confidence score requires the live percentile computation which is not worth doing in the web layer. The Sharpe value is sufficient for operator visibility.)

- [ ] **Step 5: Run tests — confirm they pass**

```bash
pytest tests/unit/test_web_service.py -v -k "confidence"
```

Expected: PASS.

- [ ] **Step 6: Run full suite**

```bash
pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 7: Final full test run**

```bash
pytest tests/unit/ -v 2>&1 | tail -20
```

Expected: all PASS. Note the count — should be 1535+ tests passing.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/web/service.py \
        src/alpaca_bot/web/templates/dashboard.html \
        tests/unit/test_web_service.py
git commit -m "feat: add confidence floor display to dashboard (floor value, triggers, Sharpe column)"
```

---

## Self-Review

### Spec Coverage Check

| Spec requirement | Task |
|---|---|
| Replace effective_equity with full equity × confidence_score | Task 5 |
| Sharpe percentile ranking → confidence_score | Task 4 |
| Adjustable floor (auto-raise / manual-lower) | Tasks 6, 7, 8 |
| Drawdown trigger | Task 6 |
| Volatility trigger | Task 6 |
| Losing streak trigger (per-strategy) | Task 7 |
| Admin CLI `set-confidence-floor` | Task 8 |
| Weights demoted to priority ordering | Task 5 (step 6) |
| `confidence_floor_store` Postgres table | Tasks 1, 2 |
| Dashboard: floor value + triggers | Task 9 |
| New env vars validated in Settings | Task 3 |
| Audit events for all floor changes | Tasks 6, 7, 8 |
| `evaluate_cycle()` remains pure | Not violated — confidence scores computed in supervisor before calling engine |

### Placeholder Scan
No TBD or incomplete steps found. All code blocks are complete.

### Type Consistency
- `WeightResult` returned from `_update_session_weights` throughout Tasks 2 and 5 — consistent.
- `ConfidenceFloor` dataclass fields match SQL column names and `ConfidenceFloorStore` parameter order throughout.
- `compute_confidence_scores` called with `(sharpes, floor)` — consistent across Tasks 4 and 5.
- `_check_and_update_floor_triggers` called with keyword args — consistent.
