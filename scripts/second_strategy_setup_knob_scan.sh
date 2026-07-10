#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-${SECOND_STRATEGY_ENV_FILE:-/etc/alpaca_bot/alpaca-bot.env}}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${SECOND_STRATEGY_SETUP_OUTPUT_DIR:-}"
VALIDATION_OUTPUT_DIR="${SECOND_STRATEGY_SETUP_VALIDATION_OUTPUT_DIR:-}"

emit_scan_result() {
  local rc="$?"
  local status="ok"
  if [[ "$rc" -ne 0 ]]; then
    status="failed"
  fi
  echo "second strategy setup-knob scan result: status=$status rc=$rc output_dir=${OUTPUT_DIR:-none} validation_output_dir=${VALIDATION_OUTPUT_DIR:-none}"
}
trap emit_scan_result EXIT

fail() {
  echo "second strategy setup-knob scan failed: $*" >&2
  exit 1
}

publish_replay_artifacts() {
  local tmp_report_path="$1"
  local report_path="$2"
  local tmp_jsonl_path="$3"
  local jsonl_path="$4"
  local tmp_stderr_path="$5"
  local stderr_path="$6"

  mv -f "$tmp_report_path" "$report_path"
  mv -f "$tmp_jsonl_path" "$jsonl_path"
  mv -f "$tmp_stderr_path" "$stderr_path"
}

discard_replay_artifacts() {
  local tmp_report_path="$1"
  local tmp_jsonl_path="$2"
  local tmp_stderr_path="$3"
  local stderr_path="$4"

  rm -f "$tmp_report_path" "$tmp_jsonl_path"
  if [[ -e "$tmp_stderr_path" ]]; then
    mv -f "$tmp_stderr_path" "$stderr_path"
  fi
}

update_latest_link() {
  local target="$1"
  local link_path="$2"
  local link_dir
  local link_name
  local tmp_link

  link_dir="$(dirname "$link_path")"
  link_name="$(basename "$link_path")"
  mkdir -p "$link_dir"
  tmp_link="$(mktemp "$link_dir/.${link_name}.tmp.XXXXXX")"
  rm -f "$tmp_link"
  if ! ln -s "$target" "$tmp_link"; then
    rm -f "$tmp_link"
    return 1
  fi
  if ! mv -Tf "$tmp_link" "$link_path"; then
    rm -f "$tmp_link"
    return 1
  fi
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

validate_override_assignment() {
  local assignment="$1"
  local name="${assignment%%=*}"
  [[ "$assignment" =~ ^[A-Z_][A-Z0-9_]*=[^[:space:]]+$ ]] \
    || fail "invalid setup override assignment: $assignment"
  case "$name" in
    ENTRY_ORDER_ACTIVE_BARS|ENTRY_MIN_CLOSE_TO_ENTRY_PCT|STOP_LIMIT_BUFFER_PCT|ENTRY_STOP_PRICE_BUFFER)
      fail "protected paper proof parameter cannot be varied by setup scan: $name"
      ;;
    RELATIVE_VOLUME_THRESHOLD|ATR_STOP_MULTIPLIER|DAILY_SMA_PERIOD)
      fail "shared base strategy parameter cannot be varied by setup scan: $name"
      ;;
  esac
}

split_override_assignments() {
  local raw="$1"
  local assignment
  override_env_args=()
  IFS=',' read -r -a override_env_args <<< "$raw"
  for assignment in "${override_env_args[@]}"; do
    validate_override_assignment "$assignment"
  done
}

[[ -f "$ENV_FILE" ]] || fail "missing env file: $ENV_FILE"

cd "$ROOT_DIR"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

SCENARIO_DIR="${SECOND_STRATEGY_SCENARIO_DIR:-/var/lib/alpaca-bot/nightly/scenarios}"
BASE_STRATEGY="${SECOND_STRATEGY_BASE_STRATEGY:-bull_flag}"
SAMPLE_SIZE="${SECOND_STRATEGY_SETUP_SAMPLE_SIZE:-${SECOND_STRATEGY_SAMPLE_SIZE:-80}}"
SAMPLE_SEED="${SECOND_STRATEGY_SETUP_SAMPLE_SEED:-second-strategy-setup-knob-prefilter}"
SLIPPAGE_BPS="${SECOND_STRATEGY_SETUP_SLIPPAGE_BPS:-${SECOND_STRATEGY_SLIPPAGE_BPS:-${REPLAY_SLIPPAGE_BPS:-2}}}"
MAX_OPEN_POSITIONS_VALUE="${SECOND_STRATEGY_SETUP_MAX_OPEN_POSITIONS:-${SECOND_STRATEGY_MAX_OPEN_POSITIONS:-${MAX_OPEN_POSITIONS:-1}}}"
CANDIDATE_SCALE="${SECOND_STRATEGY_SETUP_CANDIDATE_SCALE:-${SECOND_STRATEGY_CANDIDATE_SCALE:-0.25}}"
OUTPUT_ROOT="${SECOND_STRATEGY_SETUP_OUTPUT_ROOT:-${SECOND_STRATEGY_OUTPUT_ROOT:-/var/lib/alpaca-bot/nightly/second_strategy}/setup_knobs}"
OUTPUT_DIR="${SECOND_STRATEGY_SETUP_OUTPUT_DIR:-$OUTPUT_ROOT/$(date -u +%Y%m%dT%H%M%SZ)}"
LATEST_LINK="${SECOND_STRATEGY_SETUP_LATEST_LINK:-}"
UPDATE_LATEST_LINKS="${SECOND_STRATEGY_SETUP_UPDATE_LATEST_LINKS:-true}"
EXCLUDE_CANDIDATES="${SECOND_STRATEGY_SETUP_EXCLUDE_CANDIDATES:-${SECOND_STRATEGY_EXCLUDE_CANDIDATES:-vwap_cross}}"
VARIANT_MODE="${SECOND_STRATEGY_SETUP_VARIANT_MODE:-curated}"
VARIANT_LABELS="${SECOND_STRATEGY_SETUP_VARIANT_LABELS:-}"
MAX_VARIANTS="${SECOND_STRATEGY_SETUP_MAX_VARIANTS:-0}"
VALIDATE_POSITIVES="${SECOND_STRATEGY_SETUP_VALIDATE_POSITIVES:-true}"
MAX_VALIDATION_CANDIDATES="${SECOND_STRATEGY_SETUP_MAX_VALIDATION_CANDIDATES:-0}"
SCAN_JOBS="${SECOND_STRATEGY_SETUP_SCAN_JOBS:-${SECOND_STRATEGY_SCAN_JOBS:-2}}"
VALIDATION_SAMPLE_SIZE="${SECOND_STRATEGY_SETUP_VALIDATION_SAMPLE_SIZE:-${SECOND_STRATEGY_VALIDATION_SAMPLE_SIZE:-160}}"
VALIDATION_SAMPLE_SEED="${SECOND_STRATEGY_SETUP_VALIDATION_SAMPLE_SEED:-second-strategy-setup-knob-independent-validation}"
VALIDATION_OUTPUT_DIR="${SECOND_STRATEGY_SETUP_VALIDATION_OUTPUT_DIR:-$OUTPUT_DIR/validation}"
VALIDATION_LATEST_LINK="${SECOND_STRATEGY_SETUP_VALIDATION_LATEST_LINK:-}"

