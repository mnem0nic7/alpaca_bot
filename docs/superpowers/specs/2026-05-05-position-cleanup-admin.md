# Position Cleanup Admin Commands — Spec

**Date:** 2026-05-05

## Problem

After scaling `MAX_OPEN_POSITIONS` from 3 → 20, the supervisor entered 67 positions in one session.
All 67 stop orders are failing with Alpaca error `40310000` ("insufficient qty available") because
partially-filled entry orders hold the shares, blocking any sell-side operation (replace stop,
submit new stop). The result: 67 live positions with no active stop protection.

Two independent actions are needed:
1. Reduce 67 positions to 20, choosing the 20 with the best risk profile.
2. Unblock stop submission for the remaining 20 by canceling the partially-filled entry orders.

## Fix

Two new `alpaca-bot-admin` subcommands:

### `close-excess` — reduce to N positions

Ranks all open positions by `stop_pct = (entry_price - stop_price) / entry_price` ascending.
Tighter stop = less tolerated drawdown per dollar = better risk profile. Keeps the top N (default
20); submits market exits for the rest.

For each closed symbol:
1. Query `OrderStore` for open entry orders (`intent_type='entry'`, `status` in
   `['new', 'pending_submit', 'partially_filled']`). For each with a `broker_order_id`, call
   `broker.cancel_order(broker_order_id)`.
2. Query `OrderStore` for open stop orders (`intent_type='stop'`, `status` in
   `['new', 'pending_submit']`). For each with a `broker_order_id`, call
   `broker.cancel_order(broker_order_id)`. Update status to `'canceled'` in the DB so the
   dispatch loop does not retry.
3. Submit a market sell: `broker.submit_market_exit(symbol=..., quantity=position.quantity,
   client_order_id=...)`. Client order ID format:
   `{strategy_version}:{symbol}:force_exit:{timestamp.isoformat()}`.
4. Save the exit order to `OrderStore` with status from the broker response.
5. Append `AuditEvent(event_type='position_force_closed', symbol=symbol,
   payload={'symbol': symbol, 'quantity': position.quantity, 'entry_price': str(position.entry_price),
   'stop_pct': str(round(stop_pct * 100, 2))}, created_at=now)`.

The position row is NOT deleted here — the trade update stream handles position removal when the
market sell fill arrives.

`--dry-run` prints the ranked table (keep/close, symbol, stop_pct) without calling the broker or
writing to the DB.

### `cancel-partial-fills` — unblock stop submission

Queries all orders with `intent_type='entry'` and `status='partially_filled'` for the given
trading mode and strategy version. For each:
1. Call `broker.cancel_order(broker_order_id)`. Skip orders with no `broker_order_id`.
2. Update the order status to `'canceled'` in the DB (`OrderStore.save()`).
3. Append `AuditEvent(event_type='partial_fill_canceled_by_admin', symbol=order.symbol,
   payload={'client_order_id': order.client_order_id, 'broker_order_id': order.broker_order_id},
   created_at=now)`.

After this command, the supervisor's next cycle can replace/submit stop orders without hitting
40310000.

`--dry-run` prints the list of orders that would be canceled without acting.

## Scope

- Modify: `src/alpaca_bot/admin/cli.py`
  - Add `close-excess` and `cancel-partial-fills` subparsers to `build_parser()`
  - Add `broker_factory: Callable[[Settings], BrokerProtocol] | None = None` parameter to `main()`
  - Add `position_store_factory` and `order_store_factory` injectable parameters to `main()` for testability
  - Add `_run_close_excess()` and `_run_cancel_partial_fills()` internal functions
- Extend: `tests/unit/test_admin_cli.py`
  - 4 new tests (see Tests section)

No migrations, no new env vars.

## Command Interface

```
alpaca-bot-admin close-excess [--keep N] [--mode paper|live] [--strategy-version V] [--dry-run]
alpaca-bot-admin cancel-partial-fills [--mode paper|live] [--strategy-version V] [--dry-run]
```

`--keep` defaults to 20. `--mode` and `--strategy-version` default to the values in `Settings`.

## Typical Workflow

```bash
# 1. Preview what close-excess will do
alpaca-bot-admin close-excess --dry-run

# 2. Execute: close 47 excess positions, cancel their entry/stop orders
alpaca-bot-admin close-excess

# 3. Unblock stops for the 20 kept positions
alpaca-bot-admin cancel-partial-fills

# 4. Supervisor next cycle retries stop submission without 40310000
```

## Tests

Four new tests in `tests/unit/test_admin_cli.py`:

1. `test_close_excess_submits_market_exits_for_positions_outside_top_n`
   - 3 positions with stop_pct 1%, 5%, 10%; `--keep 1`
   - Asserts: broker market exit called for the 5% and 10% symbols, not for 1%
   - Asserts: `position_force_closed` audit events appended for closed symbols
   - Asserts: exit code 0

2. `test_close_excess_dry_run_prints_plan_without_broker_calls`
   - Same 3 positions; `--keep 1 --dry-run`
   - Asserts: broker NOT called, no audit events written, exit code 0, stdout contains symbol names

3. `test_cancel_partial_fills_cancels_at_broker_and_marks_canceled_in_db`
   - 2 partially_filled entry orders
   - Asserts: `broker.cancel_calls` contains both broker_order_ids
   - Asserts: `order_store.saved` shows status='canceled' for both
   - Asserts: `partial_fill_canceled_by_admin` audit events appended

4. `test_cancel_partial_fills_dry_run_prints_without_acting`
   - 2 partially_filled entry orders; `--dry-run`
   - Asserts: broker NOT called, no DB changes, stdout contains order info

## Safety

- Both commands default to `--mode paper`. Passing `--mode live` is explicit.
- Both commands are idempotent: calling `cancel-partial-fills` twice is safe (second call finds
  no `partially_filled` entries).
- `close-excess` is NOT idempotent: a second run will find the same positions (position rows
  persist until the fill arrives). The `--dry-run` flag should be used to verify before running.
- The broker calls (cancel, market exit) happen BEFORE the audit event writes. A crash between
  broker call and audit write leaves the position in an ambiguous state, but the trade update
  stream will still process the fill and update the position table correctly.
- No change to `evaluate_cycle()` or the advisory lock. These commands run as one-shot operators
  alongside the running supervisor.
