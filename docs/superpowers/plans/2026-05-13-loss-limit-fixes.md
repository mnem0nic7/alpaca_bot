# Loss Limit Fixes: Re-fire Flag and External Short Exclusion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two daily loss limit bugs: (1) re-fire events on supervisor restart now carry `re_fire: true` in the payload to distinguish them from genuine new breaches, and (2) unrealized P&L from externally-created short positions is excluded from the loss limit calculation so their intraday swings don't block the bot's own trading.

**Architecture:** Three layers change in dependency order. The data model gains one field each in `BrokerPosition` and `DailySessionState`. The DB migration adds the column. The storage layer reads/writes it. The supervisor adds one in-memory dict and one set for restart tracking, uses a module-level helper `_external_short_upnl`, and replaces the `total_pnl` computation with `adjusted_pnl`. All changes are purely additive — existing tests will continue to pass because: no existing tests supply short positions to the broker fake, so `_external_short_upnl` returns 0 and `adjusted_pnl == total_pnl` for all legacy scenarios; the `re_fire` key is new so existing payload assertions do not check it.

**Tech Stack:** Python 3.12, PostgreSQL, pytest, existing project fakes (`RecordingDailySessionStateStore`, `FakeBroker`)

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/execution/alpaca.py` | Add `unrealized_pl: float \| None = None` to `BrokerPosition`; populate from Alpaca |
| `src/alpaca_bot/storage/models.py` | Add `external_upnl_baseline: float \| None = None` to `DailySessionState` |
| `migrations/021_add_external_upnl_baseline.sql` | Create — `ALTER TABLE daily_session_state ADD COLUMN external_upnl_baseline REAL` |
| `src/alpaca_bot/storage/repositories.py` | Update `DailySessionStateStore.save()`, `.load()`, `list_by_session()` for new column |
| `src/alpaca_bot/runtime/supervisor.py` | Add `_session_external_upnl_baseline` dict, `_loss_limit_loaded_from_db` set, `_external_short_upnl` helper; update baseline logic and loss limit block |
| `tests/unit/test_runtime_supervisor.py` | Add 5 new tests |

---

### Task 1: Add `unrealized_pl` to `BrokerPosition` and `external_upnl_baseline` to `DailySessionState`

**Files:**
- Modify: `src/alpaca_bot/execution/alpaca.py:94-98`
- Modify: `src/alpaca_bot/storage/models.py:78-90`

- [ ] **Step 1: Write the failing tests**

Add at the bottom of `tests/unit/test_runtime_supervisor.py`:

```python
def test_broker_position_has_unrealized_pl_field() -> None:
    """BrokerPosition must accept unrealized_pl as a keyword argument."""
    from alpaca_bot.execution import BrokerPosition
    pos = BrokerPosition(
        symbol="QBTS", quantity=-500.0, entry_price=2.50,
        market_value=-650.0, unrealized_pl=-300.0,
    )
    assert pos.unrealized_pl == pytest.approx(-300.0)
    pos_no_upnl = BrokerPosition(symbol="AAPL", quantity=10.0)
    assert pos_no_upnl.unrealized_pl is None


def test_daily_session_state_has_external_upnl_baseline_field() -> None:
    """DailySessionState must accept external_upnl_baseline and default to None."""
    from alpaca_bot.storage import DailySessionState
    from alpaca_bot.config import TradingMode
    state = DailySessionState(
        session_date=date(2026, 5, 13),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        entries_disabled=False,
        flatten_complete=False,
        equity_baseline=100_000.0,
        external_upnl_baseline=-500.0,
    )
    assert state.external_upnl_baseline == pytest.approx(-500.0)
    state_default = DailySessionState(
        session_date=date(2026, 5, 13),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        entries_disabled=False,
        flatten_complete=False,
    )
    assert state_default.external_upnl_baseline is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_runtime_supervisor.py::test_broker_position_has_unrealized_pl_field tests/unit/test_runtime_supervisor.py::test_daily_session_state_has_external_upnl_baseline_field -v
