# After-Hours Session Summary & Deployment Docs Design

> **Status:** Spec — ready for implementation planning

**Goal:** Surface per-session P&L breakdown in the daily summary email and document all extended-hours settings in DEPLOYMENT.md.

**Architecture:** Two independent changes. No new settings, no migrations, no new files (except this spec). Pure read path for the summary change — zero risk to order submission or position management.

---

## Context

After-hours trading is live (`EXTENDED_HOURS_ENABLED=true`). The system trades both PRE_MARKET (4:00–9:20 AM ET) and AFTER_HOURS (4:05–7:30 PM ET) sessions alongside the regular session. The daily session summary already groups trades by strategy. With two or three session types active, operators have no way to see which session is producing profit and which is not.

Additionally, none of the 7 configurable extended-hours settings appear in `DEPLOYMENT.md`'s env-file template, so an operator enabling the feature has no reference for what they can tune.

---

## Change 1: Session Breakdown in Daily Summary

### Where

`src/alpaca_bot/runtime/daily_summary.py` — `_build_body()` function.

### What

Add a `--- Session Breakdown ---` section immediately after the existing `--- Strategy Breakdown ---` section. The section is only rendered when `settings.extended_hours_enabled` is `True` and there are any closed trades.

### How

Each trade dict already includes `exit_time` (a timezone-aware `datetime`). Call `detect_session_type(trade["exit_time"], settings)` to classify each trade. Group by the result. Render one line per session that has at least one trade.

Session label mapping:
- `SessionType.PRE_MARKET` → `"Pre-Market"`
- `SessionType.REGULAR` → `"Regular"`
- `SessionType.AFTER_HOURS` → `"After-Hours"`
- `SessionType.CLOSED` → skip (no entries should be in CLOSED session)

Line format (matching the strategy breakdown style):
```
Pre-Market  : 2 trades  $12.50 PnL
Regular     : 8 trades  $45.00 PnL
After-Hours : 3 trades  -$8.20 PnL
```

### Implementation sketch

```python
from alpaca_bot.strategy.session import SessionType, detect_session_type

_SESSION_LABELS = {
    SessionType.PRE_MARKET: "Pre-Market",
    SessionType.REGULAR: "Regular",
    SessionType.AFTER_HOURS: "After-Hours",
}

# Inside _build_body(), after the strategy breakdown block:
if settings.extended_hours_enabled and trades:
    by_session: dict[str, list[dict]] = {}
    for t in trades:
        exit_dt = t.get("exit_time")
        if exit_dt is None:
            continue
        stype = detect_session_type(exit_dt, settings)
        label = _SESSION_LABELS.get(stype)
        if label is None:
            continue  # CLOSED — skip
        by_session.setdefault(label, []).append(t)
    if by_session:
        lines.append("--- Session Breakdown ---")
        for label in ("Pre-Market", "Regular", "After-Hours"):
            group = by_session.get(label)
            if not group:
                continue
            spnl = sum(_trade_pnl(t) for t in group)
            lines.append(f"{label:<12}: {len(group)} trades  {_fmt_pnl(spnl)} PnL")
        lines.append("")
```

### Tests

New tests in `tests/unit/test_daily_summary.py`:

1. `test_session_breakdown_omitted_when_extended_hours_disabled` — regular-only settings, body must NOT contain "Session Breakdown"
2. `test_session_breakdown_shows_regular_and_afterhours` — extended_hours_enabled=True, trades with exit_times in REGULAR (e.g. 14:00 ET) and AFTER_HOURS (e.g. 17:00 ET), verify both rows appear with correct counts
3. `test_session_breakdown_omitted_with_no_trades` — extended hours enabled but zero trades, section must not appear
4. `test_session_breakdown_skips_closed_session_trades` — trade with exit_time at 21:00 ET (CLOSED), verify it is silently skipped and not shown in any row

---

## Change 2: DEPLOYMENT.md Extended-Hours Settings Block

### Where

`DEPLOYMENT.md` — the env-file template section. Insert after the `DAILY_LOSS_LIMIT_PCT` block and before the `STOP_LIMIT_BUFFER_PCT` line.

### What

```dotenv
# Extended-hours trading (pre-market 4am–9:20am ET and after-hours 4:05pm–7:30pm ET)
# EXTENDED_HOURS_ENABLED=false
# PRE_MARKET_ENTRY_WINDOW_START=04:00
# PRE_MARKET_ENTRY_WINDOW_END=09:20
# AFTER_HOURS_ENTRY_WINDOW_START=16:05
# AFTER_HOURS_ENTRY_WINDOW_END=19:30
# EXTENDED_HOURS_FLATTEN_TIME=19:45
# EXTENDED_HOURS_LIMIT_OFFSET_PCT=0.001   # limit price slippage buffer vs. last trade price
# EXTENDED_HOURS_MAX_SPREAD_PCT=0.01      # max bid-ask spread as fraction of price (1% default)
```

All values shown are the current defaults. Operators uncomment and adjust as needed.

---

## Constraints

- `detect_session_type` requires a timezone-aware datetime. The `exit_time` from `list_closed_trades` is timezone-aware (from `updated_at` in Postgres). No conversion needed.
- The session breakdown must not appear for regular-only deployments. Guard: `settings.extended_hours_enabled`.
- `_trade_pnl` already handles trades with null fills (returns 0.0). The session breakdown reuses it — no new null-handling needed.
- No new env vars, no migrations, no schema changes.

---

## Out of Scope

- Intraday digest session breakdown (runs during REGULAR only; after-hours trades haven't occurred yet)
- Per-symbol or per-session P&L in the dashboard (separate project)
- Storing session_type explicitly on order records
