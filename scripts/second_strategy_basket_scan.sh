#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-${SECOND_STRATEGY_ENV_FILE:-/etc/alpaca_bot/alpaca-bot.env}}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  echo "second strategy basket scan failed: $*" >&2
  exit 1
}

extract_field() {
  local line="$1"
  local key="$2"
  local part
  for part in $line; do
    case "$part" in
      "$key="*)
        printf '%s\n' "${part#*=}"
        return 0
        ;;
    esac
  done
  return 1
}

read_name_list() {
  local raw="${1//,/ }"
  local name
  for name in $raw; do
    [[ -n "$name" && "$name" != "none" ]] || continue
    printf '%s\n' "$name"
  done
}

read_validation_candidate_specs() {
  local raw="$1"
  local default_scale="$2"
  local spec
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    if [[ "$spec" == *"="* ]]; then
      printf '%s\t%s\n' "${spec%%=*}" "${spec#*=}"
    elif [[ "$spec" == *":"* ]]; then
      printf '%s\t%s\n' "${spec%%:*}" "${spec#*:}"
    else
      printf '%s\t%s\n' "$spec" "$default_scale"
    fi
  done < <(read_name_list "$raw")
}

[[ -f "$ENV_FILE" ]] || fail "missing env file: $ENV_FILE"

cd "$ROOT_DIR"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

SCENARIO_DIR="${SECOND_STRATEGY_SCENARIO_DIR:-/var/lib/alpaca-bot/nightly/scenarios}"
BASE_STRATEGY="${SECOND_STRATEGY_BASE_STRATEGY:-bull_flag}"
SAMPLE_SIZE="${SECOND_STRATEGY_SAMPLE_SIZE:-80}"
SAMPLE_SEED="${SECOND_STRATEGY_SAMPLE_SEED:-second-strategy-k1-giveback-refresh}"
SLIPPAGE_BPS="${SECOND_STRATEGY_SLIPPAGE_BPS:-${REPLAY_SLIPPAGE_BPS:-2}}"
MAX_OPEN_POSITIONS_VALUE="${SECOND_STRATEGY_MAX_OPEN_POSITIONS:-${MAX_OPEN_POSITIONS:-1}}"
CANDIDATE_SCALES="${SECOND_STRATEGY_CANDIDATE_SCALES:-${SECOND_STRATEGY_CANDIDATE_SCALE:-0.10,0.25,0.50}}"
OUTPUT_ROOT="${SECOND_STRATEGY_OUTPUT_ROOT:-/var/lib/alpaca-bot/nightly/second_strategy}"
OUTPUT_DIR="${SECOND_STRATEGY_OUTPUT_DIR:-$OUTPUT_ROOT/$(date -u +%Y%m%dT%H%M%SZ)}"
PREFILTER_SUMMARY_JSON="${SECOND_STRATEGY_PREFILTER_SUMMARY_JSON:-}"
LATEST_LINK="${SECOND_STRATEGY_LATEST_LINK:-}"
EXCLUDE_CANDIDATES="${SECOND_STRATEGY_EXCLUDE_CANDIDATES:-vwap_cross}"
VALIDATE_POSITIVES="${SECOND_STRATEGY_VALIDATE_POSITIVES:-true}"
VALIDATE_ALL_POSITIVE_ROWS="${SECOND_STRATEGY_VALIDATE_ALL_POSITIVE_ROWS:-true}"
VALIDATION_CANDIDATES="${SECOND_STRATEGY_VALIDATION_CANDIDATES:-}"
DEFAULT_VALIDATION_CANDIDATE_SCALE="${SECOND_STRATEGY_VALIDATION_CANDIDATE_SCALE:-${SECOND_STRATEGY_CANDIDATE_SCALE:-0.25}}"
MAX_VALIDATION_CANDIDATES="${SECOND_STRATEGY_MAX_VALIDATION_CANDIDATES:-0}"
SCAN_JOBS="${SECOND_STRATEGY_SCAN_JOBS:-2}"
VALIDATION_SAMPLE_SIZE="${SECOND_STRATEGY_VALIDATION_SAMPLE_SIZE:-160}"
VALIDATION_SAMPLE_SEED="${SECOND_STRATEGY_VALIDATION_SAMPLE_SEED:-second-strategy-independent-validation}"
VALIDATION_OUTPUT_DIR="${SECOND_STRATEGY_VALIDATION_OUTPUT_DIR:-$OUTPUT_DIR/validation}"
VALIDATION_LATEST_LINK="${SECOND_STRATEGY_VALIDATION_LATEST_LINK:-}"
INCLUDE_OPTION_CANDIDATES="${SECOND_STRATEGY_INCLUDE_OPTION_CANDIDATES:-auto}"
HOST_OPTION_CHAIN_SNAPSHOT_DIR="${SECOND_STRATEGY_HOST_OPTION_CHAIN_SNAPSHOT_DIR:-/var/lib/alpaca-bot/option-chain-snapshots}"
OPTION_CHAIN_SNAPSHOTS="${SECOND_STRATEGY_OPTION_CHAIN_SNAPSHOTS:-}"
if [[ -z "$OPTION_CHAIN_SNAPSHOTS" ]]; then
  OPTION_CHAIN_SNAPSHOTS="${OPTION_CHAIN_SNAPSHOT_DIR:-}"
  if [[ "$OPTION_CHAIN_SNAPSHOTS" == "/data/option-chain-snapshots" && -d "$HOST_OPTION_CHAIN_SNAPSHOT_DIR" ]]; then
    OPTION_CHAIN_SNAPSHOTS="$HOST_OPTION_CHAIN_SNAPSHOT_DIR"
  fi
