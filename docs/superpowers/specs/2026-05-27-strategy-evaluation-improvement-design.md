# Strategy Evaluation and Improvement Design

## Goal

Two distinct improvements that together address "evaluate and improve trading strategies":

1. **Annualized Sharpe correction** — align the backtest scoring objective with the capital allocation formula so the nightly auto-tune selects strategy variants by the same metric used to allocate capital between them.
2. **Signal funnel analytics CLI** — surface decision-log data as a per-strategy rejection funnel so operators can see where signals are being discarded.

---

## Problem Statement

### Scoring vs. Allocation Inconsistency

`BacktestReport.sharpe_ratio` is a per-trade, non-annualized ratio:

```python
mean(return_pct for each trade) / std(return_pct for each trade)
```

`score_report()` in `tuning/sweep.py` uses this as the primary optimization objective. The nightly auto-tune selects the best parameter variant by this score.

`risk/weighting.py` allocates capital between strategies by:

```python
mean(daily_pnl per day) / std(daily_pnl per day) * sqrt(252)
```

Where "per day" means each calendar date when at least one trade closed.

This creates an inconsistency: we **optimize** by per-trade Sharpe but **allocate** by daily-return annualized Sharpe. A variant with 10 large trades spread across 10 days will score identically to one with 10 small trades crammed into 2 days under per-trade Sharpe — but very differently under daily Sharpe.

**Fix**: Add `annualized_sharpe: float | None` to `BacktestReport` using the same daily-PnL-bucketed formula as `weighting.py`. Update `score_report()` to use `annualized_sharpe` when available, falling back to `sharpe_ratio` for backward compatibility with old report objects.

### No Signal Funnel Visibility

The `decision_log` table records every per-symbol evaluation: which stage rejected it, what the reject reason was, whether it was accepted. But there is no analytics layer on top. Operators cannot currently see:

- What fraction of evaluated symbols generate a signal at all
- Which filter (VWAP, VIX, sector, position limit) accounts for the most rejections
- Whether a strategy variant is generating zero trades because no signals fire, or because signals fire but get filtered

**Fix**: New `alpaca-bot-funnel-report` CLI that queries `decision_log` for a date range and prints a per-strategy stage breakdown.

---

## Architecture

### Files Modified

| File | Change |
|------|--------|
| `src/alpaca_bot/replay/report.py` | Add `annualized_sharpe: float | None = None` to `BacktestReport`; add `_compute_annualized_sharpe()` helper; call it in `report_from_records()` |
| `src/alpaca_bot/tuning/sweep.py` | Update `score_report()` to prefer `annualized_sharpe` over `sharpe_ratio`; update `_aggregate_reports()` to average `annualized_sharpe` across scenarios |
| `src/alpaca_bot/storage/repositories.py` | Add `DecisionLogStore.funnel_by_strategy()` query method |
| `pyproject.toml` | Add `alpaca-bot-funnel-report = "alpaca_bot.admin.funnel_report_cli:main"` entry point |

### Files Created

| File | Purpose |
|------|---------|
| `src/alpaca_bot/admin/funnel_report_cli.py` | CLI: reads `decision_log`, prints funnel table per strategy |
| `tests/unit/test_annualized_sharpe.py` | Unit tests for new Sharpe computation and score_report() update |
| `tests/unit/test_funnel_report.py` | Unit tests for `funnel_by_strategy()` and CLI argument parsing |

---

## Component Design

### 1. `_compute_annualized_sharpe(trades, starting_equity)`

Groups trades by exit date (using `entry_time.date()` — ReplayTradeRecord has no explicit exit_date, so we use `exit_time.date()`). Computes daily PnL sum per date. Applies `mean / std * sqrt(252)`.

Degenerate cases:
- `< 2` trading days → `None` (insufficient data for std)
- `std == 0` → `None` (all same-day PnL — not a meaningful Sharpe)
- `trades == []` → `None`

Note: only days with closed trades are included (same as `weighting.py`). Zero-trade days are not padded in.

