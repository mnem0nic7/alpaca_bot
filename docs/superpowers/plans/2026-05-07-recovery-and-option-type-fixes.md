# Recovery Report Completeness and Option Fill Type Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two latent correctness issues: (1) a zero-qty broker position leaves its symbol in `broker_positions_by_symbol`, so the clearing loop undercounts and omits the "local position missing at broker" mismatch for that symbol; (2) `option_store.update_fill` receives `float` for `filled_quantity` after the 2026-05-07 fractional-fill fix, but the method signature and DB column expect `int`.

**Architecture:** Both fixes are surgical — one line each in the production code. No new settings, no DB migrations, no new I/O. Each fix is covered TDD-first using the project's fake-store DI pattern (fake callables, in-memory stores — no mocks).

**Tech Stack:** Python, pytest.

---

## Files

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/startup_recovery.py` | Add `broker_positions_by_symbol.pop(...)` before `continue` in qty<=0 guard |
| `tests/unit/test_startup_recovery.py` | Add test: zero-qty broker position + existing local position → cleared_position_count==1, correct mismatch string |
| `src/alpaca_bot/runtime/trade_updates.py` | Cast `normalized.filled_qty` to `int` at `option_store.update_fill` call site |
| `tests/unit/test_trade_updates.py` | Add test: option fill with float filled_qty calls `update_fill` with `int` filled_quantity |

---

## Task 1: Fix startup_recovery.py — zero-qty broker position clears from report correctly

**Spec:** `docs/superpowers/specs/2026-05-07-recovery-and-option-type-fixes-design.md` § Bug 1

**Files:**
- Modify: `tests/unit/test_startup_recovery.py` (append after existing `test_recover_startup_state_skips_negative_qty_broker_position`)
- Modify: `src/alpaca_bot/runtime/startup_recovery.py:127`

**Background:** `broker_positions_by_symbol` is a dict built from ALL broker positions at line 102, before the qty<=0 guard runs. The guard fires and calls `continue` without removing the symbol from the dict. The clearing loop at line 204 checks `if position.symbol not in broker_positions_by_symbol` — so if a local position exists for the zero-qty symbol, it is never counted in `cleared_position_count` and the mismatch string "local position missing at broker: SYMBOL" is never recorded, even though the position IS correctly omitted from `synced_positions` and cleared by `position_store.replace_all()`.

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/unit/test_startup_recovery.py`:

```python


# ---------------------------------------------------------------------------
# Regression: zero-qty broker position must appear in cleared_position_count
# ---------------------------------------------------------------------------

def test_recover_startup_state_zero_qty_broker_position_counts_as_cleared() -> None:
    """A broker position with quantity=0 (stale closed position not yet purged)
    must be skipped by the guard AND counted in cleared_position_count when a
    corresponding local position exists, so the report accurately reflects the
    state change and operators can diagnose it.

    Before the fix, cleared_position_count stays at 0 for the zero-qty symbol
    because it is still in broker_positions_by_symbol when the clearing loop runs.
    """
    settings = make_settings()
    now = datetime(2026, 5, 7, 20, 0, tzinfo=timezone.utc)
    skyt_local = PositionRecord(
        symbol="SKYT",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=2,
        entry_price=33.61,
        stop_price=33.0,
        initial_stop_price=33.0,
        opened_at=now,
    )
    position_store = RecordingPositionStore(existing_positions=[skyt_local])
    order_store = RecordingOrderStore()
    audit_event_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol="SKYT", quantity=0, entry_price=33.61, market_value=0.0),
        ],
        broker_open_orders=[],
        now=now,
    )

    # The guard mismatch must fire (qty <= 0 skipped).
    skyt_guard_mismatches = [m for m in report.mismatches if "non-positive quantity skipped" in m and "SKYT" in m]
    assert skyt_guard_mismatches, (
        f"Expected guard mismatch for SKYT, got mismatches={report.mismatches}"
    )

    # The clearing mismatch must also fire (local position now has no broker counterpart).
    skyt_clearing_mismatches = [m for m in report.mismatches if "local position missing at broker" in m and "SKYT" in m]
    assert skyt_clearing_mismatches, (
        f"Expected 'local position missing at broker: SKYT' in mismatches, got {report.mismatches}"
    )

    # cleared_position_count must include SKYT.
    assert report.cleared_position_count == 1, (
        f"cleared_position_count should be 1, got {report.cleared_position_count}"
    )

    # SKYT must not appear in synced positions.
    synced_symbols = {
        pos.symbol
        for call in position_store.replace_all_calls
        for pos in call["positions"]
    }
    assert "SKYT" not in synced_symbols, "zero-qty SKYT must not be written to position_store"
```

