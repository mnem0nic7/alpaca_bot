# Session Evaluation CLI — Design Spec

**Date:** 2026-05-04
**Status:** Draft

---

## Problem

After a trading session, an operator has two ways to evaluate performance:

1. **Web dashboard `/metrics`** — shows P&L, win rate, mean return, Sharpe, max drawdown, slippage per trade. Requires a browser and a running web server.
2. **Daily summary notification** — pushed via Notifier (Slack/email) after market close. Plain-text; covers P&L, trade count, win rate, per-strategy breakdown, open positions.

Neither surfaces the full `BacktestReport`-quality metrics that `alpaca-bot-backtest run` produces for historical scenario files:
- **Profit factor** (gross wins / gross losses)
- **Exit reason breakdown** (stop wins, stop losses, EOD wins, EOD losses)
- **Average hold time**
- **Consecutive win/loss streaks**
- **Average win/loss return %** (win bucket vs loss bucket separately)

These metrics are computed during replay but are never surfaced for live trades. An operator who wants to compare today's live performance against backtest benchmarks has no direct path.

---

## Goal

Add `alpaca-bot-session-eval` — a read-only CLI command that queries live trade data from Postgres and prints a `BacktestReport`-quality evaluation of any session (default: today).

```
alpaca-bot-session-eval
alpaca-bot-session-eval --date 2026-05-03
alpaca-bot-session-eval --date 2026-05-03 --mode paper --strategy breakout
```

---

## Architecture

```
replay/report.py          — extract report_from_records() as public function
storage/repositories.py   — add intent_type to list_closed_trades() return dict
admin/session_eval_cli.py — new CLI: query DB → ReplayTradeRecord list → report
pyproject.toml            — add alpaca-bot-session-eval entry point
```

No new Postgres tables. No migrations. No new env vars.

---

## Component: `report_from_records()` in `replay/report.py`

Extract the stat-computation body of `build_backtest_report()` into a new public function:

```python
def report_from_records(
    trades: list[ReplayTradeRecord],
    starting_equity: float,
    strategy_name: str = "breakout",
) -> BacktestReport:
```

`build_backtest_report()` is refactored to call `report_from_records()`:

```python
def build_backtest_report(result: ReplayResult, strategy_name: str = "breakout") -> BacktestReport:
    trades = _extract_trades(result.events)
    return report_from_records(trades, result.scenario.starting_equity, strategy_name)
```

This is a non-breaking refactor: existing callers of `build_backtest_report()` are unaffected. The new function is used by `session_eval_cli.py` to avoid duplicating stat logic.

---

## Component: `list_closed_trades()` in `storage/repositories.py`

Add `x.intent_type` to the SELECT:

```sql
SELECT
    x.symbol,
    x.strategy_name,
    x.intent_type,        -- NEW: "stop" or "exit" (used to determine exit_reason)
    ... (existing correlated subqueries) ...
    x.fill_price AS exit_fill,
    x.updated_at AS exit_time,
    COALESCE(x.filled_quantity, x.quantity) AS qty
```

And to the returned dict:

```python
{
    "symbol": row[0],
    "strategy_name": row[1],
    "intent_type": row[2],   # NEW
    "entry_fill": float(row[3]) if row[3] is not None else None,
    ...
    "qty": int(row[8]),
}
```

`intent_type = "stop"` maps to `exit_reason = "stop"`.
`intent_type = "exit"` maps to `exit_reason = "eod"`.

This is backward compatible: existing callers access the dict by key — adding a new key does not affect them.

---

## Component: `admin/session_eval_cli.py`

New file. Entry point: `alpaca-bot-session-eval`.

```python
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-session-eval")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="Session date to evaluate (default: today)")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                        help="Trading mode (default: paper)")
    parser.add_argument("--strategy-version", metavar="VERSION",
                        help="Strategy version (default: STRATEGY_VERSION env var)")
    parser.add_argument("--strategy", metavar="NAME",
                        help="Filter to a single strategy name")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
```

