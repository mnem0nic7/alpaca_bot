import subprocess
from pathlib import Path


def test_cron_runs_session_guard_profit_probe_then_nightly() -> None:
    cron_text = Path("deploy/cron.d/alpaca-bot").read_text()
    install_cron = Path("scripts/install_cron.sh").read_text()
    run_if_ny_time = Path("scripts/run_if_ny_time.sh").read_text()
    cron_health = Path("scripts/cron_health_check.sh").read_text()

    readiness = "15 13,14 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 0915"
    readiness_retry = "55 13,14 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 0955"
    readiness_final = (
        "58 13,14 * * 1-5 root RUN_IF_NY_TIME_GRACE_MINUTES=1 "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 0958"
    )
    readiness_post_open_repair = (
        "2 14,15 * * 1-5 root RUN_IF_NY_TIME_GRACE_MINUTES=1 "
        "PAPER_READINESS_REQUIRE_FLAT=false /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1002"
    )
    readiness_post_open_repair_1005 = (
        "5 14,15 * * 1-5 root RUN_IF_NY_TIME_GRACE_MINUTES=1 "
        "PAPER_READINESS_REQUIRE_FLAT=false /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1005"
    )
    readiness_post_open_repair_1010 = (
        "10 14,15 * * 1-5 root RUN_IF_NY_TIME_GRACE_MINUTES=1 "
        "PAPER_READINESS_REQUIRE_FLAT=false /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1010"
    )
    readiness_stale_repair_1015 = (
        "15 14,15 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1015"
    )
    readiness_stale_repair_1045 = (
        "45 14,15 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1045"
    )
    readiness_stale_repair_1115 = (
        "15 15,16 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1115"
    )
    readiness_stale_repair_1145 = (
        "45 15,16 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1145"
    )
    readiness_midday_refresh = (
        "15 16,17 * * 1-5 root PAPER_READINESS_FORCE_REFRESH=true "
        "PAPER_READINESS_REQUIRE_FLAT=false /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1215"
    )
    readiness_stale_repair_1245 = (
        "45 16,17 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1245"
    )
    readiness_stale_repair_1315 = (
        "15 17,18 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1315"
    )
    readiness_stale_repair_1345 = (
        "45 17,18 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1345"
    )
    readiness_stale_repair_1415 = (
        "15 18,19 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1415"
    )
    readiness_afternoon_refresh = (
        "25 18,19 * * 1-5 root PAPER_READINESS_FORCE_REFRESH=true "
        "PAPER_READINESS_REQUIRE_FLAT=false /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1425"
    )
    readiness_stale_repair_1445 = (
        "45 18,19 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1445"
    )
    readiness_stale_repair_1515 = (
        "15 19,20 * * 1-5 root PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1515"
    )
    readiness_post_close_refresh = (
        "55 20,21 * * 1-5 root PAPER_READINESS_FORCE_REFRESH=true "
        "PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1655"
    )
    readiness_pre_proof_refresh = (
        "24 21,22 * * 1-5 root PAPER_READINESS_FORCE_REFRESH=true "
        "PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1724"
    )
    early_activity = (
        "25 14,15 * * 1-5 root PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1025"
    )
    first_fatal_activity = "35 14,15 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1035"
    activity = "0 16,17 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1200"
    late_activity = "35 18,19 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1435"
    session_guard = "10 21,22 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1710"
    profit_probe = "20 21,22 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1720"
    proof_status = (
        "28 21,22 * * 1-5 root PROOF_STATUS_FAIL_ON_ISSUES=true "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1728"
    )
    nightly = "30 21,22 * * 1-5 root /workspace/alpaca_bot/scripts/run_if_ny_time.sh 1730"

    assert readiness in cron_text
    assert readiness_retry in cron_text
    assert readiness_final in cron_text
    assert readiness_post_open_repair in cron_text
    assert readiness_post_open_repair_1005 in cron_text
    assert readiness_post_open_repair_1010 in cron_text
    assert readiness_stale_repair_1015 in cron_text
    assert readiness_stale_repair_1045 in cron_text
    assert readiness_stale_repair_1115 in cron_text
    assert readiness_stale_repair_1145 in cron_text
    assert readiness_midday_refresh in cron_text
    assert readiness_stale_repair_1245 in cron_text
    assert readiness_stale_repair_1315 in cron_text
    assert readiness_stale_repair_1345 in cron_text
    assert readiness_stale_repair_1415 in cron_text
    assert readiness_afternoon_refresh in cron_text
    assert readiness_stale_repair_1445 in cron_text
    assert readiness_stale_repair_1515 in cron_text
    assert readiness_post_close_refresh in cron_text
    assert readiness_pre_proof_refresh in cron_text
    assert early_activity in cron_text
    assert first_fatal_activity in cron_text
    assert activity in cron_text
    assert late_activity in cron_text
    assert session_guard in cron_text
    assert profit_probe in cron_text
    assert proof_status in cron_text
    assert nightly in cron_text
    assert cron_text.index(readiness) < cron_text.index(readiness_retry)
    assert cron_text.index(readiness_retry) < cron_text.index(readiness_final)
    assert cron_text.index(readiness_final) < cron_text.index(readiness_post_open_repair)
    assert cron_text.index(readiness_post_open_repair) < cron_text.index(readiness_post_open_repair_1005)
    assert cron_text.index(readiness_post_open_repair_1005) < cron_text.index(readiness_post_open_repair_1010)
    assert cron_text.index(readiness_post_open_repair_1010) < cron_text.index(readiness_stale_repair_1015)
    assert cron_text.index(readiness_stale_repair_1015) < cron_text.index(early_activity)
    assert cron_text.index(early_activity) < cron_text.index(first_fatal_activity)
    assert cron_text.index(first_fatal_activity) < cron_text.index(readiness_stale_repair_1045)
    assert cron_text.index(first_fatal_activity) < cron_text.index(activity)
    assert cron_text.index(early_activity) < cron_text.index(readiness_stale_repair_1045)
    assert cron_text.index(readiness_stale_repair_1045) < cron_text.index(readiness_stale_repair_1115)
    assert cron_text.index(readiness_stale_repair_1115) < cron_text.index(readiness_stale_repair_1145)
    assert cron_text.index(readiness_stale_repair_1145) < cron_text.index(activity)
    assert cron_text.index(activity) < cron_text.index(readiness_midday_refresh)
    assert cron_text.index(readiness_midday_refresh) < cron_text.index(readiness_stale_repair_1245)
    assert cron_text.index(readiness_stale_repair_1245) < cron_text.index(readiness_stale_repair_1315)
    assert cron_text.index(readiness_stale_repair_1315) < cron_text.index(readiness_stale_repair_1345)
    assert cron_text.index(readiness_stale_repair_1345) < cron_text.index(readiness_stale_repair_1415)
    assert cron_text.index(readiness_stale_repair_1415) < cron_text.index(readiness_afternoon_refresh)
    assert cron_text.index(readiness_afternoon_refresh) < cron_text.index(late_activity)
    assert cron_text.index(late_activity) < cron_text.index(readiness_stale_repair_1445)
    assert cron_text.index(readiness_stale_repair_1445) < cron_text.index(readiness_stale_repair_1515)
    assert cron_text.index(readiness_stale_repair_1515) < cron_text.index(readiness_post_close_refresh)
    assert cron_text.index(readiness_afternoon_refresh) < cron_text.index(readiness_post_close_refresh)
    assert cron_text.index(readiness_post_close_refresh) < cron_text.index(session_guard)
    assert cron_text.index(session_guard) < cron_text.index(profit_probe)
    assert cron_text.index(profit_probe) < cron_text.index(readiness_pre_proof_refresh)
    assert cron_text.index(readiness_pre_proof_refresh) < cron_text.index(proof_status)
    assert cron_text.index(profit_probe) < cron_text.index(proof_status)
    assert cron_text.index(proof_status) < cron_text.index(nightly)
    assert cron_text.index(profit_probe) < cron_text.index(nightly)
    assert cron_text.count("scripts/run_if_ny_time.sh") == 28
    assert cron_text.count("scripts/run_locked_check_with_audit.sh") == 27
    assert "flock -n /var/lock/alpaca-bot-nightly.lock" in cron_text
    assert "flock -n /var/lock/alpaca-bot-paper" not in cron_text
    assert "flock -n /var/lock/alpaca-bot-session-guard.lock" not in cron_text
    assert "flock -n /var/lock/alpaca-bot-profit-probe.lock" not in cron_text
    assert "alpaca-bot-premarket" not in cron_text
    assert "scripts/paper_readiness_check.sh" in cron_text
    assert cron_text.count("scripts/paper_readiness_check.sh") == 2
    assert "scripts/paper_readiness_if_needed.sh" in cron_text
    assert cron_text.count("scripts/paper_readiness_if_needed.sh") == 18
    assert cron_text.count("PAPER_READINESS_FORCE_REFRESH=true") == 4
    assert cron_text.count("PAPER_READINESS_REQUIRE_FLAT=false") == 15
    assert (
        "PAPER_READINESS_FORCE_REFRESH=true PAPER_READINESS_REQUIRE_FLAT=false "
        "/workspace/alpaca_bot/scripts/run_if_ny_time.sh 1425"
    ) in cron_text
    assert cron_text.count("PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED=false") == 2
    assert cron_text.index("PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED=false") < cron_text.index(
        "run_if_ny_time.sh 1655"
    )
    assert "run_locked_check_with_audit.sh paper_readiness" in cron_text
    assert "RUN_IF_NY_TIME_GRACE_MINUTES=1" in cron_text
    assert "/var/log/alpaca-bot-paper-readiness.log" in cron_text
    assert "scripts/paper_activity_check.sh" in cron_text
    assert cron_text.count("scripts/paper_activity_check.sh") == 4
    assert cron_text.count("PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE=false") == 1
    assert cron_text.index("PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE=false") < cron_text.index(
        "run_if_ny_time.sh 1025"
    )
    assert first_fatal_activity in cron_text
    assert cron_text.index("run_if_ny_time.sh 1025") < cron_text.index("run_if_ny_time.sh 1035")
    assert cron_text.index("run_if_ny_time.sh 1035") < cron_text.index("run_if_ny_time.sh 1200")
    assert "run_locked_check_with_audit.sh paper_activity" in cron_text
    assert "/var/log/alpaca-bot-paper-activity.log" in cron_text
    assert "scripts/paper_profit_probe.sh" in cron_text
    assert "run_locked_check_with_audit.sh paper_profit_probe" in cron_text
    assert "/var/log/alpaca-bot-profit-probe.log" in cron_text
    assert "scripts/paper_proof_status.sh" in cron_text
    assert "run_locked_check_with_audit.sh paper_proof_status" in cron_text
    assert "/var/lock/alpaca-bot-proof-status.lock" in cron_text
    assert "PROOF_STATUS_FAIL_ON_ISSUES=true" in cron_text
    assert "/var/log/alpaca-bot-proof-status.log" in cron_text
    assert "run_locked_check_with_audit.sh session_guard" in cron_text
    assert 'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in install_cron
    assert 'install -m 644 "$ROOT_DIR/deploy/cron.d/alpaca-bot" /etc/cron.d/alpaca-bot' in install_cron
    assert '"$ROOT_DIR/scripts/cron_health_check.sh"' in install_cron
    assert "Runs weekdays on New York wall time" in install_cron
    assert "paper readiness 09:15/09:55/09:58/10:02/10:05/10:10 plus stale-repair checks from 10:15-15:15" in install_cron
    assert "force refresh 12:15/14:25/16:55/17:24" in install_cron
    assert "paper activity 10:25/10:35/12:00/14:35" in install_cron
    assert "proof status 17:28" in install_cron
    assert "scripts/apply_candidate.sh" in cron_text
    assert "docker compose --env-file /etc/alpaca_bot/alpaca-bot.env -f deploy/compose.yaml run --rm nightly" in cron_text
    assert "docker compose -f deploy/compose.yaml run --rm nightly" not in cron_text
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
    assert "normalize_cron_for_required_drift()" in cron_health
    assert "<paper_proof_status_command>" in cron_health
    assert "installed cron differs from repo required schedule" in cron_health
    assert 'cmp -s <(normalize_cron_for_required_drift "$EXPECTED_CRON")' in cron_health
    assert "expected_proof_status_line=" in cron_health
    assert "installed_proof_status_line=" in cron_health
    assert "installed paper proof status command differs from repo schedule" in cron_health
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
    assert "paper_decision_dry_run.sh" in cron_health
    assert "paper_readiness_check.sh" in cron_health
    assert "paper_readiness_if_needed.sh" in cron_health
    assert "paper_activity_check.sh" in cron_health
    assert "session_guard.sh" in cron_health
    assert "paper_profit_probe.sh" in cron_health
    assert "paper_proof_status.sh" in cron_health
    assert "apply_candidate.sh" in cron_health
    assert "runtime_image_health_check.sh" in cron_health
    assert "cron health ok" in cron_health


def test_paper_readiness_final_retry_does_not_rerun_after_pass(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
                "PAPER_READINESS_CHECK_SCRIPT=/bin/false",
                "PAPER_READINESS_FORCE_REFRESH=false",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'paper_readiness_latest_status=2026-06-29|passed|2026-06-27T18:07:44.000000Z|2026-06-27T18:07:43.000000Z\\n'\n"
        "printf 'paper_readiness_latest_decision_dry_run=paper decision dry run ok: strategy=bull_flag as_of=2026-06-26T11:30:00-04:00 active=980 decision_records=941 accepted=3 entry_intents=3 sample_times=10:30,11:30,12:30,13:30,14:30,15:30 evaluations=6 min_decision_records=929 max_accepted=3 max_entry_intents=3\\n'\n"
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
    assert "paper decision dry run ok: strategy=bull_flag" in result.stdout
    assert "decision_records=941" in result.stdout
    assert "sample_times=10:30,11:30,12:30,13:30,14:30,15:30" in result.stdout
    assert "paper readiness already passed for session 2026-06-29" in result.stdout
    assert "paper readiness check skipped" not in result.stdout


def test_paper_readiness_final_retry_reruns_after_pass_without_dry_run(
    tmp_path: Path,
) -> None:
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
        "proof_start=2026-06-29 reason=decision_dry_run_missing"
    ) in result.stdout
    assert "lacks accepted entry-intent decision dry-run proof (missing)" in result.stdout
    assert "fresh readiness ran" in result.stdout
    assert "paper readiness already passed for session 2026-06-29" not in result.stdout


def test_paper_readiness_final_retry_reruns_after_pass_with_zero_entry_intents(
    tmp_path: Path,
) -> None:
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
        "printf 'paper_readiness_latest_decision_dry_run=paper decision dry run ok: strategy=bull_flag as_of=2026-06-26T11:30:00-04:00 active=980 decision_records=941 accepted=2 entry_intents=0 sample_times=10:30,11:30,12:30,13:30,14:30,15:30 evaluations=6 min_decision_records=929 max_accepted=2 max_entry_intents=0\\n'\n"
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
        "proof_start=2026-06-29 "
        "reason=decision_dry_run_entry_intents_under_minimum"
    ) in result.stdout
    assert "paper decision dry run ok: strategy=bull_flag" in result.stdout
    assert "max_entry_intents=0" in result.stdout
    assert "fresh readiness ran" in result.stdout
    assert "paper readiness already passed for session 2026-06-29" not in result.stdout


def test_paper_readiness_force_refresh_reruns_after_recent_pass_without_age(
    tmp_path: Path,
) -> None:
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
        "printf 'paper_readiness_latest_status=2026-06-29|passed|2026-06-29T16:55:00.000000Z|2026-06-29T14:01:00.000000Z|5\\n'\n"
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
            "PAPER_READINESS_FORCE_REFRESH": "true",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 reason=force_refresh"
    ) in result.stdout
    assert "paper readiness force refresh requested" in result.stdout
    assert "fresh readiness ran" in result.stdout
    assert "paper readiness already passed for session 2026-06-29" not in result.stdout


def test_paper_readiness_if_needed_preserves_check_overrides_after_env_source(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
                "PAPER_READINESS_AUTO_RESUME=true",
                "PAPER_READINESS_REQUIRE_FLAT=true",
                "PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED=true",
                "PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE=true",
                "PAPER_READINESS_REQUIRE_DECISION_DRY_RUN=true",
                "PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS=0",
                "PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS=900",
                "PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED=false",
                "PAPER_READINESS_MAX_PASS_AGE_MINUTES=180",
                "PAPER_READINESS_PREVIOUS_SESSION_DATE=2026-06-26",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'paper_readiness_latest_status=2026-06-29|failed|2026-06-29T16:55:00.000000Z|2026-06-29T14:01:00.000000Z|5\\n'\n"
    )
    fake_docker.chmod(0o755)
    fake_readiness = tmp_path / "paper_readiness_check.sh"
    fake_readiness.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'auto=%s flat=%s session_unblocked=%s max_age=%s previous_session=%s\\n' "
        '"${PAPER_READINESS_AUTO_RESUME:-}" '
        '"${PAPER_READINESS_REQUIRE_FLAT:-}" '
        '"${PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED:-}" '
        '"${PAPER_READINESS_MAX_PASS_AGE_MINUTES:-}" '
        '"${PAPER_READINESS_PREVIOUS_SESSION_DATE:-}"\n'
        "printf 'active_data=%s max_missing=%s\\n' "
        '"${PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE:-}" '
        '"${PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS:-}"\n'
        "printf 'decision_dry_run=%s min_records=%s require_accepted=%s strategy=%s\\n' "
        '"${PAPER_READINESS_REQUIRE_DECISION_DRY_RUN:-}" '
        '"${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-}" '
        '"${PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-}" '
        '"${PAPER_READINESS_DECISION_DRY_RUN_STRATEGY:-}"\n'
        "printf 'decision_dry_run_sample_times=%s\\n' "
        '"${PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES:-}"\n'
    )
    fake_readiness.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_readiness_if_needed.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_READINESS_CHECK_SCRIPT": str(fake_readiness),
            "PAPER_READINESS_AUTO_RESUME": "false",
            "PAPER_READINESS_REQUIRE_FLAT": "false",
            "PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED": "false",
            "PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE": "false",
            "PAPER_READINESS_REQUIRE_DECISION_DRY_RUN": "false",
            "PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS": "3",
            "PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS": "17",
            "PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED": "true",
            "PAPER_READINESS_DECISION_DRY_RUN_STRATEGY": "bull_flag",
            "PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES": "10:30,15:30",
            "PAPER_READINESS_MAX_PASS_AGE_MINUTES": "5",
            "PAPER_READINESS_PREVIOUS_SESSION_DATE": "2026-06-25",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "auto=false flat=false session_unblocked=false max_age=5 "
        "previous_session=2026-06-25"
    ) in result.stdout
    assert "active_data=false max_missing=3" in result.stdout
    assert (
        "decision_dry_run=false min_records=17 "
        "require_accepted=true strategy=bull_flag"
    ) in result.stdout
    assert "decision_dry_run_sample_times=10:30,15:30" in result.stdout


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


