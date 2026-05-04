# Spec: Nightly Evolve Pipeline + Rolling Performance Report

**Date:** 2026-05-04  
**Branch:** stop-order-reliability-fixes

---

## Problem

Three automation gaps keep parameter quality in check only when a human remembers to act:

1. **No scheduled re-optimization.** `alpaca-bot-backfill` and `alpaca-bot-evolve` must be run manually. Parameters can drift from market conditions indefinitely without anyone noticing.

2. **No aggregated live performance view.** `alpaca-bot-session-eval` shows one day at a time. There is no rolling 20- or 30-day view that reveals whether the live strategy is performing as the backtest predicted.

3. **No link between live performance and parameter freshness.** When live performance degrades, there is no automated process that surfaces it and proposes updated parameters.

---

## Fix

One new CLI (`alpaca-bot-nightly`) that chains:

```
watchlist → backfill → evolve (walk-forward) → rolling live report → candidate env file
```

One new Docker Compose service (`nightly`) so the pipeline runs automatically after market close. No auto-deploy — writing a candidate env file is the output; a human reviews and deploys.

---

## Design

### `alpaca-bot-nightly` CLI

**File:** `src/alpaca_bot/nightly/cli.py`

**Arguments:**

| Arg | Default | Description |
|---|---|---|
| `--trading-mode` | `TRADING_MODE` env | `paper` or `live` |
| `--days` | `252` | Lookback days for backfill (trading days) |
| `--report-days` | `20` | Lookback days for rolling live performance report |
| `--output-dir` | `/data/scenarios` | Where to write scenario JSON files |
| `--output-env` | (none) | Path to write winning candidate env block |
| `--validate-pct` | `0.2` | OOS fraction for walk-forward gate |
| `--strategy` | `breakout` | Strategy grid to sweep |
| `--no-db` | false | Skip persisting results to `tuning_results` |
| `--dry-run` | false | Skip Alpaca API calls; use existing scenario files |

**Flow:**

```
1.  Settings.from_env()         → credentials, trading_mode, strategy_version
2.  WatchlistStore.list_enabled(trading_mode)  → symbols
    (if empty: warn, skip backfill+evolve, still run live report)
3.  BackfillFetcher.fetch_and_save(symbols, days, output_dir)   [skipped if --dry-run]
4.  Load scenario files from output_dir
    (error if < 2 files after backfill)
5.  split_scenario(s, in_sample_ratio=1.0-validate_pct) per scenario
6.  run_multi_scenario_sweep(is_scenarios, base_env, grid, ...)
7.  evaluate_candidates_oos(top10, oos_scenarios, ...)
8.  held_pairs gate (same as evolve CLI)
9.  If winner: write output-env, print env block
    If no winner: print notice, continue (not an error)
10. Optional: persist to tuning_results (unless --no-db)
11. Rolling live report:
    - list weekdays in last report_days from today
    - list_closed_trades(trading_mode, strategy_version, date=d) per day
    - aggregate all ReplayTradeRecord objects across days
    - report_from_records(aggregated_trades, starting_equity)
    - print formatted rolling report
12. Return 0 (success), 1 (hard error — backfill failure, DB unreachable, etc.)
```

**Exit codes:**

- `0` — pipeline ran successfully (even if no candidates held)
- `1` — unrecoverable error (DB down, no scenario files after backfill, etc.)

No winner is not exit code 1 — it is a valid result with an informative message.

**Output example:**

```
── Backfill ────────────────────────────────────────────────
Symbols: 34 (from watchlist, paper mode)
Fetched: AAPL (4800 intraday, 252 daily), MSFT (4800/252), ...
Wrote 34 scenario files to /data/scenarios

── Evolve ──────────────────────────────────────────────────
Scenarios: 34 × IS/OOS split (80% / 20%)
Grid: breakout (36 combinations × 34 scenarios)
Scored: 28 / 36 candidates
Walk-forward: 6 / 10 held

Winner: BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=20
IS score: 0.82  OOS score: 0.61
Candidate env written to /data/candidate.env

── Live Performance (last 20 trading days) ─────────────────
Trades:       47   Wins: 28   Losses: 19   Win rate: 59.6%
P&L:     +$312.44  Sharpe:  0.82  Prof.fac:  1.31
Mean:      +0.43%  Max DD:  3.2%  Avg hold:  38min
MaxCL:  3         MaxCW:   5
Gates: ✓ Sharpe > 0  ✓ Profit factor ≥ 1.0  ✓ Trades ≥ 3
```

---

### Rolling Live Report

`session_eval_cli.py` covers single-day. The nightly CLI aggregates across `--report-days` weekdays by:

1. Iterating weekdays from `today - 1` back `report_days` days
2. Calling `order_store.list_closed_trades(trading_mode, strategy_version, session_date=d)` for each
3. Converting rows to `ReplayTradeRecord` via the same `_row_to_trade_record()` helper in `session_eval_cli.py` (imported, not duplicated)
4. Calling `report_from_records(all_trades, starting_equity)` once on the full list
5. Printing the report + gate status check (Sharpe > 0, profit_factor ≥ 1.0)