- [ ] **Step 2: Run the test to confirm red**

```bash
pytest tests/unit/test_startup_recovery.py::test_recover_startup_state_zero_qty_broker_position_counts_as_cleared -v
```

Expected: **FAILED** — `cleared_position_count` is 0 instead of 1, and "local position missing at broker: SKYT" is absent from mismatches because SKYT is still in `broker_positions_by_symbol` when the clearing loop runs.

- [ ] **Step 3: Add the pop in startup_recovery.py**

In `src/alpaca_bot/runtime/startup_recovery.py`, locate the guard at line 127 (`continue` at the end of the `if broker_position.quantity <= 0:` block). Insert `broker_positions_by_symbol.pop(broker_position.symbol, None)` immediately before `continue`:

```python
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="startup_recovery_skipped_nonpositive_qty",
                    symbol=broker_position.symbol,
                    payload={"symbol": broker_position.symbol, "qty": broker_position.quantity},
                    created_at=timestamp,
                ),
                commit=False,
            )
            broker_positions_by_symbol.pop(broker_position.symbol, None)
            continue
```

The pop removes the symbol from the dict. The clearing loop (line 204) then sees the local position as having no broker counterpart and correctly records the mismatch and increments `cleared_position_count`.

- [ ] **Step 4: Run the test to confirm green**

```bash
pytest tests/unit/test_startup_recovery.py::test_recover_startup_state_zero_qty_broker_position_counts_as_cleared -v
```

Expected: **PASSED**.

- [ ] **Step 5: Run the full test file**

```bash
pytest tests/unit/test_startup_recovery.py -v
```

Expected: all tests **PASS**. The existing `test_recover_startup_state_skips_negative_qty_broker_position` must still pass — SKYT in that test has no local position record, so the pop does not change its outcome (the clearing loop iterates `local_positions`, finds nothing for SKYT, adds nothing to mismatches).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_startup_recovery.py src/alpaca_bot/runtime/startup_recovery.py
git commit -m "fix: pop zero-qty broker symbol from dict so clearing loop reports correctly

When a broker position has quantity=0 (stale closed position) the guard
correctly skips it and calls continue, but left the symbol in
broker_positions_by_symbol. The clearing loop checks 'symbol not in
broker_positions_by_symbol' — so any corresponding local PositionRecord
was silently excluded from cleared_position_count and the
'local position missing at broker' mismatch string was never produced.

The local position was always cleared from the DB by replace_all(), but the
report was misleading. Pop the symbol before continue so the clearing loop
sees it as absent from the broker and produces the correct diagnostic."
```

---

## Task 2: Fix trade_updates.py — option fill passes int filled_quantity

**Spec:** `docs/superpowers/specs/2026-05-07-recovery-and-option-type-fixes-design.md` § Bug 2

**Files:**
- Modify: `tests/unit/test_trade_updates.py` (append at end of file)
- Modify: `src/alpaca_bot/runtime/trade_updates.py:108`

**Background:** After the 2026-05-07 fix, `normalized.filled_qty` is now `float | None`. The equity path uses this correctly — fractional share quantities must be floats. But the option routing path at lines 104–111 passes `normalized.filled_qty` directly to `option_store.update_fill(... filled_quantity: int ...)`. Option contracts are always whole numbers; the `OptionOrderRecord.filled_quantity: int | None`, the `option_orders.filled_quantity INTEGER` DB column, and the deserializer (`int(row[15])`) are all semantically correct. The fix is a cast at the call site: `int(normalized.filled_qty)`.

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/unit/test_trade_updates.py`:

