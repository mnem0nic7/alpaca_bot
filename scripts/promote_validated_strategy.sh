#!/usr/bin/env bash
# Promote a replay-validated stock strategy into the paper-approved allowlist,
# enable its runtime flag, and redeploy. The action requires an explicit
# operator confirmation string after evidence validation succeeds.
# Set PROMOTE_VALIDATED_STRATEGY_APPROVAL_ONLY=true to record the approval
# marker only, without changing the env file, strategy flag, or deployment.
#
# Usage: promote_validated_strategy.sh [ENV_FILE] [STRATEGY_NAME] [EVIDENCE_ROOT] [DEPLOY_SCRIPT]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
REQUESTED_STRATEGY="${2:-}"
EVIDENCE_ROOT="${3:-${SECOND_STRATEGY_OUTPUT_ROOT:-/var/lib/alpaca-bot/nightly/second_strategy}}"
DEPLOY_SCRIPT="${4:-$ROOT_DIR/scripts/deploy.sh}"
COMPOSE_FILE="${PROMOTE_VALIDATED_STRATEGY_COMPOSE_FILE:-$ROOT_DIR/deploy/compose.yaml}"
APPROVAL_MARKER="${PROMOTE_VALIDATED_STRATEGY_APPROVAL_MARKER:-$EVIDENCE_ROOT/promotion_approval.json}"
MAX_P_MEAN_LE_ZERO="${PROMOTE_VALIDATED_STRATEGY_MAX_P_MEAN_LE_ZERO:-0.05}"
MIN_CANDIDATE_TRADES="${PROMOTE_VALIDATED_STRATEGY_MIN_CANDIDATE_TRADES:-30}"
REQUIRE_DECISION_DRY_RUN="${PROMOTE_VALIDATED_STRATEGY_REQUIRE_DECISION_DRY_RUN:-true}"
CONFIRMATION="${PROMOTE_VALIDATED_STRATEGY_CONFIRM:-}"
DRY_RUN="${PROMOTE_VALIDATED_STRATEGY_DRY_RUN:-true}"
APPROVAL_ONLY="${PROMOTE_VALIDATED_STRATEGY_APPROVAL_ONLY:-false}"
LOG_PREFIX="[promote_validated_strategy $(date -u '+%Y-%m-%dT%H:%M:%SZ')]"
PROMOTION_SCOPED_STRATEGY_KEYS=(
  PROFIT_PROBE_STRATEGIES
  PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES
  PAPER_READINESS_EXPECT_ENABLED_STRATEGIES
  PAPER_ACTIVITY_STRATEGIES
  SESSION_GUARD_STRATEGIES
  PROOF_STATUS_APPROVED_STRATEGIES
  DEPLOY_EXPECT_ENABLED_STRATEGIES
  DEPLOY_DECISION_DRY_RUN_STRATEGIES
)
PROMOTION_ENV_KEYS=(
  PAPER_APPROVED_STRATEGIES
  "${PROMOTION_SCOPED_STRATEGY_KEYS[@]}"
)

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$LOG_PREFIX env file not found: $ENV_FILE" >&2
  exit 1
fi

if [[ ! "$MAX_P_MEAN_LE_ZERO" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "$LOG_PREFIX PROMOTE_VALIDATED_STRATEGY_MAX_P_MEAN_LE_ZERO must be a non-negative number" >&2
  exit 1
fi
if [[ ! "$MIN_CANDIDATE_TRADES" =~ ^[0-9]+$ ]] || [[ "$MIN_CANDIDATE_TRADES" -lt 1 ]]; then
  echo "$LOG_PREFIX PROMOTE_VALIDATED_STRATEGY_MIN_CANDIDATE_TRADES must be a positive integer" >&2
  exit 1
fi
case "${DRY_RUN,,}" in
  true|1|yes|y)
    DRY_RUN=true
    ;;
  false|0|no|n|"")
    DRY_RUN=false
    ;;
  *)
    echo "$LOG_PREFIX PROMOTE_VALIDATED_STRATEGY_DRY_RUN must be true or false" >&2
    exit 1
    ;;
esac
case "${REQUIRE_DECISION_DRY_RUN,,}" in
  true|1|yes|y)
    REQUIRE_DECISION_DRY_RUN=true
    ;;
  false|0|no|n|"")
    REQUIRE_DECISION_DRY_RUN=false
    ;;
  *)
    echo "$LOG_PREFIX PROMOTE_VALIDATED_STRATEGY_REQUIRE_DECISION_DRY_RUN must be true or false" >&2
    exit 1
    ;;
esac
case "${APPROVAL_ONLY,,}" in
  true|1|yes|y)
    APPROVAL_ONLY=true
    ;;
  false|0|no|n|"")
    APPROVAL_ONLY=false
    ;;
  *)
    echo "$LOG_PREFIX PROMOTE_VALIDATED_STRATEGY_APPROVAL_ONLY must be true or false" >&2
    exit 1
    ;;
esac

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

MIN_PROOF_HORIZON_PASS_RATE="${PROMOTE_VALIDATED_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE:-${PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE:-0.50}}"
MAX_EVIDENCE_AGE_HOURS="${PROMOTE_VALIDATED_STRATEGY_MAX_EVIDENCE_AGE_HOURS:-${PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS:-48}}"
PROMOTION_DENYLIST="${PROMOTE_VALIDATED_STRATEGY_DENYLIST:-${PAPER_STRATEGY_PROMOTION_DENYLIST:-ema_pullback,vwap_cross}}"

if [[ ! "$MIN_PROOF_HORIZON_PASS_RATE" =~ ^(0(\.[0-9]+)?|1(\.0+)?)$ ]]; then
  echo "$LOG_PREFIX PROMOTE_VALIDATED_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE must be between 0 and 1" >&2
  exit 1
