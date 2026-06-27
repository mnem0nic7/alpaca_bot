from pathlib import Path


def test_cron_runs_session_guard_profit_probe_then_nightly() -> None:
    cron_text = Path("deploy/cron.d/alpaca-bot").read_text()
    install_cron = Path("scripts/install_cron.sh").read_text()
    run_if_ny_time = Path("scripts/run_if_ny_time.sh").read_text()

    readiness = "20 13,14 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 0920"
    readiness_retry = "55 13,14 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 0955"
    early_activity = "15 14,15 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1015"
    activity = "0 16,17 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1200"
    session_guard = "10 21,22 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1710"
    profit_probe = "20 21,22 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1720"
    nightly = "30 21,22 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1730"

    assert readiness in cron_text
    assert readiness_retry in cron_text
    assert early_activity in cron_text
    assert activity in cron_text
    assert session_guard in cron_text
    assert profit_probe in cron_text
    assert nightly in cron_text
    assert cron_text.index(readiness) < cron_text.index(readiness_retry)
    assert cron_text.index(readiness_retry) < cron_text.index(early_activity)
    assert cron_text.index(early_activity) < cron_text.index(activity)
    assert cron_text.index(session_guard) < cron_text.index(profit_probe)
    assert cron_text.index(profit_probe) < cron_text.index(nightly)
    assert cron_text.count("scripts/run_if_ny_time.sh") == 7
    assert cron_text.count("scripts/run_check_with_audit.sh") == 6
    assert "alpaca-bot-premarket" not in cron_text
    assert "scripts/paper_readiness_check.sh" in cron_text
    assert cron_text.count("scripts/paper_readiness_check.sh") == 2
    assert "run_check_with_audit.sh paper_readiness" in cron_text
    assert "/var/log/alpaca-bot-paper-readiness.log" in cron_text
    assert "scripts/paper_activity_check.sh" in cron_text
    assert cron_text.count("scripts/paper_activity_check.sh") == 2
    assert "run_check_with_audit.sh paper_activity" in cron_text
    assert "/var/log/alpaca-bot-paper-activity.log" in cron_text
    assert "scripts/paper_profit_probe.sh" in cron_text
    assert "run_check_with_audit.sh paper_profit_probe" in cron_text
    assert "/var/log/alpaca-bot-profit-probe.log" in cron_text
    assert "run_check_with_audit.sh session_guard" in cron_text
    assert 'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in install_cron
    assert 'install -m 644 "$ROOT_DIR/deploy/cron.d/alpaca-bot" /etc/cron.d/alpaca-bot' in install_cron
    assert "Runs weekdays on New York wall time" in install_cron
    assert 'ACTUAL_HHMM="$(TZ=America/New_York date +%H%M)"' in run_if_ny_time
    assert 'exec "$@"' in run_if_ny_time


def test_run_check_with_audit_records_scheduled_check_result() -> None:
    script_path = Path("scripts/run_check_with_audit.sh")
    script = script_path.read_text()

    assert script_path.stat().st_mode & 0o111
    assert "scheduled_check_completed" in script
    assert 'AUDIT_CHECK_NAME="$CHECK_NAME"' in script
    assert 'AUDIT_STATUS="$status"' in script
    assert 'AUDIT_EXIT_CODE="$rc"' in script
    assert 'AUDIT_OUTPUT_TAIL="$output_tail"' in script
    assert "-e AUDIT_CHECK_NAME" in script
    assert "-e AUDIT_STATUS" in script
    assert "-e AUDIT_EXIT_CODE" in script
    assert "-e AUDIT_OUTPUT_TAIL" in script
    assert 'output_tail="$(tail -c 4000 "$output_file" 2>/dev/null || true)"' in script
    assert 'paper readiness check skipped' in script
    assert 'paper activity check skipped' in script
    assert 'paper activity skipped:' in script
    assert 'status="skipped"' in script
    assert '43)' in script
    assert 'status="pending"' in script
    assert 'tee "$output_file"' in script
    assert 'tee -a "$output_file" >&2' in script
    assert 'docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm' in script
    assert "AuditEventStore(conn).append" in script
    assert '"trading_mode": settings.trading_mode.value' in script
    assert '"strategy_version": settings.strategy_version' in script
    assert 'exit "$rc"' in script