```python


# ---------------------------------------------------------------------------
# Regression: option fill must call update_fill with int filled_quantity
# ---------------------------------------------------------------------------

class RecordingOptionStore:
    """Minimal fake option_order_store that records update_fill calls."""

    def __init__(self) -> None:
        self.update_fill_calls: list[dict] = []

    def update_fill(
        self,
        *,
        client_order_id: str,
        broker_order_id: str,
        fill_price: float,
        filled_quantity: int,
        status: str,
        updated_at,
    ) -> None:
        self.update_fill_calls.append({
            "client_order_id": client_order_id,
            "broker_order_id": broker_order_id,
            "fill_price": fill_price,
            "filled_quantity": filled_quantity,
            "status": status,
        })


class TestOptionFillRouting:
    def test_option_fill_calls_update_fill_with_int_filled_quantity(self):
        """Option fills must call option_store.update_fill with int filled_quantity.

        Production scenario: an option fill arrives with filled_qty='1.0' (a float
        string from the broker). After the 2026-05-07 fractional-fill fix, this is
        parsed as float(1.0) instead of int(1). The option_store.update_fill method
        expects int (option contracts are always whole numbers; the DB column is
        INTEGER). The call site must explicitly cast to int.
        """
        from alpaca_bot.runtime.trade_updates import apply_trade_update

        option_store = RecordingOptionStore()
        runtime = SimpleNamespace(
            order_store=RecordingOrderStore(),
            position_store=RecordingPositionStore(),
            audit_event_store=RecordingAuditEventStore(),
            option_order_store=option_store,
            connection=SimpleNamespace(commit=lambda: None),
        )

        update = {
            "event": "fill",
            "symbol": "MSFT",
            "side": "buy",
            "status": "filled",
            "client_order_id": "option:v1-calls:2026-05-07:MSFT260516C00420000:entry",
            "broker_order_id": "broker-option-1",
            "qty": "1.0",
            "filled_qty": "1.0",
            "filled_avg_price": "5.30",
            "timestamp": NOW.isoformat(),
        }

        result = apply_trade_update(
            settings=make_settings(),
            runtime=runtime,
            update=update,
            now=NOW,
        )

        assert result.get("routed_to") == "option_store", (
            f"Expected option routing, got {result}"
        )
        assert len(option_store.update_fill_calls) == 1, (
            f"update_fill must be called exactly once, got {len(option_store.update_fill_calls)} calls"
        )
        call = option_store.update_fill_calls[0]
        assert isinstance(call["filled_quantity"], int), (
            f"filled_quantity must be int (option contracts are whole numbers), "
            f"got {type(call['filled_quantity']).__name__} = {call['filled_quantity']!r}. "
            "This means the int() cast is missing at the call site in trade_updates.py."
        )
        assert call["filled_quantity"] == 1
        assert call["fill_price"] == pytest.approx(5.30)
```

- [ ] **Step 2: Run the test to confirm red**

```bash
pytest tests/unit/test_trade_updates.py::TestOptionFillRouting -v
```

Expected: **FAILED** — `isinstance(call["filled_quantity"], int)` is False because `normalized.filled_qty` is `float(1.0)` and is passed without a cast. The assertion message will show `got float = 1.0`.

- [ ] **Step 3: Fix the cast in trade_updates.py**

In `src/alpaca_bot/runtime/trade_updates.py`, locate lines 104–111 (the `option_store.update_fill(...)` call). Change line 108:

```python
        option_store.update_fill(
            client_order_id=client_order_id,
            broker_order_id=normalized.broker_order_id or "",
            fill_price=normalized.filled_avg_price,
            filled_quantity=int(normalized.filled_qty),
            status=normalized.status,
            updated_at=timestamp,
        )
```

(was `filled_quantity=normalized.filled_qty`)

- [ ] **Step 4: Run the test to confirm green**

```bash
pytest tests/unit/test_trade_updates.py::TestOptionFillRouting -v
```

Expected: **PASSED**.

- [ ] **Step 5: Run the full test suite**

```bash
pytest -x
```

Expected: all tests **PASS**.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_trade_updates.py src/alpaca_bot/runtime/trade_updates.py
git commit -m "fix: cast option fill filled_qty to int at update_fill call site

After the 2026-05-07 fractional-fill fix, normalized.filled_qty is float|None.
The equity path uses float correctly (fractional shares). The option routing
path passed the float directly to option_store.update_fill(filled_quantity: int),
which accepts only whole-number contract counts. The DB column (INTEGER) and
OptionOrderRecord.filled_quantity (int|None) are both semantically correct for
options. Add int() cast at the call site to match the method contract."
```

---

## Verification

After both tasks, run the complete suite one final time:

```bash
pytest
```

Expected: all tests PASS.
