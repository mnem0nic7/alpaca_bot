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
CONFIRMATION="${PROMOTE_VALIDATED_STRATEGY_CONFIRM:-}"
DRY_RUN="${PROMOTE_VALIDATED_STRATEGY_DRY_RUN:-true}"
APPROVAL_ONLY="${PROMOTE_VALIDATED_STRATEGY_APPROVAL_ONLY:-false}"
LOG_PREFIX="[promote_validated_strategy $(date -u '+%Y-%m-%dT%H:%M:%SZ')]"

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
    "$MIN_CANDIDATE_TRADES" <<'PY'
from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path

from alpaca_bot.strategy import OPTION_STRATEGY_NAMES, STRATEGY_REGISTRY


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def as_float(row: dict[str, object], key: str) -> float:
    value = row.get(key)
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} is not numeric") from exc


def as_int(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} is not an integer") from exc


root = Path(sys.argv[1])
requested_strategy = sys.argv[2].strip()
max_p_mean_le_zero = float(sys.argv[3])
min_candidate_trades = int(sys.argv[4])
summary_path = root / "latest_validation" / "summary.json"
if not summary_path.exists():
    fail(f"validation summary missing: {summary_path}")
summary_bytes = summary_path.read_bytes()
try:
    payload = json.loads(summary_bytes)
except json.JSONDecodeError as exc:
    fail(f"validation summary is not valid JSON: {exc}")

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

if requested_strategy and not passing_rows:
    detail = "; ".join(errors) if errors else "no matching candidate row"
    fail(f"no promotable validation row for {requested_strategy}: {detail}")
if not requested_strategy:
    unique_candidates = sorted({str(row.get("candidate")) for row in passing_rows})
    if len(unique_candidates) != 1:
        fail(
            "strategy name required because promotable candidates="
            + (",".join(unique_candidates) if unique_candidates else "none")
        )

selected = sorted(
    passing_rows,
    key=lambda row: (
        as_float(row, "candidate_ci_low"),
        -as_float(row, "candidate_p_mean_le_zero"),
        as_int(row, "candidate_trades"),
    ),
    reverse=True,
)[0]
strategy_name = str(selected["candidate"])
outputs = {
    "VALIDATED_STRATEGY": strategy_name,
    "VALIDATED_SCALE": str(selected.get("candidate_scale") or ""),
    "VALIDATED_TRADES": str(selected["candidate_trades"]),
    "VALIDATED_TOTAL_PNL": str(selected["candidate_total_pnl"]),
    "VALIDATED_CI_LOW": str(selected["candidate_ci_low"]),
    "VALIDATED_P_MEAN_LE_ZERO": str(selected["candidate_p_mean_le_zero"]),
    "VALIDATION_SUMMARY": str(summary_path.resolve()),
    "VALIDATION_SUMMARY_SHA256": hashlib.sha256(summary_bytes).hexdigest(),
}
for key, value in outputs.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"

eval "$validation_env"

required_confirmation="approve-${VALIDATED_STRATEGY}-paper-promotion-sha256-${VALIDATION_SUMMARY_SHA256}"
confirmation_status="missing"
if [[ -n "$CONFIRMATION" ]]; then
  if [[ "$CONFIRMATION" == "$required_confirmation" ]]; then
    confirmation_status="ok"
  else
    confirmation_status="mismatch"
  fi
fi

verify_validation_summary_current() {
  python3 - \
    "$EVIDENCE_ROOT" \
    "$VALIDATION_SUMMARY" \
    "$VALIDATION_SUMMARY_SHA256" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


root = Path(sys.argv[1])
expected_path = sys.argv[2]
expected_sha256 = sys.argv[3]
summary_path = root / "latest_validation" / "summary.json"
if not summary_path.exists():
    fail(f"validation summary missing: {summary_path}")
current_path = str(summary_path.resolve())
if current_path != expected_path:
    fail(f"validation summary changed: {current_path} != {expected_path}")
current_sha256 = hashlib.sha256(summary_path.read_bytes()).hexdigest()
if current_sha256 != expected_sha256:
    fail("validation summary hash changed")
PY
}

if ! verify_validation_summary_current; then
  echo "$LOG_PREFIX validation summary changed after evidence validation; aborting promotion" >&2
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

compact_dry_run_detail() {
  local value="$1"
  value="${value//$'\r'/ }"
  value="${value//$'\n'/; }"
  value="${value//$'\t'/ }"
  printf '%s\n' "$value"
}

if [[ "$DRY_RUN" == "true" ]]; then
  validation_current_status="ok"
  validation_current_detail="ok"
  if ! validation_current_detail="$(verify_validation_summary_current 2>&1)"; then
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
  printf '%s dry_run=true strategy=%s scale=%s trades=%s pnl=%s ci_low=%s p_mean_le_zero=%s validation_summary=%s validation_summary_sha256=%s confirmation_status=%s required_confirmation=%s validation_current_status=%s validation_current_detail=%s write_access_status=%s env_file_writable=%s env_dir_writable=%s approval_marker=%s approval_marker_writable=%s approval_marker_dir_writable=%s broker_flat_status=%s broker_flat_detail=%s\n' \
    "$LOG_PREFIX" \
    "$VALIDATED_STRATEGY" \
    "$VALIDATED_SCALE" \
    "$VALIDATED_TRADES" \
    "$VALIDATED_TOTAL_PNL" \
    "$VALIDATED_CI_LOW" \
    "$VALIDATED_P_MEAN_LE_ZERO" \
    "$VALIDATION_SUMMARY" \
    "$VALIDATION_SUMMARY_SHA256" \
    "$confirmation_status" \
    "$required_confirmation" \
    "$validation_current_status" \
    "${validation_current_detail:-ok}" \
    "$promotion_write_access_status" \
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
  if [[ "$validation_current_status" != "ok" || "$broker_flat_status" != "ok" ]]; then
    exit 1
  fi
  exit 0
fi

if [[ "$confirmation_status" != "ok" ]]; then
  echo "$LOG_PREFIX evidence validated for $VALIDATED_STRATEGY scale=$VALIDATED_SCALE trades=$VALIDATED_TRADES pnl=$VALIDATED_TOTAL_PNL ci_low=$VALIDATED_CI_LOW p_mean_le_zero=$VALIDATED_P_MEAN_LE_ZERO validation_summary=$VALIDATION_SUMMARY validation_summary_sha256=$VALIDATION_SUMMARY_SHA256" >&2
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
    "$VALIDATED_P_MEAN_LE_ZERO" <<'PY'
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

marker = Path(sys.argv[1])
payload = {
    "schema_version": 2,
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
  if ! verify_validation_summary_current; then
    echo "$LOG_PREFIX validation summary changed after broker flat check; aborting approval marker write" >&2
    exit 1
  fi
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

if ! verify_validation_summary_current; then
  echo "$LOG_PREFIX validation summary changed after broker flat check; aborting promotion before mutation" >&2
  exit 1
fi

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

for scoped_strategy_key in \
  PROFIT_PROBE_STRATEGIES \
  PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES \
  PAPER_READINESS_EXPECT_ENABLED_STRATEGIES \
  PAPER_ACTIVITY_STRATEGIES \
  SESSION_GUARD_STRATEGIES \
  PROOF_STATUS_APPROVED_STRATEGIES \
  DEPLOY_EXPECT_ENABLED_STRATEGIES \
  DEPLOY_DECISION_DRY_RUN_STRATEGIES; do
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
