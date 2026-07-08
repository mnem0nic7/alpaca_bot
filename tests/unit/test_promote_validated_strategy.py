from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "promote_validated_strategy.sh"


def _write_env(path: Path, approved: str = "bull_flag") -> None:
    path.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "STRATEGY_VERSION=v1-breakout",
                "PAPER_PROOF_FREEZE=true",
                f"PAPER_APPROVED_STRATEGIES={approved}",
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
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmp_path / 'docker_calls'}\n"
        "case \"$*\" in\n"
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
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{_make_fake_docker(tmp_path)}:/usr/bin:/bin",
    }
    if confirmation is not None:
        env["PROMOTE_VALIDATED_STRATEGY_CONFIRM"] = confirmation
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
    )

    summary_path = evidence_root / "latest_validation" / "summary.json"
    summary_sha256 = hashlib.sha256(summary_path.read_bytes()).hexdigest()
    assert result.returncode == 2
    assert "PROMOTE_VALIDATED_STRATEGY_CONFIRM=approve-ema_pullback-paper-promotion" in result.stderr
    assert f"validation_summary={summary_path.resolve()}" in result.stderr
    assert f"validation_summary_sha256={summary_sha256}" in result.stderr
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
        confirmation="approve-ema_pullback-paper-promotion",
    )

    assert result.returncode == 1
    assert "candidate_ci_low" in result.stderr
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    assert not (tmp_path / "docker_calls").exists()
    assert not (tmp_path / "deploy_calls").exists()


def test_promote_validated_strategy_updates_allowlist_enables_and_deploys(tmp_path: Path) -> None:
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
    )

    assert result.returncode == 0, result.stderr
    assert "PAPER_APPROVED_STRATEGIES=bull_flag,ema_pullback" in env_file.read_text()
    assert "enable-strategy ema_pullback --mode paper --strategy-version v1-breakout" in (
        tmp_path / "docker_calls"
    ).read_text()
    assert (tmp_path / "deploy_calls").read_text().strip() == str(env_file)
    approval_marker = json.loads((evidence_root / "promotion_approval.json").read_text())
    summary_path = evidence_root / "latest_validation" / "summary.json"
    summary_sha256 = hashlib.sha256(summary_path.read_bytes()).hexdigest()
    assert approval_marker["schema_version"] == 2
    assert approval_marker["strategy"] == "ema_pullback"
    assert approval_marker["confirmation"] == "approve-ema_pullback-paper-promotion"
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
        confirmation="approve-ema_pullback-paper-promotion",
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
    _write_env(env_file)
    _write_summary(evidence_root)
    deploy_script = _make_fake_deploy(tmp_path, exit_code=42)

    result = _run_promote(
        env_file=env_file,
        evidence_root=evidence_root,
        deploy_script=deploy_script,
        tmp_path=tmp_path,
        confirmation="approve-ema_pullback-paper-promotion",
    )

    assert result.returncode == 1
    assert "deploy failed; rolling back env allowlist and strategy flag" in result.stderr
    assert "PAPER_APPROVED_STRATEGIES=bull_flag\n" in env_file.read_text()
    assert not (evidence_root / "promotion_approval.json").exists()
    docker_calls = (tmp_path / "docker_calls").read_text()
    assert "enable-strategy ema_pullback --mode paper --strategy-version v1-breakout" in docker_calls
    assert "disable-strategy ema_pullback --mode paper --strategy-version v1-breakout" in docker_calls
