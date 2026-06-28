import subprocess
from pathlib import Path


def test_cron_runs_session_guard_profit_probe_then_nightly() -> None:
    cron_text = Path("deploy/cron.d/alpaca-bot").read_text()
    install_cron = Path("scripts/install_cron.sh").read_text()
    run_if_ny_time = Path("scripts/run_if_ny_time.sh").read_text()
    cron_health = Path("scripts/cron_health_check.sh").read_text()

    readiness = "20 13,14 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 0920"
    readiness_retry = "55 13,14 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 0955"
    readiness_final = (
        "58 13,14 * * 1-5 root RUN_IF_NY_TIME_GRACE_MINUTES=1 "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 0958"
    )
    readiness_post_open_repair = (
        "2 14,15 * * 1-5 root RUN_IF_NY_TIME_GRACE_MINUTES=1 "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1002"
    )
    early_activity = "15 14,15 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1015"
    activity = "0 16,17 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1200"
    session_guard = "10 21,22 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1710"
    profit_probe = "20 21,22 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1720"
    nightly = "30 21,22 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1730"

    assert readiness in cron_text
    assert readiness_retry in cron_text
    assert readiness_final in cron_text
    assert readiness_post_open_repair in cron_text
    assert early_activity in cron_text
    assert activity in cron_text
    assert session_guard in cron_text
    assert profit_probe in cron_text
    assert nightly in cron_text
    assert cron_text.index(readiness) < cron_text.index(readiness_retry)
    assert cron_text.index(readiness_retry) < cron_text.index(readiness_final)
    assert cron_text.index(readiness_final) < cron_text.index(readiness_post_open_repair)
    assert cron_text.index(readiness_post_open_repair) < cron_text.index(early_activity)
    assert cron_text.index(early_activity) < cron_text.index(activity)
    assert cron_text.index(session_guard) < cron_text.index(profit_probe)
    assert cron_text.index(profit_probe) < cron_text.index(nightly)
    assert cron_text.count("scripts/run_if_ny_time.sh") == 9
    assert cron_text.count("scripts/run_locked_check_with_audit.sh") == 8
    assert "flock -n /var/lock/alpaca-bot-nightly.lock" in cron_text
    assert "flock -n /var/lock/alpaca-bot-paper" not in cron_text
    assert "flock -n /var/lock/alpaca-bot-session-guard.lock" not in cron_text
    assert "flock -n /var/lock/alpaca-bot-profit-probe.lock" not in cron_text
    assert "alpaca-bot-premarket" not in cron_text
    assert "scripts/paper_readiness_check.sh" in cron_text
    assert cron_text.count("scripts/paper_readiness_check.sh") == 2
    assert "scripts/paper_readiness_if_needed.sh" in cron_text
    assert cron_text.count("scripts/paper_readiness_if_needed.sh") == 2
    assert "run_locked_check_with_audit.sh paper_readiness" in cron_text
    assert "RUN_IF_NY_TIME_GRACE_MINUTES=1" in cron_text
    assert "/var/log/alpaca-bot-paper-readiness.log" in cron_text
    assert "scripts/paper_activity_check.sh" in cron_text
    assert cron_text.count("scripts/paper_activity_check.sh") == 2
    assert "run_locked_check_with_audit.sh paper_activity" in cron_text
    assert "/var/log/alpaca-bot-paper-activity.log" in cron_text
    assert "scripts/paper_profit_probe.sh" in cron_text
    assert "run_locked_check_with_audit.sh paper_profit_probe" in cron_text
    assert "/var/log/alpaca-bot-profit-probe.log" in cron_text
    assert "run_locked_check_with_audit.sh session_guard" in cron_text
    assert 'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in install_cron
    assert 'install -m 644 "$ROOT_DIR/deploy/cron.d/alpaca-bot" /etc/cron.d/alpaca-bot' in install_cron
    assert '"$ROOT_DIR/scripts/cron_health_check.sh"' in install_cron
    assert "Runs weekdays on New York wall time" in install_cron
    assert "paper readiness 09:20/09:55/09:58/10:02" in install_cron
    assert 'ACTUAL_HHMM="$(TZ=America/New_York date +%H%M)"' in run_if_ny_time
    assert "expected HHMM must be a valid 24-hour time" in run_if_ny_time
    assert "date returned invalid HHMM" in run_if_ny_time
    assert 'RUN_IF_NY_TIME_GRACE_MINUTES="${RUN_IF_NY_TIME_GRACE_MINUTES:-2}"' in run_if_ny_time
    assert "RUN_IF_NY_TIME_GRACE_MINUTES must be a non-negative integer" in run_if_ny_time
    assert "RUN_IF_NY_TIME_GRACE_MINUTES must be at most 10" in run_if_ny_time
    assert "delay_minutes=$((actual_minutes - expected_minutes))" in run_if_ny_time
    assert 'exec "$@"' in run_if_ny_time
    assert 'EXPECTED_CRON="$ROOT_DIR/deploy/cron.d/alpaca-bot"' in cron_health
    assert 'INSTALLED_CRON="${ALPACA_BOT_CRON_FILE:-/etc/cron.d/alpaca-bot}"' in cron_health
    assert 'cmp -s "$EXPECTED_CRON" "$INSTALLED_CRON"' in cron_health
    assert 'while read -r cron_user log_file' in cron_health
    assert 'user = $6' in cron_health
    assert '"$cron_user" != "root"' in cron_health
    assert "scheduled log target is not a file" in cron_health
    assert "scheduled log directory is missing" in cron_health
    assert "scheduled log target is not writable" in cron_health
    assert "scheduled log directory is not writable" in cron_health
    assert "systemctl is-active --quiet cron" in cron_health
    assert "ps -eo comm=" in cron_health
    assert "run_locked_check_with_audit.sh" in cron_health
    assert 'bash -n "$path"' in cron_health
    assert "required scheduled script has syntax errors" in cron_health
    assert "run_check_with_audit.sh" in cron_health
    assert "scheduled_check_lock_skipped.sh" in cron_health
    assert "paper_readiness_check.sh" in cron_health
    assert "paper_readiness_if_needed.sh" in cron_health
    assert "paper_activity_check.sh" in cron_health
    assert "session_guard.sh" in cron_health
    assert "paper_profit_probe.sh" in cron_health
    assert "cron health ok" in cron_health


