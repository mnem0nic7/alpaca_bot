# Session Evaluation Bug Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two active production bugs discovered during the 2026-05-07 paper trading session: (1) a recovery crash loop caused by a negative broker quantity, and (2) fractional fill quantities being truncated to zero by integer parsing.

**Architecture:** Both fixes are surgical — no new settings, no DB migrations, no new I/O. Bug 1 adds a guard clause at the top of the broker-positions loop in `startup_recovery.py`. Bug 2 changes two type annotations, two parse calls, and one log format string in `trade_updates.py`. Both changes are covered with TDD-first tests using the project's fake-store DI pattern.

**Tech Stack:** Python, pytest, psycopg, Alpaca Trading SDK (alpaca-py).

---

## Files

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/startup_recovery.py` | Add guard at top of broker positions loop (lines 107–108) |
| `tests/unit/test_startup_recovery.py` | Add test: negative broker qty is skipped, logged as mismatch, normal position still processed |
| `src/alpaca_bot/runtime/trade_updates.py` | `quantity`/`filled_qty` type `int\|None` → `float\|None`; parsing `_optional_int` → `_optional_float`; log format `%d` → `%g` |
| `tests/unit/test_trade_updates.py` | Add test: fractional `filled_qty` is preserved as float by `_normalize_trade_update`; add test: full `apply_trade_update` with fractional fill produces correct stop qty |

---

## Task 1: Fix startup_recovery.py — negative broker qty crash loop

**Spec:** `docs/superpowers/specs/2026-05-07-session-eval-bug-fixes-design.md` § Bug 1

**Files:**
- Modify: `tests/unit/test_startup_recovery.py` (append at end of file)
- Modify: `src/alpaca_bot/runtime/startup_recovery.py:107`

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/unit/test_startup_recovery.py`:

```python

# ---------------------------------------------------------------------------
# Regression: negative broker quantity must be skipped, not crash recovery
# ---------------------------------------------------------------------------

def test_recover_startup_state_skips_negative_qty_broker_position() -> None:
    """A broker position with quantity <= 0 (e.g. short from a double-sell) must be
    skipped and recorded as a mismatch — not inserted into the DB, which would
    violate the CHECK (quantity >= 0) constraint and crash the supervisor every cycle.

    The normal (positive-qty) position in the same call must still be processed.
    """
    settings = make_settings()
    now = datetime(2026, 5, 7, 20, 0, tzinfo=timezone.utc)
    position_store = RecordingPositionStore()
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
            BrokerPosition(symbol="SKYT", quantity=-2, entry_price=33.61, market_value=-67.22),
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=189.25, market_value=1892.5),
        ],
        broker_open_orders=[],
        now=now,
    )

    # Must not raise — the crash loop is the core symptom this test guards against.

    # SKYT must appear in mismatches (non-positive qty recorded for operator review).
    skyt_mismatches = [m for m in report.mismatches if "SKYT" in m]
    assert skyt_mismatches, (
        f"Expected a mismatch entry for SKYT negative qty, got mismatches={report.mismatches}"
    )

    # SKYT must NOT appear in synced positions (should have been skipped entirely).
    synced_symbols = {
        pos.symbol
        for call in position_store.replace_all_calls
        for pos in call["positions"]
    }
    assert "SKYT" not in synced_symbols, "negative-qty SKYT must not be written to position_store"

    # AAPL (normal positive qty) must still be processed correctly.
    assert "AAPL" in synced_symbols, "normal AAPL position must still be synced"
```

- [ ] **Step 2: Run the test to confirm red**

```bash
pytest tests/unit/test_startup_recovery.py::test_recover_startup_state_skips_negative_qty_broker_position -v
```

Expected: **FAILED** — before the fix, SKYT has no local record so it enters the `if not local_for_symbol:` branch and is appended to `synced_positions`. The assertion `assert "SKYT" not in synced_symbols` then fails because SKYT IS in the written positions.

- [ ] **Step 3: Add the guard in startup_recovery.py**

In `src/alpaca_bot/runtime/startup_recovery.py`, locate the `for broker_position in broker_open_positions:` loop at line 107. Insert immediately after that line (before the `local_for_symbol = ...` line at 108):