fi

case "${INCLUDE_OPTION_CANDIDATES,,}" in
  true|1|yes|y)
    INCLUDE_OPTION_CANDIDATES=true
    ;;
  false|0|no|n|"")
    INCLUDE_OPTION_CANDIDATES=false
    ;;
  auto)
    INCLUDE_OPTION_CANDIDATES=auto
    ;;
  *)
    fail "SECOND_STRATEGY_INCLUDE_OPTION_CANDIDATES must be true, false, or auto"
    ;;
esac
case "${VALIDATE_ALL_POSITIVE_ROWS,,}" in
  true|1|yes|y)
    VALIDATE_ALL_POSITIVE_ROWS=true
    ;;
  false|0|no|n|"")
    VALIDATE_ALL_POSITIVE_ROWS=false
    ;;
  *)
    fail "SECOND_STRATEGY_VALIDATE_ALL_POSITIVE_ROWS must be true or false"
    ;;
esac

[[ -d "$SCENARIO_DIR" ]] || fail "missing scenario dir: $SCENARIO_DIR"
if [[ -n "$PREFILTER_SUMMARY_JSON" && ! -f "$PREFILTER_SUMMARY_JSON" ]]; then
  fail "missing prefilter summary JSON: $PREFILTER_SUMMARY_JSON"
fi
mkdir -p "$OUTPUT_DIR"

proof_output="$OUTPUT_DIR/proof_status.txt"
proof_status_loaded=false
load_proof_status() {
  local reason="$1"
  if [[ "$proof_status_loaded" == "true" ]]; then
    return 0
  fi
  echo "second strategy basket scan: $reason from proof status"
  if ! ./scripts/paper_proof_status.sh "$ENV_FILE" > "$proof_output"; then
    fail "could not load proof status; see $proof_output"
  fi
  proof_status_loaded=true
}

option_snapshot_path_has_files() {
  local path="$1"
  if [[ -f "$path" ]]; then
    [[ -s "$path" ]]
    return
  fi
  [[ -d "$path" ]] || return 1
  [[ -n "$(find "$path" -maxdepth 1 -type f -name 'option-chain-snapshots-*.jsonl' -size +0c -print -quit)" ]]
}

option_snapshot_contract_count() {
  local path="$1"
  python3 - "$path" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
latest_path = None
try:
    if path.is_file():
        if path.stat().st_size > 0:
            latest_path = path
    elif path.is_dir():
        files = []
        for file_path in path.glob("option-chain-snapshots-*.jsonl"):
            if not file_path.is_file():
                continue
            stat = file_path.stat()
            if stat.st_size > 0:
                files.append((stat.st_mtime, file_path))
        if files:
            latest_path = max(files, key=lambda item: item[0])[1]
except OSError:
    print(0)
    raise SystemExit(0)

total_contracts = 0
if latest_path is not None:
    expected_date = None
    prefix = "option-chain-snapshots-"
    if latest_path.stem.startswith(prefix):
        try:
            expected_date = datetime.fromisoformat(
                latest_path.stem.removeprefix(prefix)
            ).date()
        except ValueError:
            expected_date = None
    try:
        with latest_path.open(encoding="utf-8") as snapshot_file:
            for raw_line in snapshot_file:
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if expected_date is not None:
                    cycle_at = datetime.fromisoformat(str(payload["cycle_at"]))
                    if cycle_at.tzinfo is None:
                        cycle_at = cycle_at.replace(tzinfo=timezone.utc)
                    else:
                        cycle_at = cycle_at.astimezone(timezone.utc)
                    if cycle_at.date() != expected_date:
                        total_contracts = 0
                        break
                chains_by_symbol = payload.get("chains_by_symbol")
                if not isinstance(chains_by_symbol, dict):
                    continue
                total_contracts += sum(
                    len(contracts)
                    for contracts in chains_by_symbol.values()
                    if isinstance(contracts, list)
                )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        total_contracts = 0
print(total_contracts)
PY
}

OPTION_SNAPSHOT_CONTRACTS=0
if [[ -n "$OPTION_CHAIN_SNAPSHOTS" ]] && option_snapshot_path_has_files "$OPTION_CHAIN_SNAPSHOTS"; then
  OPTION_SNAPSHOT_CONTRACTS="$(option_snapshot_contract_count "$OPTION_CHAIN_SNAPSHOTS")"
fi
OPTION_REPLAY_STATUS=not_checked

if [[ "$INCLUDE_OPTION_CANDIDATES" == "auto" ]]; then
  if [[ -n "$OPTION_CHAIN_SNAPSHOTS" ]] && option_snapshot_path_has_files "$OPTION_CHAIN_SNAPSHOTS"; then
    load_proof_status "checking option snapshot replay support"
    option_snapshot_line="$(grep -E '^paper proof option snapshots: ' "$proof_output" | tail -n 1 || true)"
    if [[ -n "$option_snapshot_line" ]]; then
      OPTION_REPLAY_STATUS="$(extract_field "$option_snapshot_line" "replay_status" || true)"
      OPTION_REPLAY_STATUS="${OPTION_REPLAY_STATUS:-unknown}"
    else
      OPTION_REPLAY_STATUS=missing
    fi
  fi
  if [[ "$OPTION_REPLAY_STATUS" == "supported" \
    && "$OPTION_SNAPSHOT_CONTRACTS" =~ ^[0-9]+$ \
    && "$OPTION_SNAPSHOT_CONTRACTS" -gt 0 ]]; then
    INCLUDE_OPTION_CANDIDATES=true
  else
    INCLUDE_OPTION_CANDIDATES=false
  fi
