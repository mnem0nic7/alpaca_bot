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
OUTPUT_DIR="${SECOND_STRATEGY_OUTPUT_DIR:-/tmp/alpaca-second-strategy-scan-$(date -u +%Y%m%dT%H%M%SZ)}"

[[ -d "$SCENARIO_DIR" ]] || fail "missing scenario dir: $SCENARIO_DIR"
mkdir -p "$OUTPUT_DIR"

proof_output="$OUTPUT_DIR/proof_status.txt"
if [[ -n "${SECOND_STRATEGY_CANDIDATES:-}" ]]; then
  mapfile -t candidates < <(read_name_list "$SECOND_STRATEGY_CANDIDATES")
else
  echo "second strategy basket scan: discovering disabled stock candidates from proof status"
  if ! ./scripts/paper_proof_status.sh "$ENV_FILE" > "$proof_output"; then
    fail "could not discover candidates from paper proof status; see $proof_output"
  fi
  diversification_line="$(grep -E '^paper proof strategy diversification: ' "$proof_output" | tail -n 1 || true)"
  [[ -n "$diversification_line" ]] || fail "proof status did not print strategy diversification details"
  candidate_csv="$(extract_field "$diversification_line" "stock_disabled_candidate_names" || true)"
  mapfile -t candidates < <(read_name_list "$candidate_csv")
fi

[[ "${#candidates[@]}" -gt 0 ]] || fail "no candidate strategies to scan"

starting_equity="${SECOND_STRATEGY_STARTING_EQUITY:-}"
if [[ -z "$starting_equity" && -s "$proof_output" ]]; then
  broker_line="$(grep -E '^paper proof broker account: ' "$proof_output" | tail -n 1 || true)"
  starting_equity="$(extract_field "$broker_line" "equity" || true)"
fi

status_file="$OUTPUT_DIR/status.tsv"
summary_file="$OUTPUT_DIR/summary.md"
: > "$status_file"

echo "second strategy basket scan: output_dir=$OUTPUT_DIR"
echo "second strategy basket scan: scenario_dir=$SCENARIO_DIR base=$BASE_STRATEGY sample_size=$SAMPLE_SIZE sample_seed=$SAMPLE_SEED slippage_bps=$SLIPPAGE_BPS max_open_positions=$MAX_OPEN_POSITIONS_VALUE candidate_scale=$CANDIDATE_SCALE starting_equity=${starting_equity:-scenario_default}"

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

python3 - "$status_file" "$summary_file" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

status_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])


def fmt(value, spec: str = ".2f") -> str:
    if value is None:
        return "n/a"
    return format(float(value), spec)


rows = []
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
        return (3, float("-inf"), candidate)
    verdict_rank = {
        "positive-edge": 0,
        "no-evidence": 1,
        "insufficient-data": 2,
        "negative-edge": 2,
    }.get(audit_row.get("verdict"), 2)
    ci_low = audit_row.get("ci_low")
    return (verdict_rank, float(ci_low) if ci_low is not None else float("-inf"), candidate)


lines = [
    "# Second strategy basket scan",
    "",
    "| candidate | status | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict | report |",
    "|---|---|---:|---:|---:|---:|---|---:|---:|---|---|",
]

positive_edges = 0
for candidate, status, report, stderr, audit_row in sorted(rows, key=sort_key):
    if status != "passed" or audit_row is None:
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
    p_mean_le_zero = (
        None
        if audit_row["p_positive"] is None
        else 1.0 - float(audit_row["p_positive"])
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
print("\n".join(lines))
print(f"summary={summary_path}")
print(f"positive_edge_prefilter_rows={positive_edges}")
PY

if [[ "$failed_count" -gt 0 ]]; then
  fail "$failed_count candidate scan command(s) failed; see $OUTPUT_DIR"
fi