```python
    for broker_position in broker_open_positions:
        if broker_position.quantity <= 0:
            _log.warning(
                "startup_recovery: skipping broker position %s with non-positive qty=%s "
                "(possible short or stale position — manual review required)",
                broker_position.symbol,
                broker_position.quantity,
            )
            mismatches.append(
                f"broker position non-positive quantity skipped: {broker_position.symbol} qty={broker_position.quantity}"
            )
            continue
        local_for_symbol = local_positions_by_symbol.get(broker_position.symbol, [])
```

The `continue` skips all position syncing, stop queuing, and the `synced_positions.append(...)` calls below — so the negative-qty position is never written anywhere.

**Safety property:** The mismatch string written by the guard causes `recovery_report.mismatches` to be non-empty. At `supervisor.py:444`, the supervisor sets `entries_disabled = True` whenever `bool(recovery_report.mismatches)` is True. This means no new entries are submitted while a non-positive broker position is outstanding — automatic self-protection without any additional logic.

- [ ] **Step 4: Run the test to confirm green**

```bash
pytest tests/unit/test_startup_recovery.py::test_recover_startup_state_skips_negative_qty_broker_position -v
```

Expected: **PASSED**.

- [ ] **Step 5: Run the full test file**

```bash
pytest tests/unit/test_startup_recovery.py -v
```

Expected: all tests **PASS**.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_startup_recovery.py src/alpaca_bot/runtime/startup_recovery.py
git commit -m "fix: skip non-positive broker qty in startup_recovery to stop crash loop

A SKYT short position (-2 shares) caused recover_startup_state() to attempt
inserting an OrderRecord with quantity=-2, violating orders_quantity_check.
The exception was caught at the supervisor level but skipped recovery entirely,
producing 236 recovery_exception audit events per day and disabling mismatch
detection.

Guard clause skips and logs any broker position with quantity <= 0, records it
as a mismatch for operator review, and allows the rest of recovery to proceed."
```

---

## Task 2: Fix trade_updates.py — fractional fill quantities truncated to zero

**Spec:** `docs/superpowers/specs/2026-05-07-session-eval-bug-fixes-design.md` § Bug 2

**Files:**
- Modify: `tests/unit/test_trade_updates.py` (append at end of file)
- Modify: `src/alpaca_bot/runtime/trade_updates.py` (lines 50–51, 189, 481–482)

- [ ] **Step 1: Write two failing tests**

Append to the end of `tests/unit/test_trade_updates.py`:

```python

# ---------------------------------------------------------------------------
# Regression: fractional filled_qty must not be truncated to zero
# ---------------------------------------------------------------------------

class TestFractionalFillQuantity:
    def test_normalize_preserves_fractional_filled_qty(self):
        """_normalize_trade_update must return filled_qty as a float, not truncated int.

        Production scenario: MSFT fill arrived with filled_qty=0.7737. The handler
        stored it as int(float(0.7737)) == 0, causing the protective stop to be
        submitted with qty=0 and rejected by Alpaca ('qty must be > 0').
        """
        from alpaca_bot.runtime.trade_updates import _normalize_trade_update

        payload = {
            "event": "fill",
            "symbol": "MSFT",
            "side": "buy",
            "status": "filled",
            "client_order_id": "v1-breakout:2026-04-25:MSFT:entry:2026-04-25T14:00:00+00:00",
            "broker_order_id": "broker-msft-1",
            "qty": "0.7737",
            "filled_qty": "0.7737",
            "filled_avg_price": "425.63",
            "timestamp": NOW.isoformat(),
        }

        normalized = _normalize_trade_update(payload)

        assert normalized.filled_qty == pytest.approx(0.7737), (
            f"filled_qty was truncated: expected ~0.7737, got {normalized.filled_qty!r}. "
            "This means _optional_int is still being used instead of _optional_float."
        )
        assert normalized.quantity == pytest.approx(0.7737), (
            f"quantity was truncated: expected ~0.7737, got {normalized.quantity!r}."
        )

    def test_apply_fractional_fill_creates_stop_with_correct_qty(self):
        """A fractional entry fill must produce a protective stop with the fractional qty.

        Production scenario: MSFT partially filled 0.7737 shares. Stop was created
        with quantity=0 (truncated by _optional_int), causing Alpaca to reject it.
        After the fix, stop.quantity == 0.7737.
        """
        entry_order = _make_entry_order(
            client_order_id="v1-breakout:2026-04-25:MSFT:entry:2026-04-25T14:00:00+00:00",
            symbol="MSFT",
            initial_stop_price=418.50,
            quantity=1,
        )
        runtime = _make_runtime(orders=[entry_order])

        update = {
            "event": "fill",
            "symbol": "MSFT",
            "side": "buy",
            "status": "filled",
            "client_order_id": entry_order.client_order_id,
            "broker_order_id": "broker-msft-1",
            "qty": "0.7737",
            "filled_qty": "0.7737",
            "filled_avg_price": "425.63",
            "timestamp": NOW.isoformat(),
        }

        result = _apply(runtime, update)

        assert result["position_updated"] is True
        assert result["protective_stop_queued"] is True

        stop_orders = [o for o in runtime.order_store.saved if o.intent_type == "stop"]
        assert len(stop_orders) == 1, "exactly one stop order must be created"
        stop = stop_orders[0]
        assert stop.quantity == pytest.approx(0.7737), (
            f"stop.quantity was {stop.quantity!r}, expected ~0.7737. "
            "A stop with qty=0 would be rejected by Alpaca with 'qty must be > 0'."
        )
        assert stop.stop_price == pytest.approx(418.50)
        assert stop.status == "pending_submit"