```python
def _compute_annualized_sharpe(trades: list[ReplayTradeRecord]) -> float | None:
    if len(trades) < 2:
        return None
    daily: dict[date, float] = {}
    for t in trades:
        d = t.exit_time.date()
        daily[d] = daily.get(d, 0.0) + t.pnl
    if len(daily) < 2:
        return None
    values = list(daily.values())
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = variance ** 0.5
    if std == 0.0:
        return None
    return mean / std * math.sqrt(252)
```

### 2. `BacktestReport` Field Addition

```python
annualized_sharpe: float | None = None  # daily-bucketed, sqrt(252) annualized
```

Added after `sharpe_ratio`. Computed and passed in `report_from_records()`. Existing `sharpe_ratio` retained for display/backward compat.

### 3. `score_report()` Update in `sweep.py`

Current:
```python
if report.sharpe_ratio is not None:
    base = report.sharpe_ratio
```

New:
```python
if report.annualized_sharpe is not None:
    base = report.annualized_sharpe
elif report.sharpe_ratio is not None:
    base = report.sharpe_ratio
```

The `_aggregate_reports()` helper that averages Sharpe across multi-scenario sweeps also needs the same preference update.

### 4. `DecisionLogStore.funnel_by_strategy()`

New query method that returns per-strategy raw stage counts for a date range. The actual `decision` and `reject_stage` values used in the engine are:

**Global pre-checks (emitted for all symbols):**
- `decision="rejected", reject_stage="pre_filter", reject_reason in ("regime_blocked","vix_blocked","sector_blocked")`
- `decision="rejected", reject_stage="capacity", reject_reason="capacity_full"` — all slots occupied

**Per-symbol loop:**
- `decision="skipped_existing_position"` — symbol already has open position or working order
- `decision="skipped_already_traded"` — already traded this symbol today
- `decision="rejected", reject_stage="stale_data"` — daily bars too old
- `decision="skipped_no_signal"` — signal evaluator returned None
- `decision="rejected", reject_stage="vwap_filter"` — signal bar close < VWAP
- `decision="rejected", reject_stage="sizing"` — quantity zero or below min notional
- `decision="accepted"` — accepted for entry

The query returns aggregate counts per strategy per day, returning raw counts per decision/stage. Python code assembles the funnel display:

```python
def funnel_by_strategy(
    self,
    *,
    start_date: date,
    end_date: date,
    trading_mode: str,
    market_timezone: str = "America/New_York",
) -> list[dict]:
    """Return per-strategy funnel counts for a date range.

    Each row: {strategy_name, evaluated, not_skipped, not_prefiltered,
               signal_fired, passed_entry_filter, sized, accepted}.
    """
```

SQL using FILTER clauses (Postgres 9.4+):

```sql
SELECT
    strategy_name,
    COUNT(*) AS evaluated,
    COUNT(*) FILTER (WHERE decision NOT IN ('skipped_existing_position', 'skipped_already_traded'))
        AS not_skipped,
    COUNT(*) FILTER (WHERE decision NOT IN ('skipped_existing_position', 'skipped_already_traded')
                       AND reject_stage IS DISTINCT FROM 'pre_filter'
                       AND reject_stage IS DISTINCT FROM 'stale_data')
        AS not_prefiltered,
    COUNT(*) FILTER (WHERE decision NOT IN ('skipped_existing_position', 'skipped_already_traded',
                                             'skipped_no_signal')
                       AND reject_stage IS DISTINCT FROM 'pre_filter'
                       AND reject_stage IS DISTINCT FROM 'stale_data')
        AS signal_fired,
    COUNT(*) FILTER (WHERE decision NOT IN ('skipped_existing_position', 'skipped_already_traded',
                                             'skipped_no_signal')
                       AND reject_stage IS DISTINCT FROM 'pre_filter'
                       AND reject_stage IS DISTINCT FROM 'stale_data'
                       AND reject_stage IS DISTINCT FROM 'vwap_filter')
        AS passed_entry_filter,
    COUNT(*) FILTER (WHERE decision NOT IN ('skipped_existing_position', 'skipped_already_traded',
                                             'skipped_no_signal')
                       AND reject_stage IS DISTINCT FROM 'pre_filter'
                       AND reject_stage IS DISTINCT FROM 'stale_data'
                       AND reject_stage IS DISTINCT FROM 'vwap_filter'
                       AND reject_stage IS DISTINCT FROM 'sizing')
        AS sized,
    COUNT(*) FILTER (WHERE decision = 'accepted') AS accepted
FROM decision_log
WHERE DATE(cycle_at AT TIME ZONE %s) BETWEEN %s AND %s
  AND trading_mode = %s
GROUP BY strategy_name
ORDER BY strategy_name
```