```
Expected: FAIL — `BrokerPosition` has no `unrealized_pl` argument; `DailySessionState` has no `external_upnl_baseline` argument.

- [ ] **Step 3: Add `unrealized_pl` to `BrokerPosition`**

In `src/alpaca_bot/execution/alpaca.py`, replace the `BrokerPosition` dataclass (lines 93–98):

```python
@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: float
    entry_price: float | None = None
    market_value: float | None = None
    unrealized_pl: float | None = None
```

Also update `AlpacaBroker.list_positions()` (the factory inside the list comprehension, currently ending at line 280) to populate the new field:

```python
    def list_positions(self) -> list[BrokerPosition]:
        raw_positions = _retry_with_backoff(self._trading.get_all_positions)
        return [
            BrokerPosition(
                symbol=str(position.symbol).upper(),
                quantity=float(position.qty),
                entry_price=float(position.avg_entry_price)
                if getattr(position, "avg_entry_price", None) is not None
                else None,
                market_value=float(position.market_value)
                if getattr(position, "market_value", None) is not None
                else None,
                unrealized_pl=float(position.unrealized_pl)
                if getattr(position, "unrealized_pl", None) is not None
                else None,
            )
            for position in raw_positions
        ]
```

- [ ] **Step 4: Add `external_upnl_baseline` to `DailySessionState`**

In `src/alpaca_bot/storage/models.py`, replace the `DailySessionState` dataclass (lines 78–89):

```python
@dataclass(frozen=True)
class DailySessionState:
    session_date: date
    trading_mode: TradingMode
    strategy_version: str
    entries_disabled: bool
    flatten_complete: bool
    strategy_name: str = "breakout"
    last_reconciled_at: datetime | None = None
    notes: str | None = None
    equity_baseline: float | None = None
    external_upnl_baseline: float | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_runtime_supervisor.py::test_broker_position_has_unrealized_pl_field tests/unit/test_runtime_supervisor.py::test_daily_session_state_has_external_upnl_baseline_field -v
```
Expected: PASS

- [ ] **Step 6: Run full suite to confirm no regressions**

```bash
pytest tests/unit/ -x -q
```
Expected: all existing tests pass (green).

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/execution/alpaca.py src/alpaca_bot/storage/models.py tests/unit/test_runtime_supervisor.py
git commit -m "feat: add unrealized_pl to BrokerPosition and external_upnl_baseline to DailySessionState"
```

---

### Task 2: DB migration

**Files:**
- Create: `migrations/021_add_external_upnl_baseline.sql`

- [ ] **Step 1: Create the migration file**

```sql
ALTER TABLE daily_session_state ADD COLUMN IF NOT EXISTS external_upnl_baseline REAL DEFAULT NULL;
```

Save as `migrations/021_add_external_upnl_baseline.sql`.

- [ ] **Step 2: Commit**

```bash
git add migrations/021_add_external_upnl_baseline.sql
git commit -m "feat: migration 021 — add external_upnl_baseline to daily_session_state"
```

---

