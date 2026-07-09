#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-${SECOND_STRATEGY_ENV_FILE:-/etc/alpaca_bot/alpaca-bot.env}}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${SECOND_STRATEGY_OUTPUT_DIR:-}"
VALIDATION_OUTPUT_DIR="${SECOND_STRATEGY_VALIDATION_OUTPUT_DIR:-}"

emit_scan_result() {
  local rc="$?"
  local status="ok"
  if [[ "$rc" -ne 0 ]]; then
    status="failed"
  fi
  echo "second strategy basket scan result: status=$status rc=$rc output_dir=${OUTPUT_DIR:-none} validation_output_dir=${VALIDATION_OUTPUT_DIR:-none}"
}
trap emit_scan_result EXIT

fail() {
  echo "second strategy basket scan failed: $*" >&2
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
UPDATE_LATEST_LINKS="${SECOND_STRATEGY_UPDATE_LATEST_LINKS:-true}"
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
RUN_PROOF_HORIZON="${SECOND_STRATEGY_RUN_PROOF_HORIZON:-true}"
PROOF_HORIZON_OUTPUT_DIR="${SECOND_STRATEGY_PROOF_HORIZON_OUTPUT_DIR:-$OUTPUT_DIR/proof_horizon}"
PROOF_HORIZON_LATEST_LINK="${SECOND_STRATEGY_PROOF_HORIZON_LATEST_LINK:-}"
PROOF_HORIZON_SAMPLE_SIZE="${SECOND_STRATEGY_PROOF_HORIZON_SAMPLE_SIZE:-160}"
PROOF_HORIZON_SAMPLE_SEED="${SECOND_STRATEGY_PROOF_HORIZON_SAMPLE_SEED:-second-strategy-proof-horizon}"
PROOF_HORIZON_MIN_TRADES="${SECOND_STRATEGY_PROOF_HORIZON_MIN_TRADES:-${PROOF_STATUS_SCALE_MIN_TRADES:-${PAPER_SCALE_MIN_TRADES:-30}}}"
PROOF_HORIZON_MIN_PNL="${SECOND_STRATEGY_PROOF_HORIZON_MIN_PNL:-${PROOF_STATUS_MIN_PNL:-${PROFIT_PROBE_MIN_PNL:-0.01}}}"
PROOF_HORIZON_MIN_ACTIVE_DAYS="${SECOND_STRATEGY_PROOF_HORIZON_MIN_ACTIVE_DAYS:-${PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS:-${PAPER_SCALE_MIN_ACTIVE_DAYS:-5}}}"
PROOF_HORIZON_MIN_PROFIT_FACTOR="${SECOND_STRATEGY_PROOF_HORIZON_MIN_PROFIT_FACTOR:-${PROOF_STATUS_SCALE_MIN_PROFIT_FACTOR:-${PAPER_SCALE_MIN_PROFIT_FACTOR:-1.20}}}"
PROOF_HORIZON_MAX_SINGLE_WIN_PNL_SHARE="${SECOND_STRATEGY_PROOF_HORIZON_MAX_SINGLE_WIN_PNL_SHARE:-${PROOF_STATUS_SCALE_MAX_SINGLE_WIN_PNL_SHARE:-${PAPER_SCALE_MAX_SINGLE_WIN_PNL_SHARE:-0.50}}}"
PROOF_HORIZON_MAX_EOD_LOSS_SHARE="${SECOND_STRATEGY_PROOF_HORIZON_MAX_EOD_LOSS_SHARE:-${PROOF_STATUS_SCALE_MAX_EOD_LOSS_SHARE:-${PAPER_SCALE_MAX_EOD_LOSS_SHARE:-0.50}}}"
PROOF_HORIZON_MIN_PASS_RATE="${SECOND_STRATEGY_PROOF_HORIZON_MIN_PASS_RATE:-${PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE:-0.50}}"
RESUME_COMPLETED_JOBS="${SECOND_STRATEGY_RESUME_COMPLETED_JOBS:-true}"
INCLUDE_OPTION_CANDIDATES="${SECOND_STRATEGY_INCLUDE_OPTION_CANDIDATES:-auto}"
HOST_OPTION_CHAIN_SNAPSHOT_DIR="${SECOND_STRATEGY_HOST_OPTION_CHAIN_SNAPSHOT_DIR:-/var/lib/alpaca-bot/option-chain-snapshots}"
OPTION_CHAIN_SNAPSHOTS="${SECOND_STRATEGY_OPTION_CHAIN_SNAPSHOTS:-}"
OPTION_REPLAY_MIN_SESSIONS="${SECOND_STRATEGY_OPTION_REPLAY_MIN_SESSIONS:-${PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS:-${PAPER_SCALE_MIN_ACTIVE_DAYS:-5}}}"
if [[ -z "$OPTION_CHAIN_SNAPSHOTS" ]]; then
  OPTION_CHAIN_SNAPSHOTS="${OPTION_CHAIN_SNAPSHOT_DIR:-}"
  if [[ "$OPTION_CHAIN_SNAPSHOTS" == "/data/option-chain-snapshots" && -d "$HOST_OPTION_CHAIN_SNAPSHOT_DIR" ]]; then
    OPTION_CHAIN_SNAPSHOTS="$HOST_OPTION_CHAIN_SNAPSHOT_DIR"
  fi
fi
KNOWN_OPTION_CANDIDATES="${SECOND_STRATEGY_KNOWN_OPTION_CANDIDATES:-}"
if [[ -z "$KNOWN_OPTION_CANDIDATES" ]]; then
  KNOWN_OPTION_CANDIDATES="$(python3 - <<'PY'
from alpaca_bot.strategy import OPTION_STRATEGY_NAMES

print(",".join(sorted(OPTION_STRATEGY_NAMES)))
PY
)"
fi
mapfile -t known_option_candidates < <(read_name_list "$KNOWN_OPTION_CANDIDATES")

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
case "${RESUME_COMPLETED_JOBS,,}" in
  true|1|yes|y)
    RESUME_COMPLETED_JOBS=true
    ;;
  false|0|no|n|"")
    RESUME_COMPLETED_JOBS=false
    ;;
  *)
    fail "SECOND_STRATEGY_RESUME_COMPLETED_JOBS must be true or false"
    ;;
