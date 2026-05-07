# After-Hours Session Summary & Deployment Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-session P&L breakdown section to the daily summary email and document all extended-hours env vars in DEPLOYMENT.md.

**Architecture:** Two independent changes. Task 1 adds a "Session Breakdown" section to `_build_body()` in `daily_summary.py` by calling `detect_session_type()` on each trade's `exit_time`. Task 2 is a DEPLOYMENT.md edit only. No new settings, no migrations, no schema changes.

**Tech Stack:** Python 3.11, pytest. `detect_session_type(datetime, Settings) → SessionType` already exists in `alpaca_bot.strategy.session`.

---

## File Map

| File | Change |
|------|--------|
| `src/alpaca_bot/runtime/daily_summary.py` | Add `_SESSION_LABELS` dict + session breakdown block in `_build_body()` |
| `tests/unit/test_daily_summary.py` | Add `make_extended_settings()`, `_trade_with_exit_time()`, `TestSessionBreakdown` class (4 tests) |
| `DEPLOYMENT.md` | Insert extended-hours settings block after `INTRADAY_CONSECUTIVE_LOSS_GATE=0` |

---

## Context: The existing code

**`src/alpaca_bot/runtime/daily_summary.py`** currently:
- Imports only `Settings` from `alpaca_bot.config`
- `_build_body()` takes `settings`, `session_date`, `trades`, `total_pnl`, `open_positions`, `daily_loss_limit_breached`
- Each trade dict has: `symbol`, `strategy_name`, `entry_fill`, `exit_fill`, `qty`, `exit_time` (timezone-aware datetime), `entry_time`
- Already has `_is_win(trade)`, `_trade_pnl(trade)`, `_fmt_pnl(v)` helpers
- `_build_body()` has sections in this order: P&L → Strategy Breakdown → Positions at Close → Risk

**`alpaca_bot.strategy.session`** exports:
```python
class SessionType(enum.Enum):
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"

def detect_session_type(timestamp: datetime, settings: Settings) -> SessionType: ...
```

**Existing `_trade()` helper in tests** does not include `exit_time`. Add a wrapper; do not modify `_trade()`.

**`DEPLOYMENT.md` insertion point:** After line containing `INTRADAY_CONSECUTIVE_LOSS_GATE=0`, before `STOP_LIMIT_BUFFER_PCT=0.001`. The block follows the same commented-block style as the options trading block just below the entry window settings.

**EDT offsets for test datetimes** (SESSION_DATE = 2026-04-28, EDT = UTC-4):
- PRE_MARKET 7am EDT = `datetime(2026, 4, 28, 11, 0, tzinfo=timezone.utc)`
- REGULAR 2pm EDT = `datetime(2026, 4, 28, 18, 0, tzinfo=timezone.utc)`
- AFTER_HOURS 5pm EDT = `datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)`
- CLOSED 9pm EDT = `datetime(2026, 4, 29, 1, 0, tzinfo=timezone.utc)`

---

## Task 1: Session Breakdown in Daily Summary

**Files:**
- Modify: `src/alpaca_bot/runtime/daily_summary.py`
- Modify: `tests/unit/test_daily_summary.py`

---

- [ ] **Step 1: Write the four failing tests**

Add `make_extended_settings()`, `_trade_with_exit_time()`, and `TestSessionBreakdown` to `tests/unit/test_daily_summary.py`. Insert after the existing `_position()` helper (around line 109, before the `TestBuildDailySummary` class):

