from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from alpaca_bot.admin.session_eval_cli import _row_to_trade_record
from alpaca_bot.backfill.fetcher import BackfillFetcher
from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import (
    AlpacaCredentialsError,
    AlpacaExecutionAdapter,
    AlpacaMarketDataAdapter,
)
from alpaca_bot.replay.report import BacktestReport, report_from_records
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.splitter import split_scenario
from alpaca_bot.risk.confidence import compute_confidence_scores
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.models import EQUITY_SESSION_STATE_STRATEGY_NAME, AuditEvent
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    ConfidenceFloorStore,
    DailySessionStateStore,
    DecisionLogStore,
    OrderStore,
    StrategyFlagStore,
    StrategyWeightStore,
    TuningResultStore,
    WatchlistStore,
)
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.tuning.surrogate import SurrogateModel
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    TuningCandidate,
    _pooled_report,
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
    parser.add_argument("--max-combos", type=int, default=0,
                        help="Maximum grid combinations to evaluate per strategy (0 = all)")
    parser.add_argument("--proof-guard", action="store_true",
                        help=(
                            "Before writing a held candidate, require it to preserve "
                            "current-parameter proof-horizon metrics on the active "
                            "scenario set"
                        ))
    parser.add_argument(
        "--strategies",
        default="enabled",
        help=(
            "Comma-separated strategy names, 'all', or 'enabled' "
            "(default: enabled; falls back to all when no flags exist)"
        ),
    )
    parser.add_argument("--no-db", action="store_true",
                        help="Skip persisting results to tuning_results")
    parser.add_argument("--prune-keep-days", type=int, default=30,
                        help="Prune decision_log rows older than N days after the "
                             "report (default: 30; 0 disables)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip Alpaca API calls; use existing scenario files in --output-dir")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    if args.max_combos < 0:
        print("--max-combos must be non-negative", file=sys.stderr)
        return 1

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
    output_dir = Path(args.output_dir)
    run_started_at = datetime.now(timezone.utc)
    account_equity = _resolve_account_equity(settings=settings, dry_run=args.dry_run)

    conn = connect_postgres(settings.database_url)
    try:
        # ── Watchlist ────────────────────────────────────────────────────────
        watchlist_store = WatchlistStore(conn)
        enabled_symbols: list[str] = watchlist_store.list_enabled(trading_mode.value)
        list_ignored = getattr(watchlist_store, "list_ignored", None)
        ignored_symbols = (
            set(list_ignored(trading_mode.value))
            if callable(list_ignored)
            else set()
        )
        symbols = [symbol for symbol in enabled_symbols if symbol not in ignored_symbols]

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
                symbols=symbols,
                days=args.days,
                output_dir=output_dir,
                starting_equity=account_equity or 100_000.0,
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
            files, missing_scenario_symbols = _scenario_files_for_symbols(
                output_dir, symbols
            )
            if missing_scenario_symbols:
                examples = ", ".join(missing_scenario_symbols[:20])
                examples_suffix = f" ({examples})" if examples else ""
                print(
                    f"Error: missing active-symbol scenario files in {output_dir}: "
                    f"{len(missing_scenario_symbols)} missing{examples_suffix}. "
                    "Run without --dry-run or refresh scenario files.",
                    file=sys.stderr,
                )
                return 1
            if len(files) < 2:
                print(
                    f"Error: need at least 2 active-symbol scenario files in {output_dir}; "
                    f"found {len(files)} for {len(symbols)} active symbols. "
                    "Run without --dry-run or refresh scenario files.",
                    file=sys.stderr,
                )
                return 1

            enabled_strategy_names = (
                _load_enabled_strategy_names(conn=conn, settings=settings)
                if args.strategies.strip().lower() == "enabled"
                else ()
            )
            strategy_names = _resolve_strategies(
                args.strategies,
                enabled_strategy_names=enabled_strategy_names,
            )
            fractionable_symbols = _resolve_fractionable_symbols(
                settings=settings,
                symbols=symbols,
                dry_run=args.dry_run,
            )
            confidence_floor = _load_confidence_floor(conn=conn, settings=settings)
            confidence_scores = _load_strategy_confidence_scores(
                conn=conn,
                settings=settings,
                confidence_floor=confidence_floor,
            )
            all_scenarios = [ReplayRunner.load_scenario(f) for f in files]
            is_scenarios = []
            oos_scenarios = []
            for s in all_scenarios:
                is_s, oos_s = split_scenario(s, in_sample_ratio=1.0 - args.validate_pct)
                is_scenarios.append(is_s)
                oos_scenarios.append(oos_s)

            scenario_name_base = "+".join(s.name for s in all_scenarios)
            oos_pct_int = round(args.validate_pct * 100)
            print(
                f"Scenarios: {len(all_scenarios)} × IS/OOS split "
                f"({100 - oos_pct_int}% / {oos_pct_int}%)"
            )
            print(f"Strategies: {', '.join(strategy_names)}")

            tuning_store = TuningResultStore(conn)
            try:
                all_historical = tuning_store.load_all_scored(trading_mode=trading_mode.value)
            except Exception as exc:
                print(f"Warning: could not load tuning history for surrogate: {exc}",
                      file=sys.stderr)
                all_historical = []

            winners: list[tuple[str, TuningCandidate, float]] = []

            for strat_name in strategy_names:
                grid = STRATEGY_GRIDS.get(strat_name, DEFAULT_GRID)
                signal_evaluator = STRATEGY_REGISTRY[strat_name]
                strategy_equity = _effective_strategy_equity(
                    account_equity=account_equity,
                    confidence_scores=confidence_scores,
                    confidence_floor=confidence_floor,
                    strategy_name=strat_name,
                )
                if strategy_equity is not None:
                    print(
                        f"  [{strat_name}] replay sizing equity="
                        f"${strategy_equity:,.2f}"
                    )
                strat_all_scenarios = _with_starting_equity(
                    all_scenarios,
                    strategy_equity,
                )
                strat_is_scenarios = _with_starting_equity(
                    is_scenarios,
                    strategy_equity,
                )
                strat_oos_scenarios = _with_starting_equity(
                    oos_scenarios,
                    strategy_equity,
                )

                grid_keys = set(grid.keys())
                historical = [r for r in all_historical if set(r["params"].keys()) == grid_keys]
                surrogate = SurrogateModel()
                surrogate_fitted = surrogate.fit(historical)
                if surrogate_fitted:
                    print(f"  [{strat_name}] surrogate: fitted on {len(historical)} records")

                candidates = run_multi_scenario_sweep(
                    scenarios=strat_is_scenarios,
                    base_env=base_env,
                    grid=grid,
                    aggregate="pooled",
                    min_trades_per_scenario=3,
                    max_drawdown_pct=args.max_drawdown_pct,
                    max_trades=args.max_trades,
                    signal_evaluator=signal_evaluator,
                    surrogate=surrogate,
                    max_combos=args.max_combos,
                    on_progress=lambda msg, strat=strat_name: print(
                        f"  [{strat}] {msg}"
                    ),
                    fractionable_symbols=fractionable_symbols,
                )
                scored = [c for c in candidates if c.score is not None]

                top10 = scored[:10]
                if not top10:
                    print(f"  [{strat_name}] no scored candidates — skipped")
                    continue

                oos_scores = evaluate_candidates_oos(
                    candidates=top10,
                    oos_scenarios=strat_oos_scenarios,
                    base_env=base_env,
                    min_trades=3,
                    aggregate="pooled",
                    max_drawdown_pct=args.max_drawdown_pct,
                    max_trades=args.max_trades,
                    signal_evaluator=signal_evaluator,
                    fractionable_symbols=fractionable_symbols,
                )

                held_pairs = [
                    (c, s) for c, s in zip(top10, oos_scores)
                    if s is not None
                    and c.score is not None
                    and s >= c.score * args.oos_gate_ratio
                    and s >= args.min_oos_score
                ]

                if not args.no_db and candidates:
                    try:
                        run_id = tuning_store.save_run(
                            scenario_name=f"{scenario_name_base} [{strat_name}]",
                            trading_mode=trading_mode.value,
                            candidates=candidates,
                            created_at=run_started_at,
                        )
                        print(f"  [{strat_name}] DB run_id={run_id}")
                    except Exception as exc:
                        print(f"Warning: could not save tuning results ({strat_name}): {exc}",
                              file=sys.stderr)

                if held_pairs:
                    if args.proof_guard:
                        guarded = _select_proof_guarded_candidate(
                            held_pairs=held_pairs,
                            scenarios=strat_all_scenarios,
                            base_env=base_env,
                            signal_evaluator=signal_evaluator,
                            strategy_name=strat_name,
                            fractionable_symbols=fractionable_symbols,
                        )
                    else:
                        guarded = max(
                            held_pairs,
                            key=lambda p: _viability_key(p[0], p[1]),
                        )
                    if guarded is not None:
                        best, best_oos = guarded
                        winners.append((strat_name, best, best_oos))

            _print_strategy_results(winners, strategy_names, all_scenarios)

            if not args.no_db and winners:
                try:
                    weight_computed_at = datetime.now(timezone.utc)
                    _publish_winner_strategy_weights(
                        conn=conn,
                        settings=settings,
                        strategy_names=strategy_names,
                        winners=winners,
                        computed_at=weight_computed_at,
                    )
                except Exception as exc:
                    rollback = getattr(conn, "rollback", None)
                    if callable(rollback):
                        rollback()
                    print(
                        f"Warning: could not publish nightly strategy weights: {exc}",
                        file=sys.stderr,
                    )

            env_written = False
            if winners:
                composite_params = _build_composite_env(winners)
                env_block = _format_composite_env_block(
                    composite_params, winners[0][0], run_started_at
                )
                print(f"\n{env_block}")
                if args.output_env:
                    Path(args.output_env).write_text(env_block + "\n")
                    print(f"Candidate env written to {args.output_env}")
                    env_written = True
            else:
                print("\nNo walk-forward held candidates across all strategies — current parameters remain active.")
                if args.output_env:
                    output_env = Path(args.output_env)
                    if output_env.exists():
                        output_env.unlink()
                        print(f"Removed stale candidate env at {args.output_env}")

            best_strat = winners[0][0] if winners else None
            best_score = winners[0][2] if winners else None
            try:
                completed_at = datetime.now(timezone.utc)
                AuditEventStore(conn).append(
                    AuditEvent(
                        event_type="nightly_sweep_completed",
                        payload={
                            "strategy_count": len(strategy_names),
                            "candidates_accepted": len(winners),
                            "best_strategy": best_strat,
                            "best_score": best_score,
                            "candidate_env_written": env_written,
                            "proof_guard_enabled": args.proof_guard,
                            "run_started_at": run_started_at.isoformat(),
                            "completed_at": completed_at.isoformat(),
                        },
                        created_at=completed_at,
                    )
                )
            except Exception as exc:
                print(
                    f"Warning: could not write nightly_sweep_completed audit event: {exc}",
                    file=sys.stderr,
                )

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

        # ── Decision log retention ────────────────────────────────────────────
        if not args.no_db and args.prune_keep_days > 0:
            try:
                pruned_at = datetime.now(timezone.utc)
                deleted = DecisionLogStore(conn).prune(
                    older_than_days=args.prune_keep_days, now=pruned_at
                )
                AuditEventStore(conn).append(
                    AuditEvent(
                        event_type="decision_log_pruned",
                        payload={
                            "deleted_count": deleted,
                            "keep_days": args.prune_keep_days,
                            "source": "nightly",
                        },
                        created_at=pruned_at,
                    )
                )
                print(
                    f"\nDecision log pruned: {deleted} rows older than "
                    f"{args.prune_keep_days} days removed."
                )
            except Exception as exc:
                print(f"Warning: decision_log prune failed: {exc}", file=sys.stderr)

    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

    return 0


