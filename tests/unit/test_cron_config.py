from pathlib import Path


def test_cron_runs_session_guard_profit_probe_then_nightly() -> None:
    cron_text = Path("deploy/cron.d/alpaca-bot").read_text()

    readiness = "20 13 * * 1-5 root flock -n /var/lock/alpaca-bot-paper-readiness.lock"
    premarket = "30 12 * * 1-5 root flock -n /var/lock/alpaca-bot-nightly.lock"
    activity = "0 16 * * 1-5 root flock -n /var/lock/alpaca-bot-paper-activity.lock"
    session_guard = "10 22 * * 1-5 root flock -n /var/lock/alpaca-bot-session-guard.lock"
    profit_probe = "20 22 * * 1-5 root flock -n /var/lock/alpaca-bot-profit-probe.lock"
    nightly = "30 22 * * 1-5 root flock -n /var/lock/alpaca-bot-nightly.lock"

    assert readiness in cron_text
    assert premarket in cron_text
    assert activity in cron_text
    assert session_guard in cron_text
    assert profit_probe in cron_text
    assert nightly in cron_text
    assert cron_text.index(premarket) < cron_text.index(readiness)
    assert cron_text.index(premarket) < cron_text.index(activity)
    assert cron_text.index(session_guard) < cron_text.index(profit_probe)
    assert cron_text.index(profit_probe) < cron_text.index(nightly)
    assert "scripts/paper_readiness_check.sh" in cron_text
    assert "/var/log/alpaca-bot-paper-readiness.log" in cron_text
    assert "scripts/paper_activity_check.sh" in cron_text
    assert "/var/log/alpaca-bot-paper-activity.log" in cron_text
    assert "scripts/paper_profit_probe.sh" in cron_text
    assert "/var/log/alpaca-bot-profit-probe.log" in cron_text


def test_paper_readiness_auto_resume_is_guarded() -> None:
    script = Path("scripts/paper_readiness_check.sh").read_text()

    assert 'PAPER_READINESS_AUTO_RESUME="${PAPER_READINESS_AUTO_RESUME:-true}"' in script
    assert 'PAPER_READINESS_AUTO_RESET_WEIGHTS="${PAPER_READINESS_AUTO_RESET_WEIGHTS:-true}"' in script
    assert 'PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}"' in script
    assert 'PAPER_READINESS_MIN_CONFIDENCE_FLOOR="${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.01}"' in script
    assert 'status=close_only' in script
    assert 'kill_switch=false' in script
    assert 'open_positions" == "0"' in script
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
    assert "pre-open paper readiness auto-resume" in script
    assert "--expect-trading-status enabled" in script
    assert "--expect-only-enabled-strategy bull_flag" in script
    assert "require_env_value MAX_OPEN_POSITIONS 2" in script
    assert "require_env_value REPLAY_SLIPPAGE_BPS 2.0" in script
    assert "require_env_value RISK_PER_TRADE_PCT 0.01" in script
    assert "require_env_true ENABLE_VWAP_ENTRY_FILTER" in script
    assert "require_env_false_or_unset ENABLE_VIX_FILTER" in script
    assert "require_env_false_or_unset ENABLE_SECTOR_FILTER" in script
    assert "require_env_false_or_unset ENABLE_REGIME_FILTER" in script
    assert "require_env_false_or_unset ENABLE_OPTIONS_TRADING" in script


def test_paper_activity_check_verifies_mid_session_evaluation() -> None:
    script = Path("scripts/paper_activity_check.sh").read_text()

    assert "PAPER_ACTIVITY_WINDOW_MINUTES" in script
    assert "PAPER_READINESS_AUTO_RESUME=false" in script
    assert "decision_record_count" in script
    assert "entries_disabled" in script
    assert "market_closed" in script
    assert "no supervisor cycles" in script
    assert "no decision cycles" in script


def test_post_close_checks_fail_on_open_positions() -> None:
    session_guard = Path("scripts/session_guard.sh").read_text()
    profit_probe = Path("scripts/paper_profit_probe.sh").read_text()

    assert "--fail-on-open-positions" in session_guard
    assert "--fail-on-open-positions" in profit_probe
    assert 'PROFIT_PROBE_START_DATE="${PROFIT_PROBE_START_DATE:-2026-06-26}"' in profit_probe
    assert "--start-date" in profit_probe
    assert "--end-date" in profit_probe
    assert '"$rc" -eq 44' in session_guard
    assert "open positions remain after close" in session_guard