[[ -d "$SCENARIO_DIR" ]] || fail "missing scenario dir: $SCENARIO_DIR"
mkdir -p "$OUTPUT_DIR"

python3 - "$CANDIDATE_SCALE" "$MAX_VALIDATION_CANDIDATES" "$SCAN_JOBS" "$MAX_VARIANTS" <<'PY' || fail "invalid setup scan numeric setting"
from __future__ import annotations

import sys

try:
    scale = float(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"candidate scale must be numeric: {sys.argv[1]}") from exc
if not 0.0 < scale <= 1.0:
    raise SystemExit(f"candidate scale must be in (0.0, 1.0]: {sys.argv[1]}")
try:
    cap = int(sys.argv[2])
except ValueError as exc:
    raise SystemExit(f"max validation candidates must be an integer: {sys.argv[2]}") from exc
if cap < 0:
    raise SystemExit(f"max validation candidates must be non-negative: {sys.argv[2]}")
try:
    jobs = int(sys.argv[3])
except ValueError as exc:
    raise SystemExit(f"scan jobs must be an integer: {sys.argv[3]}") from exc
if jobs < 1:
    raise SystemExit(f"scan jobs must be at least 1: {sys.argv[3]}")
try:
    max_variants = int(sys.argv[4])
except ValueError as exc:
    raise SystemExit(f"max variants must be an integer: {sys.argv[4]}") from exc
if max_variants < 0:
    raise SystemExit(f"max variants must be non-negative: {sys.argv[4]}")
PY
case "${UPDATE_LATEST_LINKS,,}" in
  true|1|yes|y)
    UPDATE_LATEST_LINKS=true
    ;;
  false|0|no|n|"")
    UPDATE_LATEST_LINKS=false
    ;;
  *)
    fail "SECOND_STRATEGY_SETUP_UPDATE_LATEST_LINKS must be true or false"
    ;;
esac

proof_output="$OUTPUT_DIR/proof_status.txt"
proof_status_loaded=false
load_proof_status() {
  local reason="$1"
  if [[ "$proof_status_loaded" == "true" ]]; then
    return 0
  fi
  echo "second strategy setup-knob scan: $reason from proof status"
  if ! ./scripts/paper_proof_status.sh "$ENV_FILE" > "$proof_output"; then
    fail "could not load proof status; see $proof_output"
  fi
  proof_status_loaded=true
}

if [[ -n "${SECOND_STRATEGY_SETUP_CANDIDATES:-${SECOND_STRATEGY_CANDIDATES:-}}" ]]; then
  candidate_csv="${SECOND_STRATEGY_SETUP_CANDIDATES:-${SECOND_STRATEGY_CANDIDATES:-}}"
else
  load_proof_status "discovering disabled stock candidates"
  diversification_line="$(grep -E '^paper proof strategy diversification: ' "$proof_output" | tail -n 1 || true)"
  [[ -n "$diversification_line" ]] || fail "proof status did not print strategy diversification details"
  candidate_csv="$(extract_field "$diversification_line" "stock_disabled_candidate_names" || true)"
fi
[[ -n "$candidate_csv" ]] || fail "no candidate strategies to scan"

starting_equity="${SECOND_STRATEGY_SETUP_STARTING_EQUITY:-${SECOND_STRATEGY_STARTING_EQUITY:-}}"
if [[ -z "$starting_equity" ]]; then
  load_proof_status "loading live broker equity"
  broker_line="$(grep -E '^paper proof broker account: ' "$proof_output" | tail -n 1 || true)"
  starting_equity="$(extract_field "$broker_line" "equity" || true)"
fi

