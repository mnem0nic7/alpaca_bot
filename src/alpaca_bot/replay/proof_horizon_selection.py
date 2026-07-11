from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class CandidateResult:
    candidate: str
    candidate_scale: str
    report_path: Path
    summary_path: Path
    stderr_path: Path
    payload: dict[str, Any]
    status: str
    detail: str


def _as_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _evaluate_candidate(
    *,
    candidate: str,
    candidate_scale: str,
    payload: dict[str, Any],
    min_eventual_pass_rate: float,
    default_min_pnl: float,
) -> tuple[str, str]:
    strategy_parts = {
        part.strip()
        for part in str(payload.get("strategy") or "").split("+")
        if part.strip()
    }
    if candidate not in strategy_parts:
        return "invalid", "candidate_missing_from_strategy_label"

    scales = payload.get("confidence_scales")
    expected_scale = _as_float(candidate_scale)
    actual_scale = _as_float(scales.get(candidate)) if isinstance(scales, dict) else None
    if expected_scale is None or actual_scale is None:
        return "invalid", "candidate_scale_missing"
    if not math.isclose(expected_scale, actual_scale, rel_tol=1e-9, abs_tol=1e-9):
        return "invalid", "candidate_scale_mismatch"

    trades = _as_int(payload.get("trades"))
    total_pnl = _as_float(payload.get("total_pnl"))
    starts_eventually_passed = _as_int(payload.get("starts_eventually_passed"))
    historical_starts = _as_int(payload.get("historical_starts_checked"))
    eventual_pass_rate = _as_float(payload.get("eventual_pass_rate"))
    if (
        trades is None
        or total_pnl is None
        or starts_eventually_passed is None
        or historical_starts is None
        or eventual_pass_rate is None
    ):
        return "invalid", "required_metrics_missing"

    required_pnl = _as_float(payload.get("min_pnl"))
    if required_pnl is None:
        required_pnl = default_min_pnl
    if total_pnl < required_pnl:
        return "failed", "total_pnl_below_gate"
    if starts_eventually_passed <= 0:
        return "failed", "no_historical_start_passed"
    if eventual_pass_rate < min_eventual_pass_rate:
        return "failed", "eventual_pass_rate_below_gate"
    return "ok", "fresh"


def _read_results(
    path: Path,
    *,
    min_eventual_pass_rate: float,
    default_min_pnl: float,
) -> list[CandidateResult]:
    results: list[CandidateResult] = []
    seen: set[tuple[str, str]] = set()
    for line_number, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) != 6:
            raise ValueError(f"invalid results row {line_number}: expected 6 fields")
        candidate, candidate_scale, command_status, report, summary, stderr = parts
        if not re.fullmatch(r"[A-Za-z0-9_:-]+", candidate):
            raise ValueError(f"invalid candidate on results row {line_number}")
        key = (candidate, candidate_scale)
        if key in seen:
            raise ValueError(f"duplicate proof-horizon candidate: {candidate} {candidate_scale}")
        seen.add(key)
        report_path = Path(report)
        summary_path = Path(summary)
        stderr_path = Path(stderr)
        if command_status != "passed":
            raise ValueError(
                f"proof-horizon command did not pass: {candidate} {candidate_scale}"
            )
        try:
            payload = json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid proof-horizon summary: {summary_path}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"proof-horizon summary is not an object: {summary_path}")
        status, detail = _evaluate_candidate(
            candidate=candidate,
            candidate_scale=candidate_scale,
            payload=payload,
            min_eventual_pass_rate=min_eventual_pass_rate,
            default_min_pnl=default_min_pnl,
        )
        results.append(
            CandidateResult(
                candidate=candidate,
                candidate_scale=candidate_scale,
                report_path=report_path,
                summary_path=summary_path,
                stderr_path=stderr_path,
                payload=payload,
                status=status,
                detail=detail,
            )
        )
    if not results:
        raise ValueError("proof-horizon results are empty")
    return results


def _write_text_atomic(path: Path, value: str) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(value)
    tmp_path.replace(path)


def _selection_payload(
    results: list[CandidateResult],
    *,
    selected: CandidateResult,
    min_eventual_pass_rate: float,
) -> dict[str, Any]:
    rows = []
    for result in results:
        rows.append(
            {
                "candidate": result.candidate,
                "candidate_scale": result.candidate_scale,
                "status": result.status,
                "detail": result.detail,
                "summary": str(result.summary_path),
                "trades": _as_int(result.payload.get("trades")),
                "total_pnl": _as_float(result.payload.get("total_pnl")),
                "eventual_pass_rate": _as_float(
                    result.payload.get("eventual_pass_rate")
                ),
                "starts_eventually_passed": _as_int(
                    result.payload.get("starts_eventually_passed")
                ),
                "historical_starts_checked": _as_int(
                    result.payload.get("historical_starts_checked")
                ),
            }
        )
    passing_count = sum(result.status == "ok" for result in results)
    return {
        "schema_version": 1,
        "selected_candidate": selected.candidate,
        "selected_candidate_scale": selected.candidate_scale,
        "selection_reason": (
            "first_passing" if selected.status == "ok" else "top_ranked_failure"
        ),
        "candidate_count": len(results),
        "passing_candidate_count": passing_count,
        "min_eventual_pass_rate": min_eventual_pass_rate,
        "rows": rows,
    }


