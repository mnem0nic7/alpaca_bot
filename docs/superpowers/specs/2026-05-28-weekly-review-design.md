# Weekly Trade Review Design

## Goal

Two-part deliverable:

1. **Immediate analysis** — run existing CLI tools (`alpaca-bot-strategy-report`, `alpaca-bot-session-eval`, `alpaca-bot-funnel-report`) against the production database and interpret the last 7 days of trades from a data-engineer and expert-trader perspective.

2. **New `alpaca-bot-weekly-review` CLI** — a single command that produces a comprehensive weekly review covering all the gaps in the existing tooling: per-day P&L table, symbol attribution, trade quality ratio, signal funnel summary, and operational health.

---

## Problem Statement

Running a thorough weekly review currently requires three separate commands, and still leaves gaps:

| Gap | Missing from existing tools |
|-----|-----------------------------|
| Per-day P&L table | `strategy-report` shows a sparkline only — no date rows with P&L, trade count, win% |
| Symbol attribution | No tool shows which symbols drove P&L for the week |
| Trade quality ratio | No tool shows avg winner $ vs. avg loser $ (win/loss dollar ratio) |
| Combined single command | Must run strategy-report + session-eval + funnel-report separately |

---

## Architecture

### Files Created

| File | Purpose |
|------|---------|
| `src/alpaca_bot/admin/weekly_review_cli.py` | New CLI (~350 LOC) |
| `tests/unit/test_weekly_review_cli.py` | Unit tests |

### Files Modified

| File | Change |
|------|--------|
| `pyproject.toml` | Register `alpaca-bot-weekly-review` entry point |

---

## CLI Interface

```
alpaca-bot-weekly-review [--days N] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                          [--mode paper|live] [--strategy-version VERSION]
                          [--csv-dir PATH]
```

Defaults: `--days 7`, `--mode paper`, strategy version from `STRATEGY_VERSION` env var.

`--csv-dir` exports three files: `equity_trades.csv`, `option_trades.csv`, `daily_pnl.csv` (same schema as strategy-report export).

---

## Output Sections

### Section 1 — Header / Period Summary

```
Weekly Review — 2026-05-21 to 2026-05-27  [paper / v1]
═══════════════════════════════════════════════════════
 Period: 7 days   Equity trades: 12   Option contracts: 4   Total P&L: +$423.50
 Win rate: 58%   Ann. Sharpe: 1.42   Profit factor: 1.87   Max DD: 1.2%
```

Metrics: total_trades, win_rate, annualized_sharpe, profit_factor, max_drawdown_pct — all computed by `report_from_records()`.

### Section 2 — Day-by-Day P&L Table (NEW)

```
 Daily Breakdown
 ──────────────────────────────────────────────────────────────────
 Date         Eq P&L    Opt P&L   Total P&L  Trades  Win%   Cumul
 2026-05-21  +$145.20   +$22.50  +$167.70       3   67%  +$167.70
 2026-05-22    -$44.10      —      -$44.10       2   0%   +$123.60
 ...
```

Source: `list_daily_pnl_breakdown()` + `list_closed_option_trade_records()` grouped by exit date.

Zero-trade days (weekends, holidays) are not shown — only dates where at least one trade closed.

### Section 3 — Per-Strategy Stats

```
 Equity Strategies
 ──────────────────────────────────────────────────────────────────────────────
 Strategy             Trades   Win%      P&L     PF  Expect%  AvgHold  AnnSharpe
 breakout                  9   56%  +$298.40  1.72   +0.31%     23min       1.18
 vwap_reversion            3   67%  +$125.10  2.41   +0.52%     18min       1.74
```

Reuses `compute_equity_stats()` from `strategy_report_cli.py`. Adds annualized Sharpe column (from `report_from_records()` per-strategy).

### Section 4 — Symbol Attribution (NEW)

```
 Symbol Attribution (equity)
 ──────────────────────────────────────────────────────
 Top 5 winners            P&L   Trades  Win%
 NVDA                 +$184.20       2  100%
 AAPL                  +$99.40       1  100%
 ...

 Bottom 5 losers          P&L   Trades  Win%
 TSLA                  -$88.30       2    0%
 META                  -$31.10       1    0%
 ...
```

Source: `list_symbol_pnl_for_period()` — returns one row per symbol with total_pnl, trade_count, win_count.