variants_file="$OUTPUT_DIR/variants.tsv"
python3 - "$variants_file" "$candidate_csv" "$EXCLUDE_CANDIDATES" "$VARIANT_LABELS" "$VARIANT_MODE" "$MAX_VARIANTS" <<'PY'
from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path

from alpaca_bot.nightly.setup_variants import stratified_variant_cap

output_path = Path(sys.argv[1])


def parse_names(raw: str) -> list[str]:
    names: list[str] = []
    for part in re.split(r"[,\s]+", raw or ""):
        name = part.strip()
        if not name or name == "none":
            continue
        if not re.fullmatch(r"[A-Za-z0-9_:-]+", name):
            raise SystemExit(f"invalid name in setup scan filter: {name}")
        if name not in names:
            names.append(name)
    return names


candidate_names = set(parse_names(sys.argv[2]))
excluded_names = set(parse_names(sys.argv[3]))
label_filter = set(parse_names(sys.argv[4]))
variant_mode = sys.argv[5].strip().lower()
max_variants = int(sys.argv[6])
shared_base_env_names = {
    "ATR_STOP_MULTIPLIER",
    "DAILY_SMA_PERIOD",
    "RELATIVE_VOLUME_THRESHOLD",
}
protected_env_names = shared_base_env_names | {
    "ENTRY_ORDER_ACTIVE_BARS",
    "ENTRY_MIN_CLOSE_TO_ENTRY_PCT",
    "STOP_LIMIT_BUFFER_PCT",
    "ENTRY_STOP_PRICE_BUFFER",
}

default_variants = [
    ("failed_breakdown", "I_failed_breakdown_volume", "FAILED_BREAKDOWN_VOLUME_RATIO=2.5"),
    ("failed_breakdown", "J_failed_breakdown_recapture", "FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT=0.002"),
    ("momentum", "U_prior_high_lookback", "PRIOR_DAY_HIGH_LOOKBACK_BARS=2"),
    ("ema_pullback", "AH_ema_period", "EMA_PERIOD=7"),
    ("breakout", "X_breakout_lookback", "BREAKOUT_LOOKBACK_BARS=10"),
    ("orb", "AA_orb_opening_bars", "ORB_OPENING_BARS=3"),
    ("high_watermark", "AB_high_watermark_lookback", "HIGH_WATERMARK_LOOKBACK_DAYS=126"),
    ("vwap_reversion", "AI_vwap_dip", "VWAP_DIP_THRESHOLD_PCT=0.02"),
    ("gap_and_go", "AJ_gap_threshold", "GAP_THRESHOLD_PCT=0.01"),
    ("gap_and_go", "AK_gap_volume", "GAP_VOLUME_THRESHOLD=1.5"),
    ("bb_squeeze", "AL_bb_period", "BB_PERIOD=10"),
    ("bb_squeeze", "AM_bb_std_dev", "BB_STD_DEV=1.5"),
    ("bb_squeeze", "AN_bb_squeeze_threshold", "BB_SQUEEZE_THRESHOLD_PCT=0.05"),
    ("bb_squeeze", "AO_bb_squeeze_min_bars", "BB_SQUEEZE_MIN_BARS=3"),
]


def validate_overrides(overrides: str) -> None:
    for assignment in overrides.split(","):
        name, _, value = assignment.partition("=")
        if not name or not value:
            raise SystemExit(f"invalid setup override assignment: {assignment}")
        if name in protected_env_names:
            parameter_kind = (
                "shared base strategy"
                if name in shared_base_env_names
                else "protected paper proof"
            )
            raise SystemExit(f"{parameter_kind} parameter cannot be varied: {name}")


def curated_variants() -> list[tuple[str, str, str]]:
    return default_variants


def grid_variants() -> list[tuple[str, str, str]]:
    from alpaca_bot.tuning.sweep import STRATEGY_GRIDS

    rows: list[tuple[str, str, str]] = []
    for candidate in sorted(candidate_names):
        if candidate in excluded_names:
            continue
        grid = STRATEGY_GRIDS.get(candidate)
        if not grid:
            continue
        keys = [key for key in grid if key not in protected_env_names]
        if not keys:
            continue
        values = [grid[key] for key in keys]
        for index, combo in enumerate(itertools.product(*values), start=1):
            assignments = [f"{key}={value}" for key, value in zip(keys, combo)]
            rows.append((candidate, f"grid_{index:03d}", ",".join(assignments)))
    return rows


if variant_mode == "curated":
    variant_rows = curated_variants()
elif variant_mode == "grid":
    variant_rows = grid_variants()
else:
    raise SystemExit(
        "SECOND_STRATEGY_SETUP_VARIANT_MODE must be one of: curated, grid"
    )

filtered_rows: list[tuple[str, str, str]] = []
for candidate, label, overrides in variant_rows:
    if candidate not in candidate_names or candidate in excluded_names:
        continue
    if label_filter and label not in label_filter:
        continue
    validate_overrides(overrides)
    filtered_rows.append((candidate, label, overrides))

selected_rows = stratified_variant_cap(filtered_rows, max_variants)
lines = [
    f"{candidate}\t{label}\t{overrides}"
    for candidate, label, overrides in selected_rows
]

output_path.write_text("\n".join(lines) + ("\n" if lines else ""))
PY

variant_count="$(wc -l < "$variants_file" | tr -d ' ')"
[[ "$variant_count" -gt 0 ]] || fail "no setup-knob variants matched candidates=$candidate_csv labels=${VARIANT_LABELS:-all}"

