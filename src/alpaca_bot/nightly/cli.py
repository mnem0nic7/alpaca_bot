from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from alpaca_bot.admin.session_eval_cli import _row_to_trade_record
from alpaca_bot.backfill.fetcher import BackfillFetcher
from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaCredentialsError, AlpacaMarketDataAdapter
from alpaca_bot.replay.report import BacktestReport, report_from_records
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.splitter import split_scenario
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.models import EQUITY_SESSION_STATE_STRATEGY_NAME
from alpaca_bot.storage.repositories import (
    DailySessionStateStore,
    OrderStore,
    TuningResultStore,
    WatchlistStore,
)
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.tuning.cli import _format_env_block, _print_top_candidates, _print_walk_forward_block
from alpaca_bot.tuning.surrogate import SurrogateModel
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    _viability_key,
    evaluate_candidates_oos,
    run_multi_scenario_sweep,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-nightly")
    parser.add_argument("--trading-mode", choices=["paper", "live"],
                        help="Override TRADING_MODE env var")
    parser.add_argument("--days", type=int, default=252,
                        help="Lookback trading days for backfill (default: 252)")
    parser.add_argument("--report-days", type=int, default=20,
                        help="Lookback weekdays for rolling live report (default: 20)")
    parser.add_argument("--output-dir", default="/data/scenarios",
                        help="Directory for scenario JSON files (default: /data/scenarios)")
    parser.add_argument("--output-env", metavar="FILE",
                        help="Path to write winning candidate env block")
    parser.add_argument("--validate-pct", type=float, default=0.2,
                        help="OOS fraction for walk-forward gate (default: 0.2)")
    parser.add_argument("--min-oos-score", type=float, default=0.2,
                        help="Minimum absolute OOS score to accept a candidate (default: 0.2)")
    parser.add_argument("--oos-gate-ratio", type=float, default=0.6,
                        help="Required OOS/IS score ratio to hold a candidate (default: 0.6)")
    parser.add_argument("--max-drawdown-pct", type=float, default=0.0,
                        help="Maximum allowed IS/OOS drawdown to accept a candidate (0.0 = disabled)")
    parser.add_argument("--max-trades", type=int, default=0,
                        help="Maximum trades per scenario to accept a candidate (0 = disabled)")
    parser.add_argument("--strategy", default="breakout",
                        choices=list(STRATEGY_REGISTRY),
                        help="Strategy grid to sweep (default: breakout)")
    parser.add_argument("--no-db", action="store_true",
                        help="Skip persisting results to tuning_results")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip Alpaca API calls; use existing scenario files in --output-dir")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    base_env = dict(os.environ)
    if args.trading_mode:
        base_env["TRADING_MODE"] = args.trading_mode

    try:
        settings = Settings.from_env(base_env)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    trading_mode = settings.trading_mode
    strategy_version = settings.strategy_version
    signal_evaluator = STRATEGY_REGISTRY[args.strategy]
    grid = STRATEGY_GRIDS.get(args.strategy, DEFAULT_GRID)
    output_dir = Path(args.output_dir)
    now = datetime.now(timezone.utc)

    conn = connect_postgres(settings.database_url)
    try:
        # ── Watchlist ────────────────────────────────────────────────────────
        watchlist_store = WatchlistStore(conn)
        symbols: list[str] = watchlist_store.list_enabled(trading_mode.value)

        # ── Backfill ─────────────────────────────────────────────────────────
        print("\n── Backfill ─────────────────────────────────────────────────────────")
        if not symbols:
            print("Warning: no enabled symbols in watchlist — skipping backfill and evolve.")
        elif args.dry_run:
            print(f"Symbols: {len(symbols)} (dry-run — skipping Alpaca API calls)")
        else:
            try:
                adapter = AlpacaMarketDataAdapter.from_settings(settings)
            except AlpacaCredentialsError as exc:
                print(f"Alpaca credentials error: {exc}", file=sys.stderr)
                return 1
            fetcher = BackfillFetcher(adapter, settings)
            results = fetcher.fetch_and_save(
                symbols=symbols, days=args.days, output_dir=output_dir
            )
            if not results:
                print(
                    f"Error: backfill produced 0 scenario files for {len(symbols)} symbols. "
                    "Check data availability and credentials.",
                    file=sys.stderr,
                )
                return 1
            print(f"Symbols: {len(symbols)} (from watchlist, {trading_mode.value} mode)")
            for path, n_intraday, n_daily in results:
                print(f"  {path.stem}: {n_intraday} intraday, {n_daily} daily bars")
            print(f"Wrote {len(results)} scenario files to {output_dir}")

        # ── Evolve ───────────────────────────────────────────────────────────
        if symbols:
            print("\n── Evolve ───────────────────────────────────────────────────────────")
            files = sorted(output_dir.glob("*.json"))
            if len(files) < 2:
                print(
                    f"Error: need at least 2 scenario files in {output_dir}; "
                    f"found {len(files)}. Run without --dry-run or add scenario files.",
                    file=sys.stderr,
                )
                return 1

            all_scenarios = [ReplayRunner.load_scenario(f) for f in files]
            is_scenarios = []
            oos_scenarios = []
            for s in all_scenarios:
                is_s, oos_s = split_scenario(s, in_sample_ratio=1.0 - args.validate_pct)
                is_scenarios.append(is_s)
                oos_scenarios.append(oos_s)

            oos_pct_int = round(args.validate_pct * 100)
            total_combos = 1
            for vals in grid.values():
                total_combos *= len(vals)
            print(
                f"Scenarios: {len(all_scenarios)} × IS/OOS split "
                f"({100 - oos_pct_int}% / {oos_pct_int}%)"
            )
            print(
                f"Grid: {args.strategy} "
                f"({total_combos} combinations × {len(is_scenarios)} scenarios)"
            )

            tuning_store = TuningResultStore(conn)
            try:
                historical = tuning_store.load_all_scored(trading_mode=trading_mode.value)
            except Exception as exc:
                print(f"Warning: could not load tuning history for surrogate: {exc}",
                      file=sys.stderr)
                historical = []
            grid_keys = set(grid.keys())
            historical = [r for r in historical if set(r["params"].keys()) == grid_keys]
            surrogate = SurrogateModel()
            surrogate_fitted = surrogate.fit(historical)
            if surrogate_fitted:
                print(f"Surrogate: fitted on {len(historical)} historical records")
            else:
                print(f"Surrogate: cold start ({len(historical)} records < 50 — full grid)")

            candidates = run_multi_scenario_sweep(
                scenarios=is_scenarios,
                base_env=base_env,
                grid=grid,
                max_drawdown_pct=args.max_drawdown_pct,
                max_trades=args.max_trades,
                signal_evaluator=signal_evaluator,
                surrogate=surrogate,
            )
            scored = [c for c in candidates if c.score is not None]
            print(f"Scored: {len(scored)} / {len(candidates)} candidates")

            _print_top_candidates(scored[:10])

            top10 = scored[:10]
            oos_scores = evaluate_candidates_oos(
                candidates=top10,
                oos_scenarios=oos_scenarios,
                base_env=base_env,
                min_trades=3,
                max_drawdown_pct=args.max_drawdown_pct,
                max_trades=args.max_trades,
                signal_evaluator=signal_evaluator,
            )
            _print_walk_forward_block(
                top10, oos_scores,
                validate_pct=args.validate_pct,
                aggregate="min",
                oos_gate_ratio=args.oos_gate_ratio,
                min_oos_score=args.min_oos_score,
            )

            held_pairs = [
                (c, s) for c, s in zip(top10, oos_scores)
                if s is not None
                and c.score is not None
                and s >= c.score * args.oos_gate_ratio
                and s >= args.min_oos_score
            ]
            print(f"Walk-forward: {len(held_pairs)} / {len(top10)} held")

            scenario_name = "+".join(s.name for s in all_scenarios)

            if held_pairs:
                best = max(held_pairs, key=lambda pair: _viability_key(pair[0], pair[1]))[0]
                env_block = _format_env_block(best, now)
                print(f"\n{env_block}")
                if args.output_env:
                    Path(args.output_env).write_text(env_block + "\n")
                    print(f"Candidate env written to {args.output_env}")
                if not args.no_db:
                    try:
                        run_id = tuning_store.save_run(
                            scenario_name=scenario_name,
                            trading_mode=trading_mode.value,
                            candidates=candidates,
                            created_at=now,
                        )
                        print(f"Results saved to DB (run_id={run_id})")
                    except Exception as exc:
                        print(f"Warning: could not save tuning results: {exc}", file=sys.stderr)
            else:
                print("\nNo walk-forward held candidates — current parameters remain active.")

        # ── Rolling live report ───────────────────────────────────────────────
        print(
            f"\n── Live Performance (last {args.report_days} trading days) "
            "─────────────────────────────────────"
        )
        report_dates = _weekdays_back(args.report_days)
        oldest_date = report_dates[-1] if report_dates else date.today()

        session_store = DailySessionStateStore(conn)
        try:
            state = session_store.load(
                session_date=oldest_date,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
            )
            starting_equity = (
                state.equity_baseline
                if (state and state.equity_baseline is not None)
                else 100_000.0
            )
        except Exception:
            starting_equity = 100_000.0

        order_store = OrderStore(conn)
        all_trade_records = []
        for d in report_dates:
            try:
                rows = order_store.list_closed_trades(
                    trading_mode=trading_mode,
                    strategy_version=strategy_version,
                    session_date=d,
                )
                all_trade_records.extend(_row_to_trade_record(row) for row in rows)
            except Exception as exc:
                print(f"Warning: could not load trades for {d}: {exc}", file=sys.stderr)

        if not all_trade_records:
            print(f"No closed trades found in the last {args.report_days} trading days.")
        else:
            report = report_from_records(
                all_trade_records,
                starting_equity=starting_equity,
                strategy_name="all",
            )
            _print_rolling_report(report, report_days=args.report_days)

    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

    return 0


