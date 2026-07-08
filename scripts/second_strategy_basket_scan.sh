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
CANDIDATE_SCALE="${SECOND_STRATEGY_CANDIDATE_SCALE:-0.25}"
OUTPUT_ROOT="${SECOND_STRATEGY_OUTPUT_ROOT:-/var/lib/alpaca-bot/nightly/second_strategy}"
OUTPUT_DIR="${SECOND_STRATEGY_OUTPUT_DIR:-$OUTPUT_ROOT/$(date -u +%Y%m%dT%H%M%SZ)}"
LATEST_LINK="${SECOND_STRATEGY_LATEST_LINK:-}"
EXCLUDE_CANDIDATES="${SECOND_STRATEGY_EXCLUDE_CANDIDATES:-vwap_cross}"

[[ -d "$SCENARIO_DIR" ]] || fail "missing scenario dir: $SCENARIO_DIR"
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

if [[ -n "${SECOND_STRATEGY_CANDIDATES:-}" ]]; then
  mapfile -t candidates < <(read_name_list "$SECOND_STRATEGY_CANDIDATES")
else
  load_proof_status "discovering disabled stock candidates"
  diversification_line="$(grep -E '^paper proof strategy diversification: ' "$proof_output" | tail -n 1 || true)"
  [[ -n "$diversification_line" ]] || fail "proof status did not print strategy diversification details"
  candidate_csv="$(extract_field "$diversification_line" "stock_disabled_candidate_names" || true)"
  mapfile -t candidates < <(read_name_list "$candidate_csv")
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

[[ "${#candidates[@]}" -gt 0 ]] || fail "no candidate strategies to scan"

starting_equity="${SECOND_STRATEGY_STARTING_EQUITY:-}"
if [[ -z "$starting_equity" ]]; then
  load_proof_status "loading live broker equity"
  broker_line="$(grep -E '^paper proof broker account: ' "$proof_output" | tail -n 1 || true)"
  starting_equity="$(extract_field "$broker_line" "equity" || true)"
fi

status_file="$OUTPUT_DIR/status.tsv"
summary_file="$OUTPUT_DIR/summary.md"
summary_json_file="$OUTPUT_DIR/summary.json"
: > "$status_file"

echo "second strategy basket scan: output_dir=$OUTPUT_DIR"
echo "second strategy basket scan: scenario_dir=$SCENARIO_DIR base=$BASE_STRATEGY sample_size=$SAMPLE_SIZE sample_seed=$SAMPLE_SEED slippage_bps=$SLIPPAGE_BPS max_open_positions=$MAX_OPEN_POSITIONS_VALUE candidate_scale=$CANDIDATE_SCALE starting_equity=${starting_equity:-scenario_default} excluded_candidates=${skipped_candidates[*]:-none}"

failed_count=0
for candidate in "${candidates[@]}"; do
  if [[ "$candidate" == "$BASE_STRATEGY" ]]; then
    continue
  fi
  safe_candidate="$(printf '%s' "$candidate" | tr -c 'A-Za-z0-9_' '_')"
  report_path="$OUTPUT_DIR/${safe_candidate}_basket.md"
  jsonl_path="$OUTPUT_DIR/${safe_candidate}_basket.jsonl"
  stderr_path="$OUTPUT_DIR/${safe_candidate}_basket.stderr"
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
    --output "$report_path"
    --jsonl "$jsonl_path"
  )
  if [[ -n "$starting_equity" && "$starting_equity" != "none" ]]; then
    cmd+=(--starting-equity "$starting_equity")
  fi

  echo "second strategy basket scan: candidate=$candidate"
  if "${cmd[@]}" 2> "$stderr_path"; then
    printf '%s\t%s\t%s\t%s\t%s\n' "$candidate" "passed" "$report_path" "$jsonl_path" "$stderr_path" >> "$status_file"
  else
    failed_count=$((failed_count + 1))
    printf '%s\t%s\t%s\t%s\t%s\n' "$candidate" "failed" "$report_path" "$jsonl_path" "$stderr_path" >> "$status_file"
  fi
done

python3 - "$status_file" "$summary_file" "$summary_json_file" \
  "$SCENARIO_DIR" "$BASE_STRATEGY" "$SAMPLE_SIZE" "$SAMPLE_SEED" \
  "$SLIPPAGE_BPS" "$MAX_OPEN_POSITIONS_VALUE" "$CANDIDATE_SCALE" \
  "${starting_equity:-scenario_default}" "${skipped_candidates[*]:-none}" <<'PY'
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
candidate_scale = sys.argv[10]
starting_equity = sys.argv[11]
excluded_candidates = sys.argv[12]


def fmt(value, spec: str = ".2f") -> str:
    if value is None:
        return "n/a"
    return format(float(value), spec)


rows = []
json_rows = []
for raw in status_path.read_text().splitlines():
    if not raw.strip():
        continue
    candidate, status, report, jsonl, stderr = raw.split("\t")
    audit_row = None
    if status == "passed" and Path(jsonl).exists():
        payloads = [
            json.loads(line)
            for line in Path(jsonl).read_text().splitlines()
            if line.strip()
        ]
        if payloads and payloads[-1].get("rows"):
            audit_row = payloads[-1]["rows"][0]
    rows.append((candidate, status, report, stderr, audit_row))


def sort_key(item):
    candidate, status, _report, _stderr, audit_row = item
    if status != "passed" or audit_row is None:
        return (3, 0.0, candidate)
    verdict_rank = {
        "positive-edge": 0,
        "no-evidence": 1,
        "insufficient-data": 2,
        "negative-edge": 2,
    }.get(audit_row.get("verdict"), 2)
    ci_low = audit_row.get("ci_low")
    ci_rank = -(float(ci_low) if ci_low is not None else float("-inf"))
    return (verdict_rank, ci_rank, candidate)


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
    f"- candidate_scale: `{candidate_scale}`",
    f"- starting_equity: `{starting_equity}`",
    f"- excluded_candidates: `{excluded_candidates}`",
    "",
    "| candidate | status | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict | report |",
    "|---|---|---:|---:|---:|---:|---|---:|---:|---|---|",
]

positive_edges = 0
for candidate, status, report, stderr, audit_row in sorted(rows, key=sort_key):
    if status != "passed" or audit_row is None:
        json_rows.append(
            {
                "candidate": candidate,
                "status": status,
                "report": report,
                "stderr": stderr,
                "verdict": None,
            }
        )
        lines.append(
            f"| `{candidate}` | `{status}` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `{stderr}` |"
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
        f"`{candidate}` | `passed` | {audit_row['trades']} | "
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
            "candidate_scale": candidate_scale,
            "starting_equity": starting_equity,
            "excluded_candidates": excluded_candidates,
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

if [[ -z "$LATEST_LINK" && -z "${SECOND_STRATEGY_OUTPUT_DIR:-}" ]]; then
  LATEST_LINK="$OUTPUT_ROOT/latest"
fi
if [[ -n "$LATEST_LINK" ]]; then
  mkdir -p "$(dirname "$LATEST_LINK")"
  ln -sfn "$OUTPUT_DIR" "$LATEST_LINK"
  echo "latest=$LATEST_LINK"
fi

if [[ "$failed_count" -gt 0 ]]; then
  fail "$failed_count candidate scan command(s) failed; see $OUTPUT_DIR"
fi