def test_paper_readiness_final_retry_does_not_rerun_after_pass(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'paper_readiness_latest_status=2026-06-29|passed|2026-06-27T18:07:44.000000Z|2026-06-27T18:07:43.000000Z\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_readiness_if_needed.sh", str(env_file)],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 reason=already_passed"
    ) in result.stdout
    assert "paper readiness already passed for session 2026-06-29" in result.stdout
    assert "paper readiness check skipped" not in result.stdout


def test_paper_readiness_final_retry_reruns_after_supervisor_restart(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'paper_readiness_latest_status=2026-06-29|passed|2026-06-27T17:25:41.000000Z|2026-06-27T18:07:43.000000Z\\n'\n"
    )
    fake_docker.chmod(0o755)
    fake_readiness = tmp_path / "paper_readiness_check.sh"
    fake_readiness.write_text("#!/usr/bin/env bash\nprintf 'fresh readiness ran\\n'\n")
    fake_readiness.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_readiness_if_needed.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_READINESS_CHECK_SCRIPT": str(fake_readiness),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 reason=stale_after_supervisor_start"
    ) in result.stdout
    assert "paper readiness prior pass is older than latest supervisor start" in result.stdout
    assert "fresh readiness ran" in result.stdout
    assert "paper readiness already passed for session 2026-06-29" not in result.stdout


def test_paper_readiness_lock_skip_does_not_block_after_pass(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text("#!/usr/bin/env bash\nprintf '2026-07-04\\n'\n")
    fake_date.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'paper_readiness_session_date=2026-07-06\\n'\n"
        "printf 'paper_readiness_latest_status=passed|2026-06-27T18:07:44.000000Z|2026-06-27T18:07:43.000000Z\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "paper_readiness",
            str(tmp_path / "readiness.lock"),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "scheduled check context: session_date=2026-07-06 "
        "proof_start=2026-06-29 reason=lock_busy_already_passed"
    ) in result.stdout
    assert "paper readiness lock busy after prior pass for session 2026-07-06" in result.stdout
    assert "paper readiness check skipped" not in result.stdout