### Section 5 — Trade Quality (NEW)

```
 Trade Quality
 ──────────────────────────────────────────────────────
 Avg winner:   +$52.40   Avg loser:  -$28.10   Ratio: 1.86×
 Max winner:  +$144.20   Max loser:  -$44.10

 Exit breakdown:
   Stop wins:   2   Stop losses:   3
   EOD wins:    5   EOD losses:    1
   Target wins: 0   Target losses: 0

 Loser analysis: 3 stopped out, 1 held to EOD loss
```

The "loser analysis" line flags how many losing trades were cut by stop vs. held to EOD — a key trader signal for whether the strategy is respecting stops.

### Section 6 — Signal Funnel Summary

```
 Signal Funnel (last 7 days)
 ──────────────────────────────────────────────────────────────────
 Strategy             Eval  Signal  Filter  Sized  Accept  Rate
 breakout              240     130     120    119      45  18.8%
 vwap_reversion         80      20      18      8       3   3.8%
```

Reuses `DecisionLogStore.funnel_by_strategy()`. Acceptance rate = accepted / evaluated.

### Section 7 — Operational Health

```
 Operational Health
 ──────────────────────────────────────────────────────
 Total cycles:        56
 Cycle errors:         0
 Dispatch failures:    0
 Skipped exits (OCC):  8
 Stale exits skipped:  0
```

Reuses `AuditEventStore.list_by_event_types()`.

---

## No New Repository Methods

`list_closed_trade_records()` already returns `{symbol, strategy_name, pnl, entry_price, exit_price, entry_time, exit_time, hold_seconds}` for a date range. The daily breakdown and symbol attribution are computed by grouping these records in Python — the same pattern used by `compute_daily_pnl()` and `compute_equity_stats()` in `strategy_report_cli.py`.

This avoids adding methods to `repositories.py` and keeps all new logic in `weekly_review_cli.py`.

---

## Data Flow

```
alpaca-bot-weekly-review
    ↓
Settings.from_env() → DATABASE_URL, STRATEGY_VERSION
    ↓
OrderStore: list_closed_trade_records()          → equity trades (raw dicts)
OptionOrderRepository: list_closed_option...()   → option trades (raw dicts)
DecisionLogStore: funnel_by_strategy()           → funnel counts
AuditEventStore: list_by_event_types()           → operational health
    ↓
report_from_records()                            → BacktestReport (aggregate stats)
compute_equity_stats()                           → per-strategy stats
_group_by_date(equity_records, option_records)   → per-day table (Python grouping)
_group_by_symbol(equity_records)                 → symbol attribution (Python grouping)
    ↓
Seven sections printed to stdout
```

---

## Error Handling

- **No trades in period**: prints "No closed trades in period" for Sections 1–5; still prints funnel and health.
- **No decision_log rows**: Section 6 shows "(no decision_log data for period)".
- **Missing equity baseline**: omit cumulative column in Section 2 (same graceful fallback as session-eval).
- **No option trades**: Section 6 option row shows "—" for all values.
- Connection errors propagate naturally to stderr (read-only CLI, no retry needed).

---

## Testing

### `tests/unit/test_weekly_review_cli.py`

| Test | What it verifies |
|------|-----------------|
| `test_list_daily_pnl_breakdown_groups_by_date` | 4 trades across 2 dates → 2 result rows with correct trade_count and total_pnl |
| `test_list_symbol_pnl_for_period_sorts_descending` | 3 symbols → result sorted by total_pnl desc |
| `test_weekly_review_no_trades_prints_no_data` | zero-trade path: no crash, "No closed trades" printed |
| `test_trade_quality_win_loss_ratio` | avg winner / avg loser ratio computed correctly |
| `test_symbol_attribution_top_bottom_5` | top-5 / bottom-5 split when more than 10 symbols |
| `test_loser_analysis_counts_stop_vs_eod` | stopped losers vs. EOD losers counted correctly from exit_reason |

---

## What This Does Not Change

- `evaluate_cycle()` — no changes; pure analytics
- Order submission, dispatch, position sizing, stop management — untouched
- `ENABLE_LIVE_TRADING=false` gate — untouched
- No new schema — all queries read existing tables
- `strategy_report_cli.py` — not modified; both CLIs coexist