esac
case "${UPDATE_LATEST_LINKS,,}" in
  true|1|yes|y)
    UPDATE_LATEST_LINKS=true
    ;;
  false|0|no|n|"")
    UPDATE_LATEST_LINKS=false
    ;;
  *)
    fail "SECOND_STRATEGY_UPDATE_LATEST_LINKS must be true or false"
    ;;
esac
case "${RUN_PROOF_HORIZON,,}" in
  true|1|yes|y)
    RUN_PROOF_HORIZON=true
    ;;
  false|0|no|n|"")
    RUN_PROOF_HORIZON=false
    ;;
  *)
    fail "SECOND_STRATEGY_RUN_PROOF_HORIZON must be true or false"
    ;;
esac

[[ "$PROOF_HORIZON_SAMPLE_SIZE" =~ ^[0-9]+$ ]] \
  || fail "SECOND_STRATEGY_PROOF_HORIZON_SAMPLE_SIZE must be a non-negative integer"
[[ "$PROOF_HORIZON_MIN_TRADES" =~ ^[0-9]+$ && "$PROOF_HORIZON_MIN_TRADES" -gt 0 ]] \
  || fail "SECOND_STRATEGY_PROOF_HORIZON_MIN_TRADES must be a positive integer"
[[ "$PROOF_HORIZON_MIN_ACTIVE_DAYS" =~ ^[0-9]+$ && "$PROOF_HORIZON_MIN_ACTIVE_DAYS" -gt 0 ]] \
  || fail "SECOND_STRATEGY_PROOF_HORIZON_MIN_ACTIVE_DAYS must be a positive integer"
[[ "$PROOF_HORIZON_MIN_PNL" =~ ^-?[0-9]+([.][0-9]+)?$ ]] \
  || fail "SECOND_STRATEGY_PROOF_HORIZON_MIN_PNL must be a number"
[[ "$PROOF_HORIZON_MIN_PROFIT_FACTOR" =~ ^[0-9]+([.][0-9]+)?$ ]] \
  || fail "SECOND_STRATEGY_PROOF_HORIZON_MIN_PROFIT_FACTOR must be a non-negative number"
[[ "$PROOF_HORIZON_MAX_SINGLE_WIN_PNL_SHARE" =~ ^[0-9]+([.][0-9]+)?$ ]] \
  || fail "SECOND_STRATEGY_PROOF_HORIZON_MAX_SINGLE_WIN_PNL_SHARE must be a non-negative number"
[[ "$PROOF_HORIZON_MAX_EOD_LOSS_SHARE" =~ ^[0-9]+([.][0-9]+)?$ ]] \
  || fail "SECOND_STRATEGY_PROOF_HORIZON_MAX_EOD_LOSS_SHARE must be a non-negative number"
[[ "$PROOF_HORIZON_MIN_PASS_RATE" =~ ^(0([.][0-9]+)?|1([.]0+)?)$ ]] \
  || fail "SECOND_STRATEGY_PROOF_HORIZON_MIN_PASS_RATE must be between 0 and 1"
[[ "$OPTION_REPLAY_MIN_SESSIONS" =~ ^[0-9]+$ && "$OPTION_REPLAY_MIN_SESSIONS" -gt 0 ]] \
  || fail "SECOND_STRATEGY_OPTION_REPLAY_MIN_SESSIONS must be a positive integer"

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

option_snapshot_session_count() {
  local path="$1"
  python3 - "$path" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
files = [path] if path.is_file() else list(path.glob("option-chain-snapshots-*.jsonl"))
sessions = {
    match.group(1)
    for file_path in files
    if file_path.is_file()
    and file_path.stat().st_size > 0
    and (match := re.fullmatch(r"option-chain-snapshots-(\d{4}-\d{2}-\d{2})\.jsonl", file_path.name))
}
print(len(sessions))
PY
}

prefilter_summary_starting_equity() {
  local summary_path="$1"
  python3 - "$summary_path" <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text())
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
value = payload.get("starting_equity")
if value is None:
    raise SystemExit(0)
text = str(value).strip()
if text and text not in {"none", "scenario_default"}:
    print(text)
PY
}

freeze_option_snapshot_input() {
  local source_path="$1"
  local destination_root="$2"
  python3 -m alpaca_bot.replay.option_snapshots freeze \
    "$source_path" "$destination_root/option_chain_snapshots" \
    --interval-minutes "${ENTRY_TIMEFRAME_MINUTES:-15}"
}

OPTION_SNAPSHOT_CONTRACTS=0
OPTION_SNAPSHOT_SESSIONS=0
OPTION_SNAPSHOT_POINTS=0
if [[ -n "$OPTION_CHAIN_SNAPSHOTS" ]] && option_snapshot_path_has_files "$OPTION_CHAIN_SNAPSHOTS"; then
  OPTION_SNAPSHOT_CONTRACTS="$(option_snapshot_contract_count "$OPTION_CHAIN_SNAPSHOTS")"
  OPTION_SNAPSHOT_SESSIONS="$(option_snapshot_session_count "$OPTION_CHAIN_SNAPSHOTS")"
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
    && "$OPTION_SNAPSHOT_CONTRACTS" -gt 0 \
    && "$OPTION_SNAPSHOT_SESSIONS" =~ ^[0-9]+$ \
    && "$OPTION_SNAPSHOT_SESSIONS" -ge "$OPTION_REPLAY_MIN_SESSIONS" ]]; then
    INCLUDE_OPTION_CANDIDATES=true
  else
    INCLUDE_OPTION_CANDIDATES=false
  fi