def test_paper_readiness_lock_skip_blocks_stale_pass_after_restart(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text("#!/usr/bin/env bash\nprintf '2026-07-04\\n'\n")
    fake_date.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'paper_readiness_session_date=2026-07-06\\n'\n"
        "printf 'paper_readiness_latest_status=passed|2026-06-27T17:25:41.000000Z|2026-06-27T18:07:43.000000Z\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "paper_readiness",
            str(tmp_path / "readiness.lock"),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 48
    assert (
        "scheduled check context: session_date=2026-07-06 "
        "proof_start=2026-06-29 reason=lock_busy_stale_pass"
    ) in result.stdout
    assert "paper readiness prior pass is older than latest supervisor start" in result.stderr


def test_paper_readiness_lock_skip_blocks_without_pass(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text("#!/usr/bin/env bash\nprintf '2026-07-04\\n'\n")
    fake_date.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'paper_readiness_session_date=2026-07-06\\n'\n"
        "printf 'paper_readiness_latest_status=\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "paper_readiness",
            str(tmp_path / "readiness.lock"),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 48
    assert (
        "scheduled check context: session_date=2026-07-06 "
        "proof_start=2026-06-29 reason=lock_busy"
    ) in result.stdout
    assert "scheduled check lock busy: check=paper_readiness" in result.stderr


def test_run_if_ny_time_allows_short_cron_delay(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$FAKE_HHMM\"\n")
    fake_date.chmod(0o755)

    marker = tmp_path / "ran"
    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "FAKE_HHMM": "0922",
        "MARKER": str(marker),
    }

    result = subprocess.run(
        [
            "scripts/run_if_ny_time.sh",
            "0920",
            "bash",
            "-c",
            "printf ran > \"$MARKER\"",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert marker.read_text() == "ran"


def test_run_if_ny_time_skips_outside_grace_window(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$FAKE_HHMM\"\n")
    fake_date.chmod(0o755)

    for actual_hhmm in ("0919", "0923", "1020"):
        marker = tmp_path / f"ran-{actual_hhmm}"
        env = {
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "FAKE_HHMM": actual_hhmm,
            "MARKER": str(marker),
        }

        result = subprocess.run(
            [
                "scripts/run_if_ny_time.sh",
                "0920",
                "bash",
                "-c",
                "printf ran > \"$MARKER\"",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
        )

        assert result.returncode == 0
        assert not marker.exists()


def test_run_if_ny_time_rejects_unsafe_grace_window() -> None:
    result = subprocess.run(
        ["scripts/run_if_ny_time.sh", "0920", "true"],
        cwd=Path.cwd(),
        env={
            "PATH": "/usr/bin:/bin",
            "RUN_IF_NY_TIME_GRACE_MINUTES": "60",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "RUN_IF_NY_TIME_GRACE_MINUTES must be at most 10" in result.stderr


def test_run_if_ny_time_rejects_invalid_hhmm() -> None:
    result = subprocess.run(
        ["scripts/run_if_ny_time.sh", "2460", "true"],
        cwd=Path.cwd(),
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "expected HHMM must be a valid 24-hour time" in result.stderr


def test_locked_check_wrapper_audits_lock_skips() -> None:
    wrapper = Path("scripts/run_locked_check_with_audit.sh").read_text()
    lock_skip = Path("scripts/scheduled_check_lock_skipped.sh").read_text()

    assert "flock -n -E 75" in wrapper
    assert '"$ROOT_DIR/scripts/run_check_with_audit.sh"' in wrapper
    assert '"$ROOT_DIR/scripts/scheduled_check_lock_skipped.sh"' in wrapper
    assert 'if [[ "$rc" -eq 75 ]]' in wrapper
    assert 'exit "$rc"' in wrapper
    assert "scheduled check lock busy" in lock_skip
    assert "scheduled check context:" in lock_skip
    assert "reason=lock_busy" in lock_skip
    assert "reason=lock_busy_already_passed" in lock_skip
    assert "paper_readiness_session_date=" in lock_skip
    assert "paper_readiness_latest_status=" in lock_skip
    assert "paper_readiness)" in lock_skip
    assert "paper_activity)" in lock_skip
    assert "proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} strategy=${PAPER_ACTIVITY_STRATEGY" in lock_skip
    assert "session_guard)" in lock_skip
    assert "proof_start=${SESSION_GUARD_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}} strategy=${SESSION_GUARD_STRATEGY" in lock_skip
    assert "paper_profit_probe)" in lock_skip
    assert "exit 48" in lock_skip


def test_run_check_with_audit_records_scheduled_check_result() -> None:
    script_path = Path("scripts/run_check_with_audit.sh")
    script = script_path.read_text()

    assert script_path.stat().st_mode & 0o111
    assert "scheduled_check_completed" in script
    assert 'RUN_CHECK_REQUIRE_AUDIT="${RUN_CHECK_REQUIRE_AUDIT:-true}"' in script
    assert "RUN_CHECK_REQUIRE_AUDIT must be true or false" in script
    assert 'AUDIT_CHECK_NAME="$CHECK_NAME"' in script
    assert 'AUDIT_STATUS="$status"' in script
    assert 'AUDIT_EXIT_CODE="$rc"' in script
    assert 'AUDIT_OUTPUT_TAIL="$output_tail"' in script
    assert 'AUDIT_CONTEXT_LINE="$context_line"' in script
    assert "-e AUDIT_CHECK_NAME" in script
    assert "-e AUDIT_STATUS" in script
    assert "-e AUDIT_EXIT_CODE" in script
    assert "-e AUDIT_OUTPUT_TAIL" in script
    assert "-e AUDIT_CONTEXT_LINE" in script
    assert 'output_tail="$(tail -c 4000 "$output_file" 2>/dev/null || true)"' in script
    assert 'context_line="$(grep -E' in script
    assert "scheduled check context: " in script
    assert "CONTEXT_KEYS" in script
    assert '"session_date"' in script
    assert '"previous_session_date"' in script
    assert '"proof_start"' in script
    assert '"reason"' in script
    assert "payload.update(parse_context" in script
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
    assert "audit_failed=false" in script
    assert "audit_failed=true" in script
    assert "scheduled check audit failed" in script
    assert "exit 47" in script
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
    assert 'PAPER_READINESS_CLOSE_ONLY_ON_FAILURE="${PAPER_READINESS_CLOSE_ONLY_ON_FAILURE:-true}"' in script
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
    assert "PAPER_READINESS_CLOSE_ONLY_ON_FAILURE must be true or false" in script
    assert "close_only_on_readiness_failure" in script
    assert "trap close_only_on_readiness_failure EXIT" in script
    assert "paper readiness failed for session ${PAPER_READINESS_SESSION_DATE:-unknown}: pre-open checks failed" in script
    assert "paper readiness warning: failed to apply close-only after readiness failure" in script
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
    context_index = script.index(
        "scheduled check context: session_date=$PAPER_READINESS_SESSION_DATE"
    )
    assert context_index < script.index("trap close_only_on_readiness_failure EXIT")
    assert context_index < script.index("run_market_data_smoke_check")
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
    assert "PAPER_READINESS_REQUIRE_WATCHLIST_ASSETS" in script
    assert "run_watchlist_asset_check" in script
    assert "load_active_watchlist_symbols" in script
    assert '-e PAPER_READINESS_ACTIVE_SYMBOLS="$active_symbols"' in script
    assert "AlpacaExecutionAdapter.from_settings(settings)" in script
    assert "get_all_assets(filter=asset_filter)" in script
    assert "missing_active_asset" in script
    assert "not_tradable" in script
    assert "paper readiness Alpaca assets ok" in script
    assert "paper readiness watchlist Alpaca asset check skipped" in script
    assert "paper readiness Alpaca non-fractionable symbols" in script
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
    assert "paper readiness broker account ok" in script
    assert "broker account not tradable" in script
    assert "minimum_required" in script
    assert "trading_blocked" in script
    assert "settings.max_position_pct" in script
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
    assert "scheduled check context: session_date=$PAPER_READINESS_SESSION_DATE" in script
    assert "previous_session_date=$PAPER_READINESS_PREVIOUS_SESSION_DATE" in script
    assert "scheduled_check_completed" in script
    assert "payload->>'session_date' = :'previous_session_date'" in script
    assert "NOT (payload ? 'session_date')" in script
    assert "payload->>'check_name' IN ('session_guard', 'paper_profit_probe')" in script
    assert "latest_checks AS" in script
    assert "missing AS" in script
    assert "invalid AS" in script
    assert "check_name = 'session_guard' AND status = 'passed'" in script
    assert "check_name = 'paper_profit_probe'" in script
    assert "OR (status = 'pending' AND exit_code = '43')" in script
    assert "check_name = 'paper_profit_probe' AND status IN ('passed', 'pending')" not in script
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
    assert "require_env_empty_or_unset OPTION_CHAIN_SYMBOLS" in script
    assert 'check("option_chain_symbols", settings.option_chain_symbols, ())' in script


def test_paper_activity_check_verifies_mid_session_evaluation() -> None:
    script = Path("scripts/paper_activity_check.sh").read_text()

    assert "PAPER_ACTIVITY_WINDOW_MINUTES" in script
    assert 'PAPER_ACTIVITY_MIN_DECISION_RECORDS="${PAPER_ACTIVITY_MIN_DECISION_RECORDS:-900}"' in script
    assert 'PAPER_ACTIVITY_REQUIRE_DECISION_LOG="${PAPER_ACTIVITY_REQUIRE_DECISION_LOG:-true}"' in script
    assert 'PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE="${PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE:-true}"' in script
    assert 'PAPER_ACTIVITY_READINESS_RUNNER="${PAPER_ACTIVITY_READINESS_RUNNER:-./scripts/run_locked_check_with_audit.sh}"' in script
    assert 'PAPER_ACTIVITY_READINESS_SCRIPT="${PAPER_ACTIVITY_READINESS_SCRIPT:-./scripts/paper_readiness_if_needed.sh}"' in script
    assert "PAPER_ACTIVITY_REQUIRE_DECISION_LOG must be true or false" in script
    assert "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE must be true or false" in script
    assert 'PAPER_ACTIVITY_STRATEGY="${PAPER_ACTIVITY_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"' in script
    assert "close_only_on_activity_failure" in script
    assert "trap close_only_on_activity_failure EXIT" in script
    assert "paper activity failed for session" in script
    assert "post-open checks failed for strategy" in script
    assert "paper activity warning: failed to apply close-only after activity failure" in script
    assert "PAPER_READINESS_AUTO_RESUME=false" in script
    assert "PAPER_READINESS_REQUIRE_FLAT=false" in script
    assert '"$PAPER_ACTIVITY_READINESS_RUNNER"' in script
    assert "paper_readiness" in script
    assert "/var/lock/alpaca-bot-paper-readiness.lock" in script
    assert '"$PAPER_ACTIVITY_READINESS_SCRIPT"' in script
    assert "./scripts/paper_readiness_check.sh" not in script
    assert "readiness repair lock busy" in script
    assert 'if [[ "$rc" -eq 43 ]]' in script
    assert (
        "scheduled check context: session_date=$(TZ=America/New_York date +%F) "
        "proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} strategy=$PAPER_ACTIVITY_STRATEGY"
    ) in script
    assert "decision_record_count" in script
    assert "decision_log" in script
    assert "latest_supervisor AS" in script
    assert "latest_cycle_entries_disabled" in script
    assert "latest_cycle_strategy_blocked" in script
    assert "strategy_decision_log_cycles" in script
    assert "strategy_decision_log_records" in script
    assert "strategy_evidence_records" in script
    assert "stock_open_positions" in script
    assert "active_stock_orders" in script
    assert "has_stock_exposure" in script
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
    assert "latest supervisor cycle had entries disabled" in script
    assert "latest $PAPER_ACTIVITY_STRATEGY entries blocked" in script
    assert "disabled_cycles=$disabled_cycles/$supervisor_cycles" in script
    assert "blocked_cycles=$strategy_blocked_cycles/$supervisor_cycles" in script
    assert "PAPER_ACTIVITY_STRATEGY contains unsupported characters" in script
    assert "emit_scheduled_context()" in script
    assert (
        'echo "scheduled check context: session_date=$(TZ=America/New_York date +%F) '
        'proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} strategy=$PAPER_ACTIVITY_STRATEGY"'
    ) in script
    assert "emit_scheduled_context\n\n  if [[ \"${PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE,,}\"" in script
    assert "emit_scheduled_context\n\nload_market_clock_status" in script
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


def test_paper_activity_allows_low_record_count_when_stock_exposure_exists(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "POSTGRES_USER=postgres",
                "POSTGRES_DB=postgres",
            ]
        )
    )
    fake_runner = tmp_path / "readiness_runner.sh"
    fake_runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'scheduled check context: session_date=2026-06-29 proof_start=2026-06-29 reason=already_passed\\n'\n"
        "exit 0\n"
    )
    fake_runner.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text("#!/usr/bin/env bash\nprintf '2026-06-29\\n'\n")
    fake_date.chmod(0o755)
    docker_marker = tmp_path / "docker_close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "if printf '%s\\n' \"$*\" | grep -q ' admin close-only'; then\n"
        f"  touch {docker_marker}\n"
        "  exit 99\n"
        "fi\n"
        "printf '10|0|10|10|0|2026-06-29 16:00:00+00|false||false||2026-06-29 16:00:00+00|0|10|10|0|10|10|2026-06-29 16:00:00+00|bull_flag|||3|0\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_MIN_DECISION_RECORDS": "900",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "paper activity ok:" in result.stdout
    assert "bull_flag_decision_log_records=10" in result.stdout
    assert "stock_open_positions=3" in result.stdout
    assert "active_stock_orders=0" in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_allows_recovered_disabled_cycles(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "POSTGRES_USER=postgres",
                "POSTGRES_DB=postgres",
            ]
        )
    )
    fake_runner = tmp_path / "readiness_runner.sh"
    fake_runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'scheduled check context: session_date=2026-06-29 proof_start=2026-06-29 reason=already_passed\\n'\n"
        "exit 0\n"
    )
    fake_runner.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text("#!/usr/bin/env bash\nprintf '2026-06-29\\n'\n")
    fake_date.chmod(0o755)
    docker_marker = tmp_path / "docker_close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "if printf '%s\\n' \"$*\" | grep -q ' admin close-only'; then\n"
        f"  touch {docker_marker}\n"
        "  exit 99\n"
        "fi\n"
        "printf '12|4|8|7840|0|2026-06-29 14:15:00+00|false||false||2026-06-29 14:15:00+00|4|8|7840|0|8|7840|2026-06-29 14:15:00+00|bull_flag|paper_readiness_check_missing:4|paper_readiness_check_missing:4|0|0\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_MIN_DECISION_RECORDS": "900",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "paper activity ok:" in result.stdout
    assert "disabled_cycles=4" in result.stdout
    assert "latest_cycle_entries_disabled=false" in result.stdout
    assert "bull_flag_decision_log_records=7840" in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_readiness_lock_busy_is_pending_without_close_only(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
            ]
        )
    )
    fake_runner = tmp_path / "readiness_runner.sh"
    fake_runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'scheduled check context: session_date=2026-06-29 proof_start=2026-06-29 reason=lock_busy_stale_pass\\n'\n"
        "printf 'paper readiness prior pass is older than latest supervisor start; lock busy remains blocking\\n' >&2\n"
        "exit 48\n"
    )
    fake_runner.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_marker = tmp_path / "docker_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {docker_marker}\n"
        "printf 'docker should not be called for pending readiness lock\\n' >&2\n"
        "exit 99\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert (
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 reason=lock_busy_stale_pass"
    ) in result.stdout
    assert "scheduled check context: session_date=" in result.stdout
    assert "proof_start=2026-06-29" in result.stdout
    assert "strategy=bull_flag" in result.stdout
    assert "paper activity pending: readiness repair lock busy" in result.stdout
    assert not docker_marker.exists()
    assert "docker should not be called" not in result.stderr