```python
def make_extended_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://test/db",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.01",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
            "EXTENDED_HOURS_ENABLED": "true",
        }
    )


def _trade_with_exit_time(exit_time: datetime, **kwargs) -> dict:
    """Wrap _trade() and attach an exit_time datetime."""
    t = _trade(**kwargs)
    t["exit_time"] = exit_time
    return t


# EDT offsets (SESSION_DATE = 2026-04-28, EDT = UTC-4)
_REGULAR_EXIT = datetime(2026, 4, 28, 18, 0, tzinfo=timezone.utc)    # 2pm EDT
_AFTERHOURS_EXIT = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc) # 5pm EDT
_PREMARKET_EXIT = datetime(2026, 4, 28, 11, 0, tzinfo=timezone.utc)  # 7am EDT
_CLOSED_EXIT = datetime(2026, 4, 29, 1, 0, tzinfo=timezone.utc)      # 9pm EDT (past 8pm close)


class TestSessionBreakdown:
    def test_session_breakdown_omitted_when_extended_hours_disabled(self):
        """No Session Breakdown section when extended_hours_enabled=False."""
        trades = [_trade_with_exit_time(_REGULAR_EXIT)]
        settings = make_settings()  # extended_hours_enabled=False
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(trades=trades, pnl=50.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "Session Breakdown" not in body

    def test_session_breakdown_shows_regular_and_afterhours(self):
        """Two rows (Regular + After-Hours) with correct trade counts and PnL."""
        trades = [
            _trade_with_exit_time(_REGULAR_EXIT, entry_fill=100.0, exit_fill=110.0, qty=2),
            _trade_with_exit_time(_AFTERHOURS_EXIT, entry_fill=100.0, exit_fill=95.0, qty=1),
        ]
        settings = make_extended_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(trades=trades, pnl=15.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "Session Breakdown" in body
        assert "Regular" in body
        assert "$20.00" in body       # regular PnL: (110-100)*2 = $20
        assert "After-Hours" in body
        assert "-$5.00" in body       # after-hours PnL: (95-100)*1 = -$5

    def test_session_breakdown_omitted_with_no_trades(self):
        """No Session Breakdown section when there are zero trades."""
        settings = make_extended_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(trades=[], pnl=0.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "Session Breakdown" not in body

    def test_session_breakdown_skips_closed_session_trades(self):
        """Trade with exit_time in the CLOSED window is silently skipped."""
        trades = [
            _trade_with_exit_time(_REGULAR_EXIT),
            _trade_with_exit_time(_CLOSED_EXIT),   # 9pm EDT — CLOSED
        ]
        settings = make_extended_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(trades=trades, pnl=50.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "Session Breakdown" in body
        assert "Regular     : 1 trades" in body
        # CLOSED trade must not create a row
        assert "Closed" not in body
        assert "CLOSED" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_daily_summary.py::TestSessionBreakdown -v
```

Expected: 4 FAILED — `test_session_breakdown_omitted_when_extended_hours_disabled` will pass (no section yet), the others will fail on `assert "Session Breakdown" in body`.

Actually all 4 may fail or pass differently — what matters is that the implementation is absent.

- [ ] **Step 3: Implement the session breakdown in `daily_summary.py`**

**Add imports** at the top of `src/alpaca_bot/runtime/daily_summary.py` (after the existing import lines):

```python
from alpaca_bot.strategy.session import SessionType, detect_session_type
```

**Add `_SESSION_LABELS` constant** after the imports block:

```python
_SESSION_LABELS: dict[SessionType, str] = {
    SessionType.PRE_MARKET: "Pre-Market",
    SessionType.REGULAR: "Regular",
    SessionType.AFTER_HOURS: "After-Hours",
}
```

**Insert the session breakdown block** in `_build_body()`, immediately after the closing `lines.append("")` of the existing `--- Strategy Breakdown ---` block and before the `# --- Positions at Close ---` comment:

```python
    # --- Session Breakdown (extended hours only) ---
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

The full updated `_build_body()` function for reference:

```python
def _build_body(
    *,
    settings: Settings,
    session_date: date,
    trades: list[dict],
    total_pnl: float,
    open_positions: list,
    daily_loss_limit_breached: bool,
) -> str:
    lines: list[str] = []

    lines.append(
        f"Session: {session_date}  "
        f"Mode: {settings.trading_mode.value}  "
        f"Strategy: {settings.strategy_version}"
    )
    lines.append("")

    # --- P&L ---
    lines.append("--- P&L ---")
    lines.append(f"Realized PnL : {_fmt_pnl(total_pnl)}")
    lines.append(f"Trades       : {len(trades)}")
    if trades:
        wins = sum(1 for t in trades if _is_win(t))
        losses = len(trades) - wins
        win_rate = wins / len(trades)
        lines.append(f"Win rate     : {win_rate:.1%}  ({wins}W / {losses}L)")
    lines.append("")

    # --- Strategy Breakdown ---
    if trades:
        lines.append("--- Strategy Breakdown ---")
        by_strategy: dict[str, list[dict]] = {}
        for t in trades:
            name = t.get("strategy_name") or "breakout"
            by_strategy.setdefault(name, []).append(t)
        for name, group in by_strategy.items():
            strat_pnl = sum(_trade_pnl(t) for t in group)
            lines.append(f"{name:<12}: {len(group)} trades  {_fmt_pnl(strat_pnl)} PnL")
        lines.append("")

    # --- Session Breakdown (extended hours only) ---
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

    # --- Positions at Close ---
    lines.append("--- Positions at Close ---")
    lines.append(f"Open positions: {len(open_positions)}")
    for pos in open_positions:
        symbol = getattr(pos, "symbol", "?")
        qty = getattr(pos, "quantity", "?")
        entry = getattr(pos, "entry_price", 0.0)
        stop = getattr(pos, "stop_price", 0.0)
        lines.append(f"  {symbol} x{qty} @ {entry:.2f} (stop {stop:.2f})")
    lines.append("")

    # --- Risk ---
    lines.append("--- Risk ---")
    lines.append(
        f"Daily loss limit breached: {'Yes' if daily_loss_limit_breached else 'No'}"
    )

    return "\n".join(lines)
