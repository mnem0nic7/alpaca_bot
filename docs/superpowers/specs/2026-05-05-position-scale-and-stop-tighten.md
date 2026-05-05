# Position Scale-Up and Stop Tighten — Spec

**Date:** 2026-05-05

## Problem

Two related configuration gaps limit the strategy's effectiveness:

1. **Stop loss too wide.** `ATR_STOP_MULTIPLIER = 1.5` places stops 1.5× the average true range below entry. This is wider than necessary, increasing per-trade risk and reducing the number of viable positions.

2. **Position count too low.** `MAX_OPEN_POSITIONS = 3` caps the portfolio at 3 concurrent trades. The watchlist (managed via the `symbol_watchlist` Postgres table) may contain up to 20 high-confidence symbols; the current cap prevents the strategy from deploying capital across all of them.

## Fix

Recalibrate four env-var defaults to support 20 concurrent positions with 1.0× ATR stops:

| Parameter | Current | New |
|---|---|---|
| `ATR_STOP_MULTIPLIER` | 1.5 | 1.0 |
| `MAX_OPEN_POSITIONS` | 3 | 20 |
| `MAX_POSITION_PCT` | 0.05 (5%) | 0.015 (1.5%) |
| `MAX_PORTFOLIO_EXPOSURE_PCT` | 0.15 (15%) | 0.30 (30%) |

Unchanged: `RISK_PER_TRADE_PCT = 0.0025 (0.25%)`, `DAILY_LOSS_LIMIT_PCT = 0.01 (1%)`.

## Risk Analysis

With these values on a $100k portfolio:

- **Per-trade stop-out loss:** max 0.25% = $250 (unchanged)
- **Max individual position size:** 1.5% = $1,500
- **Max total exposure:** 30% = $30,000 across all 20 positions
- **Daily halt trigger:** 1% = $1,000, fired after roughly 4 concurrent stop-outs

The existing Settings validation (`MAX_POSITION_PCT ≤ MAX_PORTFOLIO_EXPOSURE_PCT`) passes: 1.5% ≤ 30%.

The tighter 1.0× ATR stop reduces the distance to the stop price by ~33%. With `RISK_PER_TRADE_PCT` fixed, this naturally increases position sizing (more shares for the same dollar risk) until `MAX_POSITION_PCT` caps it. The combined effect: faster stops, slightly larger positions, same per-trade dollar risk.

## Scope

**Two files only:**

1. `src/alpaca_bot/config/__init__.py`
   - `atr_stop_multiplier` dataclass default: `1.5` → `1.0`
   - `from_env()` default for `ATR_STOP_MULTIPLIER`: `"1.5"` → `"1.0"`
   - `from_env()` default for `MAX_OPEN_POSITIONS`: `"3"` → `"20"`
   - `from_env()` default for `MAX_POSITION_PCT`: `"0.05"` → `"0.015"`
   - `MAX_PORTFOLIO_EXPOSURE_PCT` dataclass default: `0.15` → `0.30`

2. `DEPLOYMENT.md`
   - Update env var template values and inline commentary for all four parameters

**No migrations, no new env vars, no behavioral code changes.**

## Deployment Note

The running production env file (`/etc/alpaca_bot/alpaca-bot.env`) overrides Python defaults. After deploying the code change, the operator must manually update that file and redeploy for the new values to take effect in production.

## Tests

- Any test that constructs `Settings` without using `_base_env()` and asserts on old default values for these four parameters must be updated.
- `_base_env()` sets all values explicitly, so most tests are unaffected.
- Add or update one test asserting the new defaults are parsed correctly from a minimal env (no explicit values for these four keys).
