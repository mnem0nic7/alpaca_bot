---
title: Multi-Period Strategy Report CLI
date: 2026-05-26
status: approved
---

# Multi-Period Strategy Report CLI

## Background

`alpaca-bot-session-eval` provides good single-day diagnostics but requires running
manually once per day to build a picture. No tool currently answers: "which of our 15+
active strategies are profitable over the last 30 days, and what is the option strategy's
premium retention rate?"

The short-put strategy lost -$21,079 over 9 trading days (May 2026). That loss was
discovered by debugging operational bugs, not by running a report. This tool closes that gap.

## Goal

Build `alpaca-bot-strategy-report`: a read-only CLI that aggregates multi-day equity and
option trade history, breaks results down by strategy name, computes option premium
retention, generates a daily P&L sparkline, and optionally exports raw data as CSV.

## CLI Interface

```
alpaca-bot-strategy-report [--days N | --since YYYY-MM-DD] [--until YYYY-MM-DD]
                            [--mode paper|live]
                            [--strategy-version VERSION]
                            [--csv-dir PATH]
```

Defaults:
- `--days 30` (overridden by `--since`)
- `--mode paper`
- `--strategy-version` from `STRATEGY_VERSION` env var
- `--until` today (ET)

## Output

### Section 1 — Header
```
Strategy Report — 2026-04-27 to 2026-05-26  [paper / v1-breakout]
═══════════════════════════════════════════════════════════════
Period: 30 days   Equity trades: 47   Option contracts: 12   Total P&L: -$18,412
```

### Section 2 — Equity Strategy Table
```
 Strategy          Trades  Win%   P&L       Prof.Fac  Expect%  AvgHold
 ─────────────────────────────────────────────────────────────────────
 breakout              18  56%   +$1,240     1.8      +0.18%    42min
 bear_orb               8  38%   -$620       0.7      -0.08%    31min
 gap_and_go             6  67%   +$890       2.1      +0.22%    55min
 ...
```

### Section 3 — Option Premium Table
```
 Underlying  Strategy   Contracts  Collected    Close Cost   Net P&L    Retention
 ──────────────────────────────────────────────────────────────────────────────────
 ALHC        bear_orb        5     +$210        $890         -$680        -324%
 AMLX        bear_orb        3     +$180        $330         -$150         -83%
 ...
 TOTAL                       8     +$390        $1,220       -$830        -213%
```

### Section 4 — Daily P&L Sparkline (last 14 trading days)
```
 Daily P&L (last 14 days)
 ▁▃▅▇▃▁▂▁▁▃▅▇▂▁  [range: -$1,240 to +$620]
 04/28                              05/26
```

### Section 5 — Operational Health
```
 Operational Health (period)
 ─────────────────────────
 Total cycles:       2,847
 Cycle errors:           3
 Dispatch failures:      7
 Skipped exits (OCC):  412     ← options-market-closed guard fired
 Stale exits skipped:   189    ← stale OCC positions skipped
```

## Architecture

### Files to Create / Modify

| Action  | File                                                     | Purpose                              |
|---------|----------------------------------------------------------|--------------------------------------|
| Create  | `src/alpaca_bot/admin/strategy_report_cli.py`            | CLI entrypoint + render + compute     |
| Modify  | `src/alpaca_bot/storage/repositories.py`                 | 2 new query methods                  |
| Modify  | `pyproject.toml`                                         | Register `alpaca-bot-strategy-report` |
| Create  | `tests/unit/test_strategy_report.py`                     | Unit tests for compute functions      |

### New Repository Methods

**`OrderStore.list_closed_trade_records`**

Returns one dict per closed equity round-trip in a date range. Extends the correlated
subquery pattern from `list_closed_trades` but accepts `since_date`/`until_date` instead
of a single `session_date`.

```python
def list_closed_trade_records(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    since_date: date,
    until_date: date,
    strategy_name: str | None = None,
    market_timezone: str = "America/New_York",
) -> list[dict]:
    ...
```

Each returned dict:
```python
{
    "symbol": str,
    "strategy_name": str,
    "qty": float,
    "entry_price": float,
    "exit_price": float,
    "entry_time": datetime,
    "exit_time": datetime,
    "pnl": float,           # (exit_price - entry_price) * qty
    "hold_seconds": float,
}
```

**`OptionOrderStore.list_closed_option_trade_records`**

Returns one dict per closed option position (sell + matched buy-to-close) in a date
range. Based on the correlated-subquery pattern in `list_trade_pnl_by_strategy`.

```python
def list_closed_option_trade_records(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    since_date: date,
    until_date: date,
    market_timezone: str = "America/New_York",
) -> list[dict]:
    ...
```

Each returned dict:
```python
{
    "occ_symbol": str,
    "underlying": str,
    "strategy_name": str,
    "qty": int,
    "premium_collected": float,   # sell_fill * qty * 100
    "close_cost": float,          # buy_fill * qty * 100
    "pnl": float,                 # premium_collected - close_cost
    "opened_at": datetime,
    "closed_at": datetime,
}
```

