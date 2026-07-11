from __future__ import annotations

import json
from pathlib import Path

from alpaca_bot.replay.proof_horizon_selection import publish_selection


def _write_candidate(
    root: Path,
    *,
    candidate: str,
    scale: str,
    total_pnl: float,
    eventual_pass_rate: float,
    starts_eventually_passed: int,
) -> tuple[Path, Path, Path]:
    candidate_dir = root / f"{candidate}_{scale.replace('.', '_')}"
    candidate_dir.mkdir(parents=True)
    report_path = candidate_dir / "summary.md"
    report_path.write_text(f"# {candidate} report\n")
    summary_path = candidate_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "strategy": f"bull_flag+{candidate}",
                "confidence_scales": {candidate: float(scale)},
                "trades": 100,
                "total_pnl": total_pnl,
                "min_pnl": 0.01,
                "starts_eventually_passed": starts_eventually_passed,
                "historical_starts_checked": 100,
                "eventual_pass_rate": eventual_pass_rate,
            }
        )
    )
    stderr_path = candidate_dir / "stderr.txt"
    stderr_path.write_text("")
    return report_path, summary_path, stderr_path


def _write_results(
    path: Path,
    rows: list[tuple[str, str, Path, Path, Path]],
) -> None:
    path.write_text(
        "".join(
            "\t".join(
                [candidate, scale, "passed", str(report), str(summary), str(stderr)]
            )
            + "\n"
            for candidate, scale, report, summary, stderr in rows
        )
    )


def test_publish_selection_uses_first_candidate_that_passes_basket_gate(
    tmp_path: Path,
) -> None:
    ema_paths = _write_candidate(
        tmp_path,
        candidate="ema_pullback",
        scale="0.10",
        total_pnl=-30.03,
        eventual_pass_rate=0.08,
        starts_eventually_passed=8,
    )
    orb_paths = _write_candidate(
        tmp_path,
        candidate="orb",
        scale="0.25",
        total_pnl=125.0,
        eventual_pass_rate=0.72,
        starts_eventually_passed=72,
    )
    results_path = tmp_path / "results.tsv"
    _write_results(
        results_path,
        [
            ("ema_pullback", "0.10", *ema_paths),
            ("orb", "0.25", *orb_paths),
        ],
    )
    output_dir = tmp_path / "published"
    fractionability_snapshot = {
        "snapshot_file": "/evidence/fractionable_symbols.txt",
        "snapshot_sha256": "a" * 64,
        "universe_sha256": "b" * 64,
        "fractionable_symbol_count": 974,
        "non_fractionable_symbol_count": 2,
    }

    selection = publish_selection(
        results_path=results_path,
        output_dir=output_dir,
        min_eventual_pass_rate=0.50,
        default_min_pnl=0.01,
        fractionability_snapshot=fractionability_snapshot,
    )

    assert selection["selected_candidate"] == "orb"
    assert selection["selected_candidate_scale"] == "0.25"
    assert selection["selection_reason"] == "first_passing"
    assert selection["candidate_count"] == 2
    assert selection["passing_candidate_count"] == 1
    assert selection["rows"][0]["detail"] == "total_pnl_below_gate"
    assert selection["rows"][1]["status"] == "ok"
    published = json.loads((output_dir / "summary.json").read_text())
    assert published["strategy"] == "bull_flag+orb"
    assert published["candidate_selection"] == selection
    assert published["fractionability_snapshot"] == fractionability_snapshot
    assert json.loads((output_dir / "candidates.json").read_text()) == selection
    markdown = (output_dir / "summary.md").read_text()
    assert "| `ema_pullback` | 0.10 | `failed`" in markdown
    assert "| `orb` | 0.25 | `ok`" in markdown
    assert "# orb report" in markdown
    assert "fractionability_snapshot_sha256: `" + "a" * 64 + "`" in markdown


def test_publish_selection_keeps_top_ranked_failure_when_none_pass(
    tmp_path: Path,
) -> None:
    ema_paths = _write_candidate(
        tmp_path,
        candidate="ema_pullback",
        scale="0.10",
        total_pnl=10.0,
        eventual_pass_rate=0.08,
        starts_eventually_passed=8,
    )
    orb_paths = _write_candidate(
        tmp_path,
        candidate="orb",
        scale="0.25",
        total_pnl=5.0,
        eventual_pass_rate=0.20,
        starts_eventually_passed=20,
    )
    results_path = tmp_path / "results.tsv"
    _write_results(
        results_path,
        [
            ("ema_pullback", "0.10", *ema_paths),
            ("orb", "0.25", *orb_paths),
        ],
    )

    selection = publish_selection(
        results_path=results_path,
        output_dir=tmp_path / "published",
        min_eventual_pass_rate=0.50,
        default_min_pnl=0.01,
    )

    assert selection["selected_candidate"] == "ema_pullback"
    assert selection["selection_reason"] == "top_ranked_failure"
    assert selection["passing_candidate_count"] == 0
    assert all(
        row["detail"] == "eventual_pass_rate_below_gate"
        for row in selection["rows"]
    )