fi

is_option_candidate() {
  local candidate="$1"
  local option_candidate
  for option_candidate in "${option_candidates[@]}"; do
    if [[ "$candidate" == "$option_candidate" ]]; then
      return 0
    fi
  done
  return 1
}

option_candidate_csv=""
if [[ "$INCLUDE_OPTION_CANDIDATES" == "true" ]]; then
  option_candidate_csv="${SECOND_STRATEGY_OPTION_CANDIDATES:-}"
fi
if [[ -n "$PREFILTER_SUMMARY_JSON" ]]; then
  candidates=()
  if [[ "$INCLUDE_OPTION_CANDIDATES" == "true" && -z "$option_candidate_csv" ]]; then
    load_proof_status "discovering disabled option candidates"
    diversification_line="$(grep -E '^paper proof strategy diversification: ' "$proof_output" | tail -n 1 || true)"
    [[ -n "$diversification_line" ]] || fail "proof status did not print strategy diversification details"
    option_candidate_csv="$(extract_field "$diversification_line" "option_gated_disabled_candidate_names" || true)"
  fi
elif [[ -n "${SECOND_STRATEGY_CANDIDATES:-}" ]]; then
  mapfile -t candidates < <(read_name_list "$SECOND_STRATEGY_CANDIDATES")
  if [[ "$INCLUDE_OPTION_CANDIDATES" == "true" && -z "$option_candidate_csv" ]]; then
    load_proof_status "discovering disabled option candidates"
    diversification_line="$(grep -E '^paper proof strategy diversification: ' "$proof_output" | tail -n 1 || true)"
    [[ -n "$diversification_line" ]] || fail "proof status did not print strategy diversification details"
    option_candidate_csv="$(extract_field "$diversification_line" "option_gated_disabled_candidate_names" || true)"
  fi
else
  load_proof_status "discovering disabled candidate strategies"
  diversification_line="$(grep -E '^paper proof strategy diversification: ' "$proof_output" | tail -n 1 || true)"
  [[ -n "$diversification_line" ]] || fail "proof status did not print strategy diversification details"
  candidate_csv="$(extract_field "$diversification_line" "stock_disabled_candidate_names" || true)"
  mapfile -t candidates < <(read_name_list "$candidate_csv")
  if [[ "$INCLUDE_OPTION_CANDIDATES" == "true" ]]; then
    option_candidate_csv="$(extract_field "$diversification_line" "option_gated_disabled_candidate_names" || true)"
    mapfile -t discovered_option_candidates < <(read_name_list "$option_candidate_csv")
    candidates+=("${discovered_option_candidates[@]}")
  fi
fi
mapfile -t option_candidates < <(read_name_list "$option_candidate_csv")

if [[ "$INCLUDE_OPTION_CANDIDATES" == "true" ]]; then
  [[ -n "$OPTION_CHAIN_SNAPSHOTS" ]] || fail "SECOND_STRATEGY_OPTION_CHAIN_SNAPSHOTS or OPTION_CHAIN_SNAPSHOT_DIR is required when option candidates are included"
  option_snapshot_path_has_files "$OPTION_CHAIN_SNAPSHOTS" || fail "option-chain snapshot path is empty or missing: $OPTION_CHAIN_SNAPSHOTS"
  [[ "$OPTION_SNAPSHOT_CONTRACTS" =~ ^[0-9]+$ && "$OPTION_SNAPSHOT_CONTRACTS" -gt 0 ]] || fail "option-chain snapshot path has no replayable contracts: $OPTION_CHAIN_SNAPSHOTS"
fi

mapfile -t excluded_candidates < <(read_name_list "$EXCLUDE_CANDIDATES")
if [[ "${#excluded_candidates[@]}" -gt 0 ]]; then
  filtered_candidates=()
  skipped_candidates=()
  for candidate in "${candidates[@]}"; do
    skip_candidate=false
    for excluded_candidate in "${excluded_candidates[@]}"; do
      if [[ "$candidate" == "$excluded_candidate" ]]; then
        skip_candidate=true
        break
      fi
    done
    if [[ "$skip_candidate" == "true" ]]; then
      skipped_candidates+=("$candidate")
    else
      filtered_candidates+=("$candidate")
    fi
  done
  candidates=("${filtered_candidates[@]}")
else
  skipped_candidates=()
fi

if [[ -z "$PREFILTER_SUMMARY_JSON" ]]; then
  [[ "${#candidates[@]}" -gt 0 ]] || fail "no candidate strategies to scan"
fi
mapfile -t candidate_scales < <(read_name_list "$CANDIDATE_SCALES")
[[ "${#candidate_scales[@]}" -gt 0 ]] || fail "no candidate scales to scan"
python3 - "${candidate_scales[@]}" <<'PY' || fail "invalid candidate scale list: $CANDIDATE_SCALES"
from __future__ import annotations

import sys

for raw in sys.argv[1:]:
    try:
        scale = float(raw)
    except ValueError as exc:
        raise SystemExit(f"candidate scale must be numeric: {raw}") from exc
    if not 0.0 < scale <= 1.0:
        raise SystemExit(f"candidate scale must be in (0.0, 1.0]: {raw}")
PY
python3 - "$DEFAULT_VALIDATION_CANDIDATE_SCALE" "$MAX_VALIDATION_CANDIDATES" <<'PY' || fail "invalid validation scale or cap"
from __future__ import annotations

import sys

try:
    scale = float(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"default validation scale must be numeric: {sys.argv[1]}") from exc