fi

is_known_option_candidate() {
  local candidate="$1"
  local option_candidate
  for option_candidate in "${known_option_candidates[@]}"; do
    if [[ "$candidate" == "$option_candidate" ]]; then
      return 0
    fi
  done
  return 1
}

is_option_candidate() {
  local candidate="$1"
  is_known_option_candidate "$candidate"
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

requested_option_candidates=()
for candidate in "${candidates[@]}"; do
  if is_known_option_candidate "$candidate"; then
    requested_option_candidates+=("$candidate")
  fi
done
if [[ "$INCLUDE_OPTION_CANDIDATES" != "true" && "${#requested_option_candidates[@]}" -gt 0 ]]; then
  fail "option candidate(s) require supported option replay: ${requested_option_candidates[*]} option_replay_status=$OPTION_REPLAY_STATUS"
fi

if [[ "$INCLUDE_OPTION_CANDIDATES" == "true" ]]; then
  [[ -n "$OPTION_CHAIN_SNAPSHOTS" ]] || fail "SECOND_STRATEGY_OPTION_CHAIN_SNAPSHOTS or OPTION_CHAIN_SNAPSHOT_DIR is required when option candidates are included"
  option_snapshot_path_has_files "$OPTION_CHAIN_SNAPSHOTS" || fail "option-chain snapshot path is empty or missing: $OPTION_CHAIN_SNAPSHOTS"
  [[ "$OPTION_SNAPSHOT_CONTRACTS" =~ ^[0-9]+$ && "$OPTION_SNAPSHOT_CONTRACTS" -gt 0 ]] || fail "option-chain snapshot path has no replayable contracts: $OPTION_CHAIN_SNAPSHOTS"
  [[ "$OPTION_SNAPSHOT_SESSIONS" =~ ^[0-9]+$ && "$OPTION_SNAPSHOT_SESSIONS" -ge "$OPTION_REPLAY_MIN_SESSIONS" ]] || fail "option-chain snapshots require at least $OPTION_REPLAY_MIN_SESSIONS replay sessions; found $OPTION_SNAPSHOT_SESSIONS"
  frozen_option_snapshot="$(freeze_option_snapshot_input "$OPTION_CHAIN_SNAPSHOTS" "$OUTPUT_DIR")"
  IFS=$'\t' read -r OPTION_CHAIN_SNAPSHOTS OPTION_SNAPSHOT_CONTRACTS \
    OPTION_SNAPSHOT_SESSIONS OPTION_SNAPSHOT_POINTS <<< "$frozen_option_snapshot"
  [[ -d "$OPTION_CHAIN_SNAPSHOTS" ]] || fail "could not freeze option-chain snapshots: $OPTION_CHAIN_SNAPSHOTS"
  [[ "$OPTION_SNAPSHOT_CONTRACTS" =~ ^[0-9]+$ && "$OPTION_SNAPSHOT_CONTRACTS" -gt 0 ]] || fail "frozen option-chain snapshot has no replayable contracts: $OPTION_CHAIN_SNAPSHOTS"
  [[ "$OPTION_SNAPSHOT_SESSIONS" =~ ^[0-9]+$ && "$OPTION_SNAPSHOT_SESSIONS" -gt 0 ]] || fail "frozen option-chain snapshots have no replayable sessions: $OPTION_CHAIN_SNAPSHOTS"
  [[ "$OPTION_SNAPSHOT_SESSIONS" -ge "$OPTION_REPLAY_MIN_SESSIONS" ]] || fail "frozen option-chain snapshots require at least $OPTION_REPLAY_MIN_SESSIONS replay sessions; found $OPTION_SNAPSHOT_SESSIONS"
  [[ "$OPTION_SNAPSHOT_POINTS" =~ ^[0-9]+$ && "$OPTION_SNAPSHOT_POINTS" -gt 0 ]] || fail "frozen option-chain snapshots have no replayable points: $OPTION_CHAIN_SNAPSHOTS"
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
starting_equity_source=operator
if [[ -z "$starting_equity" && -n "$PREFILTER_SUMMARY_JSON" ]]; then
  starting_equity="$(prefilter_summary_starting_equity "$PREFILTER_SUMMARY_JSON")"
  if [[ -n "$starting_equity" ]]; then
    starting_equity_source=prefilter_summary
  fi
fi
if [[ -z "$starting_equity" ]]; then
  load_proof_status "loading live broker equity"
  broker_line="$(grep -E '^paper proof broker account: ' "$proof_output" | tail -n 1 || true)"
  starting_equity="$(extract_field "$broker_line" "equity" || true)"
  if [[ -n "$starting_equity" ]]; then
    starting_equity_source=live_broker_equity
  else
    starting_equity_source=scenario_default
  fi
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
echo "second strategy basket scan: scenario_dir=$SCENARIO_DIR base=$BASE_STRATEGY sample_size=$SAMPLE_SIZE sample_seed=$SAMPLE_SEED slippage_bps=$SLIPPAGE_BPS max_open_positions=$MAX_OPEN_POSITIONS_VALUE candidate_scales=${candidate_scales[*]} scan_jobs=$SCAN_JOBS starting_equity=${starting_equity:-scenario_default} starting_equity_source=$starting_equity_source excluded_candidates=${skipped_candidates[*]:-none} include_option_candidates=$INCLUDE_OPTION_CANDIDATES option_chain_snapshots=${OPTION_CHAIN_SNAPSHOTS:-none} option_snapshot_contracts=$OPTION_SNAPSHOT_CONTRACTS option_snapshot_sessions=$OPTION_SNAPSHOT_SESSIONS option_snapshot_points=$OPTION_SNAPSHOT_POINTS option_replay_min_sessions=$OPTION_REPLAY_MIN_SESSIONS option_replay_status=$OPTION_REPLAY_STATUS prefilter_summary_json=${PREFILTER_SUMMARY_JSON:-none}"

completed_status_part_is_reusable() {
  local status_part="$1"
  local expected_candidate="$2"
  local expected_scale="$3"
  local require_fingerprint="${4:-false}"
  local expected_fingerprint="${5:-}"
  local candidate
  local candidate_scale
  local status
  local report
  local jsonl
  local stderr
  local extra
  local fingerprint_path

  [[ "$RESUME_COMPLETED_JOBS" == "true" && -s "$status_part" ]] || return 1
  IFS=$'\t' read -r candidate candidate_scale status report jsonl stderr extra < "$status_part" || return 1
  [[ -z "${extra:-}" ]] || return 1
  [[ "$candidate" == "$expected_candidate" && "$candidate_scale" == "$expected_scale" ]] || return 1
  [[ "$status" == "passed" ]] || return 1
  [[ -s "$report" && -s "$jsonl" && -e "$stderr" ]] || return 1
  if [[ "$require_fingerprint" == "true" ]]; then
    fingerprint_path="$status_part.fingerprint"
    [[ -f "$fingerprint_path" ]] || return 1
    [[ "$(tr -d '\n' < "$fingerprint_path")" == "$expected_fingerprint" ]] || return 1
  fi
}

write_status_part_fingerprint() {
  local status_part="$1"
  local fingerprint="$2"
  printf '%s\n' "$fingerprint" > "$status_part.fingerprint"
}

failed_count=0
run_prefilter_job() {
  local candidate="$1"
  local candidate_scale="$2"
  local safe_candidate
  local safe_scale
  local report_path
  local jsonl_path
  local stderr_path
  local tmp_report_path
  local tmp_jsonl_path
  local tmp_stderr_path
  local status_part
  local require_fingerprint
  local job_fingerprint
  local -a cmd

  safe_candidate="$(printf '%s' "$candidate" | tr -c 'A-Za-z0-9_' '_')"
  safe_scale="$(printf '%s' "$candidate_scale" | tr -c 'A-Za-z0-9_' '_')"
  report_path="$OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_basket.md"
  jsonl_path="$OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_basket.jsonl"
  stderr_path="$OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_basket.stderr"
  tmp_report_path="$report_path.tmp.$BASHPID"
  tmp_jsonl_path="$jsonl_path.tmp.$BASHPID"
  tmp_stderr_path="$stderr_path.tmp.$BASHPID"
  status_part="$status_parts_dir/${safe_candidate}_scale_${safe_scale}.tsv"
  require_fingerprint=true
  job_fingerprint="prefilter|scenario=$SCENARIO_DIR|base=$BASE_STRATEGY|sample=$SAMPLE_SIZE|seed=$SAMPLE_SEED|slippage=$SLIPPAGE_BPS|max_open=$MAX_OPEN_POSITIONS_VALUE|equity=${starting_equity:-scenario_default}|options=$INCLUDE_OPTION_CANDIDATES|option_path=${OPTION_CHAIN_SNAPSHOTS:-none}|option_contracts=$OPTION_SNAPSHOT_CONTRACTS|option_sessions=$OPTION_SNAPSHOT_SESSIONS|option_points=$OPTION_SNAPSHOT_POINTS|option_replay=$OPTION_REPLAY_STATUS|diagnostics=trade_attribution_v2"
  if completed_status_part_is_reusable "$status_part" "$candidate" "$candidate_scale" "$require_fingerprint" "$job_fingerprint"; then
    echo "second strategy basket scan: reusing completed candidate=$candidate scale=$candidate_scale"
    return 0
  fi
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
    --output "$tmp_report_path"
    --jsonl "$tmp_jsonl_path"
  )
  if [[ -n "$starting_equity" && "$starting_equity" != "none" ]]; then
    cmd+=(--starting-equity "$starting_equity")
  fi
  if is_option_candidate "$candidate"; then
    cmd+=(--option-chain-snapshots "$OPTION_CHAIN_SNAPSHOTS")
  fi

  echo "second strategy basket scan: candidate=$candidate scale=$candidate_scale"
  if "${cmd[@]}" 2> "$tmp_stderr_path"; then
    publish_replay_artifacts "$tmp_report_path" "$report_path" "$tmp_jsonl_path" "$jsonl_path" "$tmp_stderr_path" "$stderr_path"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$candidate_scale" "passed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
    write_status_part_fingerprint "$status_part" "$job_fingerprint"
    return 0
  fi
  discard_replay_artifacts "$tmp_report_path" "$tmp_jsonl_path" "$tmp_stderr_path" "$stderr_path"
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
import os
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


def write_text_atomic(path: Path, text: str) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(text)
    tmp_path.replace(path)


def candidate_contribution(audit_row, candidate: str):
    diagnostics = audit_row.get("trade_diagnostics") or {}
    for row in diagnostics.get("strategies", []):
        if row.get("strategy") == candidate:
            return {
                "trades": int(row.get("trades") or 0),
                "total_pnl": row.get("total_pnl"),
                "mean_trade_pnl": row.get("mean_trade_pnl"),
                "ci_low": row.get("ci_low"),
                "ci_high": row.get("ci_high"),
                "p_mean_le_zero": row.get("p_mean_le_zero"),
                "verdict": row.get("verdict"),
            }
    return {
        "trades": None,
        "total_pnl": None,
        "mean_trade_pnl": None,
        "ci_low": None,
        "ci_high": None,
        "p_mean_le_zero": None,
        "verdict": None,
    }


def contribution_status(contribution) -> str:
    trades = contribution["trades"]
    total_pnl = contribution["total_pnl"]
    if trades is None:
        return "unknown"
    if trades <= 0:
        return "no_trades"
    if total_pnl is not None and float(total_pnl) <= 0.0:
        return "non_positive_pnl"
    return "positive_pnl"


def evidence_verdict(audit_row, candidate: str) -> str:
    basket_verdict = audit_row.get("verdict")
    contribution = candidate_contribution(audit_row, candidate)
    status = contribution_status(contribution)
    if status == "no_trades":
        return "no-candidate-trades"
    if status == "non_positive_pnl":
        return "non-positive-candidate-pnl"
    candidate_verdict = contribution.get("verdict")
    if candidate_verdict:
        return candidate_verdict
    if basket_verdict == "positive-edge":
        return "missing-candidate-edge-diagnostics"
    return basket_verdict


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
            trade_diagnostics = payloads[-1].get("trade_diagnostics")
            if trade_diagnostics is not None:
                audit_row["trade_diagnostics"] = trade_diagnostics
    rows.append((candidate, candidate_scale, status, report, stderr, audit_row))

candidate_names = []
for candidate, *_rest in rows:
    if candidate not in candidate_names:
        candidate_names.append(candidate)


def sort_key(item):
    candidate, candidate_scale, status, _report, _stderr, audit_row = item
    if status != "passed" or audit_row is None:
        return (3, 0.0, candidate, float(candidate_scale))
    verdict_rank = {
        "positive-edge": 0,
        "no-evidence": 1,
        "insufficient-data": 2,
        "negative-edge": 2,
        "no-candidate-trades": 2,
        "non-positive-candidate-pnl": 2,
        "missing-candidate-edge-diagnostics": 2,
    }.get(evidence_verdict(audit_row, candidate), 2)
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
    f"- candidate_names: `{','.join(candidate_names) if candidate_names else 'none'}`",
    "",
    "| candidate | scale | status | trades | candidate trades | candidate P&L | candidate mean/trade | candidate 95% CI mean/trade | candidate p(mean<=0) | basket total P&L | basket 95% CI mean/trade | basket verdict | verdict | report |",
    "|---|---:|---|---:|---:|---:|---:|---|---:|---:|---|---|---|---|",
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
            f"| `{candidate}` | {candidate_scale} | `{status}` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `{stderr}` |"
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
        f"`{candidate}` | {candidate_scale} | `passed` | {audit_row['trades']} | "
        f"{fmt(candidate_stats['trades'], '.0f')} | "
        f"{fmt(candidate_stats['total_pnl'])} | "
        f"{fmt(candidate_stats['mean_trade_pnl'], '.4f')} | {candidate_ci} | "
        f"{fmt(candidate_stats['p_mean_le_zero'], '.4f')} | "
        f"{fmt(audit_row['total_pnl'])} | {ci} | "
        f"`{basket_verdict}` | `{verdict}` | `{report}` |"
    )