Note: `capacity` rejections are intentionally not a separate funnel stage because they are a cycle-level constraint (all slots full), not a per-signal quality filter. They show up in `not_prefiltered` counts alongside VIX/sector rejections, which is the appropriate grouping.

### 5. `alpaca-bot-funnel-report` CLI

```
usage: alpaca-bot-funnel-report [--days N] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                                 [--strategy STRATEGY]

Defaults: last 7 days.

Output (Rich table):
Strategy         Eval  Signal  Mkt-Gate  Filter  Pos-Lim  Sized  Accepted
breakout          240     130       125     120      120    119       45
momentum_reversion 80      20        18      18       16      8        3
```

The CLI connects to Postgres via `Settings.from_env()` (reads DATABASE_URL), creates a `DecisionLogStore`, and calls `funnel_by_strategy()`. Output uses Rich tables (already a dependency in the project) or plain text fallback.

---

## Data Flow

```
alpaca-bot-funnel-report
    ↓
Settings.from_env() → DATABASE_URL
    ↓
DecisionLogStore.funnel_by_strategy(start_date, end_date, trading_mode)
    ↓
SELECT ... FROM decision_log WHERE ... GROUP BY strategy_name
    ↓
Rich table printed to stdout
```

```
BacktestReport / report_from_records()
    ↓
_compute_annualized_sharpe(trades)  → annualized_sharpe field
    ↓
score_report()  prefers annualized_sharpe → better nightly auto-tune selection
```

---

## Error Handling

- `annualized_sharpe`: returns `None` when < 2 trading days — `score_report()` falls through to `sharpe_ratio` gracefully.
- Funnel CLI: connects to Postgres via `psycopg2`; propagates connection errors to stderr naturally. No `--dry-run` needed (read-only).
- `funnel_by_strategy()` returns empty list if no matching rows (no error).

---

## Testing

### `test_annualized_sharpe.py`

- `test_annualized_sharpe_groups_by_day()` — 4 trades across 2 days → daily_pnl = [sum_day1, sum_day2], verify formula
- `test_annualized_sharpe_none_when_single_day()` — all trades on same day → None
- `test_annualized_sharpe_none_when_fewer_than_2_trades()` — 1 trade → None
- `test_score_report_prefers_annualized_sharpe()` — report with both fields, verify annualized_sharpe wins
- `test_score_report_falls_back_to_sharpe_ratio_when_annualized_none()` — annualized_sharpe=None, verify sharpe_ratio used

### `test_funnel_report.py`

- `test_funnel_by_strategy_counts_stages()` — fake connection returning 5 rows across 2 strategies; verify stage counts
- `test_funnel_by_strategy_empty_returns_empty()` — zero rows, returns []
- `test_funnel_cli_main_prints_table()` — capture stdout, verify header row present

---

## Backward Compatibility

- `BacktestReport` is a frozen dataclass with a default `annualized_sharpe=None`. All existing call sites pass positional args only up to `sharpe_ratio` (passed as keyword in `report_from_records`). The new field being keyword-with-default means no existing code breaks.
- `score_report()` fallback to `sharpe_ratio` ensures old `BacktestReport` objects (without `annualized_sharpe`) from persisted scenarios continue to work.
- `weighting.py` is not modified — it keeps its own live-trade daily Sharpe computation unchanged.

---

## What This Does Not Change

- `evaluate_cycle()` — no changes; this is pure analytics
- Order submission, dispatch, position sizing, stop management — untouched
- `ENABLE_LIVE_TRADING=false` gate — untouched
- Option strategies — untouched; they don't appear in STRATEGY_GRIDS and thus aren't scored by `score_report()`
- Migration — no schema change needed; reads existing `decision_log` rows