fi
if [[ ! "$MAX_EVIDENCE_AGE_HOURS" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "$LOG_PREFIX PROMOTE_VALIDATED_STRATEGY_MAX_EVIDENCE_AGE_HOURS must be a non-negative number" >&2
  exit 1
fi

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "$LOG_PREFIX refusing promotion outside paper mode: TRADING_MODE=${TRADING_MODE:-unset}" >&2
  exit 1
fi
if [[ -z "${STRATEGY_VERSION:-}" ]]; then
  echo "$LOG_PREFIX missing STRATEGY_VERSION in $ENV_FILE" >&2
  exit 1
fi
if [[ "${PAPER_PROOF_FREEZE:-false}" != "true" ]]; then
  echo "$LOG_PREFIX refusing promotion unless PAPER_PROOF_FREEZE=true" >&2
  exit 1
fi

validation_env="$(
  PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" python3 - \
    "$EVIDENCE_ROOT" \
    "$REQUESTED_STRATEGY" \
    "$MAX_P_MEAN_LE_ZERO" \
    "$MIN_CANDIDATE_TRADES" \
    "$MIN_PROOF_HORIZON_PASS_RATE" \
    "$MAX_EVIDENCE_AGE_HOURS" \
    "$PROMOTION_DENYLIST" <<'PY'
from __future__ import annotations

import hashlib
import json
import math
import shlex
import sys
import time
from pathlib import Path

from alpaca_bot.strategy import OPTION_STRATEGY_NAMES, STRATEGY_REGISTRY


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def as_float(row: dict[str, object], key: str) -> float:
    value = row.get(key)
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} is not numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{key} is not finite")
    return parsed


def as_int(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} is not an integer") from exc


def fractionability_identity(
    payload: dict[str, object],
    *,
    label: str,
    summary_path: Path,
) -> tuple[str, str, int, int, int]:
    snapshot = payload.get("fractionability_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        fail(f"{label} fractionability snapshot is missing or invalid")
    snapshot_sha256 = str(snapshot.get("snapshot_sha256") or "").lower()
    universe_sha256 = str(snapshot.get("universe_sha256") or "").lower()
    if len(snapshot_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in snapshot_sha256
    ):
        fail(f"{label} fractionability snapshot SHA256 is invalid")
    if len(universe_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in universe_sha256
    ):
        fail(f"{label} fractionability universe SHA256 is invalid")
    try:
        universe_count = as_int(snapshot, "universe_symbol_count")
        fractionable_count = as_int(snapshot, "fractionable_symbol_count")
        non_fractionable_count = as_int(
            snapshot, "non_fractionable_symbol_count"
        )
    except ValueError as exc:
        fail(f"{label} fractionability snapshot is invalid: {exc}")
    if (
        universe_count <= 0
        or fractionable_count < 0
        or non_fractionable_count < 0
        or fractionable_count + non_fractionable_count != universe_count
    ):
        fail(f"{label} fractionability symbol counts are invalid")
    snapshot_file = str(snapshot.get("snapshot_file") or "").strip()
    if not snapshot_file:
        fail(f"{label} fractionability snapshot file is missing")
    snapshot_path = Path(snapshot_file)
    if not snapshot_path.is_absolute():
        snapshot_path = summary_path.parent / snapshot_path
    try:
        snapshot_bytes = snapshot_path.read_bytes()
        snapshot_symbols = {
            line.strip().upper()
            for line in snapshot_bytes.decode("utf-8").splitlines()
            if line.strip()
        }
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"{label} fractionability snapshot file is unreadable: {exc}")
    current_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
    if current_sha256 != snapshot_sha256:
        fail(f"{label} fractionability snapshot file hash does not match")
    if len(snapshot_symbols) != fractionable_count:
        fail(f"{label} fractionability snapshot symbol count does not match")
    universe_symbols_file = str(
        snapshot.get("universe_symbols_file") or ""
    ).strip()
    if not universe_symbols_file:
        fail(f"{label} scenario universe file is missing")
    universe_path = Path(universe_symbols_file)
    if not universe_path.is_absolute():
        universe_path = summary_path.parent / universe_path
    try:
        universe_bytes = universe_path.read_bytes()
        universe_symbols = {
            line.strip().upper()
            for line in universe_bytes.decode("utf-8").splitlines()
            if line.strip()
        }
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"{label} scenario universe file is unreadable: {exc}")
    if hashlib.sha256(universe_bytes).hexdigest() != universe_sha256:
        fail(f"{label} scenario universe file hash does not match")
    if (
        len(universe_symbols) != universe_count
        or not snapshot_symbols.issubset(universe_symbols)
        or len(universe_symbols - snapshot_symbols) != non_fractionable_count
    ):
        fail(f"{label} scenario universe symbols do not match metadata")
    return (
        snapshot_sha256,
        universe_sha256,
        universe_count,
        fractionable_count,
        non_fractionable_count,
    )


root = Path(sys.argv[1])
requested_strategy = sys.argv[2].strip()
max_p_mean_le_zero = float(sys.argv[3])
min_candidate_trades = int(sys.argv[4])
min_proof_horizon_pass_rate = float(sys.argv[5])
max_evidence_age_hours = float(sys.argv[6])
promotion_denylist = {
    name.strip()
    for name in sys.argv[7].split(",")
    if name.strip() and name.strip().lower() != "none"
}
if any(
    any(not (char.isalnum() or char in "_:-") for char in name)
    for name in promotion_denylist
):
    fail("promotion denylist contains an invalid strategy name")
summary_path = root / "latest_validation" / "summary.json"
if not summary_path.exists():
    fail(f"validation summary missing: {summary_path}")
summary_bytes = summary_path.read_bytes()
try:
    payload = json.loads(summary_bytes)
except json.JSONDecodeError as exc:
    fail(f"validation summary is not valid JSON: {exc}")
if not isinstance(payload, dict):
    fail("validation summary must be an object")

prefilter_path = root / "latest" / "summary.json"
if not prefilter_path.exists():
    fail(f"prefilter summary missing: {prefilter_path}")
prefilter_bytes = prefilter_path.read_bytes()
try:
    prefilter_payload = json.loads(prefilter_bytes)
except json.JSONDecodeError as exc:
    fail(f"prefilter summary is not valid JSON: {exc}")
if not isinstance(prefilter_payload, dict):
    fail("prefilter summary must be an object")
validation_prefilter_reference = str(
    payload.get("prefilter_summary_json") or ""
).strip()
if not validation_prefilter_reference:
    fail("validation summary prefilter reference is missing")
validation_prefilter_path = Path(validation_prefilter_reference)
if not validation_prefilter_path.is_absolute():
    validation_prefilter_path = summary_path.parent / validation_prefilter_path
if validation_prefilter_path.resolve() != prefilter_path.resolve():
    fail("validation summary references a stale prefilter summary")