status_file="$OUTPUT_DIR/status.tsv"
summary_file="$OUTPUT_DIR/summary.md"
summary_json_file="$OUTPUT_DIR/summary.json"
status_parts_dir="$OUTPUT_DIR/status_parts"
: > "$status_file"
mkdir -p "$status_parts_dir"

echo "second strategy setup-knob scan: output_dir=$OUTPUT_DIR"
echo "second strategy setup-knob scan: scenario_dir=$SCENARIO_DIR base=$BASE_STRATEGY sample_size=$SAMPLE_SIZE sample_seed=$SAMPLE_SEED slippage_bps=$SLIPPAGE_BPS max_open_positions=$MAX_OPEN_POSITIONS_VALUE candidate_scale=$CANDIDATE_SCALE scan_jobs=$SCAN_JOBS starting_equity=${starting_equity:-scenario_default} variant_mode=$VARIANT_MODE variants=$variant_count max_variants=$MAX_VARIANTS excluded_candidates=$EXCLUDE_CANDIDATES labels=${VARIANT_LABELS:-all}"

failed_count=0
run_prefilter_job() {
  local candidate="$1"
  local variant_label="$2"
  local env_overrides="$3"
  local safe_name
  local report_path
  local jsonl_path
  local stderr_path
  local tmp_report_path
  local tmp_jsonl_path
  local tmp_stderr_path
  local status_part
  local -a cmd
  local -a override_env_args

  split_override_assignments "$env_overrides"
  safe_name="$(printf '%s__%s' "$candidate" "$variant_label" | tr -c 'A-Za-z0-9_' '_')"
  report_path="$OUTPUT_DIR/${safe_name}_basket.md"
  jsonl_path="$OUTPUT_DIR/${safe_name}_basket.jsonl"
  stderr_path="$OUTPUT_DIR/${safe_name}_basket.stderr"
  tmp_report_path="$report_path.tmp.$BASHPID"
  tmp_jsonl_path="$jsonl_path.tmp.$BASHPID"
  tmp_stderr_path="$stderr_path.tmp.$BASHPID"
  status_part="$status_parts_dir/${safe_name}.tsv"
  cmd=(
    python3 -m alpaca_bot.replay.cli portfolio-basket-audit
    --scenario-dir "$SCENARIO_DIR"
    --strategy "$BASE_STRATEGY"
    --strategy "$candidate"
    --sample-size "$SAMPLE_SIZE"
    --sample-seed "$SAMPLE_SEED"
    --slippage-bps "$SLIPPAGE_BPS"
    --max-open-positions "$MAX_OPEN_POSITIONS_VALUE"
    --confidence-scale "$candidate=$CANDIDATE_SCALE"
    --output "$tmp_report_path"
    --jsonl "$tmp_jsonl_path"
  )
  if [[ -n "$starting_equity" && "$starting_equity" != "none" ]]; then
    cmd+=(--starting-equity "$starting_equity")
  fi

  echo "second strategy setup-knob scan: candidate=$candidate variant=$variant_label overrides=$env_overrides"
  if env "${override_env_args[@]}" "${cmd[@]}" 2> "$tmp_stderr_path"; then
    publish_replay_artifacts "$tmp_report_path" "$report_path" "$tmp_jsonl_path" "$jsonl_path" "$tmp_stderr_path" "$stderr_path"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$variant_label" "$env_overrides" "$CANDIDATE_SCALE" "passed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
    return 0
  fi
  discard_replay_artifacts "$tmp_report_path" "$tmp_jsonl_path" "$tmp_stderr_path" "$stderr_path"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$variant_label" "$env_overrides" "$CANDIDATE_SCALE" "failed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
  return 1
}

wait_for_next_prefilter_job() {
  if ! wait -n; then
    failed_count=$((failed_count + 1))
  fi
  prefilter_running_jobs=$((prefilter_running_jobs - 1))
}

prefilter_running_jobs=0
while IFS=$'\t' read -r candidate variant_label env_overrides; do
  [[ -n "$candidate" && -n "$variant_label" && -n "$env_overrides" ]] || continue
  run_prefilter_job "$candidate" "$variant_label" "$env_overrides" &
  prefilter_running_jobs=$((prefilter_running_jobs + 1))
  if [[ "$prefilter_running_jobs" -ge "$SCAN_JOBS" ]]; then
    wait_for_next_prefilter_job
  fi
done < "$variants_file"
while [[ "$prefilter_running_jobs" -gt 0 ]]; do
  wait_for_next_prefilter_job
