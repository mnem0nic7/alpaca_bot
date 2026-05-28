# Weekly Review CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `alpaca-bot-weekly-review`, a single CLI command that produces a comprehensive 7-section weekly trade review covering daily P&L breakdown, symbol attribution, trade quality ratio, signal funnel, and operational health.

**Architecture:** New `weekly_review_cli.py` in `src/alpaca_bot/admin/` reuses `list_closed_trade_records()` (extended to include `intent_type`), `report_from_records()`, `compute_equity_stats()`, and `funnel_by_strategy()` — no new repository methods. Pure computation helpers (`_group_by_date`, `_group_by_symbol`, `_trade_quality`) mirror the pattern of `compute_daily_pnl()` in `strategy_report_cli.py`.

**Tech Stack:** Python 3.11+, psycopg2, existing project repositories (`OrderStore`, `OptionOrderRepository`, `DecisionLogStore`, `AuditEventStore`), `report_from_records()` from `replay/report.py`.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `src/alpaca_bot/storage/repositories.py` | Modify | Add `intent_type` field to `list_closed_trade_records()` result dict |
| `src/alpaca_bot/admin/weekly_review_cli.py` | Create | New CLI: all 7 sections + pure computation helpers |
| `tests/unit/test_weekly_review_cli.py` | Create | 6 unit tests for pure computation functions |
| `pyproject.toml` | Modify | Register `alpaca-bot-weekly-review` entry point |

---

## Task 1: Extend `list_closed_trade_records()` to include `intent_type`

**Why:** Section 5 (Trade Quality) requires stop-vs-EOD exit breakdown. The SQL already filters `WHERE intent_type IN ('stop', 'exit')` — adding it to the SELECT is a minimal additive change that doesn't break any existing consumers (they simply receive an extra key).

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (lines ~830–901)
- Test: `tests/unit/test_repositories.py`

- [ ] **Step 1: Read the exact SELECT in `list_closed_trade_records`**

  Open `src/alpaca_bot/storage/repositories.py` at line 830. Find the SQL string in `list_closed_trade_records`. The SELECT currently returns 7 columns (indices 0–6):
  ```
  [0] x.symbol
  [1] x.strategy_name
  [2] COALESCE(x.filled_quantity, x.quantity) AS qty
  [3] x.fill_price AS exit_fill
  [4] x.updated_at AS exit_time
  [5] (subquery) AS entry_fill
  [6] (subquery) AS entry_time
  ```

- [ ] **Step 2: Add `x.intent_type` to the SELECT**

  In `repositories.py`, find the SQL in `list_closed_trade_records`. Add `x.intent_type` as the last column before `FROM orders x`:

  ```python
  # OLD — last line of SELECT before FROM:
  #   (SELECT e.updated_at ... LIMIT 1) AS entry_time
  # NEW — add one line after entry_time subquery:
  #   x.intent_type
  ```

  The full edited SELECT block becomes:
  ```sql
  SELECT
      x.symbol,
      x.strategy_name,
      COALESCE(x.filled_quantity, x.quantity) AS qty,
      x.fill_price AS exit_fill,
      x.updated_at AS exit_time,
      (SELECT e.fill_price
         FROM orders e
        WHERE e.symbol = x.symbol
          AND e.trading_mode = x.trading_mode
          AND e.strategy_version = x.strategy_version
          AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
          AND e.intent_type = 'entry'
          AND e.fill_price IS NOT NULL
          AND e.status = 'filled'
          AND e.updated_at <= x.updated_at
        ORDER BY e.updated_at DESC LIMIT 1) AS entry_fill,
      (SELECT e.updated_at
         FROM orders e
        WHERE e.symbol = x.symbol
          AND e.trading_mode = x.trading_mode
          AND e.strategy_version = x.strategy_version
          AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
          AND e.intent_type = 'entry'
          AND e.fill_price IS NOT NULL
          AND e.status = 'filled'
          AND e.updated_at <= x.updated_at
        ORDER BY e.updated_at DESC LIMIT 1) AS entry_time,
      x.intent_type
  ```

- [ ] **Step 3: Add `intent_type` to the result dict**

  In the same method, find the `result.append({...})` block. Add `"intent_type": row[7]` as the last key:

  ```python
  result.append({
      "symbol": row[0],
      "strategy_name": row[1],
      "qty": qty,
      "entry_price": entry_fill,
      "exit_price": exit_fill,
      "entry_time": entry_time,
      "exit_time": exit_time,
      "pnl": (exit_fill - entry_fill) * qty,
      "hold_seconds": hold_seconds,
      "intent_type": row[7],
  })
  ```