expected_prefilter_sha256 = str(
    payload.get("prefilter_summary_sha256") or ""
).strip()
current_prefilter_sha256 = hashlib.sha256(prefilter_bytes).hexdigest()
if expected_prefilter_sha256 != current_prefilter_sha256:
    fail("validation summary prefilter hash does not match")

proof_horizon_path = root / "latest_proof_horizon" / "summary.json"
if not proof_horizon_path.exists():
    fail(f"proof horizon summary missing: {proof_horizon_path}")
proof_horizon_bytes = proof_horizon_path.read_bytes()
try:
    proof_horizon_payload = json.loads(proof_horizon_bytes)
except json.JSONDecodeError as exc:
    fail(f"proof horizon summary is not valid JSON: {exc}")
if not isinstance(proof_horizon_payload, dict):
    fail("proof horizon summary must be an object")

try:
    prefilter_mtime = prefilter_path.stat().st_mtime
    validation_mtime = summary_path.stat().st_mtime
    proof_horizon_mtime = proof_horizon_path.stat().st_mtime
except OSError as exc:
    fail(f"could not inspect evidence timestamps: {exc}")
if proof_horizon_mtime < validation_mtime:
    fail("proof horizon summary is older than validation summary")
if validation_mtime < prefilter_mtime:
    fail("validation summary is older than prefilter summary")
now = time.time()
for label, modified_at in (
    ("prefilter", prefilter_mtime),
    ("validation", validation_mtime),
    ("proof horizon", proof_horizon_mtime),
):
    age_hours = max(0.0, (now - modified_at) / 3600.0)
    if age_hours > max_evidence_age_hours:
        fail(
            f"{label} summary is stale: age_hours={age_hours:.2f} "
            f"max_age_hours={max_evidence_age_hours:g}"
        )

prefilter_fractionability = fractionability_identity(
    prefilter_payload,
    label="prefilter",
    summary_path=prefilter_path,
)
validation_fractionability = fractionability_identity(
    payload,
    label="validation",
    summary_path=summary_path,
)
proof_fractionability = fractionability_identity(
    proof_horizon_payload,
    label="proof horizon",
    summary_path=proof_horizon_path,
)
if not (
    prefilter_fractionability
    == validation_fractionability
    == proof_fractionability
):
    fail("fractionability lineage does not match across promotion evidence")

rows = payload.get("rows")
if not isinstance(rows, list):
    fail("validation summary rows must be a list")

stock_strategy_names = set(STRATEGY_REGISTRY)
option_strategy_names = set(OPTION_STRATEGY_NAMES)
if requested_strategy:
    if requested_strategy in option_strategy_names:
        fail(f"{requested_strategy} is an option strategy; stock-only paper proof promotion required")
    if requested_strategy not in stock_strategy_names:
        fail(f"{requested_strategy} is not a known stock strategy")
    if requested_strategy in promotion_denylist:
        fail(f"{requested_strategy} is denied by paper strategy promotion policy")