def test_paper_readiness_final_retry_reruns_after_old_pass(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
                "PAPER_READINESS_MAX_PASS_AGE_MINUTES=180",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'paper_readiness_latest_status=2026-06-29|passed|2026-06-28T04:12:49.000000Z|2026-06-28T04:11:57.000000Z|181\\n'\n"
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
        "proof_start=2026-06-29 reason=stale_by_age"
    ) in result.stdout
    assert "paper readiness prior pass is older than max age 180m" in result.stdout
    assert "fresh readiness ran" in result.stdout
    assert "paper readiness already passed for session 2026-06-29" not in result.stdout


def test_paper_readiness_force_refresh_reruns_after_recent_pass_with_age(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
                "PAPER_READINESS_MAX_PASS_AGE_MINUTES=180",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'paper_readiness_latest_status=2026-06-29|passed|2026-06-29T14:02:00.000000Z|2026-06-29T13:50:00.000000Z|173\\n'\n"
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
            "PAPER_READINESS_FORCE_REFRESH": "true",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 reason=force_refresh"
    ) in result.stdout
    assert "paper readiness force refresh requested" in result.stdout
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
        "printf 'paper_readiness_latest_decision_dry_run=paper decision dry run ok: strategy=bull_flag as_of=2026-06-26T11:30:00-04:00 active=980 decision_records=941 accepted=3 entry_intents=3 sample_times=10:30,11:30,12:30,13:30,14:30,15:30 evaluations=6 min_decision_records=929 max_accepted=3 max_entry_intents=3\\n'\n"
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
    assert "paper decision dry run ok: strategy=bull_flag" in result.stdout
    assert "decision_records=941" in result.stdout
    assert "evaluations=6" in result.stdout
    assert "paper readiness lock busy after prior pass for session 2026-07-06" in result.stdout
    assert "paper readiness check skipped" not in result.stdout


def test_paper_readiness_lock_skip_blocks_pass_without_dry_run(tmp_path: Path) -> None:
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

    assert result.returncode == 48
    assert (
        "scheduled check context: session_date=2026-07-06 "
        "proof_start=2026-06-29 reason=lock_busy_decision_dry_run_missing"
    ) in result.stdout
    assert "lacks accepted entry-intent decision dry-run proof (missing)" in result.stderr


def test_paper_readiness_lock_skip_blocks_pass_with_zero_entry_intents(
    tmp_path: Path,
) -> None:
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
        "printf 'paper_readiness_latest_decision_dry_run=paper decision dry run ok: strategy=bull_flag as_of=2026-06-26T11:30:00-04:00 active=980 decision_records=941 accepted=2 entry_intents=0 sample_times=10:30,11:30,12:30,13:30,14:30,15:30 evaluations=6 min_decision_records=929 max_accepted=2 max_entry_intents=0\\n'\n"
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
        "proof_start=2026-06-29 "
        "reason=lock_busy_decision_dry_run_entry_intents_under_minimum"
    ) in result.stdout
    assert "paper decision dry run ok: strategy=bull_flag" in result.stdout
    assert "max_entry_intents=0" in result.stdout
    assert (
        "lacks accepted entry-intent decision dry-run proof "
        "(entry_intents_under_minimum)"
    ) in result.stderr


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


def test_paper_readiness_lock_skip_blocks_old_pass(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "STRATEGY_VERSION=v1-breakout",
                "PAPER_READINESS_MAX_PASS_AGE_MINUTES=180",
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
        "printf 'paper_readiness_latest_status=passed|2026-06-28T04:12:49.000000Z|2026-06-28T04:11:57.000000Z|181\\n'\n"
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
    assert "paper readiness prior pass is older than max age 180m" in result.stderr


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
    readiness_if_needed = Path("scripts/paper_readiness_if_needed.sh").read_text()

    assert "flock -n -E 75" in wrapper
    assert '"$ROOT_DIR/scripts/run_check_with_audit.sh"' in wrapper
    assert '"$ROOT_DIR/scripts/scheduled_check_lock_skipped.sh"' in wrapper
    assert 'if [[ "$rc" -eq 75 ]]' in wrapper
    assert 'exit "$rc"' in wrapper
    assert "scheduled check lock busy" in lock_skip
    assert "scheduled check context:" in lock_skip
    assert "capture_env_overrides" in lock_skip
    assert "restore_env_overrides" in lock_skip
    assert lock_skip.index('source "$ENV_FILE"') < lock_skip.index("\n  restore_env_overrides\n")
    assert "reason=lock_busy" in lock_skip
    assert "reason=lock_busy_already_passed" in lock_skip
    assert "paper_readiness_session_date=" in lock_skip
    assert "paper_readiness_latest_status=" in lock_skip
    assert "load_latest_readiness_decision_dry_run" in lock_skip
    assert "paper_readiness_latest_decision_dry_run=" in lock_skip
    assert "paper decision dry run ok:" in lock_skip
    assert "decision_dry_run_reject_stages" in lock_skip
    assert "decision_dry_run_reject_reasons" in lock_skip
    assert "decision_dry_run_reject_stages" in readiness_if_needed
    assert "decision_dry_run_reject_reasons" in readiness_if_needed
    assert "proof_start = settings.profit_probe_start_date.isoformat()" in lock_skip
    assert "payload->>'proof_start' = %s" in lock_skip
    assert "paper_readiness)" in lock_skip
    assert "paper_activity)" in lock_skip
    assert "proof_start=${PROFIT_PROBE_START_DATE:-2026-06-30} strategy=${PAPER_ACTIVITY_STRATEGY" in lock_skip
    assert "session_guard)" in lock_skip
    assert "POST_CLOSE_LOCK_MAX_AGE_MINUTES" in lock_skip
    assert "load_latest_post_close_check_status" in lock_skip
    assert "guard_min_trades=\"${SESSION_GUARD_MIN_TRADES:-10}\"" in lock_skip
    assert "guard_min_pnl=\"${SESSION_GUARD_FAIL_BELOW_PNL:-0}\"" in lock_skip
    assert "reason=lock_busy_already_passed" in lock_skip
    assert "session guard passed: lock busy after recent pass" in lock_skip
    assert "reason=lock_busy_already_pending" in lock_skip
    assert "session guard pending: lock busy after recent pending result" in lock_skip
    assert "paper_profit_probe)" in lock_skip
    assert "reason=lock_busy_already_pending" in lock_skip
    assert "paper profit probe pending: lock busy after recent pending result" in lock_skip
    assert "paper_proof_status)" in lock_skip
    assert "load_latest_proof_status" in lock_skip
    assert "paper_proof_status_latest=" in lock_skip
    assert "PROOF_STATUS_LOCK_STRATEGY" in lock_skip
    assert "PROOF_STATUS_LOCK_MIN_TRADES" in lock_skip
    assert "PROOF_STATUS_LOCK_MIN_PNL" in lock_skip
    assert "payload->>'strategy' = %s" in lock_skip
    assert "payload->>'min_trades' = %s" in lock_skip
    assert "payload->>'min_pnl' = %s" in lock_skip
    assert "PROOF_STATUS_LOCK_MAX_AGE_MINUTES" in lock_skip
    assert "reason=lock_busy_already_reported" in lock_skip
    assert "paper proof summary:" in lock_skip
    assert "paper proof progress:" in lock_skip
    assert "paper proof scoring:" in lock_skip
    assert "paper proof status check skipped:" in lock_skip
    assert "proof_closed_trades" in lock_skip
    assert "proof_required_trades" in lock_skip
    assert "proof_first_exit_session" in lock_skip
    assert "proof_scoreable_closed_trades" in lock_skip
    assert "proof_unpaired_filled_exits" in lock_skip
    assert "proof_unpaired_symbols" in lock_skip
    assert "proof_scenario_status" in lock_skip
    assert "proof_scenario_active" in lock_skip
    assert "proof_scenario_expected_session" in lock_skip
    assert "proof_scenario_problems" in lock_skip
    assert "paper proof scenarios: status=$latest_scenario_status" in lock_skip
    assert '"$latest_status" == "pending" && "$latest_exit_code" == "43" && "$latest_proof" == "pending"' in lock_skip
    assert '"$latest_status" == "passed" && "$latest_exit_code" == "0" && "$latest_proof" == "passed"' in lock_skip
    assert "PROOF_STATUS_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-30}" in lock_skip
    assert "PROOF_STATUS_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}" in lock_skip
    assert "exit 48" in lock_skip
    assert "payload ? 'trading_mode'" in lock_skip
    assert "payload ? 'strategy_version'" in lock_skip
    assert "settings.trading_mode.value, settings.strategy_version" in lock_skip
    assert "PAPER_READINESS_MAX_PASS_AGE_MINUTES" in lock_skip
    assert "payload ? 'trading_mode'" in readiness_if_needed
    assert "payload ? 'strategy_version'" in readiness_if_needed
    assert "proof_start = settings.profit_probe_start_date.isoformat()" in readiness_if_needed
    assert "payload->>'proof_start' = %s" in readiness_if_needed
    assert "settings.trading_mode.value, settings.strategy_version" in readiness_if_needed
    assert "capture_env_overrides" in readiness_if_needed
    assert "restore_env_overrides" in readiness_if_needed
    assert readiness_if_needed.index('source "$ENV_FILE"') < readiness_if_needed.index("\nrestore_env_overrides\n")
    assert "PAPER_READINESS_MAX_PASS_AGE_MINUTES" in readiness_if_needed
    assert "PAPER_READINESS_FORCE_REFRESH" in readiness_if_needed
    assert "PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS" in readiness_if_needed
    assert "PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE" in readiness_if_needed
    assert "PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES" in readiness_if_needed
    assert "reason=force_refresh" in readiness_if_needed
    assert "reason=stale_by_age" in readiness_if_needed
    assert "PAPER_READINESS_FORCE_REFRESH" in readiness_if_needed
    assert "reason=force_refresh" in readiness_if_needed


def test_proof_status_lock_skip_uses_recent_proof_status_audit(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "echo 'paper_proof_status_latest=pending|43|pending|ready|none|awaiting_completed_proof_session|none|pending|0|10|0.00|0.01|none|none|ok|980|2026-06-26|none|0|0|none|2026-06-28T06:37:20.499132Z|0'\n"
    )
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("")
    lock_file = tmp_path / "proof-status.lock"

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "paper_proof_status",
            str(lock_file),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "reason=lock_busy_already_reported" in result.stdout
    assert (
        "paper proof summary: readiness=ready proof=pending "
        "reason=awaiting_completed_proof_session blockers=none warnings=none"
    ) in result.stdout
    assert (
        "paper proof progress: status=pending closed_trades=0 required_trades=10 "
        "pnl=0.00 required_pnl=0.01"
    ) in result.stdout
    assert (
        "paper proof scoring: scoreable_closed_trades=0 "
        "unpaired_filled_exits=0 unpaired_symbols=none"
    ) in result.stdout
    assert (
        "paper proof scenarios: status=ok active=980 "
        "expected_session=2026-06-26 problems=none"
    ) in result.stdout
    assert "paper proof status check skipped:" in result.stdout


def test_proof_status_lock_skip_accepts_recent_skipped_proof_status_audit(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "echo 'paper_proof_status_latest=skipped|0|pending|ready|none|awaiting_completed_proof_session|none|pending|0|10|0.00|0.01|none|none|ok|980|2026-06-26|none|0|0|none|2026-06-28T06:37:20.499132Z|0'\n"
    )
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("")
    lock_file = tmp_path / "proof-status.lock"

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "paper_proof_status",
            str(lock_file),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "reason=lock_busy_already_reported" in result.stdout
    assert "paper proof summary: readiness=ready proof=pending" in result.stdout
    assert "paper proof status check skipped:" in result.stdout


def test_proof_status_lock_skip_preserves_invocation_overrides(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "echo 'paper_proof_status_latest=pending|43|pending|ready|none|awaiting_completed_proof_session|none|pending|0|12|0.00|2.34|none|none|ok|980|2026-06-26|none|0|0|none|2026-06-28T06:37:20.499132Z|0'\n"
    )
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "PROFIT_PROBE_STRATEGY=bull_flag",
                "PROFIT_PROBE_MIN_TRADES=10",
                "PROFIT_PROBE_MIN_PNL=0.01",
            ]
        )
    )
    lock_file = tmp_path / "proof-status.lock"

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "paper_proof_status",
            str(lock_file),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PROOF_STATUS_START_DATE": "2026-07-06",
            "PROOF_STATUS_STRATEGY": "custom_flag",
            "PROOF_STATUS_MIN_TRADES": "12",
            "PROOF_STATUS_MIN_PNL": "2.34",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "scheduled check context: session_date="
        in result.stdout
    )
    assert "proof_start=2026-07-06" in result.stdout
    assert "strategy=custom_flag" in result.stdout
    assert "min_trades=12" in result.stdout
    assert "min_pnl=2.34" in result.stdout
    assert "reason=lock_busy_already_reported" in result.stdout


def test_proof_status_lock_skip_fails_without_recent_evidence(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "echo 'paper_proof_status_latest=pending|43|pending|ready|none|awaiting_completed_proof_session|none|pending|0|10|0.00|0.01|none|none|ok|980|2026-06-26|none|0|0|none|2026-06-28T06:37:20.499132Z|31'\n"
    )
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("")
    lock_file = tmp_path / "proof-status.lock"

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "paper_proof_status",
            str(lock_file),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PROOF_STATUS_LOCK_MAX_AGE_MINUTES": "30",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 48
    assert "reason=lock_busy" in result.stdout
    assert "scheduled check lock busy: check=paper_proof_status" in result.stderr


def test_session_guard_lock_skip_uses_recent_post_close_pass(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "echo 'post_close_check_latest=passed|0|2026-06-29T21:10:00.000000Z|0'\n"
    )
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "PROFIT_PROBE_STRATEGY=bull_flag",
            ]
        )
    )
    lock_file = tmp_path / "session-guard.lock"

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "session_guard",
            str(lock_file),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "SESSION_GUARD_START_DATE": "2026-07-06",
            "SESSION_GUARD_STRATEGY": "custom_flag",
            "SESSION_GUARD_MIN_TRADES": "12",
            "SESSION_GUARD_FAIL_BELOW_PNL": "1.23",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "proof_start=2026-07-06" in result.stdout
    assert "strategy=custom_flag" in result.stdout
    assert "min_trades=12" in result.stdout
    assert "min_pnl=1.23" in result.stdout
    assert "reason=lock_busy_already_passed" in result.stdout
    assert "session guard passed: lock busy after recent pass" in result.stdout


def test_session_guard_lock_skip_uses_recent_post_close_pending(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "echo 'post_close_check_latest=pending|43|2026-06-29T21:10:00.000000Z|0'\n"
    )
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "PROFIT_PROBE_STRATEGY=bull_flag",
            ]
        )
    )
    lock_file = tmp_path / "session-guard.lock"

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "session_guard",
            str(lock_file),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "SESSION_GUARD_START_DATE": "2026-07-06",
            "SESSION_GUARD_STRATEGY": "custom_flag",
            "SESSION_GUARD_MIN_TRADES": "12",
            "SESSION_GUARD_FAIL_BELOW_PNL": "1.23",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert "proof_start=2026-07-06" in result.stdout
    assert "strategy=custom_flag" in result.stdout
    assert "min_trades=12" in result.stdout
    assert "min_pnl=1.23" in result.stdout
    assert "reason=lock_busy_already_pending" in result.stdout
    assert "session guard pending: lock busy after recent pending result" in result.stdout


def test_profit_probe_lock_skip_uses_recent_post_close_pending(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "echo 'post_close_check_latest=pending|43|2026-06-29T21:20:00.000000Z|0'\n"
    )
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "PROFIT_PROBE_STRATEGY=bull_flag",
                "PROFIT_PROBE_MIN_TRADES=10",
                "PROFIT_PROBE_MIN_PNL=0.01",
            ]
        )
    )
    lock_file = tmp_path / "profit-probe.lock"

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "paper_profit_probe",
            str(lock_file),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PROFIT_PROBE_START_DATE": "2026-07-06",
            "PROFIT_PROBE_STRATEGY": "custom_flag",
            "PROFIT_PROBE_MIN_TRADES": "12",
            "PROFIT_PROBE_MIN_PNL": "2.34",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert "proof_start=2026-07-06" in result.stdout
    assert "strategy=custom_flag" in result.stdout
    assert "min_trades=12" in result.stdout
    assert "min_pnl=2.34" in result.stdout
    assert "reason=lock_busy_already_pending" in result.stdout
    assert "paper profit probe pending: lock busy after recent pending result" in result.stdout