Only closed round-trips (rows where a matching buy fill exists) are returned.

### Pure Compute Functions

All compute functions are pure (no I/O) so they are testable without a database.

```python
@dataclass(frozen=True)
class EquityStrategyStats:
    strategy_name: str
    trades: int
    winning_trades: int
    total_pnl: float
    profit_factor: float | None
    expectancy_pct: float | None
    avg_hold_minutes: float | None

@dataclass(frozen=True)
class OptionStrategyStats:
    underlying: str
    strategy_name: str
    contracts: int
    premium_collected: float
    close_cost: float
    net_pnl: float
    retention_pct: float    # net_pnl / premium_collected * 100

def compute_equity_stats(records: list[dict]) -> list[EquityStrategyStats]:
    """Group by strategy_name, compute per-strategy metrics."""

def compute_option_stats(records: list[dict]) -> list[OptionStrategyStats]:
    """Group by (underlying, strategy_name), compute premium retention."""

def compute_daily_pnl(
    equity_records: list[dict],
    option_records: list[dict],
    market_timezone: str,
) -> dict[date, float]:
    """Sum equity + option P&L by exit date."""
```

### CSV Export

When `--csv-dir PATH` is passed, write three files:
- `{csv_dir}/equity_trades.csv` — one row per closed equity trade
- `{csv_dir}/option_trades.csv` — one row per closed option round-trip
- `{csv_dir}/daily_pnl.csv` — one row per trading day

Column headers match the dict keys from the repo methods above.

### Operational Health Counts

Queried from `audit_events` using the existing `AuditEventStore.list_by_event_types`.
Counts for the period:

| Label                  | Event types queried                                      |
|------------------------|----------------------------------------------------------|
| Total cycles           | `supervisor_cycle`                                       |
| Cycle errors           | `supervisor_cycle_error`, `strategy_cycle_error`         |
| Dispatch failures      | `order_dispatch_failed`, `option_order_dispatch_failed`  |
| Skipped exits (OCC)    | `cycle_intent_skipped` with `reason=options_market_closed` |
| Stale exits skipped    | `stale_positions_detected` with `skipped_exit_option_count > 0` |

For all counts use `COUNT(*)` via `len(list_by_event_types(...))` with `limit=10000`.
The sparkline counts are cosmetic health metrics, not financial — an approximate count
is acceptable.

## Testing

Tests in `tests/unit/test_strategy_report.py`. All test pure functions only.

```python
def test_compute_equity_stats_single_strategy():
    records = [
        {"symbol": "AAPL", "strategy_name": "breakout", "qty": 10,
         "entry_price": 100.0, "exit_price": 102.0,
         "entry_time": ..., "exit_time": ..., "pnl": 20.0, "hold_seconds": 1800},
        {"symbol": "MSFT", "strategy_name": "breakout", "qty": 5,
         "entry_price": 200.0, "exit_price": 198.0,
         "entry_time": ..., "exit_time": ..., "pnl": -10.0, "hold_seconds": 900},
    ]
    stats = compute_equity_stats(records)
    assert len(stats) == 1
    s = stats[0]
    assert s.trades == 2
    assert s.winning_trades == 1
    assert s.total_pnl == 10.0

def test_compute_option_stats_retention():
    records = [
        {"occ_symbol": "ALHC260618P00017500", "underlying": "ALHC",
         "strategy_name": "bear_orb", "qty": 5,
         "premium_collected": 210.0, "close_cost": 890.0,
         "pnl": -680.0, "opened_at": ..., "closed_at": ...},
    ]
    stats = compute_option_stats(records)
    assert len(stats) == 1
    assert stats[0].retention_pct == pytest.approx(-323.8, abs=0.2)

def test_compute_daily_pnl_groups_by_date():
    tz = "America/New_York"
    equity = [{"exit_time": datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
               "pnl": 100.0, "strategy_name": "breakout", ...}]
    option = [{"closed_at": datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc),
               "pnl": -50.0, "strategy_name": "bear_orb", ...}]
    daily = compute_daily_pnl(equity, option, tz)
    assert daily[date(2026, 5, 26)] == pytest.approx(50.0)

def test_compute_equity_stats_no_trades():
    stats = compute_equity_stats([])
    assert stats == []
```

## Safety

- No new env vars. All new env var reads use existing `Settings` fields.
- No order submission, no broker API calls. Read-only Postgres queries only.
- Identical behaviour in `TRADING_MODE=paper` and `TRADING_MODE=live` (both supported
  via `--mode` flag).
- `ENABLE_LIVE_TRADING=false` is unaffected — this tool never submits orders.
- No change to `evaluate_cycle()` or any runtime path.
- No migration needed: queries use existing tables and columns.