passing_rows: list[dict[str, object]] = []
errors: list[str] = []
for row in rows:
    if not isinstance(row, dict):
        continue
    candidate = str(row.get("candidate") or "").strip()
    if requested_strategy and candidate != requested_strategy:
        continue
    if candidate in option_strategy_names:
        errors.append(f"{candidate}: option strategies are not promoted by this tool")
        continue
    if candidate not in stock_strategy_names:
        errors.append(f"{candidate}: unknown stock strategy")
        continue
    try:
        candidate_trades = as_int(row, "candidate_trades")
        candidate_total_pnl = as_float(row, "candidate_total_pnl")
        candidate_ci_low = as_float(row, "candidate_ci_low")
        candidate_p_mean_le_zero = as_float(row, "candidate_p_mean_le_zero")
    except ValueError as exc:
        errors.append(f"{candidate}: {exc}")
        continue
    checks = {
        "status": row.get("status") == "passed",
        "row_verdict": row.get("verdict") == "positive-edge",
        "candidate_verdict": row.get("candidate_verdict") == "positive-edge",
        "candidate_contribution_status": row.get("candidate_contribution_status") == "positive_pnl",
        "candidate_trades": candidate_trades >= min_candidate_trades,
        "candidate_total_pnl": candidate_total_pnl > 0.0,
        "candidate_ci_low": candidate_ci_low > 0.0,
        "candidate_p_mean_le_zero": candidate_p_mean_le_zero <= max_p_mean_le_zero,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        errors.append(f"{candidate}: failed {','.join(failed)}")
        continue
    passing_rows.append(row)

selection = proof_horizon_payload.get("candidate_selection")
if not isinstance(selection, dict) or selection.get("schema_version") != 1:
    fail("proof horizon candidate selection is missing or invalid")
selected_candidate = str(selection.get("selected_candidate") or "").strip()
selected_scale = str(selection.get("selected_candidate_scale") or "").strip()
selection_reason = str(selection.get("selection_reason") or "").strip()
try:
    candidate_count = as_int(selection, "candidate_count")
    passing_candidate_count = as_int(selection, "passing_candidate_count")
    selection_min_pass_rate = as_float(selection, "min_eventual_pass_rate")
except ValueError as exc:
    fail(f"proof horizon candidate selection is invalid: {exc}")
if candidate_count < 1:
    fail("proof horizon candidate selection is empty")
if passing_candidate_count < 1 or selection_reason != "first_passing":
    fail(
        "proof horizon failed: "
        f"selection_reason={selection_reason or 'missing'} "
        f"passing_candidate_count={passing_candidate_count}"
    )
if selection_min_pass_rate < min_proof_horizon_pass_rate:
    fail(
        "proof horizon gate is weaker than required: "
        f"evidence={selection_min_pass_rate:.4f} "
        f"required={min_proof_horizon_pass_rate:.4f}"
    )
if requested_strategy and selected_candidate != requested_strategy:
    fail(
        "proof horizon selected a different candidate: "
        f"selected={selected_candidate or 'none'} requested={requested_strategy}"
    )
if not requested_strategy and not selected_candidate:
    fail("proof horizon did not select a candidate")

selection_rows = selection.get("rows")
if not isinstance(selection_rows, list):
    fail("proof horizon candidate selection rows must be a list")
selected_proof_rows = [
    row
    for row in selection_rows
    if isinstance(row, dict)
    and str(row.get("candidate") or "").strip() == selected_candidate
    and str(row.get("candidate_scale") or "").strip() == selected_scale
]
if len(selected_proof_rows) != 1:
    fail("proof horizon selected row is missing or ambiguous")
selected_proof_row = selected_proof_rows[0]
if selected_proof_row.get("status") != "ok":
    fail(
        "proof horizon selected row did not pass: "
        f"status={selected_proof_row.get('status') or 'missing'} "
        f"detail={selected_proof_row.get('detail') or 'missing'}"
    )

try:
    selected_scale_value = float(selected_scale)
except ValueError:
    fail("proof horizon selected candidate scale is not numeric")
matching_rows = []
for row in passing_rows:
    if str(row.get("candidate") or "").strip() != selected_candidate:
        continue
    try:
        row_scale = as_float(row, "candidate_scale")
    except ValueError:
        continue
    if math.isclose(
        row_scale,
        selected_scale_value,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        matching_rows.append(row)
if len(matching_rows) != 1:
    detail = "; ".join(errors) if errors else "selected row missing"
    fail(
        f"no unique promotable validation row for {selected_candidate} "
        f"scale={selected_scale}: {detail}"
    )
selected = matching_rows[0]
strategy_name = selected_candidate
if strategy_name in promotion_denylist:
    fail(f"{strategy_name} is denied by paper strategy promotion policy")

proof_strategy_parts = {
    part.strip()
    for part in str(proof_horizon_payload.get("strategy") or "").split("+")
    if part.strip()
}
if strategy_name not in proof_strategy_parts:
    fail("proof horizon selected candidate is missing from strategy label")
proof_scales = proof_horizon_payload.get("confidence_scales")
proof_scale = (
    proof_scales.get(strategy_name) if isinstance(proof_scales, dict) else None
)
try:
    proof_scale_value = float(proof_scale)  # type: ignore[arg-type]
except (TypeError, ValueError):
    fail("proof horizon selected candidate scale is missing")
if not math.isclose(
    proof_scale_value,
    selected_scale_value,
    rel_tol=1e-9,
    abs_tol=1e-9,
):
    fail("proof horizon selected candidate scale does not match summary")
try:
    proof_trades = as_int(proof_horizon_payload, "trades")
    proof_total_pnl = as_float(proof_horizon_payload, "total_pnl")
    proof_starts_passed = as_int(
        proof_horizon_payload, "starts_eventually_passed"
    )
    proof_historical_starts = as_int(
        proof_horizon_payload, "historical_starts_checked"
    )
    proof_eventual_pass_rate = as_float(
        proof_horizon_payload, "eventual_pass_rate"
    )
    proof_min_pnl = as_float(proof_horizon_payload, "min_pnl")
except ValueError as exc:
    fail(f"proof horizon summary is invalid: {exc}")
if proof_trades < min_candidate_trades:
    fail("proof horizon trade count is below the promotion minimum")
if proof_total_pnl < proof_min_pnl:
    fail("proof horizon total P&L is below its gate")
if proof_starts_passed <= 0 or proof_historical_starts <= 0:
    fail("proof horizon has no passing historical starts")
if proof_eventual_pass_rate < min_proof_horizon_pass_rate:
    fail(
        "proof horizon eventual pass rate is below the promotion gate: "
        f"actual={proof_eventual_pass_rate:.4f} "
        f"required={min_proof_horizon_pass_rate:.4f}"
    )
outputs = {
    "VALIDATED_STRATEGY": strategy_name,
    "VALIDATED_SCALE": str(selected.get("candidate_scale") or ""),
    "VALIDATED_TRADES": str(selected["candidate_trades"]),
    "VALIDATED_TOTAL_PNL": str(selected["candidate_total_pnl"]),
    "VALIDATED_CI_LOW": str(selected["candidate_ci_low"]),
    "VALIDATED_P_MEAN_LE_ZERO": str(selected["candidate_p_mean_le_zero"]),
    "PREFILTER_SUMMARY": str(prefilter_path.resolve()),
    "PREFILTER_SUMMARY_SHA256": current_prefilter_sha256,
    "VALIDATION_SUMMARY": str(summary_path.resolve()),
    "VALIDATION_SUMMARY_SHA256": hashlib.sha256(summary_bytes).hexdigest(),
    "PROOF_HORIZON_SUMMARY": str(proof_horizon_path.resolve()),
    "PROOF_HORIZON_SUMMARY_SHA256": hashlib.sha256(
        proof_horizon_bytes
    ).hexdigest(),
    "PROOF_HORIZON_TRADES": str(proof_trades),
    "PROOF_HORIZON_TOTAL_PNL": str(proof_total_pnl),
    "PROOF_HORIZON_EVENTUAL_PASS_RATE": str(proof_eventual_pass_rate),
    "PROOF_HORIZON_STARTS_PASSED": str(proof_starts_passed),
    "PROOF_HORIZON_HISTORICAL_STARTS": str(proof_historical_starts),
    "PROOF_HORIZON_SELECTION_REASON": selection_reason,
    "PROOF_HORIZON_CANDIDATE_COUNT": str(candidate_count),
    "PROOF_HORIZON_PASSING_CANDIDATE_COUNT": str(passing_candidate_count),
}
for key, value in outputs.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"

eval "$validation_env"

required_confirmation="approve-${VALIDATED_STRATEGY}-paper-promotion-sha256-${VALIDATION_SUMMARY_SHA256}-proof-sha256-${PROOF_HORIZON_SUMMARY_SHA256}"
confirmation_status="missing"
if [[ -n "$CONFIRMATION" ]]; then
  if [[ "$CONFIRMATION" == "$required_confirmation" ]]; then
    confirmation_status="ok"
  else
    confirmation_status="mismatch"
  fi
fi

verify_evidence_current() {
  python3 - \
    "$EVIDENCE_ROOT" \
    "$PREFILTER_SUMMARY" \
    "$PREFILTER_SUMMARY_SHA256" \
    "$VALIDATION_SUMMARY" \
    "$VALIDATION_SUMMARY_SHA256" \
    "$PROOF_HORIZON_SUMMARY" \
    "$PROOF_HORIZON_SUMMARY_SHA256" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


root = Path(sys.argv[1])
expected_prefilter_path = sys.argv[2]
expected_prefilter_sha256 = sys.argv[3]
expected_path = sys.argv[4]
expected_sha256 = sys.argv[5]
expected_proof_path = sys.argv[6]
expected_proof_sha256 = sys.argv[7]
prefilter_path = root / "latest" / "summary.json"
if not prefilter_path.exists():
    fail(f"prefilter summary missing: {prefilter_path}")
current_prefilter_path = str(prefilter_path.resolve())
if current_prefilter_path != expected_prefilter_path:
    fail(
        "prefilter summary changed: "
        f"{current_prefilter_path} != {expected_prefilter_path}"
    )
current_prefilter_sha256 = hashlib.sha256(
    prefilter_path.read_bytes()
).hexdigest()
if current_prefilter_sha256 != expected_prefilter_sha256:
    fail("prefilter summary hash changed")
summary_path = root / "latest_validation" / "summary.json"
if not summary_path.exists():
    fail(f"validation summary missing: {summary_path}")
current_path = str(summary_path.resolve())
if current_path != expected_path:
    fail(f"validation summary changed: {current_path} != {expected_path}")
current_sha256 = hashlib.sha256(summary_path.read_bytes()).hexdigest()
if current_sha256 != expected_sha256:
    fail("validation summary hash changed")
proof_path = root / "latest_proof_horizon" / "summary.json"
if not proof_path.exists():
    fail(f"proof horizon summary missing: {proof_path}")
current_proof_path = str(proof_path.resolve())
if current_proof_path != expected_proof_path:
    fail(f"proof horizon summary changed: {current_proof_path} != {expected_proof_path}")
current_proof_sha256 = hashlib.sha256(proof_path.read_bytes()).hexdigest()
if current_proof_sha256 != expected_proof_sha256:
    fail("proof horizon summary hash changed")
if proof_path.stat().st_mtime < summary_path.stat().st_mtime:
    fail("proof horizon summary is older than validation summary")
if summary_path.stat().st_mtime < prefilter_path.stat().st_mtime:
    fail("validation summary is older than prefilter summary")
PY
}

if ! verify_evidence_current; then
  echo "$LOG_PREFIX promotion evidence changed after validation; aborting promotion" >&2
  exit 1
fi

promotion_write_access_status="ok"
promotion_env_file_writable="false"
promotion_env_dir_writable="false"
promotion_approval_marker_writable="false"
promotion_approval_marker_dir_writable="false"
probe_promotion_write_access() {
  local env_dir
  local marker_dir
  local marker_parent

  promotion_write_access_status="ok"
  promotion_env_file_writable="false"
  promotion_env_dir_writable="false"
  promotion_approval_marker_writable="false"
  promotion_approval_marker_dir_writable="false"
  env_dir="$(dirname "$ENV_FILE")"
  marker_dir="$(dirname "$APPROVAL_MARKER")"
  marker_parent="$(dirname "$marker_dir")"

  if [[ ! -w "$ENV_FILE" ]]; then
    promotion_write_access_status="env_file_not_writable"
  else
    promotion_env_file_writable="true"
  fi
  if [[ ! -w "$env_dir" ]]; then
    if [[ "$promotion_write_access_status" == "ok" ]]; then
      promotion_write_access_status="env_dir_not_writable"
    fi
  else
    promotion_env_dir_writable="true"
  fi

  if [[ -e "$APPROVAL_MARKER" && ! -w "$APPROVAL_MARKER" ]]; then
    if [[ "$promotion_write_access_status" == "ok" ]]; then
      promotion_write_access_status="approval_marker_not_writable"
    fi
  else
    promotion_approval_marker_writable="true"
  fi
  if [[ -d "$marker_dir" ]]; then
    if [[ ! -w "$marker_dir" ]]; then
      if [[ "$promotion_write_access_status" == "ok" ]]; then
        promotion_write_access_status="approval_marker_dir_not_writable"
      fi
    else
      promotion_approval_marker_dir_writable="true"
    fi
  elif [[ ! -d "$marker_parent" || ! -w "$marker_parent" ]]; then
    if [[ "$promotion_write_access_status" == "ok" ]]; then
      promotion_write_access_status="approval_marker_parent_not_writable"
    fi
  fi
}

require_promotion_write_access() {
  probe_promotion_write_access
  case "$promotion_write_access_status" in
    ok)
      return 0
      ;;
    env_file_not_writable)
      echo "$LOG_PREFIX env file is not writable: $ENV_FILE" >&2
      ;;
    env_dir_not_writable)
      echo "$LOG_PREFIX env file directory is not writable for atomic update: $(dirname "$ENV_FILE")" >&2
      ;;
    approval_marker_not_writable)
      echo "$LOG_PREFIX approval marker is not writable: $APPROVAL_MARKER" >&2
      ;;
    approval_marker_dir_not_writable)
      echo "$LOG_PREFIX approval marker directory is not writable: $(dirname "$APPROVAL_MARKER")" >&2
      ;;
    approval_marker_parent_not_writable)
      echo "$LOG_PREFIX approval marker parent directory is not writable: $(dirname "$(dirname "$APPROVAL_MARKER")")" >&2
      ;;
    *)
      echo "$LOG_PREFIX promotion write access failed: $promotion_write_access_status" >&2
      ;;
  esac
  echo "$LOG_PREFIX promotion handoff: status=$(promotion_handoff_status) step=$(promotion_handoff_step) env_keys=$(promotion_env_keys_csv)" >&2
  exit 1
}