def test_session_guard_pending_before_proof_start_does_not_close_only(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
            ]
        )
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    session_eval_marker = tmp_path / "session_eval_called"
    close_only_marker = tmp_path / "close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-session-eval'; then\n"
        f"  touch {session_eval_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q ' admin close-only'; then\n"
        f"  touch {close_only_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'BROKER_FLAT_CONTEXT'; then\n"
        "  printf 'bull_flag session guard pending 2026-06-29 broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '2026-06-26\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/session_guard.sh", str(env_file)],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert (
        "scheduled check context: session_date=2026-06-26 "
        "proof_start=2026-06-29 strategy=bull_flag"
    ) in result.stdout
    assert (
        "session guard pending: latest completed session 2026-06-26 "
        "is before proof start 2026-06-29"
    ) in result.stdout
    assert "broker exposure ok: open_orders=0 open_positions=0" in result.stdout
    assert not session_eval_marker.exists()
    assert not close_only_marker.exists()


def test_paper_proof_status_is_read_only(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
            ]
        )
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker_calls"
    mutating_marker = tmp_path / "mutating_call"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        f"printf '%s\\n' \"$args\" >> \"{docker_calls}\"\n"
        "case \"$args\" in\n"
        "  *close-only*|*resume*|*alpaca-bot-session-eval*)\n"
        f"    touch \"{mutating_marker}\"\n"
        "    printf 'mutating docker call: %s\\n' \"$args\" >&2\n"
        "    exit 99\n"
        "    ;;\n"
        "esac\n"
        "if [[ \"$args\" == *' admin status '* ]]; then\n"
        "  printf 'status=enabled kill_switch=false reason=proof running\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$args\" == *'alpaca-bot-ops-check admin'* ]]; then\n"
        "  printf 'status=ok db=ok trading_mode=paper strategy_version=v1-breakout trading_status=enabled kill_switch_enabled=False enabled_strategies=bull_flag worker_status=fresh\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$args\" == *'--entrypoint python admin'* ]]; then\n"
        "  printf 'paper proof active strategies: bull_flag\\n'\n"
        "  printf 'paper proof runtime: ops_status=ok ops_detail=status=ok db=ok trading_mode=paper strategy_version=v1-breakout trading_status=enabled kill_switch_enabled=False enabled_strategies=bull_flag worker_status=fresh\\n'\n"
        "  printf 'paper proof readiness audit: status=ok target_session=2026-06-29 check_status=passed created_at=2026-06-29T13:20:00+00:00 latest_supervisor_started_at=2026-06-29T13:00:00+00:00\\n'\n"
        "  printf 'paper proof post-close audit: status=ok target_session=2026-06-29 due=true due_after=2026-06-29 17:25 America/New_York session_guard=passed:0:2026-06-29T21:10:00+00:00 paper_profit_probe=pending:43:2026-06-29T21:20:00+00:00\\n'\n"
        "  printf 'paper proof scheduled check: name=paper_profit_probe status=pending exit_code=43 session_date=2026-06-26 proof_start=2026-06-29 created_at=2026-06-27T22:00:00.000000Z\\n'\n"
        "  printf 'paper proof progress: status=pending closed_trades=3 required_trades=10 pnl=12.34 required_pnl=0.01 window=2026-06-29..2026-06-29 first_exit_session=2026-06-29 latest_exit_session=2026-06-29\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'unexpected docker call: %s\\n' \"$args\" >&2\n"
        "exit 99\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_proof_status.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PROOF_STATUS_END_DATE": "2026-06-29",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "paper proof status context: proof_start=2026-06-29 mode=paper "
        "strategy_version=v1-breakout strategy=bull_flag min_trades=10 min_pnl=0.01"
    ) in result.stdout
    assert "  status=enabled kill_switch=false reason=proof running" in result.stdout
    assert "paper proof active strategies: bull_flag" in result.stdout
    assert "paper proof runtime: ops_status=ok" in result.stdout
    assert "paper proof readiness audit: status=ok target_session=2026-06-29" in result.stdout
    assert "paper proof post-close audit: status=ok target_session=2026-06-29" in result.stdout
    assert "paper proof scheduled check: name=paper_profit_probe status=pending" in result.stdout
    assert (
        "paper proof progress: status=pending closed_trades=3 "
        "required_trades=10 pnl=12.34 required_pnl=0.01"
    ) in result.stdout
    assert not mutating_marker.exists()
    calls = docker_calls.read_text()
    assert "close-only" not in calls
    assert "resume" not in calls
    assert "alpaca-bot-session-eval" not in calls