- [ ] **Step 4: Write a targeted test**

  Open `tests/unit/test_repositories.py`. Add a test that verifies `intent_type` appears in results. Find the `_FakeConn` / `_FakeCursor` pattern already used in `test_funnel_report.py` — use the same approach:

  ```python
  def test_list_closed_trade_records_includes_intent_type():
      """Verify that intent_type is returned in each result dict."""
      from datetime import datetime, timezone
      from alpaca_bot.storage.repositories import OrderStore
      from alpaca_bot.storage.models import TradingMode  # or config.TradingMode

      _now = datetime(2026, 5, 21, 20, 0, 0, tzinfo=timezone.utc)
      # Row columns: symbol, strategy_name, qty, exit_fill, exit_time,
      #              entry_fill, entry_time, intent_type
      fake_row = ("AAPL", "breakout", 10.0, 105.0, _now, 100.0, _now, "stop")

      class _FakeCursor:
          def execute(self, sql, params): pass
          def fetchall(self): return [fake_row]

      class _FakeConn:
          def cursor(self): return _FakeCursor()

      from alpaca_bot.config import TradingMode
      from datetime import date
      store = OrderStore(_FakeConn())
      records = store.list_closed_trade_records(
          trading_mode=TradingMode("paper"),
          strategy_version="v1",
          since_date=date(2026, 5, 21),
          until_date=date(2026, 5, 21),
      )
      assert len(records) == 1
      assert records[0]["intent_type"] == "stop"
  ```

- [ ] **Step 5: Run the test to verify it fails first**

  ```bash
  pytest tests/unit/test_repositories.py::test_list_closed_trade_records_includes_intent_type -v
  ```
  Expected: `FAIL` — `KeyError: 'intent_type'` or `IndexError: tuple index out of range`.

- [ ] **Step 6: Run after implementation to confirm it passes**

  ```bash
  pytest tests/unit/test_repositories.py::test_list_closed_trade_records_includes_intent_type -v
  ```
  Expected: `PASS`.

- [ ] **Step 7: Run the full test suite to confirm no regressions**

  ```bash
  pytest -x -q
  ```
  Expected: all passing (the `intent_type` key is additive — no existing code breaks).

- [ ] **Step 8: Commit**

  ```bash
  git add src/alpaca_bot/storage/repositories.py tests/unit/test_repositories.py
  git commit -m "feat: add intent_type to list_closed_trade_records result dict"
  ```

---

## Task 2: Write failing tests for `weekly_review_cli.py`

**Files:**
- Create: `tests/unit/test_weekly_review_cli.py`

- [ ] **Step 1: Create the test file with 6 tests**

  Create `tests/unit/test_weekly_review_cli.py` with the content below. These tests import from `weekly_review_cli.py` which doesn't exist yet — all 6 should fail with `ImportError`.

  ```python
  from __future__ import annotations

  from datetime import datetime, timezone

  import pytest

  from alpaca_bot.admin.weekly_review_cli import (
      _group_by_date,
      _group_by_symbol,
      _trade_quality,
      _render_daily_table,
      _render_symbol_attribution,
      _render_trade_quality,
  )


  def _rec(
      symbol: str = "AAPL",
      strategy_name: str = "breakout",
      pnl: float = 50.0,
      intent_type: str = "exit",
      exit_time: datetime | None = None,
      entry_time: datetime | None = None,
  ) -> dict:
      """Build a minimal equity trade record dict."""
      if exit_time is None:
          exit_time = datetime(2026, 5, 21, 20, 0, 0, tzinfo=timezone.utc)
      if entry_time is None:
          entry_time = datetime(2026, 5, 21, 18, 0, 0, tzinfo=timezone.utc)
      return {
          "symbol": symbol,
          "strategy_name": strategy_name,
          "qty": 10.0,
          "entry_price": 100.0,
          "exit_price": 100.0 + pnl / 10.0,
          "pnl": pnl,
          "intent_type": intent_type,
          "exit_time": exit_time,
          "entry_time": entry_time,
          "hold_seconds": (exit_time - entry_time).total_seconds(),
      }


  def test_group_by_date_groups_trades_by_exit_date():
      day1 = datetime(2026, 5, 21, 20, 0, 0, tzinfo=timezone.utc)
      day2 = datetime(2026, 5, 22, 20, 0, 0, tzinfo=timezone.utc)
      records = [
          _rec(pnl=100.0, exit_time=day1),
          _rec(pnl=-20.0, exit_time=day1),
          _rec(pnl=50.0, exit_time=day2),
          _rec(pnl=30.0, exit_time=day2),
      ]
      rows = _group_by_date(records, [], "America/New_York")
      assert len(rows) == 2
      assert rows[0]["trade_count"] == 2
      assert rows[0]["total_pnl"] == pytest.approx(80.0)
      assert rows[0]["win_count"] == 1
      assert rows[1]["trade_count"] == 2
      # cumulative after day2 = 80 + 80 = 160
      assert rows[1]["cumul_pnl"] == pytest.approx(160.0)


  def test_group_by_symbol_sorts_by_pnl_descending():
      records = [
          _rec(symbol="TSLA", pnl=-50.0),
          _rec(symbol="NVDA", pnl=200.0),
          _rec(symbol="AAPL", pnl=80.0),
      ]
      rows = _group_by_symbol(records)
      assert rows[0]["symbol"] == "NVDA"
      assert rows[1]["symbol"] == "AAPL"
      assert rows[2]["symbol"] == "TSLA"
      assert rows[2]["total_pnl"] == pytest.approx(-50.0)


  def test_trade_quality_win_loss_ratio():
      records = [
          _rec(pnl=60.0),
          _rec(pnl=40.0),
          _rec(pnl=-20.0),
          _rec(pnl=-30.0),
      ]
      q = _trade_quality(records)
      assert q["avg_winner"] == pytest.approx(50.0)
      assert q["avg_loser"] == pytest.approx(-25.0)
      assert q["win_loss_ratio"] == pytest.approx(2.0)
      assert q["max_winner"] == pytest.approx(60.0)
      assert q["max_loser"] == pytest.approx(-30.0)


  def test_symbol_attribution_top_bottom_5():
      """_group_by_symbol returns correctly sorted data for top/bottom slicing."""
      records = [_rec(symbol=f"SYM{i}", pnl=float(100 - i * 20)) for i in range(12)]
      rows = _group_by_symbol(records)
      # Best first
      assert rows[0]["symbol"] == "SYM0"
      assert rows[0]["total_pnl"] == pytest.approx(100.0)
      # Worst last
      assert rows[-1]["symbol"] == "SYM11"
      assert rows[-1]["total_pnl"] == pytest.approx(100.0 - 11 * 20.0)
      # At least 12 total rows, 5 distinct top and 5 distinct bottom
      assert len(rows) == 12
      top5 = rows[:5]
      bottom5 = rows[-5:]
      assert all(t["total_pnl"] > b["total_pnl"] for t in top5 for b in bottom5)


  def test_loser_analysis_counts_stop_vs_eod():
      records = [
          _rec(pnl=-30.0, intent_type="stop"),
          _rec(pnl=-20.0, intent_type="stop"),
          _rec(pnl=-10.0, intent_type="exit"),
          _rec(pnl=50.0, intent_type="exit"),
      ]
      q = _trade_quality(records)
      assert q["stop_losses"] == 2
      assert q["eod_losses"] == 1
      assert q["eod_wins"] == 1
      assert q["stop_wins"] == 0


  def test_weekly_review_no_trades_prints_no_data(capsys):
      """Zero-trade path: render functions produce 'no closed' messages without crashing."""
      _render_daily_table([])
      _render_symbol_attribution([])
      _render_trade_quality({
          "avg_winner": None, "avg_loser": None,
          "max_winner": None, "max_loser": None,
          "win_loss_ratio": None,
          "stop_wins": 0, "stop_losses": 0,
          "eod_wins": 0, "eod_losses": 0,
      })
      captured = capsys.readouterr()
      assert "no closed" in captured.out.lower()
  ```