require_approval_marker_write_access() {
  local marker_dir
  local marker_parent

  marker_dir="$(dirname "$APPROVAL_MARKER")"
  marker_parent="$(dirname "$marker_dir")"
  if [[ -e "$APPROVAL_MARKER" && ! -w "$APPROVAL_MARKER" ]]; then
    echo "$LOG_PREFIX approval marker is not writable: $APPROVAL_MARKER" >&2
    exit 1
  fi
  if [[ -d "$marker_dir" ]]; then
    if [[ ! -w "$marker_dir" ]]; then
      echo "$LOG_PREFIX approval marker directory is not writable: $marker_dir" >&2
      exit 1
    fi
    return 0
  fi
  if [[ ! -d "$marker_parent" || ! -w "$marker_parent" ]]; then
    echo "$LOG_PREFIX approval marker parent directory is not writable: $marker_parent" >&2
    exit 1
  fi
}

run_broker_flat_check() {
  BROKER_FLAT_CONTEXT="promote validated strategy" \
    "$ROOT_DIR/scripts/broker_flat_check.sh" "$ENV_FILE"
}

run_candidate_decision_dry_run() {
  PAPER_DECISION_DRY_RUN_STRATEGY="$VALIDATED_STRATEGY" \
    PAPER_DECISION_DRY_RUN_ALLOW_DISABLED=true \
    "$ROOT_DIR/scripts/paper_decision_dry_run.sh" "$ENV_FILE"
}