if not 0.0 < scale <= 1.0:
    raise SystemExit(f"default validation scale must be in (0.0, 1.0]: {sys.argv[1]}")
try:
    cap = int(sys.argv[2])
except ValueError as exc:
    raise SystemExit(f"max validation candidates must be an integer: {sys.argv[2]}") from exc
if cap < 0:
    raise SystemExit(f"max validation candidates must be non-negative: {sys.argv[2]}")
PY
python3 - "$SCAN_JOBS" <<'PY' || fail "invalid scan job count"
from __future__ import annotations

import sys

try:
    jobs = int(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"scan jobs must be an integer: {sys.argv[1]}") from exc
if jobs < 1:
    raise SystemExit(f"scan jobs must be at least 1: {sys.argv[1]}")
PY

starting_equity="${SECOND_STRATEGY_STARTING_EQUITY:-}"
if [[ -z "$starting_equity" ]]; then
  load_proof_status "loading live broker equity"
  broker_line="$(grep -E '^paper proof broker account: ' "$proof_output" | tail -n 1 || true)"
  starting_equity="$(extract_field "$broker_line" "equity" || true)"
fi

status_file="$OUTPUT_DIR/status.tsv"
summary_file="$OUTPUT_DIR/summary.md"
summary_json_file="$OUTPUT_DIR/summary.json"
prefilter_skipped=false
if [[ -n "$PREFILTER_SUMMARY_JSON" ]]; then
  summary_json_file="$PREFILTER_SUMMARY_JSON"
  prefilter_skipped=true
fi
status_parts_dir="$OUTPUT_DIR/status_parts"
: > "$status_file"
mkdir -p "$status_parts_dir"

echo "second strategy basket scan: output_dir=$OUTPUT_DIR"
echo "second strategy basket scan: scenario_dir=$SCENARIO_DIR base=$BASE_STRATEGY sample_size=$SAMPLE_SIZE sample_seed=$SAMPLE_SEED slippage_bps=$SLIPPAGE_BPS max_open_positions=$MAX_OPEN_POSITIONS_VALUE candidate_scales=${candidate_scales[*]} scan_jobs=$SCAN_JOBS starting_equity=${starting_equity:-scenario_default} excluded_candidates=${skipped_candidates[*]:-none} include_option_candidates=$INCLUDE_OPTION_CANDIDATES option_chain_snapshots=${OPTION_CHAIN_SNAPSHOTS:-none} option_snapshot_contracts=$OPTION_SNAPSHOT_CONTRACTS option_replay_status=$OPTION_REPLAY_STATUS prefilter_summary_json=${PREFILTER_SUMMARY_JSON:-none}"

failed_count=0
run_prefilter_job() {
  local candidate="$1"
  local candidate_scale="$2"
  local safe_candidate
  local safe_scale
  local report_path
  local jsonl_path
  local stderr_path
  local status_part
  local -a cmd

  safe_candidate="$(printf '%s' "$candidate" | tr -c 'A-Za-z0-9_' '_')"
  safe_scale="$(printf '%s' "$candidate_scale" | tr -c 'A-Za-z0-9_' '_')"
  report_path="$OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_basket.md"
  jsonl_path="$OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_basket.jsonl"
  stderr_path="$OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_basket.stderr"
  status_part="$status_parts_dir/${safe_candidate}_scale_${safe_scale}.tsv"
  cmd=(
    python3 -m alpaca_bot.replay.cli portfolio-basket-audit
    --scenario-dir "$SCENARIO_DIR"
    --strategy "$BASE_STRATEGY"
    --strategy "$candidate"
    --sample-size "$SAMPLE_SIZE"
    --sample-seed "$SAMPLE_SEED"
    --slippage-bps "$SLIPPAGE_BPS"
    --max-open-positions "$MAX_OPEN_POSITIONS_VALUE"
    --confidence-scale "$candidate=$candidate_scale"
    --output "$report_path"
    --jsonl "$jsonl_path"
  )
  if [[ -n "$starting_equity" && "$starting_equity" != "none" ]]; then
    cmd+=(--starting-equity "$starting_equity")
  fi
  if is_option_candidate "$candidate"; then
    cmd+=(--option-chain-snapshots "$OPTION_CHAIN_SNAPSHOTS")
  fi

  echo "second strategy basket scan: candidate=$candidate scale=$candidate_scale"
  if "${cmd[@]}" 2> "$stderr_path"; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$candidate_scale" "passed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
    return 0
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$candidate_scale" "failed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
  return 1
}

wait_for_next_prefilter_job() {
  if ! wait -n; then
    failed_count=$((failed_count + 1))
  fi
  prefilter_running_jobs=$((prefilter_running_jobs - 1))
}

if [[ "$prefilter_skipped" == "true" ]]; then
  echo "second strategy basket scan: using existing prefilter_summary_json=$summary_json_file"