lines.extend([
    "",
    (
        "Promotion note: a positive prefilter row is only a survivor for a "
        "separate independent validation; it is not approval to change "
        "PAPER_APPROVED_STRATEGIES."
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
            "candidate_scales": candidate_scales,
            "scan_jobs": scan_jobs,
            "starting_equity": starting_equity,
            "excluded_candidates": excluded_candidates,
            "include_option_candidates": include_option_candidates,
            "option_chain_snapshots": option_chain_snapshots,
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
fi

validation_failed_count=0
proof_horizon_failed_count=0
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
        ci_low = row.get("candidate_ci_low", row.get("ci_low"))
        p_mean_le_zero = row.get(
            "candidate_p_mean_le_zero",
            row.get("p_mean_le_zero"),
        )
        total_pnl = row.get("candidate_total_pnl", row.get("total_pnl"))
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

  validation_option_candidates=()
  while IFS=$'\t' read -r candidate _candidate_scale; do
    [[ -n "$candidate" ]] || continue
    if is_known_option_candidate "$candidate"; then
      validation_option_candidates+=("$candidate")
    fi
  done < "$validation_specs_file"
  if [[ "$INCLUDE_OPTION_CANDIDATES" != "true" && "${#validation_option_candidates[@]}" -gt 0 ]]; then
    fail "option validation candidate(s) require supported option replay: ${validation_option_candidates[*]} option_replay_status=$OPTION_REPLAY_STATUS"
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
    local tmp_report_path
    local tmp_jsonl_path
    local tmp_stderr_path
    local status_part
    local require_fingerprint
    local job_fingerprint
    local -a cmd

    safe_candidate="$(printf '%s' "$candidate" | tr -c 'A-Za-z0-9_' '_')"
    safe_scale="$(printf '%s' "$candidate_scale" | tr -c 'A-Za-z0-9_' '_')"
    report_path="$VALIDATION_OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_validation.md"
    jsonl_path="$VALIDATION_OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_validation.jsonl"
    stderr_path="$VALIDATION_OUTPUT_DIR/${safe_candidate}_scale_${safe_scale}_validation.stderr"
    tmp_report_path="$report_path.tmp.$BASHPID"
    tmp_jsonl_path="$jsonl_path.tmp.$BASHPID"
    tmp_stderr_path="$stderr_path.tmp.$BASHPID"
    status_part="$validation_status_parts_dir/${safe_candidate}_scale_${safe_scale}.tsv"
    require_fingerprint=true
    job_fingerprint="validation|scenario=$SCENARIO_DIR|base=$BASE_STRATEGY|sample=$VALIDATION_SAMPLE_SIZE|seed=$VALIDATION_SAMPLE_SEED|slippage=$SLIPPAGE_BPS|max_open=$MAX_OPEN_POSITIONS_VALUE|equity=${starting_equity:-scenario_default}|options=$INCLUDE_OPTION_CANDIDATES|option_path=${OPTION_CHAIN_SNAPSHOTS:-none}|option_contracts=$OPTION_SNAPSHOT_CONTRACTS|option_sessions=$OPTION_SNAPSHOT_SESSIONS|option_points=$OPTION_SNAPSHOT_POINTS|option_replay=$OPTION_REPLAY_STATUS|diagnostics=trade_attribution_v2"
    if completed_status_part_is_reusable "$status_part" "$candidate" "$candidate_scale" "$require_fingerprint" "$job_fingerprint"; then
      echo "second strategy basket validation: reusing completed candidate=$candidate scale=$candidate_scale"
      return 0
    fi
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
    if is_option_candidate "$candidate"; then
      cmd+=(--option-chain-snapshots "$OPTION_CHAIN_SNAPSHOTS")
    fi

    echo "second strategy basket validation: candidate=$candidate scale=$candidate_scale"
    if "${cmd[@]}" 2> "$tmp_stderr_path"; then
      publish_replay_artifacts "$tmp_report_path" "$report_path" "$tmp_jsonl_path" "$jsonl_path" "$tmp_stderr_path" "$stderr_path"
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$candidate" "$candidate_scale" "passed" "$report_path" "$jsonl_path" "$stderr_path" > "$status_part"
      write_status_part_fingerprint "$status_part" "$job_fingerprint"
      return 0
    fi
    discard_replay_artifacts "$tmp_report_path" "$tmp_jsonl_path" "$tmp_stderr_path" "$stderr_path"
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
import os
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


def write_text_atomic(path: Path, text: str) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(text)
    tmp_path.replace(path)


def candidate_contribution(audit_row, candidate: str):
    diagnostics = audit_row.get("trade_diagnostics") or {}
    for row in diagnostics.get("strategies", []):
        if row.get("strategy") == candidate:
            return {
                "trades": int(row.get("trades") or 0),
                "total_pnl": row.get("total_pnl"),
                "mean_trade_pnl": row.get("mean_trade_pnl"),
                "ci_low": row.get("ci_low"),
                "ci_high": row.get("ci_high"),
                "p_mean_le_zero": row.get("p_mean_le_zero"),
                "verdict": row.get("verdict"),
            }
    return {
        "trades": None,
        "total_pnl": None,
        "mean_trade_pnl": None,
        "ci_low": None,
        "ci_high": None,
        "p_mean_le_zero": None,
        "verdict": None,
    }


def contribution_status(contribution) -> str:
    trades = contribution["trades"]
    total_pnl = contribution["total_pnl"]
    if trades is None:
        return "unknown"
    if trades <= 0:
        return "no_trades"
    if total_pnl is not None and float(total_pnl) <= 0.0:
        return "non_positive_pnl"
    return "positive_pnl"


def evidence_verdict(audit_row, candidate: str) -> str:
    basket_verdict = audit_row.get("verdict")
    contribution = candidate_contribution(audit_row, candidate)
    status = contribution_status(contribution)
    if status == "no_trades":
        return "no-candidate-trades"
    if status == "non_positive_pnl":
        return "non-positive-candidate-pnl"
    candidate_verdict = contribution.get("verdict")
    if candidate_verdict:
        return candidate_verdict
    if basket_verdict == "positive-edge":
        return "missing-candidate-edge-diagnostics"
    return basket_verdict


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
            trade_diagnostics = payloads[-1].get("trade_diagnostics")
            if trade_diagnostics is not None:
                audit_row["trade_diagnostics"] = trade_diagnostics
    rows.append((candidate, candidate_scale, status, report, stderr, audit_row))

candidate_names = []
for candidate, *_rest in rows:
    if candidate not in candidate_names:
        candidate_names.append(candidate)


def sort_key(item):
    candidate, candidate_scale, status, _report, _stderr, audit_row = item
    if status != "passed" or audit_row is None:
        return (3, 0.0, candidate, float(candidate_scale))
    verdict_rank = {
        "positive-edge": 0,
        "no-evidence": 1,
        "insufficient-data": 2,
        "negative-edge": 2,
        "no-candidate-trades": 2,
        "non-positive-candidate-pnl": 2,
        "missing-candidate-edge-diagnostics": 2,
    }.get(evidence_verdict(audit_row, candidate), 2)
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
    f"- candidate_names: `{','.join(candidate_names) if candidate_names else 'none'}`",
    "",
    "| candidate | scale | status | trades | candidate trades | candidate P&L | candidate mean/trade | candidate 95% CI mean/trade | candidate p(mean<=0) | basket total P&L | basket 95% CI mean/trade | basket verdict | verdict | report |",
    "|---|---:|---|---:|---:|---:|---:|---|---:|---:|---|---|---|---|",
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
            f"| `{candidate}` | {candidate_scale} | `{status}` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `{stderr}` |"
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
        f"`{candidate}` | {candidate_scale} | `passed` | {audit_row['trades']} | "
        f"{fmt(candidate_stats['trades'], '.0f')} | "
        f"{fmt(candidate_stats['total_pnl'])} | "
        f"{fmt(candidate_stats['mean_trade_pnl'], '.4f')} | {candidate_ci} | "
        f"{fmt(candidate_stats['p_mean_le_zero'], '.4f')} | "
        f"{fmt(audit_row['total_pnl'])} | {ci} | "
        f"`{basket_verdict}` | `{verdict}` | `{report}` |"
    )

if not rows:
    lines.append("| `none` | n/a | `skipped` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |")

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
            "max_validation_candidates": max_validation_candidates,
            "scan_jobs": scan_jobs,
            "starting_equity": starting_equity,
            "excluded_candidates": excluded_candidates,
            "include_option_candidates": include_option_candidates,
            "option_chain_snapshots": option_chain_snapshots,
            "validate_all_positive_rows": validate_all_positive_rows,
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

  proof_horizon_candidates_file="$VALIDATION_OUTPUT_DIR/proof_horizon_candidates.tsv"
  python3 - "$validation_summary_json_file" "$proof_horizon_candidates_file" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

from alpaca_bot.strategy import OPTION_STRATEGY_NAMES, STRATEGY_REGISTRY

summary_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])