require_candidate_decision_dry_run() {
  local detail

  if [[ "$REQUIRE_DECISION_DRY_RUN" != "true" ]]; then
    echo "$LOG_PREFIX candidate decision dry run skipped: require=false"
    return 0
  fi
  if ! detail="$(run_candidate_decision_dry_run 2>&1)"; then
    detail="$(compact_dry_run_detail "$detail")"
    echo "$LOG_PREFIX candidate decision dry run failed for $VALIDATED_STRATEGY: ${detail:-failed}" >&2
    exit 1
  fi
  echo "$LOG_PREFIX candidate decision dry run ok: $(compact_dry_run_detail "$detail")"
}

compact_dry_run_detail() {
  local value="$1"
  value="${value//$'\r'/ }"
  value="${value//$'\n'/; }"
  value="${value//$'\t'/ }"
  printf '%s\n' "$value"
}

promotion_env_keys_csv() {
  local IFS=,
  printf '%s' "${PROMOTION_ENV_KEYS[*]}"
}

approval_marker_handoff_ready() {
  local marker_dir
  local marker_parent

  marker_dir="$(dirname "$APPROVAL_MARKER")"
  marker_parent="$(dirname "$marker_dir")"
  if [[ -e "$APPROVAL_MARKER" ]]; then
    [[ -w "$APPROVAL_MARKER" && -w "$marker_dir" ]]
    return
  fi
  if [[ -d "$marker_dir" ]]; then
    [[ -w "$marker_dir" ]]
    return
  fi
  [[ -d "$marker_parent" && -w "$marker_parent" ]]
}

promotion_handoff_status() {
  if [[ "$APPROVAL_ONLY" == "true" ]]; then
    if approval_marker_handoff_ready; then
      printf 'ready_needs_approval_marker'
    else
      printf 'ready_needs_marker_write_access'
    fi
    return
  fi
  case "$promotion_write_access_status" in
    ok)
      printf 'none'
      ;;
    env_file_not_writable|env_dir_not_writable)
      printf 'ready_needs_privileged_env_write'
      ;;
    approval_marker_not_writable|approval_marker_dir_not_writable|approval_marker_parent_not_writable)
      printf 'ready_needs_marker_write_access'
      ;;
    *)
      printf 'blocked'
      ;;
  esac
}

promotion_handoff_step() {
  if [[ "$APPROVAL_ONLY" == "true" ]]; then
    if approval_marker_handoff_ready; then
      printf 'approval_marker_write'
    else
      printf 'approval_marker_write_access'
    fi
    return
  fi
  case "$promotion_write_access_status" in
    ok)
      printf 'none'
      ;;
    env_file_not_writable|env_dir_not_writable)
      printf 'env_allowlist_update'
      ;;
    approval_marker_not_writable|approval_marker_dir_not_writable|approval_marker_parent_not_writable)
      printf 'approval_marker_write'
      ;;
    *)
      printf 'write_access_probe'
      ;;
  esac
}

if [[ "$DRY_RUN" == "true" ]]; then
  validation_current_status="ok"
  validation_current_detail="ok"
  if ! validation_current_detail="$(verify_evidence_current 2>&1)"; then
    validation_current_status="failed"
  fi
  probe_promotion_write_access
  broker_flat_status="ok"
  broker_flat_detail=""
  if ! broker_flat_detail="$(run_broker_flat_check 2>&1)"; then
    broker_flat_status="failed"
  fi
  validation_current_detail="$(compact_dry_run_detail "${validation_current_detail:-ok}")"
  broker_flat_detail="$(compact_dry_run_detail "${broker_flat_detail:-ok}")"
  candidate_decision_dry_run_status="skipped"
  candidate_decision_dry_run_detail="require=false"
  if [[ "$REQUIRE_DECISION_DRY_RUN" == "true" ]]; then
    candidate_decision_dry_run_status="ok"
    if ! candidate_decision_dry_run_detail="$(run_candidate_decision_dry_run 2>&1)"; then
      candidate_decision_dry_run_status="failed"
    fi
    candidate_decision_dry_run_detail="$(compact_dry_run_detail "${candidate_decision_dry_run_detail:-ok}")"
  fi
  printf '%s dry_run=true strategy=%s scale=%s trades=%s pnl=%s ci_low=%s p_mean_le_zero=%s validation_summary=%s validation_summary_sha256=%s proof_horizon_summary=%s proof_horizon_summary_sha256=%s proof_horizon_eventual_pass_rate=%s proof_horizon_passing_candidate_count=%s confirmation_status=%s required_confirmation=%s evidence_current_status=%s evidence_current_detail=%s candidate_decision_dry_run_status=%s candidate_decision_dry_run_detail=%s write_access_status=%s promotion_handoff_status=%s promotion_handoff_step=%s promotion_env_keys=%s env_file_writable=%s env_dir_writable=%s approval_marker=%s approval_marker_writable=%s approval_marker_dir_writable=%s broker_flat_status=%s broker_flat_detail=%s\n' \
    "$LOG_PREFIX" \
    "$VALIDATED_STRATEGY" \
    "$VALIDATED_SCALE" \
    "$VALIDATED_TRADES" \
    "$VALIDATED_TOTAL_PNL" \
    "$VALIDATED_CI_LOW" \
    "$VALIDATED_P_MEAN_LE_ZERO" \
    "$VALIDATION_SUMMARY" \
    "$VALIDATION_SUMMARY_SHA256" \
    "$PROOF_HORIZON_SUMMARY" \
    "$PROOF_HORIZON_SUMMARY_SHA256" \
    "$PROOF_HORIZON_EVENTUAL_PASS_RATE" \
    "$PROOF_HORIZON_PASSING_CANDIDATE_COUNT" \
    "$confirmation_status" \
    "$required_confirmation" \
    "$validation_current_status" \
    "${validation_current_detail:-ok}" \
    "$candidate_decision_dry_run_status" \
    "${candidate_decision_dry_run_detail:-ok}" \
    "$promotion_write_access_status" \
    "$(promotion_handoff_status)" \
    "$(promotion_handoff_step)" \
    "$(promotion_env_keys_csv)" \
    "$promotion_env_file_writable" \
    "$promotion_env_dir_writable" \
    "$APPROVAL_MARKER" \
    "$promotion_approval_marker_writable" \
    "$promotion_approval_marker_dir_writable" \
    "$broker_flat_status" \
    "${broker_flat_detail:-ok}"
  printf '%s dry_run_promotion_command=env PROMOTE_VALIDATED_STRATEGY_CONFIRM=%q PROMOTE_VALIDATED_STRATEGY_DRY_RUN=false %q %q %q %q %q\n' \
    "$LOG_PREFIX" \
    "$required_confirmation" \
    "$0" \
    "$ENV_FILE" \
    "$VALIDATED_STRATEGY" \
    "$EVIDENCE_ROOT" \
    "$DEPLOY_SCRIPT"
  printf '%s dry_run_approval_marker_command=env PROMOTE_VALIDATED_STRATEGY_CONFIRM=%q PROMOTE_VALIDATED_STRATEGY_DRY_RUN=false PROMOTE_VALIDATED_STRATEGY_APPROVAL_ONLY=true %q %q %q %q %q\n' \
    "$LOG_PREFIX" \
    "$required_confirmation" \
    "$0" \
    "$ENV_FILE" \
    "$VALIDATED_STRATEGY" \
    "$EVIDENCE_ROOT" \
    "$DEPLOY_SCRIPT"
  if [[ "$confirmation_status" == "mismatch" ]]; then
    exit 2
  fi
  if [[ "$validation_current_status" != "ok" || "$broker_flat_status" != "ok" || "$candidate_decision_dry_run_status" != "ok" ]]; then
    exit 1
  fi
  exit 0