def test_paper_readiness_auto_resume_is_guarded() -> None:
    script = Path("scripts/paper_readiness_check.sh").read_text()
    broker_flat = Path("scripts/broker_flat_check.sh").read_text()

    assert 'PAPER_READINESS_AUTO_RESUME="${PAPER_READINESS_AUTO_RESUME:-true}"' in script
    assert 'PAPER_READINESS_AUTO_RESET_WEIGHTS="${PAPER_READINESS_AUTO_RESET_WEIGHTS:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_FLAT="${PAPER_READINESS_REQUIRE_FLAT:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED="${PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR="${PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_MARKET_DATA="${PAPER_READINESS_REQUIRE_MARKET_DATA:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_SCENARIOS="${PAPER_READINESS_REQUIRE_SCENARIOS:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS="${PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS:-true}"' in script
    assert 'PAPER_READINESS_PRIOR_PROOF_START_DATE="${PAPER_READINESS_PRIOR_PROOF_START_DATE:-}"' in script
    assert 'PAPER_READINESS_PRIOR_PROOF_START_DATE="${PAPER_READINESS_PRIOR_PROOF_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}}"' in script
    assert 'PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-}"' in script
    assert 'PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-${LOSING_STREAK_N:-3}}"' in script
    assert 'PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}"' in script
    assert 'PAPER_READINESS_MIN_CONFIDENCE_FLOOR="${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.25}"' in script
    assert 'PAPER_READINESS_DATA_SMOKE_SYMBOLS="${PAPER_READINESS_DATA_SMOKE_SYMBOLS:-SPY,AAPL}"' in script
    assert 'PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS="${PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS:-10}"' in script
    assert 'PAPER_READINESS_SCENARIO_DIR="${PAPER_READINESS_SCENARIO_DIR:-/var/lib/alpaca-bot/nightly/scenarios}"' in script
    assert "PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS must be a positive integer" in script
    assert "PAPER_READINESS_PRIOR_PROOF_START_DATE must be YYYY-MM-DD" in script
    assert 'PAPER_READINESS_SESSION_DATE="${PAPER_READINESS_SESSION_DATE:-$(load_readiness_session_date)}"' in script
    assert 'PAPER_READINESS_PREVIOUS_SESSION_DATE="${PAPER_READINESS_PREVIOUS_SESSION_DATE:-$(load_previous_session_date)}"' in script
    assert "load_readiness_session_date" in script
    assert "load_previous_session_date" in script
    assert "fallback_readiness_session_date" in script
    assert "fallback_previous_session_date" in script
    assert "get_market_calendar" in script
    assert "no upcoming market session found" in script
    assert "no previous market session found" in script
    assert "market calendar lookup failed; using weekday fallback" in script
    assert "previous market session lookup failed; using weekday fallback" in script
    assert "-v readiness_session_date=\"$PAPER_READINESS_SESSION_DATE\"" in script
    assert "session_date = (:'readiness_session_date')::date" in script
    assert "paper readiness session entry blocks ok: session=$PAPER_READINESS_SESSION_DATE blocked=0" in script
    assert "<= ((:'readiness_session_date')::date - 1)" in script
    assert "paper readiness losing streak gate ok: session=$PAPER_READINESS_SESSION_DATE blocked=0" in script
    assert 'status=close_only' in script
    assert 'kill_switch=false' in script
    assert 'open_positions" == "0"' in script
    assert 'active_orders" == "0"' in script
    assert "load_stock_exposure_counts" in script
    assert "'pending_submit'" in script
    assert "'partially_filled'" in script
    assert "paper readiness stock exposure ok: positions=0 active_orders=0" in script
    assert "paper readiness flat exposure check skipped" in script
    assert "stock-only proof has $open_positions open stock positions" in script
    assert "stock-only proof has $active_orders active stock orders" in script
    assert 'BROKER_FLAT_CONTEXT="paper readiness" ./scripts/broker_flat_check.sh "$ENV_FILE"' in script
    assert "AlpacaExecutionAdapter.from_settings" in broker_flat
    assert "{context} broker exposure ok: open_orders=0 open_positions=0" in broker_flat
    assert "broker has {len(open_orders)} open stock orders" in broker_flat
    assert "broker has {len(open_positions)} open stock positions" in broker_flat
    assert "close_only with $active_orders active orders" in script
    assert "symbol_watchlist" in script
    assert "COALESCE(ignored, FALSE) = FALSE" in script
    assert "entry watchlist has" in script
    assert "paper readiness watchlist ok" in script
    assert "run_scenario_freshness_check" in script
    assert "PAPER_READINESS_ACTIVE_SYMBOLS" in script
    assert "PAPER_READINESS_EXPECTED_SCENARIO_DATE" in script
    assert 'scenario_dir / f"{symbol}_252d.json"' in script
    assert "paper readiness scenario freshness ok" in script
    assert "paper readiness scenario freshness check skipped" in script
    assert "scenario directory missing" in script
    assert "active-symbol evidence" in script
    assert "stale_daily" in script
    assert "stale_intraday" in script
    assert "strategy weights mismatch" in script
    assert "sharpe IS NULL" in script
    assert "null_sharpes=${null_sharpes:-0}" in script
    assert "paper readiness resetting stale strategy weights" in script
    assert "admin reset-weights" in script
    assert "paper readiness weights ok" in script
    assert "confidence_floor_store" in script
    assert "paper readiness confidence floor ok" in script
    assert "confidence watermark" in script
    assert "drawdown=${confidence_watermark_drawdown:-unset} exceeds trigger" in script
    assert "paper readiness confidence watermark ok" in script
    assert "AlpacaExecutionAdapter.from_settings(settings).get_account()" in script
    assert "settings.drawdown_raise_pct" in script
    assert "expected >= $PAPER_READINESS_MIN_CONFIDENCE_FLOOR and <= 1.0" in script
    assert "run_market_data_smoke_check" in script
    assert "run_container_settings_posture_check" in script
    assert "paper readiness container Settings ok" in script
    assert "paper readiness failed: container Settings posture drift:" in script
    assert 'check("market_data_feed", settings.market_data_feed.value, "iex")' in script
    assert 'check("trailing_stop_atr_multiplier", settings.trailing_stop_atr_multiplier, 1.5)' in script
    assert 'check("enable_profit_trail", settings.enable_profit_trail, True)' in script
    assert 'check("paper_proof_freeze", settings.paper_proof_freeze, True)' in script
    assert 'check("enable_vwap_entry_filter", settings.enable_vwap_entry_filter, True)' in script
    assert 'check("enable_news_filter", settings.enable_news_filter, False)' in script
    assert 'check("max_loss_per_trade_dollars", settings.max_loss_per_trade_dollars, None)' in script
    assert script.index("run_container_settings_posture_check") < script.index("run_market_data_smoke_check")
    assert "AlpacaMarketDataAdapter.from_settings" in script
    assert "adapter.get_daily_bars" in script
    assert "paper readiness failed: market data daily-bars smoke failed" in script
    assert "paper readiness failed: market data daily-bars smoke returned no bars" in script
    assert "paper readiness market data ok" in script
    assert "paper readiness market data check skipped" in script
    assert "active option orders" in script
    assert "paper readiness option positions ok: net_open=0 active_orders=0" in script
    assert "stock-only proof has $open_option_positions net-open option positions" in script
    assert "paper readiness refusing auto-resume after failed proof guard" in script
    assert "paper proof failed" in script
    assert "session guard failed" in script
    assert "paper readiness prior proof checks pending" in script
    assert "prior proof scheduled checks missing" in script
    assert "prior proof scheduled checks failed" in script
    assert "paper readiness prior proof checks ok" in script
    assert "PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS" in script
    assert "PAPER_READINESS_PREVIOUS_SESSION_DATE\" < \"$PAPER_READINESS_PRIOR_PROOF_START_DATE" in script
    assert "scheduled_check_completed" in script
    assert "payload->>'check_name' IN ('session_guard', 'paper_profit_probe')" in script
    assert "latest_checks AS" in script
    assert "missing AS" in script
    assert "invalid AS" in script
    assert "check_name = 'session_guard' AND status = 'passed'" in script
    assert "check_name = 'paper_profit_probe' AND status IN ('passed', 'pending')" in script
    assert "session $PAPER_READINESS_SESSION_DATE has entry-blocking state" in script
    assert "paper readiness session entry blocks ok: session=$PAPER_READINESS_SESSION_DATE blocked=0" in script
    assert "PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED" in script
    assert "IN ('_global', '_equity')" in script
    assert "LOSING_STREAK_N must be a positive integer" in script
    assert "paper readiness failed: active strategies at losing-streak gate" in script
    assert "paper readiness losing streak gate ok: session=$PAPER_READINESS_SESSION_DATE blocked=0" in script
    assert "PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR" in script
    assert "non_loss_days_newer" in script
    assert "losing_streak >= (:'losing_streak_n')::int" in script
    assert "pre-open paper readiness auto-resume" in script
    assert "--expect-trading-status enabled" in script
    assert "--expect-only-enabled-strategy bull_flag" in script
    assert "require_env_value MARKET_DATA_FEED iex" in script
    assert "require_env_value DAILY_SMA_PERIOD 20" in script
    assert "require_env_value BREAKOUT_LOOKBACK_BARS 20" in script
    assert "require_env_value RELATIVE_VOLUME_LOOKBACK_BARS 20" in script
    assert "require_env_value RELATIVE_VOLUME_THRESHOLD 2.0" in script
    assert "require_env_value ENTRY_TIMEFRAME_MINUTES 15" in script
    assert "require_env_value MAX_OPEN_POSITIONS 3" in script
    assert "require_env_value REPLAY_SLIPPAGE_BPS 2.0" in script
    assert "require_env_value RISK_PER_TRADE_PCT 0.01" in script
    assert "require_env_value_or_unset ATR_PERIOD 14" in script
    assert "require_env_value_or_unset ATR_STOP_MULTIPLIER 1.0" in script
    assert "require_env_value TRAILING_STOP_ATR_MULTIPLIER 1.5" in script
    assert "require_env_value_or_unset TRAILING_STOP_PROFIT_TRIGGER_R 1.0" in script
    assert "require_env_value INTRADAY_CONSECUTIVE_LOSS_GATE 0" in script
    assert "require_env_value ENTRY_WINDOW_START 10:00" in script
    assert "require_env_value ENTRY_WINDOW_END 15:30" in script
    assert "require_env_value FLATTEN_TIME 15:45" in script
    assert "require_env_true PAPER_PROOF_FREEZE" in script
    assert "require_env_true ENABLE_VWAP_ENTRY_FILTER" in script
    assert "require_env_true ENABLE_PROFIT_TRAIL" in script
    assert "require_env_value PROFIT_TRAIL_PCT 0.95" in script
    assert "require_env_true_or_unset ENABLE_BREAKEVEN_STOP" in script
    assert "require_env_value_or_unset BREAKEVEN_TRIGGER_PCT 0.0025" in script
    assert "require_env_value_or_unset BREAKEVEN_TRAIL_PCT 0.002" in script
    assert "require_env_false_or_unset EXTENDED_HOURS_ENABLED" in script
    assert "require_env_false_or_unset ENABLE_VIX_FILTER" in script
    assert "require_env_false_or_unset ENABLE_SECTOR_FILTER" in script
    assert "require_env_false_or_unset ENABLE_REGIME_FILTER" in script
    assert "require_env_false_or_unset ENABLE_NEWS_FILTER" in script
    assert "require_env_false_or_unset ENABLE_SPREAD_FILTER" in script
    assert "require_env_false_or_unset ENABLE_OPTIONS_TRADING" in script


