# Position Cleanup Admin Commands — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `close-excess` and `cancel-partial-fills` subcommands to `alpaca-bot-admin` to reduce 67 live positions to 20 and unblock stop order submission by canceling partially-filled entry orders.

**Architecture:** Two-file change. (1) Extend `src/alpaca_bot/admin/cli.py` — add two subparsers to `build_parser()`, three new injectable factory params to `main()` (`broker_factory`, `position_store_factory`, `order_store_factory`), and two private functions `_run_close_excess()` and `_run_cancel_partial_fills()`. (2) Extend `tests/unit/test_admin_cli.py` — add three new fake classes and four new tests following the existing `StoreFactoryStub` / `RecordingXStore` injection pattern.

**Tech Stack:** Python, argparse, pytest, existing `PositionStore`/`OrderStore`/`AuditEventStore` from `alpaca_bot.storage`, `AlpacaBroker.from_settings()` from `alpaca_bot.execution.alpaca`, `dataclasses.replace()` for frozen-dataclass status updates.

---

### Task 1: `close-excess` command (TDD)

**Files:**
- Modify: `tests/unit/test_admin_cli.py` — add `RecordingBroker`, `RecordingOrderStore`, `RecordingPositionStore` fakes and two `close-excess` tests
- Modify: `src/alpaca_bot/admin/cli.py` — add imports, subparser, factory params, `_run_close_excess`, dispatch

- [ ] **Step 1: Add fake classes and two failing tests to `tests/unit/test_admin_cli.py`**

Extend the existing `from alpaca_bot.storage import (...)` line in `tests/unit/test_admin_cli.py` to also include `OrderRecord` and `PositionRecord` (`SimpleNamespace` is already imported):

```python
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord, TradingStatus, TradingStatusValue
```

Then add these fake classes and two tests at the **end** of `tests/unit/test_admin_cli.py`:

```python
# ---------------------------------------------------------------------------
# Fakes for close-excess and cancel-partial-fills
# ---------------------------------------------------------------------------


class RecordingBroker:
    def __init__(self) -> None:
        self.cancel_calls: list[str] = []
        self.market_exit_calls: list[dict] = []

    def cancel_order(self, order_id: str) -> None:
        self.cancel_calls.append(order_id)

    def submit_market_exit(self, **kwargs) -> object:
        self.market_exit_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id=f"broker-exit-{kwargs['symbol']}",
            symbol=kwargs["symbol"],
            side="sell",
            status="ACCEPTED",
            quantity=kwargs["quantity"],
        )


class RecordingOrderStore:
    def __init__(self, *, orders: list | None = None) -> None:
        self._orders: list = orders or []
        self.saved: list = []

    def list_by_status(
        self,
        *,
        trading_mode,
        strategy_version,
        statuses: list[str],
        strategy_name=None,
    ) -> list:
        return [o for o in self._orders if o.status in statuses]

    def save(self, order, *, commit: bool = True) -> None:
        self.saved.append(order)


class RecordingPositionStore:
    def __init__(self, *, positions: list | None = None) -> None:
        self._positions: list = positions or []

    def list_all(self, *, trading_mode, strategy_version, strategy_name=None) -> list:
        return self._positions


# ---------------------------------------------------------------------------
# close-excess tests
# ---------------------------------------------------------------------------


def test_close_excess_submits_market_exits_for_positions_outside_top_n() -> None:
    """close-excess --keep 1 must exit the 2 positions with the widest stops."""
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    audit_store = RecordingAuditEventStore()

    # stop_pct = (entry_price - stop_price) / entry_price
    positions = [
        PositionRecord(
            symbol="AAPL",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=99.0,   # stop_pct = 1% → KEEP
            initial_stop_price=99.0,
            opened_at=now,
        ),
        PositionRecord(
            symbol="MSFT",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=95.0,   # stop_pct = 5% → CLOSE
            initial_stop_price=95.0,
            opened_at=now,
        ),
        PositionRecord(
            symbol="SPY",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=90.0,   # stop_pct = 10% → CLOSE
            initial_stop_price=90.0,
            opened_at=now,
        ),
    ]
    order_store = RecordingOrderStore(orders=[])
    position_store = RecordingPositionStore(positions=positions)
    broker = RecordingBroker()
    stdout = io.StringIO()

    exit_code = main(
        ["close-excess", "--keep", "1", "--mode", "paper", "--strategy-version", "v1-breakout"],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(RecordingTradingStatusStore()),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
        broker_factory=lambda _: broker,
        position_store_factory=StoreFactoryStub(position_store),
        order_store_factory=StoreFactoryStub(order_store),
    )

    assert exit_code == 0
    exited_symbols = {call["symbol"] for call in broker.market_exit_calls}
    assert exited_symbols == {"MSFT", "SPY"}
    assert "AAPL" not in exited_symbols
    closed_event_symbols = {
        e.symbol for e in audit_store.appended if e.event_type == "position_force_closed"
    }
    assert closed_event_symbols == {"MSFT", "SPY"}


def test_close_excess_dry_run_prints_plan_without_broker_calls() -> None:
    """close-excess --dry-run must print ranked table but make no broker calls or DB writes."""
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    audit_store = RecordingAuditEventStore()

    positions = [
        PositionRecord(
            symbol="AAPL",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=99.0,
            initial_stop_price=99.0,
            opened_at=now,
        ),
        PositionRecord(
            symbol="MSFT",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=95.0,
            initial_stop_price=95.0,
            opened_at=now,
        ),
        PositionRecord(
            symbol="SPY",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=90.0,
            initial_stop_price=90.0,
            opened_at=now,
        ),
    ]
    order_store = RecordingOrderStore(orders=[])
    position_store = RecordingPositionStore(positions=positions)
    broker = RecordingBroker()
    stdout = io.StringIO()

    exit_code = main(
        ["close-excess", "--keep", "1", "--dry-run", "--mode", "paper", "--strategy-version", "v1-breakout"],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(RecordingTradingStatusStore()),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
        broker_factory=lambda _: broker,
        position_store_factory=StoreFactoryStub(position_store),
        order_store_factory=StoreFactoryStub(order_store),
    )

    assert exit_code == 0
    assert broker.market_exit_calls == []
    assert broker.cancel_calls == []
    assert audit_store.appended == []
    rendered = stdout.getvalue()
    assert "AAPL" in rendered
    assert "MSFT" in rendered
    assert "SPY" in rendered
```