def as_float(row: dict[str, object], key: str) -> float | None:
    try:
        return float(row.get(key))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def as_int(row: dict[str, object], key: str) -> int | None:
    try:
        return int(row.get(key))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


payload = json.loads(summary_path.read_text())
rows = payload.get("rows")
candidates: list[tuple[float, float, int, str, str]] = []
stock_strategy_names = set(STRATEGY_REGISTRY)
option_strategy_names = set(OPTION_STRATEGY_NAMES)
if isinstance(rows, list):
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("candidate") or "").strip()
        if name not in stock_strategy_names or name in option_strategy_names:
            continue
        if row.get("status") != "passed":
            continue
        if row.get("verdict") != "positive-edge":
            continue
        if row.get("candidate_verdict") != "positive-edge":
            continue
        if row.get("candidate_contribution_status") != "positive_pnl":
            continue
        trades = as_int(row, "candidate_trades")
        total_pnl = as_float(row, "candidate_total_pnl")
        ci_low = as_float(row, "candidate_ci_low")
        p_mean_le_zero = as_float(row, "candidate_p_mean_le_zero")
        scale = str(row.get("candidate_scale") or "").strip()
        if (
            trades is None
            or total_pnl is None
            or ci_low is None
            or p_mean_le_zero is None
            or not scale
        ):
            continue
        if trades < 30 or total_pnl <= 0.0 or ci_low <= 0.0 or p_mean_le_zero > 0.05:
            continue
        candidates.append((ci_low, -p_mean_le_zero, trades, name, scale))