- [ ] **Step 2: Run tests to confirm they all fail**

  ```bash
  pytest tests/unit/test_weekly_review_cli.py -v
  ```
  Expected: all 6 `FAILED` with `ModuleNotFoundError: No module named 'alpaca_bot.admin.weekly_review_cli'`.

---

## Task 3: Implement `weekly_review_cli.py`

**Files:**
- Create: `src/alpaca_bot/admin/weekly_review_cli.py`

- [ ] **Step 1: Create the file**

  Create `src/alpaca_bot/admin/weekly_review_cli.py` with the full implementation below:

  ```python
  from __future__ import annotations

  import argparse
  import csv
  import os
  import sys
  from collections import defaultdict
  from datetime import date, datetime, time, timedelta, timezone
  from typing import Sequence
  from zoneinfo import ZoneInfo

  from alpaca_bot.admin.strategy_report_cli import EquityStrategyStats, compute_equity_stats
  from alpaca_bot.config import Settings, TradingMode
  from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records
  from alpaca_bot.storage.db import connect_postgres
  from alpaca_bot.storage.repositories import (
      AuditEventStore,
      DecisionLogStore,
      OptionOrderRepository,
      OrderStore,
  )


  def main(argv: Sequence[str] | None = None) -> int:
      parser = argparse.ArgumentParser(
          prog="alpaca-bot-weekly-review",
          description="Combined weekly trade review: P&L, attribution, quality, funnel, health",
      )
      parser.add_argument("--days", type=int, default=7, metavar="N",
                          help="Number of calendar days to look back (default: 7)")
      parser.add_argument("--since", metavar="YYYY-MM-DD",
                          help="Start date (overrides --days)")
      parser.add_argument("--until", metavar="YYYY-MM-DD",
                          help="End date inclusive (default: today ET)")
      parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                          help="Trading mode (default: paper)")
      parser.add_argument("--strategy-version", metavar="VERSION",
                          help="Strategy version (default: STRATEGY_VERSION env var)")
      parser.add_argument("--csv-dir", metavar="PATH",
                          help="Export equity_trades.csv, option_trades.csv, daily_pnl.csv here")
      args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

      settings = Settings.from_env()
      tz = settings.market_timezone
      today_et = datetime.now(tz).date()
      until_date = date.fromisoformat(args.until) if args.until else today_et
      since_date = (
          date.fromisoformat(args.since)
          if args.since
          else until_date - timedelta(days=args.days - 1)
      )
      trading_mode = TradingMode(args.mode)
      strategy_version = args.strategy_version or settings.strategy_version
      market_timezone = settings.market_timezone.key

      since_dt = datetime.combine(since_date, time.min, tzinfo=tz).astimezone(timezone.utc)
      until_dt = datetime.combine(until_date + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)

      conn = connect_postgres(settings.database_url)
      try:
          order_store = OrderStore(conn)
          option_repo = OptionOrderRepository(conn)
          audit_store = AuditEventStore(conn)
          decision_log_store = DecisionLogStore(conn)

          equity_records = order_store.list_closed_trade_records(
              trading_mode=trading_mode,
              strategy_version=strategy_version,
              since_date=since_date,
              until_date=until_date,
              market_timezone=market_timezone,
          )
          option_records = option_repo.list_closed_option_trade_records(
              trading_mode=trading_mode,
              strategy_version=strategy_version,
              since_date=since_date,
              until_date=until_date,
              market_timezone=market_timezone,
          )
          funnel_rows = decision_log_store.funnel_by_strategy(
              start_date=since_date,
              end_date=until_date,
              trading_mode=args.mode,
              market_timezone=market_timezone,
          )
      finally:
          close = getattr(conn, "close", None)
          if callable(close):
              close()

      replay_trades = _records_to_replay_trades(equity_records)
      report = (
          report_from_records(replay_trades, starting_equity=100_000.0)
          if replay_trades else None
      )

      equity_stats = compute_equity_stats(equity_records)
      per_strategy_sharpe = _compute_per_strategy_sharpe(equity_records)
      daily_rows = _group_by_date(equity_records, option_records, market_timezone)
      symbol_rows = _group_by_symbol(equity_records)
      quality = _trade_quality(equity_records)

      total_option_contracts = sum(r["qty"] for r in option_records)
      total_pnl = (
          sum(r["pnl"] for r in equity_records)
          + sum(r["pnl"] for r in option_records)
      )

      _render_header(
          since_date=since_date,
          until_date=until_date,
          trading_mode=args.mode,
          strategy_version=strategy_version,
          equity_count=len(equity_records),
          option_count=total_option_contracts,
          total_pnl=total_pnl,
          report=report,
      )
      _render_daily_table(daily_rows)
      _render_strategy_table(equity_stats, per_strategy_sharpe)
      _render_symbol_attribution(symbol_rows)
      _render_trade_quality(quality)
      _render_funnel_summary(funnel_rows, since_date, until_date)
      _render_operational_health(audit_store, since_dt, until_dt)

      if args.csv_dir:
          _export_csv(equity_records, option_records, daily_rows, args.csv_dir)

      return 0


  # ── Pure computation helpers ──────────────────────────────────────────────────


  def _records_to_replay_trades(records: list[dict]) -> list[ReplayTradeRecord]:
      """Convert list_closed_trade_records dicts to ReplayTradeRecord objects."""
      result = []
      for r in records:
          entry = r["entry_price"]
          exit_ = r["exit_price"]
          qty = r["qty"]
          return_pct = (exit_ - entry) / entry if entry != 0.0 else 0.0
          exit_reason = "stop" if r.get("intent_type") == "stop" else "eod"
          result.append(ReplayTradeRecord(
              symbol=r["symbol"],
              entry_price=entry,
              exit_price=exit_,
              quantity=int(qty),
              entry_time=r["entry_time"],
              exit_time=r["exit_time"],
              exit_reason=exit_reason,
              pnl=r["pnl"],
              return_pct=return_pct,
          ))
      return result


  def _compute_per_strategy_sharpe(equity_records: list[dict]) -> dict[str, float | None]:
      """Compute annualized_sharpe per strategy from raw equity records."""
      by_strategy: dict[str, list[dict]] = defaultdict(list)
      for r in equity_records:
          by_strategy[r["strategy_name"]].append(r)
      result: dict[str, float | None] = {}
      for name, records in by_strategy.items():
          trades = _records_to_replay_trades(records)
          if trades:
              rpt = report_from_records(trades, starting_equity=100_000.0, strategy_name=name)
              result[name] = rpt.annualized_sharpe
          else:
              result[name] = None
      return result


  def _group_by_date(
      equity_records: list[dict],
      option_records: list[dict],
      market_timezone: str,
  ) -> list[dict]:
      """Group trade records by exit date; return rows sorted by date ascending.

      Each row: date, eq_pnl, opt_pnl, total_pnl, trade_count, win_count, cumul_pnl.
      """
      tz = ZoneInfo(market_timezone)
      by_date: dict[date, dict] = {}

      for r in equity_records:
          d = r["exit_time"].astimezone(tz).date()
          if d not in by_date:
              by_date[d] = {"date": d, "eq_pnl": 0.0, "opt_pnl": 0.0, "trade_count": 0, "win_count": 0}
          by_date[d]["eq_pnl"] += r["pnl"]
          by_date[d]["trade_count"] += 1
          if r["pnl"] > 0:
              by_date[d]["win_count"] += 1

      for r in option_records:
          d = r["closed_at"].astimezone(tz).date()
          if d not in by_date:
              by_date[d] = {"date": d, "eq_pnl": 0.0, "opt_pnl": 0.0, "trade_count": 0, "win_count": 0}
          by_date[d]["opt_pnl"] += r["pnl"]
          by_date[d]["trade_count"] += 1
          if r["pnl"] > 0:
              by_date[d]["win_count"] += 1

      rows = sorted(by_date.values(), key=lambda x: x["date"])
      cumul = 0.0
      for row in rows:
          row["total_pnl"] = row["eq_pnl"] + row["opt_pnl"]
          cumul += row["total_pnl"]
          row["cumul_pnl"] = cumul
      return rows


  def _group_by_symbol(equity_records: list[dict]) -> list[dict]:
      """Group equity records by symbol; return sorted by total_pnl descending.

      Each row: symbol, total_pnl, trade_count, win_count.
      """
      by_symbol: dict[str, dict] = {}
      for r in equity_records:
          sym = r["symbol"]
          if sym not in by_symbol:
              by_symbol[sym] = {"symbol": sym, "total_pnl": 0.0, "trade_count": 0, "win_count": 0}
          by_symbol[sym]["total_pnl"] += r["pnl"]
          by_symbol[sym]["trade_count"] += 1
          if r["pnl"] > 0:
              by_symbol[sym]["win_count"] += 1
      return sorted(by_symbol.values(), key=lambda x: x["total_pnl"], reverse=True)


  def _trade_quality(equity_records: list[dict]) -> dict:
      """Compute trade quality metrics from equity records.

      Returns: avg_winner, avg_loser, max_winner, max_loser, win_loss_ratio,
               stop_wins, stop_losses, eod_wins, eod_losses.
      """
      winners = [r["pnl"] for r in equity_records if r["pnl"] > 0]
      losers = [r["pnl"] for r in equity_records if r["pnl"] <= 0]

      avg_winner = sum(winners) / len(winners) if winners else None
      avg_loser = sum(losers) / len(losers) if losers else None
      max_winner = max(winners) if winners else None
      max_loser = min(losers) if losers else None
      win_loss_ratio = (
          avg_winner / abs(avg_loser)
          if (avg_winner is not None and avg_loser is not None and avg_loser != 0)
          else None
      )

      stop_wins = sum(1 for r in equity_records if r.get("intent_type") == "stop" and r["pnl"] > 0)
      stop_losses = sum(1 for r in equity_records if r.get("intent_type") == "stop" and r["pnl"] <= 0)
      eod_wins = sum(1 for r in equity_records if r.get("intent_type") != "stop" and r["pnl"] > 0)
      eod_losses = sum(1 for r in equity_records if r.get("intent_type") != "stop" and r["pnl"] <= 0)

      return {
          "avg_winner": avg_winner,
          "avg_loser": avg_loser,
          "max_winner": max_winner,
          "max_loser": max_loser,
          "win_loss_ratio": win_loss_ratio,
          "stop_wins": stop_wins,
          "stop_losses": stop_losses,
          "eod_wins": eod_wins,
          "eod_losses": eod_losses,
      }


  # ── Formatting helpers ────────────────────────────────────────────────────────


  def _fmt_pnl(v: float) -> str:
      return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


  # ── Section renderers ─────────────────────────────────────────────────────────


  def _render_header(
      *,
      since_date: date,
      until_date: date,
      trading_mode: str,
      strategy_version: str,
      equity_count: int,
      option_count: int,
      total_pnl: float,
      report: BacktestReport | None,
  ) -> None:
      header = f"Weekly Review — {since_date} to {until_date}  [{trading_mode} / {strategy_version}]"
      print()
      print(header)
      print("═" * len(header))
      days = (until_date - since_date).days + 1
      pnl_str = _fmt_pnl(total_pnl)
      print(
          f" Period: {days} days   "
          f"Equity trades: {equity_count}   "
          f"Option contracts: {option_count}   "
          f"Total P&L: {pnl_str}"
      )
      if report is not None:
          win_str = f"{report.win_rate:.0%}" if report.win_rate is not None else "—"
          sharpe_str = f"{report.annualized_sharpe:.2f}" if report.annualized_sharpe is not None else "—"
          pf_str = f"{report.profit_factor:.2f}" if report.profit_factor is not None else "—"
          dd_str = f"{report.max_drawdown_pct:.2%}" if report.max_drawdown_pct is not None else "—"
          print(
              f" Win rate: {win_str}   "
              f"Ann. Sharpe: {sharpe_str}   "
              f"Profit factor: {pf_str}   "
              f"Max DD: {dd_str}"
          )


  def _render_daily_table(rows: list[dict]) -> None:
      print()
      print(" Daily Breakdown")
      print(" " + "─" * 72)
      if not rows:
          print(" (no closed trades in period)")
          return
      print(
          f" {'Date':<12} {'Eq P&L':>10}  {'Opt P&L':>10}  "
          f"{'Total P&L':>10}  {'Trades':>6}  {'Win%':>5}  {'Cumul':>10}"
      )
      print(
          f" {'-'*12} {'-'*10}  {'-'*10}  "
          f"{'-'*10}  {'-'*6}  {'-'*5}  {'-'*10}"
      )
      for row in rows:
          eq_str = _fmt_pnl(row["eq_pnl"]) if row["eq_pnl"] != 0.0 else "—"
          opt_str = _fmt_pnl(row["opt_pnl"]) if row["opt_pnl"] != 0.0 else "—"
          win_pct = f"{row['win_count'] / row['trade_count']:.0%}" if row["trade_count"] else "—"
          print(
              f" {row['date'].isoformat():<12} {eq_str:>10}  {opt_str:>10}  "
              f"{_fmt_pnl(row['total_pnl']):>10}  {row['trade_count']:>6}  "
              f"{win_pct:>5}  {_fmt_pnl(row['cumul_pnl']):>10}"
          )


  def _render_strategy_table(
      equity_stats: list[EquityStrategyStats],
      per_strategy_sharpe: dict[str, float | None],
  ) -> None:
      print()
      print(" Equity Strategies")
      print(" " + "─" * 82)
      if not equity_stats:
          print(" (no closed equity trades in period)")
          return
      print(
          f" {'Strategy':<20} {'Trades':>6}  {'Win%':>5}  {'P&L':>10}  "
          f"{'PF':>5}  {'Expect%':>8}  {'AvgHold':>8}  {'AnnSharpe':>9}"
      )
      print(
          f" {'-'*20} {'-'*6}  {'-'*5}  {'-'*10}  "
          f"{'-'*5}  {'-'*8}  {'-'*8}  {'-'*9}"
      )
      for s in equity_stats:
          win_pct = f"{s.winning_trades / s.trades:.0%}" if s.trades else "—"
          pf_str = f"{s.profit_factor:.2f}" if s.profit_factor is not None else "—"
          exp_str = (
              (f"+{s.expectancy_pct:.2%}" if s.expectancy_pct >= 0 else f"{s.expectancy_pct:.2%}")
              if s.expectancy_pct is not None else "—"
          )
          hold_str = f"{s.avg_hold_minutes:.0f}min" if s.avg_hold_minutes is not None else "—"
          sharpe_val = per_strategy_sharpe.get(s.strategy_name)
          sharpe_str = f"{sharpe_val:.2f}" if sharpe_val is not None else "—"
          print(
              f" {s.strategy_name:<20} {s.trades:>6}  {win_pct:>5}  "
              f"{_fmt_pnl(s.total_pnl):>10}  {pf_str:>5}  {exp_str:>8}  "
              f"{hold_str:>8}  {sharpe_str:>9}"
          )


  def _render_symbol_attribution(symbol_rows: list[dict]) -> None:
      print()
      print(" Symbol Attribution (equity)")
      print(" " + "─" * 50)
      if not symbol_rows:
          print(" (no closed equity trades in period)")
          return
      top5 = symbol_rows[:5]
      bottom5 = symbol_rows[-5:] if len(symbol_rows) > 5 else []

      print(f" {'Top winners':<16} {'P&L':>10}  {'Trades':>6}  {'Win%':>5}")
      print(f" {'-'*16} {'-'*10}  {'-'*6}  {'-'*5}")
      for row in top5:
          win_pct = f"{row['win_count'] / row['trade_count']:.0%}" if row["trade_count"] else "—"
          print(f" {row['symbol']:<16} {_fmt_pnl(row['total_pnl']):>10}  {row['trade_count']:>6}  {win_pct:>5}")

      if bottom5:
          print()
          print(f" {'Bottom losers':<16} {'P&L':>10}  {'Trades':>6}  {'Win%':>5}")
          print(f" {'-'*16} {'-'*10}  {'-'*6}  {'-'*5}")
          for row in bottom5:
              win_pct = f"{row['win_count'] / row['trade_count']:.0%}" if row["trade_count"] else "—"
              print(f" {row['symbol']:<16} {_fmt_pnl(row['total_pnl']):>10}  {row['trade_count']:>6}  {win_pct:>5}")


  def _render_trade_quality(quality: dict) -> None:
      print()
      print(" Trade Quality")
      print(" " + "─" * 50)
      if quality["avg_winner"] is None and quality["avg_loser"] is None:
          print(" (no closed equity trades in period)")
          return

      avg_w = _fmt_pnl(quality["avg_winner"]) if quality["avg_winner"] is not None else "—"
      avg_l = _fmt_pnl(quality["avg_loser"]) if quality["avg_loser"] is not None else "—"
      ratio = f"{quality['win_loss_ratio']:.2f}×" if quality["win_loss_ratio"] is not None else "—"
      max_w = _fmt_pnl(quality["max_winner"]) if quality["max_winner"] is not None else "—"
      max_l = _fmt_pnl(quality["max_loser"]) if quality["max_loser"] is not None else "—"

      print(f" Avg winner: {avg_w:>9}   Avg loser: {avg_l:>9}   Ratio: {ratio}")
      print(f" Max winner: {max_w:>9}   Max loser: {max_l:>9}")
      print()
      print(" Exit breakdown:")
      print(f"   Stop wins:   {quality['stop_wins']:3d}   Stop losses:   {quality['stop_losses']:3d}")
      print(f"   EOD wins:    {quality['eod_wins']:3d}   EOD losses:    {quality['eod_losses']:3d}")
      total_losers = quality["stop_losses"] + quality["eod_losses"]
      if total_losers > 0:
          print()
          print(
              f" Loser analysis: {quality['stop_losses']} stopped out, "
              f"{quality['eod_losses']} held to EOD loss"
          )


  def _render_funnel_summary(rows: list[dict], since_date: date, until_date: date) -> None:
      print()
      header = f" Signal Funnel  {since_date} → {until_date}"
      print(header)
      print(" " + "─" * (len(header) - 1))
      if not rows:
          print(" (no decision_log data for period)")
          print()
          return
      col_w = 20
      num_w = 7
      print(
          f" {'Strategy':<{col_w}} "
          f"{'Eval':>{num_w}} "
          f"{'Signal':>{num_w}} "
          f"{'Filter':>{num_w}} "
          f"{'Sized':>{num_w}} "
          f"{'Accept':>{num_w}} "
          f"{'Rate':>{num_w}}"
      )
      print(f" {'-'*col_w} " + (f"{'-'*num_w} " * 6).rstrip())
      for row in rows:
          name = row["strategy_name"] or "(unknown)"
          rate = f"{row['accepted'] / row['evaluated']:.1%}" if row["evaluated"] else "—"
          print(
              f" {name:<{col_w}} "
              f"{row['evaluated']:>{num_w}} "
              f"{row['signal_fired']:>{num_w}} "
              f"{row['passed_entry_filter']:>{num_w}} "
              f"{row['sized']:>{num_w}} "
              f"{row['accepted']:>{num_w}} "
              f"{rate:>{num_w}}"
          )
      print()


  def _render_operational_health(
      audit_store: AuditEventStore,
      since_dt: datetime,
      until_dt: datetime,
  ) -> None:
      print()
      print(" Operational Health")
      print(" " + "─" * 50)
      cycles = audit_store.list_by_event_types(
          event_types=["supervisor_cycle"],
          since=since_dt,
          until=until_dt,
          limit=10000,
      )
      errors = audit_store.list_by_event_types(
          event_types=["supervisor_cycle_error", "strategy_cycle_error"],
          since=since_dt,
          until=until_dt,
          limit=10000,
      )
      dispatch_failures = audit_store.list_by_event_types(
          event_types=["order_dispatch_failed", "option_order_dispatch_failed"],
          since=since_dt,
          until=until_dt,
          limit=10000,
      )
      skipped_events = audit_store.list_by_event_types(
          event_types=["cycle_intent_skipped"],
          since=since_dt,
          until=until_dt,
          limit=10000,
      )
      options_skipped = sum(
          1 for e in skipped_events if e.payload.get("reason") == "options_market_closed"
      )
      print(f" Total cycles:       {len(cycles):>6}")
      print(f" Cycle errors:       {len(errors):>6}")
      print(f" Dispatch failures:  {len(dispatch_failures):>6}")
      print(f" Skipped exits (OCC):{options_skipped:>6}")
      print()


  def _export_csv(
      equity_records: list[dict],
      option_records: list[dict],
      daily_rows: list[dict],
      csv_dir: str,
  ) -> None:
      os.makedirs(csv_dir, exist_ok=True)

      equity_path = os.path.join(csv_dir, "equity_trades.csv")
      equity_fields = ["symbol", "strategy_name", "qty", "entry_price", "exit_price",
                       "entry_time", "exit_time", "pnl", "hold_seconds"]
      with open(equity_path, "w", newline="") as f:
          w = csv.DictWriter(f, fieldnames=equity_fields, extrasaction="ignore")
          w.writeheader()
          w.writerows(equity_records)
      print(f" Wrote {len(equity_records)} rows → {equity_path}")

      option_path = os.path.join(csv_dir, "option_trades.csv")
      option_fields = ["occ_symbol", "underlying", "strategy_name", "qty",
                       "premium_collected", "close_cost", "pnl", "opened_at", "closed_at"]
      with open(option_path, "w", newline="") as f:
          w = csv.DictWriter(f, fieldnames=option_fields, extrasaction="ignore")
          w.writeheader()
          w.writerows(option_records)
      print(f" Wrote {len(option_records)} rows → {option_path}")

      daily_path = os.path.join(csv_dir, "daily_pnl.csv")
      with open(daily_path, "w", newline="") as f:
          w = csv.writer(f)
          w.writerow(["date", "eq_pnl", "opt_pnl", "total_pnl", "trade_count", "win_count", "cumul_pnl"])
          for row in daily_rows:
              w.writerow([
                  row["date"].isoformat(),
                  f"{row['eq_pnl']:.2f}",
                  f"{row['opt_pnl']:.2f}",
                  f"{row['total_pnl']:.2f}",
                  row["trade_count"],
                  row["win_count"],
                  f"{row['cumul_pnl']:.2f}",
              ])
      print(f" Wrote {len(daily_rows)} rows → {daily_path}")
  ```