- [ ] **Step 2: Run the two new tests to verify they fail**

```bash
pytest tests/unit/test_admin_cli.py::test_close_excess_submits_market_exits_for_positions_outside_top_n tests/unit/test_admin_cli.py::test_close_excess_dry_run_prints_plan_without_broker_calls -v
```

Expected: both FAIL — `TypeError: main() got an unexpected keyword argument 'broker_factory'`

- [ ] **Step 3: Add imports and `_run_close_excess` to `src/alpaca_bot/admin/cli.py`**

At the top of `src/alpaca_bot/admin/cli.py`, add `import dataclasses` after the existing `import argparse`:

```python
import dataclasses
```

Change the `from alpaca_bot.storage import (...)` block to also include `OrderRecord`, `OrderStore`, `PositionRecord`, `PositionStore`:

```python
from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    OrderRecord,
    OrderStore,
    PositionRecord,
    PositionStore,
    StrategyFlag,
    StrategyFlagStore,
    TradingStatus,
    TradingStatusStore,
    TradingStatusValue,
)
```

Add `_run_close_excess` and `_make_default_broker` as new functions just before `_fallback_settings()` at the bottom of `src/alpaca_bot/admin/cli.py`:

```python
def _run_close_excess(
    *,
    position_store: PositionStore,
    order_store: OrderStore,
    audit_store: AuditEventStore,
    broker: object,
    keep: int,
    dry_run: bool,
    trading_mode: TradingMode,
    strategy_version: str,
    now: datetime,
    stdout: TextIO,
) -> None:
    positions = position_store.list_all(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
    )

    def _stop_pct(p: PositionRecord) -> float:
        return (p.entry_price - p.stop_price) / p.entry_price

    ranked = sorted(positions, key=_stop_pct)
    keep_symbols = {p.symbol for p in ranked[:keep]}
    to_close = ranked[keep:]

    for position in ranked:
        label = "KEEP" if position.symbol in keep_symbols else "CLOSE"
        print(
            f"{label}  {position.symbol}  stop_pct={round(_stop_pct(position) * 100, 2):.2f}%",
            file=stdout,
        )

    if dry_run or not to_close:
        return

    entry_orders = order_store.list_by_status(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        statuses=["new", "pending_submit", "partially_filled"],
    )
    stop_orders = order_store.list_by_status(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        statuses=["new", "pending_submit"],
    )

    for position in to_close:
        pct = _stop_pct(position)

        for order in entry_orders:
            if (
                order.intent_type == "entry"
                and order.symbol == position.symbol
                and order.broker_order_id
            ):
                broker.cancel_order(order.broker_order_id)

        for order in stop_orders:
            if order.intent_type == "stop" and order.symbol == position.symbol:
                if order.broker_order_id:
                    broker.cancel_order(order.broker_order_id)
                order_store.save(
                    dataclasses.replace(order, status="canceled", updated_at=now),
                    commit=False,
                )

        client_order_id = (
            f"{strategy_version}:{position.symbol}:force_exit:{now.isoformat()}"
        )
        broker_order = broker.submit_market_exit(
            symbol=position.symbol,
            quantity=position.quantity,
            client_order_id=client_order_id,
        )
        order_store.save(
            OrderRecord(
                client_order_id=client_order_id,
                symbol=position.symbol,
                side="sell",
                intent_type="exit",
                status=broker_order.status,
                quantity=position.quantity,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                broker_order_id=broker_order.broker_order_id,
                created_at=now,
                updated_at=now,
            ),
            commit=False,
        )
        audit_store.append(
            AuditEvent(
                event_type="position_force_closed",
                symbol=position.symbol,
                payload={
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "entry_price": str(position.entry_price),
                    "stop_pct": str(round(pct * 100, 2)),
                },
                created_at=now,
            ),
            commit=True,
        )


def _make_default_broker(settings: Settings) -> object:
    from alpaca_bot.execution.alpaca import AlpacaBroker
    return AlpacaBroker.from_settings(settings)
```