fi

if [[ "$confirmation_status" != "ok" ]]; then
  echo "$LOG_PREFIX evidence validated for $VALIDATED_STRATEGY scale=$VALIDATED_SCALE trades=$VALIDATED_TRADES pnl=$VALIDATED_TOTAL_PNL ci_low=$VALIDATED_CI_LOW p_mean_le_zero=$VALIDATED_P_MEAN_LE_ZERO validation_summary=$VALIDATION_SUMMARY validation_summary_sha256=$VALIDATION_SUMMARY_SHA256 proof_horizon_summary=$PROOF_HORIZON_SUMMARY proof_horizon_summary_sha256=$PROOF_HORIZON_SUMMARY_SHA256 proof_horizon_eventual_pass_rate=$PROOF_HORIZON_EVENTUAL_PASS_RATE" >&2
  echo "$LOG_PREFIX refusing to promote without explicit confirmation" >&2
  echo "$LOG_PREFIX rerun with PROMOTE_VALIDATED_STRATEGY_CONFIRM=$required_confirmation" >&2
  exit 2
fi

read_env_value() {
  local key="$1"
  awk -v key="$key" '
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      value=$0
      sub("^[[:space:]]*" key "[[:space:]]*=", "", value)
      sub(/[[:space:]]*#.*/, "", value)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^["\047]|["\047]$/, "", value)
      print value
      exit
    }
  ' "$ENV_FILE"
}

env_key_exists() {
  local key="$1"
  grep -Eq "^[[:space:]]*$key[[:space:]]*=" "$ENV_FILE"
}