def test_post_close_lock_skip_fails_without_recent_evidence(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "echo 'post_close_check_latest=passed|0|2026-06-29T21:10:00.000000Z|31'\n"
    )
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("")
    lock_file = tmp_path / "session-guard.lock"

    result = subprocess.run(
        [
            "scripts/scheduled_check_lock_skipped.sh",
            "session_guard",
            str(lock_file),
            str(env_file),
        ],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "POST_CLOSE_LOCK_MAX_AGE_MINUTES": "30",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 48
    assert "reason=lock_busy" in result.stdout
    assert "scheduled check lock busy: check=session_guard" in result.stderr


def test_locked_check_wrapper_preserves_wrapped_check_exit_code(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/usr/bin/env bash\nexit 0\n")
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("")
    lock_file = tmp_path / "scheduled-check.lock"

    result = subprocess.run(
        [
            "scripts/run_locked_check_with_audit.sh",
            "paper_proof_status",
            str(lock_file),
            str(env_file),
            "bash",
            "-c",
            "exit 43",
        ],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "RUN_CHECK_REQUIRE_AUDIT": "false",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43


def test_run_check_with_audit_marks_proof_status_lock_skip_as_skipped(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    audit_status_file = tmp_path / "audit-status.txt"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "printf '%s\\n' \"$AUDIT_STATUS\" > \"$AUDIT_STATUS_FILE\"\n"
        "exit 0\n"
    )
    docker.chmod(0o755)

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("")

    result = subprocess.run(
        [
            "scripts/run_check_with_audit.sh",
            "paper_proof_status",
            str(env_file),
            "bash",
            "-c",
            "echo 'paper proof status check skipped: lock busy after recent proof status pending'",
        ],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "AUDIT_STATUS_FILE": str(audit_status_file),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert audit_status_file.read_text().strip() == "skipped"


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
    assert 'AUDIT_PROOF_SUMMARY_LINE="$proof_summary_line"' in script
    assert 'AUDIT_PROOF_PROGRESS_LINE="$proof_progress_line"' in script
    assert 'AUDIT_PROOF_SCORING_LINE="$proof_scoring_line"' in script
    assert 'AUDIT_PROOF_SCENARIOS_LINE="$proof_scenarios_line"' in script
    assert 'AUDIT_DECISION_DRY_RUN_LINE="$decision_dry_run_line"' in script
    assert "-e AUDIT_CHECK_NAME" in script
    assert "-e AUDIT_STATUS" in script
    assert "-e AUDIT_EXIT_CODE" in script
    assert "-e AUDIT_OUTPUT_TAIL" in script
    assert "-e AUDIT_CONTEXT_LINE" in script
    assert "-e AUDIT_PROOF_SUMMARY_LINE" in script
    assert "-e AUDIT_PROOF_PROGRESS_LINE" in script
    assert "-e AUDIT_PROOF_SCORING_LINE" in script
    assert "-e AUDIT_PROOF_SCENARIOS_LINE" in script
    assert "-e AUDIT_DECISION_DRY_RUN_LINE" in script
    assert 'output_tail="$(tail -c 4000 "$output_file" 2>/dev/null || true)"' in script
    assert 'context_line="$(grep -E' in script
    assert 'proof_summary_line="$(grep -E' in script
    assert 'proof_progress_line="$(grep -E' in script
    assert 'proof_scoring_line="$(grep -E' in script
    assert 'proof_scenarios_line="$(grep -E' in script
    assert 'decision_dry_run_line="$(grep -E' in script
    assert "scheduled check context: " in script
    assert "CONTEXT_KEYS" in script
    assert "PROOF_SUMMARY_FIELDS" in script
    assert "PROOF_SCORING_FIELDS" in script
    assert "PROOF_SCENARIOS_FIELDS" in script
    assert 'PROOF_VALUE = re.compile(r"^[A-Za-z0-9_.:,+/;@-]+$")' in script
    assert '"readiness": "proof_readiness"' in script
    assert '"proof": "proof_status"' in script
    assert '"closed_trades": "proof_closed_trades"' in script
    assert '"pnl": "proof_pnl"' in script
    assert '"scoreable_closed_trades": "proof_scoreable_closed_trades"' in script
    assert '"unpaired_filled_exits": "proof_unpaired_filled_exits"' in script
    assert '"unpaired_symbols": "proof_unpaired_symbols"' in script
    assert '"status": "proof_scenario_status"' in script
    assert '"active": "proof_scenario_active"' in script
    assert '"expected_session": "proof_scenario_expected_session"' in script
    assert '"problems": "proof_scenario_problems"' in script
    assert "DECISION_DRY_RUN_FIELDS" in script
    assert '"decision_records": "decision_dry_run_records"' in script
    assert '"accepted": "decision_dry_run_accepted"' in script
    assert '"entry_intents": "decision_dry_run_entry_intents"' in script
    assert '"sample": "decision_dry_run_sample"' in script
    assert '"sample_times": "decision_dry_run_sample_times"' in script
    assert '"evaluations": "decision_dry_run_evaluations"' in script
    assert '"min_decision_records": "decision_dry_run_min_decision_records"' in script
    assert '"max_accepted": "decision_dry_run_max_accepted"' in script
    assert '"max_entry_intents": "decision_dry_run_max_entry_intents"' in script
    assert '"reject_stages": "decision_dry_run_reject_stages"' in script
    assert '"reject_reasons": "decision_dry_run_reject_reasons"' in script
    assert "parse_prefixed_fields" in script
    assert '"session_date"' in script
    assert '"previous_session_date"' in script
    assert '"proof_start"' in script
    assert '"reason"' in script
    assert "payload.update(parse_context" in script
    assert 'os.environ.get("AUDIT_PROOF_SUMMARY_LINE", "")' in script
    assert 'os.environ.get("AUDIT_PROOF_PROGRESS_LINE", "")' in script
    assert 'os.environ.get("AUDIT_PROOF_SCORING_LINE", "")' in script
    assert 'os.environ.get("AUDIT_PROOF_SCENARIOS_LINE", "")' in script
    assert 'os.environ.get("AUDIT_DECISION_DRY_RUN_LINE", "")' in script
    assert 'paper readiness check skipped' in script
    assert 'paper activity check skipped' in script
    assert 'paper activity skipped:' in script
    assert 'paper proof status check skipped:' in script
    assert 'status="skipped"' in script
    assert '43)' in script
    assert 'status="pending"' in script
    assert '"$@" > "$output_file" 2>&1' in script
    assert 'cat "$output_file"' in script
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

    assert "capture_env_overrides" in script
    assert "restore_env_overrides" in script
    assert script.index('source "$ENV_FILE"') < script.index("\nrestore_env_overrides\n")
    assert "PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED \\" in script
    assert "PAPER_READINESS_PREVIOUS_SESSION_DATE \\" in script
    assert 'PAPER_READINESS_AUTO_RESUME="${PAPER_READINESS_AUTO_RESUME:-true}"' in script
    assert 'PAPER_READINESS_AUTO_RESET_WEIGHTS="${PAPER_READINESS_AUTO_RESET_WEIGHTS:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_FLAT="${PAPER_READINESS_REQUIRE_FLAT:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED="${PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR="${PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_MARKET_DATA="${PAPER_READINESS_REQUIRE_MARKET_DATA:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE="${PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_DECISION_DRY_RUN="${PAPER_READINESS_REQUIRE_DECISION_DRY_RUN:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_SCENARIOS="${PAPER_READINESS_REQUIRE_SCENARIOS:-true}"' in script
    assert 'PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS="${PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS:-true}"' in script
    assert 'PAPER_READINESS_CLOSE_ONLY_ON_FAILURE="${PAPER_READINESS_CLOSE_ONLY_ON_FAILURE:-true}"' in script
    assert "PAPER_READINESS_PRIOR_PROOF_START_DATE \\" in script
    assert 'PAPER_READINESS_PRIOR_PROOF_START_DATE="${PAPER_READINESS_PRIOR_PROOF_START_DATE:-}"' in script
    assert 'PAPER_READINESS_PRIOR_PROOF_START_DATE="${PAPER_READINESS_PRIOR_PROOF_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-30}}"' in script
    assert "PAPER_READINESS_LOSING_STREAK_N \\" in script
    assert 'PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-}"' in script
    assert 'PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-${LOSING_STREAK_N:-3}}"' in script
    assert 'PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}"' in script
    assert 'PAPER_READINESS_MIN_CONFIDENCE_FLOOR="${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.25}"' in script
    assert 'PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS="${PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS:-0}"' in script
    assert 'PAPER_READINESS_DATA_SMOKE_SYMBOLS="${PAPER_READINESS_DATA_SMOKE_SYMBOLS:-SPY,AAPL}"' in script
    assert 'PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS="${PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS:-10}"' in script
    assert 'PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS="${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-900}"' in script
    assert 'PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED="${PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-true}"' in script
    assert 'PAPER_READINESS_DECISION_DRY_RUN_STRATEGY="${PAPER_READINESS_DECISION_DRY_RUN_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"' in script
    assert 'PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES="${PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES:-10:30,11:30,12:30,13:30,14:30,15:30}"' in script
    assert 'PAPER_READINESS_SCENARIO_DIR="${PAPER_READINESS_SCENARIO_DIR:-/var/lib/alpaca-bot/nightly/scenarios}"' in script
    assert "PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE must be true or false" in script
    assert "PAPER_READINESS_REQUIRE_DECISION_DRY_RUN must be true or false" in script
    assert "PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED must be true or false" in script
    assert "PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS must be a non-negative integer" in script
    assert "PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS must be a positive integer" in script
    assert "PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" in script
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
    assert "is_after_configured_flatten_time" in script
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
    assert "paper readiness session entry block check skipped after flatten" in script
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
    assert "PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE" in script
    assert "run_active_data_coverage_check" in script
    assert "paper readiness active data coverage ok" in script
    assert "paper readiness active data coverage check skipped" in script
    assert "active watchlist market data coverage below threshold" in script
    assert "thin_intraday_lt20" in script
    assert "run_decision_dry_run_check" in script
    assert "./scripts/paper_decision_dry_run.sh" in script
    assert 'PAPER_DECISION_DRY_RUN_STRATEGY="$PAPER_READINESS_DECISION_DRY_RUN_STRATEGY"' in script
    assert 'PAPER_DECISION_DRY_RUN_MIN_RECORDS="$PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS"' in script
    assert 'PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED="$PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED"' in script
    assert 'PAPER_DECISION_DRY_RUN_SAMPLE_TIMES="$PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES"' in script
    assert "paper readiness decision dry run check skipped" in script
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
    assert "require_env_value_or_unset BULL_FLAG_MIN_RUN_PCT 0.02" in script
    assert "require_env_value_or_unset BULL_FLAG_CONSOLIDATION_VOLUME_RATIO 0.6" in script
    assert "require_env_value_or_unset BULL_FLAG_CONSOLIDATION_RANGE_PCT 0.5" in script

    assert 'check("market_data_feed", settings.market_data_feed.value, "iex")' in script
    assert 'check("trailing_stop_atr_multiplier", settings.trailing_stop_atr_multiplier, 1.0)' in script
    assert 'check("bull_flag_min_run_pct", settings.bull_flag_min_run_pct, 0.02)' in script
    assert 'check("stop_limit_buffer_pct", settings.stop_limit_buffer_pct, 0.0005)' in script
    assert 'check("entry_stop_price_buffer", settings.entry_stop_price_buffer, 0.02)' in script
    assert (
        'check("bull_flag_consolidation_volume_ratio", '
        'settings.bull_flag_consolidation_volume_ratio, 0.6)'
    ) in script
    assert (
        'check("bull_flag_consolidation_range_pct", '
        'settings.bull_flag_consolidation_range_pct, 0.5)'
    ) in script
    assert 'check("enable_profit_trail", settings.enable_profit_trail, True)' in script
    assert 'check("paper_proof_freeze", settings.paper_proof_freeze, True)' in script
    assert 'check("enable_vwap_entry_filter", settings.enable_vwap_entry_filter, False)' in script
    assert 'check("enable_news_filter", settings.enable_news_filter, False)' in script
    assert "require_env_value MAX_LOSS_PER_TRADE_DOLLARS 20.0" in script
    assert 'check("max_loss_per_trade_dollars", settings.max_loss_per_trade_dollars, 20.0)' in script
    assert script.index("run_container_settings_posture_check") < script.index("run_market_data_smoke_check")
    assert "AlpacaMarketDataAdapter.from_settings" in script
    assert "adapter.get_daily_bars" in script
    assert "adapter.get_stock_bars" in script
    assert "paper readiness failed: market data daily-bars smoke failed" in script
    assert "paper readiness failed: market data daily-bars smoke returned no bars" in script
    assert "paper readiness failed: market data intraday-bars smoke failed" in script
    assert "paper readiness failed: market data intraday-bars smoke returned no bars" in script
    assert "timeframe_minutes={settings.entry_timeframe_minutes}" in script
    assert "paper readiness market data ok" in script
    assert "paper readiness market data check skipped" in script
    assert "active option orders" in script
    assert "paper readiness option positions ok: net_open=0 active_orders=0" in script
    assert "stock-only proof has $open_option_positions net-open option positions" in script
    assert "paper readiness refusing auto-resume after failed proof guard" in script
    assert "paper proof failed" in script
    assert "session guard failed" in script
    assert "same_session_profit_lock" in script
    assert "reason=paper profit lock" in script
    assert "status_session_date" in script
    assert 'current_session_date="$(TZ=America/New_York date +%F)"' in script
    assert 'readiness_session_date="$PAPER_READINESS_SESSION_DATE"' in script
    assert '"$status_session_date" == "$current_session_date"' in script
    assert "paper readiness preserving same-session paper profit lock" in script
    assert "paper readiness ops check accepting same-session paper profit lock" in script
    assert "same-session paper profit lock has $active_orders active stock orders" in script
    assert "paper readiness session entry block check accepted for same-session paper profit lock" in script
    assert "ops_expected_trading_status=\"enabled\"" in script
    assert "ops_expected_trading_status=\"close_only\"" in script
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
    assert "check_name = 'session_guard'" in script
    assert "status = 'passed'" in script
    assert "check_name = 'paper_profit_probe'" in script
    assert "OR (status = 'pending' AND exit_code = '43')" in script
    assert "check_name = 'paper_profit_probe' AND status IN ('passed', 'pending')" not in script
    assert "session $PAPER_READINESS_SESSION_DATE has entry-blocking state" in script
    assert "paper readiness session entry blocks ok: session=$PAPER_READINESS_SESSION_DATE blocked=0" in script
    assert "paper readiness session entry block check accepted for same-session paper profit lock" in script
    assert "paper readiness session entry block check skipped after flatten" in script
    assert "PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED" in script
    assert "IN ('_global', '_equity')" in script
    assert "LOSING_STREAK_N must be a positive integer" in script
    assert "paper readiness failed: active strategies at losing-streak gate" in script
    assert "paper readiness losing streak gate ok: session=$PAPER_READINESS_SESSION_DATE blocked=0" in script
    assert "PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR" in script
    assert "non_loss_days_newer" in script
    assert "losing_streak >= (:'losing_streak_n')::int" in script
    assert "pre-open paper readiness auto-resume" in script
    assert '--expect-trading-status "$ops_expected_trading_status"' in script
    assert "--expect-only-enabled-strategy bull_flag" in script
    assert "require_env_value MARKET_DATA_FEED iex" in script
    assert "require_env_value DAILY_SMA_PERIOD 20" in script
    assert "require_env_value BREAKOUT_LOOKBACK_BARS 20" in script
    assert "require_env_value RELATIVE_VOLUME_LOOKBACK_BARS 20" in script
    assert "require_env_value RELATIVE_VOLUME_THRESHOLD 2.0" in script
    assert "require_env_value ENTRY_TIMEFRAME_MINUTES 15" in script
    assert "require_env_value MAX_OPEN_POSITIONS 4" in script
    assert "require_env_value REPLAY_SLIPPAGE_BPS 2.0" in script
    assert "require_env_value RISK_PER_TRADE_PCT 0.01" in script
    assert "require_env_value STOP_LIMIT_BUFFER_PCT 0.0005" in script
    assert "require_env_value ENTRY_STOP_PRICE_BUFFER 0.02" in script
    assert "require_env_value_or_unset ATR_PERIOD 20" in script
    assert "require_env_value_or_unset ATR_STOP_MULTIPLIER 1.0" in script
    assert "require_env_value TRAILING_STOP_ATR_MULTIPLIER 1.0" in script
    assert "require_env_value_or_unset TRAILING_STOP_PROFIT_TRIGGER_R 1.0" in script
    assert "require_env_value INTRADAY_CONSECUTIVE_LOSS_GATE 0" in script
    assert "require_env_value ENTRY_WINDOW_START 10:00" in script
    assert "require_env_value ENTRY_WINDOW_END 15:30" in script
    assert "require_env_value FLATTEN_TIME 15:45" in script
    assert "require_env_true PAPER_PROOF_FREEZE" in script
    assert "require_env_false_or_unset ENABLE_VWAP_ENTRY_FILTER" in script
    assert "require_env_true ENABLE_PROFIT_TRAIL" in script
    assert "require_env_value PROFIT_TRAIL_PCT 0.90" in script
    assert "require_env_true ENABLE_PROFIT_TARGET" in script
    assert "require_env_value PROFIT_TARGET_R 3.0" in script
    assert "require_env_true_or_unset ENABLE_BREAKEVEN_STOP" in script
    assert "require_env_value BREAKEVEN_TRIGGER_PCT 0.005" in script
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


def test_paper_readiness_auto_resumes_stale_profit_lock(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "MARKET_DATA_FEED=iex",
                "DAILY_SMA_PERIOD=20",
                "BREAKOUT_LOOKBACK_BARS=20",
                "RELATIVE_VOLUME_LOOKBACK_BARS=20",
                "RELATIVE_VOLUME_THRESHOLD=2.0",
                "ENTRY_TIMEFRAME_MINUTES=15",
                "MAX_OPEN_POSITIONS=4",
                "REPLAY_SLIPPAGE_BPS=2.0",
                "RISK_PER_TRADE_PCT=0.01",
                "MAX_POSITION_PCT=0.05",
                "MAX_LOSS_PER_TRADE_DOLLARS=20.0",
                "MAX_PORTFOLIO_EXPOSURE_PCT=0.30",
                "DAILY_LOSS_LIMIT_PCT=0.01",
                "STOP_LIMIT_BUFFER_PCT=0.0005",
                "ENTRY_STOP_PRICE_BUFFER=0.02",
                "TRAILING_STOP_ATR_MULTIPLIER=1.0",
                "INTRADAY_CONSECUTIVE_LOSS_GATE=0",
                "ENTRY_WINDOW_START=10:00",
                "ENTRY_WINDOW_END=15:30",
                "FLATTEN_TIME=15:45",
                "PAPER_PROOF_FREEZE=true",
                "ENABLE_PROFIT_TRAIL=true",
                "PROFIT_TRAIL_PCT=0.90",
                "ENABLE_PROFIT_TARGET=true",
                "PROFIT_TARGET_R=3.0",
                "BREAKEVEN_TRIGGER_PCT=0.005",
                "POSTGRES_USER=postgres",
                "POSTGRES_DB=postgres",
            ]
        )
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *'2026-06-30T16:39:25+00:00'* ]]; then\n"
        "  printf '2026-06-30\\n'\n"
        "elif [[ \"$*\" == *'+%F'* ]]; then\n"
        "  printf '2026-07-01\\n'\n"
        "elif [[ \"$*\" == *'+%u'* ]]; then\n"
        "  printf '3\\n'\n"
        "elif [[ \"$*\" == *'+%H:%M'* ]]; then\n"
        "  printf '09:15\\n'\n"
        "else\n"
        "  /usr/bin/date \"$@\"\n"
        "fi\n"
    )
    fake_date.chmod(0o755)

    resume_marker = tmp_path / "resume_called"
    close_only_marker = tmp_path / "close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "args=\"$*\"\n"
        "input=\"$(cat || true)\"\n"
        "if [[ \"$args\" == *' admin close-only'* ]]; then\n"
        f"  touch {close_only_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if [[ \"$args\" == *' admin status'* ]]; then\n"
        "  printf 'mode=paper strategy=v1-breakout status=close_only kill_switch=false reason=paper profit lock: stop-out projection negative updated_at=2026-06-30T16:39:25+00:00\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$args\" == *' admin resume'* ]]; then\n"
        f"  touch {resume_marker}\n"
        "  printf 'mode=paper strategy=v1-breakout status=ENABLED reason=pre-open paper readiness auto-resume\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$args\" == *'--entrypoint alpaca-bot-ops-check admin'* ]]; then\n"
        "  printf 'status=ok db=ok trading_mode=paper strategy_version=v1-breakout trading_status=enabled kill_switch_enabled=False enabled_strategies=bull_flag worker_status=fresh\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$args\" == *' exec -T postgres psql'* ]]; then\n"
        "  if [[ \"$input\" == *'COUNT(*) FILTER'* && \"$input\" == *'FROM symbol_watchlist'* ]]; then\n"
        "    printf '900|900|0\\n'\n"
        "  elif [[ \"$input\" == *'FROM strategy_flags'* && \"$input\" == *'FROM strategy_weights'* ]]; then\n"
        "    printf 'ok|bull_flag|bull_flag|1.000000|0\\n'\n"
        "  elif [[ \"$input\" == *'confidence_floor_store'* ]]; then\n"
        "    printf 'ok|0.250000\\n'\n"
        "  elif [[ \"$input\" == *'FROM positions'* && \"$input\" == *'FROM orders'* ]]; then\n"
        "    printf '0|0\\n'\n"
        "  elif [[ \"$input\" == *'FROM option_orders'* ]]; then\n"
        "    printf '0\\n'\n"
        "  else\n"
        "    printf 'unexpected psql call\\n%s\\n' \"$input\" >&2\n"
        "    exit 98\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$args\" == *'--entrypoint python admin'* ]]; then\n"
        "  if [[ \"$input\" == *'ConfidenceFloorStore'* ]]; then\n"
        "    printf 'ok|100000.00|100000.00|0.000000|0.050000|ok|200000.00|5000.00|false\\n'\n"
        "  elif [[ \"$input\" == *'list_open_orders'* ]]; then\n"
        "    printf 'paper readiness broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  else\n"
        "    printf 'paper readiness container Settings ok\\n'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "printf 'unexpected docker call: %s\\n%s\\n' \"$args\" \"$input\" >&2\n"
        "exit 98\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_readiness_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_READINESS_SESSION_DATE": "2026-07-01",
            "PAPER_READINESS_PREVIOUS_SESSION_DATE": "2026-06-30",
            "PAPER_READINESS_REQUIRE_MARKET_DATA": "false",
            "PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE": "false",
            "PAPER_READINESS_REQUIRE_WATCHLIST_ASSETS": "false",
            "PAPER_READINESS_REQUIRE_DECISION_DRY_RUN": "false",
            "PAPER_READINESS_REQUIRE_SCENARIOS": "false",
            "PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS": "false",
            "PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED": "false",
            "PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR": "false",
            "PAPER_READINESS_REQUIRE_FLAT": "false",
            "PAPER_READINESS_CLOSE_ONLY_ON_FAILURE": "false",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "paper readiness auto-resuming stale close_only state" in result.stdout
    assert "paper readiness preserving same-session paper profit lock" not in result.stdout
    assert resume_marker.exists()
    assert not close_only_marker.exists()