@dataclass(frozen=True)
class _ProofGuardMetrics:
    trades: int
    total_pnl: float
    eventual_pass_rate: float | None
    first_threshold_pass_rate: float | None
    p95_sessions_to_pass: int | None
    slowest_sessions_to_pass: int | None


def _select_proof_guarded_candidate(
    *,
    held_pairs: Sequence[tuple[TuningCandidate, float]],
    scenarios: list,
    base_env: dict[str, str],
    signal_evaluator,
    strategy_name: str,
    fractionable_symbols: frozenset[str] | None = None,
) -> tuple[TuningCandidate, float] | None:
    """Return the first held candidate that does not regress proof metrics."""

    min_trades, min_pnl = _resolve_proof_guard_thresholds(base_env)
    base_settings = _settings_with_fractionable(
        Settings.from_env(base_env),
        fractionable_symbols=fractionable_symbols,
    )
    baseline_report = _pooled_report(
        scenarios=scenarios,
        settings=base_settings,
        signal_evaluator=signal_evaluator,
    )
    if baseline_report is None:
        print(
            f"  [{strategy_name}] proof guard: no baseline report - "
            f"rejecting {len(held_pairs)} held candidate(s)"
        )
        return None
    baseline = _proof_guard_metrics(
        scenarios=scenarios,
        report=baseline_report,
        settings=base_settings,
        min_trades=min_trades,
        min_pnl=min_pnl,
    )
    print(f"  [{strategy_name}] proof guard baseline: {_format_proof_guard_metrics(baseline)}")

    ranked_pairs = sorted(
        held_pairs,
        key=lambda p: _viability_key(p[0], p[1]),
        reverse=True,
    )
    print(
        f"  [{strategy_name}] proof guard: evaluating {len(ranked_pairs)} "
        f"held candidate(s) against min_trades={min_trades} min_pnl={min_pnl:.2f}"
    )

    rejected = 0
    for index, (candidate, oos_score) in enumerate(ranked_pairs, start=1):
        print(
            f"  [{strategy_name}] proof guard checking {index}/{len(ranked_pairs)} "
            f"params={candidate.params}"
        )
        candidate_settings = _settings_with_fractionable(
            Settings.from_env({**base_env, **candidate.params}),
            fractionable_symbols=fractionable_symbols,
        )
        candidate_report = _pooled_report(
            scenarios=scenarios,
            settings=candidate_settings,
            signal_evaluator=signal_evaluator,
        )
        if candidate_report is None:
            rejected += 1
            print(
                f"  [{strategy_name}] proof guard rejected params={candidate.params}: "
                "no candidate report"
            )
            continue
        metrics = _proof_guard_metrics(
            scenarios=scenarios,
            report=candidate_report,
            settings=candidate_settings,
            min_trades=min_trades,
            min_pnl=min_pnl,
        )
        regressions = _proof_guard_regressions(baseline=baseline, candidate=metrics)
        if not regressions:
            print(
                f"  [{strategy_name}] proof guard accepted params={candidate.params}: "
                f"{_format_proof_guard_metrics(metrics)}"
            )
            return candidate, oos_score
        rejected += 1
        print(
            f"  [{strategy_name}] proof guard rejected params={candidate.params}: "
            + "; ".join(regressions)
            + f" (candidate {_format_proof_guard_metrics(metrics)})"
        )
    print(
        f"  [{strategy_name}] proof guard: rejected all {rejected} "
        f"held candidate(s)"
    )
    return None