def test_paper_proof_status_labels_pre_start_window_with_completed_session() -> None:
    script = Path("scripts/paper_proof_status.sh").read_text()

    assert "load_latest_completed_session_date" in script
    assert "load_next_market_session_date" in script
    assert "AlpacaExecutionAdapter.from_settings" in script
    assert "get_market_calendar" in script
    assert "close_at + timedelta(minutes=30)" in script
    assert "not_started(" in script
    assert "latest_completed_session=" in script
    assert "current_market_date=" in script
    assert "scoring_end_date=" in script
    assert "paper proof summary:" in script
    assert "readiness={readiness_status}" in script
    assert "proof={proof_status}" in script
    assert "reason={proof_reason}" in script
    assert "blockers={','.join(blockers) if blockers else 'none'}" in script
    assert "warnings={','.join(warnings) if warnings else 'none'}" in script
    assert "./scripts/cron_health_check.sh 2>&1" in script
    assert "PROOF_STATUS_CRON_HEALTH_STATUS" in script
    assert "PROOF_STATUS_CRON_HEALTH_DETAIL" in script
    assert "./scripts/ops_check.sh \"$ENV_FILE\" 2>&1" in script
    assert "PROOF_STATUS_OPS_HEALTH_STATUS" in script
    assert "PROOF_STATUS_OPS_HEALTH_DETAIL" in script
    assert "cron_health_failed" in script
    assert "ops_health_failed" in script
    assert "compact_check_detail()" in script
    assert "paper proof automation:" in script
    assert "cron_status={cron_health_status}" in script
    assert "cron_detail={cron_health_detail or 'none'}" in script
    assert "paper proof runtime:" in script
    assert "ops_status={ops_health_status}" in script
    assert "ops_detail={ops_health_detail or 'none'}" in script
    assert "readiness_target_session = next_market_session or current_market_date" in script
    assert "if readiness_target_session < proof_start" in script
    assert "event_type = 'supervisor_started'" in script
    assert "latest_supervisor_started_at" in script
    assert "payload->>'check_name' = 'paper_readiness'" in script
    assert "payload->>'session_date' = %s" in script
    assert "readiness_audit_status" in script
    assert "readiness_audit_status = \"stale\"" in script
    assert "readiness_audit_{readiness_audit_status}" in script
    assert "paper proof readiness audit:" in script
    assert "status={readiness_audit_status}" in script
    assert "target_session={readiness_target_session.isoformat()}" in script
    assert "check_status={readiness_audit_check_status}" in script
    assert "created_at={readiness_audit_created_text}" in script
    assert "from datetime import date, datetime, time, timedelta" in script
    assert "post_close_target_session = proof_end if proof_end >= proof_start else None" in script
    assert "post_close_audit_rows = []" in script
    assert "payload->>'check_name' IN (" in script
    assert "'session_guard'" in script
    assert "'paper_profit_probe'" in script
    assert "payload->>'proof_start' = %s" in script
    assert "due_time = time(17, 25)" in script
    assert "post_close_due_after" in script
    assert "post_close_audit_status = \"not_due\"" in script
    assert "post_close_audit_status = \"missing\"" in script
    assert "post_close_audit_status = \"failed\"" in script
    assert "post_close_audit_status = \"ok\"" in script
    assert "profit_probe_status == \"pending\" and profit_probe_exit_code == \"43\"" in script
    assert "blockers.append(f\"post_close_audit_{post_close_audit_status}\")" in script
    assert "paper proof post-close audit:" in script
    assert "status={post_close_audit_status}" in script
    assert "target_session={post_close_target_session.isoformat() if post_close_target_session else 'none'}" in script
    assert "due={str(post_close_due).lower()}" in script
    assert "session_guard={post_close_check_statuses['session_guard']}" in script
    assert "paper_profit_probe={post_close_check_statuses['paper_profit_probe']}" in script
    assert "strategy_disabled" in script
    assert "posture_drifted" in script
    assert "broker_account_blocked" in script
    assert "awaiting_completed_proof_session" in script
    assert "awaiting_min_trades" in script
    assert "profit_proven" in script
    assert "paper proof strategy status:" in script
    assert "status={strategy_status} target={strategy_name}" in script
    assert "paper proof posture:" in script
    assert "status={posture_status}" in script
    assert "relative_volume_threshold={settings.relative_volume_threshold:g}" in script
    assert "max_open_positions={settings.max_open_positions}" in script
    assert "abs(float(settings.relative_volume_threshold) - 2.0)" in script
    assert "int(settings.max_open_positions) == 3" in script
    assert "bool(settings.enable_vwap_entry_filter)" in script
    assert "not bool(settings.enable_vix_filter)" in script
    assert "not bool(settings.enable_sector_filter)" in script
    assert "not bool(settings.extended_hours_enabled)" in script
    assert "bool(settings.paper_proof_freeze)" in script
    assert "int(settings.intraday_consecutive_loss_gate) == 0" in script
    assert "paper proof local exposure:" in script
    assert "positions={local_open_positions} active_orders={local_active_orders}" in script
    assert "load_broker_exposure" in script
    assert "broker.list_open_orders()" in script
    assert "broker.list_positions()" in script
    assert "broker.get_account()" in script
    assert "paper proof broker exposure:" in script
    assert "open_orders={broker_open_orders} open_positions={broker_open_positions}" in script
    assert "paper proof broker account:" in script
    assert "status={broker_account_status}" in script
    assert "equity={broker_equity:.2f}" in script
    assert "buying_power={broker_buying_power:.2f}" in script
    assert "minimum_required={broker_minimum_buying_power:.2f}" in script
    assert "trading_blocked={str(broker_trading_blocked).lower()}" in script
    assert "'pending_submit'" in script
    assert "'partially_filled'" in script
    assert "latest_market_date" not in script