def test_paper_readiness_preserves_profit_lock_on_current_wall_date(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "MARKET_DATA_FEED=iex",
                "DAILY_SMA_PERIOD=20",
                "BREAKOUT_LOOKBACK_BARS=20",
                "RELATIVE_VOLUME_LOOKBACK_BARS=20",
                "RELATIVE_VOLUME_THRESHOLD=2.0",
                "ENTRY_TIMEFRAME_MINUTES=15",
                "MAX_OPEN_POSITIONS=4",
                "REPLAY_SLIPPAGE_BPS=2.0",
                "RISK_PER_TRADE_PCT=0.01",
                "MAX_POSITION_PCT=0.05",
                "MAX_LOSS_PER_TRADE_DOLLARS=20.0",
                "MAX_PORTFOLIO_EXPOSURE_PCT=0.30",
                "DAILY_LOSS_LIMIT_PCT=0.01",
                "STOP_LIMIT_BUFFER_PCT=0.0005",
                "ENTRY_STOP_PRICE_BUFFER=0.02",
                "TRAILING_STOP_ATR_MULTIPLIER=1.0",
                "INTRADAY_CONSECUTIVE_LOSS_GATE=0",
                "ENTRY_WINDOW_START=10:00",
                "ENTRY_WINDOW_END=15:30",
                "FLATTEN_TIME=15:45",
                "PAPER_PROOF_FREEZE=true",
                "ENABLE_PROFIT_TRAIL=true",
                "PROFIT_TRAIL_PCT=0.90",
                "ENABLE_PROFIT_TARGET=true",
                "PROFIT_TARGET_R=3.0",
                "BREAKEVEN_TRIGGER_PCT=0.005",
                "POSTGRES_USER=postgres",
                "POSTGRES_DB=postgres",
            ]
        )
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *'2026-06-30T16:39:25+00:00'* ]]; then\n"
        "  printf '2026-06-30\\n'\n"
        "elif [[ \"$*\" == *'+%F'* ]]; then\n"
        "  printf '2026-06-30\\n'\n"
        "elif [[ \"$*\" == *'+%u'* ]]; then\n"
        "  printf '2\\n'\n"
        "elif [[ \"$*\" == *'+%H:%M'* ]]; then\n"
        "  printf '13:15\\n'\n"
        "else\n"
        "  /usr/bin/date \"$@\"\n"
        "fi\n"
    )
    fake_date.chmod(0o755)

    resume_marker = tmp_path / "resume_called"
    close_only_marker = tmp_path / "close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "args=\"$*\"\n"
        "input=\"$(cat || true)\"\n"
        "if [[ \"$args\" == *' admin close-only'* ]]; then\n"
        f"  touch {close_only_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if [[ \"$args\" == *' admin status'* ]]; then\n"
        "  printf 'mode=paper strategy=v1-breakout status=close_only kill_switch=false reason=paper profit lock: stop-out projection negative updated_at=2026-06-30T16:39:25+00:00\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$args\" == *' admin resume'* ]]; then\n"
        f"  touch {resume_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if [[ \"$args\" == *'--entrypoint alpaca-bot-ops-check admin'* ]]; then\n"
        "  printf 'status=ok db=ok trading_mode=paper strategy_version=v1-breakout trading_status=close_only kill_switch_enabled=False enabled_strategies=bull_flag worker_status=fresh\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$args\" == *' exec -T postgres psql'* ]]; then\n"
        "  if [[ \"$input\" == *'COUNT(*) FILTER'* && \"$input\" == *'FROM symbol_watchlist'* ]]; then\n"
        "    printf '900|900|0\\n'\n"
        "  elif [[ \"$input\" == *'FROM strategy_flags'* && \"$input\" == *'FROM strategy_weights'* ]]; then\n"
        "    printf 'ok|bull_flag|bull_flag|1.000000|0\\n'\n"
        "  elif [[ \"$input\" == *'confidence_floor_store'* ]]; then\n"
        "    printf 'ok|0.250000\\n'\n"
        "  elif [[ \"$input\" == *'FROM positions'* && \"$input\" == *'FROM orders'* ]]; then\n"
        "    printf '0|0\\n'\n"
        "  elif [[ \"$input\" == *'FROM option_orders'* ]]; then\n"
        "    printf '0\\n'\n"
        "  elif [[ \"$input\" == *'entries_disabled = TRUE'* ]]; then\n"
        "    printf '1|_global\\n'\n"
        "  else\n"
        "    printf 'unexpected psql call\\n%s\\n' \"$input\" >&2\n"
        "    exit 98\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$args\" == *'--entrypoint python admin'* ]]; then\n"
        "  if [[ \"$input\" == *'ConfidenceFloorStore'* ]]; then\n"
        "    printf 'ok|100000.00|100000.00|0.000000|0.050000|ok|200000.00|5000.00|false\\n'\n"
        "  elif [[ \"$input\" == *'list_open_orders'* ]]; then\n"
        "    printf 'paper readiness profit lock broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  else\n"
        "    printf 'paper readiness container Settings ok\\n'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "printf 'unexpected docker call: %s\\n%s\\n' \"$args\" \"$input\" >&2\n"
        "exit 98\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_readiness_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_READINESS_SESSION_DATE": "2026-07-01",
            "PAPER_READINESS_PREVIOUS_SESSION_DATE": "2026-06-30",
            "PAPER_READINESS_REQUIRE_MARKET_DATA": "false",
            "PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE": "false",
            "PAPER_READINESS_REQUIRE_WATCHLIST_ASSETS": "false",
            "PAPER_READINESS_REQUIRE_DECISION_DRY_RUN": "false",
            "PAPER_READINESS_REQUIRE_SCENARIOS": "false",
            "PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS": "false",
            "PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR": "false",
            "PAPER_READINESS_REQUIRE_FLAT": "false",
            "PAPER_READINESS_CLOSE_ONLY_ON_FAILURE": "false",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "paper readiness preserving same-session paper profit lock" in result.stdout
    assert "paper readiness auto-resuming stale close_only state" not in result.stdout
    assert "paper readiness ops check accepting same-session paper profit lock" in result.stdout
    assert not resume_marker.exists()
    assert not close_only_marker.exists()


def test_paper_activity_check_verifies_mid_session_evaluation() -> None:
    script = Path("scripts/paper_activity_check.sh").read_text()

    assert "capture_env_overrides" in script
    assert "restore_env_overrides" in script
    assert script.index('source "$ENV_FILE"') < script.index("\nrestore_env_overrides\n")
    assert "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE \\" in script
    assert "PAPER_ACTIVITY_WINDOW_MINUTES" in script
    assert 'PAPER_ACTIVITY_MIN_DECISION_RECORDS="${PAPER_ACTIVITY_MIN_DECISION_RECORDS:-900}"' in script
    assert 'PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES="${PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES:-5}"' in script
    assert 'PAPER_ACTIVITY_REQUIRE_DECISION_LOG="${PAPER_ACTIVITY_REQUIRE_DECISION_LOG:-true}"' in script
    assert 'PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT="${PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT:-true}"' in script
    assert 'PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE="${PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE:-true}"' in script
    assert 'PAPER_ACTIVITY_READINESS_RUNNER="${PAPER_ACTIVITY_READINESS_RUNNER:-./scripts/run_locked_check_with_audit.sh}"' in script
    assert 'PAPER_ACTIVITY_READINESS_SCRIPT="${PAPER_ACTIVITY_READINESS_SCRIPT:-./scripts/paper_readiness_if_needed.sh}"' in script
    assert "PAPER_ACTIVITY_REQUIRE_DECISION_LOG must be true or false" in script
    assert "PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES must be a positive integer" in script
    assert "PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT must be true or false" in script
    assert "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE must be true or false" in script
    assert 'PAPER_ACTIVITY_STRATEGY="${PAPER_ACTIVITY_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"' in script
    assert "close_only_on_activity_failure" in script
    assert "trap close_only_on_activity_failure EXIT" in script
    assert "paper activity failed for session" in script
    assert "post-open checks failed for strategy" in script
    assert "paper activity warning: failed to apply close-only after activity failure" in script
    assert "PAPER_READINESS_AUTO_RESUME=false" in script
    assert "PAPER_READINESS_AUTO_RESET_WEIGHTS=false" in script
    assert 'PAPER_READINESS_CLOSE_ONLY_ON_FAILURE="$PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE"' in script
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
        "proof_start=${PROFIT_PROBE_START_DATE:-2026-06-30} strategy=$PAPER_ACTIVITY_STRATEGY"
    ) in script
    assert "decision_record_count" in script
    assert "decision_log" in script
    assert "latest_supervisor AS" in script
    assert "latest_supervisor_activity AS" in script
    assert "latest_supervisor_started AS" in script
    assert "SELECT MAX(created_at) AS created_at" in script
    assert "(SELECT created_at FROM latest_supervisor_started)" in script
    assert "latest_cycle_entries_disabled" in script
    assert "latest_cycle_strategy_blocked" in script
    assert "latest_activity_market_closed" in script
    assert "strategy_decision_log_cycles" in script
    assert "strategy_decision_log_records" in script
    assert "strategy_decision_log_summary" in script
    assert "decision_log_summary" in script
    assert "reject_stage" in script
    assert "reject_reason" in script
    assert "strategy_accepted_decisions" in script
    assert "latest_accepted_decision_log" in script
    assert "recent_entry_orders" in script
    assert "recent_entry_order_status_summary" in script
    assert (
        "created_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')\n"
        "      OR updated_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')"
    ) in script
    assert "accepted_symbols" in script
    assert "materialized_entry_symbols" in script
    assert "unmaterialized_accepted_symbols" in script
    assert "stale_pending_entry_orders" in script
    assert "stale_pending_entry_order_summary" in script
    assert "broker_order_id IS NULL" in script
    assert "accepted_decisions=${strategy_accepted_decisions:-0}" in script
    assert "unmaterialized_accepted_symbol_count" in script
    assert "unmaterialized_accepted_symbols=[" in script
    assert "entry_order_status_summary" in script
    assert "stale pending entry orders" in script
    assert "PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES" in script
    assert "strategy_evidence_records" in script
    assert "order_dispatch_failed" in script
    assert "order_dispatch_stop_price_rejected" in script
    assert "dispatch_failures" in script
    assert "paper activity failed: order dispatch failure events" in script
    assert "stream_heartbeat_stale" not in script
    assert "stream_issue.event_type = 'stream_heartbeat_stale'" not in script
    assert "stream_restart_failed" in script
    assert "trade_update_stream_failed" in script
    assert "trade_update_failed" in script
    assert "protective_stop_quantity_replace_failed" in script
    assert "stream_issues" in script
    assert "paper activity failed: trade update stream issues" in script
    assert "stock_open_positions" in script
    assert "active_stock_orders" in script
    assert script.count("strategy_name IS NOT DISTINCT FROM :'paper_activity_strategy'") >= 2
    assert "has_stock_exposure" in script
    assert "decision_evidence_records" in script
    assert "payload->>'strategy_name' = :'paper_activity_strategy'" in script
    assert (
        "AND (NOT (payload ? 'strategy_name') OR payload->>'strategy_name' = :'paper_activity_strategy')"
        in script
    )
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
    assert "only_strategy_session_state_reasons" in script
    assert "is_after_configured_flatten_time" in script
    assert "post_flatten_strategy_blocked" in script
    assert "profit_lock_flat_pause_active" in script
    assert "only_profit_lock_pause_reasons" in script
    assert "paper_profit_lock_pause=$paper_profit_lock_pause" in script
    assert "BROKER_FLAT_CONTEXT=\"paper activity profit lock\"" in script
    assert "PAPER_ACTIVITY_STRATEGY contains unsupported characters" in script
    assert "emit_scheduled_context()" in script
    assert (
        'echo "scheduled check context: session_date=$(TZ=America/New_York date +%F) '
        'proof_start=${PROFIT_PROBE_START_DATE:-2026-06-30} strategy=$PAPER_ACTIVITY_STRATEGY"'
    ) in script
    assert "emit_scheduled_context\n\n  if [[ \"${PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE,,}\"" in script
    assert "emit_scheduled_context\n\nload_market_clock_status" in script
    assert "load_market_clock_status" in script
    assert "load_broker_activity_status" in script
    assert "AlpacaExecutionAdapter.from_settings" in script
    assert "get_market_clock" in script
    assert "broker.get_account()" in script
    assert "broker.list_open_orders()" in script
    assert "broker.list_positions()" in script
    assert "broker account not tradable" in script
    assert "broker_account_status=${broker_account_status:-unset}" in script
    assert "require_broker_account=${PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT,,}" in script
    assert "supervisor reported market_closed but Alpaca clock is" in script
    assert "latest supervisor activity is market_closed" in script
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
        "if printf '%s\\n' \"$*\" | grep -q -- '--entrypoint python admin'; then\n"
        "  printf 'ok|100000.00|200000.00|5000.00|false|1|3|DASH|DASH\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '10|0|10|10|0|2026-06-29 16:00:00+00|false||false||2026-06-29 16:00:00+00|0|10|10|0|10|10|2026-06-29 16:00:00+00|accepted/none/none:1,skipped_no_signal/none/none:9|1|2026-06-29 16:00:00+00|0||1|DASH|1|DASH|0||0||bull_flag|||3|0|0|0\\n'\n"
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
    assert (
        "bull_flag_decision_log_summary=[accepted/none/none:1,"
        "skipped_no_signal/none/none:9]"
    ) in result.stdout
    assert "stock_open_positions=3" in result.stdout
    assert "active_stock_orders=0" in result.stdout
    assert "bull_flag_accepted_symbols=[DASH]" in result.stdout
    assert "bull_flag_materialized_entry_symbols=[DASH]" in result.stdout
    assert "bull_flag_unmaterialized_accepted_symbols=[]" in result.stdout
    assert "require_broker_account=true" in result.stdout
    assert "broker_account_status=ok" in result.stdout
    assert "broker_open_orders=1" in result.stdout
    assert "broker_open_positions=3" in result.stdout
    assert "broker_open_order_symbols=DASH" in result.stdout
    assert "broker_open_position_symbols=DASH" in result.stdout
    assert "dispatch_failures=0" in result.stdout
    assert "stream_issues=0" in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_fails_when_accepted_decisions_do_not_materialize_orders(
    tmp_path: Path,
) -> None:
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
        "printf '10|0|10|10|0|2026-06-29 16:00:00+00|false||false||2026-06-29 16:00:00+00|0|10|10|0|10|10|2026-06-29 16:00:00+00|accepted/none/none:1,skipped_no_signal/none/none:9|1|2026-06-29 16:00:00+00|0||1|DASH|0||1|DASH|0||bull_flag|||0|0|0|0\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE": "false",
            "PAPER_ACTIVITY_MIN_DECISION_RECORDS": "0",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "accepted_decisions=1" in result.stderr
    assert "unmaterialized_accepted_symbols=[DASH]" in result.stderr
    assert "materialized_entry_symbols=[]" in result.stderr
    assert "latest_accepted_decision_log=2026-06-29 16:00:00+00" in result.stderr
    assert not docker_marker.exists()


