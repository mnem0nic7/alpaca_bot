# Spec: Block Entry When ATR Unavailable

## Problem

`atr_stop_buffer()` in `risk/atr.py` silently falls back to `fallback_anchor * fallback_pct`
(BREAKOUT_STOP_BUFFER_PCT=0.001, i.e. 0.1%) when `calculate_atr()` returns `None` due to
insufficient daily bars. On a ~$100 stock this produces a stop only $0.10 below the breakout
level — trivially breached by normal intraday noise, guaranteeing a loss on entry.

Observed in production: SHAK position opened at $102.41 with initial stop at $102.31 (0.10%,
$0.10 stop distance). A proper ATR-based stop (1.5× daily ATR on a $100 stock) should be
$1.50–$4.50+ wide.

The supervisor fetches `max(daily_sma_period * 3, 60, ...)` days of daily bars — at least 60
calendar days, so ~42 trading days. ATR requires only `period + 1 = 15` bars. Returning `None`
signals a genuine data failure, not a routine edge case.

## Decision

**Block entry in all 5 strategies when ATR is unavailable.** Do not use the fallback pct as a
live-trading stop size. The fallback remains in `atr_stop_buffer()` for backward compatibility
and for `startup_recovery.py` which uses it in a different context (recovery stop sizing for
an existing position, not entry sizing).

## Scope

- All 5 entry strategies: breakout, momentum, orb, ema_pullback, high_watermark
- No changes to `risk/atr.py` (preserves backward compatibility)
- No changes to `startup_recovery.py` (separate concern, out of scope)
- No config changes (BREAKOUT_STOP_BUFFER_PCT stays for startup_recovery)

## Constraints

- `evaluate_cycle()` must remain a pure function (no I/O introduced)
- No new env vars
- All existing tests must pass after updating the 5 fallback tests