### Task 3: Update `DailySessionStateStore` to persist and load `external_upnl_baseline`

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py:907-1027`

- [ ] **Step 1: Update `DailySessionStateStore.save()`**

Replace the `save()` method body (lines 907–946) with:

```python
    def save(self, state: DailySessionState, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO daily_session_state (
                session_date,
                trading_mode,
                strategy_version,
                strategy_name,
                entries_disabled,
                flatten_complete,
                last_reconciled_at,
                notes,
                equity_baseline,
                updated_at,
                external_upnl_baseline
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_date, trading_mode, strategy_version, strategy_name)
            DO UPDATE SET
                entries_disabled = EXCLUDED.entries_disabled,
                flatten_complete = EXCLUDED.flatten_complete,
                last_reconciled_at = EXCLUDED.last_reconciled_at,
                notes = EXCLUDED.notes,
                equity_baseline = COALESCE(EXCLUDED.equity_baseline, daily_session_state.equity_baseline),
                updated_at = EXCLUDED.updated_at,
                external_upnl_baseline = COALESCE(EXCLUDED.external_upnl_baseline, daily_session_state.external_upnl_baseline)
            """,
            (
                state.session_date,
                state.trading_mode.value,
                state.strategy_version,
                state.strategy_name,
                state.entries_disabled,
                state.flatten_complete,
                state.last_reconciled_at,
                state.notes,
                state.equity_baseline,
                state.updated_at,
                state.external_upnl_baseline,
            ),
            commit=commit,
        )
```

- [ ] **Step 2: Update `DailySessionStateStore.load()`**

Replace the SELECT and row mapping in `load()` (lines 956–989). The SELECT appends `external_upnl_baseline` after `updated_at` so existing column indices remain unchanged (`updated_at` stays at `row[9]`, new field at `row[10]`):

```python
    def load(
        self,
        *,
        session_date: Any,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str = "breakout",
    ) -> DailySessionState | None:
        row = fetch_one(
            self._connection,
            """
            SELECT
                session_date,
                trading_mode,
                strategy_version,
                strategy_name,
                entries_disabled,
                flatten_complete,
                last_reconciled_at,
                notes,
                equity_baseline,
                updated_at,
                external_upnl_baseline
            FROM daily_session_state
            WHERE session_date = %s AND trading_mode = %s AND strategy_version = %s
              AND strategy_name IS NOT DISTINCT FROM %s
            """,
            (session_date, trading_mode.value, strategy_version, strategy_name),
        )
        if row is None:
            return None
        return DailySessionState(
            session_date=row[0],
            trading_mode=TradingMode(row[1]),
            strategy_version=row[2],
            strategy_name=row[3],
            entries_disabled=bool(row[4]),
            flatten_complete=bool(row[5]),
            last_reconciled_at=row[6],
            notes=row[7],
            equity_baseline=float(row[8]) if row[8] is not None else None,
            updated_at=row[9],
            external_upnl_baseline=float(row[10]) if row[10] is not None else None,
        )
```

- [ ] **Step 3: Update `DailySessionStateStore.list_by_session()`**

Replace the SELECT and row mapping in `list_by_session()` (lines 992–1027):

```python
    def list_by_session(
        self,
        *,
        session_date: Any,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[DailySessionState]:
        rows = fetch_all(
            self._connection,
            """
            SELECT
                session_date, trading_mode, strategy_version, strategy_name,
                entries_disabled, flatten_complete, last_reconciled_at,
                notes, equity_baseline, updated_at, external_upnl_baseline
            FROM daily_session_state
            WHERE session_date = %s
              AND trading_mode = %s
              AND strategy_version = %s
            """,
            (session_date, trading_mode.value, strategy_version),
        )
        return [
            DailySessionState(
                session_date=row[0],
                trading_mode=TradingMode(row[1]),
                strategy_version=row[2],
                strategy_name=row[3],
                entries_disabled=bool(row[4]),
                flatten_complete=bool(row[5]),
                last_reconciled_at=row[6],
                notes=row[7],
                equity_baseline=float(row[8]) if row[8] is not None else None,
                updated_at=row[9],
                external_upnl_baseline=float(row[10]) if row[10] is not None else None,
            )
            for row in rows
        ]
```

- [ ] **Step 4: Run full suite**

```bash
pytest tests/unit/ -x -q
```
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py
git commit -m "feat: update DailySessionStateStore to persist and load external_upnl_baseline"
```

---

### Task 4: Supervisor Fix 2 — exclude external short P&L from the loss limit

This task adds: a module-level helper, two new instance dicts, updates to the equity-baseline block, and replaces `total_pnl` with `adjusted_pnl` in the loss limit block.

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Modify: `tests/unit/test_runtime_supervisor.py`

- [ ] **Step 1: Write three failing tests**

Append to `tests/unit/test_runtime_supervisor.py`:

```python
def test_external_short_upnl_excluded_from_loss_limit(monkeypatch) -> None:
    """External short positions (quantity < 0, unrealized_pl set) must not count
    toward the daily loss limit. Only bot-managed adjusted P&L triggers the breach.

    Numbers:
      baseline_equity = 100_000
      account.equity  =  98_800   → raw total_pnl = -1_200
      external_upnl_baseline = -200  (stored when baseline was set)
      external_upnl_now      = -1_300 (short lost more as price rose)
      adjusted_pnl = -1_200 - (-1_300 - (-200)) = -1_200 + 1_100 = -100
      loss_limit = 1% × 100_000 = 1_000
      -100 < -1_000 → False → no breach
    """
    module, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()  # DAILY_LOSS_LIMIT_PCT=0.01
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)

    short_position = BrokerPosition(
        symbol="QBTS",
        quantity=-500.0,
        entry_price=2.50,
        market_value=-650.0,
        unrealized_pl=-1_300.0,
    )
    broker = FakeBroker(
        account=BrokerAccount(equity=98_800.0, buying_power=197_600.0, trading_blocked=False),
        open_positions=[short_position],
    )
    order_store = RecordingOrderStore(daily_pnl=0.0)
    supervisor, runtime = _make_minimal_supervisor(
        module, RuntimeSupervisor,
        settings=settings, order_store=order_store, broker=broker,
        now=now, equity_baseline=100_000.0,
    )
    from alpaca_bot.strategy.breakout import session_day as _session_day
    supervisor._session_external_upnl_baseline[_session_day(now, settings)] = -200.0

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    report = supervisor.run_cycle_once(now=lambda: now)

    assert report.entries_disabled is False, (
        "Entries must not be disabled when bot-managed adjusted_pnl is within the limit"
    )
    breach_events = [
        e for e in runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "daily_loss_limit_breached"
    ]
    assert len(breach_events) == 0


def test_external_short_upnl_baseline_persisted_on_first_cycle(monkeypatch) -> None:
    """On the first cycle of a session the supervisor must save external_upnl_baseline
    (sum of unrealized_pl for all short-qty broker positions) alongside equity_baseline."""
    module, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)

    short_position = BrokerPosition(
        symbol="QBTS",
        quantity=-500.0,
        entry_price=2.50,
        market_value=-650.0,
        unrealized_pl=-500.0,
    )
    broker = FakeBroker(
        account=BrokerAccount(equity=100_000.0, buying_power=200_000.0, trading_blocked=False),
        open_positions=[short_position],
    )
    session_store = RecordingDailySessionStateStore()
    order_store = RecordingOrderStore(daily_pnl=0.0)
    runtime = make_runtime_context(
        settings, order_store=order_store, daily_session_state_store=session_store
    )
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    supervisor.run_cycle_once(now=lambda: now)

    baseline_rows = [
        s for s in session_store.saved
        if getattr(s, "strategy_name", None) == "_equity"
    ]
    assert len(baseline_rows) >= 1
    assert baseline_rows[0].external_upnl_baseline == pytest.approx(-500.0)


def test_external_short_upnl_baseline_restored_from_db_on_restart(monkeypatch) -> None:
    """On restart, external_upnl_baseline from the persisted DB row must be loaded into
    _session_external_upnl_baseline so the loss limit adjustment stays correct."""
    module, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    session_date = date(2026, 4, 25)

    class PersistedBaselineStore(RecordingDailySessionStateStore):
        def load(
            self, *, session_date, trading_mode, strategy_version, strategy_name="breakout"
        ):
            if strategy_name == "_equity":
                return DailySessionState(
                    session_date=session_date,
                    trading_mode=trading_mode,
                    strategy_version=strategy_version,
                    strategy_name="_equity",
                    entries_disabled=False,
                    flatten_complete=False,
                    equity_baseline=100_000.0,
                    external_upnl_baseline=-300.0,
                )
            return None

    broker = FakeBroker(
        account=BrokerAccount(equity=100_000.0, buying_power=200_000.0, trading_blocked=False)
    )
    order_store = RecordingOrderStore(daily_pnl=0.0)
    runtime = make_runtime_context(
        settings,
        order_store=order_store,
        daily_session_state_store=PersistedBaselineStore(),
    )
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    supervisor.run_cycle_once(now=lambda: now)

    assert supervisor._session_external_upnl_baseline.get(session_date) == pytest.approx(-300.0), (
        "external_upnl_baseline must be loaded from persisted DB row after restart"
    )
```

- [ ] **Step 2: Run the three tests to verify they fail**

```bash
pytest tests/unit/test_runtime_supervisor.py::test_external_short_upnl_excluded_from_loss_limit tests/unit/test_runtime_supervisor.py::test_external_short_upnl_baseline_persisted_on_first_cycle tests/unit/test_runtime_supervisor.py::test_external_short_upnl_baseline_restored_from_db_on_restart -v
```
Expected: FAIL — `BrokerPosition` rejects `unrealized_pl` (fixed in Task 1, so it accepts it) but `_session_external_upnl_baseline` doesn't exist yet; `adjusted_pnl` is not yet implemented.

- [ ] **Step 3: Add `_session_external_upnl_baseline` and `_loss_limit_loaded_from_db` to `__init__`**

In `src/alpaca_bot/runtime/supervisor.py`, find the block starting `self._stale_cleanup_notified: set[date] = set()` (currently the last line of `__init__`, around line 154). Add after it:

```python
        # External short unrealized P&L at the moment equity baseline was recorded.
        # Used to neutralise external-short intraday swings from the loss-limit calculation.
        self._session_external_upnl_baseline: dict[date, float] = {}
        # Dates where _loss_limit_fired was seeded from persisted DB state (not a live breach).
        self._loss_limit_loaded_from_db: set[date] = set()
```

- [ ] **Step 4: Add `_external_short_upnl` module-level helper**

Just before the `class RuntimeSupervisor` definition (search for it in the file), add:

```python
def _external_short_upnl(broker_positions: list) -> float:
    """Sum unrealized_pl for all short (quantity < 0) broker positions.

    Alpaca's unrealized_pl already incorporates the ×100 option contract multiplier,
    so this works for both equity shorts and short option positions.
    Returns 0.0 when no short positions exist or none have unrealized_pl set.
    """
    return sum(
        bp.unrealized_pl
        for bp in broker_positions
        if bp.quantity < 0 and bp.unrealized_pl is not None
    )
```

- [ ] **Step 5: Update the equity-baseline loading block**

Find the block (around lines 339–361):
```python
        if session_date not in self._session_equity_baseline:
            persisted = self._load_session_state(
                session_date=session_date,
                strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
            )
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
                        strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
                        entries_disabled=False,
                        flatten_complete=False,
                        equity_baseline=account.equity,
                        updated_at=timestamp,
                    )
                )
```

Replace it with:

```python
        if session_date not in self._session_equity_baseline:
            persisted = self._load_session_state(
                session_date=session_date,
                strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
            )
            if persisted is not None and persisted.equity_baseline is not None:
                self._session_equity_baseline[session_date] = persisted.equity_baseline
                if persisted.external_upnl_baseline is not None:
                    self._session_external_upnl_baseline[session_date] = (
                        persisted.external_upnl_baseline
                    )
                if persisted.entries_disabled:
                    self._loss_limit_fired.add(session_date)
                    self._loss_limit_loaded_from_db.add(session_date)
            else:
                _ext_upnl = _external_short_upnl(broker_open_positions)
                self._session_equity_baseline[session_date] = account.equity
                self._session_external_upnl_baseline[session_date] = _ext_upnl
                self._save_session_state(
                    DailySessionState(
                        session_date=session_date,
                        trading_mode=self.settings.trading_mode,
                        strategy_version=self.settings.strategy_version,
                        strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
                        entries_disabled=False,
                        flatten_complete=False,
                        equity_baseline=account.equity,
                        external_upnl_baseline=_ext_upnl,
                        updated_at=timestamp,
                    )
                )
```

- [ ] **Step 6: Replace the loss limit evaluation block**

Find lines 430–482:
```python
        loss_limit = self.settings.daily_loss_limit_pct * baseline_equity
        # Include unrealized losses via broker-reported equity delta ...
        total_pnl = account.equity - baseline_equity
        if total_pnl < -loss_limit:
            self._loss_limit_fired.add(session_date)
        # During extended-hours sessions ...
        _is_extended_session = session_type in {SessionType.PRE_MARKET, SessionType.AFTER_HOURS}
        daily_loss_limit_breached = (
            total_pnl < -loss_limit
            if _is_extended_session
            else session_date in self._loss_limit_fired
        )
        if daily_loss_limit_breached and session_date not in self._loss_limit_alerted:
            self._loss_limit_alerted.add(session_date)
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

Replace with:

```python
        loss_limit = self.settings.daily_loss_limit_pct * baseline_equity
        # Exclude intraday P&L swings from externally-created short positions
        # (quantity < 0 in the broker account). Their unrealized P&L is neutralised
        # by subtracting the change since the equity baseline was recorded.
        # Fallback: if the baseline was written before this fix (NULL in DB),
        # external_upnl_baseline_val == external_upnl_now → zero adjustment.
        external_upnl_now = _external_short_upnl(broker_open_positions)
        external_upnl_baseline_val = self._session_external_upnl_baseline.get(
            session_date, external_upnl_now
        )
        adjusted_pnl = (account.equity - baseline_equity) - (
            external_upnl_now - external_upnl_baseline_val
        )
        if adjusted_pnl < -loss_limit:
            self._loss_limit_fired.add(session_date)
        # During extended-hours sessions the _loss_limit_fired set may have been
        # populated by the regular-session consecutive-loss gate (not an actual
        # loss-limit breach). Re-evaluate against real-time adjusted P&L so a small-loss
        # regular session doesn't silently block the entire after-hours session.
        _is_extended_session = session_type in {SessionType.PRE_MARKET, SessionType.AFTER_HOURS}
        daily_loss_limit_breached = (
            adjusted_pnl < -loss_limit
            if _is_extended_session
            else session_date in self._loss_limit_fired
        )
        if daily_loss_limit_breached and session_date not in self._loss_limit_alerted:
            self._loss_limit_alerted.add(session_date)
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
            self._append_audit(
                AuditEvent(
                    event_type="daily_loss_limit_breached",
                    payload={
                        "realized_pnl": realized_pnl,
                        "total_pnl": adjusted_pnl,
                        "limit": loss_limit,
                        "re_fire": session_date in self._loss_limit_loaded_from_db,
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
                            f"Total PnL {adjusted_pnl:.2f} (realized {realized_pnl:.2f}) "
                            f"exceeded limit {-loss_limit:.2f}. Entries disabled for the session."
                        ),
                    )
                except Exception:
                    logger.exception("Notifier failed to send daily loss limit alert")
```

- [ ] **Step 7: Run the three new tests to verify they pass**

```bash
pytest tests/unit/test_runtime_supervisor.py::test_external_short_upnl_excluded_from_loss_limit tests/unit/test_runtime_supervisor.py::test_external_short_upnl_baseline_persisted_on_first_cycle tests/unit/test_runtime_supervisor.py::test_external_short_upnl_baseline_restored_from_db_on_restart -v
```
Expected: all PASS.

- [ ] **Step 8: Run full suite to confirm no regressions**

```bash
pytest tests/unit/ -x -q
```
Expected: all passing.

Note: `test_daily_loss_limit_disables_entries_and_emits_audit_event_when_breached` checks `payload["total_pnl"] == pytest.approx(-600.0)` — this still passes because that test uses `FakeBroker` with no short positions, so `external_upnl_now == external_upnl_baseline_val == 0.0` and `adjusted_pnl == total_pnl == -600.0`.

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_runtime_supervisor.py
git commit -m "feat: exclude external short P&L from daily loss limit and add _session_external_upnl_baseline"
```

---

### Task 5: Supervisor Fix 1 — `re_fire` flag in `daily_loss_limit_breached` payload

The `_loss_limit_loaded_from_db` set was already added in Task 4 and the `re_fire` key was already included in the payload in Task 4's loss limit block replacement. This task writes the tests that verify the re_fire logic.

**Files:**
- Modify: `tests/unit/test_runtime_supervisor.py`

- [ ] **Step 1: Write two failing tests**

Append to `tests/unit/test_runtime_supervisor.py`:

```python
def test_loss_limit_re_fire_flag_true_when_breach_loaded_from_db(monkeypatch) -> None:
    """When entries_disabled=True is loaded from Postgres at startup, the
    daily_loss_limit_breached event fired on the first cycle must have re_fire=True.

    This identifies restart re-notifications from genuine new breaches.
    Current equity may be positive (positions recovered) — that's fine.
    """
    module, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)

    class BreachPersistedStore(RecordingDailySessionStateStore):
        def load(
            self, *, session_date, trading_mode, strategy_version, strategy_name="breakout"
        ):
            if strategy_name == "_equity":
                return DailySessionState(
                    session_date=session_date,
                    trading_mode=trading_mode,
                    strategy_version=strategy_version,
                    strategy_name="_equity",
                    entries_disabled=True,
                    flatten_complete=False,
                    equity_baseline=10_000.0,
                    external_upnl_baseline=0.0,
                )
            return None

    # Positions recovered: current equity is above baseline
    broker = FakeBroker(
        account=BrokerAccount(equity=10_100.0, buying_power=20_200.0, trading_blocked=False)
    )
    order_store = RecordingOrderStore(daily_pnl=0.0)
    runtime = make_runtime_context(
        settings,
        order_store=order_store,
        daily_session_state_store=BreachPersistedStore(),
    )
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    supervisor.run_cycle_once(now=lambda: now)

    breach_events = [
        e for e in runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "daily_loss_limit_breached"
    ]
    assert len(breach_events) == 1, "Expected re-fire event on first cycle after restart"
    assert breach_events[0].payload["re_fire"] is True, (
        "re_fire must be True when breach was loaded from DB (not triggered this process lifetime)"
    )


def test_loss_limit_re_fire_flag_false_on_genuine_breach(monkeypatch) -> None:
    """A genuine new breach that occurs during the current process lifetime
    (no persisted entries_disabled in DB) must have re_fire=False."""
    module, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()  # DAILY_LOSS_LIMIT_PCT=0.01 → limit=$100 on $10k
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)

    # equity dropped 600 from 10k baseline → adjusted_pnl=-600 < -100 limit → breached
    broker = FakeBroker(
        account=BrokerAccount(equity=9_400.0, buying_power=18_800.0, trading_blocked=False)
    )
    order_store = RecordingOrderStore(daily_pnl=0.0)
    supervisor, runtime = _make_minimal_supervisor(
        module, RuntimeSupervisor,
        settings=settings, order_store=order_store, broker=broker,
        now=now, equity_baseline=10_000.0,
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    supervisor.run_cycle_once(now=lambda: now)

    breach_events = [
        e for e in runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "daily_loss_limit_breached"
    ]
    assert len(breach_events) == 1
    assert breach_events[0].payload["re_fire"] is False, (
        "re_fire must be False when the breach first occurs in this process lifetime"
    )
```

- [ ] **Step 2: Run the two tests to verify they fail**

```bash
pytest tests/unit/test_runtime_supervisor.py::test_loss_limit_re_fire_flag_true_when_breach_loaded_from_db tests/unit/test_runtime_supervisor.py::test_loss_limit_re_fire_flag_false_on_genuine_breach -v
```
Expected: FAIL — `re_fire` key absent from payload (or `_loss_limit_loaded_from_db` missing).

After implementing Task 4 (which already added `re_fire` to the payload and `_loss_limit_loaded_from_db` to `__init__`), run these again:

```bash
pytest tests/unit/test_runtime_supervisor.py::test_loss_limit_re_fire_flag_true_when_breach_loaded_from_db tests/unit/test_runtime_supervisor.py::test_loss_limit_re_fire_flag_false_on_genuine_breach -v
```
Expected: PASS — Task 4 already implemented the logic these tests verify.

- [ ] **Step 3: Run full suite**

```bash
pytest tests/unit/ -x -q
```
Expected: all passing.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_runtime_supervisor.py
git commit -m "test: add re_fire flag and external short exclusion tests for daily loss limit"
```

---

## Final Verification

- [ ] **Run the complete test suite one last time**

```bash
pytest tests/unit/ -v 2>&1 | tail -20
```
Expected: all green. No new failures.

- [ ] **Apply the migration in the dev/prod database**

```bash
alpaca-bot-migrate
```
Expected: `021_add_external_upnl_baseline.sql` applied without error.