def test_paper_activity_fails_when_some_accepted_symbols_do_not_materialize(
    tmp_path: Path,
) -> None:
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
        "printf '10|0|10|10|0|2026-06-29 16:00:00+00|false||false||2026-06-29 16:00:00+00|0|10|10|0|10|10|2026-06-29 16:00:00+00|accepted/none/none:2,skipped_no_signal/none/none:8|2|2026-06-29 16:00:00+00|1|submitted:1|2|DASH,SNOW|1|DASH|1|SNOW|0||bull_flag|||0|1|0|0\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE": "false",
            "PAPER_ACTIVITY_MIN_DECISION_RECORDS": "0",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "accepted_decisions=2" in result.stderr
    assert "accepted_symbols=[DASH,SNOW]" in result.stderr
    assert "materialized_entry_symbols=[DASH]" in result.stderr
    assert "unmaterialized_accepted_symbols=[SNOW]" in result.stderr
    assert not docker_marker.exists()


def test_paper_activity_fails_on_stale_pending_entry_orders(tmp_path: Path) -> None:
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
        "printf '10|0|10|1000|0|2026-06-29 16:00:00+00|false||false||2026-06-29 16:00:00+00|0|10|1000|0|10|1000|2026-06-29 16:00:00+00|accepted/none/none:1,skipped_no_signal/none/none:999|1|2026-06-29 16:00:00+00|1|pending_submit:1|1|DASH|1|DASH|0||1|DASH:16:00:00|bull_flag|||0|1|0|0\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE": "false",
            "PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES": "5",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "stale pending entry orders count=1" in result.stderr
    assert "max_age_minutes=5" in result.stderr
    assert "symbols=[DASH:16:00:00]" in result.stderr
    assert not docker_marker.exists()


def test_paper_activity_passes_diagnostic_mode_to_readiness_runner(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "PAPER_READINESS_AUTO_RESUME=true",
                "PAPER_READINESS_AUTO_RESET_WEIGHTS=true",
                "PAPER_READINESS_CLOSE_ONLY_ON_FAILURE=true",
                "PAPER_READINESS_REQUIRE_FLAT=true",
            ]
        )
    )
    fake_runner = tmp_path / "readiness_runner.sh"
    fake_runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'readiness_overrides auto_resume=%s auto_reset=%s close_only=%s require_flat=%s\\n' "
        '"${PAPER_READINESS_AUTO_RESUME:-}" '
        '"${PAPER_READINESS_AUTO_RESET_WEIGHTS:-}" '
        '"${PAPER_READINESS_CLOSE_ONLY_ON_FAILURE:-}" '
        '"${PAPER_READINESS_REQUIRE_FLAT:-}"\n'
        "exit 48\n"
    )
    fake_runner.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": "/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE": "false",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert (
        "readiness_overrides auto_resume=false auto_reset=false "
        "close_only=false require_flat=false"
    ) in result.stdout
    assert "paper activity pending: readiness repair lock busy" in result.stdout


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
        "if printf '%s\\n' \"$*\" | grep -q -- '--entrypoint python admin'; then\n"
        "  printf 'ok|100000.00|200000.00|5000.00|false|0|0|none|none\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '12|4|8|7840|0|2026-06-29 14:15:00+00|false||false||2026-06-29 14:15:00+00|4|8|7840|0|8|7840|2026-06-29 14:15:00+00|skipped_no_signal/none/none:7838,rejected/vwap_filter/below_vwap:2|0||0||0||0||0||0||bull_flag|paper_readiness_check_missing:4|paper_readiness_check_missing:4|0|0|0|0\\n'\n"
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
    assert (
        "bull_flag_decision_log_summary=[skipped_no_signal/none/none:7838,"
        "rejected/vwap_filter/below_vwap:2]"
    ) in result.stdout
    assert "dispatch_failures=0" in result.stdout
    assert "stream_issues=0" in result.stdout
    assert "broker_account_status=ok" in result.stdout
    assert "broker_open_orders=0" in result.stdout
    assert "broker_open_positions=0" in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_allows_post_flatten_strategy_session_block(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "POSTGRES_USER=postgres",
                "POSTGRES_DB=postgres",
                "FLATTEN_TIME=15:45",
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
    fake_date.write_text(
        "#!/usr/bin/env bash\n"
        "case \"${*: -1}\" in\n"
        "  +%H:%M) printf '15:50\\n' ;;\n"
        "  *) printf '2026-06-29\\n' ;;\n"
        "esac\n"
    )
    fake_date.chmod(0o755)
    docker_marker = tmp_path / "docker_close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "if printf '%s\\n' \"$*\" | grep -q ' admin close-only'; then\n"
        f"  touch {docker_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$*\" | grep -q -- '--entrypoint python admin'; then\n"
        "  printf 'ok|100000.00|200000.00|5000.00|false|0|0|none|none\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '12|0|12|9000|0|2026-06-29 19:49:00+00|false||true|strategy_session_state_entries_disabled|2026-06-29 19:49:00+00|2|12|9000|0|12|9000|2026-06-29 19:49:00+00|skipped_no_signal/none/none:9000|0||0||0||0||0||0||bull_flag||strategy_session_state_entries_disabled:2|0|0|0|0\\n'\n"
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
    assert "latest_bull_flag_blocked=true" in result.stdout
    assert "post_flatten_strategy_blocked=true" in result.stdout
    assert "stock_open_positions=0" in result.stdout
    assert "active_stock_orders=0" in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_allows_flat_profit_lock_pause(tmp_path: Path) -> None:
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
        "if printf '%s\\n' \"$*\" | grep -q ' admin status'; then\n"
        "  printf 'mode=paper strategy=v1-breakout status=close_only kill_switch=false reason=paper profit lock: stop-out projection negative updated_at=2026-06-29T16:00:00+00:00\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if printf '%s\\n' \"$*\" | grep -q -- '--entrypoint python admin'; then\n"
        "  printf 'ok|100000.00|200000.00|5000.00|false|0|0|none|none\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '12|4|8|882|0|2026-06-29 16:02:00+00|true|trading_status:close_only,runtime_reconciliation_mismatch|true|trading_status:close_only,entry_cadence_waiting_for_new_bar,runtime_reconciliation_mismatch|2026-06-29 16:01:00+00|4|8|882|0|8|882|2026-06-29 16:01:00+00|accepted/none/none:1,skipped_no_signal/none/none:881|1|2026-06-29 16:01:00+00|1|filled:1|1|AEVA|1|AEVA|0||0||bull_flag|trading_status:close_only:4|trading_status:close_only:4,entry_cadence_waiting_for_new_bar:4|0|0|0|0\\n'\n"
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
    assert "paper_profit_lock_pause=true" in result.stdout
    assert "latest_cycle_entries_disabled=true" in result.stdout
    assert "latest_bull_flag_blocked=true" in result.stdout
    assert "stock_open_positions=0" in result.stdout
    assert "active_stock_orders=0" in result.stdout
    assert "broker_open_orders=0" in result.stdout
    assert "broker_open_positions=0" in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_skips_when_latest_supervisor_activity_is_market_closed(
    tmp_path: Path,
) -> None:
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
        "if printf '%s\\n' \"$*\" | grep -q -- '--entrypoint python admin'; then\n"
        "  printf 'closed|timestamp=2026-06-29T20:02:00+00:00 next_open=2026-06-30T13:30:00+00:00 next_close=2026-06-30T20:00:00+00:00\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '12|4|8|7840|3|2026-06-29 19:59:00+00|true|trading_status:close_only,paper_readiness_check_missing|true|trading_status:close_only,paper_readiness_check_missing,strategy_session_state_entries_disabled|2026-06-29 19:59:00+00|4|8|7840|0|8|7840|2026-06-29 19:59:00+00|skipped_no_signal/none/none:7840|0||0||0||0||0||0||bull_flag|trading_status:close_only:4,paper_readiness_check_missing:4|strategy_session_state_entries_disabled:4|0|0|0|0|true\\n'\n"
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

    assert result.returncode == 0
    assert "paper activity skipped: latest supervisor activity is market_closed" in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_latest_readiness_missing_cycle_is_pending(tmp_path: Path) -> None:
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
        "printf 'scheduled check context: session_date=2026-06-29 proof_start=2026-06-29 reason=stale_after_supervisor_start\\n'\n"
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
        "printf '12|4|8|7840|0|2026-06-29 16:02:00+00|true|paper_readiness_check_missing|true|paper_readiness_check_missing|2026-06-29 16:01:00+00|4|8|7840|0|8|7840|2026-06-29 16:01:00+00|skipped_no_signal/none/none:7838,rejected/vwap_filter/below_vwap:2|0||0||0||0||0||bull_flag|paper_readiness_check_missing:4|paper_readiness_check_missing:4|0|0|0|0\\n'\n"
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

    assert result.returncode == 43
    assert (
        "paper activity pending: latest supervisor cycle still had entries disabled "
        "for paper_readiness_check_missing"
    ) in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_latest_runtime_reconciliation_cycle_is_pending(
    tmp_path: Path,
) -> None:
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
        "printf '12|4|8|7840|0|2026-06-29 16:02:00+00|true|runtime_reconciliation_mismatch|true|runtime_reconciliation_mismatch|2026-06-29 16:01:00+00|4|8|7840|0|8|7840|2026-06-29 16:01:00+00|skipped_no_signal/none/none:7838,rejected/vwap_filter/below_vwap:2|0||0||0||0||0||bull_flag|runtime_reconciliation_mismatch:4|runtime_reconciliation_mismatch:4|0|0|0|0\\n'\n"
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

    assert result.returncode == 43
    assert (
        "paper activity pending: latest supervisor cycle still had entries disabled "
        "for runtime_reconciliation_mismatch"
    ) in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_fails_when_broker_account_is_blocked(tmp_path: Path) -> None:
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
        "if printf '%s\\n' \"$*\" | grep -q -- '--entrypoint python admin'; then\n"
        "  printf 'blocked|100000.00|1000.00|5000.00|true|0|0|none|none\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '12|0|8|1000|0|2026-06-29 14:15:00+00|false||false||2026-06-29 14:15:00+00|0|8|1000|0|8|1000|2026-06-29 14:15:00+00|skipped_no_signal/none/none:1000|0||0||0||0||0||0||bull_flag|||0|0|0|0\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE": "false",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "broker account not tradable" in result.stderr
    assert "buying_power=1000.00" in result.stderr
    assert "minimum_required=5000.00" in result.stderr
    assert "trading_blocked=true" in result.stderr
    assert not docker_marker.exists()


def test_paper_activity_fails_on_recent_dispatch_failures(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE=true",
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
        "printf '12|0|8|7840|0|2026-06-29 14:15:00+00|false||false||2026-06-29 14:15:00+00|0|8|7840|0|8|7840|2026-06-29 14:15:00+00|skipped_no_signal/none/none:7840|0||0||0||0||0||0||bull_flag|||0|0|2|0\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE": "false",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "order dispatch failure events" in result.stderr
    assert "count=2" in result.stderr
    assert not docker_marker.exists()


def test_paper_activity_fails_on_recent_stream_issues(tmp_path: Path) -> None:
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
        "printf '12|0|8|7840|0|2026-06-29 14:15:00+00|false||false||2026-06-29 14:15:00+00|0|8|7840|0|8|7840|2026-06-29 14:15:00+00|skipped_no_signal/none/none:7840|0||0||0||0||0||0||bull_flag|||0|0|0|2\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE": "false",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "trade update stream issues" in result.stderr
    assert "count=2" in result.stderr
    assert not docker_marker.exists()


def test_paper_activity_diagnostic_failure_does_not_apply_close_only(tmp_path: Path) -> None:
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
        "printf '0|0|0|0|0||false||false|||0|0|0|0|0|0|||0||0||0||0||0||0||bull_flag|||0|0|0|0\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_activity_check.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PAPER_ACTIVITY_READINESS_RUNNER": str(fake_runner),
            "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE": "false",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "paper activity failed: no supervisor cycles" in result.stderr
    assert (
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 strategy=bull_flag"
    ) in result.stdout
    assert not docker_marker.exists()


def test_paper_activity_failure_preserves_active_profit_lock(tmp_path: Path) -> None:
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
    close_only_marker = tmp_path / "docker_close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "if printf '%s\\n' \"$*\" | grep -q ' admin close-only'; then\n"
        f"  touch {close_only_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$*\" | grep -q ' admin status'; then\n"
        "  printf 'mode=paper strategy=v1-breakout status=close_only kill_switch=false reason=paper profit lock: stop-out projection negative updated_at=2026-06-29T16:00:00+00:00\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '0|0|0|0|0||false||false|||0|0|0|0|0|0|||0||0||0||0||0||0||bull_flag|||0|0|0|0\\n'\n"
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

    assert result.returncode == 1
    assert "paper activity failed: no supervisor cycles" in result.stderr
    assert "paper activity preserving active paper profit lock" in result.stdout
    assert "reason=paper profit lock" in result.stdout
    assert not close_only_marker.exists()


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


def test_session_guard_uses_profit_probe_start_after_sourcing_env(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-07-06",
                "PROFIT_PROBE_STRATEGY=bull_flag",
            ]
        )
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "if printf '%s\\n' \"$args\" | grep -q 'BROKER_FLAT_CONTEXT'; then\n"
        "  printf 'bull_flag session guard pending 2026-07-06 "
        "broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'unexpected docker call: %s\\n' \"$args\" >&2\n"
        "exit 99\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/session_guard.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "SESSION_GUARD_DATE": "2026-07-05",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert (
        "scheduled check context: session_date=2026-07-05 "
        "proof_start=2026-07-06 strategy=bull_flag"
    ) in result.stdout
    assert (
        "session guard pending: latest completed session 2026-07-05 "
        "is before proof start 2026-07-06"
    ) in result.stdout
    assert "broker exposure ok: open_orders=0 open_positions=0" in result.stdout


def test_session_guard_preserves_invocation_overrides_after_env_source(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "SESSION_GUARD_STRATEGY=env_flag",
                "SESSION_GUARD_MIN_TRADES=not-an-int",
                "SESSION_GUARD_FAIL_BELOW_PNL=not-a-number",
                "SESSION_GUARD_FAIL_ON_DIAGNOSTICS=maybe",
                "SESSION_GUARD_START_DATE=2026-07-06",
                "SESSION_GUARD_DATE=2026-07-05",
            ]
        )
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "if printf '%s\\n' \"$args\" | grep -q 'BROKER_FLAT_CONTEXT'; then\n"
        "  printf 'custom_flag session guard pending 2026-07-07 broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'unexpected docker call: %s\\n' \"$args\" >&2\n"
        "exit 99\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/session_guard.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "SESSION_GUARD_STRATEGY": "custom_flag",
            "SESSION_GUARD_MIN_TRADES": "4",
            "SESSION_GUARD_FAIL_BELOW_PNL": "-1.25",
            "SESSION_GUARD_FAIL_ON_DIAGNOSTICS": "false",
            "SESSION_GUARD_START_DATE": "2026-07-07",
            "SESSION_GUARD_DATE": "2026-07-06",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert (
        "scheduled check context: session_date=2026-07-06 "
        "proof_start=2026-07-07 strategy=custom_flag"
    ) in result.stdout
    assert (
        "session guard pending: latest completed session 2026-07-06 "
        "is before proof start 2026-07-07"
    ) in result.stdout
    assert "custom_flag session guard pending 2026-07-07" in result.stdout


def test_session_guard_reuses_recent_pass_after_broker_flat(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "SESSION_GUARD_DATE=2026-06-29",
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
        "if printf '%s\\n' \"$args\" | grep -q 'SESSION_GUARD_PASS_SESSION_DATE'; then\n"
        "  printf 'session_guard_latest_pass=2026-06-29T21:38:40.551317Z|31\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'BROKER_FLAT_CONTEXT'; then\n"
        "  printf 'bull_flag session guard prior pass 2026-06-29 broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-session-eval'; then\n"
        f"  touch {session_eval_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q ' admin close-only'; then\n"
        f"  touch {close_only_marker}\n"
        "  exit 99\n"
        "fi\n"
        "printf 'unexpected docker call: %s\\n' \"$args\" >&2\n"
        "exit 99\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/session_guard.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "SESSION_GUARD_REUSE_PASS_MAX_AGE_MINUTES": "180",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 strategy=bull_flag min_trades=10 min_pnl=0 "
        "reason=already_passed"
    ) in result.stdout
    assert "session guard already passed for session 2026-06-29" in result.stdout
    assert "broker exposure ok: open_orders=0 open_positions=0" in result.stdout
    assert not session_eval_marker.exists()
    assert not close_only_marker.exists()