- [ ] **Step 4: Modify `build_parser()` to add the `close-excess` subparser**

In `build_parser()`, add this block **after** the `enable-strategy` / `disable-strategy` loop and **before** `return parser`:

```python
    ce_parser = subparsers.add_parser("close-excess")
    ce_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in TradingMode],
        default=defaults.trading_mode.value,
    )
    ce_parser.add_argument("--strategy-version", default=defaults.strategy_version)
    ce_parser.add_argument("--keep", type=int, default=20)
    ce_parser.add_argument("--dry-run", action="store_true")
```

- [ ] **Step 5: Add `broker_factory`, `position_store_factory`, `order_store_factory` params to `main()`**

Replace the current `main()` signature:

```python
def main(
    argv: Sequence[str] | None = None,
    *,
    connect: Callable[[], ConnectionProtocol] | None = None,
    trading_status_store_factory: Callable[[ConnectionProtocol], TradingStatusStore] = TradingStatusStore,
    audit_event_store_factory: Callable[[ConnectionProtocol], AuditEventStore] = AuditEventStore,
    now: Callable[[], datetime] | None = None,
    stdout: TextIO | None = None,
    settings: Settings | None = None,
    notifier: Notifier | None = None,
) -> int:
```

With:

```python
def main(
    argv: Sequence[str] | None = None,
    *,
    connect: Callable[[], ConnectionProtocol] | None = None,
    trading_status_store_factory: Callable[[ConnectionProtocol], TradingStatusStore] = TradingStatusStore,
    audit_event_store_factory: Callable[[ConnectionProtocol], AuditEventStore] = AuditEventStore,
    now: Callable[[], datetime] | None = None,
    stdout: TextIO | None = None,
    settings: Settings | None = None,
    notifier: Notifier | None = None,
    broker_factory: Callable[["Settings"], object] | None = None,
    position_store_factory: Callable[[ConnectionProtocol], PositionStore] = PositionStore,
    order_store_factory: Callable[[ConnectionProtocol], OrderStore] = OrderStore,
) -> int:
```

- [ ] **Step 6: Add `close-excess` dispatch branch in `main()`**

In `main()`, find the `elif args.command in ("enable-strategy", "disable-strategy"):` block. Add a new `elif` for `close-excess` **immediately after** that block and **before** the final `else:` (which handles halt/close-only/resume):

```python
        elif args.command == "close-excess":
            _broker = (
                broker_factory(resolved_settings)
                if broker_factory is not None
                else _make_default_broker(resolved_settings)
            )
            _run_close_excess(
                position_store=position_store_factory(connection),
                order_store=order_store_factory(connection),
                audit_store=audit_store,
                broker=_broker,
                keep=args.keep,
                dry_run=args.dry_run,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                now=timestamp,
                stdout=stdout or sys.stdout,
            )
```

