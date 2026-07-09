from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "promote_validated_strategy.sh"


def _write_env(
    path: Path,
    approved: str = "bull_flag",
    *,
    extra_lines: list[str] | None = None,
) -> None:
    path.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PAPER_PROOF_FREEZE=true",
                f"PAPER_APPROVED_STRATEGIES={approved}",
                *(extra_lines or []),
                "",
            ]
        )
    )


def _candidate_row(row_overrides: dict[str, object] | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "candidate": "ema_pullback",
        "candidate_scale": "0.50",
        "status": "passed",
        "verdict": "positive-edge",
        "candidate_verdict": "positive-edge",
        "candidate_contribution_status": "positive_pnl",
        "candidate_trades": 291,
        "candidate_total_pnl": 177.32,
        "candidate_ci_low": 0.0007,
        "candidate_ci_high": 1.3088,
        "candidate_p_mean_le_zero": 0.0245,
    }
    if row_overrides:
        row.update(row_overrides)
    return row


def _write_summary(
    root: Path,
    row_overrides: dict[str, object] | None = None,
    *,
    rows: list[dict[str, object]] | None = None,
) -> None:
    validation_dir = root / "latest_validation"
    validation_dir.mkdir(parents=True)
    summary_rows = rows if rows is not None else [_candidate_row(row_overrides)]
    (validation_dir / "summary.json").write_text(
        json.dumps(
            {
                "positive_edge_validation_rows": len(summary_rows),
                "promotion_approved": False,
                "rows": summary_rows,
            }
        )
    )


def _summary_sha256(root: Path) -> str:
    summary_path = root / "latest_validation" / "summary.json"
    return hashlib.sha256(summary_path.read_bytes()).hexdigest()


def _confirmation(root: Path, strategy: str = "ema_pullback") -> str:
    return f"approve-{strategy}-paper-promotion-sha256-{_summary_sha256(root)}"