def test_session_guard_below_pnl_after_min_trades_stays_pending(
    tmp_path: Path,
) -> None:
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
    funnel_marker = tmp_path / "funnel_called"
    close_only_marker = tmp_path / "close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-session-eval'; then\n"
        f"  touch {session_eval_marker}\n"
        "  printf 'Session Evaluation: 2026-06-29\\n'\n"
        "  printf 'Trades:   10\\n'\n"
        "  printf 'Total PnL: $-12.34\\n'\n"
        "  printf 'Guard failed: pnl=$-12.34 below $0.00 after 10 trades.\\n'\n"
        "  exit 42\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-funnel-report'; then\n"
        f"  touch {funnel_marker}\n"
        "  printf 'funnel diagnostic ok\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q ' admin close-only'; then\n"
        f"  touch {close_only_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'BROKER_FLAT_CONTEXT'; then\n"
        "  printf 'bull_flag session guard 2026-06-29 broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '2026-06-29\\n'\n"
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
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 strategy=bull_flag min_trades=10 min_pnl=0"
    ) in result.stdout
    assert "Guard failed: pnl=$-12.34 below $0.00 after 10 trades." in result.stdout
    assert (
        "session guard pending: same-day pnl below 0 after 10+ trades; "
        "continuing cumulative proof window"
    ) in result.stdout
    assert "broker exposure ok: open_orders=0 open_positions=0" in result.stdout
    assert session_eval_marker.exists()
    assert funnel_marker.exists()
    assert not close_only_marker.exists()


def test_paper_profit_probe_pending_before_proof_start_does_not_close_only(tmp_path: Path) -> None:
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
    funnel_marker = tmp_path / "funnel_called"
    close_only_marker = tmp_path / "close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-session-eval'; then\n"
        f"  touch {session_eval_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-funnel-report'; then\n"
        f"  touch {funnel_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q ' admin close-only'; then\n"
        f"  touch {close_only_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'BROKER_FLAT_CONTEXT'; then\n"
        "  printf 'bull_flag paper proof pending 2026-06-29 broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '2026-06-26\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_profit_probe.sh", str(env_file)],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert (
        "scheduled check context: session_date=2026-06-26 "
        "proof_start=2026-06-29 strategy=bull_flag min_trades=10 min_pnl=0.01"
    ) in result.stdout
    assert (
        "paper profit probe pending: latest completed session 2026-06-26 "
        "is before proof start 2026-06-29"
    ) in result.stdout
    assert "broker exposure ok: open_orders=0 open_positions=0" in result.stdout
    assert not session_eval_marker.exists()
    assert not funnel_marker.exists()
    assert not close_only_marker.exists()


def test_paper_profit_probe_preserves_invocation_overrides_after_env_source(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_STRATEGY=env_flag",
                "PROFIT_PROBE_MIN_TRADES=not-an-int",
                "PROFIT_PROBE_MIN_PNL=not-a-number",
                "PROFIT_PROBE_START_DATE=2026-07-06",
                "PROFIT_PROBE_FAIL_ON_DIAGNOSTICS=maybe",
                "PROFIT_PROBE_DATE=2026-07-05",
            ]
        )
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "if printf '%s\\n' \"$args\" | grep -q 'BROKER_FLAT_CONTEXT'; then\n"
        "  printf 'custom_flag paper proof pending 2026-07-07 broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'unexpected docker call: %s\\n' \"$args\" >&2\n"
        "exit 99\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_profit_probe.sh", str(env_file)],
        cwd=Path.cwd(),
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PROFIT_PROBE_STRATEGY": "custom_flag",
            "PROFIT_PROBE_MIN_TRADES": "12",
            "PROFIT_PROBE_MIN_PNL": "2.34",
            "PROFIT_PROBE_START_DATE": "2026-07-07",
            "PROFIT_PROBE_FAIL_ON_DIAGNOSTICS": "false",
            "PROFIT_PROBE_DATE": "2026-07-06",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert (
        "scheduled check context: session_date=2026-07-06 "
        "proof_start=2026-07-07 strategy=custom_flag min_trades=12 min_pnl=2.34"
    ) in result.stdout
    assert (
        "paper profit probe pending: latest completed session 2026-07-06 "
        "is before proof start 2026-07-07"
    ) in result.stdout
    assert "custom_flag paper proof pending 2026-07-07" in result.stdout


def test_paper_profit_probe_insufficient_trades_after_start_stays_pending(
    tmp_path: Path,
) -> None:
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
    funnel_marker = tmp_path / "funnel_called"
    close_only_marker = tmp_path / "close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-session-eval'; then\n"
        f"  touch {session_eval_marker}\n"
        "  printf 'Session Evaluation: 2026-06-29..2026-06-29\\n'\n"
        "  printf 'Trades:   3\\n'\n"
        "  printf 'Total PnL: $12.34\\n'\n"
        "  printf 'Proof incomplete: 3 closed trades below required 10.\\n'\n"
        "  exit 43\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-funnel-report'; then\n"
        f"  touch {funnel_marker}\n"
        "  printf 'funnel diagnostic ok\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q ' admin close-only'; then\n"
        f"  touch {close_only_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'BROKER_FLAT_CONTEXT'; then\n"
        "  printf 'bull_flag paper proof 2026-06-29..2026-06-29 broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '2026-06-29\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_profit_probe.sh", str(env_file)],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert (
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 strategy=bull_flag min_trades=10 min_pnl=0.01"
    ) in result.stdout
    assert "Proof incomplete: 3 closed trades below required 10." in result.stdout
    assert "broker exposure ok: open_orders=0 open_positions=0" in result.stdout
    assert session_eval_marker.exists()
    assert funnel_marker.exists()
    assert not close_only_marker.exists()


def test_paper_profit_probe_below_pnl_after_min_trades_stays_pending(
    tmp_path: Path,
) -> None:
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
    funnel_marker = tmp_path / "funnel_called"
    close_only_marker = tmp_path / "close_only_called"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "args=\"$*\"\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-session-eval'; then\n"
        f"  touch {session_eval_marker}\n"
        "  printf 'Session Evaluation: 2026-06-29..2026-06-29\\n'\n"
        "  printf 'Trades:   10\\n'\n"
        "  printf 'Total PnL: $-12.34\\n'\n"
        "  printf 'Guard failed: pnl=$-12.34 below $0.01 after 10 trades.\\n'\n"
        "  exit 42\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'alpaca-bot-funnel-report'; then\n"
        f"  touch {funnel_marker}\n"
        "  printf 'funnel diagnostic ok\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q ' admin close-only'; then\n"
        f"  touch {close_only_marker}\n"
        "  exit 99\n"
        "fi\n"
        "if printf '%s\\n' \"$args\" | grep -q 'BROKER_FLAT_CONTEXT'; then\n"
        "  printf 'bull_flag paper proof 2026-06-29..2026-06-29 broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '2026-06-29\\n'\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_profit_probe.sh", str(env_file)],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 43
    assert (
        "scheduled check context: session_date=2026-06-29 "
        "proof_start=2026-06-29 strategy=bull_flag min_trades=10 min_pnl=0.01"
    ) in result.stdout
    assert "Guard failed: pnl=$-12.34 below $0.01 after 10 trades." in result.stdout
    assert (
        "paper profit probe pending: cumulative pnl below 0.01 after 10+ trades; "
        "continuing proof window"
    ) in result.stdout
    assert "broker exposure ok: open_orders=0 open_positions=0" in result.stdout
    assert session_eval_marker.exists()
    assert funnel_marker.exists()
    assert not close_only_marker.exists()


def test_paper_proof_status_is_read_only(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "PROOF_STATUS_FAIL_ON_ISSUES=maybe",
            ]
        )
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker_calls"
    mutating_marker = tmp_path / "mutating_call"
    fake_runtime_health = tmp_path / "runtime_image_health_check.sh"
    fake_runtime_health.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'runtime image health ok: services=web,supervisor files=15\\n'\n"
    )
    fake_runtime_health.chmod(0o755)
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
        "  printf 'paper proof watchlist: status=ok active=980 enabled=986 ignored=6 required_active=900\\n'\n"
        "  printf 'paper proof sizing: status=ok confidence_floor=0.25 manual_baseline=0.25 set_by=operator required_floor=0.25 weight_status=ok active_weights=[bull_flag] stored_weights=[bull_flag] weight_sum=1 target_weight=1.0 target_sharpe=0.0\\n'\n"
        "  printf 'paper proof runtime: ops_status=ok ops_detail=status=ok db=ok trading_mode=paper strategy_version=v1-breakout trading_status=enabled kill_switch_enabled=False enabled_strategies=bull_flag worker_status=fresh image_status=ok image_detail=runtime image health ok: services=web,supervisor files=15\\n'\n"
        "  printf 'paper proof stream: status=ok latest_start=2026-06-29T12:59:59+00:00 latest_event=trade_update_stream_started:2026-06-29T12:59:59+00:00 latest_supervisor_started_at=2026-06-29T13:00:00+00:00 grace_seconds=120\\n'\n"
        "  printf 'paper proof readiness audit: status=ok target_session=2026-06-29 check_status=passed created_at=2026-06-29T13:20:00+00:00 latest_supervisor_started_at=2026-06-29T13:00:00+00:00\\n'\n"
        "  printf 'paper proof readiness decision dry run: status=ok strategy=bull_flag as_of=2026-06-26T15:30:00-04:00 active=980 decision_records=965 accepted=1 entry_intents=1 sample=TPB:39.62732912119471@87.05\\n'\n"
        "  printf 'paper proof activity audit: status=ok target_session=2026-06-29 due=true due_after=2026-06-29 10:45 America/New_York check=passed:0:2026-06-29T14:36:00+00:00\\n'\n"
        "  printf 'paper proof post-close audit: status=ok target_session=2026-06-29 due=true due_after=2026-06-29 17:25 America/New_York session_guard=passed:0:2026-06-29T21:10:00+00:00 paper_profit_probe=pending:43:2026-06-29T21:20:00+00:00\\n'\n"
        "  printf 'paper proof scheduled check: name=paper_profit_probe status=pending exit_code=43 session_date=2026-06-26 proof_start=2026-06-29 created_at=2026-06-27T22:00:00.000000Z\\n'\n"
        "  printf 'paper proof progress: status=pending closed_trades=3 required_trades=10 pnl=12.34 required_pnl=0.01 window=2026-06-29..2026-06-29 first_exit_session=2026-06-29 latest_exit_session=2026-06-29\\n'\n"
        "  printf 'paper proof trade quality: wins=2 losses=1 flats=0 win_rate=66.7%% avg_pnl=4.11 best=AVBP:10.00@2026-06-29 worst=DASH:-1.00@2026-06-29 recent=AVBP:10.00@2026-06-29,DASH:-1.00@2026-06-29,WDFC:3.34@2026-06-29\\n'\n"
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
            "PROOF_STATUS_FAIL_ON_ISSUES": "false",
            "PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT": str(fake_runtime_health),
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
    assert "paper proof watchlist: status=ok active=980 enabled=986 ignored=6 required_active=900" in result.stdout
    assert "paper proof sizing: status=ok confidence_floor=0.25" in result.stdout
    assert "paper proof runtime: ops_status=ok" in result.stdout
    assert "image_status=ok" in result.stdout
    assert "paper proof stream: status=ok latest_start=2026-06-29T12:59:59+00:00" in result.stdout
    assert "paper proof readiness audit: status=ok target_session=2026-06-29" in result.stdout
    assert "paper proof readiness decision dry run: status=ok strategy=bull_flag" in result.stdout
    assert "decision_records=965 accepted=1 entry_intents=1 sample=TPB" in result.stdout
    assert "paper proof activity audit: status=ok target_session=2026-06-29" in result.stdout
    assert "paper proof post-close audit: status=ok target_session=2026-06-29" in result.stdout
    assert "paper proof scheduled check: name=paper_profit_probe status=pending" in result.stdout
    assert (
        "paper proof progress: status=pending closed_trades=3 "
        "required_trades=10 pnl=12.34 required_pnl=0.01"
    ) in result.stdout
    assert "paper proof trade quality: wins=2 losses=1 flats=0" in result.stdout
    assert "win_rate=66.7% avg_pnl=4.11" in result.stdout
    assert not mutating_marker.exists()
    calls = docker_calls.read_text()
    assert "close-only" not in calls
    assert "resume" not in calls
    assert "alpaca-bot-session-eval" not in calls
    assert "--expect-trading-mode paper" in calls
    assert "--expect-strategy-version v1-breakout" in calls
    assert "--expect-trading-status enabled" in calls
    assert "--expect-kill-switch false" in calls
    assert "--expect-only-enabled-strategy bull_flag" in calls