```

- [ ] **Step 4: Run all four new tests**

```bash
pytest tests/unit/test_daily_summary.py::TestSessionBreakdown -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Run the full test suite**

```bash
pytest tests/unit/test_daily_summary.py -v
```

Expected: all existing tests still pass (the new import and block do not touch existing code paths when `extended_hours_enabled=False`).

- [ ] **Step 6: Run full suite to check for regressions**

```bash
pytest
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/runtime/daily_summary.py tests/unit/test_daily_summary.py
git commit -m "feat: add session breakdown to daily summary when extended hours enabled"
```

---

## Task 2: Document Extended-Hours Settings in DEPLOYMENT.md

**Files:**
- Modify: `DEPLOYMENT.md`

---

- [ ] **Step 1: Insert the extended-hours settings block**

In `DEPLOYMENT.md`, find the line `INTRADAY_CONSECUTIVE_LOSS_GATE=0` and insert the following block immediately after it, before `STOP_LIMIT_BUFFER_PCT=0.001`:

```
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

The result should look like:

```
# Disable entries after N consecutive losing trades (0 = disabled, safe default)
INTRADAY_CONSECUTIVE_LOSS_GATE=0
# Extended-hours trading (pre-market 4am–9:20am ET and after-hours 4:05pm–7:30pm ET)
# EXTENDED_HOURS_ENABLED=false
# PRE_MARKET_ENTRY_WINDOW_START=04:00
# PRE_MARKET_ENTRY_WINDOW_END=09:20
# AFTER_HOURS_ENTRY_WINDOW_START=16:05
# AFTER_HOURS_ENTRY_WINDOW_END=19:30
# EXTENDED_HOURS_FLATTEN_TIME=19:45
# EXTENDED_HOURS_LIMIT_OFFSET_PCT=0.001   # limit price slippage buffer vs. last trade price
# EXTENDED_HOURS_MAX_SPREAD_PCT=0.01      # max bid-ask spread as fraction of price (1% default)
STOP_LIMIT_BUFFER_PCT=0.001
```

- [ ] **Step 2: Run the test suite to confirm no regressions**

```bash
pytest
```

Expected: all tests pass (DEPLOYMENT.md is not tested directly).

- [ ] **Step 3: Commit**

```bash
git add DEPLOYMENT.md
git commit -m "docs: add extended-hours settings block to DEPLOYMENT.md env template"
```

---

## Self-Review

**Spec coverage:**
- ✓ Session breakdown omitted when `extended_hours_enabled=False` → `test_session_breakdown_omitted_when_extended_hours_disabled`
- ✓ REGULAR + AFTER_HOURS rows with correct counts and PnL → `test_session_breakdown_shows_regular_and_afterhours`
- ✓ Omitted with zero trades → `test_session_breakdown_omitted_with_no_trades`
- ✓ CLOSED-session trades silently skipped → `test_session_breakdown_skips_closed_session_trades`
- ✓ DEPLOYMENT.md block with all 7 settings → Task 2

**Placeholder scan:** None found. All code is complete.

**Type consistency:**
- `detect_session_type(exit_dt, settings)` — `exit_dt` is `datetime`, `settings` is `Settings` ✓
- `_SESSION_LABELS.get(stype)` returns `str | None` — `None` handled with `continue` ✓
- `_trade_pnl(t)` reused unchanged ✓
