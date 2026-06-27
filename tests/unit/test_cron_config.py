from pathlib import Path


def test_cron_runs_session_guard_profit_probe_then_nightly() -> None:
    cron_text = Path("deploy/cron.d/alpaca-bot").read_text()

    readiness = "20 13 * * 1-5 root flock -n /var/lock/alpaca-bot-paper-readiness.lock"
    readiness_retry = "55 13 * * 1-5 root flock -n /var/lock/alpaca-bot-paper-readiness.lock"
    early_activity = "15 14 * * 1-5 root flock -n /var/lock/alpaca-bot-paper-activity.lock"
    activity = "0 16 * * 1-5 root flock -n /var/lock/alpaca-bot-paper-activity.lock"
    session_guard = "10 22 * * 1-5 root flock -n /var/lock/alpaca-bot-session-guard.lock"
    profit_probe = "20 22 * * 1-5 root flock -n /var/lock/alpaca-bot-profit-probe.lock"
    nightly = "30 22 * * 1-5 root flock -n /var/lock/alpaca-bot-nightly.lock"

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
    assert "alpaca-bot-premarket" not in cron_text
    assert "scripts/paper_readiness_check.sh" in cron_text
    assert cron_text.count("scripts/paper_readiness_check.sh") == 2
    assert "/var/log/alpaca-bot-paper-readiness.log" in cron_text
    assert "scripts/paper_activity_check.sh" in cron_text
    assert cron_text.count("scripts/paper_activity_check.sh") == 2
    assert "/var/log/alpaca-bot-paper-activity.log" in cron_text
    assert "scripts/paper_profit_probe.sh" in cron_text
    assert "/var/log/alpaca-bot-profit-probe.log" in cron_text


def test_paper_readiness_auto_resume_is_guarded() -> None:
    script = Path("scripts/paper_readiness_check.sh").read_text()
    broker_flat = Path("scripts/broker_flat_check.sh").read_text()

    assert 'PAPER_READINESS_AUTO_RESUME="${PAPER_READINESS_AUTO_RESUME:-true}"' in script
    assert 'PAPER_READINESS_AUTO_RESET_WEIGHTS="${PAPER_READINESS_AUTO_RESET_WEIGHTS:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_FLAT="${PAPER_READINESS_REQUIRE_FLAT:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED="${PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR="${PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_MARKET_DATA="${PAPER_READINESS_REQUIRE_MARKET_DATA:-true}"' in script
    assert 'PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-}"' in script
    assert 'PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-${LOSING_STREAK_N:-3}}"' in script
    assert 'PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}"' in script
    assert 'PAPER_READINESS_MIN_CONFIDENCE_FLOOR="${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.25}"' in script
    assert 'PAPER_READINESS_DATA_SMOKE_SYMBOLS="${PAPER_READINESS_DATA_SMOKE_SYMBOLS:-SPY,AAPL}"' in script
    assert 'PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS="${PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS:-10}"' in script
    assert "PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS must be a positive integer" in script
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
    assert "strategy weights mismatch" in script
    assert "paper readiness resetting stale strategy weights" in script
    assert "admin reset-weights" in script
    assert "paper readiness weights ok" in script
    assert "confidence_floor_store" in script
    assert "paper readiness confidence floor ok" in script
    assert "expected >= $PAPER_READINESS_MIN_CONFIDENCE_FLOOR and <= 1.0" in script
    assert "run_market_data_smoke_check" in script
    assert "AlpacaMarketDataAdapter.from_settings" in script
    assert "adapter.get_daily_bars" in script
    assert "paper readiness failed: market data daily-bars smoke failed" in script
    assert "paper readiness failed: market data daily-bars smoke returned no bars" in script
    assert "paper readiness market data ok" in script
    assert "paper readiness market data check skipped" in script
    assert "paper readiness option positions ok: net_open=0" in script
    assert "stock-only proof has $open_option_positions net-open option positions" in script
    assert "paper readiness refusing auto-resume after failed proof guard" in script
    assert "paper proof failed" in script
    assert "session guard failed" in script
    assert "current session has entry-blocking state" in script
    assert "paper readiness session entry blocks ok: blocked=0" in script
    assert "PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED" in script
    assert "CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York'" in script
    assert "IN ('_global', '_equity')" in script
    assert "LOSING_STREAK_N must be a positive integer" in script
    assert "paper readiness failed: active strategies at losing-streak gate" in script
    assert "paper readiness losing streak gate ok: blocked=0" in script
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
    assert 'PAPER_ACTIVITY_STRATEGY="${PAPER_ACTIVITY_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"' in script
    assert "PAPER_READINESS_AUTO_RESUME=false" in script
    assert "PAPER_READINESS_REQUIRE_FLAT=false" in script
    assert "decision_record_count" in script
    assert "payload->>'strategy_name' = '${PAPER_ACTIVITY_STRATEGY}'" in script
    assert "strategy_decision_cycles" in script
    assert "strategy_decision_records" in script
    assert "entries_disabled" in script
    assert "blocked_strategy_names" in script
    assert "strategy_entries_disabled_reasons" in script
    assert "$PAPER_ACTIVITY_STRATEGY entries blocked" in script
    assert "PAPER_ACTIVITY_STRATEGY contains unsupported characters" in script
    assert "market_closed" in script
    assert "no supervisor cycles" in script
    assert "no decision cycles" in script
    assert "no $PAPER_ACTIVITY_STRATEGY decision cycles" in script
    assert "$PAPER_ACTIVITY_STRATEGY decision_record_count" in script


def test_post_close_checks_fail_on_open_positions() -> None:
    session_guard = Path("scripts/session_guard.sh").read_text()
    profit_probe = Path("scripts/paper_profit_probe.sh").read_text()

    assert "--fail-on-open-positions" in session_guard
    assert "--fail-on-open-positions" in profit_probe
    assert "./scripts/broker_flat_check.sh" in session_guard
    assert "./scripts/broker_flat_check.sh" in profit_probe
    assert "broker exposure remains after close" in session_guard
    assert "broker exposure remains after close" in profit_probe
    assert 'if [[ "$rc" -eq 0 ]]; then' in session_guard
    assert 'if [[ "$rc" -eq 0 || "$rc" -eq 43 ]]; then' in profit_probe
    assert 'PROFIT_PROBE_START_DATE="${PROFIT_PROBE_START_DATE:-2026-06-26}"' in profit_probe
    assert "--start-date" in profit_probe
    assert "--end-date" in profit_probe
    assert '1) TZ=America/New_York date -d "3 days ago" +%F ;;' not in profit_probe
    assert '6) TZ=America/New_York date -d "1 day ago" +%F ;;' in profit_probe
    assert '7) TZ=America/New_York date -d "2 days ago" +%F ;;' in profit_probe
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