else
  prefilter_running_jobs=0
  for candidate in "${candidates[@]}"; do
    if [[ "$candidate" == "$BASE_STRATEGY" ]]; then
      continue
    fi
    for candidate_scale in "${candidate_scales[@]}"; do
      run_prefilter_job "$candidate" "$candidate_scale" &
      prefilter_running_jobs=$((prefilter_running_jobs + 1))
      if [[ "$prefilter_running_jobs" -ge "$SCAN_JOBS" ]]; then
        wait_for_next_prefilter_job
      fi
    done
  done
  while [[ "$prefilter_running_jobs" -gt 0 ]]; do
    wait_for_next_prefilter_job
  done
  for status_part in "$status_parts_dir"/*.tsv; do
    [[ -e "$status_part" ]] || continue
    cat "$status_part" >> "$status_file"
  done

  python3 - "$status_file" "$summary_file" "$summary_json_file" \
    "$SCENARIO_DIR" "$BASE_STRATEGY" "$SAMPLE_SIZE" "$SAMPLE_SEED" \
    "$SLIPPAGE_BPS" "$MAX_OPEN_POSITIONS_VALUE" "${candidate_scales[*]}" \
    "${starting_equity:-scenario_default}" "${skipped_candidates[*]:-none}" \
    "$SCAN_JOBS" "$INCLUDE_OPTION_CANDIDATES" "${OPTION_CHAIN_SNAPSHOTS:-none}" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

status_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
summary_json_path = Path(sys.argv[3])
scenario_dir = sys.argv[4]
base_strategy = sys.argv[5]
sample_size = sys.argv[6]
sample_seed = sys.argv[7]
slippage_bps = sys.argv[8]
max_open_positions = sys.argv[9]
candidate_scales = sys.argv[10]
starting_equity = sys.argv[11]
excluded_candidates = sys.argv[12]
scan_jobs = sys.argv[13]
include_option_candidates = sys.argv[14]
option_chain_snapshots = sys.argv[15]


def fmt(value, spec: str = ".2f") -> str:
    if value is None:
        return "n/a"
    return format(float(value), spec)


rows = []
json_rows = []
for raw in status_path.read_text().splitlines():
    if not raw.strip():
        continue
    candidate, candidate_scale, status, report, jsonl, stderr = raw.split("\t")
    audit_row = None
    if status == "passed" and Path(jsonl).exists():
        payloads = [
            json.loads(line)
            for line in Path(jsonl).read_text().splitlines()
            if line.strip()
        ]
        if payloads and payloads[-1].get("rows"):
            audit_row = payloads[-1]["rows"][0]
    rows.append((candidate, candidate_scale, status, report, stderr, audit_row))


def sort_key(item):
    candidate, candidate_scale, status, _report, _stderr, audit_row = item
    if status != "passed" or audit_row is None:
        return (3, 0.0, candidate, float(candidate_scale))
    verdict_rank = {
        "positive-edge": 0,
        "no-evidence": 1,
        "insufficient-data": 2,
        "negative-edge": 2,
    }.get(audit_row.get("verdict"), 2)
    ci_low = audit_row.get("ci_low")
    ci_rank = -(float(ci_low) if ci_low is not None else float("-inf"))
    return (verdict_rank, ci_rank, candidate, float(candidate_scale))


lines = [
    "# Second strategy basket scan",
    "",
    "Run metadata:",
    "",
    f"- scenario_dir: `{scenario_dir}`",
    f"- base_strategy: `{base_strategy}`",
    f"- sample_size: `{sample_size}`",
    f"- sample_seed: `{sample_seed}`",
    f"- slippage_bps: `{slippage_bps}`",
    f"- max_open_positions: `{max_open_positions}`",
    f"- candidate_scales: `{candidate_scales}`",
    f"- scan_jobs: `{scan_jobs}`",
    f"- starting_equity: `{starting_equity}`",
    f"- excluded_candidates: `{excluded_candidates}`",
    f"- include_option_candidates: `{include_option_candidates}`",
    f"- option_chain_snapshots: `{option_chain_snapshots}`",
    "",
    "| candidate | scale | status | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict | report |",
    "|---|---:|---|---:|---:|---:|---:|---|---:|---:|---|---|",
]

positive_edges = 0
for candidate, candidate_scale, status, report, stderr, audit_row in sorted(rows, key=sort_key):
    if status != "passed" or audit_row is None:
        json_rows.append(
            {
                "candidate": candidate,
                "candidate_scale": candidate_scale,
                "status": status,
                "report": report,
                "stderr": stderr,
                "verdict": None,
            }
        )
        lines.append(
            f"| `{candidate}` | {candidate_scale} | `{status}` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `{stderr}` |"
        )
        continue
    verdict = audit_row["verdict"]
    if verdict == "positive-edge":
        positive_edges += 1
    ci = (
        "n/a"
        if audit_row["ci_low"] is None or audit_row["ci_high"] is None
        else f"[{fmt(audit_row['ci_low'], '.4f')}, {fmt(audit_row['ci_high'], '.4f')}]"
    )
    p_mean_le_zero = audit_row["p_positive"]
    json_rows.append(
        {
            "candidate": candidate,
            "candidate_scale": candidate_scale,
            "status": "passed",
            "report": report,
            "stderr": stderr,
            "trades": audit_row["trades"],
            "profit_factor": audit_row["profit_factor"],
            "total_pnl": audit_row["total_pnl"],
            "mean_trade_pnl": audit_row["mean_trade_pnl"],
            "ci_low": audit_row["ci_low"],
            "ci_high": audit_row["ci_high"],
            "p_mean_le_zero": p_mean_le_zero,
            "cost_drag": audit_row["cost_drag"],
            "verdict": verdict,
        }
    )
    lines.append(
        "| "
        f"`{candidate}` | {candidate_scale} | `passed` | {audit_row['trades']} | "
        f"{fmt(audit_row['profit_factor'])} | {fmt(audit_row['total_pnl'])} | "
        f"{fmt(audit_row['mean_trade_pnl'], '.4f')} | {ci} | "
        f"{fmt(p_mean_le_zero, '.4f')} | {fmt(audit_row['cost_drag'])} | "
        f"`{verdict}` | `{report}` |"
    )

lines.extend([
    "",
    (
        "Promotion note: a positive prefilter row is only a survivor for a "
        "separate independent validation; it is not approval to change "
        "PAPER_APPROVED_STRATEGIES."
    ),
])
summary_path.write_text("\n".join(lines) + "\n")
summary_json_path.write_text(
    json.dumps(
        {
            "scenario_dir": scenario_dir,
            "base_strategy": base_strategy,
            "sample_size": sample_size,
            "sample_seed": sample_seed,
            "slippage_bps": slippage_bps,
            "max_open_positions": max_open_positions,
            "candidate_scales": candidate_scales,
            "scan_jobs": scan_jobs,
            "starting_equity": starting_equity,
            "excluded_candidates": excluded_candidates,
            "include_option_candidates": include_option_candidates,
            "option_chain_snapshots": option_chain_snapshots,
            "positive_edge_prefilter_rows": positive_edges,
            "rows": json_rows,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
print("\n".join(lines))
print(f"summary={summary_path}")
print(f"summary_json={summary_json_path}")
print(f"positive_edge_prefilter_rows={positive_edges}")
PY
fi

validation_failed_count=0
if [[ "${VALIDATE_POSITIVES,,}" == "true" ]]; then
  validation_specs_file="$VALIDATION_OUTPUT_DIR/candidates.tsv"
  if [[ -n "$VALIDATION_CANDIDATES" ]]; then
    mkdir -p "$VALIDATION_OUTPUT_DIR"
    read_validation_candidate_specs "$VALIDATION_CANDIDATES" "$DEFAULT_VALIDATION_CANDIDATE_SCALE" > "$validation_specs_file"
  else
    mkdir -p "$VALIDATION_OUTPUT_DIR"
    python3 - "$summary_json_file" "$validation_specs_file" \
      "$MAX_VALIDATION_CANDIDATES" "$VALIDATE_ALL_POSITIVE_ROWS" <<'PY'
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

payload = json.loads(Path(sys.argv[1]).read_text())
output_path = Path(sys.argv[2])
max_validation_candidates = int(sys.argv[3])
validate_all_positive_rows = sys.argv[4].lower() == "true"

best_by_candidate = {}
positive_rows = []
for row in payload.get("rows", []):
    if row.get("status") == "passed" and row.get("verdict") == "positive-edge":
        ci_low = row.get("ci_low")
        p_mean_le_zero = row.get("p_mean_le_zero")
        total_pnl = row.get("total_pnl")
        score = (
            float(ci_low) if ci_low is not None else -math.inf,
            -(float(p_mean_le_zero) if p_mean_le_zero is not None else math.inf),
            float(total_pnl) if total_pnl is not None else -math.inf,
        )
        positive_rows.append((score, row))
        current = best_by_candidate.get(row["candidate"])
        if current is None or score > current[0]:
            best_by_candidate[row["candidate"]] = (score, row)

if validate_all_positive_rows:
    selected = [
        item[1]
        for item in sorted(
            positive_rows,
            key=lambda item: (
                item[0][0],
                item[0][1],
                item[0][2],
                item[1]["candidate"],
                float(item[1]["candidate_scale"]),
            ),
            reverse=True,
        )
    ]
else:
    selected = [
        item[1]
        for item in sorted(
            best_by_candidate.values(),
            key=lambda item: (item[0][0], item[0][1], item[0][2], item[1]["candidate"]),
            reverse=True,
        )
    ]
if max_validation_candidates > 0:
    selected = selected[:max_validation_candidates]
output_path.write_text(
    "".join(
        f"{row['candidate']}\t{row['candidate_scale']}\n"
        for row in selected
    )
)
PY
  fi

  validation_status_file="$VALIDATION_OUTPUT_DIR/status.tsv"
  validation_summary_file="$VALIDATION_OUTPUT_DIR/summary.md"
  validation_summary_json_file="$VALIDATION_OUTPUT_DIR/summary.json"
  validation_status_parts_dir="$VALIDATION_OUTPUT_DIR/status_parts"
  : > "$validation_status_file"
  mkdir -p "$validation_status_parts_dir"

  validation_spec_count="$(wc -l < "$validation_specs_file" | tr -d ' ')"
  echo "second strategy basket validation: output_dir=$VALIDATION_OUTPUT_DIR candidates=$validation_spec_count sample_size=$VALIDATION_SAMPLE_SIZE sample_seed=$VALIDATION_SAMPLE_SEED scan_jobs=$SCAN_JOBS validate_all_positive_rows=$VALIDATE_ALL_POSITIVE_ROWS"

  run_validation_job() {
    local candidate="$1"
    local candidate_scale="$2"
    local safe_candidate
    local safe_scale
    local report_path
    local jsonl_path
    local stderr_path
    local status_part
    local -a cmd

    safe_candidate="$(printf '%s' "$candidate" | tr -c 'A-Za-z0-9_' '_')"
    safe_scale="$(printf '%s' "$candidate_scale" | tr -c 'A-Za-z0-9_' '_')"
    report_path="$VALIDATION_OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_validation.md"
    jsonl_path="$VALIDATION_OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_validation.jsonl"
    stderr_path="$VALIDATION_OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_validation.stderr"
    status_part="$validation_status_parts_dir/${safe_candidate}_scale_${safe_scale}.tsv"
    cmd=(
      python3 -m alpaca_bot.replay.cli portfolio-basket-audit
      --scenario-dir "$SCENARIO_DIR"
      --strategy "$BASE_STRATEGY"
      --strategy "$candidate"
      --sample-size "$VALIDATION_SAMPLE_SIZE"
      --sample-seed "$VALIDATION_SAMPLE_SEED"
      --slippage-bps "$SLIPPAGE_BPS"
      --max-open-positions "$MAX_OPEN_POSITIONS_VALUE"
      --confidence-scale "$candidate=$candidate_scale"
      --output "$report_path"
      --jsonl "$jsonl_path"
    )
    if [[ -n "$starting_equity" && "$starting_equity" != "none" ]]; then
      cmd+=(--starting-equity "$starting_equity")
    fi
    if is_option_candidate "$candidate"; then
      cmd+=(--option-chain-snapshots "$OPTION_CHAIN_SNAPSHOTS")
    fi

    echo "second strategy basket validation: candidate=$candidate scale=$candidate_scale"
    if "${cmd[@]}" 2> "$stderr_path"; then
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$candidate_scale" "passed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
      return 0
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$candidate_scale" "failed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
    return 1
  }

  wait_for_next_validation_job() {
    if ! wait -n; then
      validation_failed_count=$((validation_failed_count + 1))
    fi
    validation_running_jobs=$((validation_running_jobs - 1))
  }

  validation_running_jobs=0
  while IFS=$'\t' read -r candidate candidate_scale; do
    [[ -n "$candidate" && -n "$candidate_scale" ]] || continue
    run_validation_job "$candidate" "$candidate_scale" &
    validation_running_jobs=$((validation_running_jobs + 1))
    if [[ "$validation_running_jobs" -ge "$SCAN_JOBS" ]]; then
      wait_for_next_validation_job
    fi
  done < "$validation_specs_file"
  while [[ "$validation_running_jobs" -gt 0 ]]; do
    wait_for_next_validation_job
  done
  for status_part in "$validation_status_parts_dir"/*.tsv; do
    [[ -e "$status_part" ]] || continue
    cat "$status_part" >> "$validation_status_file"
  done

  python3 - "$validation_status_file" "$validation_summary_file" \
    "$validation_summary_json_file" "$summary_json_file" "$VALIDATION_OUTPUT_DIR" \
    "$SCENARIO_DIR" "$BASE_STRATEGY" "$VALIDATION_SAMPLE_SIZE" \
    "$VALIDATION_SAMPLE_SEED" "$SLIPPAGE_BPS" "$MAX_OPEN_POSITIONS_VALUE" \
    "${starting_equity:-scenario_default}" "${skipped_candidates[*]:-none}" \
    "$MAX_VALIDATION_CANDIDATES" "$SCAN_JOBS" "$INCLUDE_OPTION_CANDIDATES" \
    "${OPTION_CHAIN_SNAPSHOTS:-none}" "$VALIDATE_ALL_POSITIVE_ROWS" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

status_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
summary_json_path = Path(sys.argv[3])
prefilter_summary_json_path = Path(sys.argv[4])
validation_output_dir = sys.argv[5]
scenario_dir = sys.argv[6]
base_strategy = sys.argv[7]
sample_size = sys.argv[8]
sample_seed = sys.argv[9]
slippage_bps = sys.argv[10]
max_open_positions = sys.argv[11]
starting_equity = sys.argv[12]
excluded_candidates = sys.argv[13]
max_validation_candidates = sys.argv[14]
scan_jobs = sys.argv[15]
include_option_candidates = sys.argv[16]
option_chain_snapshots = sys.argv[17]
validate_all_positive_rows = sys.argv[18]


def fmt(value, spec: str = ".2f") -> str:
    if value is None:
        return "n/a"
    return format(float(value), spec)


rows = []
json_rows = []
for raw in status_path.read_text().splitlines():
    if not raw.strip():
        continue
    candidate, candidate_scale, status, report, jsonl, stderr = raw.split("\t")
    audit_row = None
    if status == "passed" and Path(jsonl).exists():
        payloads = [
            json.loads(line)
            for line in Path(jsonl).read_text().splitlines()
            if line.strip()
        ]
        if payloads and payloads[-1].get("rows"):
            audit_row = payloads[-1]["rows"][0]
    rows.append((candidate, candidate_scale, status, report, stderr, audit_row))


def sort_key(item):
    candidate, candidate_scale, status, _report, _stderr, audit_row = item
    if status != "passed" or audit_row is None:
        return (3, 0.0, candidate, float(candidate_scale))
    verdict_rank = {
        "positive-edge": 0,
        "no-evidence": 1,
        "insufficient-data": 2,
        "negative-edge": 2,
    }.get(audit_row.get("verdict"), 2)
    ci_low = audit_row.get("ci_low")
    ci_rank = -(float(ci_low) if ci_low is not None else float("-inf"))
    return (verdict_rank, ci_rank, candidate, float(candidate_scale))


lines = [
    "# Second strategy independent validation",
    "",
    "Run metadata:",
    "",
    f"- prefilter_summary_json: `{prefilter_summary_json_path}`",
    f"- validation_output_dir: `{validation_output_dir}`",
    f"- scenario_dir: `{scenario_dir}`",
    f"- base_strategy: `{base_strategy}`",
    f"- sample_size: `{sample_size}`",
    f"- sample_seed: `{sample_seed}`",
    f"- slippage_bps: `{slippage_bps}`",
    f"- max_open_positions: `{max_open_positions}`",
    f"- max_validation_candidates: `{max_validation_candidates}`",
    f"- scan_jobs: `{scan_jobs}`",
    f"- starting_equity: `{starting_equity}`",
    f"- excluded_candidates: `{excluded_candidates}`",
    f"- include_option_candidates: `{include_option_candidates}`",
    f"- option_chain_snapshots: `{option_chain_snapshots}`",
    f"- validate_all_positive_rows: `{validate_all_positive_rows}`",
    "",
    "| candidate | scale | status | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict | report |",
    "|---|---:|---|---:|---:|---:|---:|---|---:|---:|---|---|",
]

validation_positive_edges = 0
for candidate, candidate_scale, status, report, stderr, audit_row in sorted(rows, key=sort_key):
    if status != "passed" or audit_row is None:
        json_rows.append(
            {
                "candidate": candidate,
                "candidate_scale": candidate_scale,
                "status": status,
                "report": report,
                "stderr": stderr,
                "verdict": None,
            }
        )
        lines.append(
            f"| `{candidate}` | {candidate_scale} | `{status}` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `{stderr}` |"
        )
        continue
    verdict = audit_row["verdict"]
    if verdict == "positive-edge":
        validation_positive_edges += 1
    ci = (
        "n/a"
        if audit_row["ci_low"] is None or audit_row["ci_high"] is None
        else f"[{fmt(audit_row['ci_low'], '.4f')}, {fmt(audit_row['ci_high'], '.4f')}]"
    )
    p_mean_le_zero = audit_row["p_positive"]
    json_rows.append(
        {
            "candidate": candidate,
            "candidate_scale": candidate_scale,
            "status": "passed",
            "report": report,
            "stderr": stderr,
            "trades": audit_row["trades"],
            "profit_factor": audit_row["profit_factor"],
            "total_pnl": audit_row["total_pnl"],
            "mean_trade_pnl": audit_row["mean_trade_pnl"],
            "ci_low": audit_row["ci_low"],
            "ci_high": audit_row["ci_high"],
            "p_mean_le_zero": p_mean_le_zero,
            "zero_cost_total_pnl": audit_row.get("zero_cost_total_pnl"),
            "cost_drag": audit_row["cost_drag"],
            "verdict": verdict,
        }
    )
    lines.append(
        "| "
        f"`{candidate}` | {candidate_scale} | `passed` | {audit_row['trades']} | "
        f"{fmt(audit_row['profit_factor'])} | {fmt(audit_row['total_pnl'])} | "
        f"{fmt(audit_row['mean_trade_pnl'], '.4f')} | {ci} | "
        f"{fmt(p_mean_le_zero, '.4f')} | {fmt(audit_row['cost_drag'])} | "
        f"`{verdict}` | `{report}` |"
    )

if not rows:
    lines.append("| `none` | n/a | `skipped` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |")

if validation_positive_edges:
    conclusion = (
        "Validation found positive-edge survivor(s). This is still not approval "
        "to change PAPER_APPROVED_STRATEGIES; promote only through an explicit "
        "operator-reviewed paper approval."
    )
else:
    conclusion = (
        "No candidate from this batch is approved for paper promotion; every "
        "independently validated survivor returned no positive validation edge."
    )

lines.extend(["", conclusion])
summary_path.write_text("\n".join(lines) + "\n")
summary_json_path.write_text(
    json.dumps(
        {
            "prefilter_summary_json": str(prefilter_summary_json_path),
            "validation_output_dir": validation_output_dir,
            "scenario_dir": scenario_dir,
            "base_strategy": base_strategy,
            "sample_size": sample_size,
            "sample_seed": sample_seed,
            "slippage_bps": slippage_bps,
            "max_open_positions": max_open_positions,
            "max_validation_candidates": max_validation_candidates,
            "scan_jobs": scan_jobs,
            "starting_equity": starting_equity,
            "excluded_candidates": excluded_candidates,
            "include_option_candidates": include_option_candidates,
            "option_chain_snapshots": option_chain_snapshots,
            "validate_all_positive_rows": validate_all_positive_rows,
            "positive_edge_validation_rows": validation_positive_edges,
            "promotion_approved": False,
            "conclusion": conclusion,
            "rows": json_rows,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
print("\n".join(lines))
print(f"validation_summary={summary_path}")
print(f"validation_summary_json={summary_json_path}")
print(f"positive_edge_validation_rows={validation_positive_edges}")
PY

  if [[ -z "$VALIDATION_LATEST_LINK" && -z "${SECOND_STRATEGY_OUTPUT_DIR:-}" ]]; then
    VALIDATION_LATEST_LINK="$OUTPUT_ROOT/latest_validation"
  fi
  if [[ -n "$VALIDATION_LATEST_LINK" ]]; then
    mkdir -p "$(dirname "$VALIDATION_LATEST_LINK")"
    ln -sfn "$VALIDATION_OUTPUT_DIR" "$VALIDATION_LATEST_LINK"
    echo "latest_validation=$VALIDATION_LATEST_LINK"
  fi
else
  echo "second strategy basket validation: disabled"
fi

if [[ "$prefilter_skipped" != "true" && -z "$LATEST_LINK" && -z "${SECOND_STRATEGY_OUTPUT_DIR:-}" ]]; then
  LATEST_LINK="$OUTPUT_ROOT/latest"
fi
if [[ "$prefilter_skipped" != "true" && -n "$LATEST_LINK" ]]; then
  mkdir -p "$(dirname "$LATEST_LINK")"
  ln -sfn "$OUTPUT_DIR" "$LATEST_LINK"
  echo "latest=$LATEST_LINK"
fi

if [[ "$failed_count" -gt 0 ]]; then
  fail "$failed_count candidate scan command(s) failed; see $OUTPUT_DIR"
fi
if [[ "$validation_failed_count" -gt 0 ]]; then
  fail "$validation_failed_count validation command(s) failed; see $VALIDATION_OUTPUT_DIR"
fi