`starting_equity` for the rolling report: load `DailySessionStateStore.load(oldest_date_in_range, ...)` equity baseline. Fall back to 100 000 if not found.

The `_row_to_trade_record()` function and report printing helpers are imported from `session_eval_cli.py` — not duplicated.

---

### Docker Compose Service

In `deploy/compose.yaml`:

```yaml
volumes:
  postgres_data:
  nightly_data:      # NEW — persists scenario files and candidate.env between runs

services:
  # ... existing services unchanged ...

  nightly:
    image: alpaca-bot:latest
    command:
      - alpaca-bot-nightly
      - --output-dir
      - /data/scenarios
      - --output-env
      - /data/candidate.env
    environment: *alpaca_bot_env
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - nightly_data:/data
    restart: "no"
    profiles: ["ops"]
```

`profiles: ["ops"]` means it does NOT start with `docker compose up` — it only runs when explicitly triggered.

---

### Scheduling

**File:** `deploy/cron.d/alpaca-bot`

```
# Nightly evolve pipeline — runs 30 min after NYSE close (22:30 UTC = 5:30 PM ET year-round)
30 22 * * 1-5 root cd /workspace/alpaca_bot && docker compose -f deploy/compose.yaml run --rm nightly >> /var/log/alpaca-bot-nightly.log 2>&1
```

**Install script:** `scripts/install_cron.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
install -m 644 deploy/cron.d/alpaca-bot /etc/cron.d/alpaca-bot
echo "Cron installed. Runs weekdays at 22:30 UTC (5:30 PM ET)."
```

On-demand run: `docker compose -f deploy/compose.yaml run --rm nightly`

---

## Files Changed

| File | Change |
|---|---|
| `src/alpaca_bot/nightly/__init__.py` | New (empty) |
| `src/alpaca_bot/nightly/cli.py` | New — `alpaca-bot-nightly` CLI |
| `tests/unit/test_nightly_cli.py` | New — 4 unit tests |
| `deploy/compose.yaml` | Add `nightly` service + `nightly_data` volume |
| `deploy/cron.d/alpaca-bot` | New — host cron entry |
| `scripts/install_cron.sh` | New — install helper |
| `pyproject.toml` | Add `alpaca-bot-nightly` entry point |

No migrations. No new env vars. No changes to `evaluate_cycle()`, order dispatch, or any live-trading path.

---

## Safety Analysis

- **Financial safety**: The nightly CLI is entirely offline. The only external API call is a read-only `get_stock_bars()` / `get_daily_bars()` to fetch historical data. No order submission, no position sizing, no stop placement.
- **Audit trail**: No runtime state changes. No `AuditEvent` rows needed.
- **Intent / dispatch separation**: Not affected. The CLI never touches the orders or intents tables.
- **Advisory lock**: Not acquired. The nightly CLI connects to Postgres in read-mostly mode (read watchlist, read closed trades, optionally write tuning_results). Concurrent runs are safe — scenario files are overwritten idempotently, and `TuningResultStore.save_run()` uses independent `run_id` UUIDs.
- **Pure engine boundary**: `evaluate_cycle()` untouched.
- **Rollback safety**: No migrations. ✓
- **Paper vs. live**: CLI reads `TRADING_MODE` from env. Watchlist and closed trades queries are scoped per `trading_mode`. Behavior is identical for both modes.
- **Market hours**: No broker order calls. The 22:30 UTC schedule runs after all market sessions are closed.
- **No new env vars**: Uses `DATABASE_URL`, `TRADING_MODE`, `STRATEGY_VERSION`, and Alpaca credentials — all already required by the supervisor.

---

## Design Decisions

**No auto-deploy.** Writing a candidate env file is the gate. A human must review the proposed parameter change and run `./scripts/deploy.sh` to apply it. This keeps a human in the loop for any live-trading parameter change.

**Host cron, not container cron.** A `profiles: ["ops"]` service triggered by host cron is simpler than embedding `supercronic` in the image. It avoids adding a new binary to the Dockerfile and keeps scheduling visible in a plain text file on the host.

**Exit code 0 for no-winner.** A "no held candidates" result is not a failure — it means the current parameters are still the best available choice. The nightly script should always complete without triggering alerting. A hard error (DB down, backfill 100% failed) returns 1.

**`--dry-run` for testing.** `--dry-run` skips the Alpaca API calls and uses whatever scenario files already exist in `--output-dir`. This makes the pipeline testable without credentials and allows replaying the evolve step against a fixed dataset.

**Reuse `_row_to_trade_record()` from `session_eval_cli`.** The conversion logic already exists and is tested. Import it rather than duplicating.

**Weekday approximation for trading days.** The rolling live report iterates weekdays (Mon–Fri), ignoring market holidays. This is good enough for a performance summary — being off by 1–2 days on a 20-day window doesn't materially affect the metrics.