def _resolve_proof_guard_thresholds(base_env: dict[str, str]) -> tuple[int, float]:
    scale_min_trades = _positive_int_env(
        base_env, "PAPER_SCALE_MIN_TRADES", default="30"
    )
    min_trades = _positive_int_env(
        base_env,
        "PROFIT_PROBE_MIN_TRADES",
        default=str(scale_min_trades),
    )
    return max(min_trades, scale_min_trades), float(
        base_env.get("PROFIT_PROBE_MIN_PNL", "0.01")
    )


def _positive_int_env(base_env: dict[str, str], name: str, *, default: str) -> int:
    raw = base_env.get(name, default)
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _settings_with_fractionable(
    settings: Settings,
    *,
    fractionable_symbols: frozenset[str] | None,
) -> Settings:
    if fractionable_symbols is None:
        return settings
    return replace(settings, fractionable_symbols=fractionable_symbols)


def _resolve_fractionable_symbols(
    *,
    settings: Settings,
    symbols: Sequence[str],
    dry_run: bool,
) -> frozenset[str]:
    symbol_tuple = tuple(symbols)
    if dry_run or not symbol_tuple:
        return frozenset()
    try:
        broker = AlpacaExecutionAdapter.from_settings(settings)
        fractionable = broker.get_fractionable_symbols(symbol_tuple)
    except Exception as exc:
        print(
            "Warning: could not resolve fractionable symbols; replay sizing will "
            f"assume whole-share quantities: {exc}",
            file=sys.stderr,
        )
        return frozenset()
    symbol_count = len(symbol_tuple)
    non_fractionable_count = symbol_count - len(fractionable)
    print(
        f"Fractionable symbols: {len(fractionable)}/{symbol_count} active "
        f"(whole-share assumed for {non_fractionable_count})"
    )
    return fractionable