def test_post_close_checks_fail_on_open_positions() -> None:
    session_guard = Path("scripts/session_guard.sh").read_text()
    profit_probe = Path("scripts/paper_profit_probe.sh").read_text()

    assert "--fail-on-open-positions" in session_guard
    assert "--fail-on-open-positions" in profit_probe
    assert 'if [[ ! -f "$ENV_FILE" ]]' in session_guard
    assert "missing env file: $ENV_FILE" in session_guard
    assert 'SESSION_GUARD_FAIL_ON_DIAGNOSTICS="${SESSION_GUARD_FAIL_ON_DIAGNOSTICS:-true}"' in session_guard
    assert 'SESSION_GUARD_START_DATE="${SESSION_GUARD_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}}"' in session_guard
    assert "SESSION_GUARD_FAIL_ON_DIAGNOSTICS must be true or false" in session_guard
    assert "load_latest_completed_session_date" in session_guard
    assert "AlpacaExecutionAdapter.from_settings" in session_guard
    assert "get_market_calendar" in session_guard
    assert "close_at + timedelta(minutes=30)" in session_guard
    assert "session guard warning: market calendar lookup failed; using weekday fallback" in session_guard
    assert "SESSION_GUARD_DATE must use YYYY-MM-DD" in session_guard
    assert "SESSION_GUARD_START_DATE must use YYYY-MM-DD" in session_guard
    assert "session guard pending: latest completed session" in session_guard
    assert "session guard pending ${SESSION_GUARD_START_DATE}: broker exposure remains before proof start" in session_guard
    assert "scheduled check context: session_date=$SESSION_GUARD_DATE proof_start=$SESSION_GUARD_START_DATE" in session_guard
    assert 'hhmm="$(TZ=America/New_York date +%H%M)"' in session_guard
    assert '"$hhmm" -ge 1630' in session_guard
    assert '1) TZ=America/New_York date -d "3 days ago" +%F ;;' in session_guard
    assert '6) TZ=America/New_York date -d "1 day ago" +%F ;;' in session_guard
    assert '7) TZ=America/New_York date -d "2 days ago" +%F ;;' in session_guard
    assert '*) TZ=America/New_York date -d "1 day ago" +%F ;;' in session_guard
    assert "session_eval_args+=(--fail-on-diagnostics)" in session_guard
    assert "./scripts/broker_flat_check.sh" in session_guard
    assert "./scripts/broker_flat_check.sh" in profit_probe
    assert "broker exposure remains after close" in session_guard
    assert "broker exposure remains after close" in profit_probe
    assert "broker_flat_failed=true\n  rc=44" in session_guard
    assert "broker_flat_failed=true\n  rc=44" in profit_probe
    assert 'PROFIT_PROBE_START_DATE="${PROFIT_PROBE_START_DATE:-2026-06-29}"' in profit_probe
    assert 'PROFIT_PROBE_FAIL_ON_DIAGNOSTICS="${PROFIT_PROBE_FAIL_ON_DIAGNOSTICS:-true}"' in profit_probe
    assert "PROFIT_PROBE_FAIL_ON_DIAGNOSTICS must be true or false" in profit_probe
    assert "session_eval_args=(" in profit_probe
    assert "session_eval_args+=(--fail-on-diagnostics)" in profit_probe
    assert "paper profit probe pending: latest completed session" in profit_probe
    assert "scheduled check context: session_date=$PROFIT_PROBE_DATE proof_start=$PROFIT_PROBE_START_DATE" in profit_probe
    assert 'PROFIT_PROBE_DATE" < "$PROFIT_PROBE_START_DATE' in profit_probe
    assert "paper proof pending" in profit_probe
    assert "paper proof pending ${PROFIT_PROBE_START_DATE}: broker exposure remains before proof start" in profit_probe
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
    assert '"$rc" -eq 42 || "$rc" -eq 44 || "$rc" -eq 46' in session_guard
    assert "open positions remain after close" in session_guard
    assert "session guard failed ${SESSION_GUARD_DATE}: operational diagnostics contain proof-blocking issues" in session_guard
    assert "session guard failed: could not apply close-only guard" in session_guard
    assert "exit 45" in session_guard
    assert '"$rc" -eq 42 || "$rc" -eq 44 || "$rc" -eq 46' in profit_probe
    assert '"$rc" -eq 42 || "$rc" -eq 43' in profit_probe
    assert "paper proof failed" in profit_probe
    assert "paper proof incomplete ${PROFIT_PROBE_START_DATE}..${PROFIT_PROBE_DATE}: fewer than ${PROFIT_PROBE_MIN_TRADES} closed trades" not in profit_probe
    assert "paper proof failed ${PROFIT_PROBE_START_DATE}..${PROFIT_PROBE_DATE}: operational diagnostics contain proof-blocking issues" in profit_probe
    assert "close-only" in profit_probe
    assert "alpaca-bot-funnel-report" in profit_probe
    assert '--strategy "$PROFIT_PROBE_STRATEGY"' in profit_probe
    assert "paper profit probe warning: funnel diagnostic failed" in profit_probe
    assert "paper profit probe failed: could not apply close-only guard" in profit_probe
    assert "exit 45" in profit_probe