def test_paper_proof_status_labels_pre_start_window_with_completed_session() -> None:
    script = Path("scripts/paper_proof_status.sh").read_text()

    assert "capture_env_overrides" in script
    assert "restore_env_overrides" in script
    assert script.index('source "$ENV_FILE"') < script.index("\nrestore_env_overrides\n")
    assert "PROOF_STATUS_FAIL_ON_ISSUES \\" in script
    assert script.index('source "$ENV_FILE"') < script.index(
        'PROOF_STATUS_STRATEGY="${PROOF_STATUS_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"'
    )
    assert script.index('source "$ENV_FILE"') < script.index(
        'PROOF_STATUS_START_DATE="${PROOF_STATUS_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-30}}"'
    )
    assert "load_latest_completed_session_date" in script
    assert "load_next_market_session_date" in script
    assert "load_previous_market_session_date" in script
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
    assert "partial_pnl_negative" in script
    assert "partial_pnl_below_minimum" in script
    assert "cumulative_pnl_negative" in script
    assert "cumulative_pnl_below_minimum" in script
    assert "unpaired_filled_exits" in script
    assert "scheduled check context: session_date=$(TZ=America/New_York date +%F)" in script
    assert "PROOF_STATUS_FAIL_ON_ISSUES" in script
    assert "PROOF_STATUS_FAIL_ON_ISSUES must be true or false" in script
    assert "-e PROOF_STATUS_FAIL_ON_ISSUES=\"$PROOF_STATUS_FAIL_ON_ISSUES\"" in script
    assert "./scripts/cron_health_check.sh 2>&1" in script
    assert "PROOF_STATUS_CRON_HEALTH_STATUS" in script
    assert "PROOF_STATUS_CRON_HEALTH_DETAIL" in script
    assert "PROOF_STATUS_MIN_WATCHLIST_SYMBOLS" in script
    assert "PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900" in script
    assert "PROOF_STATUS_MIN_WATCHLIST_SYMBOLS must be a positive integer" in script
    assert "PROOF_STATUS_MIN_CONFIDENCE_FLOOR" in script
    assert "PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.25" in script
    assert "PROOF_STATUS_MIN_CONFIDENCE_FLOOR must be a non-negative number" in script
    assert "PROOF_STATUS_REQUIRE_SCENARIOS" in script
    assert "PAPER_READINESS_REQUIRE_SCENARIOS:-true" in script
    assert "PROOF_STATUS_REQUIRE_SCENARIOS must be true or false" in script
    assert "PROOF_STATUS_SCENARIO_DIR" in script
    assert "PAPER_READINESS_SCENARIO_DIR:-/var/lib/alpaca-bot/nightly/scenarios" in script
    assert 'scenario_volume_args=(-v "$PROOF_STATUS_SCENARIO_DIR:$PROOF_STATUS_SCENARIO_DIR:ro")' in script
    assert "-e PROOF_STATUS_REQUIRE_SCENARIOS=\"$PROOF_STATUS_REQUIRE_SCENARIOS\"" in script
    assert "-e PROOF_STATUS_SCENARIO_DIR=\"$PROOF_STATUS_SCENARIO_DIR\"" in script
    assert "PROOF_STATUS_STREAM_START_GRACE_SECONDS" in script
    assert "PROOF_STATUS_STREAM_START_GRACE_SECONDS must be a non-negative integer" in script
    assert "PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES" in script
    assert "PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES must be a positive integer" in script
    assert "PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS" in script
    assert "PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" in script
    assert "-e PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS=\"$PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS\"" in script
    assert "-e PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS=\"$PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS\"" in script
    assert "./scripts/ops_check.sh \"$ENV_FILE\"" in script
    assert "--expect-trading-mode \"$trading_mode\"" in script
    assert "--expect-strategy-version \"$STRATEGY_VERSION\"" in script
    assert "--expect-trading-status enabled" in script
    assert "--expect-kill-switch false" in script
    assert "--expect-only-enabled-strategy \"$PROOF_STATUS_STRATEGY\"" in script
    assert "PROOF_STATUS_OPS_HEALTH_STATUS" in script
    assert "PROOF_STATUS_OPS_HEALTH_DETAIL" in script
    assert "PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT" in script
    assert "runtime_image_health_check.sh" in script
    assert "PROOF_STATUS_RUNTIME_IMAGE_HEALTH_STATUS" in script
    assert "PROOF_STATUS_RUNTIME_IMAGE_HEALTH_DETAIL" in script
    assert "cron_health_failed" in script
    assert "ops_health_failed" in script
    assert "profit_lock_pause" in script
    assert "accepted flat paper profit lock" in script
    assert "trading_status_reason.startswith(\"paper profit lock\")" in script
    assert "runtime_image_health_failed" in script
    assert "compact_check_detail()" in script
    assert "paper proof automation:" in script
    assert "cron_status={cron_health_status}" in script
    assert "cron_detail={cron_health_detail or 'none'}" in script
    assert "paper proof runtime:" in script
    assert "ops_status={ops_health_status}" in script
    assert "ops_detail={ops_health_detail or 'none'}" in script
    assert "image_status={runtime_image_health_status}" in script
    assert "image_detail={runtime_image_health_detail or 'none'}" in script
    assert "readiness_target_session = next_market_session or current_market_date" in script
    assert "if readiness_target_session < proof_start" in script
    assert "event_type = 'supervisor_started'" in script
    assert "latest_supervisor_started_at" in script
    assert "'trade_update_stream_started'" in script
    assert "'trade_update_stream_stopped'" in script
    assert "'trade_update_stream_failed'" in script
    assert "'trade_update_failed'" in script
    assert "'stream_heartbeat_stale'" not in script
    assert "'stream_restart_failed'" in script
    assert "'protective_stop_quantity_replace_failed'" in script
    assert "latest_stream_started_at" in script
    assert "stream_issue_status_by_event_type" in script
    assert '"trade_update_failed": "trade_update_failed"' in script
    assert '"stream_heartbeat_stale": "heartbeat_stale"' not in script
    assert '"stream_restart_failed": "restart_failed"' in script
    assert (
        '"protective_stop_quantity_replace_failed": '
        '"protective_stop_quantity_replace_failed"'
    ) in script
    assert "stream_status = \"missing\"" in script
    assert "stream_status = \"stale\"" in script
    assert "blockers.append(f\"stream_{stream_status}\")" in script
    assert "paper proof stream:" in script
    assert "latest_start={latest_stream_started_text}" in script
    assert "latest_event={latest_stream_event_text}" in script
    assert "grace_seconds={stream_start_grace_seconds}" in script
    assert "payload->>'check_name' = 'paper_readiness'" in script
    assert "payload->>'session_date' = %s" in script
    assert "payload->>'proof_start' = %s" in script
    assert "COALESCE(payload->>'reason', '') AS reason" in script
    assert "payload->>'decision_dry_run_strategy'" in script
    assert "payload->>'decision_dry_run_records'" in script
    assert "payload->>'decision_dry_run_sample_times'" in script
    assert "payload->>'decision_dry_run_evaluations'" in script
    assert "payload->>'decision_dry_run_min_decision_records'" in script
    assert "payload->>'decision_dry_run_max_entry_intents'" in script
    assert "payload->>'decision_dry_run_reject_stages'" in script
    assert "payload->>'decision_dry_run_reject_reasons'" in script
    assert "readiness_audit_rows = cur.fetchall()" in script
    assert "LIMIT 32" in script
    assert "parse_int_or_none" in script
    assert "min_decision_dry_run_records" in script
    assert "min_decision_dry_run_evaluations" in script
    assert "PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS" in script
    assert "PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS must be a positive integer" in script
    assert "latest_readiness_reason.startswith(\"lock_busy\")" in script
    assert "(row for row in readiness_audit_rows if row[0] == \"passed\")" in script
    assert "def readiness_row_has_decision_dry_run" in script
    assert "def readiness_row_is_current" in script
    assert "readiness_decision_dry_run_row = readiness_audit_row" in script
    assert "and readiness_row_has_decision_dry_run(row)" in script
    assert "and readiness_row_is_current(row)" in script
    assert "readiness_decision_dry_run_row[3]" in script
    assert "readiness_audit_status" in script
    assert "readiness_audit_status = \"stale\"" in script
    assert "readiness_audit_status = \"stale_by_age\"" in script
    assert "readiness_audit_{readiness_audit_status}" in script
    assert "readiness_decision_dry_run_status" in script
    assert "readiness_decision_dry_run_{readiness_decision_dry_run_status}" in script
    assert "readiness_decision_dry_run_strategy != strategy_name" in script
    assert "readiness_decision_dry_run_status = \"strategy_mismatch\"" in script
    assert "readiness_decision_dry_run_status = \"active_under_minimum\"" in script
    assert "readiness_decision_dry_run_status = \"records_under_minimum\"" in script
    assert "readiness_decision_dry_run_status = \"evaluations_under_minimum\"" in script
    assert "readiness_decision_dry_run_status = \"sample_records_under_minimum\"" in script
    assert "readiness_decision_dry_run_status = \"accepted_under_minimum\"" in script
    assert "readiness_decision_dry_run_status = \"entry_intents_under_minimum\"" in script
    assert "readiness_decision_dry_run_accepted_value = parse_int_or_none" in script
    assert "readiness_decision_dry_run_entry_intents_value = parse_int_or_none" in script
    assert "paper proof readiness audit:" in script
    assert "status={readiness_audit_status}" in script
    assert "target_session={readiness_target_session.isoformat()}" in script
    assert "check_status={readiness_audit_check_status}" in script
    assert "created_at={readiness_audit_created_text}" in script
    assert "age_minutes={readiness_audit_age_text}" in script
    assert "max_age_minutes={readiness_max_pass_age_minutes}" in script
    assert "paper proof readiness decision dry run:" in script
    assert "required_active={min_watchlist_symbols}" in script
    assert "decision_records={readiness_decision_dry_run_records or 'none'}" in script
    assert "required_records={min_decision_dry_run_records}" in script
    assert "accepted={readiness_decision_dry_run_accepted or 'none'}" in script
    assert "sample_times={readiness_decision_dry_run_sample_times or 'none'}" in script
    assert "evaluations={readiness_decision_dry_run_evaluations or 'none'}" in script
    assert "required_evaluations={min_decision_dry_run_evaluations}" in script
    assert "min_decision_records={readiness_decision_dry_run_min_records or 'none'}" in script
    assert "max_accepted={readiness_decision_dry_run_max_accepted or 'none'}" in script
    assert "max_entry_intents={readiness_decision_dry_run_max_entry_intents or 'none'}" in script
    assert "reject_stages={readiness_decision_dry_run_reject_stages or 'none'}" in script
    assert "reject_reasons={readiness_decision_dry_run_reject_reasons or 'none'}" in script
    assert "sample={readiness_decision_dry_run_sample or 'none'}" in script
    assert "activity_target_session = None" in script
    assert "activity_first_check_time = time(10, 35)" in script
    assert "activity_first_due_time = time(10, 45)" in script
    assert "activity_late_check_time = time(14, 35)" in script
    assert "activity_late_due_time = time(14, 45)" in script
    assert "AND payload->>'proof_start' = %s" in script
    assert "activity_required_since = datetime.combine" in script
    assert "activity_required_since_text = activity_required_since.isoformat()" in script
    assert "payload->>'check_name' = 'paper_activity'" in script
    assert "activity_audit_status = \"not_due\"" in script
    assert "activity_audit_status = \"missing\"" in script
    assert "activity_audit_status = \"failed\"" in script
    assert "activity_audit_status = \"stale\"" in script
    assert "activity_check_status == \"skipped\"" in script
    assert "activity_audit_status = \"skipped\" if activity_due else \"ok\"" in script
    assert "activity_audit_status in {\"missing\", \"failed\", \"skipped\", \"stale\"}" in script
    assert "elif not activity_due:" in script
    assert "activity_audit_status = \"not_due\"" in script
    assert "activity_due and activity_audit_status == \"pending\"" in script
    assert "blockers.append(f\"activity_audit_{activity_audit_status}\")" in script
    assert "paper proof activity audit:" in script
    assert "status={activity_audit_status}" in script
    assert "target_session={activity_target_session.isoformat() if activity_target_session else 'none'}" in script
    assert "due={str(activity_due).lower()}" in script
    assert "required_since={activity_required_since_text}" in script
    assert "check={activity_check_status}:{activity_check_exit_code}:{activity_check_created_text}" in script
    assert "from datetime import date, datetime, time, timedelta, timezone" in script
    assert "post_close_target_session = proof_end if proof_end >= proof_start else None" in script
    assert "post_close_audit_rows = []" in script
    assert "post_close_pass_evidence_ready = False" in script
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
    assert "session_guard_parts = post_close_check_statuses[\"session_guard\"].split(\":\")" in script
    assert "session_guard_exit_code = session_guard_parts[1]" in script
    assert (
        "session_guard_status == \"pending\" and session_guard_exit_code == \"43\""
        in script
    )
    assert "session_guard_acceptable = session_guard_status == \"passed\"" in script
    assert "profit_probe_acceptable = profit_probe_status == \"passed\"" in script
    assert "if session_guard_status != \"missing\" and not session_guard_acceptable" in script
    assert "if profit_probe_status != \"missing\" and not profit_probe_acceptable" in script
    assert "session_guard_acceptable and profit_probe_status == \"passed\"" in script
    assert "session_guard_status == \"passed\" and profit_probe_status == \"passed\"" not in script
    assert "profitable_enough = trade_count >= min_trades and pnl >= min_pnl" in script
    assert "trade_pnl_rows = [" in script
    assert "unpaired_filled_exit_count = 0" in script
    assert "unpaired_filled_exit_symbols = \"none\"" in script
    assert "NOT EXISTS (" in script
    assert "AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name" in script
    assert "DATE(e.updated_at AT TIME ZONE %s)" in script
    assert "DATE(x.updated_at AT TIME ZONE %s)" in script
    assert "warnings.append(\"unpaired_filled_exits\")" in script
    assert "wins = sum(1 for _, trade_pnl in trade_pnl_rows if trade_pnl > 0)" in script
    assert "losses = sum(1 for _, trade_pnl in trade_pnl_rows if trade_pnl < 0)" in script
    assert "win_rate_text = f\"{win_rate:.1f}%\" if win_rate is not None else \"none\"" in script
    assert (
        "avg_trade_pnl_text = f\"{avg_trade_pnl:.2f}\" "
        "if avg_trade_pnl is not None else \"none\""
    ) in script
    assert "partial_pnl_negative" in script
    assert "partial_pnl_below_minimum" in script
    assert "cumulative_pnl_negative" in script
    assert "cumulative_pnl_below_minimum" in script
    assert "format_trade_pnl_atom" in script
    assert "elif profitable_enough and post_close_pass_evidence_ready" in script
    assert "elif profitable_enough:\n    proof_status = \"pending\"" in script
    assert "elif trade_count >= min_trades:\n    proof_status = \"pending\"" in script
    assert 'proof_status = "failing"' not in script
    assert "elif profitable_enough and not post_close_pass_evidence_ready" in script
    assert "profit_probe_status == \"pending\" and profit_probe_exit_code == \"43\"" in script
    assert "blockers.append(f\"post_close_audit_{post_close_audit_status}\")" in script
    assert "paper proof post-close audit:" in script
    assert "status={post_close_audit_status}" in script
    assert "target_session={post_close_target_session.isoformat() if post_close_target_session else 'none'}" in script
    assert "fail_on_issues = os.environ.get(\"PROOF_STATUS_FAIL_ON_ISSUES\"" in script
    assert "if fail_on_issues and (readiness_status != \"ready\" or blockers)" in script
    assert "raise SystemExit(1)" in script
    assert "if fail_on_issues and proof_status == \"pending\"" in script
    assert "raise SystemExit(43)" in script
    assert "due={str(post_close_due).lower()}" in script
    assert "session_guard={post_close_check_statuses['session_guard']}" in script
    assert "paper_profit_probe={post_close_check_statuses['paper_profit_probe']}" in script
    assert "strategy_disabled" in script
    assert "watchlist_under_minimum" in script
    assert "sizing_drifted" in script
    assert "symbol_watchlist" in script
    assert "active_watchlist_symbols" in script
    assert "active_symbol_names" in script
    assert "active_watchlist_symbol_names" in script
    assert "watchlist_status = (" in script
    assert "active_watchlist_symbols >= min_watchlist_symbols" in script
    assert "load_scenario_coverage" in script
    assert "format_problem_summary" in script
    assert "re.sub(r\"[^A-Za-z0-9_.:+/-]\", \"_\", value)" in script
    assert 'parts.append(f"{name}:{len(values)}:{examples}")' in script
    assert "scenario_expected_session = proof_end" in script
    assert "not end_value" in script
    assert "latest_completed_session >= current_market_date" in script
    assert "before_date=current_market_date" in script
    assert "scenario_expected_session = previous_session" in script
    assert 'scenario_dir / f"{symbol}_252d.json"' in script
    assert "blockers.append(f\"scenario_evidence_{scenario_status}\")" in script
    assert "paper proof scenarios:" in script
    assert "status={scenario_status}" in script
    assert "expected_session={scenario_expected_session.isoformat()}" in script
    assert "problems={scenario_problem_summary}" in script
    assert "paper proof watchlist:" in script
    assert "status={watchlist_status}" in script
    assert "active={active_watchlist_symbols}" in script
    assert "required_active={min_watchlist_symbols}" in script
    assert "confidence_floor_store" in script
    assert "strategy_weights" in script
    assert "weight_status = (" in script
    assert "confidence_floor_status = (" in script
    assert "sizing_status = (" in script
    assert "paper proof sizing:" in script
    assert "confidence_floor={confidence_floor_value:g}" in script
    assert "manual_baseline={confidence_floor_manual_baseline:g}" in script
    assert "required_floor={min_confidence_floor:g}" in script
    assert "weight_status={weight_status}" in script
    assert "target_weight={target_weight if target_weight is not None else 'missing'}" in script
    assert "posture_drifted" in script
    assert "broker_account_blocked" in script
    assert "awaiting_completed_proof_session" in script
    assert "awaiting_post_close_audit" in script
    assert "awaiting_min_trades" in script
    assert "profit_proven" in script
    assert "paper proof strategy status:" in script
    assert "status={strategy_status} target={strategy_name}" in script
    assert "paper proof posture:" in script
    assert "status={posture_status}" in script
    assert "market_data_feed={settings.market_data_feed.value}" in script
    assert "daily_sma_period={settings.daily_sma_period}" in script
    assert "breakout_lookback_bars={settings.breakout_lookback_bars}" in script
    assert (
        "relative_volume_lookback_bars="
        "{settings.relative_volume_lookback_bars}"
    ) in script
    assert "relative_volume_threshold={settings.relative_volume_threshold:g}" in script
    assert "entry_timeframe_minutes={settings.entry_timeframe_minutes}" in script
    assert "risk_per_trade_pct={settings.risk_per_trade_pct:g}" in script
    assert "max_position_pct={settings.max_position_pct:g}" in script
    assert "max_open_positions={settings.max_open_positions}" in script
    assert (
        "max_portfolio_exposure_pct="
        "{settings.max_portfolio_exposure_pct:g}"
    ) in script
    assert "daily_loss_limit_pct={settings.daily_loss_limit_pct:g}" in script
    assert "stop_limit_buffer_pct={settings.stop_limit_buffer_pct:g}" in script
    assert "entry_stop_price_buffer={settings.entry_stop_price_buffer:g}" in script
    assert "atr_period={settings.atr_period}" in script
    assert "atr_stop_multiplier={settings.atr_stop_multiplier:g}" in script
    assert (
        "trailing_stop_atr_multiplier="
        "{settings.trailing_stop_atr_multiplier:g}"
    ) in script
    assert (
        "trailing_stop_profit_trigger_r="
        "{settings.trailing_stop_profit_trigger_r:g}"
    ) in script
    assert "entry_window_start={as_hhmm(settings.entry_window_start)}" in script
    assert "entry_window_end={as_hhmm(settings.entry_window_end)}" in script
    assert "flatten_time={as_hhmm(settings.flatten_time)}" in script
    assert "abs(float(settings.relative_volume_threshold) - 2.0)" in script
    assert "abs(float(settings.stop_limit_buffer_pct) - 0.0005)" in script
    assert "abs(float(settings.entry_stop_price_buffer) - 0.02)" in script
    assert 'settings.market_data_feed.value == "iex"' in script
    assert "int(settings.daily_sma_period) == 20" in script
    assert "int(settings.breakout_lookback_bars) == 20" in script
    assert "int(settings.relative_volume_lookback_bars) == 20" in script
    assert "int(settings.entry_timeframe_minutes) == 15" in script
    assert "abs(float(settings.risk_per_trade_pct) - 0.01)" in script
    assert "abs(float(settings.max_position_pct) - 0.05)" in script
    assert "int(settings.max_open_positions) == 4" in script
    assert "abs(float(settings.max_portfolio_exposure_pct) - 0.30)" in script
    assert "abs(float(settings.daily_loss_limit_pct) - 0.01)" in script
    assert "int(settings.atr_period) == 20" in script
    assert "abs(float(settings.atr_stop_multiplier) - 1.0)" in script
    assert "abs(float(settings.trailing_stop_atr_multiplier) - 1.0)" in script
    assert "abs(float(settings.trailing_stop_profit_trigger_r) - 1.0)" in script
    assert "abs(float(settings.bull_flag_min_run_pct) - 0.02)" in script
    assert "abs(float(settings.bull_flag_consolidation_volume_ratio) - 0.6)" in script
    assert "abs(float(settings.bull_flag_consolidation_range_pct) - 0.5)" in script
    assert 'as_hhmm(settings.entry_window_start) == "10:00"' in script
    assert 'as_hhmm(settings.entry_window_end) == "15:30"' in script
    assert 'as_hhmm(settings.flatten_time) == "15:45"' in script
    assert "bull_flag_min_run_pct={settings.bull_flag_min_run_pct:g}" in script
    assert (
        "bull_flag_consolidation_volume_ratio="
        "{settings.bull_flag_consolidation_volume_ratio:g}"
    ) in script
    assert (
        "bull_flag_consolidation_range_pct="
        "{settings.bull_flag_consolidation_range_pct:g}"
    ) in script
    assert "not bool(settings.enable_vwap_entry_filter)" in script
    assert "bool(settings.enable_profit_trail)" in script
    assert "abs(float(settings.profit_trail_pct) - 0.90)" in script
    assert "bool(settings.enable_profit_target)" in script
    assert "abs(float(settings.profit_target_r) - 3.0)" in script
    assert "bool(settings.enable_breakeven_stop)" in script
    assert "abs(float(settings.breakeven_trigger_pct) - 0.005)" in script
    assert "abs(float(settings.breakeven_trail_pct) - 0.002)" in script
    assert "not bool(settings.enable_vix_filter)" in script
    assert "not bool(settings.enable_sector_filter)" in script
    assert "not bool(settings.enable_regime_filter)" in script
    assert "not bool(settings.enable_news_filter)" in script
    assert "not bool(settings.enable_spread_filter)" in script
    assert "not bool(settings.enable_options_trading)" in script
    assert "not bool(settings.option_chain_symbols)" in script
    assert "not bool(settings.extended_hours_enabled)" in script
    assert "not bool(settings.enable_trend_filter_exit)" in script
    assert "not bool(settings.enable_vwap_breakdown_exit)" in script
    assert "abs(float(settings.per_symbol_loss_limit_pct) - 0.0)" in script
    assert "abs(float(settings.min_position_notional) - 0.0)" in script
    assert "abs(float(settings.max_stop_pct) - 0.05)" in script
    assert "int(settings.viability_daily_bar_max_age_days) == 5" in script
    assert "int(settings.viability_min_hold_minutes) == 0" in script
    assert "settings.max_loss_per_trade_dollars is not None" in script
    assert "abs(float(settings.max_loss_per_trade_dollars) - 20.0)" in script
    assert "bool(settings.paper_proof_freeze)" in script
    assert "int(settings.intraday_consecutive_loss_gate) == 0" in script
    assert "abs(float(settings.replay_slippage_bps) - 2.0)" in script
    assert "profit_trail={str(settings.enable_profit_trail).lower()}" in script
    assert "profit_trail_pct={settings.profit_trail_pct:g}" in script
    assert "breakeven_stop={str(settings.enable_breakeven_stop).lower()}" in script
    assert "breakeven_trigger_pct={settings.breakeven_trigger_pct:g}" in script
    assert "breakeven_trail_pct={settings.breakeven_trail_pct:g}" in script
    assert "regime_filter={str(settings.enable_regime_filter).lower()}" in script
    assert "news_filter={str(settings.enable_news_filter).lower()}" in script
    assert "spread_filter={str(settings.enable_spread_filter).lower()}" in script
    assert "options_trading={str(settings.enable_options_trading).lower()}" in script
    assert "option_chain_symbols={','.join(settings.option_chain_symbols) if settings.option_chain_symbols else 'none'}" in script
    assert "profit_target={str(settings.enable_profit_target).lower()}" in script
    assert "profit_target_r={settings.profit_target_r:g}" in script
    assert "trend_filter_exit={str(settings.enable_trend_filter_exit).lower()}" in script
    assert "vwap_breakdown_exit={str(settings.enable_vwap_breakdown_exit).lower()}" in script
    assert "per_symbol_loss_limit_pct={settings.per_symbol_loss_limit_pct:g}" in script
    assert "min_position_notional={settings.min_position_notional:g}" in script
    assert "max_stop_pct={settings.max_stop_pct:g}" in script
    assert (
        "viability_daily_bar_max_age_days="
        "{settings.viability_daily_bar_max_age_days}"
    ) in script
    assert "viability_min_hold_minutes={settings.viability_min_hold_minutes}" in script
    assert "max_loss_per_trade_dollars=" in script
    assert "replay_slippage_bps={settings.replay_slippage_bps:g}" in script
    assert "paper proof scoring:" in script
    assert "scoreable_closed_trades={trade_count}" in script
    assert "unpaired_filled_exits={unpaired_filled_exit_count}" in script
    assert "unpaired_symbols={unpaired_filled_exit_symbols or 'none'}" in script
    assert "paper proof trade quality:" in script
    assert "wins={wins}" in script
    assert "losses={losses}" in script
    assert "flats={flats}" in script
    assert "win_rate={win_rate_text}" in script
    assert "avg_pnl={avg_trade_pnl_text}" in script
    assert "best={best_trade_text}" in script
    assert "worst={worst_trade_text}" in script
    assert "recent={recent_trade_summary}" in script
    assert "paper proof local exposure:" in script
    assert "positions={local_open_positions}" in script
    assert "active_orders={local_active_orders}" in script
    assert "position_symbols={local_open_position_symbols or 'none'}" in script
    assert "active_order_symbols={local_active_order_symbols or 'none'}" in script
    assert "paper proof option exposure:" in script
    assert "net_open={local_open_option_positions}" in script
    assert "active_orders={local_active_option_orders}" in script
    assert "net_open_symbols={local_open_option_symbols or 'none'}" in script
    assert "active_order_symbols={local_active_option_order_symbols or 'none'}" in script
    assert "local_open_option_positions" in script
    assert "local_active_option_orders" in script
    assert "blockers.append(\"local_open_option_positions\")" in script
    assert "blockers.append(\"local_active_option_orders\")" in script
    assert "load_broker_exposure" in script
    assert "broker.list_open_orders()" in script
    assert "broker.list_positions()" in script
    assert "broker.get_account()" in script
    assert "paper proof broker exposure:" in script
    assert "open_orders={broker_open_orders}" in script
    assert "open_positions={broker_open_positions}" in script
    assert "open_order_symbols={broker_open_order_symbols or 'none'}" in script
    assert "open_position_symbols={broker_open_position_symbols or 'none'}" in script
    assert "paper proof broker account:" in script
    assert "status={broker_account_status}" in script
    assert "equity={broker_equity:.2f}" in script
    assert "buying_power={broker_buying_power:.2f}" in script
    assert "minimum_required={broker_minimum_buying_power:.2f}" in script
    assert "trading_blocked={str(broker_trading_blocked).lower()}" in script
    assert "latest_market_date" not in script