```

- [ ] **Step 2: Run the tests to confirm red**

```bash
pytest tests/unit/test_trade_updates.py::TestFractionalFillQuantity -v
```

Expected: **2 FAILED** — `normalized.filled_qty` will be `0` (int-truncated), `stop.quantity` will be `0`.

- [ ] **Step 3: Fix the type annotations in the TradeUpdate dataclass**

In `src/alpaca_bot/runtime/trade_updates.py`, change lines 50–51:

```python
    quantity: float | None
    filled_qty: float | None
```

(was `int | None` for both)

- [ ] **Step 4: Fix the parse calls at lines 481–482**

In the same file, change:

```python
        quantity=_optional_float(payload.get("qty")),
        filled_qty=_optional_float(payload.get("filled_qty")),
```

(was `_optional_int(...)` for both)

- [ ] **Step 5: Fix the log format at line 189**

In the same file, change:

```python
            "trade_updates: entry fill %s — order_qty=%g filled_qty=%s fill_price=%s",
```

(was `order_qty=%d`, which silently truncates floats: `"%d" % 0.7737` produces `"0"` rather than `"0.7737"`. `%g` formats both fractional and integer quantities correctly without trailing zeros: `0.7737`, `10`, `25`.)

- [ ] **Step 6: Run the tests to confirm green**

```bash
pytest tests/unit/test_trade_updates.py::TestFractionalFillQuantity -v
```

Expected: **2 PASSED**.

- [ ] **Step 7: Run the full test suite**

```bash
pytest -x
```

Expected: all tests **PASS**. If any test asserts `stop.quantity == 5` (an integer), verify it still passes — `pytest.approx` is not needed there because `int(5) == float(5)` in Python, and the fake store never goes near the DB constraint.

- [ ] **Step 8: Commit**

```bash
git add tests/unit/test_trade_updates.py src/alpaca_bot/runtime/trade_updates.py
git commit -m "fix: parse filled_qty and quantity as float to preserve fractional fills

MSFT and QQQ had status=filled with filled_quantity=0.0000 in the orders table.
Their protective stops were created with quantity=0 and rejected by Alpaca
('qty must be > 0'), leaving both positions open with no stop protection.

Root cause: TradeUpdate.filled_qty and .quantity were typed as int|None and
parsed with _optional_int(), which calls int(float(value)) — truncating 0.7737
to 0. Change both fields to float|None and parse with the existing _optional_float().
Also fix %d log format to %g so it handles fractional quantities."
```

---

## Verification

After both tasks, run the complete suite one final time:

```bash
pytest
```

Expected: all tests PASS.

**Production signals to watch after deploying:**

1. `recovery_exception` audit events should drop from ~236/day to 0. Check with:
   ```sql
   SELECT COUNT(*), MAX(created_at)
   FROM audit_events
   WHERE event_type = 'recovery_exception'
     AND created_at > NOW() - INTERVAL '10 minutes';
   ```

2. Any new fractional fill should produce a stop order with `filled_quantity > 0`:
   ```sql
   SELECT symbol, quantity, filled_quantity, status, created_at
   FROM orders
   WHERE intent_type = 'stop'
     AND created_at > NOW() - INTERVAL '1 day'
   ORDER BY created_at DESC;
   ```
   Every stop row should have `filled_quantity IS NULL` initially and, once the stop order itself fills, a non-zero value.