- [ ] **Step 2: Run the 6 new tests to confirm they pass**

  ```bash
  pytest tests/unit/test_weekly_review_cli.py -v
  ```
  Expected output:
  ```
  PASSED tests/unit/test_weekly_review_cli.py::test_group_by_date_groups_trades_by_exit_date
  PASSED tests/unit/test_weekly_review_cli.py::test_group_by_symbol_sorts_by_pnl_descending
  PASSED tests/unit/test_weekly_review_cli.py::test_trade_quality_win_loss_ratio
  PASSED tests/unit/test_weekly_review_cli.py::test_symbol_attribution_top_bottom_5
  PASSED tests/unit/test_weekly_review_cli.py::test_loser_analysis_counts_stop_vs_eod
  PASSED tests/unit/test_weekly_review_cli.py::test_weekly_review_no_trades_prints_no_data
  6 passed
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add src/alpaca_bot/admin/weekly_review_cli.py tests/unit/test_weekly_review_cli.py
  git commit -m "feat: add weekly_review_cli with 7-section report and unit tests"
  ```

---

## Task 4: Register entry point in `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the entry point**

  In `pyproject.toml`, find the `[project.scripts]` section. After the `alpaca-bot-funnel-report` line, add:

  ```toml
  alpaca-bot-weekly-review = "alpaca_bot.admin.weekly_review_cli:main"
  ```

  The section should now contain (showing context):
  ```toml
  alpaca-bot-funnel-report = "alpaca_bot.admin.funnel_report_cli:main"
  alpaca-bot-weekly-review = "alpaca_bot.admin.weekly_review_cli:main"
  alpaca-bot-nightly = "alpaca_bot.nightly.cli:main"
  ```