def test_paper_decision_dry_run_is_read_only_operator_smoke() -> None:
    script = Path("scripts/paper_decision_dry_run.sh").read_text()

    assert "capture_env_overrides" in script
    assert "restore_env_overrides" in script
    assert script.index('source "$ENV_FILE"') < script.index("\nrestore_env_overrides\n")
    assert 'PAPER_DECISION_DRY_RUN_STRATEGY="${PAPER_DECISION_DRY_RUN_STRATEGY:-bull_flag}"' in script
    assert 'PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED="${PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-true}"' in script
    assert 'PAPER_DECISION_DRY_RUN_MIN_RECORDS="${PAPER_DECISION_DRY_RUN_MIN_RECORDS:-900}"' in script
    assert 'PAPER_DECISION_DRY_RUN_SAMPLE_TIMES="${PAPER_DECISION_DRY_RUN_SAMPLE_TIMES:-10:30,11:30,12:30,13:30,14:30,15:30}"' in script
    assert "PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED must be true or false" in script
    assert "PAPER_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" in script
    assert "PAPER_DECISION_DRY_RUN_LOOKBACK_DAYS must be a positive integer" in script
    assert "PAPER_DECISION_DRY_RUN_SAMPLE_TIMES must be comma-separated HH:MM values" in script
    assert "PAPER_DECISION_DRY_RUN_EQUITY must be a number" in script
    assert "connect_postgres(settings.database_url)" in script
    assert "WatchlistStore(conn)" in script
    assert "StrategyFlagStore(conn)" in script
    assert "list_enabled(settings.trading_mode.value)" in script
    assert "list_ignored(settings.trading_mode.value)" in script
    assert "active_symbols = tuple(symbol for symbol in enabled_symbols if symbol not in ignored_symbols)" in script
    assert "AlpacaExecutionAdapter.from_settings(settings)" in script
    assert "AlpacaMarketDataAdapter.from_settings(settings)" in script
    assert "get_fractionable_symbols(active_symbols)" in script
    assert "replace(settings, fractionable_symbols=fractionable_symbols)" in script
    assert "from collections import Counter" in script
    assert "def _summary_counts" in script
    assert "get_stock_bars(" in script
    assert "get_daily_bars(" in script
    assert "_resolve_as_ofs" in script
    assert "for as_of in sorted(as_ofs)" in script
    assert "evaluate_cycle(" in script
    assert "signal_evaluator=STRATEGY_REGISTRY[strategy_name]" in script
    assert "open_positions=()" in script
    assert "working_order_symbols=set()" in script
    assert "traded_symbols_today=set()" in script
    assert "session_type=SessionType.REGULAR" in script
    assert "paper decision dry run ok:" in script
    assert "accepted=0 require_accepted=true" in script
    assert "entry_intents=0 require_accepted=true" in script
    assert "decision_records={len(records)}" in script
    assert "accepted={len(accepted)}" in script
    assert "sample_times={sample_times_text}" in script
    assert "max_entry_intents=" in script
    assert "reject_stages={reject_stages}" in script
    assert "reject_reasons={reject_reasons}" in script
    assert "submit_order" not in script
    assert "bulk_insert" not in script
    assert ".save(" not in script


def test_paper_proof_checks_count_nonterminal_order_statuses_as_active() -> None:
    scripts = [
        Path("scripts/paper_readiness_check.sh").read_text(),
        Path("scripts/paper_activity_check.sh").read_text(),
        Path("scripts/paper_proof_status.sh").read_text(),
    ]

    for script in scripts:
        for status in (
            "pending_submit",
            "submitting",
            "pending_new",
            "new",
            "accepted",
            "accepted_for_bidding",
            "submitted",
            "partially_filled",
            "held",
            "pending_replace",
            "pending_cancel",
            "stopped",
            "suspended",
            "done_for_day",
        ):
            assert f"'{status}'" in script


def test_runtime_image_health_check_compares_deployed_package_to_workspace() -> None:
    script = Path("scripts/runtime_image_health_check.sh").read_text()

    assert "RUNTIME_IMAGE_HEALTH_SERVICE" in script
    assert "RUNTIME_IMAGE_HEALTH_SERVICES" in script
    assert "supervisor" in script
    assert "RUNTIME_IMAGE_HEALTH_FILES" in script
    assert "runtime/supervisor.py" in script
    assert "nightly/cli.py" in script
    assert "core/engine.py" in script
    assert "storage/models.py" in script
    assert "strategy/bull_flag.py" in script
    assert "strategy/__init__.py" in script
    assert "strategy/breakout.py" in script
    assert "strategy/session.py" in script
    assert "storage/repositories.py" in script
    assert "web/templates/dashboard.html" in script
    assert 'local_path="src/alpaca_bot/$rel"' in script
    assert "import alpaca_bot" in script
    assert "Path(alpaca_bot.__file__).resolve().parent" in script
    assert "hashlib.sha256(path.read_bytes()).hexdigest()" in script
    assert 'diff -u "$host_hashes" "$image_hash"' in script
    assert "runtime image health ok:" in script
    assert "services=${checked_services[*]}" in script
    assert "deployed package differs from workspace" in script


def test_post_close_checks_fail_on_open_positions() -> None:
    session_guard = Path("scripts/session_guard.sh").read_text()
    profit_probe = Path("scripts/paper_profit_probe.sh").read_text()

    assert "--fail-on-open-positions" in session_guard
    assert "--fail-on-open-positions" in profit_probe
    assert 'if [[ ! -f "$ENV_FILE" ]]' in session_guard
    assert "missing env file: $ENV_FILE" in session_guard
    assert "capture_env_overrides" in session_guard
    assert "restore_env_overrides" in session_guard
    assert session_guard.index('source "$ENV_FILE"') < session_guard.index("\nrestore_env_overrides\n")
    assert "SESSION_GUARD_DATE" in session_guard
    assert "SESSION_GUARD_FAIL_ON_DIAGNOSTICS \\" in session_guard
    assert 'SESSION_GUARD_FAIL_ON_DIAGNOSTICS="${SESSION_GUARD_FAIL_ON_DIAGNOSTICS:-true}"' in session_guard
    assert 'SESSION_GUARD_START_DATE="${SESSION_GUARD_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-30}}"' in session_guard
    assert session_guard.index('source "$ENV_FILE"') < session_guard.index(
        'SESSION_GUARD_START_DATE="${SESSION_GUARD_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-30}}"'
    )
    assert session_guard.index('source "$ENV_FILE"') < session_guard.index(
        'SESSION_GUARD_STRATEGY="${SESSION_GUARD_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"'
    )
    assert "SESSION_GUARD_FAIL_ON_DIAGNOSTICS must be true or false" in session_guard
    assert "SESSION_GUARD_STRATEGY contains unsupported characters" in session_guard
    assert "load_latest_completed_session_date" in session_guard
    assert "AlpacaExecutionAdapter.from_settings" in session_guard
    assert "get_market_calendar" in session_guard
    assert "close_at + timedelta(minutes=30)" in session_guard
    assert "session guard warning: market calendar lookup failed; using weekday fallback" in session_guard
    assert "SESSION_GUARD_DATE must use YYYY-MM-DD" in session_guard
    assert "SESSION_GUARD_START_DATE must use YYYY-MM-DD" in session_guard
    assert "SESSION_GUARD_MIN_TRADES must be a non-negative integer" in session_guard
    assert "SESSION_GUARD_FAIL_BELOW_PNL must be a number" in session_guard
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
    assert "capture_env_overrides" in profit_probe
    assert "restore_env_overrides" in profit_probe
    assert profit_probe.index('source "$ENV_FILE"') < profit_probe.index("\nrestore_env_overrides\n")
    assert "PROFIT_PROBE_DATE" in profit_probe
    assert "PROFIT_PROBE_FAIL_ON_DIAGNOSTICS \\" in profit_probe
    assert 'PROFIT_PROBE_START_DATE="${PROFIT_PROBE_START_DATE:-2026-06-30}"' in profit_probe
    assert profit_probe.index('source "$ENV_FILE"') < profit_probe.index(
        'PROFIT_PROBE_START_DATE="${PROFIT_PROBE_START_DATE:-2026-06-30}"'
    )
    assert profit_probe.index('source "$ENV_FILE"') < profit_probe.index(
        'PROFIT_PROBE_STRATEGY="${PROFIT_PROBE_STRATEGY:-bull_flag}"'
    )
    assert 'PROFIT_PROBE_FAIL_ON_DIAGNOSTICS="${PROFIT_PROBE_FAIL_ON_DIAGNOSTICS:-true}"' in profit_probe
    assert "PROFIT_PROBE_FAIL_ON_DIAGNOSTICS must be true or false" in profit_probe
    assert "PROFIT_PROBE_STRATEGY contains unsupported characters" in profit_probe
    assert "PROFIT_PROBE_START_DATE must use YYYY-MM-DD" in profit_probe
    assert "PROFIT_PROBE_MIN_TRADES must be a positive integer" in profit_probe
    assert "PROFIT_PROBE_MIN_PNL must be a number" in profit_probe
    assert "PROFIT_PROBE_DATE must use YYYY-MM-DD" in profit_probe
    assert "session_eval_args=(" in profit_probe
    assert "session_eval_args+=(--fail-on-diagnostics)" in profit_probe
    assert "paper profit probe pending: latest completed session" in profit_probe
    assert "scheduled check context: session_date=$PROFIT_PROBE_DATE proof_start=$PROFIT_PROBE_START_DATE" in profit_probe
    assert "min_trades=$SESSION_GUARD_MIN_TRADES" in session_guard
    assert "min_pnl=$SESSION_GUARD_FAIL_BELOW_PNL" in session_guard
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
    assert 'if [[ "$rc" -eq 42 ]]; then' in session_guard
    assert "session guard pending: same-day pnl below" in session_guard
    assert '"$rc" -eq 44 || "$rc" -eq 46' in session_guard
    assert "open positions remain after close" in session_guard
    assert "session guard failed ${SESSION_GUARD_DATE}: operational diagnostics contain proof-blocking issues" in session_guard
    assert "session guard failed: could not apply close-only guard" in session_guard
    assert "session guard warning: funnel diagnostic failed" in session_guard
    assert '--strategy "$SESSION_GUARD_STRATEGY"' in session_guard
    assert "exit 45" in session_guard
    assert 'if [[ "$rc" -eq 42 ]]; then' in profit_probe
    assert "paper profit probe pending: cumulative pnl below" in profit_probe
    assert '"$rc" -eq 44 || "$rc" -eq 46' in profit_probe
    assert '"$rc" -eq 42 || "$rc" -eq 43 || "$rc" -eq 46' in profit_probe
    assert "paper proof failed" in profit_probe
    assert "paper proof incomplete ${PROFIT_PROBE_START_DATE}..${PROFIT_PROBE_DATE}: fewer than ${PROFIT_PROBE_MIN_TRADES} closed trades" not in profit_probe
    assert "paper proof failed ${PROFIT_PROBE_START_DATE}..${PROFIT_PROBE_DATE}: operational diagnostics contain proof-blocking issues" in profit_probe
    assert "close-only" in profit_probe
    assert "alpaca-bot-funnel-report" in profit_probe
    assert '--strategy "$PROFIT_PROBE_STRATEGY"' in profit_probe
    assert "paper profit probe warning: funnel diagnostic failed" in profit_probe
    assert "paper profit probe failed: could not apply close-only guard" in profit_probe
    assert "exit 45" in profit_probe


def test_paper_profit_probe_validates_thresholds_before_docker(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026-06-29",
                "PROFIT_PROBE_STRATEGY=bull_flag",
                "PROFIT_PROBE_MIN_TRADES=0",
                "PROFIT_PROBE_MIN_PNL=0.01",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\nprintf 'docker should not run\\n'\nexit 99\n"
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_profit_probe.sh", str(env_file)],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "PROFIT_PROBE_MIN_TRADES must be a positive integer" in result.stderr
    assert "docker should not run" not in result.stdout


def test_session_guard_validates_start_date_before_docker(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "SESSION_GUARD_START_DATE=2026/06/29",
                "SESSION_GUARD_STRATEGY=bull_flag",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/usr/bin/env bash\nprintf 'docker should not run\\n'\nexit 99\n")
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/session_guard.sh", str(env_file)],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "SESSION_GUARD_START_DATE must use YYYY-MM-DD" in result.stderr
    assert "docker should not run" not in result.stdout


def test_paper_proof_status_uses_profit_probe_start_after_sourcing_env(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PROFIT_PROBE_START_DATE=2026/07/06",
                "PROFIT_PROBE_STRATEGY=bull_flag",
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/usr/bin/env bash\nprintf 'docker should not run\\n'\nexit 99\n")
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["scripts/paper_proof_status.sh", str(env_file)],
        cwd=Path.cwd(),
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "PROOF_STATUS_START_DATE must use YYYY-MM-DD" in result.stderr
    assert "docker should not run" not in result.stdout