csv_contains() {
  local csv="$1"
  local needle="$2"
  local raw
  local name
  local -a names
  IFS=',' read -r -a names <<< "$csv"
  for raw in "${names[@]}"; do
    name="$(printf '%s' "$raw" | tr -d '[:space:]')"
    if [[ "$name" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

append_csv_name() {
  local csv="$1"
  local name="$2"
  if [[ -z "$csv" ]]; then
    printf '%s\n' "$name"
  elif csv_contains "$csv" "$name"; then
    printf '%s\n' "$csv"
  else
    printf '%s,%s\n' "$csv" "$name"
  fi
}

update_env_value() {
  local key="$1"
  local value="$2"
  local env_dir
  local env_name
  local tmp
  env_dir="$(dirname "$ENV_FILE")"
  env_name="$(basename "$ENV_FILE")"
  tmp="$(mktemp "$env_dir/.${env_name}.${key}.XXXXXX")"
  chmod --reference="$ENV_FILE" "$tmp" 2>/dev/null || true
  chown --reference="$ENV_FILE" "$tmp" 2>/dev/null || true
  if ! awk -v key="$key" -v value="$value" '
    BEGIN { updated = 0 }
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" && updated == 0 {
      print key "=" value
      updated = 1
      next
    }
    { print }
    END {
      if (updated == 0) {
        print key "=" value
      }
    }
  ' "$ENV_FILE" > "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$ENV_FILE"
}

update_strategy_scope_value() {
  local key="$1"
  local current
  local updated

  env_key_exists "$key" || return 0
  current="$(read_env_value "$key")"
  updated="$(append_csv_name "$current" "$VALIDATED_STRATEGY")"
  if [[ "$updated" != "$current" ]]; then
    restore_env_on_error=true
    update_env_value "$key" "$updated"
    echo "$LOG_PREFIX $key: ${current:-none} -> $updated"
  else
    echo "$LOG_PREFIX $key already includes $VALIDATED_STRATEGY"
  fi
}

write_approval_marker() {
  python3 - \
    "$APPROVAL_MARKER" \
    "$VALIDATED_STRATEGY" \
    "$required_confirmation" \
    "$VALIDATION_SUMMARY" \
    "$VALIDATION_SUMMARY_SHA256" \
    "$STRATEGY_VERSION" \
    "$ENV_FILE" \
    "$VALIDATED_SCALE" \
    "$VALIDATED_TRADES" \
    "$VALIDATED_TOTAL_PNL" \
    "$VALIDATED_CI_LOW" \
    "$VALIDATED_P_MEAN_LE_ZERO" \
    "$PROOF_HORIZON_SUMMARY" \
    "$PROOF_HORIZON_SUMMARY_SHA256" \
    "$PROOF_HORIZON_TRADES" \
    "$PROOF_HORIZON_TOTAL_PNL" \
    "$PROOF_HORIZON_EVENTUAL_PASS_RATE" \
    "$PROOF_HORIZON_STARTS_PASSED" \
    "$PROOF_HORIZON_HISTORICAL_STARTS" \
    "$PROOF_HORIZON_SELECTION_REASON" \
    "$PROOF_HORIZON_CANDIDATE_COUNT" \
    "$PROOF_HORIZON_PASSING_CANDIDATE_COUNT" \
    "$EVIDENCE_ROOT" <<'PY'
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

marker = Path(sys.argv[1])
payload = {
    "schema_version": 3,
    "approved_at": datetime.now(timezone.utc).isoformat(),
    "strategy": sys.argv[2],
    "confirmation": sys.argv[3],
    "validation_summary": sys.argv[4],
    "validation_summary_sha256": sys.argv[5],
    "strategy_version": sys.argv[6],
    "env_file": sys.argv[7],
    "candidate_scale": sys.argv[8],
    "candidate_trades": int(sys.argv[9]),
    "candidate_total_pnl": float(sys.argv[10]),
    "candidate_ci_low": float(sys.argv[11]),
    "candidate_p_mean_le_zero": float(sys.argv[12]),
    "proof_horizon_summary": sys.argv[13],
    "proof_horizon_summary_sha256": sys.argv[14],
    "proof_horizon_trades": int(sys.argv[15]),
    "proof_horizon_total_pnl": float(sys.argv[16]),
    "proof_horizon_eventual_pass_rate": float(sys.argv[17]),
    "proof_horizon_starts_eventually_passed": int(sys.argv[18]),
    "proof_horizon_historical_starts": int(sys.argv[19]),
    "proof_horizon_selection_reason": sys.argv[20],
    "proof_horizon_candidate_count": int(sys.argv[21]),
    "proof_horizon_passing_candidate_count": int(sys.argv[22]),
    "evidence_root": str(Path(sys.argv[23]).resolve()),
}
marker.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(
    "w",
    encoding="utf-8",
    dir=str(marker.parent),
    delete=False,
) as tmp_file:
    json.dump(payload, tmp_file, indent=2, sort_keys=True)
    tmp_file.write("\n")
    tmp_path = tmp_file.name
os.replace(tmp_path, marker)
PY
}

if [[ "$APPROVAL_ONLY" == "true" ]]; then
  require_approval_marker_write_access
  if ! run_broker_flat_check; then
    echo "$LOG_PREFIX refusing approval marker because paper broker is not flat" >&2
    exit 1
  fi
  if ! verify_evidence_current; then
    echo "$LOG_PREFIX promotion evidence changed after broker flat check; aborting approval marker write" >&2
    exit 1
  fi
  require_candidate_decision_dry_run
  if ! write_approval_marker; then
    echo "$LOG_PREFIX failed to write approval marker" >&2
    exit 1
  fi
  echo "$LOG_PREFIX wrote approval marker only: $APPROVAL_MARKER"
  echo "$LOG_PREFIX approval marker recorded for $VALIDATED_STRATEGY from $VALIDATION_SUMMARY"
  exit 0
fi

require_promotion_write_access

if ! run_broker_flat_check; then
  echo "$LOG_PREFIX refusing promotion because paper broker is not flat" >&2
  exit 1
fi

if ! verify_evidence_current; then
  echo "$LOG_PREFIX promotion evidence changed after broker flat check; aborting promotion before mutation" >&2
  exit 1
fi

require_candidate_decision_dry_run

current_approved="$(read_env_value PAPER_APPROVED_STRATEGIES)"
new_approved="$(append_csv_name "$current_approved" "$VALIDATED_STRATEGY")"

backup_env="$(mktemp)"
cp "$ENV_FILE" "$backup_env"
backup_approval_marker="$(mktemp)"
approval_marker_existed=false
if [[ -f "$APPROVAL_MARKER" ]]; then
  cp "$APPROVAL_MARKER" "$backup_approval_marker"
  approval_marker_existed=true
fi
restore_env_on_error=false
restore_approval_marker_on_error=false
rollback_strategy_on_error=false
compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
cleanup() {
  if [[ "$rollback_strategy_on_error" == "true" ]]; then
    if ! "${compose[@]}" run -T --rm admin \
      disable-strategy "$VALIDATED_STRATEGY" \
      --mode paper \
      --strategy-version "$STRATEGY_VERSION"; then
      echo "$LOG_PREFIX failed to roll back strategy flag for $VALIDATED_STRATEGY" >&2
    fi
  fi
  if [[ "$restore_env_on_error" == "true" ]]; then
    cp "$backup_env" "$ENV_FILE"
  fi
  if [[ "$restore_approval_marker_on_error" == "true" ]]; then
    if [[ "$approval_marker_existed" == "true" ]]; then
      mkdir -p "$(dirname "$APPROVAL_MARKER")"
      cp "$backup_approval_marker" "$APPROVAL_MARKER"
    else
      rm -f "$APPROVAL_MARKER"
    fi
  fi
  rm -f "$backup_env"
  rm -f "$backup_approval_marker"
}
trap cleanup EXIT

if [[ "$new_approved" != "$current_approved" ]]; then
  restore_env_on_error=true
  update_env_value PAPER_APPROVED_STRATEGIES "$new_approved"
  echo "$LOG_PREFIX PAPER_APPROVED_STRATEGIES: ${current_approved:-none} -> $new_approved"
else
  echo "$LOG_PREFIX PAPER_APPROVED_STRATEGIES already includes $VALIDATED_STRATEGY"
fi

for scoped_strategy_key in "${PROMOTION_SCOPED_STRATEGY_KEYS[@]}"; do
  update_strategy_scope_value "$scoped_strategy_key"
done

if ! "${compose[@]}" run -T --rm admin \
  enable-strategy "$VALIDATED_STRATEGY" \
  --mode paper \
  --strategy-version "$STRATEGY_VERSION"; then
  echo "$LOG_PREFIX enable-strategy failed; restored env allowlist" >&2
  exit 1
fi
rollback_strategy_on_error=true

if ! write_approval_marker; then
  echo "$LOG_PREFIX failed to write approval marker; rolling back promotion" >&2
  exit 1
fi
restore_approval_marker_on_error=true

echo "$LOG_PREFIX enabled $VALIDATED_STRATEGY from $VALIDATION_SUMMARY"
if ! "$DEPLOY_SCRIPT" "$ENV_FILE"; then
  echo "$LOG_PREFIX deploy failed; rolling back env allowlist and strategy flag" >&2
  exit 1
fi
restore_env_on_error=false
restore_approval_marker_on_error=false
rollback_strategy_on_error=false
echo "$LOG_PREFIX wrote approval marker: $APPROVAL_MARKER"
echo "$LOG_PREFIX promotion complete for $VALIDATED_STRATEGY"