def test_paper_activity_check_verifies_mid_session_evaluation() -> None:
    script = Path("scripts/paper_activity_check.sh").read_text()

    assert "PAPER_ACTIVITY_WINDOW_MINUTES" in script
    assert 'PAPER_ACTIVITY_MIN_DECISION_RECORDS="${PAPER_ACTIVITY_MIN_DECISION_RECORDS:-900}"' in script
    assert 'PAPER_ACTIVITY_REQUIRE_DECISION_LOG="${PAPER_ACTIVITY_REQUIRE_DECISION_LOG:-true}"' in script
    assert "PAPER_ACTIVITY_REQUIRE_DECISION_LOG must be true or false" in script
    assert 'PAPER_ACTIVITY_STRATEGY="${PAPER_ACTIVITY_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"' in script
    assert "PAPER_READINESS_AUTO_RESUME=false" in script
    assert "PAPER_READINESS_REQUIRE_FLAT=false" in script
    assert "decision_record_count" in script
    assert "decision_log" in script
    assert "strategy_decision_log_cycles" in script
    assert "strategy_decision_log_records" in script
    assert "strategy_evidence_records" in script
    assert "decision_evidence_records" in script
    assert "payload->>'strategy_name' = :'paper_activity_strategy'" in script
    assert "strategy_decision_cycles" in script
    assert "strategy_decision_records" in script
    assert "-v trading_mode=" in script
    assert "payload ? 'trading_mode'" in script
    assert "payload ? 'strategy_version'" in script
    assert "entries_disabled" in script
    assert "blocked_strategy_names" in script
    assert "strategy_entries_disabled_reasons" in script
    assert "$PAPER_ACTIVITY_STRATEGY entries blocked" in script
    assert "PAPER_ACTIVITY_STRATEGY contains unsupported characters" in script
    assert "load_market_clock_status" in script
    assert "AlpacaExecutionAdapter.from_settings" in script
    assert "get_market_clock" in script
    assert "supervisor reported market_closed but Alpaca clock is" in script
    assert "market_closed" in script
    assert "no supervisor cycles" in script
    assert "no decision cycles" in script
    assert "no $PAPER_ACTIVITY_STRATEGY decision cycles" in script
    assert "no $PAPER_ACTIVITY_STRATEGY decision_log cycles" in script
    assert "$PAPER_ACTIVITY_STRATEGY decision_log_records" in script
    assert "$PAPER_ACTIVITY_STRATEGY decision_evidence_records" in script
    assert "require_decision_log" in script