The resulting dispatch order must be:
1. `if args.command == "status":`
2. `elif args.command in ("enable-strategy", "disable-strategy"):`
3. `elif args.command == "close-excess":`  ← NEW
4. `else:` (handles halt / close-only / resume, raises ValueError for unknown)

- [ ] **Step 7: Run the two `close-excess` tests to verify they pass**

```bash
pytest tests/unit/test_admin_cli.py::test_close_excess_submits_market_exits_for_positions_outside_top_n tests/unit/test_admin_cli.py::test_close_excess_dry_run_prints_plan_without_broker_calls -v
```

Expected: both PASS.

- [ ] **Step 8: Run the full test suite to check no regressions**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/admin/cli.py tests/unit/test_admin_cli.py
git commit -m "feat: add close-excess admin command to market-exit positions outside top-N by stop_pct"
```

---

### Task 2: `cancel-partial-fills` command (TDD)

**Files:**
- Modify: `tests/unit/test_admin_cli.py` — add two more tests
- Modify: `src/alpaca_bot/admin/cli.py` — add subparser, `_run_cancel_partial_fills`, dispatch

- [ ] **Step 1: Add two failing tests to `tests/unit/test_admin_cli.py`**

Add these two tests at the **end** of `tests/unit/test_admin_cli.py` (after the `close-excess` tests from Task 1):

```python
# ---------------------------------------------------------------------------
# cancel-partial-fills tests
# ---------------------------------------------------------------------------


def test_cancel_partial_fills_cancels_at_broker_and_marks_canceled_in_db() -> None:
    """cancel-partial-fills must cancel each partially_filled entry at broker and DB."""
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    audit_store = RecordingAuditEventStore()
    orders = [
        OrderRecord(
            client_order_id="v1-breakout:AAPL:entry:1",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="partially_filled",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            broker_order_id="broker-entry-aapl-1",
            created_at=now,
            updated_at=now,
        ),
        OrderRecord(
            client_order_id="v1-breakout:MSFT:entry:1",
            symbol="MSFT",
            side="buy",
            intent_type="entry",
            status="partially_filled",
            quantity=5,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            broker_order_id="broker-entry-msft-1",
            created_at=now,
            updated_at=now,
        ),
    ]
    order_store = RecordingOrderStore(orders=orders)
    position_store = RecordingPositionStore()
    broker = RecordingBroker()
    stdout = io.StringIO()

    exit_code = main(
        ["cancel-partial-fills", "--mode", "paper", "--strategy-version", "v1-breakout"],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(RecordingTradingStatusStore()),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
        broker_factory=lambda _: broker,
        position_store_factory=StoreFactoryStub(position_store),
        order_store_factory=StoreFactoryStub(order_store),
    )

    assert exit_code == 0
    assert set(broker.cancel_calls) == {"broker-entry-aapl-1", "broker-entry-msft-1"}
    canceled_ids = {o.client_order_id for o in order_store.saved if o.status == "canceled"}
    assert canceled_ids == {"v1-breakout:AAPL:entry:1", "v1-breakout:MSFT:entry:1"}
    event_types = [e.event_type for e in audit_store.appended]
    assert event_types.count("partial_fill_canceled_by_admin") == 2


def test_cancel_partial_fills_dry_run_prints_without_acting() -> None:
    """cancel-partial-fills --dry-run must print order info but make no broker or DB calls."""
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    audit_store = RecordingAuditEventStore()
    orders = [
        OrderRecord(
            client_order_id="v1-breakout:AAPL:entry:1",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="partially_filled",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            broker_order_id="broker-entry-aapl-1",
            created_at=now,
            updated_at=now,
        ),
        OrderRecord(
            client_order_id="v1-breakout:MSFT:entry:1",
            symbol="MSFT",
            side="buy",
            intent_type="entry",
            status="partially_filled",
            quantity=5,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            broker_order_id="broker-entry-msft-1",
            created_at=now,
            updated_at=now,
        ),
    ]
    order_store = RecordingOrderStore(orders=orders)
    position_store = RecordingPositionStore()
    broker = RecordingBroker()
    stdout = io.StringIO()

    exit_code = main(
        ["cancel-partial-fills", "--dry-run", "--mode", "paper", "--strategy-version", "v1-breakout"],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(RecordingTradingStatusStore()),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
        broker_factory=lambda _: broker,
        position_store_factory=StoreFactoryStub(position_store),
        order_store_factory=StoreFactoryStub(order_store),
    )

    assert exit_code == 0
    assert broker.cancel_calls == []
    assert order_store.saved == []
    assert audit_store.appended == []
    rendered = stdout.getvalue()
    assert "AAPL" in rendered
    assert "MSFT" in rendered
