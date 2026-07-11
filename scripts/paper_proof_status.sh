#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"

_preserved_env_names=()
_preserved_env_values=()

capture_env_overrides() {
  local name
  for name in "$@"; do
    if [[ -n "${!name+x}" ]]; then
      _preserved_env_names+=("$name")
      _preserved_env_values+=("${!name}")
    fi
  done
}

restore_env_overrides() {
  local index
  for index in "${!_preserved_env_names[@]}"; do
    printf -v "${_preserved_env_names[$index]}" '%s' "${_preserved_env_values[$index]}"
    export "${_preserved_env_names[$index]}"
  done
}

capture_env_overrides \
  PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE \
  PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER \
  PROFIT_PROBE_STRATEGY \
  PROFIT_PROBE_MIN_TRADES \
  PROFIT_PROBE_MIN_PNL \
  PROFIT_PROBE_START_DATE \
  PAPER_SCALE_MIN_TRADES \
  PAPER_APPROVED_STRATEGIES \
  SESSION_GUARD_MIN_TRADES \
  SESSION_GUARD_FAIL_BELOW_PNL \
  PROOF_STATUS_STRATEGY \
  PROOF_STATUS_APPROVED_STRATEGIES \
  PROOF_STATUS_MIN_TRADES \
  PROOF_STATUS_MIN_PNL \
  PROOF_STATUS_SESSION_GUARD_MIN_TRADES \
  PROOF_STATUS_SESSION_GUARD_MIN_PNL \
  PROOF_STATUS_START_DATE \
  PROOF_STATUS_END_DATE \
  PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT \
  PROOF_STATUS_FAIL_ON_ISSUES \
  PROOF_STATUS_MIN_WATCHLIST_SYMBOLS \
  PROOF_STATUS_MIN_CONFIDENCE_FLOOR \
  PROOF_STATUS_REQUIRE_SCENARIOS \
  PROOF_STATUS_SCENARIO_DIR \
  PROOF_STATUS_STREAM_START_GRACE_SECONDS \
  PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES \
  PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS \
  PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS \
  PROOF_STATUS_NIGHTLY_LOCK_FILE \
  PROOF_STATUS_NIGHTLY_LOG \
  PROOF_STATUS_SECOND_STRATEGY_LOG \
  PROOF_STATUS_NIGHTLY_MAX_AGE_MINUTES \
  PROOF_STATUS_NIGHTLY_STALL_MINUTES \
  PROOF_STATUS_SCALE_MIN_TRADES \
  PROOF_STATUS_SCALE_MIN_STRATEGIES \
  PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS \
  PROOF_STATUS_SCALE_MAX_SINGLE_WIN_PNL_SHARE \
  PROOF_STATUS_SCALE_MIN_PROFIT_FACTOR \
  PROOF_STATUS_SCALE_MAX_EOD_LOSS_SHARE \
  PROOF_STATUS_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE \
  PROOF_STATUS_EXECUTION_MIN_ENTRY_FILL_RATE \
  PROOF_STATUS_EXECUTION_MAX_CAPACITY_REJECT_RATE \
  PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT \
  PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT \
  PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS \
  PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE \
  PROOF_STATUS_SECOND_STRATEGY_PROMOTION_DENYLIST \
  PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS \
  PROOF_STATUS_OPTION_REPLAY_MIN_POINTS_PER_SESSION

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
restore_env_overrides

PROOF_STATUS_STRATEGY="${PROOF_STATUS_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
PAPER_APPROVED_STRATEGIES_RESOLVED="${PAPER_APPROVED_STRATEGIES:-$PROOF_STATUS_STRATEGY}"
if resolved_approved_strategies="$(bash ./scripts/resolve_paper_approved_strategies.sh "$ENV_FILE" "$PROOF_STATUS_STRATEGY" 2>/dev/null)" \
  && [[ -n "$resolved_approved_strategies" ]]; then
  PAPER_APPROVED_STRATEGIES_RESOLVED="$resolved_approved_strategies"
fi
PROOF_STATUS_APPROVED_STRATEGIES="${PROOF_STATUS_APPROVED_STRATEGIES:-$PAPER_APPROVED_STRATEGIES_RESOLVED}"
PROOF_STATUS_MIN_TRADES="${PROOF_STATUS_MIN_TRADES:-${PROFIT_PROBE_MIN_TRADES:-${PAPER_SCALE_MIN_TRADES:-30}}}"
PROOF_STATUS_MIN_PNL="${PROOF_STATUS_MIN_PNL:-${PROFIT_PROBE_MIN_PNL:-0.01}}"
PROOF_STATUS_SESSION_GUARD_MIN_TRADES="${PROOF_STATUS_SESSION_GUARD_MIN_TRADES:-${SESSION_GUARD_MIN_TRADES:-10}}"
PROOF_STATUS_SESSION_GUARD_MIN_PNL="${PROOF_STATUS_SESSION_GUARD_MIN_PNL:-${SESSION_GUARD_FAIL_BELOW_PNL:-0}}"
PROOF_STATUS_START_DATE="${PROOF_STATUS_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-07-07}}"
PROOF_STATUS_END_DATE="${PROOF_STATUS_END_DATE:-}"
PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT="${PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT:-./scripts/runtime_image_health_check.sh}"
PROOF_STATUS_FAIL_ON_ISSUES="${PROOF_STATUS_FAIL_ON_ISSUES:-false}"
PROOF_STATUS_MIN_WATCHLIST_SYMBOLS="${PROOF_STATUS_MIN_WATCHLIST_SYMBOLS:-${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}}"
PROOF_STATUS_MIN_CONFIDENCE_FLOOR="${PROOF_STATUS_MIN_CONFIDENCE_FLOOR:-${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.25}}"
PROOF_STATUS_REQUIRE_SCENARIOS="${PROOF_STATUS_REQUIRE_SCENARIOS:-${PAPER_READINESS_REQUIRE_SCENARIOS:-true}}"
PROOF_STATUS_SCENARIO_DIR="${PROOF_STATUS_SCENARIO_DIR:-${PAPER_READINESS_SCENARIO_DIR:-/var/lib/alpaca-bot/nightly/scenarios}}"
PROOF_STATUS_STREAM_START_GRACE_SECONDS="${PROOF_STATUS_STREAM_START_GRACE_SECONDS:-120}"
PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES="${PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES:-${PAPER_READINESS_MAX_PASS_AGE_MINUTES:-180}}"
PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS="${PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS:-${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-900}}"
PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS="${PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS:-${PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS:-6}}"
PROOF_STATUS_NIGHTLY_LOCK_FILE="${PROOF_STATUS_NIGHTLY_LOCK_FILE:-/var/lock/alpaca-bot-nightly.lock}"
PROOF_STATUS_NIGHTLY_LOG="${PROOF_STATUS_NIGHTLY_LOG:-/var/log/alpaca-bot-nightly.log}"
PROOF_STATUS_SECOND_STRATEGY_LOG="${PROOF_STATUS_SECOND_STRATEGY_LOG:-/var/log/alpaca-bot-second-strategy.log}"
nightly_timeout_for_status="${NIGHTLY_TIMEOUT_SECONDS:-18000}"
second_strategy_timeout_for_status="${SECOND_STRATEGY_SCAN_TIMEOUT_SECONDS:-7200}"
if [[ ! "$nightly_timeout_for_status" =~ ^[0-9]+$ ]] || [[ "$nightly_timeout_for_status" -le 0 ]]; then
  nightly_timeout_for_status=18000
fi
if [[ ! "$second_strategy_timeout_for_status" =~ ^[0-9]+$ ]] || [[ "$second_strategy_timeout_for_status" -le 0 ]]; then
  second_strategy_timeout_for_status=7200
fi
default_nightly_max_age_minutes=$(((nightly_timeout_for_status + second_strategy_timeout_for_status + 1800 + 59) / 60))
PROOF_STATUS_NIGHTLY_MAX_AGE_MINUTES="${PROOF_STATUS_NIGHTLY_MAX_AGE_MINUTES:-$default_nightly_max_age_minutes}"
PROOF_STATUS_NIGHTLY_STALL_MINUTES="${PROOF_STATUS_NIGHTLY_STALL_MINUTES:-90}"
PROOF_STATUS_SCALE_MIN_TRADES="${PROOF_STATUS_SCALE_MIN_TRADES:-${PAPER_SCALE_MIN_TRADES:-30}}"
PROOF_STATUS_SCALE_MIN_STRATEGIES="${PROOF_STATUS_SCALE_MIN_STRATEGIES:-${PAPER_SCALE_MIN_STRATEGIES:-2}}"
PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS="${PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS:-${PAPER_SCALE_MIN_ACTIVE_DAYS:-5}}"
PROOF_STATUS_SCALE_MAX_SINGLE_WIN_PNL_SHARE="${PROOF_STATUS_SCALE_MAX_SINGLE_WIN_PNL_SHARE:-${PAPER_SCALE_MAX_SINGLE_WIN_PNL_SHARE:-0.50}}"
PROOF_STATUS_SCALE_MIN_PROFIT_FACTOR="${PROOF_STATUS_SCALE_MIN_PROFIT_FACTOR:-${PAPER_SCALE_MIN_PROFIT_FACTOR:-1.20}}"
PROOF_STATUS_SCALE_MAX_EOD_LOSS_SHARE="${PROOF_STATUS_SCALE_MAX_EOD_LOSS_SHARE:-${PAPER_SCALE_MAX_EOD_LOSS_SHARE:-0.50}}"
PROOF_STATUS_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE="${PROOF_STATUS_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE:-${PAPER_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE:-0.00}}"
PROOF_STATUS_EXECUTION_MIN_ENTRY_FILL_RATE="${PROOF_STATUS_EXECUTION_MIN_ENTRY_FILL_RATE:-${PAPER_EXECUTION_MIN_ENTRY_FILL_RATE:-0.25}}"
PROOF_STATUS_EXECUTION_MAX_CAPACITY_REJECT_RATE="${PROOF_STATUS_EXECUTION_MAX_CAPACITY_REJECT_RATE:-${PAPER_EXECUTION_MAX_CAPACITY_REJECT_RATE:-0.05}}"
PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT="${PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT:-${SECOND_STRATEGY_OUTPUT_ROOT:-/var/lib/alpaca-bot/nightly/second_strategy}}"
PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT="${PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT:-${SECOND_STRATEGY_SETUP_OUTPUT_ROOT:-$PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT/setup_knobs}}"
PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS="${PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS:-48}"
PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE="${PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE:-0.50}"
PROOF_STATUS_SECOND_STRATEGY_PROMOTION_DENYLIST="${PROOF_STATUS_SECOND_STRATEGY_PROMOTION_DENYLIST:-${PAPER_STRATEGY_PROMOTION_DENYLIST:-ema_pullback,vwap_cross}}"
PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS="${PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS:-$PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS}"
PROOF_STATUS_OPTION_REPLAY_MIN_POINTS_PER_SESSION="${PROOF_STATUS_OPTION_REPLAY_MIN_POINTS_PER_SESSION:-20}"
PROOF_STATUS_PROMOTION_APPROVAL_MARKER="${PROOF_STATUS_PROMOTION_APPROVAL_MARKER:-$PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT/promotion_approval.json}"
PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER="${PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER:-$PROOF_STATUS_PROMOTION_APPROVAL_MARKER}"
PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE="${PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE:-$ENV_FILE}"

if [[ -z "${STRATEGY_VERSION:-}" ]]; then
  echo "missing STRATEGY_VERSION in $ENV_FILE" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_START_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "PROOF_STATUS_START_DATE must use YYYY-MM-DD" >&2
  exit 1
fi
if [[ -n "$PROOF_STATUS_END_DATE" && ! "$PROOF_STATUS_END_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "PROOF_STATUS_END_DATE must use YYYY-MM-DD" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_MIN_TRADES" =~ ^[0-9]+$ ]]; then
  echo "PROOF_STATUS_MIN_TRADES must be a non-negative integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_MIN_PNL" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
  echo "PROOF_STATUS_MIN_PNL must be a number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SESSION_GUARD_MIN_TRADES" =~ ^[0-9]+$ ]]; then
  echo "PROOF_STATUS_SESSION_GUARD_MIN_TRADES must be a non-negative integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SESSION_GUARD_MIN_PNL" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
  echo "PROOF_STATUS_SESSION_GUARD_MIN_PNL must be a number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_MIN_WATCHLIST_SYMBOLS" =~ ^[0-9]+$ ]] \
  || [[ "$PROOF_STATUS_MIN_WATCHLIST_SYMBOLS" -lt 1 ]]; then
  echo "PROOF_STATUS_MIN_WATCHLIST_SYMBOLS must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_MIN_CONFIDENCE_FLOOR" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PROOF_STATUS_MIN_CONFIDENCE_FLOOR must be a non-negative number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_STREAM_START_GRACE_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "PROOF_STATUS_STREAM_START_GRACE_SECONDS must be a non-negative integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES" =~ ^[0-9]+$ || "$PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES" -le 0 ]]; then
  echo "PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS" =~ ^[0-9]+$ ]]; then
  echo "PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS" =~ ^[1-9][0-9]*$ ]]; then
  echo "PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_NIGHTLY_MAX_AGE_MINUTES" =~ ^[1-9][0-9]*$ ]]; then
  echo "PROOF_STATUS_NIGHTLY_MAX_AGE_MINUTES must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_NIGHTLY_STALL_MINUTES" =~ ^[1-9][0-9]*$ ]]; then
  echo "PROOF_STATUS_NIGHTLY_STALL_MINUTES must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_APPROVED_STRATEGIES" =~ ^[A-Za-z0-9_.-]+(,[A-Za-z0-9_.-]+)*$ ]]; then
  echo "PROOF_STATUS_APPROVED_STRATEGIES must be a comma-separated list of strategy names" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SCALE_MIN_TRADES" =~ ^[0-9]+$ ]] \
  || [[ "$PROOF_STATUS_SCALE_MIN_TRADES" -lt 1 ]]; then
  echo "PROOF_STATUS_SCALE_MIN_TRADES must be a positive integer" >&2
  exit 1
fi
if (( 10#$PROOF_STATUS_MIN_TRADES < 10#$PROOF_STATUS_SCALE_MIN_TRADES )); then
  PROOF_STATUS_MIN_TRADES="$PROOF_STATUS_SCALE_MIN_TRADES"
fi
if [[ ! "$PROOF_STATUS_SCALE_MIN_STRATEGIES" =~ ^[0-9]+$ ]] \
  || [[ "$PROOF_STATUS_SCALE_MIN_STRATEGIES" -lt 1 ]]; then
  echo "PROOF_STATUS_SCALE_MIN_STRATEGIES must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS" =~ ^[0-9]+$ ]] \
  || [[ "$PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS" -lt 1 ]]; then
  echo "PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SCALE_MAX_SINGLE_WIN_PNL_SHARE" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PROOF_STATUS_SCALE_MAX_SINGLE_WIN_PNL_SHARE must be a non-negative number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SCALE_MIN_PROFIT_FACTOR" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PROOF_STATUS_SCALE_MIN_PROFIT_FACTOR must be a non-negative number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SCALE_MAX_EOD_LOSS_SHARE" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PROOF_STATUS_SCALE_MAX_EOD_LOSS_SHARE must be a non-negative number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PROOF_STATUS_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE must be a non-negative number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE" =~ ^(0([.][0-9]+)?|1([.]0+)?)$ ]]; then
  echo "PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE must be between 0 and 1" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS" =~ ^[0-9]+$ ]] \
  || [[ "$PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS" -lt 1 ]]; then
  echo "PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_OPTION_REPLAY_MIN_POINTS_PER_SESSION" =~ ^[0-9]+$ ]] \
  || [[ "$PROOF_STATUS_OPTION_REPLAY_MIN_POINTS_PER_SESSION" -lt 1 ]]; then
  echo "PROOF_STATUS_OPTION_REPLAY_MIN_POINTS_PER_SESSION must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_EXECUTION_MIN_ENTRY_FILL_RATE" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PROOF_STATUS_EXECUTION_MIN_ENTRY_FILL_RATE must be a non-negative number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_EXECUTION_MAX_CAPACITY_REJECT_RATE" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PROOF_STATUS_EXECUTION_MAX_CAPACITY_REJECT_RATE must be a non-negative number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS" =~ ^[0-9]+$ ]] \
  || [[ "$PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS" -lt 1 ]]; then
  echo "PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS must be a positive integer" >&2
  exit 1
fi
case "${PROOF_STATUS_FAIL_ON_ISSUES,,}" in
  true|false) ;;
  *)
    echo "PROOF_STATUS_FAIL_ON_ISSUES must be true or false" >&2
    exit 1
    ;;
esac
case "${PROOF_STATUS_REQUIRE_SCENARIOS,,}" in
  true|false) ;;
  *)
    echo "PROOF_STATUS_REQUIRE_SCENARIOS must be true or false" >&2
    exit 1
    ;;
esac

export COMPOSE_ANSI="${COMPOSE_ANSI:-never}"
export COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-quiet}"

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)
trading_mode="${TRADING_MODE:-paper}"
scenario_volume_args=()
if [[ "${PROOF_STATUS_REQUIRE_SCENARIOS,,}" == "true" && -d "$PROOF_STATUS_SCENARIO_DIR" ]]; then
  scenario_volume_args=(-v "$PROOF_STATUS_SCENARIO_DIR:$PROOF_STATUS_SCENARIO_DIR:ro")
fi
second_strategy_volume_args=()
if [[ -d "$PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT" ]]; then
  second_strategy_volume_args=(-v "$PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT:$PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT:ro")
fi
if [[ -d "$PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT" ]]; then
  case "$PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT" in
    "$PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT"/*) ;;
    *)
      second_strategy_volume_args+=(-v "$PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT:$PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT:ro")
      ;;
  esac
fi

compact_check_detail() {
  local detail
  detail="$(printf '%s\n' "$1" | sed '/^[[:space:]]*$/d' | tail -n 1)"
  detail="${detail//$'\n'/; }"
  echo "$detail"
}

compact_status_value() {
  local value="$1"
  value="$(printf '%s' "$value" | tr '[:space:]' '_' | tr -cd 'A-Za-z0-9_.,:=@/%+-' | cut -c1-240)"
  if [[ -z "$value" ]]; then
    value="none"
  fi
  echo "$value"
}

nightly_status="idle"
nightly_lock_status="missing"
nightly_pid="none"
nightly_source="none"
nightly_age_minutes="none"
nightly_log_age_minutes="none"
nightly_active_log="none"
nightly_stage="none"
nightly_detail="none"
second_strategy_scan_status="unknown"
second_strategy_scan_detail="none"
second_strategy_scan_event_epoch="none"

probe_nightly_cycle_status() {
  local process_line=""
  local second_strategy_process_running="false"
  local age_seconds=""
  local command_text=""
  local now_seconds=""
  local log_mtime_seconds=""
  local second_strategy_log_mtime_seconds=""
  local active_log=""
  local active_log_mtime_seconds=""
  local log_age_seconds=""
  local latest_stage_line=""

  if [[ -e "$PROOF_STATUS_NIGHTLY_LOCK_FILE" ]]; then
    if flock -n "$PROOF_STATUS_NIGHTLY_LOCK_FILE" true 2>/dev/null; then
      nightly_lock_status="free"
    else
      nightly_lock_status="held"
    fi
  fi

  process_line="$(
    ps -eo pid=,etimes=,args= \
      | awk '
        /[a]wk/ { next }
        /flock -n .*alpaca-bot-nightly\.lock/ ||
        /[n]ightly_cycle\.sh/ ||
        /[a]lpaca-bot-nightly/ ||
        /[s]econd_strategy_basket_scan\.sh/ ||
        /docker compose .*run --rm nightly/ {
          sub(/^[[:space:]]+/, "")
          print
          exit
        }
      ' || true
  )"
  if [[ -n "$process_line" ]]; then
    read -r nightly_pid age_seconds command_text <<< "$process_line"
    nightly_source="unknown"
    if [[ "$command_text" == *"nightly_cycle.sh"* ]]; then
      nightly_source="script"
    elif [[ "$command_text" == *"bash -lc"* ]]; then
      nightly_source="legacy_inline"
    elif [[ "$command_text" == *"second_strategy_basket_scan.sh"* ]]; then
      nightly_source="second_strategy"
    elif [[ "$command_text" == *"alpaca-bot-nightly"* || "$command_text" == *"docker compose"* ]]; then
      nightly_source="compose"
    fi
    if [[ "$age_seconds" =~ ^[0-9]+$ ]]; then
      nightly_age_minutes=$(((age_seconds + 59) / 60))
    fi
    nightly_detail="$(compact_status_value "$command_text")"
    nightly_status="${nightly_source}_running"
  elif [[ "$nightly_lock_status" == "held" ]]; then
    nightly_status="lock_held"
    nightly_source="unknown"
  fi

  if ps -eo args= \
      | awk '
        /[a]wk/ { next }
        /bash -lc/ { next }
        /(^|[[:space:]])timeout[[:space:]][^[:space:]]+[[:space:]]+(\.\/)?scripts\/second_strategy_basket_scan\.sh([[:space:]]|$)/ ||
        /(^|[[:space:]])bash[[:space:]]+(\.\/)?scripts\/second_strategy_basket_scan\.sh([[:space:]]|$)/ ||
        /(^|[[:space:]])(\.\/)?scripts\/second_strategy_basket_scan\.sh([[:space:]]|$)/ {
          found = 1
          exit
        }
        END { exit found ? 0 : 1 }
      '; then
    second_strategy_process_running="true"
  fi

  now_seconds="$(date +%s)"
  if [[ -f "$PROOF_STATUS_NIGHTLY_LOG" ]]; then
    log_mtime_seconds="$(stat -c %Y "$PROOF_STATUS_NIGHTLY_LOG" 2>/dev/null || true)"
    active_log="$PROOF_STATUS_NIGHTLY_LOG"
    active_log_mtime_seconds="$log_mtime_seconds"
  fi
  if [[ "$second_strategy_process_running" == "true" && -f "$PROOF_STATUS_SECOND_STRATEGY_LOG" ]]; then
    second_strategy_log_mtime_seconds="$(stat -c %Y "$PROOF_STATUS_SECOND_STRATEGY_LOG" 2>/dev/null || true)"
    if [[ "$second_strategy_log_mtime_seconds" =~ ^[0-9]+$ ]] \
      && { [[ ! "$active_log_mtime_seconds" =~ ^[0-9]+$ ]] \
        || (( second_strategy_log_mtime_seconds >= active_log_mtime_seconds )); }; then
      active_log="$PROOF_STATUS_SECOND_STRATEGY_LOG"
      active_log_mtime_seconds="$second_strategy_log_mtime_seconds"
    fi
  fi
  if [[ -n "$active_log" ]]; then
    nightly_active_log="$(compact_status_value "$active_log")"
    if [[ "$active_log_mtime_seconds" =~ ^[0-9]+$ ]]; then
      log_age_seconds=$((now_seconds - active_log_mtime_seconds))
      if [[ "$log_age_seconds" -lt 0 ]]; then
        log_age_seconds=0
      fi
      nightly_log_age_minutes=$(((log_age_seconds + 59) / 60))
    fi
    latest_stage_line="$(
      tail -200 "$active_log" 2>/dev/null \
        | grep -E 'nightly_cycle|proof guard checking|proof guard rejected|combo [0-9]+/[0-9]+|DB run_id|PAPER_PROOF_FREEZE|Params unchanged|second-strategy|second strategy basket scan|positive_edge_validation_rows|latest_validation' \
        | tail -n 1 \
        || true
    )"
    if [[ -z "$latest_stage_line" && "$active_log" != "$PROOF_STATUS_NIGHTLY_LOG" && -f "$PROOF_STATUS_NIGHTLY_LOG" ]]; then
      latest_stage_line="$(
        tail -200 "$PROOF_STATUS_NIGHTLY_LOG" 2>/dev/null \
          | grep -E 'nightly_cycle|proof guard checking|proof guard rejected|combo [0-9]+/[0-9]+|DB run_id|PAPER_PROOF_FREEZE|Params unchanged|second-strategy' \
          | tail -n 1 \
          || true
      )"
    fi
    nightly_stage="$(compact_status_value "$latest_stage_line")"
  fi

  if [[ "$nightly_status" != "idle" && "$nightly_age_minutes" =~ ^[0-9]+$ ]]; then
    if (( nightly_age_minutes > PROOF_STATUS_NIGHTLY_MAX_AGE_MINUTES )); then
      nightly_status="${nightly_source}_stale"
    elif [[ "$nightly_log_age_minutes" =~ ^[0-9]+$ ]] \
      && (( nightly_log_age_minutes > PROOF_STATUS_NIGHTLY_STALL_MINUTES )); then
      nightly_status="${nightly_source}_stalled"
    fi
  fi
}

probe_nightly_cycle_status

probe_second_strategy_scan_status() {
  local latest_event=""
  local event_status=""
  local event_detail=""

  if [[ ! -f "$PROOF_STATUS_SECOND_STRATEGY_LOG" ]]; then
    second_strategy_scan_status="missing_log"
    second_strategy_scan_detail="none"
    second_strategy_scan_event_epoch="none"
    return 0
  fi

  latest_event="$(
    tail -1000 "$PROOF_STATUS_SECOND_STRATEGY_LOG" 2>/dev/null \
      | awk '
        /second strategy basket scan result:/ {
          if ($0 ~ /status=ok/) {
            status = "ok"
          } else if ($0 ~ /status=failed/) {
            status = "failed"
          } else {
            status = "unknown"
          }
          line = $0
        }
        /^(latest|latest_validation)=/ || /positive_edge_validation_rows=/ {
          status = "ok"
          line = $0
        }
        /second strategy basket scan failed:/ ||
        /candidate scan command\(s\) failed/ ||
        /validation command\(s\) failed/ ||
        /syntax error/ {
          status = "failed"
          line = $0
        }
        END {
          if (status != "") {
            printf "%s\t%s\n", status, line
          }
        }
      ' \
      || true
  )"
  if [[ -z "$latest_event" ]]; then
    second_strategy_scan_status="unknown"
    second_strategy_scan_detail="none"
    second_strategy_scan_event_epoch="none"
    return 0
  fi

  IFS=$'\t' read -r event_status event_detail <<< "$latest_event"
  second_strategy_scan_status="${event_status:-unknown}"
  second_strategy_scan_detail="$(compact_status_value "${event_detail:-none}")"
  second_strategy_scan_event_epoch="$(stat -c %Y "$PROOF_STATUS_SECOND_STRATEGY_LOG" 2>/dev/null || true)"
  if [[ ! "$second_strategy_scan_event_epoch" =~ ^[0-9]+$ ]]; then
    second_strategy_scan_event_epoch="none"
  fi
}

probe_second_strategy_scan_status

proof_status_enabled_strategy_args=()
build_proof_status_enabled_strategy_args() {
  local csv="$1"
  local raw
  local name
  local -a raw_names
  proof_status_enabled_strategy_args=()
  IFS=',' read -r -a raw_names <<< "$csv"
  for raw in "${raw_names[@]}"; do
    name="$(printf '%s' "$raw" | tr -d '[:space:]')"
    if [[ -z "$name" ]]; then
      continue
    fi
    if [[ ! "$name" =~ ^[A-Za-z0-9_:-]+$ ]]; then
      echo "PROOF_STATUS_APPROVED_STRATEGIES contains unsupported strategy: $name" >&2
      exit 1
    fi
    proof_status_enabled_strategy_args+=(--expect-only-enabled-strategy "$name")
  done
  if [[ "${#proof_status_enabled_strategy_args[@]}" -eq 0 ]]; then
    echo "PROOF_STATUS_APPROVED_STRATEGIES must contain at least one strategy" >&2
    exit 1
  fi
}

build_proof_status_enabled_strategy_args "$PROOF_STATUS_APPROVED_STRATEGIES"

promotion_write_access_status="ok"
promotion_env_file_writable="false"
promotion_env_dir_writable="false"
promotion_approval_marker_writable="false"
promotion_approval_marker_dir_writable="false"
probe_promotion_write_access() {
  local env_dir
  local marker_dir
  local marker_parent

  env_dir="$(dirname "$ENV_FILE")"
  marker_dir="$(dirname "$PROOF_STATUS_PROMOTION_APPROVAL_MARKER")"
  marker_parent="$(dirname "$marker_dir")"

  if [[ -w "$ENV_FILE" ]]; then
    promotion_env_file_writable="true"
  else
    promotion_write_access_status="env_file_not_writable"
  fi
  if [[ -w "$env_dir" ]]; then
    promotion_env_dir_writable="true"
  elif [[ "$promotion_write_access_status" == "ok" ]]; then
    promotion_write_access_status="env_dir_not_writable"
  fi

  if [[ -e "$PROOF_STATUS_PROMOTION_APPROVAL_MARKER" && ! -w "$PROOF_STATUS_PROMOTION_APPROVAL_MARKER" ]]; then
    if [[ "$promotion_write_access_status" == "ok" ]]; then
      promotion_write_access_status="approval_marker_not_writable"
    fi
  else
    promotion_approval_marker_writable="true"
  fi

  if [[ -d "$marker_dir" ]]; then
    if [[ -w "$marker_dir" ]]; then
      promotion_approval_marker_dir_writable="true"
    elif [[ "$promotion_write_access_status" == "ok" ]]; then
      promotion_write_access_status="approval_marker_dir_not_writable"
    fi
  elif [[ ! -d "$marker_parent" || ! -w "$marker_parent" ]]; then
    if [[ "$promotion_write_access_status" == "ok" ]]; then
      promotion_write_access_status="approval_marker_parent_not_writable"
    fi
  else
    promotion_approval_marker_dir_writable="true"
  fi
}

probe_promotion_write_access

cron_health_status="ok"
if ! cron_health_detail="$(./scripts/cron_health_check.sh 2>&1)"; then
  cron_health_status="failed"
fi
cron_health_detail="$(compact_check_detail "$cron_health_detail")"

ops_health_status="ok"
if ! ops_health_detail="$(./scripts/ops_check.sh "$ENV_FILE" \
  --expect-trading-mode "$trading_mode" \
  --expect-strategy-version "$STRATEGY_VERSION" \
  --expect-trading-status enabled \
  --expect-kill-switch false \
  "${proof_status_enabled_strategy_args[@]}" \
  2>&1)"; then
  ops_health_status="failed"
fi
ops_health_detail="$(compact_check_detail "$ops_health_detail")"

ops_close_only_health_status="skipped"
ops_close_only_health_detail=""
if [[ "$ops_health_status" != "ok" ]]; then
  ops_close_only_health_status="ok"
  if ! ops_close_only_health_detail="$(./scripts/ops_check.sh "$ENV_FILE" \
    --expect-trading-mode "$trading_mode" \
    --expect-strategy-version "$STRATEGY_VERSION" \
    --expect-trading-status close_only \
    --expect-kill-switch false \
    "${proof_status_enabled_strategy_args[@]}" \
    2>&1)"; then
    ops_close_only_health_status="failed"
  fi
  ops_close_only_health_detail="$(compact_check_detail "$ops_close_only_health_detail")"
fi

runtime_image_health_status="ok"
if ! runtime_image_health_detail="$("$PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT" "$ENV_FILE" 2>&1)"; then
  runtime_image_health_status="failed"
fi
runtime_image_health_detail="$(compact_check_detail "$runtime_image_health_detail")"

echo "scheduled check context: session_date=$(TZ=America/New_York date +%F) proof_start=$PROOF_STATUS_START_DATE strategy=$PROOF_STATUS_STRATEGY strategies=$PROOF_STATUS_APPROVED_STRATEGIES min_trades=$PROOF_STATUS_MIN_TRADES min_pnl=$PROOF_STATUS_MIN_PNL session_guard_min_trades=$PROOF_STATUS_SESSION_GUARD_MIN_TRADES session_guard_min_pnl=$PROOF_STATUS_SESSION_GUARD_MIN_PNL"
echo "paper proof status context: proof_start=$PROOF_STATUS_START_DATE mode=$trading_mode strategy_version=$STRATEGY_VERSION strategy=$PROOF_STATUS_STRATEGY strategies=$PROOF_STATUS_APPROVED_STRATEGIES min_trades=$PROOF_STATUS_MIN_TRADES min_pnl=$PROOF_STATUS_MIN_PNL session_guard_min_trades=$PROOF_STATUS_SESSION_GUARD_MIN_TRADES session_guard_min_pnl=$PROOF_STATUS_SESSION_GUARD_MIN_PNL"
echo "paper proof trading status:"
"${compose[@]}" run -T --rm admin \
  status \
  --mode "$trading_mode" \
  --strategy-version "$STRATEGY_VERSION" \
  | sed 's/^/  /'

echo "paper proof evidence status:"
"${compose[@]}" run -T --rm \
  "${scenario_volume_args[@]}" \
  "${second_strategy_volume_args[@]}" \
  -e PROOF_STATUS_STRATEGY="$PROOF_STATUS_STRATEGY" \
  -e PROOF_STATUS_APPROVED_STRATEGIES="$PROOF_STATUS_APPROVED_STRATEGIES" \
  -e PROOF_STATUS_MIN_TRADES="$PROOF_STATUS_MIN_TRADES" \
  -e PROOF_STATUS_MIN_PNL="$PROOF_STATUS_MIN_PNL" \
  -e PROOF_STATUS_SESSION_GUARD_MIN_TRADES="$PROOF_STATUS_SESSION_GUARD_MIN_TRADES" \
  -e PROOF_STATUS_SESSION_GUARD_MIN_PNL="$PROOF_STATUS_SESSION_GUARD_MIN_PNL" \
  -e PROOF_STATUS_MIN_WATCHLIST_SYMBOLS="$PROOF_STATUS_MIN_WATCHLIST_SYMBOLS" \
  -e PROOF_STATUS_MIN_CONFIDENCE_FLOOR="$PROOF_STATUS_MIN_CONFIDENCE_FLOOR" \
  -e PROOF_STATUS_REQUIRE_SCENARIOS="$PROOF_STATUS_REQUIRE_SCENARIOS" \
  -e PROOF_STATUS_SCENARIO_DIR="$PROOF_STATUS_SCENARIO_DIR" \
  -e PROOF_STATUS_STREAM_START_GRACE_SECONDS="$PROOF_STATUS_STREAM_START_GRACE_SECONDS" \
  -e PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES="$PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES" \
  -e PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS="$PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS" \
  -e PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS="$PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS" \
  -e PROOF_STATUS_SCALE_MIN_TRADES="$PROOF_STATUS_SCALE_MIN_TRADES" \
  -e PROOF_STATUS_SCALE_MIN_STRATEGIES="$PROOF_STATUS_SCALE_MIN_STRATEGIES" \
  -e PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS="$PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS" \
  -e PROOF_STATUS_SCALE_MAX_SINGLE_WIN_PNL_SHARE="$PROOF_STATUS_SCALE_MAX_SINGLE_WIN_PNL_SHARE" \
  -e PROOF_STATUS_SCALE_MIN_PROFIT_FACTOR="$PROOF_STATUS_SCALE_MIN_PROFIT_FACTOR" \
  -e PROOF_STATUS_SCALE_MAX_EOD_LOSS_SHARE="$PROOF_STATUS_SCALE_MAX_EOD_LOSS_SHARE" \
  -e PROOF_STATUS_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE="$PROOF_STATUS_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE" \
  -e PROOF_STATUS_EXECUTION_MIN_ENTRY_FILL_RATE="$PROOF_STATUS_EXECUTION_MIN_ENTRY_FILL_RATE" \
  -e PROOF_STATUS_EXECUTION_MAX_CAPACITY_REJECT_RATE="$PROOF_STATUS_EXECUTION_MAX_CAPACITY_REJECT_RATE" \
  -e PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT="$PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT" \
  -e PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT="$PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT" \
  -e PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS="$PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS" \
  -e PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE="$PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE" \
  -e PROOF_STATUS_SECOND_STRATEGY_PROMOTION_DENYLIST="$PROOF_STATUS_SECOND_STRATEGY_PROMOTION_DENYLIST" \
  -e PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS="$PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS" \
  -e PROOF_STATUS_OPTION_REPLAY_MIN_POINTS_PER_SESSION="$PROOF_STATUS_OPTION_REPLAY_MIN_POINTS_PER_SESSION" \
  -e PROOF_STATUS_PROMOTION_WRITE_ACCESS_STATUS="$promotion_write_access_status" \
  -e PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER="$PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER" \
  -e PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE="$PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE" \
  -e PROOF_STATUS_PROMOTION_ENV_FILE_WRITABLE="$promotion_env_file_writable" \
  -e PROOF_STATUS_PROMOTION_ENV_DIR_WRITABLE="$promotion_env_dir_writable" \
  -e PROOF_STATUS_PROMOTION_APPROVAL_MARKER_WRITABLE="$promotion_approval_marker_writable" \
  -e PROOF_STATUS_PROMOTION_APPROVAL_MARKER_DIR_WRITABLE="$promotion_approval_marker_dir_writable" \
  -e PROOF_STATUS_ENV_FILE="$ENV_FILE" \
  -e PROOF_STATUS_START_DATE="$PROOF_STATUS_START_DATE" \
  -e PROOF_STATUS_END_DATE="$PROOF_STATUS_END_DATE" \
  -e PROOF_STATUS_CRON_HEALTH_STATUS="$cron_health_status" \
  -e PROOF_STATUS_CRON_HEALTH_DETAIL="$cron_health_detail" \
  -e PROOF_STATUS_NIGHTLY_STATUS="$nightly_status" \
  -e PROOF_STATUS_NIGHTLY_LOCK_STATUS="$nightly_lock_status" \
  -e PROOF_STATUS_NIGHTLY_PID="$nightly_pid" \
  -e PROOF_STATUS_NIGHTLY_SOURCE="$nightly_source" \
  -e PROOF_STATUS_NIGHTLY_AGE_MINUTES="$nightly_age_minutes" \
  -e PROOF_STATUS_NIGHTLY_LOG_AGE_MINUTES="$nightly_log_age_minutes" \
  -e PROOF_STATUS_NIGHTLY_ACTIVE_LOG="$nightly_active_log" \
  -e PROOF_STATUS_NIGHTLY_MAX_AGE_MINUTES="$PROOF_STATUS_NIGHTLY_MAX_AGE_MINUTES" \
  -e PROOF_STATUS_NIGHTLY_STALL_MINUTES="$PROOF_STATUS_NIGHTLY_STALL_MINUTES" \
  -e PROOF_STATUS_NIGHTLY_STAGE="$nightly_stage" \
  -e PROOF_STATUS_NIGHTLY_DETAIL="$nightly_detail" \
  -e PROOF_STATUS_SECOND_STRATEGY_SCAN_STATUS="$second_strategy_scan_status" \
  -e PROOF_STATUS_SECOND_STRATEGY_SCAN_DETAIL="$second_strategy_scan_detail" \
  -e PROOF_STATUS_SECOND_STRATEGY_SCAN_EVENT_EPOCH="$second_strategy_scan_event_epoch" \
  -e PROOF_STATUS_OPS_HEALTH_STATUS="$ops_health_status" \
  -e PROOF_STATUS_OPS_HEALTH_DETAIL="$ops_health_detail" \
  -e PROOF_STATUS_OPS_CLOSE_ONLY_HEALTH_STATUS="$ops_close_only_health_status" \
  -e PROOF_STATUS_OPS_CLOSE_ONLY_HEALTH_DETAIL="$ops_close_only_health_detail" \
  -e PROOF_STATUS_RUNTIME_IMAGE_HEALTH_STATUS="$runtime_image_health_status" \
  -e PROOF_STATUS_RUNTIME_IMAGE_HEALTH_DETAIL="$runtime_image_health_detail" \
  -e PROOF_STATUS_FAIL_ON_ISSUES="$PROOF_STATUS_FAIL_ON_ISSUES" \
  --entrypoint python admin <<'PY'
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.domain import CAPACITY_SENTINEL_SYMBOL
from alpaca_bot.domain.models import Bar
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
from alpaca_bot.replay.mechanics import simulate_buy_stop_limit_fill
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import OrderStore
from alpaca_bot.strategy import OPTION_STRATEGY_NAMES, STRATEGY_REGISTRY


ACTIVE_ORDER_STATUSES = (
    "pending_submit",
    "submitting",
    "pending_new",
    "new",
    "accepted",
    "accepted_for_bidding",
    "submitted",
    "partially_filled",
    "held",
    "pending_replace",
    "pending_cancel",
    "stopped",
    "suspended",
    "done_for_day",
)


def parse_date(value: str, *, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must use YYYY-MM-DD") from exc


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def parse_bar_date(raw: str) -> date:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw).date()


def parse_int_or_none(raw: str) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def as_hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def format_problem_summary(problems: dict[str, list[str]]) -> str:
    parts = []
    for name, values in problems.items():
        if values:
            examples = ",".join(
                re.sub(r"[^A-Za-z0-9_.:+/-]", "_", value) for value in values[:10]
            )
            parts.append(f"{name}:{len(values)}:{examples}")
    return ";".join(parts) if parts else "none"


def parse_symbol_set(raw: str | None) -> set[str]:
    if raw is None or raw == "none":
        return set()
    return {symbol for symbol in raw.split(",") if symbol and symbol != "none"}


def parse_name_list(raw: str | None) -> list[str]:
    names: list[str] = []
    for part in (raw or "").split(","):
        name = part.strip()
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_:-]+", name):
            return []
        if name not in names:
            names.append(name)
    return names


def format_name_list(names: list[str]) -> str:
    return ",".join(names) if names else "none"


def option_snapshot_file_session(path: Path) -> str:
    match = re.fullmatch(
        r"option-chain-snapshots-(\d{4}-\d{2}-\d{2})\.jsonl",
        path.name,
    )
    return match.group(1) if match else "unknown"


def option_snapshot_contract_count(path: Path) -> int:
    total_contracts = 0
    expected_session = option_snapshot_file_session(path)
    expected_date = (
        date.fromisoformat(expected_session)
        if expected_session != "unknown"
        else None
    )
    try:
        with path.open(encoding="utf-8") as snapshot_file:
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
                        return 0
                chains_by_symbol = payload.get("chains_by_symbol")
                if not isinstance(chains_by_symbol, dict):
                    continue
                total_contracts += sum(
                    len(contracts)
                    for contracts in chains_by_symbol.values()
                    if isinstance(contracts, list)
                )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 0
    return total_contracts


def option_snapshot_session_point_counts(
    files: list[tuple[Path, object]],
    *,
    interval_minutes: int,
) -> dict[str, int]:
    if interval_minutes <= 0:
        return {}
    interval_microseconds = interval_minutes * 60 * 1_000_000
    point_counts: dict[str, int] = {}
    for file_path, _stat in files:
        session = option_snapshot_file_session(file_path)
        if session == "unknown":
            continue
        expected_date = date.fromisoformat(session)
        retained: dict[int, tuple[datetime, int]] = {}
        try:
            with file_path.open(encoding="utf-8") as snapshot_file:
                for raw_line in snapshot_file:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        break
                    cycle_at = datetime.fromisoformat(str(payload["cycle_at"]))
                    if cycle_at.tzinfo is None:
                        cycle_at = cycle_at.replace(tzinfo=timezone.utc)
                    else:
                        cycle_at = cycle_at.astimezone(timezone.utc)
                    if cycle_at.date() != expected_date:
                        return {}
                    chains_by_symbol = payload.get("chains_by_symbol")
                    if not isinstance(chains_by_symbol, dict):
                        return {}
                    contract_count = sum(
                        len(contracts)
                        for contracts in chains_by_symbol.values()
                        if isinstance(contracts, list)
                    )
                    epoch_microseconds = (
                        int(cycle_at.timestamp()) * 1_000_000
                        + cycle_at.microsecond
                    )
                    boundary = (
                        epoch_microseconds + interval_microseconds - 1
                    ) // interval_microseconds
                    current = retained.get(boundary)
                    if current is None or cycle_at > current[0]:
                        retained[boundary] = (cycle_at, contract_count)
        except (OSError, KeyError, TypeError, ValueError):
            return {}
        point_counts[session] = sum(
            1 for _cycle_at, contract_count in retained.values()
            if contract_count > 0
        )
    return point_counts


def option_snapshot_ledger_summary(
    snapshot_dir: str | None,
    target_session: date | None = None,
    *,
    interval_minutes: int = 15,
    min_points_per_session: int = 20,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "path": snapshot_dir or "none",
        "file_count": 0,
        "latest_file": "none",
        "latest_session": "none",
        "latest_modified": "none",
        "latest_bytes": 0,
        "latest_contracts": 0,
        "snapshot_session_count": 0,
        "replay_session_count": 0,
        "min_points_per_session": min_points_per_session,
        "session_points": "none",
        "undercovered_sessions": "none",
        "earliest_session": "none",
    }
    if not snapshot_dir:
        return summary
    path = Path(snapshot_dir)
    try:
        if path.is_file():
            stat = path.stat()
            files = [(path, stat)] if stat.st_size > 0 else []
        elif path.is_dir():
            files = []
            for file_path in path.glob("option-chain-snapshots-*.jsonl"):
                if not file_path.is_file():
                    continue
                stat = file_path.stat()
                if stat.st_size > 0:
                    files.append((file_path, stat))
        else:
            files = []
    except OSError:
        return summary
    if not files:
        return summary
    session_point_counts = option_snapshot_session_point_counts(
        files,
        interval_minutes=interval_minutes,
    )
    snapshot_sessions = sorted(session_point_counts)
    replay_sessions = [
        session
        for session in snapshot_sessions
        if session_point_counts[session] >= min_points_per_session
    ]
    undercovered_sessions = [
        session
        for session in snapshot_sessions
        if session_point_counts[session] < min_points_per_session
    ]
    selected_path, selected_stat = max(files, key=lambda item: item[1].st_mtime)
    if target_session is not None:
        target_snapshot_name = (
            f"option-chain-snapshots-{target_session.isoformat()}.jsonl"
        )
        for file_path, stat in files:
            if file_path.name == target_snapshot_name:
                selected_path, selected_stat = file_path, stat
                break
    summary.update(
        {
            "file_count": len(files),
            "latest_file": selected_path.name,
            "latest_session": option_snapshot_file_session(selected_path),
            "latest_modified": datetime.fromtimestamp(
                selected_stat.st_mtime,
                timezone.utc,
            ).isoformat(),
            "latest_bytes": selected_stat.st_size,
            "latest_contracts": option_snapshot_contract_count(selected_path),
            "snapshot_session_count": len(snapshot_sessions),
            "replay_session_count": len(replay_sessions),
            "session_points": ",".join(
                f"{session}:{session_point_counts[session]}"
                for session in snapshot_sessions
            ) or "none",
            "undercovered_sessions": ",".join(undercovered_sessions) or "none",
            "earliest_session": snapshot_sessions[0] if snapshot_sessions else "none",
        }
    )
    return summary


def format_optional_float(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "none"
    return f"{value:.{digits}f}"


def safe_status_value(value: object, *, max_length: int = 160) -> str:
    text = "none" if value is None else str(value).strip()
    if not text:
        return "none"
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^A-Za-z0-9_.:+,/@=-]", "_", text)
    return text[:max_length] or "none"


def format_expired_signal_price_posture(
    *,
    above_limit: int,
    below_stop: int,
    within_stop_limit: int,
    missing_context: int,
) -> str:
    total = above_limit + below_stop + within_stop_limit + missing_context
    if total <= 0:
        return "none"
    return (
        f"above_limit:{above_limit},"
        f"below_stop:{below_stop},"
        f"within_stop_limit:{within_stop_limit},"
        f"missing_context:{missing_context}"
    )


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def load_scenario_intraday_bars(
    *,
    symbol: str,
    scenario_dir: Path,
    cache: dict[str, list[Bar] | None],
) -> list[Bar] | None:
    symbol = symbol.upper()
    if symbol in cache:
        return cache[symbol]
    path = scenario_dir / f"{symbol}_252d.json"
    try:
        payload = json.loads(path.read_text())
        bars = [
            Bar.from_dict(item)
            for item in payload.get("intraday_bars") or []
            if str(item.get("symbol", symbol)).upper() == symbol
        ]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        cache[symbol] = None
        return None
    bars.sort(key=lambda bar: normalize_utc(bar.timestamp))
    cache[symbol] = bars
    return bars


def classify_expired_next_bar_fill_cause(
    *,
    symbol: str,
    signal_timestamp: datetime | None,
    created_at: datetime | None,
    stop_price: float | None,
    limit_price: float | None,
    scenario_dir: Path,
    settings: Settings,
    cache: dict[str, list[Bar] | None],
) -> str:
    if signal_timestamp is None or stop_price is None or limit_price is None:
        return "missing_context"
    if stop_price <= 0 or limit_price <= 0:
        return "missing_context"

    bars = load_scenario_intraday_bars(
        symbol=symbol,
        scenario_dir=scenario_dir,
        cache=cache,
    )
    if bars is None:
        return "missing_scenario"
    if not bars:
        return "missing_bar"

    signal_utc = normalize_utc(signal_timestamp)
    active_bars: list[Bar] = []
    for bar in bars:
        bar_utc = normalize_utc(bar.timestamp)
        if bar_utc <= signal_utc:
            continue
        bar_local = bar_utc.astimezone(settings.market_timezone)
        flatten_local = datetime.combine(
            bar_local.date(),
            settings.flatten_time,
            tzinfo=settings.market_timezone,
        )
        if bar_local >= flatten_local:
            break
        active_bars.append(bar)
        if len(active_bars) >= settings.entry_order_active_bars:
            break

    if not active_bars:
        return "missing_bar"

    limit_miss = False
    for bar in active_bars:
        bar_utc = normalize_utc(bar.timestamp)
        if (
            simulate_buy_stop_limit_fill(
                bar=bar,
                stop_price=stop_price,
                limit_price=limit_price,
            )
            is not None
        ):
            if created_at is not None and normalize_utc(created_at) > bar_utc:
                return "would_fill_if_on_time"
            return "would_fill"
        if bar.open > limit_price or bar.high >= stop_price:
            limit_miss = True

    return "limit_miss" if limit_miss else "no_trigger"


def format_expired_next_bar_fill_causes(counts: dict[str, int]) -> str:
    ordered = (
        "would_fill",
        "would_fill_if_on_time",
        "no_trigger",
        "limit_miss",
        "missing_bar",
        "missing_context",
        "missing_scenario",
    )
    total = sum(counts.values())
    if total <= 0:
        return "none"
    return ",".join(f"{name}:{counts.get(name, 0)}" for name in ordered)


def load_expired_next_bar_fill_cause_summary(
    cur,
    *,
    trading_mode: str,
    strategy_version: str,
    strategy_names: list[str],
    market_timezone: str,
    scenario_dir: Path,
    settings: Settings,
    scenario_cache: dict[str, list[Bar] | None],
    proof_start: date | None = None,
    proof_end: date | None = None,
    session_date: date | None = None,
    since: datetime | None = None,
) -> str:
    date_predicates: list[str] = []
    params: list[object] = [trading_mode, strategy_version, strategy_names]
    if session_date is not None:
        date_predicates.append(
            "AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) = %s"
        )
        params.extend([market_timezone, session_date])
    else:
        if proof_start is None or proof_end is None:
            return "none"
        date_predicates.append(
            "AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) >= %s"
        )
        params.extend([market_timezone, proof_start])
        date_predicates.append(
            "AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) <= %s"
        )
        params.extend([market_timezone, proof_end])
    if since is not None:
        date_predicates.append("AND o.created_at >= %s")
        params.append(since)

    cur.execute(
        f"""
        WITH entry_orders AS (
          SELECT
            o.symbol,
            o.status,
            o.signal_timestamp,
            o.created_at,
            o.stop_price,
            o.limit_price,
            EXISTS (
              SELECT 1
              FROM audit_events a
              WHERE a.event_type = 'entry_order_expired_next_bar'
                AND a.payload->>'client_order_id' = o.client_order_id
                AND COALESCE(a.payload->>'reason', '') LIKE 'deploy maintenance%%'
            ) AS maintenance_drained,
            EXISTS (
              SELECT 1
              FROM audit_events a
              WHERE a.event_type = 'entry_order_expired_next_bar'
                AND a.payload->>'client_order_id' = o.client_order_id
                AND COALESCE(a.payload->>'reason', '') = 'short active dispatch window'
            ) AS short_window_drained,
            EXISTS (
              SELECT 1
              FROM audit_events a
              WHERE a.event_type = 'entry_order_expired_next_bar'
                AND a.payload->>'client_order_id' = o.client_order_id
                AND COALESCE(a.payload->>'reason', '') NOT LIKE 'deploy maintenance%%'
                AND COALESCE(a.payload->>'reason', '') <> 'short active dispatch window'
            ) AS strategy_expired
          FROM orders o
          WHERE o.trading_mode = %s
            AND o.strategy_version = %s
            AND o.strategy_name = ANY(%s)
            AND o.intent_type = 'entry'
            {' '.join(date_predicates)}
        )
        SELECT symbol, signal_timestamp, created_at, stop_price, limit_price
        FROM entry_orders
        WHERE NOT maintenance_drained
          AND NOT short_window_drained
          AND (strategy_expired OR status = 'expired')
        """,
        tuple(params),
    )
    rows = cur.fetchall()
    counts: dict[str, int] = {}
    for symbol, signal_timestamp, created_at, stop_price, limit_price in rows:
        cause = classify_expired_next_bar_fill_cause(
            symbol=str(symbol),
            signal_timestamp=signal_timestamp,
            created_at=created_at,
            stop_price=float(stop_price) if stop_price is not None else None,
            limit_price=float(limit_price) if limit_price is not None else None,
            scenario_dir=scenario_dir,
            settings=settings,
            cache=scenario_cache,
        )
        counts[cause] = counts.get(cause, 0) + 1
    return format_expired_next_bar_fill_causes(counts)


def format_entry_dispatch_delay_summary(
    rows: list[tuple[object, object, object]],
    *,
    settings: Settings,
    include_late: bool = True,
) -> str:
    delays: list[tuple[str, float]] = []
    active_bar_offset_seconds = settings.entry_timeframe_minutes * 60
    late_threshold_seconds = (
        settings.entry_timeframe_minutes * settings.entry_order_active_bars * 60 * 0.5
    )
    for symbol, signal_timestamp, created_at in rows:
        if not isinstance(signal_timestamp, datetime) or not isinstance(
            created_at, datetime
        ):
            continue
        active_bar_start = normalize_utc(signal_timestamp) + timedelta(
            seconds=active_bar_offset_seconds
        )
        delay_seconds = (
            normalize_utc(created_at) - active_bar_start
        ).total_seconds()
        if not include_late and delay_seconds > late_threshold_seconds:
            continue
        delays.append((str(symbol), delay_seconds))
    if not delays:
        return "none"
    values = sorted(delay for _, delay in delays)
    midpoint = len(values) // 2
    if len(values) % 2:
        median = values[midpoint]
    else:
        median = (values[midpoint - 1] + values[midpoint]) / 2
    avg = sum(values) / len(values)
    late_symbols = sorted(
        {
            symbol
            for symbol, delay in delays
            if delay > late_threshold_seconds
        }
    )
    late_symbols_text = ",".join(late_symbols) if late_symbols else "none"
    return (
        f"count:{len(delays)},"
        f"late:{len(late_symbols)},"
        f"max_s:{max(values):.1f},"
        f"median_s:{median:.1f},"
        f"avg_s:{avg:.1f},"
        f"late_symbols:{late_symbols_text}"
    )


def load_entry_dispatch_delay_summary(
    cur,
    *,
    trading_mode: str,
    strategy_version: str,
    strategy_names: list[str],
    market_timezone: str,
    settings: Settings,
    proof_start: date | None = None,
    proof_end: date | None = None,
    session_date: date | None = None,
    since: datetime | None = None,
    current_posture_only: bool = False,
) -> str:
    date_predicates: list[str] = []
    params: list[object] = [trading_mode, strategy_version, strategy_names]
    if session_date is not None:
        date_predicates.append(
            "AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) = %s"
        )
        params.extend([market_timezone, session_date])
    else:
        if proof_start is None or proof_end is None:
            return "none"
        date_predicates.append(
            "AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) >= %s"
        )
        params.extend([market_timezone, proof_start])
        date_predicates.append(
            "AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) <= %s"
        )
        params.extend([market_timezone, proof_end])
    if since is not None:
        date_predicates.append("AND o.created_at >= %s")
        params.append(since)
    posture_join = ""
    posture_predicates = ""
    if current_posture_only:
        posture_join = """
          JOIN decision_log d
            ON d.symbol = o.symbol
           AND d.trading_mode = o.trading_mode
           AND d.strategy_version = o.strategy_version
           AND d.strategy_name IS NOT DISTINCT FROM o.strategy_name
           AND d.cycle_at = o.created_at
           AND d.decision = 'accepted'
        """
        posture_predicates = """
            AND d.entry_level IS NOT NULL
            AND d.entry_level > 0
            AND d.signal_bar_close IS NOT NULL
            AND (d.signal_bar_close / NULLIF(d.entry_level, 0) - 1) >= %s
            AND (d.signal_bar_close / NULLIF(d.entry_level, 0) - 1) <= %s
            AND NOT (
              d.stop_price IS NOT NULL
              AND d.initial_stop_price IS NOT NULL
              AND d.limit_price IS NOT NULL
              AND d.limit_price > 0
              AND d.signal_bar_close > d.limit_price
            )
        """
        params.extend(
            [
                settings.entry_min_close_to_entry_pct,
                settings.entry_max_close_to_entry_pct,
            ]
        )

    cur.execute(
        f"""
        WITH entry_orders AS (
          SELECT
            o.symbol,
            o.signal_timestamp,
            o.created_at,
            EXISTS (
              SELECT 1
              FROM audit_events a
              WHERE a.event_type = 'entry_order_expired_next_bar'
                AND a.payload->>'client_order_id' = o.client_order_id
                AND COALESCE(a.payload->>'reason', '') LIKE 'deploy maintenance%%'
            ) AS maintenance_drained,
            EXISTS (
              SELECT 1
              FROM audit_events a
              WHERE a.event_type = 'entry_order_expired_next_bar'
                AND a.payload->>'client_order_id' = o.client_order_id
                AND COALESCE(a.payload->>'reason', '') = 'short active dispatch window'
            ) AS short_window_drained
          FROM orders o
          {posture_join}
          WHERE o.trading_mode = %s
            AND o.strategy_version = %s
            AND o.strategy_name = ANY(%s)
            AND o.intent_type = 'entry'
            {' '.join(date_predicates)}
            {posture_predicates}
        )
        SELECT symbol, signal_timestamp, created_at
        FROM entry_orders
        WHERE NOT maintenance_drained
          AND NOT short_window_drained
          AND signal_timestamp IS NOT NULL
        """,
        tuple(params),
    )
    return format_entry_dispatch_delay_summary(
        cur.fetchall(),
        settings=settings,
        include_late=not current_posture_only,
    )


def load_json_payload(path: Path) -> tuple[dict | None, str | None, str | None]:
    if not path.exists():
        return None, "missing", None
    try:
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        return None, f"invalid_json:{exc.msg}", None
    except OSError as exc:
        return None, f"unreadable:{exc}", None
    if not isinstance(payload, dict):
        return None, "invalid_json:top_level_not_object", None
    return payload, None, hashlib.sha256(raw_bytes).hexdigest()


def file_age_hours(path: Path, *, now_utc: datetime) -> float | None:
    if not path.exists():
        return None
    try:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None
    return max(0.0, (now_utc - modified_at).total_seconds() / 3600.0)


def file_mtime_epoch(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return None


def candidate_names_from_rows(rows: object, *, verdict: str | None = None) -> list[str]:
    names: list[str] = []
    if not isinstance(rows, list):
        return names
    for row in rows:
        if not isinstance(row, dict):
            continue
        if verdict is not None and row.get("verdict") != verdict:
            continue
        name = str(row.get("candidate") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_:-]+", name):
            continue
        if name not in names:
            names.append(name)
    return names


def summarize_validation_verdicts(rows: object, *, limit: int = 10) -> str:
    parts: list[str] = []
    if not isinstance(rows, list):
        return "none"
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = safe_status_value(row.get("candidate"))
        if candidate == "none":
            continue
        scale = safe_status_value(row.get("candidate_scale"))
        verdict = safe_status_value(row.get("verdict"))
        parts.append(f"{candidate}:{scale}:{verdict}")
        if len(parts) >= limit:
            break
    return ",".join(parts) if parts else "none"


def summarize_counts(mapping: object, *, limit: int = 8) -> str:
    if not isinstance(mapping, dict):
        return "none"
    parts: list[str] = []
    for key, value in sorted(mapping.items(), key=lambda item: str(item[0])):
        name = safe_status_value(key)
        count = as_int_or_none(value)
        if name == "none" or count is None:
            continue
        parts.append(f"{name}:{count}")
        if len(parts) >= limit:
            break
    return ",".join(parts) if parts else "none"


def rows_missing_candidate_attribution(rows: object) -> bool:
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("status") != "passed":
            continue
        if "candidate_contribution_status" not in row:
            return True
        for key in (
            "candidate_ci_low",
            "candidate_ci_high",
            "candidate_p_mean_le_zero",
            "candidate_verdict",
        ):
            if key not in row:
                return True
    return False


def as_float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def as_int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def fractionability_snapshot_identity(
    payload: dict | None,
    *,
    summary_path: Path,
) -> tuple[dict[str, object] | None, str | None]:
    if payload is None:
        return None, "summary_missing"
    snapshot = payload.get("fractionability_snapshot")
    if not isinstance(snapshot, dict):
        return None, "metadata_missing"
    if snapshot.get("schema_version") != 1:
        return None, "schema_invalid"
    snapshot_sha256 = str(snapshot.get("snapshot_sha256") or "").lower()
    universe_sha256 = str(snapshot.get("universe_sha256") or "").lower()
    if re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256) is None:
        return None, "snapshot_sha256_invalid"
    if re.fullmatch(r"[0-9a-f]{64}", universe_sha256) is None:
        return None, "universe_sha256_invalid"
    universe_count = as_int_or_none(snapshot.get("universe_symbol_count"))
    fractionable_count = as_int_or_none(snapshot.get("fractionable_symbol_count"))
    non_fractionable_count = as_int_or_none(
        snapshot.get("non_fractionable_symbol_count")
    )
    if (
        universe_count is None
        or fractionable_count is None
        or non_fractionable_count is None
        or universe_count <= 0
        or fractionable_count < 0
        or non_fractionable_count < 0
        or fractionable_count + non_fractionable_count != universe_count
    ):
        return None, "symbol_counts_invalid"
    snapshot_file = str(snapshot.get("snapshot_file") or "").strip()
    if not snapshot_file:
        return None, "snapshot_file_missing"
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
    except (OSError, UnicodeDecodeError):
        return None, "snapshot_file_unreadable"
    current_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
    if current_sha256 != snapshot_sha256:
        return None, "snapshot_file_sha256_mismatch"
    if (
        len(snapshot_symbols) != fractionable_count
        or any(
            re.fullmatch(r"[A-Z0-9][A-Z0-9.-]*", symbol) is None
            for symbol in snapshot_symbols
        )
    ):
        return None, "snapshot_symbols_invalid"
    universe_symbols_file = str(
        snapshot.get("universe_symbols_file") or ""
    ).strip()
    if not universe_symbols_file:
        return None, "universe_symbols_file_missing"
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
    except (OSError, UnicodeDecodeError):
        return None, "universe_symbols_file_unreadable"
    if hashlib.sha256(universe_bytes).hexdigest() != universe_sha256:
        return None, "universe_symbols_file_sha256_mismatch"
    if (
        len(universe_symbols) != universe_count
        or not snapshot_symbols.issubset(universe_symbols)
        or len(universe_symbols - snapshot_symbols) != non_fractionable_count
        or any(
            re.fullmatch(r"[A-Z0-9][A-Z0-9.-]*", symbol) is None
            for symbol in universe_symbols
        )
    ):
        return None, "universe_symbols_invalid"
    return {
        "snapshot_sha256": snapshot_sha256,
        "universe_sha256": universe_sha256,
        "universe_symbol_count": universe_count,
        "fractionable_symbol_count": fractionable_count,
        "non_fractionable_symbol_count": non_fractionable_count,
    }, None


def nightly_threshold_status(
    status: str,
    value: object,
    limit: object,
    *,
    exceeded_suffix: str,
) -> str:
    if status == "idle":
        return "not_applicable_idle"
    if status.endswith(exceeded_suffix):
        return "exceeded"
    parsed_value = as_int_or_none(value)
    parsed_limit = as_int_or_none(limit)
    if parsed_value is None or parsed_limit is None:
        return "unknown"
    return "exceeded" if parsed_value > parsed_limit else "ok"


def parse_marker_approved_at(value: object) -> datetime | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    if raw_value.endswith("Z"):
        raw_value = raw_value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def best_promotion_candidate_from_rows(
    rows: object,
    *,
    preferred_name: str | None = None,
    preferred_scale: float | None = None,
) -> dict[str, object] | None:
    candidates: list[
        tuple[float, float, int, str, float, dict[str, object]]
    ] = []
    if not isinstance(rows, list):
        return None
    stock_strategy_names = set(STRATEGY_REGISTRY)
    option_strategy_names = set(OPTION_STRATEGY_NAMES)
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
        trades = as_int_or_none(row.get("candidate_trades"))
        total_pnl = as_float_or_none(row.get("candidate_total_pnl"))
        ci_low = as_float_or_none(row.get("candidate_ci_low"))
        p_mean_le_zero = as_float_or_none(row.get("candidate_p_mean_le_zero"))
        candidate_scale = as_float_or_none(row.get("candidate_scale"))
        if trades is None or total_pnl is None or ci_low is None or p_mean_le_zero is None:
            continue
        if trades < 30 or total_pnl <= 0.0 or ci_low <= 0.0 or p_mean_le_zero > 0.05:
            continue
        candidates.append(
            (
                ci_low,
                -p_mean_le_zero,
                trades,
                name,
                candidate_scale if candidate_scale is not None else -math.inf,
                row,
            )
        )
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda item: item[:5], reverse=True)
    if preferred_name:
        for _ci, _p, _trades, name, scale, row in ranked:
            if name != preferred_name:
                continue
            if preferred_scale is not None and not math.isclose(
                preferred_scale,
                scale,
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                continue
            return row
    return ranked[0][5]


def approval_marker_status(
    payload: dict | None,
    error: str | None,
    *,
    evidence_status: str,
    evidence_root: Path,
    strategy_version: str,
    env_file: str,
    now_utc: datetime,
    validation_summary_path: Path,
    validation_summary_sha256: str | None,
    proof_horizon_summary_path: Path,
    proof_horizon_summary_sha256: str | None,
    proof_horizon_status: str,
    proof_horizon_payload: dict | None,
    validation_rows: object,
    validation_positive_families: list[str],
    promotion_denied_strategies: set[str],
) -> tuple[str, str]:
    if error == "missing":
        return "missing", "none"
    if error is not None or payload is None:
        return "invalid", "none"
    if payload.get("schema_version") != 3:
        return "invalid_schema", "none"
    if not str(payload.get("approved_at") or "").strip():
        return "approved_at_missing", "none"
    approved_at = parse_marker_approved_at(payload.get("approved_at"))
    if approved_at is None:
        return "approved_at_invalid", "none"
    if approved_at > now_utc + timedelta(minutes=5):
        return "approved_at_in_future", "none"
    strategy = str(payload.get("strategy") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_:-]+", strategy):
        return "invalid_strategy", "none"
    if strategy in promotion_denied_strategies:
        return "strategy_denied", strategy
    if evidence_status != "ok":
        return f"evidence_{evidence_status}", strategy
    if proof_horizon_status != "ok":
        return f"proof_horizon_{proof_horizon_status}", strategy
    if str(payload.get("evidence_root") or "") != str(evidence_root.resolve()):
        return "evidence_root_mismatch", strategy
    marker_strategy_version = str(payload.get("strategy_version") or "").strip()
    if not marker_strategy_version:
        return "strategy_version_missing", strategy
    if marker_strategy_version != strategy_version:
        return "strategy_version_mismatch", strategy
    marker_env_file = str(payload.get("env_file") or "").strip()
    if not marker_env_file:
        return "env_file_missing", strategy
    if marker_env_file != env_file:
        return "env_file_mismatch", strategy
    expected_summary = str(validation_summary_path.resolve())
    if str(payload.get("validation_summary") or "") != expected_summary:
        return "stale_validation_summary", strategy
    try:
        validation_summary_modified_at = datetime.fromtimestamp(
            validation_summary_path.stat().st_mtime,
            timezone.utc,
        )
    except OSError:
        return "validation_summary_unreadable", strategy
    if approved_at < validation_summary_modified_at:
        return "approved_at_before_validation", strategy
    marker_summary_sha256 = str(payload.get("validation_summary_sha256") or "").strip()
    if not marker_summary_sha256:
        return "validation_summary_sha256_missing", strategy
    if validation_summary_sha256 is None:
        return "validation_summary_unreadable", strategy
    if marker_summary_sha256 != validation_summary_sha256:
        return "validation_summary_sha256_mismatch", strategy
    expected_proof_summary = str(proof_horizon_summary_path.resolve())
    if str(payload.get("proof_horizon_summary") or "") != expected_proof_summary:
        return "stale_proof_horizon_summary", strategy
    try:
        proof_horizon_summary_modified_at = datetime.fromtimestamp(
            proof_horizon_summary_path.stat().st_mtime,
            timezone.utc,
        )
    except OSError:
        return "proof_horizon_summary_unreadable", strategy
    if approved_at < proof_horizon_summary_modified_at:
        return "approved_at_before_proof_horizon", strategy
    marker_proof_summary_sha256 = str(
        payload.get("proof_horizon_summary_sha256") or ""
    ).strip()
    if not marker_proof_summary_sha256:
        return "proof_horizon_summary_sha256_missing", strategy
    if proof_horizon_summary_sha256 is None:
        return "proof_horizon_summary_unreadable", strategy
    if marker_proof_summary_sha256 != proof_horizon_summary_sha256:
        return "proof_horizon_summary_sha256_mismatch", strategy
    if proof_horizon_payload is None:
        return "proof_horizon_summary_unreadable", strategy
    proof_horizon_selection = proof_horizon_payload.get("candidate_selection")
    if not isinstance(proof_horizon_selection, dict):
        return "proof_horizon_candidate_selection_missing", strategy
    expected_proof_values: dict[str, object] = {
        "proof_horizon_trades": as_int_or_none(
            proof_horizon_payload.get("trades")
        ),
        "proof_horizon_total_pnl": as_float_or_none(
            proof_horizon_payload.get("total_pnl")
        ),
        "proof_horizon_eventual_pass_rate": as_float_or_none(
            proof_horizon_payload.get("eventual_pass_rate")
        ),
        "proof_horizon_starts_eventually_passed": as_int_or_none(
            proof_horizon_payload.get("starts_eventually_passed")
        ),
        "proof_horizon_historical_starts": as_int_or_none(
            proof_horizon_payload.get("historical_starts_checked")
        ),
        "proof_horizon_selection_reason": str(
            proof_horizon_selection.get("selection_reason") or ""
        ).strip(),
        "proof_horizon_candidate_count": as_int_or_none(
            proof_horizon_selection.get("candidate_count")
        ),
        "proof_horizon_passing_candidate_count": as_int_or_none(
            proof_horizon_selection.get("passing_candidate_count")
        ),
    }
    for key, expected_value in expected_proof_values.items():
        marker_value = payload.get(key)
        if expected_value is None or expected_value == "":
            return f"{key}_unreadable", strategy
        if isinstance(expected_value, float):
            parsed_marker_value = as_float_or_none(marker_value)
            if parsed_marker_value is None or not math.isclose(
                parsed_marker_value,
                expected_value,
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                return f"{key}_mismatch", strategy
        elif marker_value != expected_value:
            return f"{key}_mismatch", strategy
    confirmation = str(payload.get("confirmation") or "").strip()
    expected_confirmation = (
        f"approve-{strategy}-paper-promotion-sha256-{validation_summary_sha256}"
        f"-proof-sha256-{proof_horizon_summary_sha256}"
    )
    if confirmation != expected_confirmation:
        return "confirmation_mismatch", strategy
    if strategy not in validation_positive_families:
        return "latest_validation_missing_positive_edge", strategy
    if (
        str(proof_horizon_selection.get("selected_candidate") or "").strip()
        != strategy
    ):
        return "proof_horizon_candidate_mismatch", strategy
    proof_selected_scale = str(
        proof_horizon_selection.get("selected_candidate_scale") or ""
    ).strip()
    if str(payload.get("candidate_scale") or "") != proof_selected_scale:
        return "candidate_scale_mismatch", strategy
    strategy_rows = [
        row
        for row in validation_rows
        if isinstance(row, dict)
        and str(row.get("candidate") or "").strip() == strategy
    ] if isinstance(validation_rows, list) else []
    if not strategy_rows:
        return "latest_validation_missing_row", strategy
    marker_values = {
        "candidate_scale": str(payload.get("candidate_scale") or ""),
        "candidate_trades": as_int_or_none(payload.get("candidate_trades")),
        "candidate_total_pnl": as_float_or_none(payload.get("candidate_total_pnl")),
        "candidate_ci_low": as_float_or_none(payload.get("candidate_ci_low")),
        "candidate_p_mean_le_zero": as_float_or_none(payload.get("candidate_p_mean_le_zero")),
    }
    for key, marker_value in marker_values.items():
        if marker_value is None or marker_value == "":
            return f"{key}_missing", strategy

    mismatch_key = "candidate_scale"
    for row in strategy_rows:
        expected_values = {
            "candidate_scale": str(row.get("candidate_scale") or ""),
            "candidate_trades": as_int_or_none(row.get("candidate_trades")),
            "candidate_total_pnl": as_float_or_none(row.get("candidate_total_pnl")),
            "candidate_ci_low": as_float_or_none(row.get("candidate_ci_low")),
            "candidate_p_mean_le_zero": as_float_or_none(row.get("candidate_p_mean_le_zero")),
        }
        row_matches = True
        for key, expected_value in expected_values.items():
            marker_value = marker_values[key]
            if expected_value is None or expected_value == "":
                row_matches = False
                mismatch_key = key
                break
            if isinstance(expected_value, float):
                if not math.isclose(
                    float(marker_value),
                    expected_value,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ):
                    row_matches = False
                    mismatch_key = key
                    break
            elif marker_value != expected_value:
                row_matches = False
                mismatch_key = key
                break
        if row_matches:
            return "approved", strategy
    return f"{mismatch_key}_mismatch", strategy


def load_second_strategy_evidence(
    *,
    output_root: Path,
    now_utc: datetime,
    max_age_hours: int,
    strategy_version: str,
    env_file: str,
    require_candidate_attribution: bool = False,
) -> dict[str, object]:
    prefilter_summary_path = output_root / "latest" / "summary.json"
    validation_summary_path = output_root / "latest_validation" / "summary.json"
    approval_marker_path = output_root / "promotion_approval.json"
    proof_horizon_summary_path = output_root / "latest_proof_horizon" / "summary.json"
    prefilter_payload, prefilter_error, prefilter_summary_sha256 = load_json_payload(
        prefilter_summary_path
    )
    validation_payload, validation_error, validation_summary_sha256 = load_json_payload(
        validation_summary_path
    )
    approval_payload, approval_error, _approval_sha256 = load_json_payload(
        approval_marker_path
    )
    (
        proof_horizon_payload,
        proof_horizon_error,
        proof_horizon_summary_sha256,
    ) = load_json_payload(proof_horizon_summary_path)

    prefilter_rows = prefilter_payload.get("rows", []) if prefilter_payload else []
    validation_rows = validation_payload.get("rows", []) if validation_payload else []
    prefilter_fractionability, prefilter_fractionability_error = (
        fractionability_snapshot_identity(
            prefilter_payload,
            summary_path=prefilter_summary_path,
        )
    )
    validation_fractionability, validation_fractionability_error = (
        fractionability_snapshot_identity(
            validation_payload,
            summary_path=validation_summary_path,
        )
    )
    proof_horizon_fractionability, proof_horizon_fractionability_error = (
        fractionability_snapshot_identity(
            proof_horizon_payload,
            summary_path=proof_horizon_summary_path,
        )
    )
    fractionability_lineage_status = "not_required"
    validation_prefilter_summary = "none"
    validation_prefilter_summary_sha256 = "none"
    validation_prefilter_lineage_status = "not_applicable"
    validation_prefilter_lineage_error = False
    if validation_payload is not None and prefilter_payload is not None:
        validation_prefilter_summary = str(
            validation_payload.get("prefilter_summary_json") or ""
        ).strip() or "none"
        if validation_prefilter_summary == "none":
            validation_prefilter_lineage_status = "reference_missing"
            validation_prefilter_lineage_error = True
        else:
            validation_prefilter_path = Path(validation_prefilter_summary)
            if not validation_prefilter_path.is_absolute():
                validation_prefilter_path = (
                    validation_summary_path.parent / validation_prefilter_path
                )
            if (
                validation_prefilter_path.resolve()
                != prefilter_summary_path.resolve()
            ):
                validation_prefilter_lineage_status = "reference_mismatch"
                validation_prefilter_lineage_error = True
            else:
                validation_prefilter_summary_sha256 = str(
                    validation_payload.get("prefilter_summary_sha256") or ""
                ).strip() or "none"
                if validation_prefilter_summary_sha256 == "none":
                    validation_prefilter_lineage_status = "sha256_missing"
                    validation_prefilter_lineage_error = True
                elif (
                    prefilter_summary_sha256 is None
                    or validation_prefilter_summary_sha256
                    != prefilter_summary_sha256
                ):
                    validation_prefilter_lineage_status = "sha256_mismatch"
                    validation_prefilter_lineage_error = True
                else:
                    validation_prefilter_lineage_status = "ok"
    prefilter_families = candidate_names_from_rows(prefilter_rows)
    prefilter_positive_families = candidate_names_from_rows(
        prefilter_rows, verdict="positive-edge"
    )
    validated_families = candidate_names_from_rows(validation_rows)
    validation_positive_families = candidate_names_from_rows(
        validation_rows, verdict="positive-edge"
    )
    missing_validation_families = [
        name
        for name in prefilter_positive_families
        if name not in validated_families
    ]

    prefilter_age_hours = file_age_hours(prefilter_summary_path, now_utc=now_utc)
    validation_age_hours = file_age_hours(validation_summary_path, now_utc=now_utc)
    prefilter_summary_mtime_epoch = file_mtime_epoch(prefilter_summary_path)
    validation_summary_mtime_epoch = file_mtime_epoch(validation_summary_path)
    proof_horizon_age_hours = file_age_hours(
        proof_horizon_summary_path, now_utc=now_utc
    )
    proof_horizon_mtime_epoch = file_mtime_epoch(proof_horizon_summary_path)
    stale_parts: list[str] = []
    if (
        prefilter_age_hours is not None
        and prefilter_age_hours > max_age_hours
    ):
        stale_parts.append("prefilter")
    if (
        validation_age_hours is not None
        and validation_age_hours > max_age_hours
    ):
        stale_parts.append("validation")
    if require_candidate_attribution:
        if rows_missing_candidate_attribution(prefilter_rows):
            stale_parts.append("prefilter_candidate_attribution")
        if rows_missing_candidate_attribution(validation_rows):
            stale_parts.append("validation_candidate_attribution")

    invalid_parts = [
        name
        for name, error in (
            ("prefilter", prefilter_error),
            ("validation", validation_error),
        )
        if error is not None and error != "missing"
    ]
    if validation_prefilter_lineage_error:
        invalid_parts.append("validation_prefilter_lineage")
    if require_candidate_attribution:
        if (
            prefilter_payload is not None
            and prefilter_fractionability_error is not None
        ):
            invalid_parts.append(
                f"prefilter_fractionability_{prefilter_fractionability_error}"
            )
            fractionability_lineage_status = "invalid_prefilter"
        if (
            validation_payload is not None
            and validation_fractionability_error is not None
        ):
            invalid_parts.append(
                f"validation_fractionability_{validation_fractionability_error}"
            )
            fractionability_lineage_status = "invalid_validation"
        if (
            prefilter_fractionability is not None
            and validation_fractionability is not None
        ):
            if prefilter_fractionability != validation_fractionability:
                invalid_parts.append("fractionability_lineage_mismatch")
                fractionability_lineage_status = "mismatch"
            elif fractionability_lineage_status == "not_required":
                fractionability_lineage_status = "ok"
    if invalid_parts:
        evidence_status = "invalid"
        detail = ",".join(invalid_parts)
    elif prefilter_error == "missing" and validation_error == "missing":
        evidence_status = "missing"
        detail = "latest_summaries_missing"
    elif validation_error == "missing":
        evidence_status = "missing_validation"
        detail = "latest_validation_summary_missing"
    elif prefilter_error == "missing":
        evidence_status = "missing_prefilter"
        detail = "latest_prefilter_summary_missing"
    elif stale_parts:
        evidence_status = "stale"
        detail = ",".join(stale_parts)
    else:
        evidence_status = "ok"
        detail = "fresh"

    scan_promotion_approved = bool(
        validation_payload.get("promotion_approved") if validation_payload else False
    )
    validation_positive_rows = int(
        validation_payload.get("positive_edge_validation_rows", 0)
        if validation_payload
        else 0
    )
    prefilter_positive_rows = int(
        prefilter_payload.get("positive_edge_prefilter_rows", 0)
        if prefilter_payload
        else 0
    )
    proof_horizon_selection = (
        proof_horizon_payload.get("candidate_selection")
        if isinstance(proof_horizon_payload, dict)
        else None
    )
    preferred_promotion_candidate = None
    preferred_promotion_scale = None
    proof_horizon_selection_reason = "none"
    proof_horizon_candidate_count: int | None = None
    proof_horizon_passing_candidate_count: int | None = None
    proof_horizon_selected_row_status: str | None = None
    if isinstance(proof_horizon_selection, dict):
        preferred_promotion_candidate = str(
            proof_horizon_selection.get("selected_candidate") or ""
        ).strip() or None
        preferred_promotion_scale = as_float_or_none(
            proof_horizon_selection.get("selected_candidate_scale")
        )
        proof_horizon_selection_reason = str(
            proof_horizon_selection.get("selection_reason") or "none"
        ).strip() or "none"
        proof_horizon_candidate_count = as_int_or_none(
            proof_horizon_selection.get("candidate_count")
        )
        proof_horizon_passing_candidate_count = as_int_or_none(
            proof_horizon_selection.get("passing_candidate_count")
        )
        proof_horizon_selection_rows = proof_horizon_selection.get("rows")
        if isinstance(proof_horizon_selection_rows, list):
            selected_rows = []
            for row in proof_horizon_selection_rows:
                if not isinstance(row, dict):
                    continue
                if (
                    str(row.get("candidate") or "").strip()
                    != preferred_promotion_candidate
                ):
                    continue
                row_scale = as_float_or_none(row.get("candidate_scale"))
                if row_scale is None or preferred_promotion_scale is None:
                    continue
                if math.isclose(
                    row_scale,
                    preferred_promotion_scale,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ):
                    selected_rows.append(row)
            if len(selected_rows) == 1:
                proof_horizon_selected_row_status = str(
                    selected_rows[0].get("status") or ""
                ).strip() or None
    promotion_candidate = best_promotion_candidate_from_rows(
        validation_rows,
        preferred_name=preferred_promotion_candidate,
        preferred_scale=preferred_promotion_scale,
    )
    promotion_candidate_name = (
        str(promotion_candidate.get("candidate") or "").strip()
        if promotion_candidate is not None
        else ""
    )
    promotion_candidate_denied = (
        promotion_candidate_name in promotion_denied_strategy_names
    )
    validation_denied_families = [
        name
        for name in validation_positive_families
        if name in promotion_denied_strategy_names
    ]
    proof_horizon_strategy = "none"
    proof_horizon_status = "not_applicable"
    proof_horizon_detail = "no_promotion_candidate"
    proof_horizon_trades: int | None = None
    proof_horizon_total_pnl: float | None = None
    proof_horizon_eventual_pass_rate: float | None = None
    proof_horizon_starts_eventually_passed: int | None = None
    proof_horizon_historical_starts: int | None = None
    proof_horizon_terminal_blockers = "none"
    proof_horizon_confidence_scales = "none"
    proof_horizon_candidate_scale: float | None = None
    if promotion_candidate is not None:
        promotion_candidate_scale = as_float_or_none(
            promotion_candidate.get("candidate_scale")
        )
        proof_horizon_status = "missing"
        proof_horizon_detail = "latest_proof_horizon_summary_missing"
        if proof_horizon_error and proof_horizon_error != "missing":
            proof_horizon_status = "invalid"
            proof_horizon_detail = proof_horizon_error
        elif proof_horizon_payload is not None:
            proof_horizon_strategy = (
                str(proof_horizon_payload.get("strategy") or "").strip() or "none"
            )
            proof_horizon_trades = as_int_or_none(
                proof_horizon_payload.get("trades")
            )
            proof_horizon_total_pnl = as_float_or_none(
                proof_horizon_payload.get("total_pnl")
            )
            proof_horizon_eventual_pass_rate = as_float_or_none(
                proof_horizon_payload.get("eventual_pass_rate")
            )
            proof_horizon_starts_eventually_passed = as_int_or_none(
                proof_horizon_payload.get("starts_eventually_passed")
            )
            proof_horizon_historical_starts = as_int_or_none(
                proof_horizon_payload.get("historical_starts_checked")
            )
            proof_horizon_terminal_blockers = summarize_counts(
                proof_horizon_payload.get("terminal_blockers")
            )
            proof_horizon_raw_confidence_scales = proof_horizon_payload.get(
                "confidence_scales"
            )
            if isinstance(proof_horizon_raw_confidence_scales, dict):
                scale_parts: list[str] = []
                for name, value in sorted(
                    proof_horizon_raw_confidence_scales.items(),
                    key=lambda item: str(item[0]),
                ):
                    scale_name = str(name).strip()
                    scale_value = as_float_or_none(value)
                    if not re.fullmatch(r"[A-Za-z0-9_:-]+", scale_name):
                        continue
                    if scale_value is None:
                        continue
                    scale_parts.append(f"{scale_name}:{scale_value:g}")
                    if scale_name == promotion_candidate_name:
                        proof_horizon_candidate_scale = scale_value
                proof_horizon_confidence_scales = (
                    ",".join(scale_parts) if scale_parts else "none"
                )
            proof_horizon_required_pnl = (
                as_float_or_none(proof_horizon_payload.get("min_pnl")) or 0.01
            )
            proof_horizon_strategy_parts = [
                part.strip()
                for part in proof_horizon_strategy.split("+")
                if part.strip()
            ]
            if (
                proof_horizon_age_hours is not None
                and proof_horizon_age_hours > max_age_hours
            ):
                proof_horizon_status = "stale"
                proof_horizon_detail = "proof_horizon_too_old"
            elif (
                proof_horizon_mtime_epoch is not None
                and validation_summary_mtime_epoch is not None
                and proof_horizon_mtime_epoch < validation_summary_mtime_epoch
            ):
                proof_horizon_status = "stale"
                proof_horizon_detail = "proof_horizon_older_than_validation"
            elif not isinstance(proof_horizon_selection, dict):
                proof_horizon_status = "invalid"
                proof_horizon_detail = "candidate_selection_missing"
            elif (
                require_candidate_attribution
                and proof_horizon_fractionability_error is not None
            ):
                proof_horizon_status = "invalid"
                proof_horizon_detail = (
                    "fractionability_"
                    f"{proof_horizon_fractionability_error}"
                )
            elif (
                require_candidate_attribution
                and proof_horizon_fractionability
                != validation_fractionability
            ):
                proof_horizon_status = "mismatch"
                proof_horizon_detail = "fractionability_lineage_mismatch"
            elif (
                proof_horizon_candidate_count is None
                or proof_horizon_passing_candidate_count is None
            ):
                proof_horizon_status = "invalid"
                proof_horizon_detail = "candidate_selection_counts_missing"
            elif (
                proof_horizon_passing_candidate_count < 1
                or proof_horizon_selection_reason != "first_passing"
                or proof_horizon_selected_row_status != "ok"
            ):
                proof_horizon_status = "failed"
                proof_horizon_detail = "candidate_selection_failed"
            elif promotion_candidate_name not in proof_horizon_strategy_parts:
                proof_horizon_status = "mismatch"
                proof_horizon_detail = "candidate_missing_from_strategy_label"
            elif not isinstance(proof_horizon_raw_confidence_scales, dict):
                proof_horizon_status = "mismatch"
                proof_horizon_detail = "confidence_scales_missing"
            elif (
                promotion_candidate_scale is not None
                and proof_horizon_candidate_scale is None
                and not math.isclose(
                    promotion_candidate_scale,
                    1.0,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
            ):
                proof_horizon_status = "mismatch"
                proof_horizon_detail = "candidate_scale_missing"
            elif (
                promotion_candidate_scale is not None
                and proof_horizon_candidate_scale is not None
                and not math.isclose(
                    promotion_candidate_scale,
                    proof_horizon_candidate_scale,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
            ):
                proof_horizon_status = "mismatch"
                proof_horizon_detail = "candidate_scale_mismatch"
            elif (
                proof_horizon_trades is None
                or proof_horizon_total_pnl is None
                or proof_horizon_starts_eventually_passed is None
                or proof_horizon_historical_starts is None
                or proof_horizon_eventual_pass_rate is None
            ):
                proof_horizon_status = "invalid"
                proof_horizon_detail = "required_metrics_missing"
            elif proof_horizon_total_pnl < proof_horizon_required_pnl:
                proof_horizon_status = "failed"
                proof_horizon_detail = "total_pnl_below_gate"
            elif proof_horizon_starts_eventually_passed <= 0:
                proof_horizon_status = "failed"
                proof_horizon_detail = "no_historical_start_passed"
            elif (
                proof_horizon_eventual_pass_rate
                < second_strategy_min_proof_horizon_pass_rate
            ):
                proof_horizon_status = "failed"
                proof_horizon_detail = "eventual_pass_rate_below_gate"
            else:
                proof_horizon_status = "ok"
                proof_horizon_detail = "fresh"
    approval_status, approval_strategy = approval_marker_status(
        approval_payload,
        approval_error,
        evidence_status=evidence_status,
        evidence_root=output_root,
        strategy_version=strategy_version,
        env_file=env_file,
        now_utc=now_utc,
        validation_summary_path=validation_summary_path,
        validation_summary_sha256=validation_summary_sha256,
        proof_horizon_summary_path=proof_horizon_summary_path,
        proof_horizon_summary_sha256=proof_horizon_summary_sha256,
        proof_horizon_status=proof_horizon_status,
        proof_horizon_payload=proof_horizon_payload,
        validation_rows=validation_rows,
        validation_positive_families=validation_positive_families,
        promotion_denied_strategies=promotion_denied_strategy_names,
    )
    promotion_approved = approval_status == "approved"
    promotion_approved_source = (
        "approval_marker"
        if approval_status == "approved"
        else "scan_summary_ignored"
        if scan_promotion_approved
        else "none"
    )
    promotion_action_status = "none"
    if promotion_candidate_denied:
        promotion_action_status = "rejected_promotion_denylist"
    elif promotion_approved and validation_positive_rows > 0:
        promotion_action_status = "approved"
    elif evidence_status == "ok" and promotion_candidate is not None:
        if proof_horizon_status == "ok":
            promotion_action_status = "ready"
        elif proof_horizon_status == "missing":
            promotion_action_status = "blocked_missing_proof_horizon"
        elif proof_horizon_status == "failed":
            promotion_action_status = "rejected_proof_horizon"
        else:
            promotion_action_status = "blocked_unusable_proof_horizon"
    elif validation_positive_rows > 0:
        promotion_action_status = "review_evidence"

    if validation_error == "missing":
        candidate_status = "validation_missing"
    elif prefilter_error == "missing":
        candidate_status = "prefilter_missing"
    elif promotion_candidate_denied:
        candidate_status = "promotion_denied"
    elif promotion_approved and validation_positive_rows > 0:
        candidate_status = "approved_candidate_found"
    elif validation_positive_rows > 0:
        if promotion_candidate is None:
            candidate_status = "validation_candidate_not_promotable"
        elif proof_horizon_status == "ok":
            candidate_status = "validated_candidate_unapproved"
        elif proof_horizon_status == "missing":
            candidate_status = "proof_horizon_missing"
        elif proof_horizon_status == "failed":
            candidate_status = "proof_horizon_failed"
        else:
            candidate_status = "proof_horizon_unusable"
    elif missing_validation_families:
        candidate_status = "partial_validation"
    elif prefilter_positive_rows > 0 and validation_positive_rows == 0:
        candidate_status = "no_positive_validation_edge"
    elif prefilter_positive_rows == 0:
        candidate_status = "no_positive_prefilter_edge"
    else:
        candidate_status = "no_approved_candidate"

    return {
        "status": evidence_status,
        "candidate_status": candidate_status,
        "detail": detail,
        "root": str(output_root),
        "prefilter_summary": str(prefilter_summary_path),
        "validation_summary": str(validation_summary_path),
        "validation_prefilter_summary": validation_prefilter_summary,
        "validation_prefilter_summary_sha256": (
            validation_prefilter_summary_sha256
        ),
        "validation_prefilter_lineage_status": (
            validation_prefilter_lineage_status
        ),
        "fractionability_lineage_status": fractionability_lineage_status,
        "fractionability_snapshot_sha256": (
            validation_fractionability.get("snapshot_sha256")
            if validation_fractionability is not None
            else "none"
        ),
        "fractionability_universe_sha256": (
            validation_fractionability.get("universe_sha256")
            if validation_fractionability is not None
            else "none"
        ),
        "proof_horizon_summary": str(proof_horizon_summary_path),
        "prefilter_summary_sha256": prefilter_summary_sha256 or "none",
        "validation_summary_sha256": validation_summary_sha256 or "none",
        "proof_horizon_summary_sha256": proof_horizon_summary_sha256 or "none",
        "prefilter_age_hours": prefilter_age_hours,
        "validation_age_hours": validation_age_hours,
        "proof_horizon_age_hours": proof_horizon_age_hours,
        "prefilter_summary_mtime_epoch": prefilter_summary_mtime_epoch,
        "validation_summary_mtime_epoch": validation_summary_mtime_epoch,
        "proof_horizon_mtime_epoch": proof_horizon_mtime_epoch,
        "max_age_hours": max_age_hours,
        "proof_horizon_status": proof_horizon_status,
        "proof_horizon_detail": proof_horizon_detail,
        "proof_horizon_strategy": proof_horizon_strategy,
        "proof_horizon_trades": proof_horizon_trades,
        "proof_horizon_total_pnl": proof_horizon_total_pnl,
        "proof_horizon_eventual_pass_rate": proof_horizon_eventual_pass_rate,
        "proof_horizon_min_pass_rate": second_strategy_min_proof_horizon_pass_rate,
        "proof_horizon_selection_reason": proof_horizon_selection_reason,
        "proof_horizon_candidate_count": proof_horizon_candidate_count,
        "proof_horizon_passing_candidate_count": (
            proof_horizon_passing_candidate_count
        ),
        "proof_horizon_starts_eventually_passed": (
            proof_horizon_starts_eventually_passed
        ),
        "proof_horizon_historical_starts": proof_horizon_historical_starts,
        "proof_horizon_terminal_blockers": proof_horizon_terminal_blockers,
        "proof_horizon_confidence_scales": proof_horizon_confidence_scales,
        "proof_horizon_candidate_scale": proof_horizon_candidate_scale,
        "prefilter_families": prefilter_families,
        "prefilter_positive_rows": prefilter_positive_rows,
        "prefilter_positive_families": prefilter_positive_families,
        "validated_families": validated_families,
        "missing_validation_families": missing_validation_families,
        "validation_rows": len(validation_rows) if isinstance(validation_rows, list) else 0,
        "validation_positive_rows": validation_positive_rows,
        "validation_positive_families": validation_positive_families,
        "validation_denied_families": validation_denied_families,
        "promotion_denylist": sorted(promotion_denied_strategy_names),
        "promotion_candidate_denied": promotion_candidate_denied,
        "promotion_approved": promotion_approved,
        "promotion_approved_source": promotion_approved_source,
        "promotion_approval_marker": str(approval_marker_path),
        "promotion_approval_marker_status": approval_status,
        "promotion_approval_marker_strategy": approval_strategy,
        "promotion_action_status": promotion_action_status,
        "promotion_candidate": (
            str(promotion_candidate.get("candidate"))
            if promotion_candidate is not None
            else "none"
        ),
        "promotion_candidate_scale": (
            str(promotion_candidate.get("candidate_scale"))
            if promotion_candidate is not None
            else "none"
        ),
        "promotion_candidate_trades": (
            as_int_or_none(promotion_candidate.get("candidate_trades"))
            if promotion_candidate is not None
            else None
        ),
        "promotion_candidate_total_pnl": (
            as_float_or_none(promotion_candidate.get("candidate_total_pnl"))
            if promotion_candidate is not None
            else None
        ),
        "promotion_candidate_ci_low": (
            as_float_or_none(promotion_candidate.get("candidate_ci_low"))
            if promotion_candidate is not None
            else None
        ),
        "promotion_candidate_p_mean_le_zero": (
            as_float_or_none(promotion_candidate.get("candidate_p_mean_le_zero"))
            if promotion_candidate is not None
            else None
        ),
        "max_validation_candidates": (
            validation_payload.get("max_validation_candidates", "none")
            if validation_payload
            else "none"
        ),
        "validation_verdicts": summarize_validation_verdicts(validation_rows),
    }


def format_trade_pnl_atom(trade: dict, pnl: float) -> str:
    symbol = str(trade.get("symbol") or "unknown")
    exit_session_date = trade_exit_session_date(trade)
    if exit_session_date is not None:
        exit_session = exit_session_date.isoformat()
    else:
        exit_session = "unknown"
    return f"{symbol}:{pnl:.2f}@{exit_session}"


def trade_exit_session_date(trade: dict) -> date | None:
    exit_time = trade.get("exit_time")
    if isinstance(exit_time, datetime):
        return exit_time.astimezone(settings.market_timezone).date()
    return None


def load_scenario_coverage(
    *,
    symbols: list[str],
    scenario_dir: Path,
    expected_date: date,
    require_scenarios: bool,
) -> tuple[str, str]:
    if not require_scenarios:
        return "skipped", "disabled"
    if not scenario_dir.is_dir():
        return "missing", f"dir={scenario_dir}"

    problems: dict[str, list[str]] = {
        "missing": [],
        "unreadable": [],
        "empty_daily": [],
        "empty_intraday": [],
        "stale_daily": [],
        "stale_intraday": [],
    }
    for symbol in symbols:
        path = scenario_dir / f"{symbol}_252d.json"
        if not path.exists():
            problems["missing"].append(symbol)
            continue
        try:
            payload = json.loads(path.read_text())
            daily = payload.get("daily_bars") or []
            intraday = payload.get("intraday_bars") or []
            if not daily:
                problems["empty_daily"].append(symbol)
            else:
                daily_max = max(parse_bar_date(str(bar["timestamp"])) for bar in daily)
                if daily_max < expected_date:
                    problems["stale_daily"].append(f"{symbol}:{daily_max.isoformat()}")
            if not intraday:
                problems["empty_intraday"].append(symbol)
            else:
                intraday_max = max(
                    parse_bar_date(str(bar["timestamp"])) for bar in intraday
                )
                if intraday_max < expected_date:
                    problems["stale_intraday"].append(
                        f"{symbol}:{intraday_max.isoformat()}"
                    )
        except Exception as exc:
            problems["unreadable"].append(f"{symbol}:{exc}")

    if any(problems.values()):
        return "failed", format_problem_summary(problems)
    return "ok", "none"


def load_latest_completed_session_date(settings: Settings) -> tuple[date | None, str | None]:
    now = datetime.now(settings.market_timezone)
    try:
        calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
            start=now.date() - timedelta(days=14),
            end=now.date(),
        )
    except Exception as exc:
        return None, str(exc)

    completed = []
    for session in calendar:
        close_at = session.close_at
        if close_at.tzinfo is None:
            close_at = close_at.replace(tzinfo=settings.market_timezone)
        else:
            close_at = close_at.astimezone(settings.market_timezone)
        if now >= close_at + timedelta(minutes=30):
            completed.append(session.session_date)
    if not completed:
        return None, "no completed market sessions found"
    return max(completed), None


def load_next_market_session_date(settings: Settings) -> tuple[date | None, str | None]:
    now = datetime.now(settings.market_timezone)
    try:
        calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
            start=now.date(),
            end=now.date() + timedelta(days=10),
        )
    except Exception as exc:
        return None, str(exc)

    upcoming = [
        session.session_date for session in calendar if session.session_date >= now.date()
    ]
    if not upcoming:
        return None, "no upcoming market sessions found"
    return min(upcoming), None


def load_previous_market_session_date(
    settings: Settings, *, before_date: date
) -> tuple[date | None, str | None]:
    try:
        calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
            start=before_date - timedelta(days=14),
            end=before_date - timedelta(days=1),
        )
    except Exception as exc:
        return None, str(exc)

    previous = [
        session.session_date
        for session in calendar
        if session.session_date < before_date
    ]
    if not previous:
        return None, f"no market session found before {before_date.isoformat()}"
    return max(previous), None


def load_next_market_session_after(
    settings: Settings, *, after_date: date
) -> tuple[date | None, str | None]:
    try:
        calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
            start=after_date + timedelta(days=1),
            end=after_date + timedelta(days=14),
        )
    except Exception as exc:
        return None, str(exc)

    upcoming = [
        session.session_date for session in calendar if session.session_date > after_date
    ]
    if not upcoming:
        return None, f"no market session found after {after_date.isoformat()}"
    return min(upcoming), None


def load_upcoming_market_session_dates(
    settings: Settings, *, after_date: date, count: int
) -> tuple[list[date], str | None]:
    if count <= 0:
        return [], None
    lookahead_days = max(14, count * 10)
    try:
        calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
            start=after_date + timedelta(days=1),
            end=after_date + timedelta(days=lookahead_days),
        )
    except Exception as exc:
        return [], str(exc)

    upcoming = [
        session.session_date
        for session in calendar
        if session.session_date > after_date
    ]
    if len(upcoming) < count:
        return (
            upcoming,
            f"only {len(upcoming)} market sessions found after {after_date.isoformat()}",
        )
    return upcoming[:count], None


def load_broker_exposure(
    settings: Settings,
) -> tuple[
    int | None,
    int | None,
    str | None,
    str | None,
    float | None,
    float | None,
    float | None,
    bool | None,
    str | None,
    str | None,
]:
    try:
        broker = AlpacaExecutionAdapter.from_settings(settings)
        open_orders = broker.list_open_orders()
        open_positions = broker.list_positions()
        account = broker.get_account()
    except Exception as exc:
        return None, None, None, None, None, None, None, None, None, str(exc)
    open_order_symbols = ",".join(
        sorted({getattr(order, "symbol", "") for order in open_orders if getattr(order, "symbol", "")})
    ) or "none"
    open_position_symbols = ",".join(
        sorted({getattr(position, "symbol", "") for position in open_positions if getattr(position, "symbol", "")})
    ) or "none"
    equity = float(account.equity)
    buying_power = float(account.buying_power)
    minimum_buying_power = equity * float(settings.max_position_pct)
    trading_blocked = bool(account.trading_blocked)
    account_status = (
        "blocked"
        if trading_blocked or equity <= 0 or buying_power < minimum_buying_power
        else "ok"
    )
    return (
        len(open_orders),
        len(open_positions),
        open_order_symbols,
        open_position_symbols,
        equity,
        buying_power,
        minimum_buying_power,
        trading_blocked,
        account_status,
        None,
    )


settings = Settings.from_env()
trading_mode = TradingMode(os.environ.get("TRADING_MODE", "paper"))
strategy_version = os.environ["STRATEGY_VERSION"]
strategy_name = os.environ["PROOF_STATUS_STRATEGY"]
approved_strategy_names = [
    name.strip()
    for name in os.environ["PROOF_STATUS_APPROVED_STRATEGIES"].split(",")
    if name.strip()
]
approved_strategy_name_set = set(approved_strategy_names)
proof_strategy_names = parse_name_list(
    f"{strategy_name},{','.join(approved_strategy_names)}"
)
proof_strategy_csv = format_name_list(proof_strategy_names)
expected_readiness_decision_dry_run_strategy_names = parse_name_list(
    proof_strategy_csv
)
min_trades_text = os.environ["PROOF_STATUS_MIN_TRADES"]
min_trades = int(min_trades_text)
min_pnl_text = os.environ["PROOF_STATUS_MIN_PNL"]
min_pnl = float(min_pnl_text)
session_guard_min_trades_text = os.environ["PROOF_STATUS_SESSION_GUARD_MIN_TRADES"]
session_guard_min_pnl_text = os.environ["PROOF_STATUS_SESSION_GUARD_MIN_PNL"]
min_watchlist_symbols = int(os.environ["PROOF_STATUS_MIN_WATCHLIST_SYMBOLS"])
min_decision_dry_run_records = int(os.environ["PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS"])
min_decision_dry_run_evaluations = int(
    os.environ["PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS"]
)
scale_min_trades = int(os.environ["PROOF_STATUS_SCALE_MIN_TRADES"])
scale_min_strategies = int(os.environ["PROOF_STATUS_SCALE_MIN_STRATEGIES"])
scale_min_active_days = int(os.environ["PROOF_STATUS_SCALE_MIN_ACTIVE_DAYS"])
scale_max_single_win_pnl_share = float(
    os.environ["PROOF_STATUS_SCALE_MAX_SINGLE_WIN_PNL_SHARE"]
)
scale_min_profit_factor = float(os.environ["PROOF_STATUS_SCALE_MIN_PROFIT_FACTOR"])
scale_max_eod_loss_share = float(os.environ["PROOF_STATUS_SCALE_MAX_EOD_LOSS_SHARE"])
scale_max_operational_exit_loss_share = float(
    os.environ["PROOF_STATUS_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE"]
)
second_strategy_min_proof_horizon_pass_rate = float(
    os.environ["PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE"]
)
promotion_denylist_raw = os.environ.get(
    "PROOF_STATUS_SECOND_STRATEGY_PROMOTION_DENYLIST",
    "ema_pullback,vwap_cross",
)
promotion_denied_strategy_names = set(parse_name_list(promotion_denylist_raw))
promotion_denied_strategy_names.discard("none")
if (
    promotion_denylist_raw.strip().lower() not in {"", "none"}
    and not promotion_denied_strategy_names
):
    raise SystemExit("invalid second-strategy promotion denylist")
option_replay_min_sessions = int(
    os.environ["PROOF_STATUS_OPTION_REPLAY_MIN_SESSIONS"]
)
option_replay_min_points_per_session = int(
    os.environ["PROOF_STATUS_OPTION_REPLAY_MIN_POINTS_PER_SESSION"]
)
execution_min_entry_fill_rate = float(
    os.environ["PROOF_STATUS_EXECUTION_MIN_ENTRY_FILL_RATE"]
)
execution_max_capacity_reject_rate = float(
    os.environ["PROOF_STATUS_EXECUTION_MAX_CAPACITY_REJECT_RATE"]
)
min_confidence_floor = float(os.environ["PROOF_STATUS_MIN_CONFIDENCE_FLOOR"])
require_scenarios = os.environ.get("PROOF_STATUS_REQUIRE_SCENARIOS", "true").lower() == "true"
scenario_dir = Path(os.environ["PROOF_STATUS_SCENARIO_DIR"])
second_strategy_output_root = Path(
    os.environ["PROOF_STATUS_SECOND_STRATEGY_OUTPUT_ROOT"]
)
second_strategy_setup_output_root = Path(
    os.environ["PROOF_STATUS_SECOND_STRATEGY_SETUP_OUTPUT_ROOT"]
)
second_strategy_max_age_hours = int(
    os.environ["PROOF_STATUS_SECOND_STRATEGY_MAX_AGE_HOURS"]
)
promotion_write_access_status = os.environ.get(
    "PROOF_STATUS_PROMOTION_WRITE_ACCESS_STATUS",
    "unknown",
)
paper_approval_marker = os.environ.get(
    "PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER",
    "",
).strip()
paper_approval_env_file = os.environ.get(
    "PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE",
    "",
).strip()
promotion_env_file_writable = os.environ.get(
    "PROOF_STATUS_PROMOTION_ENV_FILE_WRITABLE",
    "unknown",
)
promotion_env_dir_writable = os.environ.get(
    "PROOF_STATUS_PROMOTION_ENV_DIR_WRITABLE",
    "unknown",
)
promotion_approval_marker_writable = os.environ.get(
    "PROOF_STATUS_PROMOTION_APPROVAL_MARKER_WRITABLE",
    "unknown",
)
promotion_approval_marker_dir_writable = os.environ.get(
    "PROOF_STATUS_PROMOTION_APPROVAL_MARKER_DIR_WRITABLE",
    "unknown",
)
promotion_env_keys = [
    "PAPER_APPROVED_STRATEGIES",
    "PROFIT_PROBE_STRATEGIES",
    "PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES",
    "PAPER_READINESS_EXPECT_ENABLED_STRATEGIES",
    "PAPER_ACTIVITY_STRATEGIES",
    "SESSION_GUARD_STRATEGIES",
    "PROOF_STATUS_APPROVED_STRATEGIES",
    "DEPLOY_EXPECT_ENABLED_STRATEGIES",
    "DEPLOY_DECISION_DRY_RUN_STRATEGIES",
]
promotion_env_keys_csv = ",".join(promotion_env_keys)
stream_start_grace_seconds = int(os.environ["PROOF_STATUS_STREAM_START_GRACE_SECONDS"])
readiness_max_pass_age_minutes = int(
    os.environ["PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES"]
)
fail_on_issues = os.environ.get("PROOF_STATUS_FAIL_ON_ISSUES", "false").lower() == "true"
proof_status_env_file = os.environ.get(
    "PROOF_STATUS_ENV_FILE",
    "/etc/alpaca_bot/alpaca-bot.env",
)
cron_health_status = os.environ.get("PROOF_STATUS_CRON_HEALTH_STATUS", "unknown")
cron_health_detail = os.environ.get("PROOF_STATUS_CRON_HEALTH_DETAIL", "").strip()
nightly_status = os.environ.get("PROOF_STATUS_NIGHTLY_STATUS", "unknown")
nightly_lock_status = os.environ.get("PROOF_STATUS_NIGHTLY_LOCK_STATUS", "unknown")
nightly_pid = os.environ.get("PROOF_STATUS_NIGHTLY_PID", "none")
nightly_source = os.environ.get("PROOF_STATUS_NIGHTLY_SOURCE", "unknown")
nightly_age_minutes = os.environ.get("PROOF_STATUS_NIGHTLY_AGE_MINUTES", "none")
nightly_log_age_minutes = os.environ.get(
    "PROOF_STATUS_NIGHTLY_LOG_AGE_MINUTES", "none"
)
nightly_active_log = os.environ.get("PROOF_STATUS_NIGHTLY_ACTIVE_LOG", "none")
nightly_max_age_minutes = os.environ.get(
    "PROOF_STATUS_NIGHTLY_MAX_AGE_MINUTES", "none"
)
nightly_stall_minutes = os.environ.get(
    "PROOF_STATUS_NIGHTLY_STALL_MINUTES", "none"
)
nightly_stage = os.environ.get("PROOF_STATUS_NIGHTLY_STAGE", "none")
nightly_detail = os.environ.get("PROOF_STATUS_NIGHTLY_DETAIL", "none")
nightly_run_age_limit_status = nightly_threshold_status(
    nightly_status,
    nightly_age_minutes,
    nightly_max_age_minutes,
    exceeded_suffix="_stale",
)
nightly_log_stall_status = nightly_threshold_status(
    nightly_status,
    nightly_log_age_minutes,
    nightly_stall_minutes,
    exceeded_suffix="_stalled",
)
second_strategy_scan_status = os.environ.get(
    "PROOF_STATUS_SECOND_STRATEGY_SCAN_STATUS", "unknown"
)
second_strategy_scan_detail = os.environ.get(
    "PROOF_STATUS_SECOND_STRATEGY_SCAN_DETAIL", "none"
)
second_strategy_scan_event_epoch = as_float_or_none(
    os.environ.get("PROOF_STATUS_SECOND_STRATEGY_SCAN_EVENT_EPOCH")
)
ops_health_status = os.environ.get("PROOF_STATUS_OPS_HEALTH_STATUS", "unknown")
ops_health_detail = os.environ.get("PROOF_STATUS_OPS_HEALTH_DETAIL", "").strip()
ops_close_only_health_status = os.environ.get(
    "PROOF_STATUS_OPS_CLOSE_ONLY_HEALTH_STATUS", "skipped"
)
ops_close_only_health_detail = os.environ.get(
    "PROOF_STATUS_OPS_CLOSE_ONLY_HEALTH_DETAIL", ""
).strip()
runtime_image_health_status = os.environ.get(
    "PROOF_STATUS_RUNTIME_IMAGE_HEALTH_STATUS", "unknown"
)
runtime_image_health_detail = os.environ.get(
    "PROOF_STATUS_RUNTIME_IMAGE_HEALTH_DETAIL", ""
).strip()
proof_start = parse_date(os.environ["PROOF_STATUS_START_DATE"], name="PROOF_STATUS_START_DATE")
end_value = os.environ.get("PROOF_STATUS_END_DATE", "")
current_market_datetime = datetime.now(settings.market_timezone)
current_market_date = current_market_datetime.date()
latest_completed_session, calendar_warning = load_latest_completed_session_date(settings)
next_market_session, next_session_warning = load_next_market_session_date(settings)
if next_session_warning:
    calendar_warning = (
        f"{calendar_warning}; {next_session_warning}"
        if calendar_warning
        else next_session_warning
    )
readiness_target_session = next_market_session or current_market_date
if readiness_target_session < proof_start:
    readiness_target_session = proof_start
readiness_expected_decision_dry_run_session = None
(
    readiness_expected_decision_dry_run_session,
    readiness_previous_session_warning,
) = load_previous_market_session_date(settings, before_date=readiness_target_session)
if readiness_previous_session_warning:
    calendar_warning = (
        f"{calendar_warning}; {readiness_previous_session_warning}"
        if calendar_warning
        else readiness_previous_session_warning
    )
(
    broker_open_orders,
    broker_open_positions,
    broker_open_order_symbols,
    broker_open_position_symbols,
    broker_equity,
    broker_buying_power,
    broker_minimum_buying_power,
    broker_trading_blocked,
    broker_account_status,
    broker_exposure_warning,
) = load_broker_exposure(settings)
proof_end = (
    parse_date(end_value, name="PROOF_STATUS_END_DATE")
    if end_value
    else latest_completed_session or current_market_date
)
scenario_expected_session = proof_end
if (
    not end_value
    and latest_completed_session is not None
    and latest_completed_session >= current_market_date
):
    previous_session, previous_session_warning = load_previous_market_session_date(
        settings, before_date=current_market_date
    )
    if previous_session is not None:
        scenario_expected_session = previous_session
    elif previous_session_warning:
        calendar_warning = (
            f"{calendar_warning}; {previous_session_warning}"
            if calendar_warning
            else previous_session_warning
        )
post_close_target_session = proof_end if proof_end >= proof_start else None
activity_target_session = None
if current_market_date >= proof_start and (
    next_market_session == current_market_date
    or latest_completed_session == current_market_date
):
    activity_target_session = current_market_date
now_utc = datetime.now(timezone.utc)
second_strategy_evidence = load_second_strategy_evidence(
    output_root=second_strategy_output_root,
    now_utc=now_utc,
    max_age_hours=second_strategy_max_age_hours,
    strategy_version=strategy_version,
    env_file=proof_status_env_file,
    require_candidate_attribution=True,
)
second_strategy_setup_evidence = load_second_strategy_evidence(
    output_root=second_strategy_setup_output_root,
    now_utc=now_utc,
    max_age_hours=second_strategy_max_age_hours,
    strategy_version=strategy_version,
    env_file=proof_status_env_file,
)

if second_strategy_scan_status == "failed" and second_strategy_evidence["status"] == "ok":
    evidence_event_epochs = [
        value
        for value in (
            second_strategy_evidence["prefilter_summary_mtime_epoch"],
            second_strategy_evidence["validation_summary_mtime_epoch"],
        )
        if value is not None
    ]
    if (
        second_strategy_scan_event_epoch is not None
        and evidence_event_epochs
        and max(evidence_event_epochs) > second_strategy_scan_event_epoch
    ):
        second_strategy_scan_status = "ok"
        second_strategy_scan_detail = (
            "fresh_second_strategy_evidence_supersedes_failed_scan"
        )

market_timezone = settings.market_timezone.key
readiness_due = False
readiness_first_check_time = time(9, 15)
readiness_due_time = time(9, 25)
readiness_required_since = datetime.combine(
    readiness_target_session,
    readiness_first_check_time,
    settings.market_timezone,
).astimezone(timezone.utc)
readiness_required_since_text = readiness_required_since.isoformat()
readiness_due_after = (
    f"{readiness_target_session.isoformat()} "
    f"{readiness_due_time.strftime('%H:%M')} {settings.market_timezone.key}"
)
readiness_due = current_market_datetime.date() > readiness_target_session or (
    current_market_datetime.date() == readiness_target_session
    and current_market_datetime.time() >= readiness_due_time
)
readiness_target_session_completed = (
    latest_completed_session is not None
    and latest_completed_session >= readiness_target_session
)
readiness_stale_blocks_proof = readiness_due and not readiness_target_session_completed
option_snapshot_due_time = time(10, 0)
option_snapshot_target_session = None
if current_market_date >= proof_start and (
    next_market_session == current_market_date
    or latest_completed_session == current_market_date
):
    option_snapshot_target_session = current_market_date
elif latest_completed_session is not None and latest_completed_session >= proof_start:
    option_snapshot_target_session = latest_completed_session
option_snapshot_due_after = (
    f"{option_snapshot_target_session.isoformat()} "
    f"{option_snapshot_due_time.strftime('%H:%M')} {settings.market_timezone.key}"
    if option_snapshot_target_session
    else "none"
)
option_snapshot_due = (
    bool(settings.option_chain_snapshot_dir)
    and bool(settings.option_chain_symbols)
    and option_snapshot_target_session is not None
    and (
        current_market_datetime.date() > option_snapshot_target_session
        or (
            current_market_datetime.date() == option_snapshot_target_session
            and current_market_datetime.time() >= option_snapshot_due_time
        )
    )
)

conn = connect_postgres(settings.database_url)
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(string_agg(strategy_name, ',' ORDER BY strategy_name), '')
            FROM strategy_flags
            WHERE trading_mode = %s
              AND strategy_version = %s
              AND enabled = TRUE
            """,
            (trading_mode.value, strategy_version),
        )
        active_row = cur.fetchone()
        active_strategies = active_row[0] if active_row else ""
        active_strategy_names = [name for name in active_strategies.split(",") if name]

        cur.execute(
            """
            SELECT COALESCE(string_agg(strategy_name, ',' ORDER BY strategy_name), '')
            FROM strategy_flags
            WHERE trading_mode = %s
              AND strategy_version = %s
              AND enabled = FALSE
            """,
            (trading_mode.value, strategy_version),
        )
        disabled_row = cur.fetchone()
        disabled_strategies = disabled_row[0] if disabled_row else ""
        disabled_strategy_names = [
            name for name in disabled_strategies.split(",") if name
        ]
        stock_strategy_name_set = set(STRATEGY_REGISTRY)
        option_strategy_name_set = set(OPTION_STRATEGY_NAMES)
        active_stock_strategy_names = [
            name for name in active_strategy_names if name in stock_strategy_name_set
        ]
        active_option_strategy_names = [
            name for name in active_strategy_names if name in option_strategy_name_set
        ]
        disabled_stock_strategy_names = [
            name for name in disabled_strategy_names if name in stock_strategy_name_set
        ]
        disabled_option_strategy_names = [
            name for name in disabled_strategy_names if name in option_strategy_name_set
        ]
        option_snapshot_summary = option_snapshot_ledger_summary(
            settings.option_chain_snapshot_dir,
            option_snapshot_target_session,
            interval_minutes=settings.entry_timeframe_minutes,
            min_points_per_session=option_replay_min_points_per_session,
        )
        option_snapshot_file_count = int(option_snapshot_summary["file_count"])
        option_snapshot_session_count = int(
            option_snapshot_summary["snapshot_session_count"]
        )
        option_snapshot_replay_session_count = int(
            option_snapshot_summary["replay_session_count"]
        )
        if not settings.option_chain_snapshot_dir:
            option_snapshot_status = "unconfigured"
        elif not settings.option_chain_symbols:
            option_snapshot_status = "misconfigured"
        elif option_snapshot_file_count <= 0:
            option_snapshot_status = "missing" if option_snapshot_due else "not_due"
        elif (
            option_snapshot_target_session is not None
            and option_snapshot_summary["latest_session"]
            != option_snapshot_target_session.isoformat()
        ):
            option_snapshot_status = "stale" if option_snapshot_due else "not_due"
        elif int(option_snapshot_summary["latest_contracts"]) <= 0:
            option_snapshot_status = "empty" if option_snapshot_due else "not_due"
        else:
            option_snapshot_status = "ok"
        option_snapshot_replay_ready = (
            option_snapshot_status == "ok"
            and option_snapshot_replay_session_count >= option_replay_min_sessions
        )
        replay_supported_option_strategy_name_set = (
            option_strategy_name_set if option_snapshot_replay_ready else set()
        )
        replay_supported_strategy_name_set = (
            stock_strategy_name_set | replay_supported_option_strategy_name_set
        )
        if option_snapshot_replay_ready:
            option_replay_status = "supported"
        elif option_snapshot_status == "ok":
            option_replay_status = (
                "insufficient_sessions"
                if option_snapshot_session_count < option_replay_min_sessions
                else "insufficient_coverage"
            )
        else:
            option_replay_status = f"snapshot_{option_snapshot_status}"
        active_replay_supported_strategy_names = [
            name
            for name in active_strategy_names
            if name in replay_supported_strategy_name_set
        ]
        disabled_replay_supported_strategy_names = [
            name
            for name in disabled_strategy_names
            if name in replay_supported_strategy_name_set
        ]
        active_replay_unsupported_strategy_names = [
            name
            for name in active_strategy_names
            if name not in replay_supported_strategy_name_set
        ]
        disabled_replay_unsupported_strategy_names = [
            name
            for name in disabled_strategy_names
            if name not in replay_supported_strategy_name_set
        ]
        option_gated_disabled_strategy_names = (
            disabled_option_strategy_names
            if not bool(settings.enable_options_trading)
            else []
        )
        approved_disabled_stock_candidate_names = [
            name
            for name in disabled_stock_strategy_names
            if name in approved_strategy_name_set
        ]
        approved_disabled_option_candidate_names = [
            name
            for name in disabled_option_strategy_names
            if name in approved_strategy_name_set
        ]
        validated_positive_candidate_names = (
            list(second_strategy_evidence["validation_positive_families"])
            if second_strategy_evidence["status"] == "ok"
            else []
        )
        validated_unapproved_stock_candidate_names = [
            name
            for name in validated_positive_candidate_names
            if (
                name in stock_strategy_name_set
                and name not in approved_strategy_name_set
                and name not in promotion_denied_strategy_names
            )
        ]
        validated_unapproved_option_candidate_names = [
            name
            for name in validated_positive_candidate_names
            if name in option_strategy_name_set and name not in approved_strategy_name_set
        ]
        approved_active_strategy_names = [
            name for name in active_strategy_names if name in approved_strategy_name_set
        ]
        approved_replay_active_strategy_names = [
            name
            for name in approved_active_strategy_names
            if name in replay_supported_strategy_name_set
        ]
        unapproved_active_strategy_names = [
            name for name in active_strategy_names if name not in approved_strategy_name_set
        ]
        approved_active_strategies = ",".join(approved_active_strategy_names)
        approved_replay_active_strategies = ",".join(
            approved_replay_active_strategy_names
        )
        active_replay_unsupported_strategies = ",".join(
            active_replay_unsupported_strategy_names
        )
        unapproved_active_strategies = ",".join(unapproved_active_strategy_names)
        approved_strategy_allowlist = ",".join(approved_strategy_names)

        cur.execute(
            """
            SELECT strategy_name, weight, sharpe
            FROM strategy_weights
            WHERE trading_mode = %s
              AND strategy_version = %s
            ORDER BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        strategy_weight_rows = cur.fetchall()
        weights_by_strategy = {
            row[0]: {"weight": row[1], "sharpe": row[2]}
            for row in strategy_weight_rows
        }

        cur.execute(
            """
            SELECT floor_value, manual_floor_baseline, set_by
            FROM confidence_floor_store
            WHERE trading_mode = %s
              AND strategy_version = %s
            """,
            (trading_mode.value, strategy_version),
        )
        confidence_floor_row = cur.fetchone()
        if confidence_floor_row:
            confidence_floor_value = float(confidence_floor_row[0])
            confidence_floor_manual_baseline = float(confidence_floor_row[1])
            confidence_floor_set_by = confidence_floor_row[2] or "unknown"
        else:
            confidence_floor_value = float(settings.confidence_floor)
            confidence_floor_manual_baseline = float(settings.confidence_floor)
            confidence_floor_set_by = "settings"

        cur.execute(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE enabled = TRUE AND COALESCE(ignored, FALSE) = FALSE
              )::int AS active_symbols,
              COUNT(*) FILTER (WHERE enabled = TRUE)::int AS enabled_symbols,
              COUNT(*) FILTER (
                WHERE enabled = TRUE AND COALESCE(ignored, FALSE) = TRUE
              )::int AS ignored_symbols,
              COALESCE(
                array_agg(symbol ORDER BY symbol) FILTER (
                  WHERE enabled = TRUE AND COALESCE(ignored, FALSE) = FALSE
                ),
                ARRAY[]::text[]
              ) AS active_symbol_names
            FROM symbol_watchlist
            WHERE trading_mode = %s
            """,
            (trading_mode.value,),
        )
        watchlist_row = cur.fetchone()
        active_watchlist_symbols = int(watchlist_row[0] or 0) if watchlist_row else 0
        enabled_watchlist_symbols = int(watchlist_row[1] or 0) if watchlist_row else 0
        ignored_watchlist_symbols = int(watchlist_row[2] or 0) if watchlist_row else 0
        active_watchlist_symbol_names = list(watchlist_row[3] or []) if watchlist_row else []

        cur.execute(
            """
            SELECT check_name, status, exit_code, session_date, proof_start, created_at
            FROM (
              SELECT DISTINCT ON (payload->>'check_name')
                payload->>'check_name' AS check_name,
                COALESCE(payload->>'status', '') AS status,
                COALESCE(payload->>'exit_code', '') AS exit_code,
                COALESCE(payload->>'session_date', '') AS session_date,
                COALESCE(payload->>'proof_start', '') AS proof_start,
                to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at
              FROM audit_events
              WHERE event_type = 'scheduled_check_completed'
                AND payload->>'trading_mode' = %s
                AND payload->>'strategy_version' = %s
                AND payload->>'proof_start' = %s
                AND payload->>'check_name' IN (
                  'paper_readiness',
                  'paper_activity',
                  'session_guard',
                  'paper_profit_probe'
                )
                AND (
                  payload->>'check_name' NOT IN (
                    'paper_activity',
                    'session_guard',
                    'paper_profit_probe'
                  )
                  OR %s = %s
                  OR payload->>'strategies' = %s
                )
                AND (
                  payload->>'check_name' <> 'paper_profit_probe'
                  OR payload->>'min_trades' = %s
                )
                AND (
                  payload->>'check_name' <> 'paper_profit_probe'
                  OR payload->>'min_pnl' = %s
                )
                AND (
                  payload->>'check_name' <> 'session_guard'
                  OR payload->>'min_trades' = %s
                )
                AND (
                  payload->>'check_name' <> 'session_guard'
                  OR payload->>'min_pnl' = %s
                )
              ORDER BY payload->>'check_name', created_at DESC, event_id DESC
            ) latest
            ORDER BY check_name
            """,
            (
                trading_mode.value,
                strategy_version,
                proof_start.isoformat(),
                proof_strategy_csv,
                strategy_name,
                proof_strategy_csv,
                min_trades_text,
                min_pnl_text,
                session_guard_min_trades_text,
                session_guard_min_pnl_text,
            ),
        )
        scheduled_checks = cur.fetchall()

        activity_audit_row = None
        if activity_target_session is not None:
            cur.execute(
                """
                SELECT
                  COALESCE(payload->>'status', '') AS status,
                  COALESCE(payload->>'exit_code', '') AS exit_code,
                  created_at
                FROM audit_events
                WHERE event_type = 'scheduled_check_completed'
                  AND payload->>'trading_mode' = %s
                  AND payload->>'strategy_version' = %s
                  AND payload->>'check_name' = 'paper_activity'
                  AND payload->>'session_date' = %s
                  AND payload->>'proof_start' = %s
                  AND (NOT (payload ? 'strategy') OR payload->>'strategy' = %s)
                  AND (%s = %s OR payload->>'strategies' = %s)
                ORDER BY created_at DESC, event_id DESC
                LIMIT 1
                """,
                (
                    trading_mode.value,
                    strategy_version,
                    activity_target_session.isoformat(),
                    proof_start.isoformat(),
                    strategy_name,
                    proof_strategy_csv,
                    strategy_name,
                    proof_strategy_csv,
                ),
            )
            activity_audit_row = cur.fetchone()

        cur.execute(
            """
            SELECT created_at
            FROM audit_events
            WHERE event_type = 'supervisor_started'
              AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = %s)
              AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = %s)
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (trading_mode.value, strategy_version),
        )
        latest_supervisor_row = cur.fetchone()
        latest_supervisor_started_at = (
            latest_supervisor_row[0] if latest_supervisor_row else None
        )

        cur.execute(
            """
            SELECT event_type, created_at
            FROM audit_events
            WHERE event_type IN (
                'trade_update_stream_started',
                'trade_update_stream_stopped',
                'trade_update_stream_failed',
                'trade_update_failed',
                'stream_restart_failed',
                'protective_stop_quantity_replace_failed'
              )
              AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = %s)
              AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = %s)
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (trading_mode.value, strategy_version),
        )
        latest_stream_event_row = cur.fetchone()

        cur.execute(
            """
            SELECT created_at
            FROM audit_events
            WHERE event_type = 'trade_update_stream_started'
              AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = %s)
              AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = %s)
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (trading_mode.value, strategy_version),
        )
        latest_stream_start_row = cur.fetchone()
        latest_stream_started_at = (
            latest_stream_start_row[0] if latest_stream_start_row else None
        )

        cur.execute(
            """
            SELECT
              COALESCE(payload->>'status', '') AS status,
              created_at,
              COALESCE(payload->>'reason', '') AS reason,
              COALESCE(payload->>'decision_dry_run_strategy', '') AS decision_dry_run_strategy,
              COALESCE(payload->>'decision_dry_run_as_of', '') AS decision_dry_run_as_of,
              COALESCE(payload->>'decision_dry_run_active', '') AS decision_dry_run_active,
              COALESCE(payload->>'decision_dry_run_records', '') AS decision_dry_run_records,
              COALESCE(payload->>'decision_dry_run_accepted', '') AS decision_dry_run_accepted,
              COALESCE(payload->>'decision_dry_run_entry_intents', '') AS decision_dry_run_entry_intents,
              COALESCE(payload->>'decision_dry_run_sample', '') AS decision_dry_run_sample,
              COALESCE(payload->>'decision_dry_run_sample_times', '') AS decision_dry_run_sample_times,
              COALESCE(payload->>'decision_dry_run_evaluations', '') AS decision_dry_run_evaluations,
              COALESCE(payload->>'decision_dry_run_min_decision_records', '') AS decision_dry_run_min_records,
              COALESCE(payload->>'decision_dry_run_max_accepted', '') AS decision_dry_run_max_accepted,
              COALESCE(payload->>'decision_dry_run_max_entry_intents', '') AS decision_dry_run_max_entry_intents,
              COALESCE(payload->>'decision_dry_run_reject_stages', '') AS decision_dry_run_reject_stages,
              COALESCE(payload->>'decision_dry_run_reject_reasons', '') AS decision_dry_run_reject_reasons,
              COALESCE(payload->>'decision_dry_run_strategies', '') AS decision_dry_run_strategies,
              COALESCE(payload->>'decision_dry_run_strategy_count', '') AS decision_dry_run_strategy_count
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
              AND payload->>'check_name' = 'paper_readiness'
              AND payload->>'session_date' = %s
              AND payload->>'proof_start' = %s
            ORDER BY created_at DESC, event_id DESC
            LIMIT 32
            """,
            (
                trading_mode.value,
                strategy_version,
                readiness_target_session.isoformat(),
                proof_start.isoformat(),
            ),
        )
        readiness_audit_rows = cur.fetchall()

        post_close_audit_rows = []
        if post_close_target_session is not None:
            cur.execute(
                """
                SELECT check_name, status, exit_code, created_at
                FROM (
                  SELECT DISTINCT ON (payload->>'check_name')
                    payload->>'check_name' AS check_name,
                    COALESCE(payload->>'status', '') AS status,
                    COALESCE(payload->>'exit_code', '') AS exit_code,
                    created_at
                  FROM audit_events
                  WHERE event_type = 'scheduled_check_completed'
                    AND payload->>'trading_mode' = %s
                    AND payload->>'strategy_version' = %s
                    AND payload->>'check_name' IN (
                      'session_guard',
                      'paper_profit_probe'
                    )
                    AND payload->>'session_date' = %s
                    AND payload->>'proof_start' = %s
                    AND (NOT (payload ? 'strategy') OR payload->>'strategy' = %s)
                    AND (%s = %s OR payload->>'strategies' = %s)
                    AND (
                      payload->>'check_name' <> 'paper_profit_probe'
                      OR payload->>'min_trades' = %s
                    )
                    AND (
                      payload->>'check_name' <> 'paper_profit_probe'
                      OR payload->>'min_pnl' = %s
                    )
                    AND (
                      payload->>'check_name' <> 'session_guard'
                      OR payload->>'min_trades' = %s
                    )
                    AND (
                      payload->>'check_name' <> 'session_guard'
                      OR payload->>'min_pnl' = %s
                    )
                  ORDER BY payload->>'check_name', created_at DESC, event_id DESC
                ) latest
                ORDER BY check_name
                """,
                (
                    trading_mode.value,
                    strategy_version,
                    post_close_target_session.isoformat(),
                    proof_start.isoformat(),
                    strategy_name,
                    proof_strategy_csv,
                    strategy_name,
                    proof_strategy_csv,
                    min_trades_text,
                    min_pnl_text,
                    session_guard_min_trades_text,
                    session_guard_min_pnl_text,
                ),
            )
            post_close_audit_rows = cur.fetchall()

        cur.execute(
            """
            SELECT
              (
                SELECT COUNT(*)::int
                FROM positions
                WHERE trading_mode = %s
                  AND strategy_version = %s
              ) AS open_positions,
              (
                SELECT COUNT(*)::int
                FROM orders
                WHERE trading_mode = %s
                  AND strategy_version = %s
                  AND status IN (
                    'pending_submit',
                    'submitting',
                    'pending_new',
                    'new',
                    'accepted',
                    'accepted_for_bidding',
                    'submitted',
                    'partially_filled',
                    'held',
                    'pending_replace',
                    'pending_cancel',
                    'stopped',
                    'suspended',
                    'done_for_day'
                  )
              ) AS active_orders
              ,
              (
                WITH filled AS (
                  SELECT
                    strategy_name,
                    occ_symbol,
                    COALESCE(filled_quantity, quantity) AS fill_qty,
                    side
                  FROM option_orders
                  WHERE trading_mode = %s
                    AND strategy_version = %s
                    AND status = 'filled'
                ),
                net AS (
                  SELECT
                    strategy_name,
                    occ_symbol,
                    SUM(CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END) AS net_qty
                  FROM filled
                  GROUP BY strategy_name, occ_symbol
                  HAVING SUM(CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END) <> 0
                )
                SELECT COUNT(*)::int FROM net
              ) AS open_option_positions,
              (
                SELECT COUNT(*)::int
                FROM option_orders
                WHERE trading_mode = %s
                  AND strategy_version = %s
                  AND status IN (
                    'pending_submit',
                    'submitting',
                    'pending_new',
                    'new',
                    'accepted',
                    'accepted_for_bidding',
                    'submitted',
                    'partially_filled',
                    'held',
                    'pending_replace',
                    'pending_cancel',
                    'stopped',
                    'suspended',
                    'done_for_day'
                  )
              ) AS active_option_orders
              ,
              (
                SELECT COALESCE(string_agg(DISTINCT symbol, ',' ORDER BY symbol), 'none')
                FROM positions
                WHERE trading_mode = %s
                  AND strategy_version = %s
              ) AS open_position_symbols,
              (
                SELECT COALESCE(string_agg(DISTINCT symbol, ',' ORDER BY symbol), 'none')
                FROM orders
                WHERE trading_mode = %s
                  AND strategy_version = %s
                  AND status IN (
                    'pending_submit',
                    'submitting',
                    'pending_new',
                    'new',
                    'accepted',
                    'accepted_for_bidding',
                    'submitted',
                    'partially_filled',
                    'held',
                    'pending_replace',
                    'pending_cancel',
                    'stopped',
                    'suspended',
                    'done_for_day'
                  )
              ) AS active_order_symbols,
              (
                WITH filled AS (
                  SELECT
                    occ_symbol,
                    COALESCE(filled_quantity, quantity) AS fill_qty,
                    side
                  FROM option_orders
                  WHERE trading_mode = %s
                    AND strategy_version = %s
                    AND status = 'filled'
                ),
                net AS (
                  SELECT
                    occ_symbol,
                    SUM(CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END) AS net_qty
                  FROM filled
                  GROUP BY occ_symbol
                  HAVING SUM(CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END) <> 0
                )
                SELECT COALESCE(string_agg(DISTINCT occ_symbol, ',' ORDER BY occ_symbol), 'none')
                FROM net
              ) AS open_option_symbols,
              (
                SELECT COALESCE(string_agg(DISTINCT occ_symbol, ',' ORDER BY occ_symbol), 'none')
                FROM option_orders
                WHERE trading_mode = %s
                  AND strategy_version = %s
                  AND status IN (
                    'pending_submit',
                    'submitting',
                    'pending_new',
                    'new',
                    'accepted',
                    'accepted_for_bidding',
                    'submitted',
                    'partially_filled',
                    'held',
                    'pending_replace',
                    'pending_cancel',
                    'stopped',
                    'suspended',
                    'done_for_day'
                  )
              ) AS active_option_order_symbols
            """,
            (
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
            ),
        )
        exposure_row = cur.fetchone()
        local_open_positions = int(exposure_row[0] or 0) if exposure_row else 0
        local_active_orders = int(exposure_row[1] or 0) if exposure_row else 0
        local_open_option_positions = int(exposure_row[2] or 0) if exposure_row else 0
        local_active_option_orders = int(exposure_row[3] or 0) if exposure_row else 0
        local_open_position_symbols = exposure_row[4] if exposure_row else "none"
        local_active_order_symbols = exposure_row[5] if exposure_row else "none"
        local_open_option_symbols = exposure_row[6] if exposure_row else "none"
        local_active_option_order_symbols = exposure_row[7] if exposure_row else "none"

        cur.execute(
            """
            SELECT
              (
                SELECT COUNT(*)::int
                FROM orders
                WHERE trading_mode = %s
                  AND strategy_version = %s
                  AND intent_type = 'entry'
                  AND side = 'buy'
                  AND status IN (
                    'pending_submit',
                    'submitting',
                    'pending_new',
                    'new',
                    'accepted',
                    'accepted_for_bidding',
                    'submitted',
                    'partially_filled',
                    'held',
                    'pending_replace',
                    'pending_cancel',
                    'stopped',
                    'suspended',
                    'done_for_day'
                  )
              ) AS active_entry_orders,
              (
                SELECT COUNT(*)::int
                FROM orders
                WHERE trading_mode = %s
                  AND strategy_version = %s
                  AND intent_type = 'stop'
                  AND side = 'sell'
                  AND status IN (
                    'pending_submit',
                    'submitting',
                    'pending_new',
                    'new',
                    'accepted',
                    'accepted_for_bidding',
                    'submitted',
                    'partially_filled',
                    'held',
                    'pending_replace',
                    'pending_cancel',
                    'stopped',
                    'suspended',
                    'done_for_day'
                  )
              ) AS active_stop_orders
            """,
            (
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
            ),
        )
        risk_lock_order_row = cur.fetchone()
        local_active_entry_orders = (
            int(risk_lock_order_row[0] or 0) if risk_lock_order_row else 0
        )
        local_active_stop_orders = (
            int(risk_lock_order_row[1] or 0) if risk_lock_order_row else 0
        )

        cur.execute(
            """
            SELECT status, kill_switch_enabled, COALESCE(status_reason, '')
            FROM trading_status
            WHERE trading_mode = %s
              AND strategy_version = %s
            """,
            (trading_mode.value, strategy_version),
        )
        trading_status_row = cur.fetchone()
        trading_status_value = trading_status_row[0] if trading_status_row else ""
        trading_status_kill_switch_enabled = (
            bool(trading_status_row[1]) if trading_status_row else False
        )
        trading_status_reason = trading_status_row[2] if trading_status_row else ""

        decision_evaluated = 0
        decision_signal_fired = 0
        decision_accepted = 0
        decision_capacity_rejected = 0
        decision_entry_quality_rejected = 0
        decision_vwap_rejected = 0
        decision_sizing_rejected = 0
        entry_order_count = 0
        entry_order_filled_count = 0
        entry_order_canceled_count = 0
        entry_order_expired_count = 0
        entry_order_rejected_count = 0
        entry_order_active_count = 0
        entry_order_maintenance_drained_count = 0
        entry_order_short_window_drained_count = 0
        entry_order_filled_symbols = "none"
        entry_order_expired_symbols = "none"
        entry_order_expired_reasons = "none"
        entry_order_expired_signal_price_posture = "none"
        entry_order_expired_next_bar_fill_causes = "none"
        entry_order_dispatch_delay_summary = "none"
        posture_entry_order_dispatch_delay_summary = "none"
        posture_entry_order_count = 0
        posture_entry_order_filled_count = 0
        posture_entry_quality_would_reject_count = 0
        posture_entry_order_filled_symbols = "none"
        current_session_decision_evaluated = 0
        current_session_decision_signal_fired = 0
        current_session_decision_accepted = 0
        current_session_decision_capacity_rejected = 0
        current_session_entry_order_count = 0
        current_session_entry_order_filled_count = 0
        current_session_entry_order_canceled_count = 0
        current_session_entry_order_expired_count = 0
        current_session_entry_order_rejected_count = 0
        current_session_entry_order_active_count = 0
        current_session_entry_order_maintenance_drained_count = 0
        current_session_entry_order_short_window_drained_count = 0
        current_session_entry_order_settled_count = 0
        current_session_entry_order_settled_filled_count = 0
        current_session_entry_order_filled_symbols = "none"
        current_session_entry_order_expired_symbols = "none"
        current_session_entry_order_expired_reasons = "none"
        current_session_entry_order_expired_signal_price_posture = "none"
        current_session_entry_order_expired_next_bar_fill_causes = "none"
        current_session_entry_order_dispatch_delay_summary = "none"
        current_session_entry_order_active_symbols = "none"
        current_session_entry_order_maintenance_drained_symbols = "none"
        current_session_entry_order_short_window_drained_symbols = "none"
        current_session_entry_order_short_window_count = 0
        current_session_entry_order_min_remaining_active_minutes = None
        current_session_entry_order_short_window_symbols = "none"
        post_supervisor_execution_since = None
        post_supervisor_decision_evaluated = 0
        post_supervisor_decision_signal_fired = 0
        post_supervisor_decision_accepted = 0
        post_supervisor_decision_capacity_rejected = 0
        post_supervisor_entry_order_count = 0
        post_supervisor_entry_order_filled_count = 0
        post_supervisor_entry_order_expired_count = 0
        post_supervisor_entry_order_active_count = 0
        post_supervisor_entry_order_maintenance_drained_count = 0
        post_supervisor_entry_order_short_window_drained_count = 0
        post_supervisor_entry_order_settled_count = 0
        post_supervisor_entry_order_settled_filled_count = 0
        post_supervisor_entry_order_filled_symbols = "none"
        post_supervisor_entry_order_expired_symbols = "none"
        post_supervisor_entry_order_expired_reasons = "none"
        post_supervisor_entry_order_expired_signal_price_posture = "none"
        post_supervisor_entry_order_expired_next_bar_fill_causes = "none"
        post_supervisor_entry_order_dispatch_delay_summary = "none"
        post_supervisor_entry_order_active_symbols = "none"
        post_supervisor_entry_order_short_window_count = 0
        post_supervisor_entry_order_min_remaining_active_minutes = None
        post_supervisor_entry_order_short_window_symbols = "none"
        scenario_bar_cache: dict[str, list[Bar] | None] = {}
        if proof_end >= proof_start:
            cur.execute(
                """
                SELECT
                  COALESCE(SUM(w), 0)::int AS evaluated,
                  COALESCE(SUM(w) FILTER (
                    WHERE symbol <> %s
                      AND decision NOT IN (
                      'skipped_existing_position',
                      'skipped_already_traded',
                      'skipped_no_signal'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                  ), 0)::int AS signal_fired,
                  COALESCE(SUM(w) FILTER (WHERE decision = 'accepted'), 0)::int AS accepted,
                  COALESCE(SUM(w) FILTER (
                    WHERE reject_stage = 'capacity'
                      AND symbol <> %s
                  ), 0)::int AS capacity_rejected,
                  COALESCE(SUM(w) FILTER (WHERE reject_stage = 'entry_quality'), 0)::int AS entry_quality_rejected,
                  COALESCE(SUM(w) FILTER (WHERE reject_stage = 'vwap_filter'), 0)::int AS vwap_rejected,
                  COALESCE(SUM(w) FILTER (WHERE reject_stage = 'sizing'), 0)::int AS sizing_rejected
                FROM (
                  SELECT
                    symbol,
                    decision,
                    reject_stage,
                    COALESCE((filter_results->>'blocked_symbol_count')::int, 1) AS w
                  FROM decision_log
                  WHERE trading_mode = %s
                    AND strategy_version = %s
                    AND strategy_name = ANY(%s)
                    AND DATE(cycle_at AT TIME ZONE %s) >= %s
                    AND DATE(cycle_at AT TIME ZONE %s) <= %s
                ) weighted
                """,
                (
                    CAPACITY_SENTINEL_SYMBOL,
                    CAPACITY_SENTINEL_SYMBOL,
                    trading_mode.value,
                    strategy_version,
                    proof_strategy_names,
                    market_timezone,
                    proof_start,
                    market_timezone,
                    proof_end,
                ),
            )
            decision_quality_row = cur.fetchone()
            if decision_quality_row:
                decision_evaluated = int(decision_quality_row[0] or 0)
                decision_signal_fired = int(decision_quality_row[1] or 0)
                decision_accepted = int(decision_quality_row[2] or 0)
                decision_capacity_rejected = int(decision_quality_row[3] or 0)
                decision_entry_quality_rejected = int(decision_quality_row[4] or 0)
                decision_vwap_rejected = int(decision_quality_row[5] or 0)
                decision_sizing_rejected = int(decision_quality_row[6] or 0)

            cur.execute(
                """
                WITH entry_orders AS (
                  SELECT
                    o.*,
                    COALESCE(
                      NULLIF(o.reason, ''),
                      (
                        SELECT CASE
                          WHEN a.event_type = 'order_expired_stale_signal'
                            THEN 'stale_signal'
                          WHEN COALESCE(a.payload->>'reason', '') = 'short active dispatch window'
                            THEN 'short_active_window'
                          WHEN COALESCE(a.payload->>'reason', '') LIKE 'deploy maintenance%%'
                            THEN 'deploy_maintenance'
                          ELSE 'next_bar_expired'
                        END
                        FROM audit_events a
                        WHERE a.event_type IN (
                          'entry_order_expired_next_bar',
                          'order_expired_stale_signal'
                        )
                          AND a.payload->>'client_order_id' = o.client_order_id
                        ORDER BY a.created_at DESC, a.event_id DESC
                        LIMIT 1
                      ),
                      CASE WHEN o.status = 'expired' THEN 'expired' ELSE 'none' END
                    ) AS expiry_reason,
                    EXISTS (
                      SELECT 1
                      FROM audit_events a
                      WHERE a.event_type = 'entry_order_expired_next_bar'
                        AND a.payload->>'client_order_id' = o.client_order_id
                        AND COALESCE(a.payload->>'reason', '') LIKE 'deploy maintenance%%'
                    ) AS maintenance_drained,
                    EXISTS (
                      SELECT 1
                      FROM audit_events a
                      WHERE a.event_type = 'entry_order_expired_next_bar'
                        AND a.payload->>'client_order_id' = o.client_order_id
                        AND COALESCE(a.payload->>'reason', '') = 'short active dispatch window'
                    ) AS short_window_drained,
                    EXISTS (
                      SELECT 1
                      FROM audit_events a
                      WHERE a.event_type = 'entry_order_expired_next_bar'
                        AND a.payload->>'client_order_id' = o.client_order_id
                        AND COALESCE(a.payload->>'reason', '') NOT LIKE 'deploy maintenance%%'
                        AND COALESCE(a.payload->>'reason', '') <> 'short active dispatch window'
                    ) AS strategy_expired,
                    COALESCE(
                      entry_context.signal_price_posture,
                      'missing_context'
                    ) AS signal_price_posture
                  FROM orders o
                  LEFT JOIN LATERAL (
                    SELECT
                      CASE
                        WHEN d.signal_bar_close IS NULL
                          OR d.stop_price IS NULL
                          OR d.limit_price IS NULL
                          OR d.stop_price <= 0
                          OR d.limit_price <= 0
                          THEN 'missing_context'
                        WHEN d.signal_bar_close > d.limit_price
                          THEN 'above_limit'
                        WHEN d.signal_bar_close < d.stop_price
                          THEN 'below_stop'
                        ELSE 'within_stop_limit'
                      END AS signal_price_posture
                    FROM decision_log d
                    WHERE d.symbol = o.symbol
                      AND d.trading_mode = o.trading_mode
                      AND d.strategy_version = o.strategy_version
                      AND d.strategy_name IS NOT DISTINCT FROM o.strategy_name
                      AND d.cycle_at = o.created_at
                      AND d.decision = 'accepted'
                    ORDER BY d.id DESC
                    LIMIT 1
                  ) entry_context ON true
                  WHERE o.trading_mode = %s
                    AND o.strategy_version = %s
                    AND o.strategy_name = ANY(%s)
                    AND o.intent_type = 'entry'
                    AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) >= %s
                    AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) <= %s
                )
                SELECT
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained AND NOT short_window_drained
                  )::int AS entry_orders,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (status = 'filled' OR COALESCE(filled_quantity, 0) > 0)
                  )::int AS filled_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND NOT strategy_expired
                      AND status = 'canceled'
                  )::int AS canceled_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                  )::int AS expired_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND status IN ('rejected', 'error')
                  )::int AS rejected_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND status IN (
                      'pending_submit',
                      'submitting',
                      'pending_new',
                      'new',
                      'accepted',
                      'accepted_for_bidding',
                      'submitted',
                      'partially_filled',
                      'held',
                      'pending_replace',
                      'pending_cancel',
                      'stopped',
                      'suspended',
                      'done_for_day'
                    )
                  )::int AS active_entries,
                  COUNT(*) FILTER (WHERE maintenance_drained)::int
                    AS maintenance_drained_entries,
                  COUNT(*) FILTER (WHERE short_window_drained)::int
                    AS short_window_drained_entries,
                  COALESCE(
                    string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                      WHERE NOT maintenance_drained
                        AND NOT short_window_drained
                        AND (status = 'filled' OR COALESCE(filled_quantity, 0) > 0)
                    ),
                    'none'
                  ) AS filled_symbols,
                  COALESCE(
                    string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                      WHERE NOT maintenance_drained
                        AND NOT short_window_drained
                        AND (strategy_expired OR status = 'expired')
                    ),
                    'none'
                  ) AS expired_symbols,
                  COALESCE(
                    string_agg(DISTINCT expiry_reason, ',' ORDER BY expiry_reason) FILTER (
                      WHERE NOT maintenance_drained
                        AND NOT short_window_drained
                        AND (strategy_expired OR status = 'expired')
                    ),
                    'none'
                  ) AS expired_reasons,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                      AND signal_price_posture = 'above_limit'
                  )::int AS expired_signal_above_limit_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                      AND signal_price_posture = 'below_stop'
                  )::int AS expired_signal_below_stop_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                      AND signal_price_posture = 'within_stop_limit'
                  )::int AS expired_signal_within_stop_limit_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                      AND signal_price_posture = 'missing_context'
                  )::int AS expired_signal_missing_context_entries
                FROM entry_orders
                """,
                (
                    trading_mode.value,
                    strategy_version,
                    proof_strategy_names,
                    market_timezone,
                    proof_start,
                    market_timezone,
                    proof_end,
                ),
            )
            execution_quality_row = cur.fetchone()
            if execution_quality_row:
                entry_order_count = int(execution_quality_row[0] or 0)
                entry_order_filled_count = int(execution_quality_row[1] or 0)
                entry_order_canceled_count = int(execution_quality_row[2] or 0)
                entry_order_expired_count = int(execution_quality_row[3] or 0)
                entry_order_rejected_count = int(execution_quality_row[4] or 0)
                entry_order_active_count = int(execution_quality_row[5] or 0)
                entry_order_maintenance_drained_count = int(
                    execution_quality_row[6] or 0
                )
                entry_order_short_window_drained_count = int(
                    execution_quality_row[7] or 0
                )
                entry_order_filled_symbols = execution_quality_row[8] or "none"
                entry_order_expired_symbols = execution_quality_row[9] or "none"
                entry_order_expired_reasons = execution_quality_row[10] or "none"
                entry_order_expired_signal_price_posture = (
                    format_expired_signal_price_posture(
                        above_limit=int(execution_quality_row[11] or 0),
                        below_stop=int(execution_quality_row[12] or 0),
                        within_stop_limit=int(execution_quality_row[13] or 0),
                        missing_context=int(execution_quality_row[14] or 0),
                    )
                )
                entry_order_expired_next_bar_fill_causes = (
                    load_expired_next_bar_fill_cause_summary(
                        cur,
                        trading_mode=trading_mode.value,
                        strategy_version=strategy_version,
                        strategy_names=proof_strategy_names,
                        market_timezone=market_timezone,
                        proof_start=proof_start,
                        proof_end=proof_end,
                        scenario_dir=scenario_dir,
                        settings=settings,
                        scenario_cache=scenario_bar_cache,
                    )
                )
                entry_order_dispatch_delay_summary = (
                    load_entry_dispatch_delay_summary(
                        cur,
                        trading_mode=trading_mode.value,
                        strategy_version=strategy_version,
                        strategy_names=proof_strategy_names,
                        market_timezone=market_timezone,
                        proof_start=proof_start,
                        proof_end=proof_end,
                        settings=settings,
                    )
                )
                posture_entry_order_dispatch_delay_summary = (
                    load_entry_dispatch_delay_summary(
                        cur,
                        trading_mode=trading_mode.value,
                        strategy_version=strategy_version,
                        strategy_names=proof_strategy_names,
                        market_timezone=market_timezone,
                        proof_start=proof_start,
                        proof_end=proof_end,
                        settings=settings,
                        current_posture_only=True,
                    )
                )

            cur.execute(
                """
                WITH paired AS (
                  SELECT
                    o.symbol,
                    o.status,
                    o.filled_quantity,
                    EXISTS (
                      SELECT 1
                      FROM audit_events a
                      WHERE a.event_type = 'entry_order_expired_next_bar'
                        AND a.payload->>'client_order_id' = o.client_order_id
                        AND COALESCE(a.payload->>'reason', '') LIKE 'deploy maintenance%%'
                    ) AS maintenance_drained,
                    (d.signal_bar_close / NULLIF(d.entry_level, 0) - 1)
                      AS close_to_entry_pct,
                    (
                      d.stop_price IS NOT NULL
                      AND d.initial_stop_price IS NOT NULL
                      AND d.limit_price IS NOT NULL
                      AND d.limit_price > 0
                      AND d.signal_bar_close > d.limit_price
                    ) AS close_above_limit_price
                  FROM decision_log d
                  JOIN orders o
                    ON o.symbol = d.symbol
                   AND o.trading_mode = d.trading_mode
                   AND o.strategy_version = d.strategy_version
                   AND o.strategy_name IS NOT DISTINCT FROM d.strategy_name
                   AND o.intent_type = 'entry'
                   AND o.created_at = d.cycle_at
                  WHERE d.trading_mode = %s
                    AND d.strategy_version = %s
                    AND d.strategy_name = ANY(%s)
                    AND d.decision = 'accepted'
                    AND d.entry_level IS NOT NULL
                    AND d.entry_level > 0
                    AND d.signal_bar_close IS NOT NULL
                    AND DATE(d.cycle_at AT TIME ZONE %s) >= %s
                    AND DATE(d.cycle_at AT TIME ZONE %s) <= %s
                )
                SELECT
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND close_to_entry_pct >= %s
                      AND close_to_entry_pct <= %s
                      AND NOT close_above_limit_price
                  )::int AS eligible_orders,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND close_to_entry_pct >= %s
                      AND close_to_entry_pct <= %s
                      AND NOT close_above_limit_price
                      AND (status = 'filled' OR COALESCE(filled_quantity, 0) > 0)
                  )::int AS eligible_filled,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND (
                        close_to_entry_pct < %s
                        OR close_to_entry_pct > %s
                        OR close_above_limit_price
                      )
                  )::int AS would_reject_now,
                  COALESCE(
                    string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                      WHERE NOT maintenance_drained
                        AND close_to_entry_pct >= %s
                        AND close_to_entry_pct <= %s
                        AND NOT close_above_limit_price
                        AND (status = 'filled' OR COALESCE(filled_quantity, 0) > 0)
                    ),
                    'none'
                  ) AS eligible_filled_symbols
                FROM paired
                """,
                (
                    trading_mode.value,
                    strategy_version,
                    proof_strategy_names,
                    market_timezone,
                    proof_start,
                    market_timezone,
                    proof_end,
                    settings.entry_min_close_to_entry_pct,
                    settings.entry_max_close_to_entry_pct,
                    settings.entry_min_close_to_entry_pct,
                    settings.entry_max_close_to_entry_pct,
                    settings.entry_min_close_to_entry_pct,
                    settings.entry_max_close_to_entry_pct,
                    settings.entry_min_close_to_entry_pct,
                    settings.entry_max_close_to_entry_pct,
                ),
            )
            posture_execution_row = cur.fetchone()
            if posture_execution_row:
                posture_entry_order_count = int(posture_execution_row[0] or 0)
                posture_entry_order_filled_count = int(posture_execution_row[1] or 0)
                posture_entry_quality_would_reject_count = int(
                    posture_execution_row[2] or 0
                )
                posture_entry_order_filled_symbols = (
                    posture_execution_row[3] or "none"
                )

        if current_market_date >= proof_start:
            cur.execute(
                """
                SELECT
                  COALESCE(SUM(w), 0)::int AS evaluated,
                  COALESCE(SUM(w) FILTER (
                    WHERE symbol <> %s
                      AND decision NOT IN (
                      'skipped_existing_position',
                      'skipped_already_traded',
                      'skipped_no_signal'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                  ), 0)::int AS signal_fired,
                  COALESCE(SUM(w) FILTER (WHERE decision = 'accepted'), 0)::int AS accepted,
                  COALESCE(SUM(w) FILTER (
                    WHERE reject_stage = 'capacity'
                      AND symbol <> %s
                  ), 0)::int AS capacity_rejected
                FROM (
                  SELECT
                    symbol,
                    decision,
                    reject_stage,
                    COALESCE((filter_results->>'blocked_symbol_count')::int, 1) AS w
                  FROM decision_log
                  WHERE trading_mode = %s
                    AND strategy_version = %s
                    AND strategy_name = ANY(%s)
                    AND DATE(cycle_at AT TIME ZONE %s) = %s
                ) weighted
                """,
                (
                    CAPACITY_SENTINEL_SYMBOL,
                    CAPACITY_SENTINEL_SYMBOL,
                    trading_mode.value,
                    strategy_version,
                    proof_strategy_names,
                    market_timezone,
                    current_market_date,
                ),
            )
            current_session_decision_row = cur.fetchone()
            if current_session_decision_row:
                current_session_decision_evaluated = int(
                    current_session_decision_row[0] or 0
                )
                current_session_decision_signal_fired = int(
                    current_session_decision_row[1] or 0
                )
                current_session_decision_accepted = int(
                    current_session_decision_row[2] or 0
                )
                current_session_decision_capacity_rejected = int(
                    current_session_decision_row[3] or 0
                )

            cur.execute(
                """
                WITH entry_orders AS (
                  SELECT
                    o.*,
                    COALESCE(
                      NULLIF(o.reason, ''),
                      (
                        SELECT CASE
                          WHEN a.event_type = 'order_expired_stale_signal'
                            THEN 'stale_signal'
                          WHEN COALESCE(a.payload->>'reason', '') = 'short active dispatch window'
                            THEN 'short_active_window'
                          WHEN COALESCE(a.payload->>'reason', '') LIKE 'deploy maintenance%%'
                            THEN 'deploy_maintenance'
                          ELSE 'next_bar_expired'
                        END
                        FROM audit_events a
                        WHERE a.event_type IN (
                          'entry_order_expired_next_bar',
                          'order_expired_stale_signal'
                        )
                          AND a.payload->>'client_order_id' = o.client_order_id
                        ORDER BY a.created_at DESC, a.event_id DESC
                        LIMIT 1
                      ),
                      CASE WHEN o.status = 'expired' THEN 'expired' ELSE 'none' END
                    ) AS expiry_reason,
                    EXISTS (
                      SELECT 1
                      FROM audit_events a
                      WHERE a.event_type = 'entry_order_expired_next_bar'
                        AND a.payload->>'client_order_id' = o.client_order_id
                        AND COALESCE(a.payload->>'reason', '') LIKE 'deploy maintenance%%'
                    ) AS maintenance_drained,
                    EXISTS (
                      SELECT 1
                      FROM audit_events a
                      WHERE a.event_type = 'entry_order_expired_next_bar'
                        AND a.payload->>'client_order_id' = o.client_order_id
                        AND COALESCE(a.payload->>'reason', '') = 'short active dispatch window'
                    ) AS short_window_drained,
                    EXISTS (
                      SELECT 1
                      FROM audit_events a
                      WHERE a.event_type = 'entry_order_expired_next_bar'
                        AND a.payload->>'client_order_id' = o.client_order_id
                        AND COALESCE(a.payload->>'reason', '') NOT LIKE 'deploy maintenance%%'
                        AND COALESCE(a.payload->>'reason', '') <> 'short active dispatch window'
                    ) AS strategy_expired,
                    COALESCE(
                      entry_context.signal_price_posture,
                      'missing_context'
                    ) AS signal_price_posture
                  FROM orders o
                  LEFT JOIN LATERAL (
                    SELECT
                      CASE
                        WHEN d.signal_bar_close IS NULL
                          OR d.stop_price IS NULL
                          OR d.limit_price IS NULL
                          OR d.stop_price <= 0
                          OR d.limit_price <= 0
                          THEN 'missing_context'
                        WHEN d.signal_bar_close > d.limit_price
                          THEN 'above_limit'
                        WHEN d.signal_bar_close < d.stop_price
                          THEN 'below_stop'
                        ELSE 'within_stop_limit'
                      END AS signal_price_posture
                    FROM decision_log d
                    WHERE d.symbol = o.symbol
                      AND d.trading_mode = o.trading_mode
                      AND d.strategy_version = o.strategy_version
                      AND d.strategy_name IS NOT DISTINCT FROM o.strategy_name
                      AND d.cycle_at = o.created_at
                      AND d.decision = 'accepted'
                    ORDER BY d.id DESC
                    LIMIT 1
                  ) entry_context ON true
                  WHERE o.trading_mode = %s
                    AND o.strategy_version = %s
                    AND o.strategy_name = ANY(%s)
                    AND o.intent_type = 'entry'
                    AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) = %s
                ),
                entry_order_windows AS (
                  SELECT
                    entry_orders.*,
                    CASE
                      WHEN signal_timestamp IS NULL THEN NULL
                      ELSE EXTRACT(EPOCH FROM (
                        (signal_timestamp + (%s * interval '1 minute')) - created_at
                      )) / 60.0
                    END AS remaining_active_minutes
                  FROM entry_orders
                )
                SELECT
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained AND NOT short_window_drained
                  )::int AS entry_orders,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (status = 'filled' OR COALESCE(filled_quantity, 0) > 0)
                  )::int AS filled_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND NOT strategy_expired
                      AND status = 'canceled'
                  )::int AS canceled_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                  )::int AS expired_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND status IN ('rejected', 'error')
                  )::int AS rejected_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND status = ANY(%s)
                  )::int AS active_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND status <> ALL(%s)
                  )::int AS settled_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND status <> ALL(%s)
                      AND (
                        status = 'filled'
                        OR COALESCE(filled_quantity, 0) > 0
                      )
                  )::int AS settled_filled_entries,
                  COUNT(*) FILTER (WHERE maintenance_drained)::int
                    AS maintenance_drained_entries,
                  COUNT(*) FILTER (WHERE short_window_drained)::int
                    AS short_window_drained_entries,
                  COALESCE(
                    string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                      WHERE NOT maintenance_drained
                        AND NOT short_window_drained
                        AND (status = 'filled' OR COALESCE(filled_quantity, 0) > 0)
                    ),
                    'none'
                  ) AS filled_symbols,
                  COALESCE(
                    string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                      WHERE NOT maintenance_drained
                        AND NOT short_window_drained
                        AND (strategy_expired OR status = 'expired')
                    ),
                    'none'
                  ) AS expired_symbols,
                  COALESCE(
                    string_agg(DISTINCT expiry_reason, ',' ORDER BY expiry_reason) FILTER (
                      WHERE NOT maintenance_drained
                        AND NOT short_window_drained
                        AND (strategy_expired OR status = 'expired')
                    ),
                    'none'
                  ) AS expired_reasons,
                  COALESCE(
                    string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                      WHERE NOT maintenance_drained
                        AND NOT short_window_drained
                        AND status = ANY(%s)
                    ),
                    'none'
                  ) AS active_symbols,
                  COALESCE(
                    string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                      WHERE maintenance_drained
                    ),
                    'none'
                  ) AS maintenance_drained_symbols,
                  COALESCE(
                    string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                      WHERE short_window_drained
                    ),
                    'none'
                  ) AS short_window_drained_symbols,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND remaining_active_minutes IS NOT NULL
                      AND remaining_active_minutes < %s
                  )::int AS short_window_entries,
                  ROUND((MIN(remaining_active_minutes) FILTER (
                    WHERE NOT maintenance_drained
                      AND remaining_active_minutes IS NOT NULL
                      AND remaining_active_minutes < %s
                  ))::numeric, 1) AS min_remaining_active_minutes,
                  COALESCE(
                    string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                      WHERE NOT maintenance_drained
                        AND remaining_active_minutes IS NOT NULL
                        AND remaining_active_minutes < %s
                    ),
                    'none'
                  ) AS short_window_symbols,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                      AND signal_price_posture = 'above_limit'
                  )::int AS expired_signal_above_limit_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                      AND signal_price_posture = 'below_stop'
                  )::int AS expired_signal_below_stop_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                      AND signal_price_posture = 'within_stop_limit'
                  )::int AS expired_signal_within_stop_limit_entries,
                  COUNT(*) FILTER (
                    WHERE NOT maintenance_drained
                      AND NOT short_window_drained
                      AND (strategy_expired OR status = 'expired')
                      AND signal_price_posture = 'missing_context'
                  )::int AS expired_signal_missing_context_entries
                FROM entry_order_windows
                """,
                (
                    trading_mode.value,
                    strategy_version,
                    proof_strategy_names,
                    market_timezone,
                    current_market_date,
                    settings.entry_timeframe_minutes
                    * (settings.entry_order_active_bars + 1),
                    list(ACTIVE_ORDER_STATUSES),
                    list(ACTIVE_ORDER_STATUSES),
                    list(ACTIVE_ORDER_STATUSES),
                    list(ACTIVE_ORDER_STATUSES),
                    settings.entry_timeframe_minutes
                    * settings.entry_order_active_bars
                    * 0.5,
                    settings.entry_timeframe_minutes
                    * settings.entry_order_active_bars
                    * 0.5,
                    settings.entry_timeframe_minutes
                    * settings.entry_order_active_bars
                    * 0.5,
                ),
            )
            current_session_execution_row = cur.fetchone()
            if current_session_execution_row:
                current_session_entry_order_count = int(
                    current_session_execution_row[0] or 0
                )
                current_session_entry_order_filled_count = int(
                    current_session_execution_row[1] or 0
                )
                current_session_entry_order_canceled_count = int(
                    current_session_execution_row[2] or 0
                )
                current_session_entry_order_expired_count = int(
                    current_session_execution_row[3] or 0
                )
                current_session_entry_order_rejected_count = int(
                    current_session_execution_row[4] or 0
                )
                current_session_entry_order_active_count = int(
                    current_session_execution_row[5] or 0
                )
                current_session_entry_order_settled_count = int(
                    current_session_execution_row[6] or 0
                )
                current_session_entry_order_settled_filled_count = int(
                    current_session_execution_row[7] or 0
                )
                current_session_entry_order_maintenance_drained_count = int(
                    current_session_execution_row[8] or 0
                )
                current_session_entry_order_short_window_drained_count = int(
                    current_session_execution_row[9] or 0
                )
                current_session_entry_order_filled_symbols = (
                    current_session_execution_row[10] or "none"
                )
                current_session_entry_order_expired_symbols = (
                    current_session_execution_row[11] or "none"
                )
                current_session_entry_order_expired_reasons = (
                    current_session_execution_row[12] or "none"
                )
                current_session_entry_order_active_symbols = (
                    current_session_execution_row[13] or "none"
                )
                current_session_entry_order_maintenance_drained_symbols = (
                    current_session_execution_row[14] or "none"
                )
                current_session_entry_order_short_window_drained_symbols = (
                    current_session_execution_row[15] or "none"
                )
                current_session_entry_order_short_window_count = int(
                    current_session_execution_row[16] or 0
                )
                if current_session_execution_row[17] is not None:
                    current_session_entry_order_min_remaining_active_minutes = float(
                        current_session_execution_row[17]
                    )
                current_session_entry_order_short_window_symbols = (
                    current_session_execution_row[18] or "none"
                )
                current_session_entry_order_expired_signal_price_posture = (
                    format_expired_signal_price_posture(
                        above_limit=int(current_session_execution_row[19] or 0),
                        below_stop=int(current_session_execution_row[20] or 0),
                        within_stop_limit=int(
                            current_session_execution_row[21] or 0
                        ),
                        missing_context=int(current_session_execution_row[22] or 0),
                    )
                )
                current_session_entry_order_expired_next_bar_fill_causes = (
                    load_expired_next_bar_fill_cause_summary(
                        cur,
                        trading_mode=trading_mode.value,
                        strategy_version=strategy_version,
                        strategy_names=proof_strategy_names,
                        market_timezone=market_timezone,
                        session_date=current_market_date,
                        scenario_dir=scenario_dir,
                        settings=settings,
                        scenario_cache=scenario_bar_cache,
                    )
                )
                current_session_entry_order_dispatch_delay_summary = (
                    load_entry_dispatch_delay_summary(
                        cur,
                        trading_mode=trading_mode.value,
                        strategy_version=strategy_version,
                        strategy_names=proof_strategy_names,
                        market_timezone=market_timezone,
                        session_date=current_market_date,
                        settings=settings,
                    )
                )

            if (
                latest_supervisor_started_at is not None
                and latest_supervisor_started_at.astimezone(
                    settings.market_timezone
                ).date() == current_market_date
            ):
                post_supervisor_execution_since = latest_supervisor_started_at
                cur.execute(
                    """
                    SELECT
                      COALESCE(SUM(w), 0)::int AS evaluated,
                      COALESCE(SUM(w) FILTER (
                        WHERE symbol <> %s
                          AND decision NOT IN (
                          'skipped_existing_position',
                          'skipped_already_traded',
                          'skipped_no_signal'
                        )
                          AND reject_stage IS DISTINCT FROM 'pre_filter'
                          AND reject_stage IS DISTINCT FROM 'stale_data'
                      ), 0)::int AS signal_fired,
                      COALESCE(SUM(w) FILTER (WHERE decision = 'accepted'), 0)::int AS accepted,
                      COALESCE(SUM(w) FILTER (
                        WHERE reject_stage = 'capacity'
                          AND symbol <> %s
                      ), 0)::int AS capacity_rejected
                    FROM (
                      SELECT
                        symbol,
                        decision,
                        reject_stage,
                        COALESCE((filter_results->>'blocked_symbol_count')::int, 1) AS w
                      FROM decision_log
                      WHERE trading_mode = %s
                        AND strategy_version = %s
                        AND strategy_name = ANY(%s)
                        AND DATE(cycle_at AT TIME ZONE %s) = %s
                        AND cycle_at >= %s
                    ) weighted
                    """,
                    (
                        CAPACITY_SENTINEL_SYMBOL,
                        CAPACITY_SENTINEL_SYMBOL,
                        trading_mode.value,
                        strategy_version,
                        proof_strategy_names,
                        market_timezone,
                        current_market_date,
                        post_supervisor_execution_since,
                    ),
                )
                post_supervisor_decision_row = cur.fetchone()
                if post_supervisor_decision_row:
                    post_supervisor_decision_evaluated = int(
                        post_supervisor_decision_row[0] or 0
                    )
                    post_supervisor_decision_signal_fired = int(
                        post_supervisor_decision_row[1] or 0
                    )
                    post_supervisor_decision_accepted = int(
                        post_supervisor_decision_row[2] or 0
                    )
                    post_supervisor_decision_capacity_rejected = int(
                        post_supervisor_decision_row[3] or 0
                    )

                cur.execute(
                    """
                    WITH entry_orders AS (
                      SELECT
                        o.*,
                        COALESCE(
                          NULLIF(o.reason, ''),
                          (
                            SELECT CASE
                              WHEN a.event_type = 'order_expired_stale_signal'
                                THEN 'stale_signal'
                              WHEN COALESCE(a.payload->>'reason', '') = 'short active dispatch window'
                                THEN 'short_active_window'
                              WHEN COALESCE(a.payload->>'reason', '') LIKE 'deploy maintenance%%'
                                THEN 'deploy_maintenance'
                              ELSE 'next_bar_expired'
                            END
                            FROM audit_events a
                            WHERE a.event_type IN (
                              'entry_order_expired_next_bar',
                              'order_expired_stale_signal'
                            )
                              AND a.payload->>'client_order_id' = o.client_order_id
                            ORDER BY a.created_at DESC, a.event_id DESC
                            LIMIT 1
                          ),
                          CASE WHEN o.status = 'expired' THEN 'expired' ELSE 'none' END
                        ) AS expiry_reason,
                        EXISTS (
                          SELECT 1
                          FROM audit_events a
                          WHERE a.event_type = 'entry_order_expired_next_bar'
                            AND a.payload->>'client_order_id' = o.client_order_id
                            AND COALESCE(a.payload->>'reason', '') LIKE 'deploy maintenance%%'
                        ) AS maintenance_drained,
                        EXISTS (
                          SELECT 1
                          FROM audit_events a
                          WHERE a.event_type = 'entry_order_expired_next_bar'
                            AND a.payload->>'client_order_id' = o.client_order_id
                            AND COALESCE(a.payload->>'reason', '') = 'short active dispatch window'
                        ) AS short_window_drained,
                        EXISTS (
                          SELECT 1
                          FROM audit_events a
                          WHERE a.event_type = 'entry_order_expired_next_bar'
                            AND a.payload->>'client_order_id' = o.client_order_id
                            AND COALESCE(a.payload->>'reason', '') NOT LIKE 'deploy maintenance%%'
                            AND COALESCE(a.payload->>'reason', '') <> 'short active dispatch window'
                        ) AS strategy_expired,
                        COALESCE(
                          entry_context.signal_price_posture,
                          'missing_context'
                        ) AS signal_price_posture
                      FROM orders o
                      LEFT JOIN LATERAL (
                        SELECT
                          CASE
                            WHEN d.signal_bar_close IS NULL
                              OR d.stop_price IS NULL
                              OR d.limit_price IS NULL
                              OR d.stop_price <= 0
                              OR d.limit_price <= 0
                              THEN 'missing_context'
                            WHEN d.signal_bar_close > d.limit_price
                              THEN 'above_limit'
                            WHEN d.signal_bar_close < d.stop_price
                              THEN 'below_stop'
                            ELSE 'within_stop_limit'
                          END AS signal_price_posture
                        FROM decision_log d
                        WHERE d.symbol = o.symbol
                          AND d.trading_mode = o.trading_mode
                          AND d.strategy_version = o.strategy_version
                          AND d.strategy_name IS NOT DISTINCT FROM o.strategy_name
                          AND d.cycle_at = o.created_at
                          AND d.decision = 'accepted'
                        ORDER BY d.id DESC
                        LIMIT 1
                      ) entry_context ON true
                      WHERE o.trading_mode = %s
                        AND o.strategy_version = %s
                        AND o.strategy_name = ANY(%s)
                        AND o.intent_type = 'entry'
                        AND DATE(COALESCE(o.signal_timestamp, o.created_at) AT TIME ZONE %s) = %s
                        AND o.created_at >= %s
                    ),
                    entry_order_windows AS (
                      SELECT
                        entry_orders.*,
                        CASE
                          WHEN signal_timestamp IS NULL THEN NULL
                          ELSE EXTRACT(EPOCH FROM (
                            (signal_timestamp + (%s * interval '1 minute')) - created_at
                          )) / 60.0
                        END AS remaining_active_minutes
                      FROM entry_orders
                    )
                    SELECT
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained AND NOT short_window_drained
                      )::int AS entry_orders,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND NOT short_window_drained
                          AND (status = 'filled' OR COALESCE(filled_quantity, 0) > 0)
                      )::int AS filled_entries,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND NOT short_window_drained
                          AND (strategy_expired OR status = 'expired')
                      )::int AS expired_entries,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND NOT short_window_drained
                          AND status = ANY(%s)
                      )::int AS active_entries,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND NOT short_window_drained
                          AND status <> ALL(%s)
                      )::int AS settled_entries,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND NOT short_window_drained
                          AND status <> ALL(%s)
                          AND (
                            status = 'filled'
                            OR COALESCE(filled_quantity, 0) > 0
                          )
                      )::int AS settled_filled_entries,
                      COUNT(*) FILTER (WHERE maintenance_drained)::int
                        AS maintenance_drained_entries,
                      COUNT(*) FILTER (WHERE short_window_drained)::int
                        AS short_window_drained_entries,
                      COALESCE(
                        string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                          WHERE NOT maintenance_drained
                            AND NOT short_window_drained
                            AND (status = 'filled' OR COALESCE(filled_quantity, 0) > 0)
                        ),
                        'none'
                      ) AS filled_symbols,
                      COALESCE(
                        string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                          WHERE NOT maintenance_drained
                            AND NOT short_window_drained
                            AND (strategy_expired OR status = 'expired')
                        ),
                        'none'
                      ) AS expired_symbols,
                      COALESCE(
                        string_agg(DISTINCT expiry_reason, ',' ORDER BY expiry_reason) FILTER (
                          WHERE NOT maintenance_drained
                            AND NOT short_window_drained
                            AND (strategy_expired OR status = 'expired')
                        ),
                        'none'
                      ) AS expired_reasons,
                      COALESCE(
                        string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                          WHERE NOT maintenance_drained
                            AND NOT short_window_drained
                            AND status = ANY(%s)
                        ),
                        'none'
                      ) AS active_symbols,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND remaining_active_minutes IS NOT NULL
                          AND remaining_active_minutes < %s
                      )::int AS short_window_entries,
                      ROUND((MIN(remaining_active_minutes) FILTER (
                        WHERE NOT maintenance_drained
                          AND remaining_active_minutes IS NOT NULL
                          AND remaining_active_minutes < %s
                      ))::numeric, 1) AS min_remaining_active_minutes,
                      COALESCE(
                        string_agg(DISTINCT symbol, ',' ORDER BY symbol) FILTER (
                          WHERE NOT maintenance_drained
                            AND remaining_active_minutes IS NOT NULL
                            AND remaining_active_minutes < %s
                        ),
                        'none'
                      ) AS short_window_symbols,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND NOT short_window_drained
                          AND (strategy_expired OR status = 'expired')
                          AND signal_price_posture = 'above_limit'
                      )::int AS expired_signal_above_limit_entries,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND NOT short_window_drained
                          AND (strategy_expired OR status = 'expired')
                          AND signal_price_posture = 'below_stop'
                      )::int AS expired_signal_below_stop_entries,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND NOT short_window_drained
                          AND (strategy_expired OR status = 'expired')
                          AND signal_price_posture = 'within_stop_limit'
                      )::int AS expired_signal_within_stop_limit_entries,
                      COUNT(*) FILTER (
                        WHERE NOT maintenance_drained
                          AND NOT short_window_drained
                          AND (strategy_expired OR status = 'expired')
                          AND signal_price_posture = 'missing_context'
                      )::int AS expired_signal_missing_context_entries
                    FROM entry_order_windows
                    """,
                    (
                        trading_mode.value,
                        strategy_version,
                        proof_strategy_names,
                        market_timezone,
                        current_market_date,
                        post_supervisor_execution_since,
                        settings.entry_timeframe_minutes
                        * (settings.entry_order_active_bars + 1),
                        list(ACTIVE_ORDER_STATUSES),
                        list(ACTIVE_ORDER_STATUSES),
                        list(ACTIVE_ORDER_STATUSES),
                        list(ACTIVE_ORDER_STATUSES),
                        settings.entry_timeframe_minutes
                        * settings.entry_order_active_bars
                        * 0.5,
                        settings.entry_timeframe_minutes
                        * settings.entry_order_active_bars
                        * 0.5,
                        settings.entry_timeframe_minutes
                        * settings.entry_order_active_bars
                        * 0.5,
                    ),
                )
                post_supervisor_execution_row = cur.fetchone()
                if post_supervisor_execution_row:
                    post_supervisor_entry_order_count = int(
                        post_supervisor_execution_row[0] or 0
                    )
                    post_supervisor_entry_order_filled_count = int(
                        post_supervisor_execution_row[1] or 0
                    )
                    post_supervisor_entry_order_expired_count = int(
                        post_supervisor_execution_row[2] or 0
                    )
                    post_supervisor_entry_order_active_count = int(
                        post_supervisor_execution_row[3] or 0
                    )
                    post_supervisor_entry_order_settled_count = int(
                        post_supervisor_execution_row[4] or 0
                    )
                    post_supervisor_entry_order_settled_filled_count = int(
                        post_supervisor_execution_row[5] or 0
                    )
                    post_supervisor_entry_order_maintenance_drained_count = int(
                        post_supervisor_execution_row[6] or 0
                    )
                    post_supervisor_entry_order_short_window_drained_count = int(
                        post_supervisor_execution_row[7] or 0
                    )
                    post_supervisor_entry_order_filled_symbols = (
                        post_supervisor_execution_row[8] or "none"
                    )
                    post_supervisor_entry_order_expired_symbols = (
                        post_supervisor_execution_row[9] or "none"
                    )
                    post_supervisor_entry_order_expired_reasons = (
                        post_supervisor_execution_row[10] or "none"
                    )
                    post_supervisor_entry_order_active_symbols = (
                        post_supervisor_execution_row[11] or "none"
                    )
                    post_supervisor_entry_order_short_window_count = int(
                        post_supervisor_execution_row[12] or 0
                    )
                    if post_supervisor_execution_row[13] is not None:
                        post_supervisor_entry_order_min_remaining_active_minutes = float(
                            post_supervisor_execution_row[13]
                        )
                    post_supervisor_entry_order_short_window_symbols = (
                        post_supervisor_execution_row[14] or "none"
                    )
                    post_supervisor_entry_order_expired_signal_price_posture = (
                        format_expired_signal_price_posture(
                            above_limit=int(post_supervisor_execution_row[15] or 0),
                            below_stop=int(post_supervisor_execution_row[16] or 0),
                            within_stop_limit=int(
                                post_supervisor_execution_row[17] or 0
                            ),
                            missing_context=int(post_supervisor_execution_row[18] or 0),
                        )
                    )
                    post_supervisor_entry_order_expired_next_bar_fill_causes = (
                        load_expired_next_bar_fill_cause_summary(
                            cur,
                            trading_mode=trading_mode.value,
                            strategy_version=strategy_version,
                            strategy_names=proof_strategy_names,
                            market_timezone=market_timezone,
                            session_date=current_market_date,
                            since=post_supervisor_execution_since,
                            scenario_dir=scenario_dir,
                            settings=settings,
                            scenario_cache=scenario_bar_cache,
                        )
                    )
                    post_supervisor_entry_order_dispatch_delay_summary = (
                        load_entry_dispatch_delay_summary(
                            cur,
                            trading_mode=trading_mode.value,
                            strategy_version=strategy_version,
                            strategy_names=proof_strategy_names,
                            market_timezone=market_timezone,
                            session_date=current_market_date,
                            since=post_supervisor_execution_since,
                            settings=settings,
                        )
                    )

        unpaired_filled_exit_count = 0
        unpaired_filled_exit_symbols = "none"
        if proof_end >= proof_start:
            cur.execute(
                """
                SELECT
                  COUNT(*)::int AS unpaired_filled_exits,
                  COALESCE(string_agg(DISTINCT x.symbol, ',' ORDER BY x.symbol), 'none')
                FROM orders x
                WHERE x.trading_mode = %s
                  AND x.strategy_version = %s
                  AND x.strategy_name = ANY(%s)
                  AND x.intent_type IN ('stop', 'exit')
                  AND x.fill_price IS NOT NULL
                  AND (x.status = 'filled' OR COALESCE(x.filled_quantity, 0) > 0)
                  AND DATE(x.updated_at AT TIME ZONE %s) >= %s
                  AND DATE(x.updated_at AT TIME ZONE %s) <= %s
                  AND NOT EXISTS (
                    SELECT 1
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND (e.status = 'filled' OR COALESCE(e.filled_quantity, 0) > 0)
                      AND e.updated_at <= x.updated_at
                      AND DATE(e.updated_at AT TIME ZONE %s)
                          = DATE(x.updated_at AT TIME ZONE %s)
                  )
                """,
                (
                    trading_mode.value,
                    strategy_version,
                    proof_strategy_names,
                    market_timezone,
                    proof_start,
                    market_timezone,
                    proof_end,
                    market_timezone,
                    market_timezone,
                ),
            )
            unpaired_exit_row = cur.fetchone()
            if unpaired_exit_row:
                unpaired_filled_exit_count = int(unpaired_exit_row[0] or 0)
                unpaired_filled_exit_symbols = unpaired_exit_row[1] or "none"

    order_store = OrderStore(conn)
    trades = []
    unscored_current_session_trades = []
    if proof_end >= proof_start:
        for session_date in date_range(proof_start, proof_end):
            for proof_strategy_name in proof_strategy_names:
                trades.extend(
                    order_store.list_closed_trades(
                        trading_mode=trading_mode,
                        strategy_version=strategy_version,
                        session_date=session_date,
                        strategy_name=proof_strategy_name,
                        market_timezone=market_timezone,
                    )
                )
    if current_market_date > proof_end and current_market_date >= proof_start:
        for proof_strategy_name in proof_strategy_names:
            unscored_current_session_trades.extend(
                order_store.list_closed_trades(
                    trading_mode=trading_mode,
                    strategy_version=strategy_version,
                    session_date=current_market_date,
                    strategy_name=proof_strategy_name,
                    market_timezone=market_timezone,
                )
            )
finally:
    conn.close()

scenario_status, scenario_problem_summary = load_scenario_coverage(
    symbols=active_watchlist_symbol_names,
    scenario_dir=scenario_dir,
    expected_date=scenario_expected_session,
    require_scenarios=require_scenarios,
)
trade_pnl_rows = [
    (trade, (trade["exit_fill"] - trade["entry_fill"]) * trade["qty"])
    for trade in trades
]
unscored_current_session_trade_pnl_rows = [
    (trade, (trade["exit_fill"] - trade["entry_fill"]) * trade["qty"])
    for trade in unscored_current_session_trades
]
pnl = sum(trade_pnl for _, trade_pnl in trade_pnl_rows)
trade_count = len(trades)
unscored_current_session_pnl = sum(
    trade_pnl for _, trade_pnl in unscored_current_session_trade_pnl_rows
)
unscored_current_session_trade_count = len(unscored_current_session_trades)
sealed_trade_count = trade_count + unscored_current_session_trade_count
sealed_pnl = pnl + unscored_current_session_pnl
wins = sum(1 for _, trade_pnl in trade_pnl_rows if trade_pnl > 0)
losses = sum(1 for _, trade_pnl in trade_pnl_rows if trade_pnl < 0)
flats = trade_count - wins - losses
avg_trade_pnl = pnl / trade_count if trade_count else None
win_rate = wins / trade_count * 100 if trade_count else None
best_trade = max(trade_pnl_rows, key=lambda row: row[1]) if trade_pnl_rows else None
worst_trade = min(trade_pnl_rows, key=lambda row: row[1]) if trade_pnl_rows else None
best_winning_trade = max(
    (row for row in trade_pnl_rows if row[1] > 0),
    key=lambda row: row[1],
    default=None,
)
non_best_trade_pnl_rows = list(trade_pnl_rows)
if best_winning_trade is not None:
    non_best_trade_pnl_rows.remove(best_winning_trade)
non_best_trade_count = len(non_best_trade_pnl_rows)
non_best_trade_pnl = sum(trade_pnl for _, trade_pnl in non_best_trade_pnl_rows)
non_best_avg_trade_pnl = (
    non_best_trade_pnl / non_best_trade_count
    if non_best_trade_pnl_rows
    else None
)
win_rate_text = f"{win_rate:.1f}%" if win_rate is not None else "none"
avg_trade_pnl_text = f"{avg_trade_pnl:.2f}" if avg_trade_pnl is not None else "none"
non_best_trade_pnl_text = f"{non_best_trade_pnl:.2f}"
non_best_avg_trade_pnl_text = (
    f"{non_best_avg_trade_pnl:.2f}"
    if non_best_avg_trade_pnl is not None
    else "none"
)
best_trade_text = (
    format_trade_pnl_atom(best_trade[0], best_trade[1]) if best_trade else "none"
)
worst_trade_text = (
    format_trade_pnl_atom(worst_trade[0], worst_trade[1]) if worst_trade else "none"
)
best_winning_trade_text = (
    format_trade_pnl_atom(best_winning_trade[0], best_winning_trade[1])
    if best_winning_trade is not None
    else "none"
)
recent_trade_rows = sorted(
    trade_pnl_rows,
    key=lambda row: row[0].get("exit_time") or datetime.min.replace(tzinfo=timezone.utc),
)[-5:]
recent_trade_summary = (
    ",".join(format_trade_pnl_atom(trade, trade_pnl) for trade, trade_pnl in recent_trade_rows)
    if recent_trade_rows
    else "none"
)
exit_sessions = [
    trade["exit_time"].astimezone(settings.market_timezone).date()
    for trade in trades
    if trade.get("exit_time") is not None
]
active_trade_session_dates = sorted(set(exit_sessions))
active_trade_day_count = len(active_trade_session_dates)
first_exit_session = min(exit_sessions).isoformat() if exit_sessions else ""
latest_exit_session = max(exit_sessions).isoformat() if exit_sessions else ""
active_trade_sessions_text = (
    ",".join(session.isoformat() for session in active_trade_session_dates)
    if active_trade_session_dates
    else "none"
)
trade_count_by_session_text = (
    ",".join(
        f"{session.isoformat()}:{sum(1 for exit_session in exit_sessions if exit_session == session)}"
        for session in active_trade_session_dates
    )
    if active_trade_session_dates
    else "none"
)
gross_profit = sum(trade_pnl for _, trade_pnl in trade_pnl_rows if trade_pnl > 0)
gross_loss = abs(sum(trade_pnl for _, trade_pnl in trade_pnl_rows if trade_pnl < 0))
profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
profit_factor_text = f"{profit_factor:.2f}" if profit_factor is not None else "none"
best_winning_trade_pnl = best_winning_trade[1] if best_winning_trade else 0.0
single_win_pnl_share = (
    best_winning_trade_pnl / pnl
    if pnl > 0 and best_winning_trade_pnl > 0
    else None
)
single_win_pnl_share_text = (
    f"{single_win_pnl_share:.2f}" if single_win_pnl_share is not None else "none"
)
STRATEGY_EXIT_REASONS = {
    "eod_flatten",
    "loss_limit_flatten",
    "stop_breach_extended_hours",
    "profit_target",
    "viability_trend_filter_failed",
    "viability_vwap_breakdown",
    "no_follow_through",
    "giveback_exit",
    "early_loss_exit",
    "profit_trail",
    "breakeven",
}


def exit_reason(trade: dict) -> str:
    return str(trade.get("reason") or "").strip()


def summarize_trade_pnl_rows(rows: list[tuple[dict, float]]) -> dict:
    row_count = len(rows)
    row_pnl = sum(trade_pnl for _, trade_pnl in rows)
    row_losses = sum(1 for _, trade_pnl in rows if trade_pnl < 0)
    row_exit_sessions = [
        exit_session
        for trade, _ in rows
        if (exit_session := trade_exit_session_date(trade)) is not None
    ]
    row_gross_profit = sum(trade_pnl for _, trade_pnl in rows if trade_pnl > 0)
    row_gross_loss = abs(sum(trade_pnl for _, trade_pnl in rows if trade_pnl < 0))
    row_profit_factor = (
        row_gross_profit / row_gross_loss if row_gross_loss > 0 else None
    )
    row_best_winning_trade_pnl = max(
        (trade_pnl for _, trade_pnl in rows if trade_pnl > 0),
        default=0.0,
    )
    row_single_win_pnl_share = (
        row_best_winning_trade_pnl / row_pnl
        if row_pnl > 0 and row_best_winning_trade_pnl > 0
        else None
    )
    row_eod_loss_rows = [
        (trade, trade_pnl)
        for trade, trade_pnl in rows
        if trade_pnl < 0 and exit_reason(trade) == "eod_flatten"
    ]
    row_operational_exit_loss_rows = [
        (trade, trade_pnl)
        for trade, trade_pnl in rows
        if trade_pnl < 0
        and trade.get("intent_type") == "exit"
        and exit_reason(trade) not in STRATEGY_EXIT_REASONS
    ]
    row_eod_loss_share = len(row_eod_loss_rows) / row_losses if row_losses else 0.0
    row_operational_exit_loss_share = (
        len(row_operational_exit_loss_rows) / row_losses if row_losses else 0.0
    )
    return {
        "active_days": len(set(row_exit_sessions)),
        "eod_loss_count": len(row_eod_loss_rows),
        "eod_loss_share": row_eod_loss_share,
        "eod_loss_share_text": f"{row_eod_loss_share:.2f}" if row_losses else "none",
        "eod_loss_symbols": (
            ",".join(
                sorted(
                    {
                        str(trade.get("symbol") or "")
                        for trade, _ in row_eod_loss_rows
                    }
                )
            )
            if row_eod_loss_rows
            else "none"
        ),
        "losses": row_losses,
        "operational_exit_loss_count": len(row_operational_exit_loss_rows),
        "operational_exit_loss_share": row_operational_exit_loss_share,
        "operational_exit_loss_share_text": (
            f"{row_operational_exit_loss_share:.2f}" if row_losses else "none"
        ),
        "operational_exit_loss_symbols": (
            ",".join(
                sorted(
                    {
                        str(trade.get("symbol") or "")
                        for trade, _ in row_operational_exit_loss_rows
                    }
                )
            )
            if row_operational_exit_loss_rows
            else "none"
        ),
        "pnl": row_pnl,
        "profit_factor": row_profit_factor,
        "profit_factor_text": (
            f"{row_profit_factor:.2f}" if row_profit_factor is not None else "none"
        ),
        "single_win_pnl_share": row_single_win_pnl_share,
        "single_win_pnl_share_text": (
            f"{row_single_win_pnl_share:.2f}"
            if row_single_win_pnl_share is not None
            else "none"
        ),
        "trade_count": row_count,
    }


eod_loss_count = sum(
    1
    for trade, trade_pnl in trade_pnl_rows
    if trade_pnl < 0 and exit_reason(trade) == "eod_flatten"
)
eod_loss_rows = [
    (trade, trade_pnl)
    for trade, trade_pnl in trade_pnl_rows
    if trade_pnl < 0 and exit_reason(trade) == "eod_flatten"
]
eod_loss_share = eod_loss_count / losses if losses else 0.0
eod_loss_share_text = f"{eod_loss_share:.2f}" if losses else "none"
eod_loss_symbols = (
    ",".join(sorted({str(trade.get("symbol") or "") for trade, _ in eod_loss_rows}))
    if eod_loss_rows
    else "none"
)
operational_exit_loss_rows = [
    (trade, trade_pnl)
    for trade, trade_pnl in trade_pnl_rows
    if trade_pnl < 0
    and trade.get("intent_type") == "exit"
    and exit_reason(trade) not in STRATEGY_EXIT_REASONS
]
operational_exit_loss_count = len(operational_exit_loss_rows)
operational_exit_loss_share = operational_exit_loss_count / losses if losses else 0.0
operational_exit_loss_share_text = (
    f"{operational_exit_loss_share:.2f}" if losses else "none"
)
operational_exit_loss_symbols = (
    ",".join(
        sorted({str(trade.get("symbol") or "") for trade, _ in operational_exit_loss_rows})
    )
    if operational_exit_loss_rows
    else "none"
)
operational_exit_loss_reasons = (
    ",".join(
        sorted(
            {
                exit_reason(trade) if exit_reason(trade) else "unknown"
                for trade, _ in operational_exit_loss_rows
            }
        )
    )
    if operational_exit_loss_rows
    else "none"
)
latest_operational_exit_loss_row = max(
    operational_exit_loss_rows,
    key=lambda row: row[0].get("exit_time")
    or datetime.min.replace(tzinfo=timezone.utc),
    default=None,
)
latest_operational_exit_loss_text = (
    format_trade_pnl_atom(
        latest_operational_exit_loss_row[0],
        latest_operational_exit_loss_row[1],
    )
    if latest_operational_exit_loss_row
    else "none"
)
latest_operational_exit_loss_session = None
if latest_operational_exit_loss_row is not None:
    latest_operational_exit_time = latest_operational_exit_loss_row[0].get("exit_time")
    if isinstance(latest_operational_exit_time, datetime):
        latest_operational_exit_loss_session = (
            latest_operational_exit_time.astimezone(settings.market_timezone).date()
        )
operational_exit_clean_start = None
if latest_operational_exit_loss_session is not None:
    (
        operational_exit_clean_start,
        operational_exit_clean_start_warning,
    ) = load_next_market_session_after(
        settings,
        after_date=latest_operational_exit_loss_session,
    )
    if operational_exit_clean_start_warning:
        calendar_warning = (
            f"{calendar_warning}; {operational_exit_clean_start_warning}"
            if calendar_warning
            else operational_exit_clean_start_warning
        )
operational_exit_clean_start_text = (
    operational_exit_clean_start.isoformat()
    if operational_exit_clean_start is not None
    else "none"
)
clean_window_status = "dirty" if operational_exit_loss_rows else "clean"
clean_window_progress_start = (
    operational_exit_clean_start if operational_exit_loss_rows else proof_start
)
clean_window_progress_start_text = (
    clean_window_progress_start.isoformat()
    if clean_window_progress_start is not None
    else "none"
)
clean_window_trade_pnl_rows = [
    (trade, trade_pnl)
    for trade, trade_pnl in trade_pnl_rows
    if clean_window_progress_start is not None
    and (exit_session := trade_exit_session_date(trade)) is not None
    and exit_session >= clean_window_progress_start
]
clean_window_unscored_current_session_trade_pnl_rows = [
    (trade, trade_pnl)
    for trade, trade_pnl in unscored_current_session_trade_pnl_rows
    if clean_window_progress_start is not None
    and (exit_session := trade_exit_session_date(trade)) is not None
    and exit_session >= clean_window_progress_start
]
clean_window_trade_count = len(clean_window_trade_pnl_rows)
clean_window_pnl = sum(
    trade_pnl for _, trade_pnl in clean_window_trade_pnl_rows
)
clean_window_unscored_current_session_trade_count = len(
    clean_window_unscored_current_session_trade_pnl_rows
)
clean_window_unscored_current_session_pnl = sum(
    trade_pnl
    for _, trade_pnl in clean_window_unscored_current_session_trade_pnl_rows
)
clean_window_sealed_trade_count = (
    clean_window_trade_count + clean_window_unscored_current_session_trade_count
)
clean_window_sealed_pnl = (
    clean_window_pnl + clean_window_unscored_current_session_pnl
)
clean_window_sealed_trade_pnl_rows = (
    clean_window_trade_pnl_rows
    + clean_window_unscored_current_session_trade_pnl_rows
)
clean_window_summary = summarize_trade_pnl_rows(clean_window_trade_pnl_rows)
clean_window_sealed_summary = summarize_trade_pnl_rows(
    clean_window_sealed_trade_pnl_rows
)

base_summary = summarize_trade_pnl_rows(trade_pnl_rows)
base_sealed_summary = summarize_trade_pnl_rows(
    trade_pnl_rows + unscored_current_session_trade_pnl_rows
)


def robustness_blockers_for_summary(
    summary: dict,
    *,
    require_strategy_diversification: bool,
) -> list[str]:
    summary_blockers = []
    if int(summary["trade_count"]) < scale_min_trades:
        summary_blockers.append("sample_trades")
    if (
        require_strategy_diversification
        and len(approved_replay_active_strategy_names) < scale_min_strategies
    ):
        summary_blockers.append("strategy_diversification")
    if unapproved_active_strategy_names:
        summary_blockers.append("unapproved_strategy")
    if active_replay_unsupported_strategy_names:
        summary_blockers.append("replay_unsupported_strategy")
    if int(summary["active_days"]) < scale_min_active_days:
        summary_blockers.append("active_days")
    if (
        summary["single_win_pnl_share"] is not None
        and float(summary["single_win_pnl_share"]) > scale_max_single_win_pnl_share
    ):
        summary_blockers.append("profit_concentration")
    if (
        summary["profit_factor"] is not None
        and float(summary["profit_factor"]) < scale_min_profit_factor
    ):
        summary_blockers.append("profit_factor")
    if (
        int(summary["losses"]) > 0
        and float(summary["eod_loss_share"]) > scale_max_eod_loss_share
    ):
        summary_blockers.append("eod_loss_share")
    if (
        int(summary["losses"]) > 0
        and float(summary["operational_exit_loss_share"])
        > scale_max_operational_exit_loss_share
    ):
        summary_blockers.append("operational_exit_loss_share")
    return summary_blockers


proof_blockers = robustness_blockers_for_summary(
    base_summary,
    require_strategy_diversification=False,
)
sealed_proof_blockers = robustness_blockers_for_summary(
    base_sealed_summary,
    require_strategy_diversification=False,
)
clean_window_blockers = robustness_blockers_for_summary(
    clean_window_summary,
    require_strategy_diversification=False,
)
clean_window_sealed_blockers = robustness_blockers_for_summary(
    clean_window_sealed_summary,
    require_strategy_diversification=False,
)
scale_blockers = []
if trade_count < scale_min_trades:
    scale_blockers.append("sample_trades")
if len(approved_replay_active_strategy_names) < scale_min_strategies:
    scale_blockers.append("strategy_diversification")
if unapproved_active_strategy_names:
    scale_blockers.append("unapproved_strategy")
if active_replay_unsupported_strategy_names:
    scale_blockers.append("replay_unsupported_strategy")
if active_trade_day_count < scale_min_active_days:
    scale_blockers.append("active_days")
if (
    single_win_pnl_share is not None
    and single_win_pnl_share > scale_max_single_win_pnl_share
):
    scale_blockers.append("profit_concentration")
if profit_factor is not None and profit_factor < scale_min_profit_factor:
    scale_blockers.append("profit_factor")
if losses and eod_loss_share > scale_max_eod_loss_share:
    scale_blockers.append("eod_loss_share")
if (
    losses
    and operational_exit_loss_share > scale_max_operational_exit_loss_share
):
    scale_blockers.append("operational_exit_loss_share")
entry_order_fill_rate = (
    entry_order_filled_count / entry_order_count if entry_order_count else None
)
posture_entry_fill_rate = (
    posture_entry_order_filled_count / posture_entry_order_count
    if posture_entry_order_count
    else None
)
accepted_for_fill_count = max(
    decision_accepted
    - entry_order_maintenance_drained_count
    - entry_order_short_window_drained_count,
    0,
)
accepted_to_fill_rate = (
    entry_order_filled_count / accepted_for_fill_count
    if accepted_for_fill_count
    else None
)
capacity_reject_rate = (
    decision_capacity_rejected / decision_signal_fired
    if decision_signal_fired
    else None
)
current_session_entry_order_fill_rate = (
    current_session_entry_order_filled_count / current_session_entry_order_count
    if current_session_entry_order_count
    else None
)
current_session_settled_entry_fill_rate = (
    current_session_entry_order_settled_filled_count
    / current_session_entry_order_settled_count
    if current_session_entry_order_settled_count
    else None
)
current_session_accepted_for_fill_count = max(
    current_session_decision_accepted
    - current_session_entry_order_maintenance_drained_count
    - current_session_entry_order_short_window_drained_count,
    0,
)
current_session_settled_accepted_for_fill_count = max(
    current_session_accepted_for_fill_count - current_session_entry_order_active_count,
    0,
)
current_session_accepted_to_fill_rate = (
    current_session_entry_order_filled_count
    / current_session_settled_accepted_for_fill_count
    if current_session_settled_accepted_for_fill_count
    else None
)
current_session_capacity_reject_rate = (
    current_session_decision_capacity_rejected
    / current_session_decision_signal_fired
    if current_session_decision_signal_fired
    else None
)
post_supervisor_entry_order_fill_rate = (
    post_supervisor_entry_order_filled_count / post_supervisor_entry_order_count
    if post_supervisor_entry_order_count
    else None
)
post_supervisor_settled_entry_fill_rate = (
    post_supervisor_entry_order_settled_filled_count
    / post_supervisor_entry_order_settled_count
    if post_supervisor_entry_order_settled_count
    else None
)
post_supervisor_accepted_for_fill_count = max(
    post_supervisor_decision_accepted
    - post_supervisor_entry_order_maintenance_drained_count
    - post_supervisor_entry_order_short_window_drained_count,
    0,
)
post_supervisor_settled_accepted_for_fill_count = max(
    post_supervisor_accepted_for_fill_count
    - post_supervisor_entry_order_active_count,
    0,
)
post_supervisor_accepted_to_fill_rate = (
    post_supervisor_entry_order_filled_count
    / post_supervisor_settled_accepted_for_fill_count
    if post_supervisor_settled_accepted_for_fill_count
    else None
)
post_supervisor_capacity_reject_rate = (
    post_supervisor_decision_capacity_rejected
    / post_supervisor_decision_signal_fired
    if post_supervisor_decision_signal_fired
    else None
)
entry_order_fill_rate_text = (
    f"{entry_order_fill_rate:.2f}" if entry_order_fill_rate is not None else "none"
)
posture_entry_fill_rate_text = (
    f"{posture_entry_fill_rate:.2f}" if posture_entry_fill_rate is not None else "none"
)
accepted_to_fill_rate_text = (
    f"{accepted_to_fill_rate:.2f}" if accepted_to_fill_rate is not None else "none"
)
capacity_reject_rate_text = (
    f"{capacity_reject_rate:.2f}" if capacity_reject_rate is not None else "none"
)
current_session_entry_order_fill_rate_text = (
    f"{current_session_entry_order_fill_rate:.2f}"
    if current_session_entry_order_fill_rate is not None
    else "none"
)
current_session_settled_entry_fill_rate_text = (
    f"{current_session_settled_entry_fill_rate:.2f}"
    if current_session_settled_entry_fill_rate is not None
    else "none"
)
current_session_accepted_to_fill_rate_text = (
    f"{current_session_accepted_to_fill_rate:.2f}"
    if current_session_accepted_to_fill_rate is not None
    else "none"
)
current_session_capacity_reject_rate_text = (
    f"{current_session_capacity_reject_rate:.2f}"
    if current_session_capacity_reject_rate is not None
    else "none"
)
current_session_entry_order_min_remaining_active_minutes_text = (
    f"{current_session_entry_order_min_remaining_active_minutes:.1f}"
    if current_session_entry_order_min_remaining_active_minutes is not None
    else "none"
)
post_supervisor_entry_order_fill_rate_text = (
    f"{post_supervisor_entry_order_fill_rate:.2f}"
    if post_supervisor_entry_order_fill_rate is not None
    else "none"
)
post_supervisor_settled_entry_fill_rate_text = (
    f"{post_supervisor_settled_entry_fill_rate:.2f}"
    if post_supervisor_settled_entry_fill_rate is not None
    else "none"
)
post_supervisor_accepted_to_fill_rate_text = (
    f"{post_supervisor_accepted_to_fill_rate:.2f}"
    if post_supervisor_accepted_to_fill_rate is not None
    else "none"
)
post_supervisor_capacity_reject_rate_text = (
    f"{post_supervisor_capacity_reject_rate:.2f}"
    if post_supervisor_capacity_reject_rate is not None
    else "none"
)
post_supervisor_entry_order_min_remaining_active_minutes_text = (
    f"{post_supervisor_entry_order_min_remaining_active_minutes:.1f}"
    if post_supervisor_entry_order_min_remaining_active_minutes is not None
    else "none"
)
post_supervisor_execution_since_text = (
    post_supervisor_execution_since.isoformat()
    if post_supervisor_execution_since is not None
    else "none"
)
effective_entry_fill_rate = (
    posture_entry_fill_rate
    if posture_entry_fill_rate is not None
    else entry_order_fill_rate
)
effective_entry_fill_rate_source = (
    "current_posture"
    if posture_entry_fill_rate is not None
    else "raw"
)
effective_entry_fill_rate_text = (
    f"{effective_entry_fill_rate:.2f}"
    if effective_entry_fill_rate is not None
    else "none"
)
entry_fill_rate_status = "insufficient_data"
if effective_entry_fill_rate is not None:
    if effective_entry_fill_rate < execution_min_entry_fill_rate:
        entry_fill_rate_status = "below_minimum"
    elif (
        entry_order_fill_rate is not None
        and entry_order_fill_rate < execution_min_entry_fill_rate
        and posture_entry_fill_rate is not None
    ):
        entry_fill_rate_status = "historical_below_minimum_current_ok"
    else:
        entry_fill_rate_status = "ok"
execution_quality_status = "ok"
execution_quality_warnings = []
if (
    effective_entry_fill_rate is not None
    and effective_entry_fill_rate < execution_min_entry_fill_rate
):
    execution_quality_status = "needs_work"
    execution_quality_warnings.append("entry_fill_rate")
    # Low entry throughput constrains proof velocity and scale, but the
    # profitability proof itself is settled by realized P&L/risk evidence.
    scale_blockers.append("entry_fill_rate")
elif (
    entry_order_fill_rate is not None
    and entry_order_fill_rate < execution_min_entry_fill_rate
    and posture_entry_fill_rate is not None
):
    execution_quality_warnings.append("historical_entry_fill_rate")
if (
    capacity_reject_rate is not None
    and capacity_reject_rate > execution_max_capacity_reject_rate
):
    execution_quality_status = "needs_work"
    execution_quality_warnings.append("capacity_rejections")
    # K=1 deliberately rejects many concurrent candidates; keep that visible
    # as a scale blocker without turning expected capacity pressure into a
    # profitability-evidence failure.
    scale_blockers.append("capacity_rejections")
execution_quality_summary_warnings = [
    warning
    for warning in execution_quality_warnings
    if not warning.startswith("historical_")
]
current_session_execution_status = (
    "not_started" if current_market_date < proof_start else "observing"
)
current_session_execution_warnings = []
if current_market_date >= proof_start and (
    current_session_decision_signal_fired > 0
    or current_session_entry_order_count > 0
):
    current_session_execution_status = "ok"
if (
    current_session_settled_entry_fill_rate is not None
    and current_session_settled_entry_fill_rate < execution_min_entry_fill_rate
):
    current_session_execution_status = "needs_work"
    current_session_execution_warnings.append("settled_entry_fill_rate")
elif (
    current_session_entry_order_fill_rate is not None
    and current_session_entry_order_fill_rate < execution_min_entry_fill_rate
    and current_session_settled_entry_fill_rate is not None
):
    current_session_execution_warnings.append("unsettled_entry_fill_rate")
if (
    current_session_capacity_reject_rate is not None
    and current_session_capacity_reject_rate > execution_max_capacity_reject_rate
):
    current_session_execution_status = "needs_work"
    current_session_execution_warnings.append("capacity_rejections")
if current_session_entry_order_short_window_count > 0:
    current_session_execution_status = "needs_work"
    current_session_execution_warnings.append("short_entry_windows")
post_supervisor_execution_status = (
    "not_started" if current_market_date < proof_start else "observing"
)
post_supervisor_execution_warnings = []
if current_market_date >= proof_start and post_supervisor_execution_since is None:
    post_supervisor_execution_status = "no_supervisor_boundary"
elif (
    post_supervisor_decision_signal_fired > 0
    or post_supervisor_entry_order_count > 0
):
    post_supervisor_execution_status = "ok"
if (
    post_supervisor_settled_entry_fill_rate is not None
    and post_supervisor_settled_entry_fill_rate < execution_min_entry_fill_rate
):
    post_supervisor_execution_status = "needs_work"
    post_supervisor_execution_warnings.append("settled_entry_fill_rate")
elif (
    post_supervisor_entry_order_fill_rate is not None
    and post_supervisor_entry_order_fill_rate < execution_min_entry_fill_rate
    and post_supervisor_settled_entry_fill_rate is not None
):
    post_supervisor_execution_warnings.append("unsettled_entry_fill_rate")
if (
    post_supervisor_capacity_reject_rate is not None
    and post_supervisor_capacity_reject_rate > execution_max_capacity_reject_rate
):
    post_supervisor_execution_status = "needs_work"
    post_supervisor_execution_warnings.append("capacity_rejections")
if post_supervisor_entry_order_short_window_count > 0:
    post_supervisor_execution_status = "needs_work"
    post_supervisor_execution_warnings.append("short_entry_windows")
strategy_diversification_status = (
    "ok"
    if (
        len(approved_replay_active_strategy_names) >= scale_min_strategies
        and not unapproved_active_strategy_names
        and not active_replay_unsupported_strategy_names
    )
    else "blocked"
)
strategy_diversification_gap = max(
    0,
    scale_min_strategies - len(approved_replay_active_strategy_names),
)
sample_trades_remaining = max(0, scale_min_trades - trade_count)
active_days_remaining = max(0, scale_min_active_days - active_trade_day_count)
active_day_projection_anchor = (
    active_trade_session_dates[-1]
    if active_trade_session_dates
    else proof_start - timedelta(days=1)
)
(
    active_day_future_sessions,
    active_day_projection_warning,
) = load_upcoming_market_session_dates(
    settings,
    after_date=active_day_projection_anchor,
    count=active_days_remaining,
)
active_day_future_sessions_text = (
    ",".join(session.isoformat() for session in active_day_future_sessions)
    if active_day_future_sessions
    else "none"
)
next_possible_active_session_text = (
    active_day_future_sessions[0].isoformat()
    if active_day_future_sessions
    else "none"
)
earliest_active_days_met_session_text = (
    active_day_future_sessions[-1].isoformat()
    if active_days_remaining > 0
    and len(active_day_future_sessions) >= active_days_remaining
    else latest_exit_session
    if active_days_remaining == 0 and latest_exit_session
    else "none"
)
active_day_projection_status = (
    "met"
    if active_days_remaining == 0
    else "calendar_warning"
    if active_day_projection_warning
    else "ok"
    if len(active_day_future_sessions) >= active_days_remaining
    else "incomplete"
)
remaining_trades_per_required_active_day = (
    sample_trades_remaining / active_days_remaining
    if active_days_remaining > 0
    else None
)
remaining_trades_per_required_active_day_text = (
    f"{remaining_trades_per_required_active_day:.1f}"
    if remaining_trades_per_required_active_day is not None
    else "none"
)
concentration_net_pnl_needed = 0.0
if (
    single_win_pnl_share is not None
    and single_win_pnl_share > scale_max_single_win_pnl_share
    and scale_max_single_win_pnl_share > 0.0
):
    concentration_net_pnl_needed = max(
        0.0,
        (best_winning_trade_pnl / scale_max_single_win_pnl_share) - pnl,
    )
concentration_non_best_avg_trade_gap = (
    math.ceil(concentration_net_pnl_needed / non_best_avg_trade_pnl)
    if concentration_net_pnl_needed > 0.0
    and non_best_avg_trade_pnl is not None
    and non_best_avg_trade_pnl > 0.0
    else None
)
concentration_non_best_avg_trade_gap_text = (
    str(concentration_non_best_avg_trade_gap)
    if concentration_non_best_avg_trade_gap is not None
    else "none"
)
concentration_remaining_trade_required_avg_pnl = (
    concentration_net_pnl_needed / sample_trades_remaining
    if concentration_net_pnl_needed > 0.0 and sample_trades_remaining > 0
    else None
)
concentration_remaining_trade_required_avg_pnl_text = (
    f"{concentration_remaining_trade_required_avg_pnl:.2f}"
    if concentration_remaining_trade_required_avg_pnl is not None
    else "none"
)
concentration_remaining_active_day_required_pnl = (
    concentration_net_pnl_needed / active_days_remaining
    if concentration_net_pnl_needed > 0.0 and active_days_remaining > 0
    else None
)
concentration_remaining_active_day_required_pnl_text = (
    f"{concentration_remaining_active_day_required_pnl:.2f}"
    if concentration_remaining_active_day_required_pnl is not None
    else "none"
)
if single_win_pnl_share is None:
    concentration_status = "not_applicable"
elif single_win_pnl_share > scale_max_single_win_pnl_share:
    concentration_status = "blocked"
else:
    concentration_status = "ok"
if concentration_status == "ok":
    concentration_runway_status = "met"
elif concentration_net_pnl_needed <= 0.0:
    concentration_runway_status = "not_applicable"
elif sample_trades_remaining <= 0:
    concentration_runway_status = "no_remaining_sample_trades"
elif non_best_avg_trade_pnl is None or non_best_avg_trade_pnl <= 0.0:
    concentration_runway_status = "needs_positive_non_best_pnl"
elif (
    concentration_non_best_avg_trade_gap is not None
    and concentration_non_best_avg_trade_gap <= sample_trades_remaining
):
    concentration_runway_status = "on_current_non_best_avg_pace"
else:
    concentration_runway_status = "needs_higher_non_best_pnl"
approval_marker_overlay_status = "disabled"
approval_marker_overlay_marker = "none"
approval_marker_overlay_env_file = "none"
if paper_approval_marker:
    approval_marker_overlay_marker = paper_approval_marker
    approval_marker_overlay_env_file = paper_approval_env_file or "none"
    if paper_approval_marker != second_strategy_evidence["promotion_approval_marker"]:
        approval_marker_overlay_status = "marker_mismatch"
    elif paper_approval_env_file and paper_approval_env_file != proof_status_env_file:
        approval_marker_overlay_status = "env_file_mismatch"
    else:
        approval_marker_overlay_status = "enabled"
approval_marker_overlay_ready = (
    approval_marker_overlay_status == "enabled"
    and promotion_approval_marker_writable == "true"
    and promotion_approval_marker_dir_writable == "true"
)
promotion_action_status = str(second_strategy_evidence["promotion_action_status"])
if promotion_action_status == "ready":
    if approval_marker_overlay_ready:
        promotion_action_status = "ready_needs_approval_marker"
    elif approval_marker_overlay_status == "enabled":
        promotion_action_status = "ready_needs_marker_write_access"
    elif promotion_write_access_status != "ok":
        promotion_action_status = "ready_needs_write_access"
promotion_handoff_status = "none"
promotion_handoff_step = "none"
if second_strategy_evidence["promotion_action_status"] == "ready":
    if approval_marker_overlay_ready:
        promotion_handoff_status = "ready_needs_approval_marker"
        promotion_handoff_step = "approval_marker_write"
    elif approval_marker_overlay_status == "enabled":
        promotion_handoff_status = "ready_needs_marker_write_access"
        promotion_handoff_step = "approval_marker_write"
    elif promotion_write_access_status in {
        "env_file_not_writable",
        "env_dir_not_writable",
    }:
        promotion_handoff_status = "ready_needs_privileged_env_write"
        promotion_handoff_step = "env_allowlist_update"
    elif promotion_write_access_status in {
        "approval_marker_not_writable",
        "approval_marker_dir_not_writable",
        "approval_marker_parent_not_writable",
    }:
        promotion_handoff_status = "ready_needs_marker_write_access"
        promotion_handoff_step = "approval_marker_write"
    elif promotion_write_access_status != "ok":
        promotion_handoff_status = "blocked"
        promotion_handoff_step = "write_access_probe"
approval_marker_action_status = "none"
if second_strategy_evidence["promotion_approval_marker_status"] == "approved":
    approval_marker_action_status = "approved"
elif second_strategy_evidence["promotion_action_status"] == "ready":
    if (
        promotion_approval_marker_writable == "true"
        and promotion_approval_marker_dir_writable == "true"
    ):
        approval_marker_action_status = "ready"
    else:
        approval_marker_action_status = "ready_needs_marker_write_access"
elif second_strategy_evidence["promotion_action_status"] == "review_evidence":
    approval_marker_action_status = "review_evidence"
elif second_strategy_evidence["promotion_action_status"] in {
    "blocked_missing_proof_horizon",
    "rejected_proof_horizon",
    "rejected_promotion_denylist",
    "blocked_unusable_proof_horizon",
}:
    approval_marker_action_status = str(
        second_strategy_evidence["promotion_action_status"]
    )
strategy_diversification_promotion_action_status = promotion_action_status
if strategy_diversification_status == "ok":
    strategy_diversification_candidate_status = "met"
elif unapproved_active_strategy_names:
    strategy_diversification_candidate_status = "unapproved_active_strategy"
elif active_replay_unsupported_strategy_names:
    strategy_diversification_candidate_status = "replay_unsupported_active_strategy"
elif approved_disabled_stock_candidate_names:
    strategy_diversification_candidate_status = "approved_stock_candidate_disabled"
elif second_strategy_evidence["candidate_status"] in {
    "validation_candidate_not_promotable",
    "promotion_denied",
    "proof_horizon_missing",
    "proof_horizon_failed",
    "proof_horizon_unusable",
}:
    strategy_diversification_candidate_status = str(
        second_strategy_evidence["candidate_status"]
    )
elif validated_unapproved_stock_candidate_names:
    strategy_diversification_candidate_status = "validated_stock_candidate_unapproved"
elif approved_disabled_option_candidate_names:
    strategy_diversification_candidate_status = (
        "approved_option_candidate_disabled"
        if option_snapshot_replay_ready
        else "approved_option_candidate_replay_unavailable"
    )
elif validated_unapproved_option_candidate_names:
    strategy_diversification_candidate_status = (
        "validated_option_candidate_unapproved"
        if option_snapshot_replay_ready
        else "validated_option_candidate_replay_unavailable"
    )
elif (
    second_strategy_evidence["status"] == "ok"
    and second_strategy_evidence["candidate_status"]
    in {
        "partial_validation",
        "no_positive_validation_edge",
        "no_positive_prefilter_edge",
        "no_approved_candidate",
    }
):
    strategy_diversification_candidate_status = str(
        second_strategy_evidence["candidate_status"]
    )
else:
    strategy_diversification_candidate_status = "no_approved_stock_strategy"
proof_robustness_status = "ready" if not proof_blockers else "blocked"
sealed_proof_robustness_status = (
    "ready" if not sealed_proof_blockers else "blocked"
)
clean_window_robustness_status = (
    "ready" if not clean_window_blockers else "blocked"
)
clean_window_sealed_robustness_status = (
    "ready" if not clean_window_sealed_blockers else "blocked"
)
scale_status = "ready" if not scale_blockers else "blocked"
latest_supervisor_started_text = (
    latest_supervisor_started_at.isoformat()
    if latest_supervisor_started_at is not None
    else "none"
)
latest_stream_event_type = latest_stream_event_row[0] if latest_stream_event_row else None
latest_stream_event_at = latest_stream_event_row[1] if latest_stream_event_row else None
latest_stream_started_text = (
    latest_stream_started_at.isoformat()
    if latest_stream_started_at is not None
    else "none"
)
latest_stream_event_text = (
    f"{latest_stream_event_type}:{latest_stream_event_at.isoformat()}"
    if latest_stream_event_type is not None and latest_stream_event_at is not None
    else "none"
)
stream_status = "ok"
if latest_stream_started_at is None:
    stream_status = "missing"
stream_issue_status_by_event_type = {
    "trade_update_stream_failed": "failed",
    "trade_update_stream_stopped": "stopped",
    "trade_update_failed": "trade_update_failed",
    "stream_restart_failed": "restart_failed",
    "protective_stop_quantity_replace_failed": "protective_stop_quantity_replace_failed",
}
if (
    stream_status == "ok"
    and latest_stream_event_type in stream_issue_status_by_event_type
    and latest_stream_event_at is not None
    and latest_stream_started_at is not None
    and latest_stream_event_at >= latest_stream_started_at
):
    stream_status = stream_issue_status_by_event_type[latest_stream_event_type]
elif (
    latest_supervisor_started_at is not None
    and latest_stream_started_at
    < latest_supervisor_started_at - timedelta(seconds=stream_start_grace_seconds)
):
    stream_status = "stale"
readiness_audit_row = None
if readiness_audit_rows:
    readiness_audit_row = readiness_audit_rows[0]
    latest_readiness_reason = readiness_audit_row[2] or ""
    if (
        readiness_audit_row[0] != "passed"
        and latest_readiness_reason.startswith("lock_busy")
    ):
        readiness_audit_row = next(
            (row for row in readiness_audit_rows if row[0] == "passed"),
            readiness_audit_row,
        )


def scheduled_check_created_at_text(created_at) -> str:
    if created_at is None:
        return ""
    created_utc = created_at
    if created_utc.tzinfo is None:
        created_utc = created_utc.replace(tzinfo=timezone.utc)
    else:
        created_utc = created_utc.astimezone(timezone.utc)
    return created_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


if readiness_audit_row and readiness_audit_row[0] == "passed":
    effective_readiness_scheduled_check = (
        "paper_readiness",
        "passed",
        "0",
        readiness_target_session.isoformat(),
        proof_start.isoformat(),
        scheduled_check_created_at_text(readiness_audit_row[1]),
    )
    replaced_readiness_scheduled_check = False
    effective_scheduled_checks = []
    for scheduled_check in scheduled_checks:
        if scheduled_check[0] == "paper_readiness":
            effective_scheduled_checks.append(effective_readiness_scheduled_check)
            replaced_readiness_scheduled_check = True
        else:
            effective_scheduled_checks.append(scheduled_check)
    if not replaced_readiness_scheduled_check:
        effective_scheduled_checks.append(effective_readiness_scheduled_check)
        effective_scheduled_checks = sorted(
            effective_scheduled_checks,
            key=lambda row: row[0],
        )
    scheduled_checks = effective_scheduled_checks


def readiness_row_age_minutes(row) -> int | None:
    created_at = row[1]
    if created_at is None:
        return None
    created_utc = created_at
    if created_utc.tzinfo is None:
        created_utc = created_utc.replace(tzinfo=timezone.utc)
    else:
        created_utc = created_utc.astimezone(timezone.utc)
    return max(
        0,
        int((datetime.now(timezone.utc) - created_utc).total_seconds() // 60),
    )


def readiness_row_is_current(row) -> bool:
    created_at = row[1]
    if created_at is None:
        return False
    if (
        latest_supervisor_started_at is not None
        and created_at < latest_supervisor_started_at
    ):
        return False
    age_minutes = readiness_row_age_minutes(row)
    return age_minutes is not None and age_minutes <= readiness_max_pass_age_minutes


def readiness_row_has_decision_dry_run(row) -> bool:
    return (
        len(row) >= 15
        and bool(row[3])
        and bool(row[4])
        and bool(row[5])
        and bool(row[6])
    )


def readiness_row_has_expected_decision_dry_run_strategies(row) -> bool:
    if len(row) < 19:
        return False
    strategy_names = parse_name_list(row[17] or "")
    strategy_count = parse_int_or_none(row[18] or "")
    return (
        strategy_names == expected_readiness_decision_dry_run_strategy_names
        and strategy_count == len(expected_readiness_decision_dry_run_strategy_names)
    )


readiness_audit_check_status = "missing"
readiness_audit_created_at = None
readiness_audit_age_minutes = None
readiness_audit_status = "missing" if readiness_due else "not_due"
if readiness_audit_row:
    readiness_audit_check_status = readiness_audit_row[0] or "unknown"
    readiness_audit_created_at = readiness_audit_row[1]
    readiness_audit_created_utc = readiness_audit_created_at
    if readiness_audit_created_utc.tzinfo is None:
        readiness_audit_created_utc = readiness_audit_created_utc.replace(
            tzinfo=timezone.utc
        )
    else:
        readiness_audit_created_utc = readiness_audit_created_utc.astimezone(
            timezone.utc
        )
    readiness_audit_age_minutes = max(
        0,
        int(
            (
                datetime.now(timezone.utc) - readiness_audit_created_utc
            ).total_seconds()
            // 60
        ),
    )
    readiness_stale_status = ""
    if (
        latest_supervisor_started_at is not None
        and readiness_audit_created_at < latest_supervisor_started_at
    ):
        readiness_stale_status = "stale"
    elif readiness_audit_age_minutes > readiness_max_pass_age_minutes:
        readiness_stale_status = "stale_by_age"
    if readiness_audit_check_status == "passed":
        if readiness_stale_status and readiness_stale_blocks_proof:
            readiness_audit_status = readiness_stale_status
        else:
            readiness_audit_status = "ok"
    elif readiness_audit_check_status == "pending":
        readiness_audit_status = "pending"
    elif not readiness_due:
        readiness_audit_status = "not_due"
    else:
        readiness_audit_status = readiness_audit_check_status
readiness_audit_created_text = (
    readiness_audit_created_at.isoformat()
    if readiness_audit_created_at is not None
    else "none"
)
readiness_audit_age_text = (
    str(readiness_audit_age_minutes)
    if readiness_audit_age_minutes is not None
    else "none"
)
readiness_decision_dry_run_strategy = ""
readiness_decision_dry_run_as_of = ""
readiness_decision_dry_run_active = ""
readiness_decision_dry_run_records = ""
readiness_decision_dry_run_accepted = ""
readiness_decision_dry_run_entry_intents = ""
readiness_decision_dry_run_sample = ""
readiness_decision_dry_run_sample_times = ""
readiness_decision_dry_run_evaluations = ""
readiness_decision_dry_run_min_records = ""
readiness_decision_dry_run_max_accepted = ""
readiness_decision_dry_run_max_entry_intents = ""
readiness_decision_dry_run_reject_stages = ""
readiness_decision_dry_run_reject_reasons = ""
readiness_decision_dry_run_strategies = ""
readiness_decision_dry_run_strategy_count = ""
readiness_decision_dry_run_row = next(
    (
        row
        for row in readiness_audit_rows
        if row[0] == "passed"
        and readiness_row_has_decision_dry_run(row)
        and readiness_row_is_current(row)
        and readiness_row_has_expected_decision_dry_run_strategies(row)
    ),
    None,
)
if readiness_decision_dry_run_row is None:
    readiness_decision_dry_run_row = readiness_audit_row
if not (
    readiness_decision_dry_run_row
    and readiness_row_has_decision_dry_run(readiness_decision_dry_run_row)
):
    readiness_decision_dry_run_row = next(
        (
            row
            for row in readiness_audit_rows
            if row[0] == "passed"
            and readiness_row_has_decision_dry_run(row)
            and readiness_row_is_current(row)
        ),
        readiness_decision_dry_run_row,
    )
if readiness_decision_dry_run_row and len(readiness_decision_dry_run_row) >= 10:
    readiness_decision_dry_run_strategy = readiness_decision_dry_run_row[3] or ""
    readiness_decision_dry_run_as_of = readiness_decision_dry_run_row[4] or ""
    readiness_decision_dry_run_active = readiness_decision_dry_run_row[5] or ""
    readiness_decision_dry_run_records = readiness_decision_dry_run_row[6] or ""
    readiness_decision_dry_run_accepted = readiness_decision_dry_run_row[7] or ""
    readiness_decision_dry_run_entry_intents = readiness_decision_dry_run_row[8] or ""
    readiness_decision_dry_run_sample = readiness_decision_dry_run_row[9] or ""
    if len(readiness_decision_dry_run_row) >= 15:
        readiness_decision_dry_run_sample_times = (
            readiness_decision_dry_run_row[10] or ""
        )
        readiness_decision_dry_run_evaluations = (
            readiness_decision_dry_run_row[11] or ""
        )
        readiness_decision_dry_run_min_records = (
            readiness_decision_dry_run_row[12] or ""
        )
        readiness_decision_dry_run_max_accepted = (
            readiness_decision_dry_run_row[13] or ""
        )
        readiness_decision_dry_run_max_entry_intents = (
            readiness_decision_dry_run_row[14] or ""
        )
    if len(readiness_decision_dry_run_row) >= 17:
        readiness_decision_dry_run_reject_stages = (
            readiness_decision_dry_run_row[15] or ""
        )
        readiness_decision_dry_run_reject_reasons = (
            readiness_decision_dry_run_row[16] or ""
        )
readiness_decision_dry_run_strategies_row = readiness_decision_dry_run_row
if (
    readiness_decision_dry_run_strategies_row
    and len(readiness_decision_dry_run_strategies_row) >= 19
):
    readiness_decision_dry_run_strategies = (
        readiness_decision_dry_run_strategies_row[17] or ""
    )
    readiness_decision_dry_run_strategy_count = (
        readiness_decision_dry_run_strategies_row[18] or ""
    )
readiness_decision_dry_run_active_value = parse_int_or_none(
    readiness_decision_dry_run_active
)
readiness_decision_dry_run_records_value = parse_int_or_none(
    readiness_decision_dry_run_records
)
readiness_decision_dry_run_accepted_value = parse_int_or_none(
    readiness_decision_dry_run_accepted
)
readiness_decision_dry_run_entry_intents_value = parse_int_or_none(
    readiness_decision_dry_run_entry_intents
)
readiness_decision_dry_run_min_records_value = parse_int_or_none(
    readiness_decision_dry_run_min_records
)
readiness_decision_dry_run_evaluations_value = parse_int_or_none(
    readiness_decision_dry_run_evaluations
)
readiness_decision_dry_run_max_accepted_value = parse_int_or_none(
    readiness_decision_dry_run_max_accepted
)
readiness_decision_dry_run_max_entry_intents_value = parse_int_or_none(
    readiness_decision_dry_run_max_entry_intents
)
readiness_decision_dry_run_as_of_session = None
if readiness_decision_dry_run_as_of:
    try:
        readiness_as_of_raw = readiness_decision_dry_run_as_of
        if readiness_as_of_raw.endswith("Z"):
            readiness_as_of_raw = readiness_as_of_raw[:-1] + "+00:00"
        readiness_as_of = datetime.fromisoformat(readiness_as_of_raw)
        if readiness_as_of.tzinfo is None:
            readiness_as_of = readiness_as_of.replace(tzinfo=settings.market_timezone)
        readiness_decision_dry_run_as_of_session = readiness_as_of.astimezone(
            settings.market_timezone
        ).date()
    except ValueError:
        readiness_decision_dry_run_as_of_session = None
readiness_decision_dry_run_status = "ok"
if not (
    readiness_decision_dry_run_strategy
    and readiness_decision_dry_run_as_of
    and readiness_decision_dry_run_active
    and readiness_decision_dry_run_records
    and readiness_decision_dry_run_accepted
    and readiness_decision_dry_run_entry_intents
):
    readiness_decision_dry_run_status = "missing"
elif readiness_decision_dry_run_strategy != strategy_name:
    readiness_decision_dry_run_status = "strategy_mismatch"
elif (
    readiness_decision_dry_run_active_value is None
    or readiness_decision_dry_run_records_value is None
    or readiness_decision_dry_run_accepted_value is None
    or readiness_decision_dry_run_entry_intents_value is None
    or readiness_decision_dry_run_as_of_session is None
):
    readiness_decision_dry_run_status = "invalid"
elif (
    readiness_expected_decision_dry_run_session is not None
    and readiness_decision_dry_run_as_of_session
    != readiness_expected_decision_dry_run_session
):
    readiness_decision_dry_run_status = "session_mismatch"
elif readiness_decision_dry_run_active_value < min_watchlist_symbols:
    readiness_decision_dry_run_status = "active_under_minimum"
elif readiness_decision_dry_run_records_value < min_decision_dry_run_records:
    readiness_decision_dry_run_status = "records_under_minimum"
elif (
    readiness_decision_dry_run_evaluations_value is None
    or readiness_decision_dry_run_evaluations_value < min_decision_dry_run_evaluations
):
    readiness_decision_dry_run_status = "evaluations_under_minimum"
elif (
    readiness_decision_dry_run_min_records_value is not None
    and readiness_decision_dry_run_min_records_value < min_decision_dry_run_records
):
    readiness_decision_dry_run_status = "sample_records_under_minimum"
elif (
    max(
        readiness_decision_dry_run_accepted_value,
        readiness_decision_dry_run_max_accepted_value or 0,
    )
    <= 0
):
    readiness_decision_dry_run_status = "accepted_under_minimum"
elif (
    max(
        readiness_decision_dry_run_entry_intents_value,
        readiness_decision_dry_run_max_entry_intents_value or 0,
    )
    <= 0
):
    readiness_decision_dry_run_status = "entry_intents_under_minimum"
readiness_decision_dry_run_strategy_count_value = parse_int_or_none(
    readiness_decision_dry_run_strategy_count
)
readiness_decision_dry_run_strategy_names = parse_name_list(
    readiness_decision_dry_run_strategies
)
readiness_decision_dry_run_strategies_status = "ok"
if not readiness_decision_dry_run_strategies:
    readiness_decision_dry_run_strategies_status = "missing"
elif not readiness_decision_dry_run_strategy_names:
    readiness_decision_dry_run_strategies_status = "invalid"
elif readiness_decision_dry_run_strategy_count_value is None:
    readiness_decision_dry_run_strategies_status = "invalid"
elif (
    readiness_decision_dry_run_strategy_names
    != expected_readiness_decision_dry_run_strategy_names
):
    readiness_decision_dry_run_strategies_status = "strategy_set_mismatch"
elif readiness_decision_dry_run_strategy_count_value != len(
    expected_readiness_decision_dry_run_strategy_names
):
    readiness_decision_dry_run_strategies_status = "strategy_count_mismatch"
activity_due = False
activity_due_after = "none"
activity_required_since = None
activity_required_since_text = "none"
activity_check_status = "missing"
activity_check_exit_code = "unknown"
activity_check_created_text = "none"
activity_audit_status = "not_started"
if activity_target_session is not None:
    activity_first_check_time = time(10, 35)
    activity_first_due_time = time(10, 45)
    activity_late_check_time = time(14, 35)
    activity_late_due_time = time(14, 45)
    if current_market_datetime.date() > activity_target_session or (
        current_market_datetime.date() == activity_target_session
        and current_market_datetime.time() >= activity_late_due_time
    ):
        activity_due_time = activity_late_due_time
        activity_required_since_time = activity_late_check_time
    else:
        activity_due_time = activity_first_due_time
        activity_required_since_time = activity_first_check_time
    activity_required_since = datetime.combine(
        activity_target_session,
        activity_required_since_time,
        settings.market_timezone,
    ).astimezone(timezone.utc)
    activity_required_since_text = activity_required_since.isoformat()
    activity_due_after = (
        f"{activity_target_session.isoformat()} "
        f"{activity_due_time.strftime('%H:%M')} {settings.market_timezone.key}"
    )
    activity_due = current_market_datetime.date() > activity_target_session or (
        current_market_datetime.date() == activity_target_session
        and current_market_datetime.time() >= activity_due_time
    )
    activity_audit_status = "not_due"
    if activity_audit_row:
        activity_check_status = activity_audit_row[0] or "unknown"
        activity_check_exit_code = activity_audit_row[1] or "unknown"
        activity_created_at = activity_audit_row[2]
        activity_check_created_text = (
            activity_created_at.isoformat() if activity_created_at is not None else "none"
        )
        if activity_check_status == "passed":
            if (
                activity_due
                and activity_required_since is not None
                and activity_created_at is not None
                and activity_created_at < activity_required_since
            ):
                activity_audit_status = "stale"
            else:
                activity_audit_status = "ok"
        elif activity_check_status == "skipped":
            activity_audit_status = "skipped" if activity_due else "ok"
        elif activity_check_status == "pending":
            activity_audit_status = "pending"
        elif not activity_due:
            activity_audit_status = "not_due"
        else:
            activity_audit_status = "failed"
    elif activity_due:
        activity_audit_status = "missing"
post_close_due = False
post_close_due_after = "none"
post_close_required_since = None
post_close_required_since_text = "none"
post_close_audit_status = "not_started"
post_close_pass_evidence_ready = False
post_close_check_statuses = {
    "session_guard": "missing",
    "paper_profit_probe": "missing",
}
if post_close_target_session is not None:
    due_time = time(17, 25)
    post_close_due_after = (
        f"{post_close_target_session.isoformat()} "
        f"{due_time.strftime('%H:%M')} {settings.market_timezone.key}"
    )
    post_close_required_since = datetime.combine(
        post_close_target_session,
        time(16, 30),
        settings.market_timezone,
    ).astimezone(timezone.utc)
    post_close_required_since_text = post_close_required_since.isoformat()
    post_close_due = current_market_datetime.date() > post_close_target_session or (
        current_market_datetime.date() == post_close_target_session
        and current_market_datetime.time() >= due_time
    )
    post_close_audit_status = "not_due"
    for check_name, status, exit_code, created_at in post_close_audit_rows:
        created_text = created_at.isoformat() if created_at is not None else "none"
        check_status = status or "unknown"
        if created_at is not None and post_close_required_since is not None:
            created_utc = created_at
            if created_utc.tzinfo is None:
                created_utc = created_utc.replace(tzinfo=timezone.utc)
            else:
                created_utc = created_utc.astimezone(timezone.utc)
            if created_utc < post_close_required_since:
                check_status = "stale"
        post_close_check_statuses[check_name] = (
            f"{check_status}:{exit_code or 'unknown'}:{created_text}"
        )
    if post_close_due:
        missing_checks = [
            name
            for name, status in post_close_check_statuses.items()
            if status == "missing"
        ]
        stale_checks = []
        failed_checks = []
        session_guard_parts = post_close_check_statuses["session_guard"].split(":")
        session_guard_status = session_guard_parts[0]
        session_guard_exit_code = session_guard_parts[1] if len(session_guard_parts) > 1 else ""
        profit_probe_parts = post_close_check_statuses["paper_profit_probe"].split(":")
        profit_probe_status = profit_probe_parts[0]
        profit_probe_exit_code = profit_probe_parts[1] if len(profit_probe_parts) > 1 else ""
        session_guard_acceptable = session_guard_status == "passed" or (
            session_guard_status == "pending" and session_guard_exit_code == "43"
        )
        profit_probe_acceptable = profit_probe_status == "passed" or (
            profit_probe_status == "pending" and profit_probe_exit_code == "43"
        )
        if session_guard_status == "stale":
            stale_checks.append("session_guard")
        if profit_probe_status == "stale":
            stale_checks.append("paper_profit_probe")
        if session_guard_status != "missing" and not session_guard_acceptable:
            failed_checks.append("session_guard")
        if profit_probe_status != "missing" and not profit_probe_acceptable:
            failed_checks.append("paper_profit_probe")
        if missing_checks:
            post_close_audit_status = "missing"
        elif stale_checks:
            post_close_audit_status = "stale"
        elif failed_checks:
            post_close_audit_status = "failed"
        else:
            post_close_audit_status = "ok"
            post_close_pass_evidence_ready = (
                session_guard_acceptable and profit_probe_status == "passed"
            )
proof_not_started = proof_end < proof_start
base_profitable_enough = trade_count >= min_trades and pnl >= min_pnl
base_sealed_profitable_enough = sealed_trade_count >= min_trades and sealed_pnl >= min_pnl
proof_quality_ready = proof_robustness_status == "ready"
clean_window_base_profitable_enough = (
    clean_window_status == "dirty"
    and clean_window_trade_count >= min_trades
    and clean_window_pnl >= min_pnl
)
clean_window_base_sealed_profitable_enough = (
    clean_window_status == "dirty"
    and clean_window_sealed_trade_count >= min_trades
    and clean_window_sealed_pnl >= min_pnl
)
clean_window_quality_ready = clean_window_robustness_status == "ready"
clean_window_sealed_quality_ready = clean_window_sealed_robustness_status == "ready"
base_proof_eligible = base_profitable_enough and proof_quality_ready
base_sealed_proof_eligible = base_sealed_profitable_enough and proof_quality_ready
clean_window_proof_eligible = (
    clean_window_base_profitable_enough and clean_window_quality_ready
)
clean_window_sealed_proof_eligible = (
    clean_window_base_sealed_profitable_enough
    and clean_window_sealed_quality_ready
)
profitable_enough = base_proof_eligible or clean_window_proof_eligible
sealed_profitable_enough = (
    base_sealed_proof_eligible or clean_window_sealed_proof_eligible
)
proof_basis = (
    "base"
    if base_proof_eligible
    else "clean_window"
    if clean_window_proof_eligible
    else "pending"
)
sealed_proof_basis = (
    "base"
    if base_sealed_proof_eligible
    else "clean_window"
    if clean_window_sealed_proof_eligible
    else "pending"
)
if proof_not_started:
    proof_status = "pending"
elif profitable_enough and post_close_pass_evidence_ready:
    proof_status = "passed"
elif base_profitable_enough or clean_window_base_profitable_enough:
    proof_status = "pending"
elif trade_count >= min_trades:
    proof_status = "pending"
else:
    proof_status = "pending"
proof_window = (
    f"{proof_start.isoformat()}..{proof_end.isoformat()}"
    if not proof_not_started
    else (
        "not_started("
        f"latest_completed_session={latest_completed_session.isoformat() if latest_completed_session else 'unknown'} "
        f"current_market_date={current_market_date.isoformat()}"
        ")"
    )
)
proof_strategy_missing_active_names = [
    name for name in proof_strategy_names if name not in active_strategy_names
]
proof_strategy_unapproved_names = [
    name for name in proof_strategy_names if name not in approved_strategy_name_set
]
strategy_status = (
    "ok"
    if not proof_strategy_missing_active_names and not proof_strategy_unapproved_names
    else "unapproved"
    if proof_strategy_unapproved_names
    else "disabled"
)
watchlist_status = (
    "ok"
    if active_watchlist_symbols >= min_watchlist_symbols
    else "under_minimum"
)
stored_weight_names = sorted(weights_by_strategy)
stored_weight_sum = sum(float(row["weight"]) for row in weights_by_strategy.values())
nonpositive_weight_count = sum(
    1 for row in weights_by_strategy.values() if float(row["weight"]) <= 0.0
)
null_sharpe_count = sum(1 for row in weights_by_strategy.values() if row["sharpe"] is None)
weight_status = (
    "ok"
    if (
        active_strategy_names
        and active_strategy_names == stored_weight_names
        and nonpositive_weight_count == 0
        and null_sharpe_count == 0
        and abs(stored_weight_sum - 1.0) < 0.0001
    )
    else "mismatch"
)
confidence_floor_status = (
    "ok"
    if min_confidence_floor <= confidence_floor_value <= 1.0
    else "mismatch"
)
sizing_status = (
    "ok" if weight_status == "ok" and confidence_floor_status == "ok" else "drifted"
)
target_weight_info = weights_by_strategy.get(strategy_name)
target_weight = (
    float(target_weight_info["weight"]) if target_weight_info is not None else None
)
target_sharpe = (
    float(target_weight_info["sharpe"])
    if target_weight_info is not None and target_weight_info["sharpe"] is not None
    else None
)
posture_status = (
    "ok"
    if (
        settings.market_data_feed.value == "iex"
        and int(settings.daily_sma_period) == 20
        and int(settings.breakout_lookback_bars) == 20
        and int(settings.relative_volume_lookback_bars) == 10
        and abs(float(settings.relative_volume_threshold) - 2.0) < 1e-9
        and int(settings.entry_timeframe_minutes) == 15
        and int(settings.entry_order_active_bars) == 1
        and abs(float(settings.risk_per_trade_pct) - 0.01) < 1e-9
        and abs(float(settings.max_position_pct) - 0.05) < 1e-9
        and int(settings.max_open_positions) == 1
        and abs(float(settings.max_portfolio_exposure_pct) - 0.30) < 1e-9
        and abs(float(settings.daily_loss_limit_pct) - 0.01) < 1e-9
        and abs(float(settings.stop_limit_buffer_pct) - 0.0005) < 1e-9
        and abs(float(settings.entry_stop_price_buffer) - 0.02) < 1e-9
        and abs(float(settings.entry_min_close_to_entry_pct) - (-0.01)) < 1e-9
        and abs(float(settings.entry_max_close_to_entry_pct) - 1.0) < 1e-9
        and int(settings.atr_period) == 20
        and abs(float(settings.atr_stop_multiplier) - 1.0) < 1e-9
        and abs(float(settings.trailing_stop_atr_multiplier) - 1.0) < 1e-9
        and abs(float(settings.trailing_stop_profit_trigger_r) - 1.0) < 1e-9
        and abs(float(settings.bull_flag_min_run_pct) - 0.02) < 1e-9
        and abs(float(settings.bull_flag_consolidation_volume_ratio) - 0.6) < 1e-9
        and abs(float(settings.bull_flag_consolidation_range_pct) - 0.5) < 1e-9
        and as_hhmm(settings.entry_window_start) == "10:00"
        and as_hhmm(settings.entry_window_end) == "15:30"
        and as_hhmm(settings.flatten_time) == "15:45"
        and not bool(settings.enable_vwap_entry_filter)
        and bool(settings.enable_profit_trail)
        and abs(float(settings.profit_trail_pct) - 0.90) < 1e-9
        and bool(settings.enable_profit_target)
        and abs(float(settings.profit_target_r) - 3.0) < 1e-9
        and bool(settings.enable_breakeven_stop)
        and abs(float(settings.breakeven_trigger_pct) - 0.005) < 1e-9
        and abs(float(settings.breakeven_trail_pct) - 0.002) < 1e-9
        and not bool(settings.enable_vix_filter)
        and not bool(settings.enable_sector_filter)
        and not bool(settings.enable_regime_filter)
        and not bool(settings.enable_news_filter)
        and not bool(settings.enable_spread_filter)
        and not bool(settings.enable_options_trading)
        and not bool(settings.extended_hours_enabled)
        and not bool(settings.enable_trend_filter_exit)
        and not bool(settings.enable_vwap_breakdown_exit)
        and not bool(settings.enable_no_follow_through_exit)
        and int(settings.no_follow_through_exit_minutes) == 0
        and abs(float(settings.no_follow_through_min_favorable_pct) - 0.0025) < 1e-9
        and bool(settings.enable_giveback_exit)
        and abs(float(settings.giveback_exit_min_favorable_pct) - 0.0025) < 1e-9
        and abs(float(settings.giveback_exit_max_return_pct) - 0.0) < 1e-9
        and not bool(settings.enable_early_loss_exit)
        and int(settings.early_loss_exit_minutes) == 0
        and abs(float(settings.early_loss_exit_return_pct) - 0.01) < 1e-9
        and abs(float(settings.per_symbol_loss_limit_pct) - 0.0) < 1e-9
        and abs(float(settings.min_position_notional) - 0.0) < 1e-9
        and abs(float(settings.max_stop_pct) - 0.05) < 1e-9
        and int(settings.viability_daily_bar_max_age_days) == 5
        and int(settings.viability_min_hold_minutes) == 0
        and settings.max_loss_per_trade_dollars is not None
        and abs(float(settings.max_loss_per_trade_dollars) - 20.0) < 1e-9
        and bool(settings.paper_proof_freeze)
        and int(settings.intraday_consecutive_loss_gate) == 0
        and abs(float(settings.replay_slippage_bps) - 2.0) < 1e-9
    )
    else "drifted"
)
blockers = []
if strategy_status != "ok":
    blockers.append("strategy_disabled")
if watchlist_status != "ok":
    blockers.append("watchlist_under_minimum")
if scenario_status not in {"ok", "skipped"}:
    blockers.append(f"scenario_evidence_{scenario_status}")
if sizing_status != "ok":
    blockers.append("sizing_drifted")
if posture_status != "ok":
    blockers.append("posture_drifted")
if cron_health_status != "ok":
    blockers.append("cron_health_failed")
if nightly_status.endswith("_stale") or nightly_status.endswith("_stalled"):
    blockers.append(f"nightly_{nightly_status.rsplit('_', 1)[-1]}")
if ops_health_status != "ok":
    blockers.append("ops_health_failed")
if runtime_image_health_status != "ok":
    blockers.append("runtime_image_health_failed")
if stream_status != "ok":
    blockers.append(f"stream_{stream_status}")
if readiness_audit_status in {"missing", "failed", "skipped", "stale", "stale_by_age"} or (
    readiness_due and readiness_audit_status not in {"ok", "not_due"}
):
    blockers.append(f"readiness_audit_{readiness_audit_status}")
elif readiness_audit_status == "ok" and readiness_decision_dry_run_status != "ok":
    blockers.append(f"readiness_decision_dry_run_{readiness_decision_dry_run_status}")
elif (
    readiness_audit_status == "ok"
    and readiness_decision_dry_run_strategies_status != "ok"
):
    blockers.append(
        "readiness_decision_dry_run_strategies_"
        f"{readiness_decision_dry_run_strategies_status}"
    )
if activity_audit_status in {"missing", "failed", "skipped", "stale"} or (
    activity_due and activity_audit_status == "pending"
):
    blockers.append(f"activity_audit_{activity_audit_status}")
if post_close_audit_status in {"missing", "failed", "stale"}:
    blockers.append(f"post_close_audit_{post_close_audit_status}")
if local_open_positions > 0:
    blockers.append("local_open_positions")
if local_active_orders > 0:
    blockers.append("local_active_orders")
if local_open_option_positions > 0:
    blockers.append("local_open_option_positions")
if local_active_option_orders > 0:
    blockers.append("local_active_option_orders")
if broker_exposure_warning:
    blockers.append("broker_exposure_unknown")
else:
    if broker_open_orders and broker_open_orders > 0:
        blockers.append("broker_open_orders")
    if broker_open_positions and broker_open_positions > 0:
        blockers.append("broker_open_positions")
    if broker_account_status != "ok":
        blockers.append("broker_account_blocked")

profit_lock_pause = (
    ops_health_status != "ok"
    and trading_status_value == "close_only"
    and not trading_status_kill_switch_enabled
    and trading_status_reason.startswith("paper profit lock")
    and local_open_positions == 0
    and local_active_orders == 0
    and local_open_option_positions == 0
    and local_active_option_orders == 0
    and not broker_exposure_warning
    and (broker_open_orders or 0) == 0
    and (broker_open_positions or 0) == 0
    and broker_account_status == "ok"
)
if profit_lock_pause:
    blockers = [blocker for blocker in blockers if blocker != "ops_health_failed"]
    ops_health_status = "ok"
    ops_health_detail = (
        f"{ops_health_detail or 'ops check failed'}; accepted flat paper profit lock"
    )

local_position_symbol_set = parse_symbol_set(local_open_position_symbols)
local_active_order_symbol_set = parse_symbol_set(local_active_order_symbols)
broker_position_symbol_set = parse_symbol_set(broker_open_position_symbols)
broker_order_symbol_set = parse_symbol_set(broker_open_order_symbols)
entry_pending_exposure = (
    local_open_positions == 0
    and local_active_entry_orders > 0
    and local_active_orders == local_active_entry_orders
    and local_open_option_positions == 0
    and local_active_option_orders == 0
    and not broker_exposure_warning
    and (broker_open_positions or 0) == 0
    and (broker_open_orders or 0) == local_active_entry_orders
    and broker_account_status == "ok"
)
exposure_protection_issues = []
if broker_exposure_warning:
    exposure_protection_issues.append("broker_exposure_unknown")
if local_active_entry_orders > 0 and not entry_pending_exposure:
    exposure_protection_issues.append("active_entry_orders")
if local_open_option_positions > 0:
    exposure_protection_issues.append("local_option_positions")
if local_active_option_orders > 0:
    exposure_protection_issues.append("local_option_orders")
if local_open_positions > 0:
    if local_active_stop_orders < local_open_positions:
        exposure_protection_issues.append("local_stop_orders_below_positions")
    if local_active_orders != local_active_stop_orders:
        exposure_protection_issues.append("local_active_orders_not_all_stops")
    if local_position_symbol_set != local_active_order_symbol_set:
        exposure_protection_issues.append("local_symbol_mismatch")
    if not broker_exposure_warning:
        if (broker_open_positions or 0) != local_open_positions:
            exposure_protection_issues.append("broker_position_count_mismatch")
        if (broker_open_orders or 0) != local_active_stop_orders:
            exposure_protection_issues.append("broker_order_count_mismatch")
        if local_position_symbol_set != broker_position_symbol_set:
            exposure_protection_issues.append("broker_position_symbol_mismatch")
        if local_position_symbol_set != broker_order_symbol_set:
            exposure_protection_issues.append("broker_order_symbol_mismatch")
        if broker_account_status != "ok":
            exposure_protection_issues.append("broker_account_blocked")
elif (
    not entry_pending_exposure
    and (local_active_orders > 0 or (broker_open_orders or 0) > 0)
):
    exposure_protection_issues.append("active_orders_without_local_positions")
elif not broker_exposure_warning and (broker_open_positions or 0) > 0:
    exposure_protection_issues.append("broker_positions_without_local_positions")
exposure_protection_status = (
    "flat"
    if (
        local_open_positions == 0
        and local_active_orders == 0
        and local_open_option_positions == 0
        and local_active_option_orders == 0
        and not broker_exposure_warning
        and (broker_open_positions or 0) == 0
        and (broker_open_orders or 0) == 0
    )
    else (
        "entry_pending"
        if entry_pending_exposure and not exposure_protection_issues
        else "protected"
        if not exposure_protection_issues
        else "needs_attention"
    )
)
exposure_protection_issue_text = (
    ",".join(exposure_protection_issues) if exposure_protection_issues else "none"
)
max_loss_per_trade = float(settings.max_loss_per_trade_dollars or 0.0)
open_stock_exposure_count = max(local_open_positions, broker_open_positions or 0)
projected_risk_lock_pnl = pnl - (max_loss_per_trade * open_stock_exposure_count)
proof_risk_lock_open_ok = (
    not proof_not_started
    and trade_count < min_trades
    and trade_count + local_open_positions >= min_trades
    and local_open_positions > 0
    and local_active_entry_orders == 0
    and local_active_stop_orders >= local_open_positions
    and local_active_orders == local_active_stop_orders
    and local_open_option_positions == 0
    and local_active_option_orders == 0
    and not broker_exposure_warning
    and broker_account_status == "ok"
    and (broker_open_positions or 0) == local_open_positions
    and (broker_open_orders or 0) == local_active_stop_orders
    and local_position_symbol_set == local_active_order_symbol_set
    and local_position_symbol_set == broker_position_symbol_set
    and local_position_symbol_set == broker_order_symbol_set
    and max_loss_per_trade > 0
    and projected_risk_lock_pnl >= min_pnl
)
proof_risk_lock_flat_ok = (
    not proof_not_started
    and sealed_profitable_enough
    and local_open_positions == 0
    and local_active_orders == 0
    and local_open_option_positions == 0
    and local_active_option_orders == 0
    and not broker_exposure_warning
    and (broker_open_orders or 0) == 0
    and (broker_open_positions or 0) == 0
    and broker_account_status == "ok"
)
proof_risk_lock_pause = (
    ops_health_status != "ok"
    and ops_close_only_health_status == "ok"
    and trading_status_value == "close_only"
    and not trading_status_kill_switch_enabled
    and trading_status_reason.startswith("paper proof risk lock")
    and (proof_risk_lock_open_ok or proof_risk_lock_flat_ok)
)
if proof_risk_lock_pause:
    blockers = [blocker for blocker in blockers if blocker != "ops_health_failed"]
    ops_health_status = "ok"
    ops_health_detail = (
        f"{ops_health_detail or 'ops check failed'}; "
        f"accepted paper proof risk lock; close_only_health={ops_close_only_health_detail or 'ok'}"
    )

warnings = []
if calendar_warning:
    warnings.append("calendar_warning")
if nightly_status.startswith("legacy_inline_"):
    warnings.append("nightly_legacy_inline")
if nightly_status.endswith("_stale"):
    warnings.append("nightly_stale")
elif nightly_status.endswith("_stalled"):
    warnings.append("nightly_stalled")
if second_strategy_scan_status == "failed":
    warnings.append("second_strategy_scan_failed")
if profit_lock_pause:
    warnings.append("profit_lock_pause")
if proof_risk_lock_pause:
    warnings.append("proof_risk_lock_pause")
if not proof_not_started and 0 < trade_count:
    if trade_count < min_trades:
        if pnl < 0:
            warnings.append("partial_pnl_negative")
        elif pnl < min_pnl:
            warnings.append("partial_pnl_below_minimum")
    elif pnl < 0:
        warnings.append("cumulative_pnl_negative")
    elif pnl < min_pnl:
        warnings.append("cumulative_pnl_below_minimum")
if not proof_not_started and unpaired_filled_exit_count > 0:
    warnings.append("unpaired_filled_exits")
summary_warnings = list(warnings)
for warning_prefix, warning_values in (
    ("execution", execution_quality_summary_warnings),
    ("current_session_execution", current_session_execution_warnings),
    ("post_supervisor_execution", post_supervisor_execution_warnings),
):
    for warning_value in warning_values:
        warning_name = f"{warning_prefix}_{warning_value}"
        if warning_name not in summary_warnings:
            summary_warnings.append(warning_name)

readiness_status = "blocked" if blockers else "ready"
evidence_blockers = clean_window_blockers if clean_window_status == "dirty" else proof_blockers
sealed_evidence_blockers = (
    clean_window_sealed_blockers if clean_window_status == "dirty" else sealed_proof_blockers
)
if proof_status == "passed":
    proof_reason = "profit_proven"
elif proof_not_started:
    proof_reason = "awaiting_completed_proof_session"
elif profitable_enough and not post_close_pass_evidence_ready:
    proof_reason = "awaiting_post_close_audit"
elif base_profitable_enough and not proof_quality_ready:
    proof_reason = "awaiting_robustness_evidence"
elif base_sealed_profitable_enough and latest_completed_session != current_market_date:
    proof_reason = "awaiting_completed_proof_session"
elif base_sealed_profitable_enough and not proof_quality_ready:
    proof_reason = "awaiting_robustness_evidence"
elif clean_window_base_profitable_enough and not clean_window_quality_ready:
    proof_reason = "awaiting_clean_window_robustness"
elif (
    clean_window_base_sealed_profitable_enough
    and latest_completed_session != current_market_date
):
    proof_reason = "awaiting_completed_proof_session"
elif (
    clean_window_base_sealed_profitable_enough
    and not clean_window_sealed_quality_ready
):
    proof_reason = "awaiting_clean_window_robustness"
elif clean_window_status == "dirty" and clean_window_trade_count < min_trades:
    proof_reason = "awaiting_clean_window_evidence"
elif trade_count < min_trades:
    proof_reason = "awaiting_min_trades"
else:
    proof_reason = "awaiting_positive_pnl"

paper_profit_probe_reason = "none"
paper_profit_probe_parts = post_close_check_statuses["paper_profit_probe"].split(":")
paper_profit_probe_status = paper_profit_probe_parts[0]
paper_profit_probe_exit_code = (
    paper_profit_probe_parts[1] if len(paper_profit_probe_parts) > 1 else ""
)
if paper_profit_probe_status == "pending" and paper_profit_probe_exit_code == "43":
    paper_profit_probe_reason = proof_reason
elif paper_profit_probe_status == "passed":
    paper_profit_probe_reason = "profit_probe_passed"
elif paper_profit_probe_status == "missing":
    paper_profit_probe_reason = (
        post_close_audit_status
        if post_close_audit_status in {"not_started", "not_due"}
        else "awaiting_post_close_probe"
    )
elif paper_profit_probe_status == "stale":
    paper_profit_probe_reason = "stale_post_close_probe"
elif paper_profit_probe_status != "none":
    paper_profit_probe_reason = f"probe_{paper_profit_probe_status}"

if proof_status == "passed":
    proof_overall_reason = "profit_proven"
elif blockers:
    proof_overall_reason = "readiness_blocked"
elif scale_blockers:
    proof_overall_reason = "awaiting_overall_blockers"
elif evidence_blockers or sealed_evidence_blockers:
    proof_overall_reason = "awaiting_profit_evidence"
else:
    proof_overall_reason = proof_reason

print(
    "paper proof summary: "
    f"readiness={readiness_status} "
    f"proof={proof_status} "
    f"reason={proof_reason} "
    f"overall_reason={proof_overall_reason} "
    f"blockers={','.join(blockers) if blockers else 'none'} "
    f"evidence_blockers={','.join(evidence_blockers) if evidence_blockers else 'none'} "
    f"sealed_evidence_blockers={','.join(sealed_evidence_blockers) if sealed_evidence_blockers else 'none'} "
    f"overall_blockers={','.join(scale_blockers) if scale_blockers else 'none'} "
    f"clean_window_blockers={','.join(clean_window_blockers) if clean_window_blockers else 'none'} "
    f"sealed_clean_window_blockers={','.join(clean_window_sealed_blockers) if clean_window_sealed_blockers else 'none'} "
    f"warnings={','.join(summary_warnings) if summary_warnings else 'none'}"
)

print(
    "paper proof automation: "
    f"cron_status={cron_health_status} "
    f"cron_detail={cron_health_detail or 'none'}"
)
print(
    "paper proof nightly automation: "
    f"status={nightly_status} "
    f"lock_status={nightly_lock_status} "
    f"pid={nightly_pid} "
    f"source={nightly_source} "
    f"age_minutes={nightly_age_minutes} "
    f"log_age_minutes={nightly_log_age_minutes} "
    f"active_log={nightly_active_log} "
    f"max_age_minutes={nightly_max_age_minutes} "
    f"stall_minutes={nightly_stall_minutes} "
    f"run_age_limit_status={nightly_run_age_limit_status} "
    f"log_stall_status={nightly_log_stall_status} "
    f"stage={nightly_stage or 'none'} "
    f"second_strategy_scan_status={second_strategy_scan_status} "
    f"second_strategy_scan_detail={second_strategy_scan_detail} "
    f"detail={nightly_detail or 'none'}"
)
print(
    "paper proof runtime: "
    f"ops_status={ops_health_status} "
    f"ops_detail={ops_health_detail or 'none'} "
    f"profit_lock_pause={str(profit_lock_pause).lower()} "
    f"proof_risk_lock_pause={str(proof_risk_lock_pause).lower()} "
    f"image_status={runtime_image_health_status} "
    f"image_detail={runtime_image_health_detail or 'none'}"
)
print(
    "paper proof stream: "
    f"status={stream_status} "
    f"latest_start={latest_stream_started_text} "
    f"latest_event={latest_stream_event_text} "
    f"latest_supervisor_started_at={latest_supervisor_started_text} "
    f"grace_seconds={stream_start_grace_seconds}"
)
print(
    "paper proof readiness audit: "
    f"status={readiness_audit_status} "
    f"target_session={readiness_target_session.isoformat()} "
    f"due={str(readiness_due).lower()} "
    f"target_session_completed={str(readiness_target_session_completed).lower()} "
    f"stale_blocks_proof={str(readiness_stale_blocks_proof).lower()} "
    f"due_after={readiness_due_after} "
    f"required_since={readiness_required_since_text} "
    f"check_status={readiness_audit_check_status} "
    f"created_at={readiness_audit_created_text} "
    f"age_minutes={readiness_audit_age_text} "
    f"max_age_minutes={readiness_max_pass_age_minutes} "
    f"latest_supervisor_started_at={latest_supervisor_started_text}"
)
print(
    "paper proof readiness decision dry run: "
    f"status={readiness_decision_dry_run_status} "
    f"strategy={readiness_decision_dry_run_strategy or 'none'} "
    f"as_of={readiness_decision_dry_run_as_of or 'none'} "
    f"required_as_of_session={readiness_expected_decision_dry_run_session.isoformat() if readiness_expected_decision_dry_run_session else 'unknown'} "
    f"active={readiness_decision_dry_run_active or 'none'} "
    f"required_active={min_watchlist_symbols} "
    f"decision_records={readiness_decision_dry_run_records or 'none'} "
    f"required_records={min_decision_dry_run_records} "
    f"accepted={readiness_decision_dry_run_accepted or 'none'} "
    f"entry_intents={readiness_decision_dry_run_entry_intents or 'none'} "
    f"sample={readiness_decision_dry_run_sample or 'none'} "
    f"sample_times={readiness_decision_dry_run_sample_times or 'none'} "
    f"evaluations={readiness_decision_dry_run_evaluations or 'none'} "
    f"required_evaluations={min_decision_dry_run_evaluations} "
    f"min_decision_records={readiness_decision_dry_run_min_records or 'none'} "
    f"max_accepted={readiness_decision_dry_run_max_accepted or 'none'} "
    f"max_entry_intents={readiness_decision_dry_run_max_entry_intents or 'none'} "
    f"reject_stages={readiness_decision_dry_run_reject_stages or 'none'} "
    f"reject_reasons={readiness_decision_dry_run_reject_reasons or 'none'}"
)
print(
    "paper proof readiness decision dry run strategies: "
    f"status={readiness_decision_dry_run_strategies_status} "
    f"strategies={readiness_decision_dry_run_strategies or 'none'} "
    f"expected={format_name_list(expected_readiness_decision_dry_run_strategy_names)} "
    f"count={readiness_decision_dry_run_strategy_count or 'none'} "
    f"expected_count={len(expected_readiness_decision_dry_run_strategy_names)}"
)
print(
    "paper proof activity audit: "
    f"status={activity_audit_status} "
    f"target_session={activity_target_session.isoformat() if activity_target_session else 'none'} "
    f"due={str(activity_due).lower()} "
    f"due_after={activity_due_after} "
    f"required_since={activity_required_since_text} "
    f"check={activity_check_status}:{activity_check_exit_code}:{activity_check_created_text}"
)
print(
    "paper proof post-close audit: "
    f"status={post_close_audit_status} "
    f"target_session={post_close_target_session.isoformat() if post_close_target_session else 'none'} "
    f"due={str(post_close_due).lower()} "
    f"due_after={post_close_due_after} "
    f"required_since={post_close_required_since_text} "
    f"session_guard={post_close_check_statuses['session_guard']} "
    f"paper_profit_probe={post_close_check_statuses['paper_profit_probe']} "
    f"paper_profit_probe_reason={paper_profit_probe_reason}"
)
print(f"paper proof active strategies: {active_strategies or 'none'}")
print(
    "paper proof strategy status: "
    f"status={strategy_status} "
    f"target={strategy_name} "
    f"strategies={proof_strategy_csv} "
    f"approved={str(strategy_name in approved_strategy_name_set).lower()} "
    f"approved_filter={str(not proof_strategy_unapproved_names).lower()} "
    f"active_filter={str(not proof_strategy_missing_active_names).lower()} "
    f"missing_active={format_name_list(proof_strategy_missing_active_names)} "
    f"unapproved={format_name_list(proof_strategy_unapproved_names)} "
    f"active=[{active_strategies or ''}]"
)
print(
    "paper proof strategy diversification: "
    f"status={strategy_diversification_status} "
    f"active={len(active_strategy_names)} "
    f"required={scale_min_strategies} "
    f"approved_active={len(approved_active_strategy_names)} "
    f"approved_replay_active={len(approved_replay_active_strategy_names)} "
    f"approved_required={scale_min_strategies} "
    f"gap={strategy_diversification_gap} "
    f"candidate_status={strategy_diversification_candidate_status} "
    f"promotion_action_status={strategy_diversification_promotion_action_status} "
    f"approval_marker_action_status={approval_marker_action_status} "
    f"promotion_write_access_status={safe_status_value(promotion_write_access_status)} "
    f"active_names={active_strategies or 'none'} "
    f"approved_names={approved_active_strategies or 'none'} "
    f"approved_replay_names={approved_replay_active_strategies or 'none'} "
    f"unapproved_active={unapproved_active_strategies or 'none'} "
    f"replay_unsupported_active={active_replay_unsupported_strategies or 'none'} "
    f"approved_allowlist={approved_strategy_allowlist or 'none'} "
    f"disabled_candidates={len(disabled_strategy_names)} "
    f"disabled_candidate_names={disabled_strategies or 'none'} "
    f"replay_supported_active={len(active_replay_supported_strategy_names)} "
    f"replay_supported_disabled_candidates={len(disabled_replay_supported_strategy_names)} "
    f"replay_supported_disabled_candidate_names={format_name_list(disabled_replay_supported_strategy_names)} "
    f"replay_unsupported_disabled_candidates={len(disabled_replay_unsupported_strategy_names)} "
    f"replay_unsupported_disabled_candidate_names={format_name_list(disabled_replay_unsupported_strategy_names)} "
    f"stock_active={len(active_stock_strategy_names)} "
    f"option_active={len(active_option_strategy_names)} "
    f"option_replay_status={option_replay_status} "
    f"stock_disabled_candidates={len(disabled_stock_strategy_names)} "
    f"stock_disabled_candidate_names={format_name_list(disabled_stock_strategy_names)} "
    f"option_gated_disabled_candidates={len(option_gated_disabled_strategy_names)} "
    f"option_gated_disabled_candidate_names={format_name_list(option_gated_disabled_strategy_names)} "
    f"approved_disabled_stock_candidates={format_name_list(approved_disabled_stock_candidate_names)} "
    f"approved_disabled_option_candidates={format_name_list(approved_disabled_option_candidate_names)} "
    f"validated_unapproved_stock_candidates={format_name_list(validated_unapproved_stock_candidate_names)} "
    f"validated_unapproved_option_candidates={format_name_list(validated_unapproved_option_candidate_names)}"
)
print(
    "paper proof option snapshots: "
    f"status={option_snapshot_status} "
    f"replay_status={option_replay_status} "
    f"due={str(option_snapshot_due).lower()} "
    f"target_session={option_snapshot_target_session.isoformat() if option_snapshot_target_session else 'none'} "
    f"due_after={option_snapshot_due_after} "
    f"path={safe_status_value(option_snapshot_summary['path'])} "
    f"files={option_snapshot_summary['file_count']} "
    f"latest_file={safe_status_value(option_snapshot_summary['latest_file'])} "
    f"latest_session={safe_status_value(option_snapshot_summary['latest_session'])} "
    f"latest_modified={safe_status_value(option_snapshot_summary['latest_modified'])} "
    f"latest_bytes={option_snapshot_summary['latest_bytes']} "
    f"latest_contracts={option_snapshot_summary['latest_contracts']} "
    f"snapshot_sessions={option_snapshot_summary['snapshot_session_count']} "
    f"replay_sessions={option_snapshot_summary['replay_session_count']} "
    f"required_replay_sessions={option_replay_min_sessions} "
    f"min_points_per_session={option_snapshot_summary['min_points_per_session']} "
    f"session_points={safe_status_value(option_snapshot_summary['session_points'])} "
    f"undercovered_sessions={safe_status_value(option_snapshot_summary['undercovered_sessions'])} "
    f"earliest_session={safe_status_value(option_snapshot_summary['earliest_session'])} "
    f"symbols={len(settings.option_chain_symbols)}"
)
print(
    "paper proof second strategy evidence: "
    f"status={second_strategy_evidence['status']} "
    f"candidate_status={second_strategy_evidence['candidate_status']} "
    f"detail={safe_status_value(second_strategy_evidence['detail'])} "
    f"root={safe_status_value(second_strategy_evidence['root'])} "
    f"prefilter_summary={safe_status_value(second_strategy_evidence['prefilter_summary'])} "
    f"validation_summary={safe_status_value(second_strategy_evidence['validation_summary'])} "
    f"validation_prefilter_summary={safe_status_value(second_strategy_evidence['validation_prefilter_summary'])} "
    f"validation_prefilter_summary_sha256={safe_status_value(second_strategy_evidence['validation_prefilter_summary_sha256'])} "
    f"validation_prefilter_lineage_status={safe_status_value(second_strategy_evidence['validation_prefilter_lineage_status'])} "
    f"fractionability_lineage_status={safe_status_value(second_strategy_evidence['fractionability_lineage_status'])} "
    f"fractionability_snapshot_sha256={safe_status_value(second_strategy_evidence['fractionability_snapshot_sha256'])} "
    f"fractionability_universe_sha256={safe_status_value(second_strategy_evidence['fractionability_universe_sha256'])} "
    f"proof_horizon_summary={safe_status_value(second_strategy_evidence['proof_horizon_summary'])} "
    f"prefilter_summary_sha256={safe_status_value(second_strategy_evidence['prefilter_summary_sha256'])} "
    f"validation_summary_sha256={safe_status_value(second_strategy_evidence['validation_summary_sha256'])} "
    f"proof_horizon_summary_sha256={safe_status_value(second_strategy_evidence['proof_horizon_summary_sha256'])} "
    f"prefilter_age_hours={format_optional_float(second_strategy_evidence['prefilter_age_hours'])} "
    f"validation_age_hours={format_optional_float(second_strategy_evidence['validation_age_hours'])} "
    f"proof_horizon_age_hours={format_optional_float(second_strategy_evidence['proof_horizon_age_hours'])} "
    f"max_age_hours={second_strategy_evidence['max_age_hours']} "
    f"proof_horizon_status={second_strategy_evidence['proof_horizon_status']} "
    f"proof_horizon_detail={safe_status_value(second_strategy_evidence['proof_horizon_detail'])} "
    f"proof_horizon_strategy={safe_status_value(second_strategy_evidence['proof_horizon_strategy'])} "
    f"proof_horizon_trades={safe_status_value(second_strategy_evidence['proof_horizon_trades'])} "
    f"proof_horizon_total_pnl={format_optional_float(second_strategy_evidence['proof_horizon_total_pnl'], 2)} "
    f"proof_horizon_eventual_pass_rate={format_optional_float(second_strategy_evidence['proof_horizon_eventual_pass_rate'], 4)} "
    f"proof_horizon_min_pass_rate={format_optional_float(second_strategy_evidence['proof_horizon_min_pass_rate'], 4)} "
    f"proof_horizon_selection_reason={safe_status_value(second_strategy_evidence['proof_horizon_selection_reason'])} "
    f"proof_horizon_candidate_count={safe_status_value(second_strategy_evidence['proof_horizon_candidate_count'])} "
    f"proof_horizon_passing_candidate_count={safe_status_value(second_strategy_evidence['proof_horizon_passing_candidate_count'])} "
    f"proof_horizon_confidence_scales={safe_status_value(second_strategy_evidence['proof_horizon_confidence_scales'])} "
    f"proof_horizon_candidate_scale={format_optional_float(second_strategy_evidence['proof_horizon_candidate_scale'], 4)} "
    f"proof_horizon_starts_eventually_passed={safe_status_value(second_strategy_evidence['proof_horizon_starts_eventually_passed'])} "
    f"proof_horizon_historical_starts={safe_status_value(second_strategy_evidence['proof_horizon_historical_starts'])} "
    f"proof_horizon_terminal_blockers={safe_status_value(second_strategy_evidence['proof_horizon_terminal_blockers'])} "
    f"prefilter_families={len(second_strategy_evidence['prefilter_families'])} "
    f"prefilter_family_names={format_name_list(second_strategy_evidence['prefilter_families'])} "
    f"prefilter_positive_rows={second_strategy_evidence['prefilter_positive_rows']} "
    f"prefilter_positive_families={len(second_strategy_evidence['prefilter_positive_families'])} "
    f"prefilter_positive_family_names={format_name_list(second_strategy_evidence['prefilter_positive_families'])} "
    f"validated_families={len(second_strategy_evidence['validated_families'])} "
    f"validated_family_names={format_name_list(second_strategy_evidence['validated_families'])} "
    f"missing_validation_families={format_name_list(second_strategy_evidence['missing_validation_families'])} "
    f"validation_rows={second_strategy_evidence['validation_rows']} "
    f"validation_positive_rows={second_strategy_evidence['validation_positive_rows']} "
    f"validation_positive_family_names={format_name_list(second_strategy_evidence['validation_positive_families'])} "
    f"validation_denied_family_names={format_name_list(second_strategy_evidence['validation_denied_families'])} "
    f"promotion_denylist={format_name_list(second_strategy_evidence['promotion_denylist'])} "
    f"promotion_candidate_denied={str(second_strategy_evidence['promotion_candidate_denied']).lower()} "
    f"promotion_approved={str(second_strategy_evidence['promotion_approved']).lower()} "
    f"promotion_approved_source={second_strategy_evidence['promotion_approved_source']} "
    f"promotion_approval_marker_status={second_strategy_evidence['promotion_approval_marker_status']} "
    f"promotion_approval_marker_strategy={safe_status_value(second_strategy_evidence['promotion_approval_marker_strategy'])} "
    f"max_validation_candidates={safe_status_value(second_strategy_evidence['max_validation_candidates'])} "
    f"validation_verdicts={second_strategy_evidence['validation_verdicts']}"
)
promotion_strategy = safe_status_value(second_strategy_evidence["promotion_candidate"])
promotion_validation_summary_sha256 = safe_status_value(
    second_strategy_evidence["validation_summary_sha256"]
)
promotion_proof_horizon_summary_sha256 = safe_status_value(
    second_strategy_evidence["proof_horizon_summary_sha256"]
)
promotion_confirmation = (
    f"approve-{promotion_strategy}-paper-promotion-sha256-{promotion_validation_summary_sha256}"
    f"-proof-sha256-{promotion_proof_horizon_summary_sha256}"
    if (
        promotion_strategy != "none"
        and not second_strategy_evidence["promotion_candidate_denied"]
        and promotion_validation_summary_sha256 != "none"
        and promotion_proof_horizon_summary_sha256 != "none"
    )
    else "none"
)
promotion_broker_flat_status = (
    "unknown"
    if broker_exposure_warning
    else "ok"
    if (broker_open_orders or 0) == 0 and (broker_open_positions or 0) == 0
    else "not_flat"
)
approval_marker_command_status = approval_marker_action_status
if approval_marker_action_status == "ready":
    if promotion_confirmation == "none":
        approval_marker_command_status = "missing_confirmation"
    elif promotion_broker_flat_status != "ok":
        approval_marker_command_status = f"broker_{promotion_broker_flat_status}"
    else:
        approval_marker_command_status = "ready"
approval_marker_quick_command = "unavailable"
if approval_marker_command_status == "ready":
    approval_marker_quick_command = " ".join(
        [
            "APPROVE_VALIDATED_STRATEGY_MARKER_CONFIRM="
            f"{shlex.quote(promotion_confirmation)}",
            "APPROVE_VALIDATED_STRATEGY_MARKER_DRY_RUN=false",
            shlex.quote("./scripts/approve_validated_strategy_marker.sh"),
            shlex.quote(promotion_strategy),
            "&&",
            shlex.quote("./scripts/deploy.sh"),
            shlex.quote(proof_status_env_file),
        ]
    )
print(
    "paper proof second strategy promotion action: "
    f"status={promotion_action_status} "
    f"strategy={promotion_strategy} "
    f"strategy_denied={str(second_strategy_evidence['promotion_candidate_denied']).lower()} "
    f"promotion_denylist={format_name_list(second_strategy_evidence['promotion_denylist'])} "
    f"confirmation={promotion_confirmation} "
    f"script=./scripts/promote_validated_strategy.sh "
    f"dry_run_default=true "
    f"mutation_requires_dry_run_false=true "
    f"approval_marker_only_supported=true "
    f"approval_marker_action_status={approval_marker_action_status} "
    f"approval_marker_command_status={approval_marker_command_status} "
    f"approval_marker_command_script=./scripts/approve_validated_strategy_marker.sh "
    f"approval_marker_command_confirm_env=APPROVE_VALIDATED_STRATEGY_MARKER_CONFIRM "
    f"approval_marker_command_dry_run_env=APPROVE_VALIDATED_STRATEGY_MARKER_DRY_RUN "
    f"approval_marker_command_dry_run_value=false "
    f"approval_marker_command_approval_only_env=PROMOTE_VALIDATED_STRATEGY_APPROVAL_ONLY "
    f"approval_marker_command_approval_only_value=true "
    f"approval_marker_command_evidence_root={safe_status_value(second_strategy_evidence['root'])} "
    f"approval_marker_command_deploy_script=./scripts/deploy.sh "
    f"candidate_decision_dry_run_required=true "
    f"candidate_decision_dry_run_allow_disabled=true "
    f"candidate_decision_dry_run_script=./scripts/paper_decision_dry_run.sh "
    f"approval_marker_overlay_status={approval_marker_overlay_status} "
    f"approval_marker_overlay_marker={safe_status_value(approval_marker_overlay_marker)} "
    f"approval_marker_overlay_env_file={safe_status_value(approval_marker_overlay_env_file)} "
    f"broker_flat_status={promotion_broker_flat_status} "
    f"env_file={safe_status_value(proof_status_env_file)} "
    f"write_access_status={safe_status_value(promotion_write_access_status)} "
    f"promotion_handoff_status={promotion_handoff_status} "
    f"promotion_handoff_step={promotion_handoff_step} "
    f"promotion_env_keys={promotion_env_keys_csv} "
    f"env_file_writable={safe_status_value(promotion_env_file_writable)} "
    f"env_dir_writable={safe_status_value(promotion_env_dir_writable)} "
    f"approval_marker={safe_status_value(second_strategy_evidence['promotion_approval_marker'])} "
    f"approval_marker_writable={safe_status_value(promotion_approval_marker_writable)} "
    f"approval_marker_dir_writable={safe_status_value(promotion_approval_marker_dir_writable)} "
    f"approval_marker_status={second_strategy_evidence['promotion_approval_marker_status']} "
    f"validation_summary={safe_status_value(second_strategy_evidence['validation_summary'])} "
    f"validation_summary_sha256={safe_status_value(second_strategy_evidence['validation_summary_sha256'])} "
    f"proof_horizon_status={second_strategy_evidence['proof_horizon_status']} "
    f"proof_horizon_detail={safe_status_value(second_strategy_evidence['proof_horizon_detail'])} "
    f"proof_horizon_summary={safe_status_value(second_strategy_evidence['proof_horizon_summary'])} "
    f"proof_horizon_summary_sha256={safe_status_value(second_strategy_evidence['proof_horizon_summary_sha256'])} "
    f"proof_horizon_total_pnl={format_optional_float(second_strategy_evidence['proof_horizon_total_pnl'], 2)} "
    f"proof_horizon_eventual_pass_rate={format_optional_float(second_strategy_evidence['proof_horizon_eventual_pass_rate'], 4)} "
    f"proof_horizon_min_pass_rate={format_optional_float(second_strategy_evidence['proof_horizon_min_pass_rate'], 4)} "
    f"proof_horizon_selection_reason={safe_status_value(second_strategy_evidence['proof_horizon_selection_reason'])} "
    f"proof_horizon_candidate_count={safe_status_value(second_strategy_evidence['proof_horizon_candidate_count'])} "
    f"proof_horizon_passing_candidate_count={safe_status_value(second_strategy_evidence['proof_horizon_passing_candidate_count'])} "
    f"proof_horizon_confidence_scales={safe_status_value(second_strategy_evidence['proof_horizon_confidence_scales'])} "
    f"proof_horizon_candidate_scale={format_optional_float(second_strategy_evidence['proof_horizon_candidate_scale'], 4)} "
    f"proof_horizon_terminal_blockers={safe_status_value(second_strategy_evidence['proof_horizon_terminal_blockers'])} "
    f"candidate_scale={safe_status_value(second_strategy_evidence['promotion_candidate_scale'])} "
    f"candidate_trades={safe_status_value(second_strategy_evidence['promotion_candidate_trades'])} "
    f"candidate_total_pnl={format_optional_float(second_strategy_evidence['promotion_candidate_total_pnl'], 2)} "
    f"candidate_ci_low={format_optional_float(second_strategy_evidence['promotion_candidate_ci_low'], 4)} "
    f"candidate_p_mean_le_zero={format_optional_float(second_strategy_evidence['promotion_candidate_p_mean_le_zero'], 4)}"
)
print(
    "paper proof second strategy approval quick command: "
    f"status={approval_marker_command_status} "
    f"command={approval_marker_quick_command}"
)
print(
    "paper proof second strategy setup evidence: "
    f"status={second_strategy_setup_evidence['status']} "
    f"candidate_status={second_strategy_setup_evidence['candidate_status']} "
    f"detail={safe_status_value(second_strategy_setup_evidence['detail'])} "
    f"root={safe_status_value(second_strategy_setup_evidence['root'])} "
    f"prefilter_summary={safe_status_value(second_strategy_setup_evidence['prefilter_summary'])} "
    f"validation_summary={safe_status_value(second_strategy_setup_evidence['validation_summary'])} "
    f"validation_prefilter_summary={safe_status_value(second_strategy_setup_evidence['validation_prefilter_summary'])} "
    f"validation_prefilter_summary_sha256={safe_status_value(second_strategy_setup_evidence['validation_prefilter_summary_sha256'])} "
    f"validation_prefilter_lineage_status={safe_status_value(second_strategy_setup_evidence['validation_prefilter_lineage_status'])} "
    f"fractionability_lineage_status={safe_status_value(second_strategy_setup_evidence['fractionability_lineage_status'])} "
    f"fractionability_snapshot_sha256={safe_status_value(second_strategy_setup_evidence['fractionability_snapshot_sha256'])} "
    f"fractionability_universe_sha256={safe_status_value(second_strategy_setup_evidence['fractionability_universe_sha256'])} "
    f"prefilter_summary_sha256={safe_status_value(second_strategy_setup_evidence['prefilter_summary_sha256'])} "
    f"validation_summary_sha256={safe_status_value(second_strategy_setup_evidence['validation_summary_sha256'])} "
    f"prefilter_age_hours={format_optional_float(second_strategy_setup_evidence['prefilter_age_hours'])} "
    f"validation_age_hours={format_optional_float(second_strategy_setup_evidence['validation_age_hours'])} "
    f"max_age_hours={second_strategy_setup_evidence['max_age_hours']} "
    f"prefilter_families={len(second_strategy_setup_evidence['prefilter_families'])} "
    f"prefilter_family_names={format_name_list(second_strategy_setup_evidence['prefilter_families'])} "
    f"prefilter_positive_rows={second_strategy_setup_evidence['prefilter_positive_rows']} "
    f"prefilter_positive_families={len(second_strategy_setup_evidence['prefilter_positive_families'])} "
    f"prefilter_positive_family_names={format_name_list(second_strategy_setup_evidence['prefilter_positive_families'])} "
    f"validated_families={len(second_strategy_setup_evidence['validated_families'])} "
    f"validated_family_names={format_name_list(second_strategy_setup_evidence['validated_families'])} "
    f"missing_validation_families={format_name_list(second_strategy_setup_evidence['missing_validation_families'])} "
    f"validation_rows={second_strategy_setup_evidence['validation_rows']} "
    f"validation_positive_rows={second_strategy_setup_evidence['validation_positive_rows']} "
    f"validation_positive_family_names={format_name_list(second_strategy_setup_evidence['validation_positive_families'])} "
    f"promotion_approved={str(second_strategy_setup_evidence['promotion_approved']).lower()} "
    f"max_validation_candidates={safe_status_value(second_strategy_setup_evidence['max_validation_candidates'])} "
    f"validation_verdicts={second_strategy_setup_evidence['validation_verdicts']}"
)
print(
    "paper proof watchlist: "
    f"status={watchlist_status} "
    f"active={active_watchlist_symbols} "
    f"enabled={enabled_watchlist_symbols} "
    f"ignored={ignored_watchlist_symbols} "
    f"required_active={min_watchlist_symbols}"
)
print(
    "paper proof scenarios: "
    f"status={scenario_status} "
    f"active={len(active_watchlist_symbol_names)} "
    f"expected_session={scenario_expected_session.isoformat()} "
    f"dir={scenario_dir} "
    f"problems={scenario_problem_summary}"
)
print(
    "paper proof sizing: "
    f"status={sizing_status} "
    f"confidence_floor={confidence_floor_value:g} "
    f"manual_baseline={confidence_floor_manual_baseline:g} "
    f"set_by={confidence_floor_set_by} "
    f"required_floor={min_confidence_floor:g} "
    f"weight_status={weight_status} "
    f"active_weights=[{','.join(active_strategy_names)}] "
    f"stored_weights=[{','.join(stored_weight_names)}] "
    f"weight_sum={stored_weight_sum:g} "
    f"target_weight={target_weight if target_weight is not None else 'missing'} "
    f"target_sharpe={target_sharpe if target_sharpe is not None else 'missing'}"
)
print(
    "paper proof posture: "
    f"status={posture_status} "
    f"market_data_feed={settings.market_data_feed.value} "
    f"daily_sma_period={settings.daily_sma_period} "
    f"breakout_lookback_bars={settings.breakout_lookback_bars} "
    f"relative_volume_lookback_bars={settings.relative_volume_lookback_bars} "
    f"relative_volume_threshold={settings.relative_volume_threshold:g} "
    f"entry_timeframe_minutes={settings.entry_timeframe_minutes} "
    f"entry_order_active_bars={settings.entry_order_active_bars} "
    f"risk_per_trade_pct={settings.risk_per_trade_pct:g} "
    f"max_position_pct={settings.max_position_pct:g} "
    f"max_open_positions={settings.max_open_positions} "
    f"max_portfolio_exposure_pct={settings.max_portfolio_exposure_pct:g} "
    f"daily_loss_limit_pct={settings.daily_loss_limit_pct:g} "
    f"stop_limit_buffer_pct={settings.stop_limit_buffer_pct:g} "
    f"entry_stop_price_buffer={settings.entry_stop_price_buffer:g} "
    f"entry_min_close_to_entry_pct={settings.entry_min_close_to_entry_pct:g} "
    f"entry_max_close_to_entry_pct={settings.entry_max_close_to_entry_pct:g} "
    f"atr_period={settings.atr_period} "
    f"atr_stop_multiplier={settings.atr_stop_multiplier:g} "
    f"trailing_stop_atr_multiplier={settings.trailing_stop_atr_multiplier:g} "
    f"trailing_stop_profit_trigger_r={settings.trailing_stop_profit_trigger_r:g} "
    f"bull_flag_min_run_pct={settings.bull_flag_min_run_pct:g} "
    f"bull_flag_consolidation_volume_ratio={settings.bull_flag_consolidation_volume_ratio:g} "
    f"bull_flag_consolidation_range_pct={settings.bull_flag_consolidation_range_pct:g} "
    f"entry_window_start={as_hhmm(settings.entry_window_start)} "
    f"entry_window_end={as_hhmm(settings.entry_window_end)} "
    f"flatten_time={as_hhmm(settings.flatten_time)} "
    f"vwap_filter={str(settings.enable_vwap_entry_filter).lower()} "
    f"profit_trail={str(settings.enable_profit_trail).lower()} "
    f"profit_trail_pct={settings.profit_trail_pct:g} "
    f"breakeven_stop={str(settings.enable_breakeven_stop).lower()} "
    f"breakeven_trigger_pct={settings.breakeven_trigger_pct:g} "
    f"breakeven_trail_pct={settings.breakeven_trail_pct:g} "
    f"vix_filter={str(settings.enable_vix_filter).lower()} "
    f"sector_filter={str(settings.enable_sector_filter).lower()} "
    f"regime_filter={str(settings.enable_regime_filter).lower()} "
    f"news_filter={str(settings.enable_news_filter).lower()} "
    f"spread_filter={str(settings.enable_spread_filter).lower()} "
    f"options_trading={str(settings.enable_options_trading).lower()} "
    f"option_chain_symbols={','.join(settings.option_chain_symbols) if settings.option_chain_symbols else 'none'} "
    f"extended_hours={str(settings.extended_hours_enabled).lower()} "
    f"profit_target={str(settings.enable_profit_target).lower()} "
    f"profit_target_r={settings.profit_target_r:g} "
    f"trend_filter_exit={str(settings.enable_trend_filter_exit).lower()} "
    f"vwap_breakdown_exit={str(settings.enable_vwap_breakdown_exit).lower()} "
    f"no_follow_through_exit={str(settings.enable_no_follow_through_exit).lower()} "
    f"no_follow_through_exit_minutes={settings.no_follow_through_exit_minutes} "
    f"no_follow_through_min_favorable_pct={settings.no_follow_through_min_favorable_pct:g} "
    f"giveback_exit={str(settings.enable_giveback_exit).lower()} "
    f"giveback_exit_min_favorable_pct={settings.giveback_exit_min_favorable_pct:g} "
    f"giveback_exit_max_return_pct={settings.giveback_exit_max_return_pct:g} "
    f"early_loss_exit={str(settings.enable_early_loss_exit).lower()} "
    f"early_loss_exit_minutes={settings.early_loss_exit_minutes} "
    f"early_loss_exit_return_pct={settings.early_loss_exit_return_pct:g} "
    f"per_symbol_loss_limit_pct={settings.per_symbol_loss_limit_pct:g} "
    f"min_position_notional={settings.min_position_notional:g} "
    f"max_stop_pct={settings.max_stop_pct:g} "
    f"viability_daily_bar_max_age_days={settings.viability_daily_bar_max_age_days} "
    f"viability_min_hold_minutes={settings.viability_min_hold_minutes} "
    f"max_loss_per_trade_dollars={settings.max_loss_per_trade_dollars if settings.max_loss_per_trade_dollars is not None else 'none'} "
    f"paper_proof_freeze={str(settings.paper_proof_freeze).lower()} "
    f"intraday_consecutive_loss_gate={settings.intraday_consecutive_loss_gate} "
    f"replay_slippage_bps={settings.replay_slippage_bps:g}"
)
print(
    "paper proof local exposure: "
    f"positions={local_open_positions} "
    f"active_orders={local_active_orders} "
    f"position_symbols={local_open_position_symbols or 'none'} "
    f"active_order_symbols={local_active_order_symbols or 'none'}"
)
print(
    "paper proof exposure protection: "
    f"status={exposure_protection_status} "
    f"issues={exposure_protection_issue_text} "
    f"local_positions={local_open_positions} "
    f"local_stop_orders={local_active_stop_orders} "
    f"local_entry_orders={local_active_entry_orders} "
    f"broker_positions={broker_open_positions if broker_open_positions is not None else 'unknown'} "
    f"broker_orders={broker_open_orders if broker_open_orders is not None else 'unknown'} "
    f"symbols={local_open_position_symbols or 'none'}"
)
print(
    "paper proof option exposure: "
    f"net_open={local_open_option_positions} "
    f"active_orders={local_active_option_orders} "
    f"net_open_symbols={local_open_option_symbols or 'none'} "
    f"active_order_symbols={local_active_option_order_symbols or 'none'}"
)
if broker_exposure_warning:
    print(f"paper proof broker exposure warning: {broker_exposure_warning}")
else:
    print(
        "paper proof broker exposure: "
        f"open_orders={broker_open_orders} "
        f"open_positions={broker_open_positions} "
        f"open_order_symbols={broker_open_order_symbols or 'none'} "
        f"open_position_symbols={broker_open_position_symbols or 'none'}"
    )
    print(
        "paper proof broker account: "
        f"status={broker_account_status} "
        f"equity={broker_equity:.2f} "
        f"buying_power={broker_buying_power:.2f} "
        f"minimum_required={broker_minimum_buying_power:.2f} "
        f"trading_blocked={str(broker_trading_blocked).lower()}"
    )
if calendar_warning:
    print(f"paper proof calendar warning: {calendar_warning}")
print(
    "paper proof calendar: "
    f"current_market_date={current_market_date.isoformat()} "
    f"latest_completed_session={latest_completed_session.isoformat() if latest_completed_session else 'unknown'} "
    f"scoring_end_date={proof_end.isoformat()}"
)
if scheduled_checks:
    for check_name, status, exit_code, session_date, check_proof_start, created_at in scheduled_checks:
        print(
            "paper proof scheduled check: "
            f"name={check_name} status={status or 'unknown'} "
            f"exit_code={exit_code or 'unknown'} "
            f"session_date={session_date or 'unknown'} "
            f"proof_start={check_proof_start or 'unknown'} "
            f"created_at={created_at or 'unknown'}"
        )
else:
    print("paper proof scheduled checks: none")
print(
    "paper proof progress: "
    f"status={proof_status} "
    f"strategies={proof_strategy_csv} "
    f"closed_trades={trade_count} "
    f"required_trades={min_trades} "
    f"pnl={pnl:.2f} "
    f"required_pnl={min_pnl:.2f} "
    f"basis={proof_basis} "
    f"sealed_basis={sealed_proof_basis} "
    f"window={proof_window} "
    f"first_exit_session={first_exit_session or 'none'} "
    f"latest_exit_session={latest_exit_session or 'none'}"
)
print(
    "paper proof blocker gaps: "
    f"sample_trades_remaining={sample_trades_remaining} "
    f"active_days_remaining={active_days_remaining} "
    f"approved_replay_strategy_gap={strategy_diversification_gap} "
    f"concentration_net_pnl_needed={concentration_net_pnl_needed:.2f} "
    f"concentration_non_best_avg_pnl={non_best_avg_trade_pnl_text} "
    f"concentration_non_best_avg_trade_gap={concentration_non_best_avg_trade_gap_text} "
    f"concentration_runway_status={concentration_runway_status} "
    f"concentration_remaining_trade_required_avg_pnl={concentration_remaining_trade_required_avg_pnl_text} "
    f"concentration_remaining_active_day_required_pnl={concentration_remaining_active_day_required_pnl_text} "
    f"single_win_pnl_share={single_win_pnl_share_text} "
    f"max_single_win_pnl_share={scale_max_single_win_pnl_share:.2f}"
)
print(
    "paper proof active day detail: "
    f"status={'ok' if active_days_remaining == 0 else 'blocked'} "
    f"active_days={active_trade_day_count} "
    f"required_active_days={scale_min_active_days} "
    f"active_days_remaining={active_days_remaining} "
    f"sample_trades_remaining={sample_trades_remaining} "
    f"remaining_trades_per_required_active_day={remaining_trades_per_required_active_day_text} "
    f"sessions={active_trade_sessions_text} "
    f"trades_by_session={trade_count_by_session_text} "
    f"latest_exit_session={latest_exit_session or 'none'} "
    f"next_possible_session={next_possible_active_session_text} "
    f"future_sessions={active_day_future_sessions_text} "
    f"earliest_active_days_met_session={earliest_active_days_met_session_text} "
    f"projection_status={active_day_projection_status} "
    f"projection_warning={safe_status_value(active_day_projection_warning)}"
)
print(
    "paper proof concentration: "
    f"status={concentration_status} "
    f"best_winning_trade={safe_status_value(best_winning_trade_text)} "
    f"best_winning_trade_pnl={best_winning_trade_pnl:.2f} "
    f"total_pnl={pnl:.2f} "
    f"non_best_trades={non_best_trade_count} "
    f"non_best_pnl={non_best_trade_pnl_text} "
    f"non_best_avg_pnl={non_best_avg_trade_pnl_text} "
    f"net_pnl_needed={concentration_net_pnl_needed:.2f} "
    f"non_best_avg_trade_gap={concentration_non_best_avg_trade_gap_text} "
    f"runway_status={concentration_runway_status} "
    f"remaining_trade_required_avg_pnl={concentration_remaining_trade_required_avg_pnl_text} "
    f"remaining_active_day_required_pnl={concentration_remaining_active_day_required_pnl_text} "
    f"single_win_pnl_share={single_win_pnl_share_text} "
    f"max_single_win_pnl_share={scale_max_single_win_pnl_share:.2f}"
)
print(
    "paper proof robustness: "
    f"scale_status={scale_status} "
    f"blockers={','.join(scale_blockers) if scale_blockers else 'none'} "
    f"trades={trade_count} "
    f"required_trades={scale_min_trades} "
    f"enabled_strategies={len(active_strategy_names)} "
    f"approved_enabled_strategies={len(approved_active_strategy_names)} "
    f"approved_replay_enabled_strategies={len(approved_replay_active_strategy_names)} "
    f"required_strategies={scale_min_strategies} "
    f"active_days={active_trade_day_count} "
    f"required_active_days={scale_min_active_days} "
    f"profit_factor={profit_factor_text} "
    f"required_profit_factor={scale_min_profit_factor:.2f} "
    f"single_win_pnl_share={single_win_pnl_share_text} "
    f"max_single_win_pnl_share={scale_max_single_win_pnl_share:.2f} "
    f"eod_losses={eod_loss_count} "
    f"eod_loss_share={eod_loss_share_text} "
    f"eod_loss_symbols={eod_loss_symbols} "
    f"max_eod_loss_share={scale_max_eod_loss_share:.2f} "
    f"operational_exit_losses={operational_exit_loss_count} "
    f"operational_exit_loss_share={operational_exit_loss_share_text} "
    f"operational_exit_loss_symbols={operational_exit_loss_symbols} "
    f"operational_exit_loss_reasons={operational_exit_loss_reasons} "
    f"max_operational_exit_loss_share={scale_max_operational_exit_loss_share:.2f}"
)
print(
    "paper proof clean window: "
    f"status={clean_window_status} "
    f"latest_operational_exit_loss={latest_operational_exit_loss_text} "
    f"clean_start_candidate={operational_exit_clean_start_text} "
    f"progress_start={clean_window_progress_start_text} "
    f"proof_eligible={str(clean_window_proof_eligible).lower()} "
    f"sealed_proof_eligible={str(clean_window_sealed_proof_eligible).lower()} "
    f"scoreable_trades={clean_window_trade_count} "
    f"scoreable_pnl={clean_window_pnl:.2f} "
    f"unscored_current_session_trades={clean_window_unscored_current_session_trade_count} "
    f"unscored_current_session_pnl={clean_window_unscored_current_session_pnl:.2f} "
    f"sealed_trades={clean_window_sealed_trade_count} "
    f"sealed_pnl={clean_window_sealed_pnl:.2f}"
)
print(
    "paper proof clean window robustness: "
    f"status={clean_window_robustness_status} "
    f"blockers={','.join(clean_window_blockers) if clean_window_blockers else 'none'} "
    f"trades={clean_window_summary['trade_count']} "
    f"active_days={clean_window_summary['active_days']} "
    f"profit_factor={clean_window_summary['profit_factor_text']} "
    f"single_win_pnl_share={clean_window_summary['single_win_pnl_share_text']} "
    f"eod_losses={clean_window_summary['eod_loss_count']} "
    f"eod_loss_share={clean_window_summary['eod_loss_share_text']} "
    f"eod_loss_symbols={clean_window_summary['eod_loss_symbols']} "
    f"max_eod_loss_share={scale_max_eod_loss_share:.2f} "
    f"operational_exit_losses={clean_window_summary['operational_exit_loss_count']} "
    f"operational_exit_loss_share={clean_window_summary['operational_exit_loss_share_text']} "
    f"operational_exit_loss_symbols={clean_window_summary['operational_exit_loss_symbols']} "
    f"sealed_status={clean_window_sealed_robustness_status} "
    f"sealed_blockers={','.join(clean_window_sealed_blockers) if clean_window_sealed_blockers else 'none'} "
    f"sealed_trades={clean_window_sealed_summary['trade_count']} "
    f"sealed_active_days={clean_window_sealed_summary['active_days']} "
    f"sealed_profit_factor={clean_window_sealed_summary['profit_factor_text']} "
    f"sealed_single_win_pnl_share={clean_window_sealed_summary['single_win_pnl_share_text']} "
    f"sealed_eod_losses={clean_window_sealed_summary['eod_loss_count']} "
    f"sealed_eod_loss_share={clean_window_sealed_summary['eod_loss_share_text']} "
    f"sealed_eod_loss_symbols={clean_window_sealed_summary['eod_loss_symbols']} "
    f"sealed_operational_exit_losses={clean_window_sealed_summary['operational_exit_loss_count']} "
    f"sealed_operational_exit_loss_share={clean_window_sealed_summary['operational_exit_loss_share_text']} "
    f"sealed_operational_exit_loss_symbols={clean_window_sealed_summary['operational_exit_loss_symbols']}"
)
print(
    "paper proof execution quality: "
    f"status={execution_quality_status} "
    f"warnings={','.join(execution_quality_warnings) if execution_quality_warnings else 'none'} "
    f"evaluated={decision_evaluated} "
    f"signals={decision_signal_fired} "
    f"accepted={decision_accepted} "
    f"accepted_for_fill={accepted_for_fill_count} "
    f"capacity_rejected={decision_capacity_rejected} "
    f"capacity_reject_rate={capacity_reject_rate_text} "
    f"max_capacity_reject_rate={execution_max_capacity_reject_rate:.2f} "
    f"entry_quality_rejected={decision_entry_quality_rejected} "
    f"vwap_rejected={decision_vwap_rejected} "
    f"sizing_rejected={decision_sizing_rejected} "
    f"entry_orders={entry_order_count} "
    f"filled={entry_order_filled_count} "
    f"canceled={entry_order_canceled_count} "
    f"expired={entry_order_expired_count} "
    f"rejected={entry_order_rejected_count} "
    f"active={entry_order_active_count} "
    f"maintenance_drained={entry_order_maintenance_drained_count} "
    f"short_window_drained={entry_order_short_window_drained_count} "
    f"entry_fill_rate_status={entry_fill_rate_status} "
    f"entry_fill_rate={entry_order_fill_rate_text} "
    f"min_entry_fill_rate={execution_min_entry_fill_rate:.2f} "
    f"current_posture_entry_orders={posture_entry_order_count} "
    f"current_posture_filled={posture_entry_order_filled_count} "
    f"current_posture_entry_fill_rate={posture_entry_fill_rate_text} "
    f"current_posture_would_reject={posture_entry_quality_would_reject_count} "
    f"effective_entry_fill_rate={effective_entry_fill_rate_text} "
    f"effective_entry_fill_rate_source={effective_entry_fill_rate_source} "
    f"accepted_to_fill_rate={accepted_to_fill_rate_text} "
    f"filled_symbols={entry_order_filled_symbols} "
    f"expired_symbols={entry_order_expired_symbols} "
    f"expired_reasons={entry_order_expired_reasons} "
    f"expired_signal_price_posture={entry_order_expired_signal_price_posture} "
    f"expired_next_bar_fill_causes={entry_order_expired_next_bar_fill_causes} "
    f"entry_dispatch_delay={entry_order_dispatch_delay_summary} "
    f"current_posture_entry_dispatch_delay={posture_entry_order_dispatch_delay_summary} "
    f"current_posture_filled_symbols={posture_entry_order_filled_symbols}"
)
print(
    "paper proof current-session execution: "
    f"session={current_market_date.isoformat()} "
    f"status={current_session_execution_status} "
    f"warnings={','.join(current_session_execution_warnings) if current_session_execution_warnings else 'none'} "
    f"evaluated={current_session_decision_evaluated} "
    f"signals={current_session_decision_signal_fired} "
    f"accepted={current_session_decision_accepted} "
    f"accepted_for_fill={current_session_accepted_for_fill_count} "
    f"settled_accepted_for_fill={current_session_settled_accepted_for_fill_count} "
    f"capacity_rejected={current_session_decision_capacity_rejected} "
    f"capacity_reject_rate={current_session_capacity_reject_rate_text} "
    f"max_capacity_reject_rate={execution_max_capacity_reject_rate:.2f} "
    f"entry_orders={current_session_entry_order_count} "
    f"settled={current_session_entry_order_settled_count} "
    f"settled_filled={current_session_entry_order_settled_filled_count} "
    f"filled={current_session_entry_order_filled_count} "
    f"canceled={current_session_entry_order_canceled_count} "
    f"expired={current_session_entry_order_expired_count} "
    f"rejected={current_session_entry_order_rejected_count} "
    f"active={current_session_entry_order_active_count} "
    f"maintenance_drained={current_session_entry_order_maintenance_drained_count} "
    f"short_window_drained={current_session_entry_order_short_window_drained_count} "
    f"settled_entry_fill_rate={current_session_settled_entry_fill_rate_text} "
    f"entry_fill_rate={current_session_entry_order_fill_rate_text} "
    f"min_entry_fill_rate={execution_min_entry_fill_rate:.2f} "
    f"accepted_to_fill_rate={current_session_accepted_to_fill_rate_text} "
    f"filled_symbols={current_session_entry_order_filled_symbols} "
    f"expired_symbols={current_session_entry_order_expired_symbols} "
    f"expired_reasons={current_session_entry_order_expired_reasons} "
    f"expired_signal_price_posture={current_session_entry_order_expired_signal_price_posture} "
    f"expired_next_bar_fill_causes={current_session_entry_order_expired_next_bar_fill_causes} "
    f"entry_dispatch_delay={current_session_entry_order_dispatch_delay_summary} "
    f"active_symbols={current_session_entry_order_active_symbols} "
    f"maintenance_drained_symbols={current_session_entry_order_maintenance_drained_symbols} "
    f"short_window_drained_symbols={current_session_entry_order_short_window_drained_symbols} "
    f"short_window={current_session_entry_order_short_window_count} "
    f"min_remaining_active_minutes={current_session_entry_order_min_remaining_active_minutes_text} "
    f"short_window_symbols={current_session_entry_order_short_window_symbols}"
)
print(
    "paper proof post-supervisor execution: "
    f"session={current_market_date.isoformat()} "
    f"since={post_supervisor_execution_since_text} "
    f"status={post_supervisor_execution_status} "
    f"warnings={','.join(post_supervisor_execution_warnings) if post_supervisor_execution_warnings else 'none'} "
    f"evaluated={post_supervisor_decision_evaluated} "
    f"signals={post_supervisor_decision_signal_fired} "
    f"accepted={post_supervisor_decision_accepted} "
    f"accepted_for_fill={post_supervisor_accepted_for_fill_count} "
    f"settled_accepted_for_fill={post_supervisor_settled_accepted_for_fill_count} "
    f"capacity_rejected={post_supervisor_decision_capacity_rejected} "
    f"capacity_reject_rate={post_supervisor_capacity_reject_rate_text} "
    f"max_capacity_reject_rate={execution_max_capacity_reject_rate:.2f} "
    f"entry_orders={post_supervisor_entry_order_count} "
    f"settled={post_supervisor_entry_order_settled_count} "
    f"settled_filled={post_supervisor_entry_order_settled_filled_count} "
    f"filled={post_supervisor_entry_order_filled_count} "
    f"expired={post_supervisor_entry_order_expired_count} "
    f"active={post_supervisor_entry_order_active_count} "
    f"maintenance_drained={post_supervisor_entry_order_maintenance_drained_count} "
    f"short_window_drained={post_supervisor_entry_order_short_window_drained_count} "
    f"settled_entry_fill_rate={post_supervisor_settled_entry_fill_rate_text} "
    f"entry_fill_rate={post_supervisor_entry_order_fill_rate_text} "
    f"min_entry_fill_rate={execution_min_entry_fill_rate:.2f} "
    f"accepted_to_fill_rate={post_supervisor_accepted_to_fill_rate_text} "
    f"filled_symbols={post_supervisor_entry_order_filled_symbols} "
    f"expired_symbols={post_supervisor_entry_order_expired_symbols} "
    f"expired_reasons={post_supervisor_entry_order_expired_reasons} "
    f"expired_signal_price_posture={post_supervisor_entry_order_expired_signal_price_posture} "
    f"expired_next_bar_fill_causes={post_supervisor_entry_order_expired_next_bar_fill_causes} "
    f"entry_dispatch_delay={post_supervisor_entry_order_dispatch_delay_summary} "
    f"active_symbols={post_supervisor_entry_order_active_symbols} "
    f"short_window={post_supervisor_entry_order_short_window_count} "
    f"min_remaining_active_minutes={post_supervisor_entry_order_min_remaining_active_minutes_text} "
    f"short_window_symbols={post_supervisor_entry_order_short_window_symbols}"
)
print(
    "paper proof sealed current-session progress: "
    f"closed_trades={sealed_trade_count} "
    f"scoreable_closed_trades={trade_count} "
    f"unscored_current_session_trades={unscored_current_session_trade_count} "
    f"sealed_pnl={sealed_pnl:.2f} "
    f"scoreable_pnl={pnl:.2f} "
    f"unscored_current_session_pnl={unscored_current_session_pnl:.2f} "
    f"required_trades={min_trades} "
    f"required_pnl={min_pnl:.2f}"
)
print(
    "paper proof scoring: "
    f"strategies={proof_strategy_csv} "
    f"scoreable_closed_trades={trade_count} "
    f"unpaired_filled_exits={unpaired_filled_exit_count} "
    f"unpaired_symbols={unpaired_filled_exit_symbols or 'none'}"
)
print(
    "paper proof trade quality: "
    f"wins={wins} "
    f"losses={losses} "
    f"flats={flats} "
    f"win_rate={win_rate_text} "
    f"avg_pnl={avg_trade_pnl_text} "
    f"best={best_trade_text} "
    f"worst={worst_trade_text} "
    f"recent={recent_trade_summary}"
)
if fail_on_issues and (readiness_status != "ready" or blockers):
    raise SystemExit(1)
if fail_on_issues and proof_status == "pending":
    raise SystemExit(43)
PY