done
for status_part in "$status_parts_dir"/*.tsv; do
  [[ -e "$status_part" ]] || continue
  cat "$status_part" >> "$status_file"
done

python3 - "$status_file" "$summary_file" "$summary_json_file" \
  "$SCENARIO_DIR" "$BASE_STRATEGY" "$SAMPLE_SIZE" "$SAMPLE_SEED" \
  "$SLIPPAGE_BPS" "$MAX_OPEN_POSITIONS_VALUE" "$CANDIDATE_SCALE" \
  "${starting_equity:-scenario_default}" "$EXCLUDE_CANDIDATES" "$SCAN_JOBS" \
  "$variant_count" "$VARIANT_MODE" "$MAX_VARIANTS" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from alpaca_bot.nightly.candidate_evidence import (
    candidate_contribution,
    contribution_status,
    evidence_verdict,
)

status_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
summary_json_path = Path(sys.argv[3])
scenario_dir = sys.argv[4]
base_strategy = sys.argv[5]
sample_size = sys.argv[6]
sample_seed = sys.argv[7]
slippage_bps = sys.argv[8]
max_open_positions = sys.argv[9]
candidate_scale = sys.argv[10]
starting_equity = sys.argv[11]
excluded_candidates = sys.argv[12]
scan_jobs = sys.argv[13]
variant_count = sys.argv[14]
variant_mode = sys.argv[15]
max_variants = sys.argv[16]


def fmt(value, spec: str = ".2f") -> str:
    if value is None:
        return "n/a"
    return format(float(value), spec)


def write_text_atomic(path: Path, text: str) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(text)
    tmp_path.replace(path)


rows = []
json_rows = []
for raw in status_path.read_text().splitlines():
    if not raw.strip():
        continue
    candidate, variant_label, env_overrides, scale, status, report, jsonl, stderr = raw.split("\t")
    audit_row = None
    if status == "passed" and Path(jsonl).exists():
        payloads = [
            json.loads(line)
            for line in Path(jsonl).read_text().splitlines()
            if line.strip()
        ]
        if payloads and payloads[-1].get("rows"):
            audit_row = payloads[-1]["rows"][0]
            trade_diagnostics = payloads[-1].get("trade_diagnostics")
            if trade_diagnostics is not None:
                audit_row["trade_diagnostics"] = trade_diagnostics
    rows.append((candidate, variant_label, env_overrides, scale, status, report, stderr, audit_row))


candidate_names = []
for candidate, *_rest in rows:
    if candidate not in candidate_names:
        candidate_names.append(candidate)


def sort_key(item):
    candidate, variant_label, _env_overrides, _scale, status, _report, _stderr, audit_row = item
    if status != "passed" or audit_row is None:
        return (3, 0.0, candidate, variant_label)
    verdict_rank = {
        "positive-edge": 0,
        "no-evidence": 1,
        "insufficient-data": 2,
        "negative-edge": 2,
        "no-candidate-trades": 2,
        "non-positive-candidate-pnl": 2,
        "missing-candidate-edge-diagnostics": 2,
    }.get(evidence_verdict(audit_row, candidate), 2)
    ci_low = candidate_contribution(audit_row, candidate).get("ci_low")
    ci_rank = -(float(ci_low) if ci_low is not None else float("-inf"))
    return (verdict_rank, ci_rank, candidate, variant_label)


lines = [
    "# Second strategy setup-knob basket scan",
    "",
    "Run metadata:",
    "",
    f"- scenario_dir: `{scenario_dir}`",
    f"- base_strategy: `{base_strategy}`",
    f"- sample_size: `{sample_size}`",
    f"- sample_seed: `{sample_seed}`",
    f"- slippage_bps: `{slippage_bps}`",
    f"- max_open_positions: `{max_open_positions}`",
    f"- candidate_scale: `{candidate_scale}`",
    f"- scan_jobs: `{scan_jobs}`",
    f"- starting_equity: `{starting_equity}`",
    f"- excluded_candidates: `{excluded_candidates}`",
    f"- variant_mode: `{variant_mode}`",
    f"- max_variants: `{max_variants}`",
    f"- variants: `{variant_count}`",
    f"- candidate_names: `{','.join(candidate_names) if candidate_names else 'none'}`",
    "",
    "| candidate | lever | overrides | status | trades | candidate trades | candidate P&L | candidate mean/trade | candidate 95% CI mean/trade | candidate p(mean<=0) | basket total P&L | basket 95% CI mean/trade | basket verdict | verdict | report |",
    "|---|---|---|---|---:|---:|---:|---:|---|---:|---:|---|---|---|---|",
]

positive_edges = 0
for candidate, variant_label, env_overrides, scale, status, report, stderr, audit_row in sorted(rows, key=sort_key):
    if status != "passed" or audit_row is None:
        json_rows.append(
            {
                "candidate": candidate,
                "variant_label": variant_label,
                "env_overrides": env_overrides,
                "candidate_scale": scale,
                "status": status,
                "report": report,
                "stderr": stderr,
                "verdict": None,
            }
        )
        lines.append(
            f"| `{candidate}` | `{variant_label}` | `{env_overrides}` | `{status}` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `{stderr}` |"
        )
        continue
    basket_verdict = audit_row["verdict"]
    verdict = evidence_verdict(audit_row, candidate)
    candidate_stats = candidate_contribution(audit_row, candidate)
    candidate_status = contribution_status(candidate_stats)
    if verdict == "positive-edge":
        positive_edges += 1
    ci = (
        "n/a"
        if audit_row["ci_low"] is None or audit_row["ci_high"] is None
        else f"[{fmt(audit_row['ci_low'], '.4f')}, {fmt(audit_row['ci_high'], '.4f')}]"
    )
    candidate_ci = (
        "n/a"
        if candidate_stats["ci_low"] is None or candidate_stats["ci_high"] is None
        else f"[{fmt(candidate_stats['ci_low'], '.4f')}, {fmt(candidate_stats['ci_high'], '.4f')}]"
    )
    p_mean_le_zero = audit_row["p_positive"]
    json_rows.append(
        {
            "candidate": candidate,
            "variant_label": variant_label,
            "env_overrides": env_overrides,
            "candidate_scale": scale,
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
            "basket_verdict": basket_verdict,
            "candidate_trades": candidate_stats["trades"],
            "candidate_total_pnl": candidate_stats["total_pnl"],
            "candidate_mean_trade_pnl": candidate_stats["mean_trade_pnl"],
            "candidate_ci_low": candidate_stats["ci_low"],
            "candidate_ci_high": candidate_stats["ci_high"],
            "candidate_p_mean_le_zero": candidate_stats["p_mean_le_zero"],
            "candidate_verdict": candidate_stats["verdict"],
            "candidate_contribution_status": candidate_status,
            "trade_diagnostics": audit_row.get("trade_diagnostics"),
            "verdict": verdict,
        }
    )
    lines.append(
        "| "
        f"`{candidate}` | `{variant_label}` | `{env_overrides}` | `passed` | "
        f"{audit_row['trades']} | {fmt(candidate_stats['trades'], '.0f')} | "
        f"{fmt(candidate_stats['total_pnl'])} | "
        f"{fmt(candidate_stats['mean_trade_pnl'], '.4f')} | {candidate_ci} | "
        f"{fmt(candidate_stats['p_mean_le_zero'], '.4f')} | "
        f"{fmt(audit_row['total_pnl'])} | {ci} | "
        f"`{basket_verdict}` | `{verdict}` | `{report}` |"
    )

lines.extend([
    "",
    (
        "Promotion note: a positive setup-knob prefilter row is only a survivor "
        "for a separate independent validation; it is not approval to change "
        "PAPER_APPROVED_STRATEGIES or live paper parameters."
    ),
])
write_text_atomic(summary_path, "\n".join(lines) + "\n")
write_text_atomic(
    summary_json_path,
    json.dumps(
        {
            "scenario_dir": scenario_dir,
            "base_strategy": base_strategy,
            "sample_size": sample_size,
            "sample_seed": sample_seed,
            "slippage_bps": slippage_bps,
            "max_open_positions": max_open_positions,
            "candidate_scale": candidate_scale,
            "scan_jobs": scan_jobs,
            "starting_equity": starting_equity,
            "excluded_candidates": excluded_candidates,
            "variant_mode": variant_mode,
            "max_variants": int(max_variants),
            "variant_count": int(variant_count),
            "candidate_count": len(candidate_names),
            "candidate_names": candidate_names,
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

validation_failed_count=0
if [[ "${VALIDATE_POSITIVES,,}" == "true" ]]; then
  validation_specs_file="$VALIDATION_OUTPUT_DIR/variants.tsv"
  mkdir -p "$VALIDATION_OUTPUT_DIR"
  python3 - "$summary_json_file" "$validation_specs_file" "$MAX_VALIDATION_CANDIDATES" <<'PY'
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

payload = json.loads(Path(sys.argv[1]).read_text())
output_path = Path(sys.argv[2])
max_validation_candidates = int(sys.argv[3])

selected = []
for row in payload.get("rows", []):
    if (
        row.get("status") == "passed"
        and row.get("verdict") == "positive-edge"
        and row.get("candidate_verdict") == "positive-edge"
        and row.get("candidate_contribution_status") == "positive_pnl"
    ):
        ci_low = row.get("candidate_ci_low")
        p_mean_le_zero = row.get("candidate_p_mean_le_zero")
        total_pnl = row.get("candidate_total_pnl")
        score = (
            float(ci_low) if ci_low is not None else -math.inf,
            -(float(p_mean_le_zero) if p_mean_le_zero is not None else math.inf),
            float(total_pnl) if total_pnl is not None else -math.inf,
            row["candidate"],
            row["variant_label"],
        )
        selected.append((score, row))

selected = [
    row
    for _score, row in sorted(
        selected,
        key=lambda item: item[0],
        reverse=True,
    )
]
if max_validation_candidates > 0:
    selected = selected[:max_validation_candidates]
output_path.write_text(
    "".join(
        f"{row['candidate']}\t{row['variant_label']}\t{row['env_overrides']}\t{row['candidate_scale']}\n"
        for row in selected
    )
)
PY

  validation_status_file="$VALIDATION_OUTPUT_DIR/status.tsv"
  validation_summary_file="$VALIDATION_OUTPUT_DIR/summary.md"
  validation_summary_json_file="$VALIDATION_OUTPUT_DIR/summary.json"
  validation_status_parts_dir="$VALIDATION_OUTPUT_DIR/status_parts"
  : > "$validation_status_file"
  mkdir -p "$validation_status_parts_dir"

  validation_spec_count="$(wc -l < "$validation_specs_file" | tr -d ' ')"
  echo "second strategy setup-knob validation: output_dir=$VALIDATION_OUTPUT_DIR variants=$validation_spec_count sample_size=$VALIDATION_SAMPLE_SIZE sample_seed=$VALIDATION_SAMPLE_SEED scan_jobs=$SCAN_JOBS"

  run_validation_job() {
    local candidate="$1"
    local variant_label="$2"
    local env_overrides="$3"
    local candidate_scale="$4"
    local safe_name
    local report_path
    local jsonl_path
    local stderr_path
    local tmp_report_path
    local tmp_jsonl_path
    local tmp_stderr_path
    local status_part
    local -a cmd
    local -a override_env_args

    split_override_assignments "$env_overrides"
    safe_name="$(printf '%s__%s' "$candidate" "$variant_label" | tr -c 'A-Za-z0-9_' '_')"
    report_path="$VALIDATION_OUTPUT_DIR/${safe_name}_validation.md"
    jsonl_path="$VALIDATION_OUTPUT_DIR/${safe_name}_validation.jsonl"
    stderr_path="$VALIDATION_OUTPUT_DIR/${safe_name}_validation.stderr"
    tmp_report_path="$report_path.tmp.$BASHPID"
    tmp_jsonl_path="$jsonl_path.tmp.$BASHPID"
    tmp_stderr_path="$stderr_path.tmp.$BASHPID"
    status_part="$validation_status_parts_dir/${safe_name}.tsv"
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
      --output "$tmp_report_path"
      --jsonl "$tmp_jsonl_path"
    )
    if [[ -n "$starting_equity" && "$starting_equity" != "none" ]]; then
      cmd+=(--starting-equity "$starting_equity")
    fi

    echo "second strategy setup-knob validation: candidate=$candidate variant=$variant_label overrides=$env_overrides"
    if env "${override_env_args[@]}" "${cmd[@]}" 2> "$tmp_stderr_path"; then
      publish_replay_artifacts "$tmp_report_path" "$report_path" "$tmp_jsonl_path" "$jsonl_path" "$tmp_stderr_path" "$stderr_path"
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$variant_label" "$env_overrides" "$candidate_scale" "passed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
      return 0
    fi
    discard_replay_artifacts "$tmp_report_path" "$tmp_jsonl_path" "$tmp_stderr_path" "$stderr_path"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$variant_label" "$env_overrides" "$candidate_scale" "failed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
    return 1
  }

  wait_for_next_validation_job() {
    if ! wait -n; then
      validation_failed_count=$((validation_failed_count + 1))
    fi
    validation_running_jobs=$((validation_running_jobs - 1))
  }

  validation_running_jobs=0
  while IFS=$'\t' read -r candidate variant_label env_overrides candidate_scale; do
    [[ -n "$candidate" && -n "$variant_label" && -n "$env_overrides" && -n "$candidate_scale" ]] || continue
    run_validation_job "$candidate" "$variant_label" "$env_overrides" "$candidate_scale" &
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

  python3 - "$validation_status_file" "$validation_summary_file" "$validation_summary_json_file" \
    "$summary_json_file" "$SCENARIO_DIR" "$BASE_STRATEGY" "$VALIDATION_SAMPLE_SIZE" \
    "$VALIDATION_SAMPLE_SEED" "$SLIPPAGE_BPS" "$MAX_OPEN_POSITIONS_VALUE" \
    "$CANDIDATE_SCALE" "${starting_equity:-scenario_default}" "$EXCLUDE_CANDIDATES" \
    "$SCAN_JOBS" "$MAX_VALIDATION_CANDIDATES" "$VALIDATION_OUTPUT_DIR" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from alpaca_bot.nightly.candidate_evidence import (
    candidate_contribution,
    contribution_status,
    evidence_verdict,
)

status_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
summary_json_path = Path(sys.argv[3])
prefilter_summary_json_path = Path(sys.argv[4])
scenario_dir = sys.argv[5]
base_strategy = sys.argv[6]
sample_size = sys.argv[7]
sample_seed = sys.argv[8]
slippage_bps = sys.argv[9]
max_open_positions = sys.argv[10]
candidate_scale = sys.argv[11]
starting_equity = sys.argv[12]
excluded_candidates = sys.argv[13]
scan_jobs = sys.argv[14]
max_validation_candidates = sys.argv[15]
validation_output_dir = sys.argv[16]


def fmt(value, spec: str = ".2f") -> str:
    if value is None:
        return "n/a"
    return format(float(value), spec)


def write_text_atomic(path: Path, text: str) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(text)
    tmp_path.replace(path)


rows = []
json_rows = []
for raw in status_path.read_text().splitlines():
    if not raw.strip():
        continue
    candidate, variant_label, env_overrides, scale, status, report, jsonl, stderr = raw.split("\t")
    audit_row = None
    if status == "passed" and Path(jsonl).exists():
        payloads = [
            json.loads(line)
            for line in Path(jsonl).read_text().splitlines()
            if line.strip()
        ]
        if payloads and payloads[-1].get("rows"):
            audit_row = payloads[-1]["rows"][0]
            trade_diagnostics = payloads[-1].get("trade_diagnostics")
            if trade_diagnostics is not None:
                audit_row["trade_diagnostics"] = trade_diagnostics
    rows.append((candidate, variant_label, env_overrides, scale, status, report, stderr, audit_row))


candidate_names = []
for candidate, *_rest in rows:
    if candidate not in candidate_names:
        candidate_names.append(candidate)


def sort_key(item):
    candidate, variant_label, _env_overrides, _scale, status, _report, _stderr, audit_row = item
    if status != "passed" or audit_row is None:
        return (3, 0.0, candidate, variant_label)
    verdict_rank = {
        "positive-edge": 0,
        "no-evidence": 1,
        "insufficient-data": 2,
        "negative-edge": 2,
        "no-candidate-trades": 2,
        "non-positive-candidate-pnl": 2,
        "missing-candidate-edge-diagnostics": 2,
    }.get(evidence_verdict(audit_row, candidate), 2)
    ci_low = candidate_contribution(audit_row, candidate).get("ci_low")
    ci_rank = -(float(ci_low) if ci_low is not None else float("-inf"))
    return (verdict_rank, ci_rank, candidate, variant_label)


lines = [
    "# Second strategy setup-knob independent validation",
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
    f"- candidate_scale: `{candidate_scale}`",
    f"- max_validation_candidates: `{max_validation_candidates}`",
    f"- scan_jobs: `{scan_jobs}`",
    f"- starting_equity: `{starting_equity}`",
    f"- excluded_candidates: `{excluded_candidates}`",
    f"- candidate_names: `{','.join(candidate_names) if candidate_names else 'none'}`",
    "",
    "| candidate | lever | overrides | status | trades | candidate trades | candidate P&L | candidate mean/trade | candidate 95% CI mean/trade | candidate p(mean<=0) | basket total P&L | basket 95% CI mean/trade | basket verdict | verdict | report |",
    "|---|---|---|---|---:|---:|---:|---:|---|---:|---:|---|---|---|---|",
]

validation_positive_edges = 0
for candidate, variant_label, env_overrides, scale, status, report, stderr, audit_row in sorted(rows, key=sort_key):
    if status != "passed" or audit_row is None:
        json_rows.append(
            {
                "candidate": candidate,
                "variant_label": variant_label,
                "env_overrides": env_overrides,
                "candidate_scale": scale,
                "status": status,
                "report": report,
                "stderr": stderr,
                "verdict": None,
            }
        )
        lines.append(
            f"| `{candidate}` | `{variant_label}` | `{env_overrides}` | `{status}` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `{stderr}` |"
        )
        continue
    basket_verdict = audit_row["verdict"]
    verdict = evidence_verdict(audit_row, candidate)
    candidate_stats = candidate_contribution(audit_row, candidate)
    candidate_status = contribution_status(candidate_stats)
    if verdict == "positive-edge":
        validation_positive_edges += 1
    ci = (
        "n/a"
        if audit_row["ci_low"] is None or audit_row["ci_high"] is None
        else f"[{fmt(audit_row['ci_low'], '.4f')}, {fmt(audit_row['ci_high'], '.4f')}]"
    )
    candidate_ci = (
        "n/a"
        if candidate_stats["ci_low"] is None or candidate_stats["ci_high"] is None
        else f"[{fmt(candidate_stats['ci_low'], '.4f')}, {fmt(candidate_stats['ci_high'], '.4f')}]"
    )
    p_mean_le_zero = audit_row["p_positive"]
    json_rows.append(
        {
            "candidate": candidate,
            "variant_label": variant_label,
            "env_overrides": env_overrides,
            "candidate_scale": scale,
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
            "basket_verdict": basket_verdict,
            "candidate_trades": candidate_stats["trades"],
            "candidate_total_pnl": candidate_stats["total_pnl"],
            "candidate_mean_trade_pnl": candidate_stats["mean_trade_pnl"],
            "candidate_ci_low": candidate_stats["ci_low"],
            "candidate_ci_high": candidate_stats["ci_high"],
            "candidate_p_mean_le_zero": candidate_stats["p_mean_le_zero"],
            "candidate_verdict": candidate_stats["verdict"],
            "candidate_contribution_status": candidate_status,
            "trade_diagnostics": audit_row.get("trade_diagnostics"),
            "verdict": verdict,
        }
    )
    lines.append(
        "| "
        f"`{candidate}` | `{variant_label}` | `{env_overrides}` | `passed` | "
        f"{audit_row['trades']} | {fmt(candidate_stats['trades'], '.0f')} | "
        f"{fmt(candidate_stats['total_pnl'])} | "
        f"{fmt(candidate_stats['mean_trade_pnl'], '.4f')} | {candidate_ci} | "
        f"{fmt(candidate_stats['p_mean_le_zero'], '.4f')} | "
        f"{fmt(audit_row['total_pnl'])} | {ci} | "
        f"`{basket_verdict}` | `{verdict}` | `{report}` |"
    )

if validation_positive_edges:
    conclusion = (
        "Validation found positive-edge setup-knob survivor(s). This is still "
        "not approval to change PAPER_APPROVED_STRATEGIES or live paper "
        "parameters; promote only through explicit operator review."
    )
elif not json_rows:
    conclusion = (
        "No setup-knob variant produced a positive prefilter survivor, so "
        "independent validation had no variants to run and no paper promotion is approved."
    )
else:
    conclusion = (
        "No setup-knob variant from this batch is approved for paper promotion; "
        "every independently validated survivor returned no positive validation edge."
    )

lines.extend(["", conclusion])
write_text_atomic(summary_path, "\n".join(lines) + "\n")
write_text_atomic(
    summary_json_path,
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
            "candidate_scale": candidate_scale,
            "max_validation_candidates": max_validation_candidates,
            "scan_jobs": scan_jobs,
            "starting_equity": starting_equity,
            "excluded_candidates": excluded_candidates,
            "candidate_count": len(candidate_names),
            "candidate_names": candidate_names,
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

  if [[ -z "$VALIDATION_LATEST_LINK" && "$UPDATE_LATEST_LINKS" == "true" ]]; then
    VALIDATION_LATEST_LINK="$OUTPUT_ROOT/latest_validation"
  fi
  if [[ -n "$VALIDATION_LATEST_LINK" ]]; then
    update_latest_link "$VALIDATION_OUTPUT_DIR" "$VALIDATION_LATEST_LINK"
    echo "latest_validation=$VALIDATION_LATEST_LINK"
  fi
else
  echo "second strategy setup-knob validation: disabled"
fi

if [[ -z "$LATEST_LINK" && "$UPDATE_LATEST_LINKS" == "true" ]]; then
  LATEST_LINK="$OUTPUT_ROOT/latest"
fi
if [[ -n "$LATEST_LINK" ]]; then
  update_latest_link "$OUTPUT_DIR" "$LATEST_LINK"
  echo "latest=$LATEST_LINK"
fi

if [[ "$failed_count" -gt 0 ]]; then
  fail "$failed_count setup-knob scan command(s) failed; see $OUTPUT_DIR"
fi
if [[ "$validation_failed_count" -gt 0 ]]; then
  fail "$validation_failed_count setup-knob validation command(s) failed; see $VALIDATION_OUTPUT_DIR"
fi