def _make_fake_deploy(tmp_path: Path, *, exit_code: int = 0) -> Path:
    deploy = tmp_path / "deploy.sh"
    deploy.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$1\" >> {tmp_path / 'deploy_calls'}\n"
        f"exit {exit_code}\n"
    )
    deploy.chmod(deploy.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return deploy


def _make_fake_docker(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    broker_not_flat = tmp_path / "broker_not_flat"
    mutate_summary_after_broker = tmp_path / "mutate_summary_after_broker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmp_path / 'docker_calls'}\n"
        "case \"$*\" in\n"
        "  *'--entrypoint python admin'*)\n"
        f"    if [[ -f '{broker_not_flat}' ]]; then\n"
        "      printf 'promote validated strategy failed: broker has 1 open stock positions: ARQT\\n' >&2\n"
        "      exit 1\n"
        "    fi\n"
        f"    if [[ -f '{mutate_summary_after_broker}' ]]; then\n"
        f"      summary_path=\"$(cat '{mutate_summary_after_broker}')\"\n"
        "      printf '%s\\n' "
        "'{\"positive_edge_validation_rows\": 0, "
        "\"promotion_approved\": false, \"rows\": []}' > \"$summary_path\"\n"
        "    fi\n"
        "    printf 'promote validated strategy broker exposure ok: open_orders=0 open_positions=0\\n'\n"
        "    exit 0\n"
        "    ;;\n"
        "  *'enable-strategy ema_pullback --mode paper --strategy-version v1-breakout'*)\n"
        "    printf 'strategy=ema_pullback mode=paper version=v1-breakout enabled\\n'\n"
        "    exit 0\n"
        "    ;;\n"
        "  *'disable-strategy ema_pullback --mode paper --strategy-version v1-breakout'*)\n"
        "    printf 'strategy=ema_pullback mode=paper version=v1-breakout disabled\\n'\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "printf 'unexpected docker call: %s\\n' \"$*\" >&2\n"
        "exit 99\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake_bin


def _run_promote(
    *,
    env_file: Path,
    evidence_root: Path,
    deploy_script: Path,
    tmp_path: Path,
    confirmation: str | None = None,
    dry_run: bool | None = None,
    approval_only: bool | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{_make_fake_docker(tmp_path)}:/usr/bin:/bin",
    }
    if confirmation is not None:
        env["PROMOTE_VALIDATED_STRATEGY_CONFIRM"] = confirmation
    if dry_run is not None:
        env["PROMOTE_VALIDATED_STRATEGY_DRY_RUN"] = "true" if dry_run else "false"
    if approval_only is not None:
        env["PROMOTE_VALIDATED_STRATEGY_APPROVAL_ONLY"] = (
            "true" if approval_only else "false"
        )
    return subprocess.run(
        [
            str(SCRIPT),
            str(env_file),
            "ema_pullback",
            str(evidence_root),
            str(deploy_script),
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )


def test_promote_validated_strategy_requires_explicit_confirmation(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(env_file)
    _write_summary(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path)

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        dry_run=False,
    )

    summary_path = evidence_root / "latest_validation" / "summary.json"
    summary_sha256 = _summary_sha256(evidence_root)
    assert result.returncode == 2
    assert f"PROMOTE_VALIDATED_STRATEGY_CONFIRM={_confirmation(evidence_root)}" in result.stderr
    assert f"validation_summary={summary_path.resolve()}" in result.stderr
    assert f"validation_summary_sha256={summary_sha256}" in result.stderr
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    assert not (tmp_path / "docker_calls").exists()
    assert not (tmp_path / "deploy_calls").exists()


def test_promote_validated_strategy_dry_run_reports_gates_without_mutation(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(env_file)
    _write_summary(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path)

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert 'DRY_RUN="${PROMOTE_VALIDATED_STRATEGY_DRY_RUN:-true}"' in SCRIPT.read_text()
    stdout = result.stdout
    assert "dry_run=true" in stdout
    assert "strategy=ema_pullback" in stdout
    assert "confirmation_status=missing" in stdout
    assert f"required_confirmation={_confirmation(evidence_root)}" in stdout
    assert "validation_current_status=ok" in stdout
    assert "write_access_status=ok" in stdout
    assert "env_file_writable=true" in stdout
    assert "env_dir_writable=true" in stdout
    assert "broker_flat_status=ok" in stdout
    assert "dry_run_promotion_command=env PROMOTE_VALIDATED_STRATEGY_CONFIRM=" in stdout
    assert "PROMOTE_VALIDATED_STRATEGY_DRY_RUN=false" in stdout
    assert (
        "dry_run_approval_marker_command=env "
        "PROMOTE_VALIDATED_STRATEGY_CONFIRM="
    ) in stdout
    assert "PROMOTE_VALIDATED_STRATEGY_APPROVAL_ONLY=true" in stdout
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    assert not (evidence_root / "promotion_approval.json").exists()
    docker_calls = (tmp_path / "docker_calls").read_text()
    assert "--entrypoint python admin" in docker_calls
    assert "enable-strategy" not in docker_calls
    assert "disable-strategy" not in docker_calls
    assert not (tmp_path / "deploy_calls").exists()


def test_promote_validated_strategy_approval_only_writes_marker_without_promotion(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(env_file)
    _write_summary(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path)

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        confirmation=_confirmation(evidence_root),
        dry_run=False,
        approval_only=True,
    )

    assert result.returncode == 0, result.stderr
    assert "wrote approval marker only" in result.stdout
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    approval_marker = json.loads((evidence_root / "promotion_approval.json").read_text())
    summary_path = evidence_root / "latest_validation" / "summary.json"
    assert approval_marker["strategy"] == "ema_pullback"
    assert approval_marker["confirmation"] == _confirmation(evidence_root)
    assert approval_marker["validation_summary"] == str(summary_path.resolve())
    docker_calls = (tmp_path / "docker_calls").read_text()
    assert "--entrypoint python admin" in docker_calls
    assert "enable-strategy" not in docker_calls
    assert "disable-strategy" not in docker_calls
    assert not (tmp_path / "deploy_calls").exists()


def test_promote_validated_strategy_requires_flat_broker_before_mutation(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(env_file)
    _write_summary(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path)
    (tmp_path / "broker_not_flat").write_text("true\n")

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        confirmation=_confirmation(evidence_root),
        dry_run=False,
    )

    assert result.returncode == 1
    assert "refusing promotion because paper broker is not flat" in result.stderr
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    docker_calls = (tmp_path / "docker_calls").read_text()
    assert "--entrypoint python admin" in docker_calls
    assert "enable-strategy" not in docker_calls
    assert not (tmp_path / "deploy_calls").exists()


def test_promote_validated_strategy_rechecks_evidence_after_broker_check(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(env_file)
    _write_summary(evidence_root)
    confirmation = _confirmation(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path)
    summary_path = evidence_root / "latest_validation" / "summary.json"
    (tmp_path / "mutate_summary_after_broker").write_text(f"{summary_path}\n")

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        confirmation=confirmation,
        dry_run=False,
    )

    assert result.returncode == 1
    assert "validation summary changed after broker flat check" in result.stderr
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    docker_calls = (tmp_path / "docker_calls").read_text()
    assert "--entrypoint python admin" in docker_calls
    assert "enable-strategy" not in docker_calls
    assert not (evidence_root / "promotion_approval.json").exists()
    assert not (tmp_path / "deploy_calls").exists()


def test_promote_validated_strategy_requires_env_write_access_before_broker_check(
    tmp_path: Path,
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can write through read-only mode bits")

    env_dir = tmp_path / "readonly_env"
    env_dir.mkdir()
    env_file = env_dir / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(env_file)
    _write_summary(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path)
    env_file.chmod(0o440)
    env_dir.chmod(0o550)

    try:
        result = _run_promote(
            env_file=env_file,
            evidence_root=evidence_root,
            deploy_script=deploy_script,
            tmp_path=tmp_path,
            confirmation=_confirmation(evidence_root),
            dry_run=False,
        )
    finally:
        env_dir.chmod(0o750)
        env_file.chmod(0o640)

    assert result.returncode == 1
    assert "env file is not writable" in result.stderr
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    assert not (tmp_path / "docker_calls").exists()
    assert not (tmp_path / "deploy_calls").exists()


def test_promote_validated_strategy_rejects_legacy_generic_confirmation(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(env_file)
    _write_summary(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path)

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        confirmation="approve-ema_pullback-paper-promotion",
        dry_run=False,
    )

    assert result.returncode == 2
    assert f"PROMOTE_VALIDATED_STRATEGY_CONFIRM={_confirmation(evidence_root)}" in result.stderr
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    assert not (tmp_path / "docker_calls").exists()
    assert not (tmp_path / "deploy_calls").exists()


def test_promote_validated_strategy_rejects_weak_candidate_evidence(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(env_file)
    _write_summary(evidence_root, {"candidate_ci_low": -0.01})
    deploy_script = _make_fake_deploy(tmp_path)

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        confirmation=_confirmation(evidence_root),
        dry_run=False,
    )

    assert result.returncode == 1
    assert "candidate_ci_low" in result.stderr
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    assert not (tmp_path / "docker_calls").exists()
    assert not (tmp_path / "deploy_calls").exists()


def test_promote_validated_strategy_updates_allowlist_enables_and_deploys(tmp_path: Path) -> None:
    script_text = SCRIPT.read_text()
    assert 'chmod --reference="$ENV_FILE" "$tmp"' in script_text
    assert 'chown --reference="$ENV_FILE" "$tmp"' in script_text

    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    scoped_strategy_lines = [
        "PROFIT_PROBE_STRATEGIES=bull_flag",
        "PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES=bull_flag",
        "PAPER_READINESS_EXPECT_ENABLED_STRATEGIES=bull_flag",
        "PAPER_ACTIVITY_STRATEGIES=bull_flag",
        "SESSION_GUARD_STRATEGIES=bull_flag",
        "PROOF_STATUS_APPROVED_STRATEGIES=bull_flag",
        "DEPLOY_EXPECT_ENABLED_STRATEGIES=bull_flag",
        "DEPLOY_DECISION_DRY_RUN_STRATEGIES=bull_flag",
    ]
    _write_env(env_file, extra_lines=scoped_strategy_lines)
    _write_summary(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path)

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        confirmation=_confirmation(evidence_root),
        dry_run=False,
    )

    assert result.returncode == 0, result.stderr
    env_text = env_file.read_text()
    assert "PAPER_APPROVED_STRATEGIES=bull_flag,ema_pullback" in env_text
    for key in [
        "PROFIT_PROBE_STRATEGIES",
        "PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES",
        "PAPER_READINESS_EXPECT_ENABLED_STRATEGIES",
        "PAPER_ACTIVITY_STRATEGIES",
        "SESSION_GUARD_STRATEGIES",
        "PROOF_STATUS_APPROVED_STRATEGIES",
        "DEPLOY_EXPECT_ENABLED_STRATEGIES",
        "DEPLOY_DECISION_DRY_RUN_STRATEGIES",
    ]:
        assert f"{key}=bull_flag,ema_pullback" in env_text
    assert "--entrypoint python admin" in (tmp_path / "docker_calls").read_text()
    assert "enable-strategy ema_pullback --mode paper --strategy-version v1-breakout" in (
        tmp_path / "docker_calls"
    ).read_text()
    assert (tmp_path / "deploy_calls").read_text().strip() == str(env_file)
    approval_marker = json.loads((evidence_root / "promotion_approval.json").read_text())
    summary_path = evidence_root / "latest_validation" / "summary.json"
    summary_sha256 = _summary_sha256(evidence_root)
    assert approval_marker["schema_version"] == 2
    approved_at = datetime.fromisoformat(approval_marker["approved_at"])
    assert approved_at.tzinfo is not None
    assert approved_at.astimezone(timezone.utc) <= datetime.now(timezone.utc)
    assert approval_marker["strategy"] == "ema_pullback"
    assert approval_marker["confirmation"] == _confirmation(evidence_root)
    assert approval_marker["strategy_version"] == "v1-breakout"
    assert approval_marker["env_file"] == str(env_file)
    assert approval_marker["validation_summary"] == str(summary_path.resolve())
    assert approval_marker["validation_summary_sha256"] == summary_sha256
    assert approval_marker["candidate_trades"] == 291
    assert approval_marker["candidate_ci_low"] == 0.0007


def test_promote_validated_strategy_selects_best_passing_row(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(env_file)
    _write_summary(
        evidence_root,
        rows=[
            _candidate_row(
                {
                    "candidate_scale": "0.25",
                    "candidate_trades": 80,
                    "candidate_total_pnl": 40.0,
                    "candidate_ci_low": 0.0002,
                    "candidate_p_mean_le_zero": 0.0300,
                }
            ),
            _candidate_row(
                {
                    "candidate_scale": "0.50",
                    "candidate_trades": 291,
                    "candidate_total_pnl": 177.32,
                    "candidate_ci_low": 0.0007,
                    "candidate_p_mean_le_zero": 0.0245,
                }
            ),
        ],
    )
    deploy_script = _make_fake_deploy(tmp_path)

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        confirmation=_confirmation(evidence_root),
        dry_run=False,
    )

    assert result.returncode == 0, result.stderr
    approval_marker = json.loads((evidence_root / "promotion_approval.json").read_text())
    assert approval_marker["candidate_scale"] == "0.50"
    assert approval_marker["candidate_trades"] == 291
    assert approval_marker["candidate_total_pnl"] == 177.32
    assert approval_marker["candidate_ci_low"] == 0.0007


def test_promote_validated_strategy_rolls_back_when_deploy_fails(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    evidence_root = tmp_path / "evidence"
    _write_env(
        env_file,
        extra_lines=[
            "PROFIT_PROBE_STRATEGIES=bull_flag",
            "PAPER_ACTIVITY_STRATEGIES=bull_flag",
        ],
    )
    _write_summary(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path, exit_code=42)

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        confirmation=_confirmation(evidence_root),
        dry_run=False,
    )

    assert result.returncode == 1
    assert "deploy failed; rolling back env allowlist and strategy flag" in result.stderr
    env_text = env_file.read_text()
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_text
    assert "PROFIT_PROBE_STRATEGIES=bull_flag\n" in env_text
    assert "PAPER_ACTIVITY_STRATEGIES=bull_flag\n" in env_text
    assert not (evidence_root / "promotion_approval.json").exists()
    docker_calls = (tmp_path / "docker_calls").read_text()
    assert "enable-strategy ema_pullback --mode paper --strategy-version v1-breakout" in docker_calls
    assert "disable-strategy ema_pullback --mode paper --strategy-version v1-breakout" in docker_calls
