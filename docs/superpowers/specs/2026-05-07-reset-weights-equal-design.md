# Reset Weights to Equal Allocation — Design Spec

## Problem

The strategy weight system uses Sharpe-proportional allocation. Equity strategies have months of trade history and non-trivial Sharpe ratios. New option strategies have zero trades, so they get `sharpe=0` and fall to the `min_weight=1%` floor. Simply enabling `ENABLE_OPTIONS_TRADING=true` would give 12 option strategies only 1% each (12% total), leaving 88% to 11 equity strategies — far from equal.

There is no operator-facing mechanism to force equal allocation across all strategies today. Direct DB manipulation is the only workaround.

## Solution

Add `alpaca-bot-admin reset-weights` — a new admin CLI subcommand that writes 1/N equal weights directly into `strategy_weights` for all currently active strategies (stamped today). The Sharpe system resumes naturally on the next session open.

## Scope

**In scope:**
- New `reset-weights` subparser in `admin/cli.py`
- `_reset_weights_equal()` private function following existing `_run_*` pattern
- Respects `ENABLE_OPTIONS_TRADING` from `Settings`: option strategies included iff enabled
- Respects `strategy_flags`: explicitly disabled strategies are excluded
- Appends `AuditEvent("strategy_weights_reset", {...})` for full audit trail
- `--dry-run` flag: prints what would be written without committing
- Unit tests using in-memory fakes (no mocks per project conventions)

**Out of scope:**
- Any change to `compute_strategy_weights` or the Sharpe algorithm
- Any dashboard UI changes
- Any new env vars
- Clearing trade history

## Behavior

### Which strategies are included

`reset-weights` builds the active set as:
1. All names in `STRATEGY_REGISTRY`
2. Plus all names in `OPTION_STRATEGY_FACTORIES` if `settings.enable_options_trading` is `True`
3. Minus any strategy with an explicit `StrategyFlag(enabled=False)` in the DB

If no flag row exists for a strategy, it is treated as enabled (same default as supervisor).

### Weight computation

```
equal_weight = 1.0 / len(active_names)
weights = {name: equal_weight for name in active_names}
sharpes  = {name: 0.0         for name in active_names}
```

Sharpe is written as 0.0 — these are placeholder weights, not Sharpe-derived.

### Persistence and cache interaction

`StrategyWeightStore.upsert_many()` writes the equal weights with `computed_at = now()` (today's date). The running supervisor's in-memory `_session_capital_weights` cache is **not** updated — a supervisor restart is required to pick up the new weights within the same session day. On the next session open, the supervisor finds `computed_at.date() == session_date` (fresh rows) and uses them as-is; they remain in effect all day.

The day *after* `reset-weights`, a fresh Sharpe computation runs normally. If all strategies still have < 5 trades, they remain at equal weight. As options strategies accumulate trade history, weights naturally shift toward Sharpe-proportional.

### Audit trail

An `AuditEvent` is appended atomically with the weight upsert (same transaction/commit):

```python
AuditEvent(
    event_type="strategy_weights_reset",
    payload={
        "mode": trading_mode.value,
        "version": strategy_version,
        "strategy_count": str(len(active_names)),
        "equal_weight": str(round(equal_weight, 6)),
        **{name: str(round(equal_weight, 4)) for name in active_names},
    },
    created_at=now,
)
```

### Dry run

With `--dry-run`, the command prints the strategy list and computed equal weight to stdout but makes no DB writes and appends no audit event.

## CLI interface

```
alpaca-bot-admin reset-weights [--mode paper|live] [--strategy-version v1-breakout] [--dry-run]
```

Output (non-dry-run):
```
reset strategy_count=23 equal_weight=4.3% mode=paper version=v1-breakout
```

Output (dry-run):
```
[dry-run] would reset 23 strategies to equal_weight=4.3%
breakout, momentum, orb, ... (all 23 names)
```

## Operator workflow

```
# 1. Set ENABLE_OPTIONS_TRADING=true in /etc/alpaca_bot/alpaca-bot.env
# 2. Redeploy (so supervisor picks up the env change)
# 3. Run reset-weights in the new container
docker exec deploy-supervisor-1 alpaca-bot-admin reset-weights
# 4. Restart supervisor to clear in-memory weight cache
docker restart deploy-supervisor-1
```

## Files to change

| File | Change |
|---|---|
| `src/alpaca_bot/admin/cli.py` | Add `reset-weights` subparser; add `_reset_weights_equal()` function; wire into `build_parser()` and `main()` |
| `tests/unit/test_admin_reset_weights.py` | New test file: equal weight output, flag exclusion, dry-run, audit event, options-disabled path |

No migration needed — no schema change.

## Safety considerations

- **No order side-effects**: `reset-weights` only writes to `strategy_weights` and `audit_events`. It does not touch `orders`, `positions`, or `trading_status`.
- **Idempotent**: Running the command twice in the same day overwrites the same rows with the same values.
- **Paper vs live**: The command respects `--mode` so it can be targeted at paper or live independently.
- **Supervisor restart required for same-day effect**: This is an acceptable limitation — the operator workflow above documents it explicitly.