def _format_markdown(
    selection: dict[str, Any],
    *,
    selected_report: str,
    fractionability_snapshot: dict[str, Any] | None = None,
) -> str:
    lines = [
        "# Second strategy proof-horizon candidates",
        "",
        f"- selected_candidate: `{selection['selected_candidate']}`",
        f"- selected_candidate_scale: `{selection['selected_candidate_scale']}`",
        f"- selection_reason: `{selection['selection_reason']}`",
        f"- candidate_count: `{selection['candidate_count']}`",
        f"- passing_candidate_count: `{selection['passing_candidate_count']}`",
        f"- min_eventual_pass_rate: `{selection['min_eventual_pass_rate']:.4f}`",
    ]
    if fractionability_snapshot is not None:
        lines.extend(
            [
                f"- fractionability_snapshot_file: `{fractionability_snapshot.get('snapshot_file')}`",
                f"- scenario_symbols_file: `{fractionability_snapshot.get('universe_symbols_file')}`",
                f"- fractionability_snapshot_sha256: `{fractionability_snapshot.get('snapshot_sha256')}`",
                f"- fractionability_universe_sha256: `{fractionability_snapshot.get('universe_sha256')}`",
            ]
        )
    lines.extend(
        [
            "",
            "| candidate | scale | status | detail | trades | total P&L | eventual pass rate |",
            "|---|---:|---|---|---:|---:|---:|",
        ]
    )
    for row in selection["rows"]:
        pnl = "n/a" if row["total_pnl"] is None else f"{row['total_pnl']:.2f}"
        rate = (
            "n/a"
            if row["eventual_pass_rate"] is None
            else f"{row['eventual_pass_rate']:.2%}"
        )
        trades = "n/a" if row["trades"] is None else str(row["trades"])
        lines.append(
            f"| `{row['candidate']}` | {row['candidate_scale']} | "
            f"`{row['status']}` | `{row['detail']}` | {trades} | {pnl} | {rate} |"
        )
    lines.extend(["", "## Selected candidate report", "", selected_report.rstrip(), ""])
    return "\n".join(lines)


def publish_selection(
    *,
    results_path: Path,
    output_dir: Path,
    min_eventual_pass_rate: float,
    default_min_pnl: float,
    fractionability_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not 0.0 <= min_eventual_pass_rate <= 1.0:
        raise ValueError("min eventual pass rate must be between 0 and 1")
    results = _read_results(
        results_path,
        min_eventual_pass_rate=min_eventual_pass_rate,
        default_min_pnl=default_min_pnl,
    )
    selected = next((result for result in results if result.status == "ok"), results[0])
    selection = _selection_payload(
        results,
        selected=selected,
        min_eventual_pass_rate=min_eventual_pass_rate,
    )
    selected_payload = dict(selected.payload)
    selected_payload["candidate_selection"] = selection
    if fractionability_snapshot is not None:
        selected_payload["fractionability_snapshot"] = fractionability_snapshot
    try:
        selected_report = selected.report_path.read_text()
    except OSError as exc:
        raise ValueError(f"could not read selected report: {selected.report_path}") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(
        output_dir / "summary.json",
        json.dumps(selected_payload, indent=2, sort_keys=True) + "\n",
    )
    _write_text_atomic(
        output_dir / "candidates.json",
        json.dumps(selection, indent=2, sort_keys=True) + "\n",
    )
    _write_text_atomic(
        output_dir / "summary.md",
        _format_markdown(
            selection,
            selected_report=selected_report,
            fractionability_snapshot=fractionability_snapshot,
        ),
    )
    return selection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Select and publish ranked second-strategy proof horizons."
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-eventual-pass-rate", type=float, required=True)
    parser.add_argument("--default-min-pnl", type=float, required=True)
    parser.add_argument("--fractionability-metadata", type=Path)
    args = parser.parse_args(argv)
    try:
        fractionability_snapshot = None
        if args.fractionability_metadata is not None:
            fractionability_snapshot = json.loads(
                args.fractionability_metadata.read_text()
            )
            if not isinstance(fractionability_snapshot, dict):
                raise ValueError("fractionability metadata must be a JSON object")
        selection = publish_selection(
            results_path=args.results,
            output_dir=args.output_dir,
            min_eventual_pass_rate=args.min_eventual_pass_rate,
            default_min_pnl=args.default_min_pnl,
            fractionability_snapshot=fractionability_snapshot,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        "proof_horizon_selected_candidate="
        f"{selection['selected_candidate']} "
        f"scale={selection['selected_candidate_scale']} "
        f"reason={selection['selection_reason']} "
        f"candidates={selection['candidate_count']} "
        f"passing={selection['passing_candidate_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