```

- [ ] **Step 2: Run the two new tests to verify they fail**

```bash
pytest tests/unit/test_admin_cli.py::test_cancel_partial_fills_cancels_at_broker_and_marks_canceled_in_db tests/unit/test_admin_cli.py::test_cancel_partial_fills_dry_run_prints_without_acting -v
```

Expected: both FAIL — `SystemExit` / `error: argument command: invalid choice: 'cancel-partial-fills'`

- [ ] **Step 3: Add the `cancel-partial-fills` subparser to `build_parser()` in `src/alpaca_bot/admin/cli.py`**

In `build_parser()`, add this block after the `close-excess` block added in Task 1 Step 4, before `return parser`:

```python
    cpf_parser = subparsers.add_parser("cancel-partial-fills")
    cpf_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in TradingMode],
        default=defaults.trading_mode.value,
    )
    cpf_parser.add_argument("--strategy-version", default=defaults.strategy_version)
    cpf_parser.add_argument("--dry-run", action="store_true")
```

- [ ] **Step 4: Add `_run_cancel_partial_fills` to `src/alpaca_bot/admin/cli.py`**

Add this function immediately after `_run_close_excess` (before `_make_default_broker`):

```python
def _run_cancel_partial_fills(
    *,
    order_store: OrderStore,
    audit_store: AuditEventStore,
    broker: object,
    dry_run: bool,
    trading_mode: TradingMode,
    strategy_version: str,
    now: datetime,
    stdout: TextIO,
) -> None:
    partial_entries = [
        o
        for o in order_store.list_by_status(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            statuses=["partially_filled"],
        )
        if o.intent_type == "entry"
    ]

    for order in partial_entries:
        print(
            f"{order.symbol}  client_order_id={order.client_order_id}"
            f"  broker_order_id={order.broker_order_id}",
            file=stdout,
        )

    if dry_run:
        return

    for order in partial_entries:
        if not order.broker_order_id:
            continue
        broker.cancel_order(order.broker_order_id)
        order_store.save(
            dataclasses.replace(order, status="canceled", updated_at=now),
            commit=False,
        )
        audit_store.append(
            AuditEvent(
                event_type="partial_fill_canceled_by_admin",
                symbol=order.symbol,
                payload={
                    "client_order_id": order.client_order_id,
                    "broker_order_id": order.broker_order_id,
                },
                created_at=now,
            ),
            commit=True,
        )
```

- [ ] **Step 5: Add `cancel-partial-fills` dispatch branch in `main()`**

In `main()`, add `elif args.command == "cancel-partial-fills":` **immediately after** the `elif args.command == "close-excess":` block and **before** the `else:`:

```python
        elif args.command == "cancel-partial-fills":
            _broker = (
                broker_factory(resolved_settings)
                if broker_factory is not None
                else _make_default_broker(resolved_settings)
            )
            _run_cancel_partial_fills(
                order_store=order_store_factory(connection),
                audit_store=audit_store,
                broker=_broker,
                dry_run=args.dry_run,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                now=timestamp,
                stdout=stdout or sys.stdout,
            )
```

The final dispatch order in `main()` is now:
1. `if args.command == "status":`
2. `elif args.command in ("enable-strategy", "disable-strategy"):`
3. `elif args.command == "close-excess":`
4. `elif args.command == "cancel-partial-fills":`  ← NEW
5. `else:` (handles halt / close-only / resume; raises ValueError for unknown)

- [ ] **Step 6: Run all four new tests to verify they pass**

```bash
pytest tests/unit/test_admin_cli.py::test_cancel_partial_fills_cancels_at_broker_and_marks_canceled_in_db tests/unit/test_admin_cli.py::test_cancel_partial_fills_dry_run_prints_without_acting tests/unit/test_admin_cli.py::test_close_excess_submits_market_exits_for_positions_outside_top_n tests/unit/test_admin_cli.py::test_close_excess_dry_run_prints_plan_without_broker_calls -v
```

Expected: all 4 PASS.

- [ ] **Step 7: Run the full test suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/admin/cli.py tests/unit/test_admin_cli.py
git commit -m "feat: add cancel-partial-fills admin command to unblock stop submission after 40310000"
```