**Flow:**
1. Parse `--date` (default: `date.today()`); parse `--mode`, `--strategy-version`, `--strategy`.
2. `settings = Settings.from_env()` — used for `DATABASE_URL` and fallback `STRATEGY_VERSION`.
3. Connect to Postgres via `connect_postgres(settings.database_url)`.
4. Load equity baseline: `DailySessionStateStore.load(session_date, trading_mode, strategy_version, strategy_name="_equity")`. If None, use `100_000.0` as fallback with a warning.
5. Load closed trades: `OrderStore.list_closed_trades(trading_mode, strategy_version, session_date, strategy_name=args.strategy)`.
6. If no trades: print `"No closed trades for {date}."` and return 0.
7. Convert each row to `ReplayTradeRecord` using `intent_type` for `exit_reason`.
8. Call `report_from_records(trade_records, starting_equity, strategy_name=args.strategy or "all")`.
9. Print the report via `_print_session_report()`.

**Output format:**

```
Session Evaluation — 2026-05-04  [paper / v1]
══════════════════════════════════════════════
 Trades:  8   Wins: 5   Losses: 3   Win rate: 63%
 P&L:     $183.40   Sharpe: 1.42   Prof.fac: 2.31
 Mean:    +0.51%    Max DD: 0.9%   Avg hold: 52min
 MaxCL:   2         MaxCW:  3

 Exit breakdown:
   Stop wins:   2   Stop losses: 3
   EOD wins:    3   EOD losses:  0

 Trades:
   Symbol   Strategy   Qty  Entry   Exit   P&L       Ret%    Hold   Exit
   AAPL     breakout    10  192.30  193.80  +$15.00  +0.78%   38m   eod
   TSLA     breakout     5  250.10  247.30  -$14.00  -1.12%   27m   stop
```

---

## Testing

### `tests/unit/test_session_eval.py`

1. `test_report_from_records_basic_stats` — 3 trades (2W/1L); verify total_trades=3, win_rate≈0.667, profit_factor > 0.
2. `test_report_from_records_exit_breakdown` — mix of "stop" and "eod" exit reasons; verify stop_wins/stop_losses/eod_wins/eod_losses counts.
3. `test_report_from_records_zero_trades` — empty list; verify all None fields and 0 counts.
4. `test_report_from_records_parity_with_build_backtest_report` — build a `ReplayResult` with 3 trades, call both `build_backtest_report()` and `report_from_records()` with equivalent input; stats must match.
5. `test_row_to_trade_record_stop_exit` — row with `intent_type="stop"`; `exit_reason == "stop"`.
6. `test_row_to_trade_record_eod_exit` — row with `intent_type="exit"`; `exit_reason == "eod"`.
7. `test_session_eval_cli_no_trades_exits_zero` — patch `OrderStore.list_closed_trades` to return `[]`; `main()` returns 0 and prints "No closed trades".
8. `test_session_eval_cli_produces_report` — patch stores to return 2 trades; `main()` completes without error and output contains strategy name.
9. `test_list_closed_trades_includes_intent_type` — unit test that the dict returned by `list_closed_trades()` contains an `"intent_type"` key.

### Existing tests

`test_report.py` — run after refactoring `build_backtest_report()` to confirm no regressions.
`test_tuning_sweep.py` — run after refactor to confirm `BacktestReport` construction still works.

---

## `pyproject.toml`

```toml
alpaca-bot-session-eval = "alpaca_bot.admin.session_eval_cli:main"
```

---

## Financial Safety

- Read-only. No order submission, no position sizing, no stop placement.
- `evaluate_cycle()` is unaffected — this is an independent CLI.
- No DB writes. No advisory lock required.
- `ENABLE_LIVE_TRADING` gate unaffected.
- Works identically in paper and live mode.

---

## Out of Scope

- Comparing live session stats against the latest tuning_results backtest (separate feature)
- Exporting session report to CSV or JSON
- Real-time intraday view (the web dashboard covers this)
- Multi-day roll-up report