selected = sorted(candidates, reverse=True)
output_path.write_text(
    "".join(f"{name}\t{scale}\n" for _ci, _p, _trades, name, scale in selected)
)
if selected:
    print(f"proof_horizon_candidates={len(selected)}")
    for rank, (_ci, _p, trades, name, scale) in enumerate(selected, start=1):
        print(
            "proof_horizon_candidate="
            f"{name} scale={scale} rank={rank} trades={trades} "
            f"candidate_ci_low={_ci:g}"
        )
else:
    print("proof_horizon_candidates=0")
    print("proof_horizon_candidate=none")
PY

  proof_horizon_results_file="$PROOF_HORIZON_OUTPUT_DIR/results.tsv"
  proof_horizon_results_parts_dir="$PROOF_HORIZON_OUTPUT_DIR/result_parts"
  run_proof_horizon_job() {
    local candidate="$1"
    local candidate_scale="$2"
    local safe_candidate
    local safe_scale
    local report_path
    local json_path
    local stderr_path
    local tmp_report_path
    local tmp_json_path
    local tmp_stderr_path
    local candidate_output_dir
    local result_part
    local -a cmd

    safe_candidate="$(printf '%s' "$candidate" | tr -c 'A-Za-z0-9_' '_')"
    safe_scale="$(printf '%s' "$candidate_scale" | tr -c 'A-Za-z0-9_' '_')"
    candidate_output_dir="$PROOF_HORIZON_OUTPUT_DIR/candidates/${safe_candidate}_scale_${safe_scale}"
    result_part="$proof_horizon_results_parts_dir/${safe_candidate}_scale_${safe_scale}.tsv"
    mkdir -p "$candidate_output_dir"
    report_path="$candidate_output_dir/summary.md"
    json_path="$candidate_output_dir/summary.json"
    stderr_path="$candidate_output_dir/stderr.txt"
    tmp_report_path="$report_path.tmp.$BASHPID"
    tmp_json_path="$json_path.tmp.$BASHPID"
    tmp_stderr_path="$stderr_path.tmp.$BASHPID"
    cmd=(
      python3 -m alpaca_bot.replay.cli proof-horizon-basket
      --scenario-dir "$SCENARIO_DIR"
      --strategy "$BASE_STRATEGY"
      --strategy "$candidate"
      --sample-size "$PROOF_HORIZON_SAMPLE_SIZE"
      --sample-seed "$PROOF_HORIZON_SAMPLE_SEED"
      --slippage-bps "$SLIPPAGE_BPS"
      --max-open-positions "$MAX_OPEN_POSITIONS_VALUE"
      --confidence-scale "$candidate=$candidate_scale"
      --min-trades "$PROOF_HORIZON_MIN_TRADES"
      --min-pnl "$PROOF_HORIZON_MIN_PNL"
      --min-active-days "$PROOF_HORIZON_MIN_ACTIVE_DAYS"
      --min-profit-factor "$PROOF_HORIZON_MIN_PROFIT_FACTOR"
      --max-single-win-pnl-share "$PROOF_HORIZON_MAX_SINGLE_WIN_PNL_SHARE"
      --max-eod-loss-share "$PROOF_HORIZON_MAX_EOD_LOSS_SHARE"
      --output "$tmp_report_path"
      --json "$tmp_json_path"
    )
    if [[ -n "$starting_equity" && "$starting_equity" != "none" ]]; then
      cmd+=(--starting-equity "$starting_equity")
    fi

    echo "second strategy basket proof horizon: candidate=$candidate scale=$candidate_scale output_dir=$candidate_output_dir sample_size=$PROOF_HORIZON_SAMPLE_SIZE sample_seed=$PROOF_HORIZON_SAMPLE_SEED min_trades=$PROOF_HORIZON_MIN_TRADES min_pnl=$PROOF_HORIZON_MIN_PNL min_active_days=$PROOF_HORIZON_MIN_ACTIVE_DAYS min_profit_factor=$PROOF_HORIZON_MIN_PROFIT_FACTOR max_single_win_pnl_share=$PROOF_HORIZON_MAX_SINGLE_WIN_PNL_SHARE max_eod_loss_share=$PROOF_HORIZON_MAX_EOD_LOSS_SHARE min_pass_rate=$PROOF_HORIZON_MIN_PASS_RATE"
    if "${cmd[@]}" 2> "$tmp_stderr_path"; then
      mv -f "$tmp_report_path" "$report_path"
      mv -f "$tmp_json_path" "$json_path"
      mv -f "$tmp_stderr_path" "$stderr_path"
      printf '%s\t%s\tpassed\t%s\t%s\t%s\n' \
        "$candidate" "$candidate_scale" "$report_path" "$json_path" "$stderr_path" \
        > "$result_part"
      return 0
    fi
    rm -f "$tmp_report_path" "$tmp_json_path"
    if [[ -e "$tmp_stderr_path" ]]; then
      mv -f "$tmp_stderr_path" "$stderr_path"
    fi
    printf '%s\t%s\tfailed\t%s\t%s\t%s\n' \
      "$candidate" "$candidate_scale" "$report_path" "$json_path" "$stderr_path" \
      > "$result_part"
    return 1
  }

  wait_for_next_proof_horizon_job() {
    if ! wait -n; then
      proof_horizon_failed_count=$((proof_horizon_failed_count + 1))
    fi
    proof_horizon_running_jobs=$((proof_horizon_running_jobs - 1))
  }

  if [[ "$RUN_PROOF_HORIZON" == "true" ]]; then
    if [[ -s "$proof_horizon_candidates_file" ]]; then
      mkdir -p "$PROOF_HORIZON_OUTPUT_DIR" "$proof_horizon_results_parts_dir"
      rm -f "$proof_horizon_results_parts_dir"/*.tsv
      : > "$proof_horizon_results_file"
      proof_horizon_running_jobs=0
      while IFS=$'\t' read -r proof_candidate proof_candidate_scale; do
        [[ -n "$proof_candidate" && -n "$proof_candidate_scale" ]] || continue
        run_proof_horizon_job "$proof_candidate" "$proof_candidate_scale" &
        proof_horizon_running_jobs=$((proof_horizon_running_jobs + 1))
        if [[ "$proof_horizon_running_jobs" -ge "$SCAN_JOBS" ]]; then
          wait_for_next_proof_horizon_job
        fi
      done < "$proof_horizon_candidates_file"
      while [[ "$proof_horizon_running_jobs" -gt 0 ]]; do
        wait_for_next_proof_horizon_job
      done
      while IFS=$'\t' read -r proof_candidate proof_candidate_scale; do
        [[ -n "$proof_candidate" && -n "$proof_candidate_scale" ]] || continue
        safe_candidate="$(printf '%s' "$proof_candidate" | tr -c 'A-Za-z0-9_' '_')"
        safe_scale="$(printf '%s' "$proof_candidate_scale" | tr -c 'A-Za-z0-9_' '_')"
        result_part="$proof_horizon_results_parts_dir/${safe_candidate}_scale_${safe_scale}.tsv"
        if [[ -s "$result_part" ]]; then
          cat "$result_part" >> "$proof_horizon_results_file"
        else
          proof_horizon_failed_count=$((proof_horizon_failed_count + 1))
        fi
      done < "$proof_horizon_candidates_file"
      if [[ "$proof_horizon_failed_count" -eq 0 ]]; then
        python3 -m alpaca_bot.replay.proof_horizon_selection \
          --results "$proof_horizon_results_file" \
          --output-dir "$PROOF_HORIZON_OUTPUT_DIR" \
          --min-eventual-pass-rate "$PROOF_HORIZON_MIN_PASS_RATE" \
          --default-min-pnl "$PROOF_HORIZON_MIN_PNL"
        echo "proof_horizon_summary=$PROOF_HORIZON_OUTPUT_DIR/summary.md"
        echo "proof_horizon_summary_json=$PROOF_HORIZON_OUTPUT_DIR/summary.json"
        if [[ -z "$PROOF_HORIZON_LATEST_LINK" && "$UPDATE_LATEST_LINKS" == "true" ]]; then
          PROOF_HORIZON_LATEST_LINK="$OUTPUT_ROOT/latest_proof_horizon"
        fi
        if [[ -n "$PROOF_HORIZON_LATEST_LINK" ]]; then
          update_latest_link "$PROOF_HORIZON_OUTPUT_DIR" "$PROOF_HORIZON_LATEST_LINK"
          echo "latest_proof_horizon=$PROOF_HORIZON_LATEST_LINK"
        fi
      fi
    else
      echo "second strategy basket proof horizon: no promotable validation candidate"
    fi
  else
    echo "second strategy basket proof horizon: disabled"
  fi

  if [[ -z "$VALIDATION_LATEST_LINK" && "$UPDATE_LATEST_LINKS" == "true" ]]; then
    VALIDATION_LATEST_LINK="$OUTPUT_ROOT/latest_validation"
  fi
  if [[ -n "$VALIDATION_LATEST_LINK" ]]; then
    update_latest_link "$VALIDATION_OUTPUT_DIR" "$VALIDATION_LATEST_LINK"
    echo "latest_validation=$VALIDATION_LATEST_LINK"
  fi
else
  echo "second strategy basket validation: disabled"
fi

if [[ "$prefilter_skipped" != "true" && -z "$LATEST_LINK" && "$UPDATE_LATEST_LINKS" == "true" ]]; then
  LATEST_LINK="$OUTPUT_ROOT/latest"
fi
if [[ "$prefilter_skipped" != "true" && -n "$LATEST_LINK" ]]; then
  update_latest_link "$OUTPUT_DIR" "$LATEST_LINK"
  echo "latest=$LATEST_LINK"
fi

if [[ "$failed_count" -gt 0 ]]; then
  fail "$failed_count candidate scan command(s) failed; see $OUTPUT_DIR"
fi
if [[ "$validation_failed_count" -gt 0 ]]; then
  fail "$validation_failed_count validation command(s) failed; see $VALIDATION_OUTPUT_DIR"
fi
if [[ "$proof_horizon_failed_count" -gt 0 ]]; then
  fail "$proof_horizon_failed_count proof horizon command(s) failed; see $PROOF_HORIZON_OUTPUT_DIR"
fi