def _weekdays_back(n: int) -> list[date]:
    """Return list of n weekdays (Mon–Fri) ending yesterday, most recent first."""
    result: list[date] = []
    d = date.today() - timedelta(days=1)
    while len(result) < n:
        if d.weekday() < 5:  # 0=Mon … 4=Fri
            result.append(d)
        d -= timedelta(days=1)
    return result


def _print_rolling_report(report: BacktestReport, *, report_days: int) -> None:
    win_rate_str = f"{report.win_rate:.1%}" if report.win_rate is not None else "—"
    pnl = sum(t.pnl for t in report.trades)
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    sharpe_str = f"{report.sharpe_ratio:.2f}" if report.sharpe_ratio is not None else "—"
    pf_str = f"{report.profit_factor:.2f}" if report.profit_factor is not None else "—"
    mean_str = (
        (f"+{report.mean_return_pct:.2%}" if report.mean_return_pct >= 0
         else f"{report.mean_return_pct:.2%}")
        if report.mean_return_pct is not None else "—"
    )
    dd_str = f"{report.max_drawdown_pct:.1%}" if report.max_drawdown_pct is not None else "—"
    hold_str = f"{report.avg_hold_minutes:.0f}min" if report.avg_hold_minutes is not None else "—"
    sharpe_gate = "✓" if (report.sharpe_ratio is not None and report.sharpe_ratio > 0) else "✗"
    pf_gate = "✓" if (report.profit_factor is not None and report.profit_factor >= 1.0) else "✗"
    trades_gate = "✓" if report.total_trades >= 3 else "✗"
    print(f"Trades: {report.total_trades:>5d}  Wins: {report.winning_trades:>2d}  "
          f"Losses: {report.losing_trades:>2d}  Win rate: {win_rate_str}")
    print(f"P&L:   {pnl_str:>9s}  Sharpe: {sharpe_str:>5s}  Prof.fac: {pf_str:>5s}")
    print(f"Mean:  {mean_str:>9s}  Max DD: {dd_str:>5s}  Avg hold: {hold_str}")
    print(f"MaxCL: {report.max_consecutive_losses:>2d}         MaxCW: {report.max_consecutive_wins:>2d}")
    print(f"Gates: {sharpe_gate} Sharpe > 0  {pf_gate} Profit factor ≥ 1.0  {trades_gate} Trades ≥ 3")