- [ ] **Step 2: Reinstall to pick up the new entry point**

  ```bash
  pip install -e ".[dev]" -q
  ```

- [ ] **Step 3: Smoke-test the entry point exists**

  ```bash
  alpaca-bot-weekly-review --help
  ```
  Expected: argparse help output starting with `usage: alpaca-bot-weekly-review`.

- [ ] **Step 4: Commit**

  ```bash
  git add pyproject.toml
  git commit -m "chore: register alpaca-bot-weekly-review entry point"
  ```

---

## Task 5: Full test suite and verification

- [ ] **Step 1: Run the full test suite**

  ```bash
  pytest -x -q
  ```
  Expected: all tests pass (no new failures; existing tests are unaffected by the additive `intent_type` key).

- [ ] **Step 2: Verify all 6 new tests are included in the count**

  ```bash
  pytest tests/unit/test_weekly_review_cli.py tests/unit/test_repositories.py -v
  ```
  Expected: 7 tests passing (6 weekly-review + 1 intent_type test).

---

## Self-Review

**Spec coverage check:**
- [x] Section 1 (Header / Period Summary) → `_render_header`
- [x] Section 2 (Day-by-Day Table) → `_group_by_date` + `_render_daily_table`
- [x] Section 3 (Per-Strategy Stats + AnnSharpe) → `compute_equity_stats` + `_compute_per_strategy_sharpe` + `_render_strategy_table`
- [x] Section 4 (Symbol Attribution) → `_group_by_symbol` + `_render_symbol_attribution`
- [x] Section 5 (Trade Quality) → `_trade_quality` + `_render_trade_quality`
- [x] Section 6 (Signal Funnel) → `funnel_by_strategy` + `_render_funnel_summary`
- [x] Section 7 (Operational Health) → `_render_operational_health`
- [x] CLI args: `--days`, `--since`, `--until`, `--mode`, `--strategy-version`, `--csv-dir`
- [x] CSV export: `_export_csv` (equity_trades.csv, option_trades.csv, daily_pnl.csv)
- [x] No-trades path: all render functions handle empty input
- [x] `intent_type` field: Task 1 adds it to `list_closed_trade_records()`

**Placeholder scan:** None — all steps have complete code.

**Type consistency:**
- `_group_by_date` returns `list[dict]` with keys: `date, eq_pnl, opt_pnl, total_pnl, trade_count, win_count, cumul_pnl` — used consistently in `_render_daily_table` and `_export_csv`
- `_group_by_symbol` returns `list[dict]` with keys: `symbol, total_pnl, trade_count, win_count` — used consistently in `_render_symbol_attribution`
- `_trade_quality` returns `dict` with 9 keys — all referenced by name in `_render_trade_quality`; the same keys are used in the no-trades test fixture
- `_compute_per_strategy_sharpe` returns `dict[str, float | None]` — accessed via `.get(s.strategy_name)` in `_render_strategy_table`
