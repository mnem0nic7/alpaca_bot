# Options v1 Readiness Assessment

**Date:** 2026-05-04
**Branch:** stop-order-reliability-fixes (19 commits ahead of origin, not yet pushed)
**Overall rating:** NEEDS TWO FIXES BEFORE ACTIVATION

---

## Summary

All 11 implementation tasks of options strategies v1 (long call buying on breakout
signals) are committed and 1176 tests pass. The feature code is correct and well-tested
at the unit level. However, two gaps prevent it from activating in production:

1. **Blocker:** `RuntimeSupervisor.from_settings()` does not wire `option_chain_adapter`
   or `option_broker`. The feature will silently never activate because both default to
   `None`. There is also no `ENABLE_OPTIONS_TRADING` env-var gate — operators cannot
   opt in or out at runtime.

2. **Should-fix:** `DEPLOYMENT.md` documents no options-related env vars
   (`OPTION_DTE_MIN`, `OPTION_DTE_MAX`, `OPTION_DELTA_TARGET`). An operator reading the
   deployment template would not know these exist.

These two items are the only blockers. Everything else is green.

---

## Dimension-by-Dimension Assessment

### Completeness — GREEN
- All 11 tasks committed in git; no TODOs, stubs, or `NotImplemented` in option code.
- 42 option-specific unit tests across 7 files; all pass.

### Test Coverage — GREEN
- `test_option_chain.py` — 6 tests: OCC parsing, snapshot parsing, delta extraction
- `test_option_selector.py` — 11 tests: contract selection, DTE/delta filtering
- `test_option_dispatch.py` — 5 tests: write-before-dispatch, fill routing, audit events
- `test_option_storage.py` — 6 tests: save/load, list_open_option_positions
- `test_option_cycle_routing.py` — 3 tests: ENTRY intent routing
- `test_option_domain_settings.py` — 7 tests: Settings option fields, validation
- `test_supervisor_option_integration.py` — 4 tests: supervisor contract tests
- Coverage gap: no end-to-end test of `from_settings()` with options wired.
  Acceptable — all inner components are individually tested.

### BLOCKER: Production Wiring — RED
`RuntimeSupervisor.from_settings()` (the production factory used by
`supervisor_cli.py`) does not instantiate `AlpacaOptionChainAdapter` or
wire it and `AlpacaBroker` as the option broker:

```python
@classmethod
def from_settings(cls, settings: Settings) -> "RuntimeSupervisor":
    return cls(
        settings=settings,
        runtime=bootstrap_runtime(settings),
        broker=AlpacaBroker.from_settings(settings),
        market_data=AlpacaMarketDataAdapter.from_settings(settings),
        stream=AlpacaTradingStreamAdapter.from_settings(settings),
        notifier=build_notifier(settings),
        # option_chain_adapter and option_broker are MISSING
    )
```

`AlpacaOptionChainAdapter` also lacks a `from_settings()` factory, so there is no
standard construction path from credentials. The `StockOptionDataClient` from
alpaca-py needs to be instantiated with the same API key/secret as the existing
adapters.

Fix: add `ENABLE_OPTIONS_TRADING` to `Settings` (boolean, default `False`);
add `AlpacaOptionChainAdapter.from_settings()` (wraps `StockOptionDataClient`);
update `RuntimeSupervisor.from_settings()` to wire both when
`settings.enable_options_trading is True`.

### Deployment Documentation — YELLOW (should-fix)
`DEPLOYMENT.md` contains zero references to any options env var. Operators who
read the template env file have no way to discover:
- `OPTION_DTE_MIN` (default 21)
- `OPTION_DTE_MAX` (default 60)
- `OPTION_DELTA_TARGET` (default 0.50)
- The forthcoming `ENABLE_OPTIONS_TRADING` flag

Fix: add an `# Options trading (off by default)` section to DEPLOYMENT.md.

### Migration Safety — GREEN
`migrations/012_add_option_orders.sql` uses `CREATE TABLE IF NOT EXISTS` and
`CREATE INDEX IF NOT EXISTS` throughout — fully idempotent. Migration runs before
supervisor starts (docker-compose `migrate` service runs first). No down-migration
is needed for v1 (the table is new with no dependencies from existing tables).

### Financial Safety — GREEN
- `ENABLE_LIVE_TRADING=false` gate enforced in `AlpacaExecutionAdapter._validate_live_safety()`
  which is called before any order submission. Options use `AlpacaBroker` (same class)
  for `submit_option_limit_entry` / `submit_option_market_exit` — same gate applies.
- Options entries only fire during the standard entry window (checked in
  `evaluate_cycle()` via `is_within_session_time()`).
- EOD flatten via `is_past_flatten_time()` — reuses `FLATTEN_TIME` setting, no new env
  var needed.
- Position sizing uses `risk_per_trade_pct` and `max_position_pct` — same parameters
  as equity orders.

### Audit Trail — GREEN
`option_dispatch.py` imports `AuditEvent` and appends one per dispatch (entry and
exit). State transitions logged: `pending_submit → submitted`, broker errors emit
`option_order_dispatch_failed`.

### Paper vs. Live Mode — GREEN
`AlpacaBroker` uses `resolve_alpaca_credentials(settings)` which respects
`TRADING_MODE`. The forthcoming `AlpacaOptionChainAdapter.from_settings()` will use
the same credential resolver — no separate option API keys needed.

### Advisory Lock / Concurrency — GREEN
Option dispatch runs in the single supervisor thread after equity dispatch. No new
goroutine or thread. Advisory lock semantics unchanged.

---

## Recommended Next Steps

**Before pushing/merging/deploying:**

1. **[Blocker]** Add `ENABLE_OPTIONS_TRADING: bool = False` to `Settings`; add
   `AlpacaOptionChainAdapter.from_settings(settings)`; update
   `RuntimeSupervisor.from_settings()` to wire options when the flag is true.

2. **[Should-fix]** Add options env var block to `DEPLOYMENT.md`.

**After merge, before live activation:**

3. Run on paper for at least one full session to confirm chain fetch succeeds,
   contracts are selected within DTE/delta bounds, and EOD flatten fires correctly.
4. Set `ENABLE_OPTIONS_TRADING=true` in the paper env file only. Confirm no option
   orders fire during the test without meeting the breakout condition.
5. When satisfied, set `ENABLE_OPTIONS_TRADING=true` in the live env file.