def _resolve_account_equity(
    *,
    settings: Settings,
    dry_run: bool,
) -> float | None:
    if dry_run:
        return None
    try:
        broker = AlpacaExecutionAdapter.from_settings(settings)
        equity = float(broker.get_account().equity)
    except Exception as exc:
        print(
            "Warning: could not resolve account equity; replay scenarios will "
            f"keep their stored starting equity: {exc}",
            file=sys.stderr,
        )
        return None
    print(f"Account equity for replay sizing: ${equity:,.2f}")
    return equity


def _load_confidence_floor(*, conn, settings: Settings) -> float:
    try:
        rec = ConfidenceFloorStore(conn).load(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    except Exception as exc:
        print(
            f"Warning: could not load confidence floor; using settings value: {exc}",
            file=sys.stderr,
        )
        return settings.confidence_floor
    return rec.floor_value if rec is not None else settings.confidence_floor


def _load_strategy_confidence_scores(
    *,
    conn,
    settings: Settings,
    confidence_floor: float,
) -> dict[str, float]:
    try:
        weights = StrategyWeightStore(conn).load_all(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    except Exception as exc:
        print(
            f"Warning: could not load strategy confidence scores; using floor: {exc}",
            file=sys.stderr,
        )
        return {}
    sharpes = {row.strategy_name: row.sharpe for row in weights}
    return compute_confidence_scores(sharpes, confidence_floor)


def _effective_strategy_equity(
    *,
    account_equity: float | None,
    confidence_scores: dict[str, float],
    confidence_floor: float,
    strategy_name: str,
) -> float | None:
    if account_equity is None:
        return None
    return account_equity * confidence_scores.get(strategy_name, confidence_floor)


def _with_starting_equity(scenarios: list, starting_equity: float | None) -> list:
    if starting_equity is None:
        return scenarios
    return [
        replace(scenario, starting_equity=starting_equity)
        for scenario in scenarios
    ]


def _proof_guard_metrics(
    *,
    scenarios: list,
    report,
    settings: Settings,
    min_trades: int,
    min_pnl: float,
) -> _ProofGuardMetrics:
    sessions = sorted({
        bar.timestamp.astimezone(settings.market_timezone).date()
        for scenario in scenarios
        for bar in scenario.intraday_bars
    })
    pnls_by_exit_session: dict[date, list[float]] = {}
    for trade in report.trades:
        exit_session = trade.exit_time.astimezone(settings.market_timezone).date()
        pnls_by_exit_session.setdefault(exit_session, []).append(float(trade.pnl))

    starts_eventually_passed = 0
    starts_reaching_min_trades = 0
    first_threshold_passes = 0
    sessions_to_pass: list[int] = []

    for start_index, _start_session in enumerate(sessions):
        trade_count = 0
        pnl = 0.0
        first_threshold_seen = False
        pass_index: int | None = None

        for session_index in range(start_index, len(sessions)):
            session = sessions[session_index]
            session_pnls = pnls_by_exit_session.get(session, [])
            if session_pnls:
                trade_count += len(session_pnls)
                pnl += sum(session_pnls)
            if not first_threshold_seen and trade_count >= min_trades:
                first_threshold_seen = True
                starts_reaching_min_trades += 1
                if pnl >= min_pnl:
                    first_threshold_passes += 1
            if trade_count >= min_trades and pnl >= min_pnl:
                pass_index = session_index
                break

        if pass_index is not None:
            starts_eventually_passed += 1
            sessions_to_pass.append(pass_index - start_index + 1)

    sessions_to_pass.sort()
    return _ProofGuardMetrics(
        trades=int(report.total_trades),
        total_pnl=round(sum(float(trade.pnl) for trade in report.trades), 2),
        eventual_pass_rate=(
            starts_eventually_passed / len(sessions)
            if sessions else None
        ),
        first_threshold_pass_rate=(
            first_threshold_passes / starts_reaching_min_trades
            if starts_reaching_min_trades else None
        ),
        p95_sessions_to_pass=_ceil_percentile(sessions_to_pass, 0.95),
        slowest_sessions_to_pass=max(sessions_to_pass) if sessions_to_pass else None,
    )


def _proof_guard_regressions(
    *,
    baseline: _ProofGuardMetrics,
    candidate: _ProofGuardMetrics,
) -> list[str]:
    regressions: list[str] = []
    epsilon = 1e-9
    if candidate.total_pnl + epsilon < baseline.total_pnl:
        regressions.append(
            f"total_pnl {candidate.total_pnl:.2f} < baseline {baseline.total_pnl:.2f}"
        )
    if _lt_optional(candidate.eventual_pass_rate, baseline.eventual_pass_rate, epsilon):
        regressions.append(
            "eventual_pass_rate "
            f"{_fmt_pct(candidate.eventual_pass_rate)} < baseline "
            f"{_fmt_pct(baseline.eventual_pass_rate)}"
        )
    if _lt_optional(
        candidate.first_threshold_pass_rate,
        baseline.first_threshold_pass_rate,
        epsilon,
    ):
        regressions.append(
            "first_threshold_pass_rate "
            f"{_fmt_pct(candidate.first_threshold_pass_rate)} < baseline "
            f"{_fmt_pct(baseline.first_threshold_pass_rate)}"
        )
    if _gt_optional(candidate.p95_sessions_to_pass, baseline.p95_sessions_to_pass):
        regressions.append(
            "p95_sessions_to_pass "
            f"{_fmt_int(candidate.p95_sessions_to_pass)} > baseline "
            f"{_fmt_int(baseline.p95_sessions_to_pass)}"
        )
    if _gt_optional(candidate.slowest_sessions_to_pass, baseline.slowest_sessions_to_pass):
        regressions.append(
            "slowest_sessions_to_pass "
            f"{_fmt_int(candidate.slowest_sessions_to_pass)} > baseline "
            f"{_fmt_int(baseline.slowest_sessions_to_pass)}"
        )
    return regressions


def _ceil_percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    index = int(len(values) * pct)
    if len(values) * pct != index:
        index += 1
    index -= 1
    index = max(0, min(len(values) - 1, index))
    return values[index]


def _lt_optional(candidate: float | None, baseline: float | None, epsilon: float) -> bool:
    if baseline is None:
        return False
    if candidate is None:
        return True
    return candidate + epsilon < baseline


def _gt_optional(candidate: int | None, baseline: int | None) -> bool:
    if baseline is None:
        return False
    if candidate is None:
        return True
    return candidate > baseline


def _format_proof_guard_metrics(metrics: _ProofGuardMetrics) -> str:
    return (
        f"trades={metrics.trades} pnl={metrics.total_pnl:.2f} "
        f"eventual={_fmt_pct(metrics.eventual_pass_rate)} "
        f"first={_fmt_pct(metrics.first_threshold_pass_rate)} "
        f"p95={_fmt_int(metrics.p95_sessions_to_pass)} "
        f"slowest={_fmt_int(metrics.slowest_sessions_to_pass)}"
    )


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _fmt_int(value: int | None) -> str:
    return "n/a" if value is None else str(value)


def _weekdays_back(n: int) -> list[date]:
    """Return list of n weekdays (Mon–Fri) ending yesterday, most recent first."""
    result: list[date] = []
    d = date.today() - timedelta(days=1)
    while len(result) < n:
        if d.weekday() < 5:  # 0=Mon … 4=Fri
            result.append(d)
        d -= timedelta(days=1)
    return result


def _scenario_files_for_symbols(
    output_dir: Path, symbols: Sequence[str]
) -> tuple[list[Path], list[str]]:
    """Return scenario files for active watchlist symbols and any missing symbols."""

    files: list[Path] = []
    missing: list[str] = []
    for symbol in symbols:
        path = output_dir / f"{symbol}_252d.json"
        if path.exists():
            files.append(path)
        else:
            missing.append(symbol)
    return files, missing


def _load_enabled_strategy_names(*, conn, settings: Settings) -> list[str]:
    flags = StrategyFlagStore(conn).list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    return [flag.strategy_name for flag in flags if flag.enabled]


def _resolve_strategies(
    strategies_arg: str,
    *,
    enabled_strategy_names: Sequence[str] = (),
) -> list[str]:
    """Resolve '--strategies all', 'enabled', or comma-separated names."""
    raw = strategies_arg.strip().lower()
    if raw == "all":
        return list(STRATEGY_GRIDS.keys())
    if raw == "enabled":
        enabled = [name for name in enabled_strategy_names if name in STRATEGY_GRIDS]
        if enabled:
            return enabled
        print(
            "Warning: no enabled strategy flags found; falling back to all strategy grids",
            file=sys.stderr,
        )
        return list(STRATEGY_GRIDS.keys())
    names = [s.strip() for s in strategies_arg.split(",") if s.strip()]
    unknown = [n for n in names if n not in STRATEGY_GRIDS]
    if unknown:
        print(f"Warning: unknown strategies ignored: {unknown}", file=sys.stderr)
    return [n for n in names if n in STRATEGY_GRIDS]


def _active_strategy_names_for_weight_publish(
    *,
    conn,
    settings: Settings,
    strategy_names: Sequence[str],
) -> list[str]:
    enabled_strategy_names = _load_enabled_strategy_names(conn=conn, settings=settings)
    if enabled_strategy_names:
        enabled = set(enabled_strategy_names)
        return [name for name in strategy_names if name in enabled]
    return [name for name in strategy_names if name in STRATEGY_GRIDS]


def _publish_winner_strategy_weights(
    *,
    conn,
    settings: Settings,
    strategy_names: Sequence[str],
    winners: Sequence[tuple[str, TuningCandidate, float]],
    computed_at: datetime,
) -> bool:
    active_names = _active_strategy_names_for_weight_publish(
        conn=conn,
        settings=settings,
        strategy_names=strategy_names,
    )
    if not active_names:
        _append_strategy_weight_skip_event(
            conn=conn,
            computed_at=computed_at,
            reason="no_active_enabled_strategies",
            active_names=active_names,
            winner_scores={},
        )
        print("Strategy weights unchanged: no enabled strategies were swept.")
        return False

    winner_scores = {
        strategy_name: float(oos_score)
        for strategy_name, _candidate, oos_score in winners
        if strategy_name in active_names
    }
    missing = [name for name in active_names if name not in winner_scores]
    if missing:
        _append_strategy_weight_skip_event(
            conn=conn,
            computed_at=computed_at,
            reason="active_strategy_without_held_winner",
            active_names=active_names,
            winner_scores=winner_scores,
            missing=missing,
        )
        print(
            "Strategy weights unchanged: active strategies without held winners: "
            + ", ".join(missing)
        )
        return False

    non_positive = [name for name in active_names if winner_scores[name] <= 0.0]
    total_score = sum(max(winner_scores[name], 0.0) for name in active_names)
    if non_positive or total_score <= 0.0:
        _append_strategy_weight_skip_event(
            conn=conn,
            computed_at=computed_at,
            reason="non_positive_pooled_oos_score",
            active_names=active_names,
            winner_scores=winner_scores,
            missing=non_positive,
        )
        print(
            "Strategy weights unchanged: non-positive pooled OOS scores for "
            + ", ".join(non_positive or active_names)
        )
        return False

    weights = {name: winner_scores[name] / total_score for name in active_names}
    sharpes = {name: winner_scores[name] for name in active_names}
    rounded_weights = {name: round(weight, 6) for name, weight in weights.items()}
    rounded_sharpes = {name: round(sharpe, 6) for name, sharpe in sharpes.items()}

    StrategyWeightStore(conn).upsert_many(
        weights=weights,
        sharpes=sharpes,
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        computed_at=computed_at,
        commit=False,
    )
    AuditEventStore(conn).append(
        AuditEvent(
            event_type="strategy_weights_updated",
            payload={
                "source": "nightly_pooled_oos",
                "active_strategies": list(active_names),
                "weights": rounded_weights,
                "sharpes": rounded_sharpes,
            },
            created_at=computed_at,
        ),
        commit=False,
    )
    commit = getattr(conn, "commit", None)
    if callable(commit):
        commit()
    print(
        "Strategy weights updated from pooled OOS scores: "
        + ", ".join(f"{name}={weights[name]:.4f}" for name in active_names)
    )
    return True


def _append_strategy_weight_skip_event(
    *,
    conn,
    computed_at: datetime,
    reason: str,
    active_names: Sequence[str],
    winner_scores: dict[str, float],
    missing: Sequence[str] = (),
) -> None:
    AuditEventStore(conn).append(
        AuditEvent(
            event_type="nightly_strategy_weights_skipped",
            payload={
                "source": "nightly_pooled_oos",
                "reason": reason,
                "active_strategies": list(active_names),
                "held_winner_scores": {
                    name: round(score, 6) for name, score in winner_scores.items()
                },
                "missing_strategies": list(missing),
            },
            created_at=computed_at,
        )
    )


def _build_composite_env(
    winners: list[tuple[str, TuningCandidate, float]],
) -> dict[str, str]:
    """First-wins merge: sort by _viability_key descending, apply params in rank order."""
    sorted_winners = sorted(winners, key=lambda t: _viability_key(t[1], t[2]), reverse=True)
    composite: dict[str, str] = {}
    for _strat, candidate, _oos in sorted_winners:
        for k, v in candidate.params.items():
            if k not in composite:
                composite[k] = v
    return composite


def _format_composite_env_block(
    params: dict[str, str],
    top_strategy: str,
    now: datetime,
) -> str:
    lines = [
        f"# Composite params from nightly run {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"# Shared params from: {top_strategy}",
    ]
    lines += [f"{k}={v}" for k, v in params.items()]
    return "\n".join(lines)


def _print_strategy_results(
    winners: list[tuple[str, TuningCandidate, float]],
    strategy_names: list[str],
    all_scenarios: list,
) -> None:
    print("\n── Strategy Results ─────────────────────────────────────────────────")
    winner_map = {strat: (cand, oos) for strat, cand, oos in winners}
    for strat in strategy_names:
        if strat in winner_map:
            cand, oos = winner_map[strat]
            report = cand.report
            trades = report.total_trades if report else 0
            pf = f"{report.profit_factor:.2f}" if (report and report.profit_factor is not None) else "—"
            print(f"  {strat:<20s} score={oos:.4f}  trades={trades:<3d}  pf={pf}  held? ✓")
        else:
            print(f"  {strat:<20s} held? ✗  (no held candidates)")
    if winners:
        top = sorted(winners, key=lambda t: _viability_key(t[1], t[2]), reverse=True)[0][0]
        print(f"Composite winner (shared params from: {top})")


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
