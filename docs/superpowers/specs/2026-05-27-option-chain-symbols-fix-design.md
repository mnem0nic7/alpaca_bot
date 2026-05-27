---
title: Option Chain Symbol Decoupling Fix
date: 2026-05-27
status: approved
---

# Option Chain Symbol Decoupling Fix

## Problem Confirmed

Zero option `entry_intent_created` events since 2026-05-14 (13 days). Every supervisor cycle
logs: **"option chain fetch timed out after 45s, using partial results"** and
**"Connection reset by peer"** — Alpaca is dropping connections from the burst of
1003 simultaneous requests.

Root cause: `symbols_to_fetch` in `supervisor.py` is derived from
`intraday_bars_by_symbol.items()`, which covers **all 1003 symbols** in the `symbol_watchlist`
table. With `max_workers=5` and a 45-second deadline, only ~258 fast-responding large-cap
symbols return chains before the timeout. The small-cap stocks that bear strategies target
(ALHC, AMLX, AROC, BCRX, BFLY, CMG, CNK, ASAN, ATEC, etc.) are never reached.

The bear strategy evaluators (`bear_orb`, `bear_breakdown`, etc.) can only produce an
`EntrySignal` when the underlying symbol's option chain is present in
`option_chains_by_symbol`. Without chains → no entries.

The volume filter `OPTION_CHAIN_MIN_TOTAL_VOLUME=50000` was intended to limit scope, but it
filters by intraday bar volume (sum of 15-min bars ≥ 50,000 shares), not option-chain
suitability. Large-caps trivially pass; targeted bear-strategy names often don't.

All five bugs from the 2026-05-26 spec have been fixed and deployed. This spec addresses
the one remaining root cause of options silence.

---

## Scope

One change: introduce `OPTION_CHAIN_SYMBOLS` as a dedicated config for which underlying
symbols to fetch option chains for, completely decoupled from the equity watchlist.

**Out of scope:** option strategy signal tuning, contract selection parameters, expanding the
bear strategy universe.

---

## Design

### New Config Key: `OPTION_CHAIN_SYMBOLS`

Add `option_chain_symbols: list[str]` to `Settings` in `src/alpaca_bot/config/__init__.py`.

- Parsed from env var `OPTION_CHAIN_SYMBOLS` as a comma-separated string
  (e.g., `OPTION_CHAIN_SYMBOLS=ALHC,AMLX,AROC,BCRX,BFLY,CMG,CNK,ASAN,ATEC`).
- Default: empty list `[]` — treated as "no option symbols configured" (disables option chain
  fetch and option strategies, same as `ENABLE_OPTIONS_TRADING=false`).
- Validation: log a warning if `ENABLE_OPTIONS_TRADING=true` but `OPTION_CHAIN_SYMBOLS` is
  empty, so the misconfiguration is visible.

### Supervisor Change

In `supervisor.py`, replace:

```python
symbols_to_fetch = [
    sym for sym, bars in intraday_bars_by_symbol.items()
    if min_vol == 0 or sum(b.volume for b in bars) >= min_vol
]
```

with:

```python
configured = set(s.upper() for s in self.settings.option_chain_symbols)
symbols_to_fetch = [sym for sym in intraday_bars_by_symbol if sym in configured]
```

`OPTION_CHAIN_MIN_TOTAL_VOLUME` becomes irrelevant for symbol selection (it was a workaround
for the missing config). It can be left in `Settings` for backwards compatibility but is no
longer used in `symbols_to_fetch` construction. Document this in a comment.

### Worker Pool

Increase `max_workers` from 5 to 10. With a focused list of 9–15 symbols, 5 workers was fine,
but 10 is still small and provides headroom if the list grows.

The 45-second timeout remains. With ≤15 symbols and 10 workers, all chains complete well
within the window; the timeout is a safety net.

### Production Env Update

Add to `/etc/alpaca_bot/alpaca-bot.env`:

```
OPTION_CHAIN_SYMBOLS=ALHC,AMLX,AROC,BCRX,BFLY,CMG,CNK,ASAN,ATEC
```

These 9 symbols are the underlyings from the last session that had option positions (confirmed
from `option_orders` table). The list can be expanded as strategy expands.

---

## Architecture Decisions

**Why a separate config key rather than relying on the volume filter?**
The equity watchlist is a broad scan for momentum candidates across 1000+ tickers. Option
strategies are tactical overlays on a small set of named underlyings. These are fundamentally
different concerns with different lifecycles — the equity list changes daily via the watchlist
table; the option underlying list should be curated and stable.

**Why not use `settings.symbols` (the static equity SYMBOLS env var)?**
`SYMBOLS` drives equity breakout entry candidates. Option underlyings overlap but are not
identical — `SYMBOLS` may include symbols with poor option liquidity, and option underlyings
may not be in `SYMBOLS`. Coupling them creates confusion.

**What about `OPTION_CHAIN_MIN_TOTAL_VOLUME`?**
With `OPTION_CHAIN_SYMBOLS` controlling the fetch list, volume filtering is no longer needed at
the symbol-selection layer. The existing option contract selectors
(`option_selector.py`) already filter individual contracts by strike, delta, DTE, bid/ask
spread, and OI. Leave `OPTION_CHAIN_MIN_TOTAL_VOLUME` in `Settings` but stop using it in
`symbols_to_fetch`. A follow-up spec can deprecate it.

---

## Testing

**Test 1:** `Settings.from_env` with `OPTION_CHAIN_SYMBOLS=ALHC,AMLX` → `settings.option_chain_symbols == ["ALHC", "AMLX"]`.

**Test 2:** `Settings.from_env` with `OPTION_CHAIN_SYMBOLS` not set → `settings.option_chain_symbols == []`.

**Test 3:** Supervisor `_build_symbols_to_fetch` (extracted helper) with:
- `intraday_bars_by_symbol` covering 100 symbols including `ALHC` and `AMLX`
- `settings.option_chain_symbols = ["ALHC", "AMLX"]`
- → assert only `["ALHC", "AMLX"]` (or subset present in bars) returned

**Test 4:** Supervisor option chain block with `option_chain_symbols = []` → assert
`option_chains_by_symbol` is empty and no option strategies are appended.

All tests use the project's fake-callable DI pattern. No mocks.

---

## Rollback

Config-only change (plus two lines of Python). If the new config causes unexpected behavior:
1. Remove `OPTION_CHAIN_SYMBOLS` from env → `option_chain_symbols = []` → option strategies disabled (same as before the last option session).
2. No DB changes, no migration needed.

---

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/config/__init__.py` | Add `option_chain_symbols: list[str]` field with env parsing |
| `src/alpaca_bot/runtime/supervisor.py` | Replace `symbols_to_fetch` construction; increase `max_workers` to 10 |
| `/etc/alpaca_bot/alpaca-bot.env` | Add `OPTION_CHAIN_SYMBOLS=ALHC,AMLX,AROC,BCRX,BFLY,CMG,CNK,ASAN,ATEC` |
| `tests/unit/test_settings.py` | Add `option_chain_symbols` parse tests |
| `tests/unit/test_supervisor_option_chains.py` | Add symbol filtering and empty-list tests |