def test_post_close_checks_fail_on_open_positions() -> None:
    session_guard = Path("scripts/session_guard.sh").read_text()
    profit_probe = Path("scripts/paper_profit_probe.sh").read_text()

    assert "--fail-on-open-positions" in session_guard
    assert "--fail-on-open-positions" in profit_probe
    assert 'SESSION_GUARD_FAIL_ON_DIAGNOSTICS="${SESSION_GUARD_FAIL_ON_DIAGNOSTICS:-true}"' in session_guard
    assert "SESSION_GUARD_FAIL_ON_DIAGNOSTICS must be true or false" in session_guard
    assert "session_eval_args+=(--fail-on-diagnostics)" in session_guard
    assert "./scripts/broker_flat_check.sh" in session_guard
    assert "./scripts/broker_flat_check.sh" in profit_probe
    assert "broker exposure remains after close" in session_guard
    assert "broker exposure remains after close" in profit_probe
    assert "broker_flat_failed=true\n  rc=44" in session_guard
    assert "broker_flat_failed=true\n  rc=44" in profit_probe
    assert 'PROFIT_PROBE_START_DATE="${PROFIT_PROBE_START_DATE:-2026-06-29}"' in profit_probe
    assert "paper profit probe pending: latest completed session" in profit_probe
    assert 'PROFIT_PROBE_DATE" < "$PROFIT_PROBE_START_DATE' in profit_probe
    assert "paper proof pending" in profit_probe
    assert "load_latest_completed_session_date" in profit_probe
    assert "AlpacaExecutionAdapter.from_settings" in profit_probe
    assert "get_market_calendar" in profit_probe
    assert "close_at + timedelta(minutes=30)" in profit_probe
    assert "market calendar lookup failed; using weekday fallback" in profit_probe
    assert "--start-date" in profit_probe
    assert "--end-date" in profit_probe
    assert 'hhmm="$(TZ=America/New_York date +%H%M)"' in profit_probe
    assert '"$hhmm" -ge 1630' in profit_probe
    assert '1) TZ=America/New_York date -d "3 days ago" +%F ;;' in profit_probe
    assert '6) TZ=America/New_York date -d "1 day ago" +%F ;;' in profit_probe
    assert '7) TZ=America/New_York date -d "2 days ago" +%F ;;' in profit_probe
    assert '*) TZ=America/New_York date -d "1 day ago" +%F ;;' in profit_probe
    assert '"$rc" -eq 44' in session_guard
    assert "open positions remain after close" in session_guard
    assert "session guard failed: could not apply close-only guard" in session_guard
    assert "exit 45" in session_guard
    assert '"$rc" -eq 42 || "$rc" -eq 44' in profit_probe
    assert "paper proof failed" in profit_probe
    assert "close-only" in profit_probe
    assert '"$rc" -eq 42 || "$rc" -eq 43' in profit_probe
    assert "alpaca-bot-funnel-report" in profit_probe
    assert '--strategy "$PROFIT_PROBE_STRATEGY"' in profit_probe
    assert "paper profit probe warning: funnel diagnostic failed" in profit_probe
    assert "paper profit probe failed: could not apply close-only guard" in profit_probe
    assert "exit 45" in profit_probe
