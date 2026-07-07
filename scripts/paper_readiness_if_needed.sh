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
  PAPER_READINESS_AUTO_RESUME \
  PAPER_READINESS_AUTO_RESET_WEIGHTS \
  PAPER_READINESS_CHECK_SCRIPT \
  PAPER_READINESS_CLOSE_ONLY_ON_FAILURE \
  PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS \
  PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS \
  PAPER_READINESS_DATA_SMOKE_SYMBOLS \
  PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS \
  PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS \
  PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED \
  PAPER_READINESS_DECISION_DRY_RUN_STRATEGY \
  PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES \
  PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES \
  PAPER_READINESS_AUTO_IGNORE_STALE_SCENARIOS \
  PAPER_READINESS_AUTO_IGNORE_STALE_SCENARIO_MAX \
  PAPER_READINESS_AUTO_UNIGNORE_REPAIRED_SCENARIOS \
  PAPER_READINESS_AUTO_UNIGNORE_REPAIRED_SCENARIO_MAX \
  PAPER_READINESS_FORCE_REFRESH \
  PAPER_READINESS_LOSING_STREAK_N \
  PAPER_READINESS_MAX_PASS_AGE_MINUTES \
  PAPER_READINESS_MIN_CONFIDENCE_FLOOR \
  PAPER_READINESS_MIN_WATCHLIST_SYMBOLS \
  PAPER_READINESS_PREVIOUS_SESSION_DATE \
  PAPER_READINESS_PRIOR_PROOF_START_DATE \
  PAPER_READINESS_REQUIRE_FLAT \
  PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE \
  PAPER_READINESS_REQUIRE_DECISION_DRY_RUN \
  PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR \
  PAPER_READINESS_REQUIRE_MARKET_DATA \
  PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS \
  PAPER_READINESS_REQUIRE_SCENARIOS \
  PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED \
  PAPER_READINESS_REQUIRE_WATCHLIST_ASSETS \
  PAPER_READINESS_SCENARIO_DIR \
  PAPER_READINESS_SESSION_DATE

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

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "paper readiness check skipped for TRADING_MODE=${TRADING_MODE:-unset}"
  exit 0
fi

PAPER_READINESS_CHECK_SCRIPT="${PAPER_READINESS_CHECK_SCRIPT:-./scripts/paper_readiness_check.sh}"
PAPER_READINESS_MAX_PASS_AGE_MINUTES="${PAPER_READINESS_MAX_PASS_AGE_MINUTES:-180}"
if [[ ! "$PAPER_READINESS_MAX_PASS_AGE_MINUTES" =~ ^[0-9]+$ || "$PAPER_READINESS_MAX_PASS_AGE_MINUTES" -le 0 ]]; then
  echo "PAPER_READINESS_MAX_PASS_AGE_MINUTES must be a positive integer" >&2
  exit 1
fi
PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}"
if [[ ! "$PAPER_READINESS_MIN_WATCHLIST_SYMBOLS" =~ ^[0-9]+$ || "$PAPER_READINESS_MIN_WATCHLIST_SYMBOLS" -lt 1 ]]; then
  echo "PAPER_READINESS_MIN_WATCHLIST_SYMBOLS must be a positive integer" >&2
  exit 1
fi
PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS="${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-900}"
if [[ ! "$PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS" =~ ^[0-9]+$ ]]; then
  echo "PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" >&2
  exit 1
fi
PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS="${PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS:-6}"
if [[ ! "$PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS" =~ ^[1-9][0-9]*$ ]]; then
  echo "PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS must be a positive integer" >&2
  exit 1
fi
PAPER_READINESS_DECISION_DRY_RUN_STRATEGY="${PAPER_READINESS_DECISION_DRY_RUN_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
if [[ -z "$PAPER_READINESS_DECISION_DRY_RUN_STRATEGY" ]]; then
  echo "PAPER_READINESS_DECISION_DRY_RUN_STRATEGY must not be empty" >&2
  exit 1
fi
PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES="${PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES:-${PAPER_APPROVED_STRATEGIES:-$PAPER_READINESS_DECISION_DRY_RUN_STRATEGY}}"
PAPER_READINESS_FORCE_REFRESH="${PAPER_READINESS_FORCE_REFRESH:-false}"
case "${PAPER_READINESS_FORCE_REFRESH,,}" in
  true|false) ;;
  *)
    echo "PAPER_READINESS_FORCE_REFRESH must be true or false" >&2
    exit 1
    ;;
esac

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

decision_dry_run_field() {
  local line="$1"
  local key="$2"
  local part
  local body="${line#paper decision dry run ok: }"

  for part in $body; do
    if [[ "$part" == "$key="* ]]; then
      printf '%s\n' "${part#*=}"
      return 0
    fi
  done

  return 1
}

normalize_strategy_csv() {
  local csv="$1"
  local raw
  local name
  local existing
  local -a raw_names
  local -a names

  IFS=',' read -r -a raw_names <<< "$csv"
  for raw in "${raw_names[@]}"; do
    name="$(printf '%s' "$raw" | tr -d '[:space:]')"
    if [[ -z "$name" ]]; then
      continue
    fi
    if [[ ! "$name" =~ ^[A-Za-z0-9_:-]+$ ]]; then
      return 1
    fi
    for existing in "${names[@]}"; do
      if [[ "$existing" == "$name" ]]; then
        continue 2
      fi
    done
    names+=("$name")
  done
  if [[ "${#names[@]}" -eq 0 ]]; then
    return 1
  fi
  (
    IFS=,
    printf '%s\n' "${names[*]}"
  )
}

validate_readiness_decision_dry_run_strategies_line() {
  local line="$1"
  local expected_csv="$2"
  local expected
  local strategies
  local count
  local expected_count

  if [[ -z "$line" ]]; then
    echo "missing"
    return 1
  fi
  if [[ "$line" != "paper readiness decision dry run strategies ok: "* ]]; then
    echo "invalid"
    return 1
  fi

  expected="$(normalize_strategy_csv "$expected_csv" || true)"
  strategies="$(decision_dry_run_field "$line" strategies || true)"
  count="$(decision_dry_run_field "$line" count || true)"
  if [[ -z "$expected" || -z "$strategies" || -z "$count" ]]; then
    echo "missing"
    return 1
  fi
  strategies="$(normalize_strategy_csv "$strategies" || true)"
  if [[ -z "$strategies" || ! "$count" =~ ^[0-9]+$ ]]; then
    echo "invalid"
    return 1
  fi
  expected_count="$(awk -F',' '{print NF}' <<< "$expected")"
  if [[ "$strategies" != "$expected" ]]; then
    echo "strategy_set_mismatch"
    return 1
  fi
  if (( 10#$count != 10#$expected_count )); then
    echo "strategy_count_mismatch"
    return 1
  fi

  echo "ok"
}

validate_readiness_decision_dry_run_line() {
  local line="$1"
  local expected_as_of_session="${2:-}"
  local strategy
  local as_of
  local as_of_session
  local active
  local decision_records
  local accepted
  local entry_intents
  local evaluations
  local min_decision_records
  local max_accepted
  local max_entry_intents
  local accepted_evidence
  local entry_intent_evidence

  if [[ -z "$line" ]]; then
    echo "missing"
    return 1
  fi
  if [[ "$line" != "paper decision dry run ok: "* ]]; then
    echo "invalid"
    return 1
  fi

  strategy="$(decision_dry_run_field "$line" strategy || true)"
  as_of="$(decision_dry_run_field "$line" as_of || true)"
  active="$(decision_dry_run_field "$line" active || true)"
  decision_records="$(decision_dry_run_field "$line" decision_records || true)"
  accepted="$(decision_dry_run_field "$line" accepted || true)"
  entry_intents="$(decision_dry_run_field "$line" entry_intents || true)"
  evaluations="$(decision_dry_run_field "$line" evaluations || true)"
  min_decision_records="$(decision_dry_run_field "$line" min_decision_records || true)"
  max_accepted="$(decision_dry_run_field "$line" max_accepted || true)"
  max_entry_intents="$(decision_dry_run_field "$line" max_entry_intents || true)"

  if [[ -z "$strategy" || -z "$as_of" || -z "$active" || -z "$decision_records" || -z "$accepted" || -z "$entry_intents" ]]; then
    echo "missing"
    return 1
  fi
  if [[ "$strategy" != "$PAPER_READINESS_DECISION_DRY_RUN_STRATEGY" ]]; then
    echo "strategy_mismatch"
    return 1
  fi
  if [[ -n "$expected_as_of_session" ]]; then
    as_of_session="$(TZ=America/New_York date -d "$as_of" +%F 2>/dev/null || true)"
    if [[ -z "$as_of_session" ]]; then
      echo "invalid"
      return 1
    fi
    if [[ "$as_of_session" != "$expected_as_of_session" ]]; then
      echo "session_mismatch"
      return 1
    fi
  fi
  if [[ ! "$active" =~ ^[0-9]+$ || ! "$decision_records" =~ ^[0-9]+$ || ! "$accepted" =~ ^[0-9]+$ || ! "$entry_intents" =~ ^[0-9]+$ ]]; then
    echo "invalid"
    return 1
  fi
  if (( 10#$active < 10#$PAPER_READINESS_MIN_WATCHLIST_SYMBOLS )); then
    echo "active_under_minimum"
    return 1
  fi
  if (( 10#$decision_records < 10#$PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS )); then
    echo "records_under_minimum"
    return 1
  fi
  if [[ ! "$evaluations" =~ ^[0-9]+$ ]] \
    || (( 10#$evaluations < 10#$PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS )); then
    echo "evaluations_under_minimum"
    return 1
  fi
  if [[ -n "$min_decision_records" ]]; then
    if [[ ! "$min_decision_records" =~ ^[0-9]+$ ]]; then
      echo "invalid"
      return 1
    fi
    if (( 10#$min_decision_records < 10#$PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS )); then
      echo "sample_records_under_minimum"
      return 1
    fi
  fi

  accepted_evidence="$accepted"
  if [[ -n "$max_accepted" ]]; then
    if [[ ! "$max_accepted" =~ ^[0-9]+$ ]]; then
      echo "invalid"
      return 1
    fi
    if (( 10#$max_accepted > 10#$accepted_evidence )); then
      accepted_evidence="$max_accepted"
    fi
  fi
  if (( 10#$accepted_evidence <= 0 )); then
    echo "accepted_under_minimum"
    return 1
  fi

  entry_intent_evidence="$entry_intents"
  if [[ -n "$max_entry_intents" ]]; then
    if [[ ! "$max_entry_intents" =~ ^[0-9]+$ ]]; then
      echo "invalid"
      return 1
    fi
    if (( 10#$max_entry_intents > 10#$entry_intent_evidence )); then
      entry_intent_evidence="$max_entry_intents"
    fi
  fi
  if (( 10#$entry_intent_evidence <= 0 )); then
    echo "entry_intents_under_minimum"
    return 1
  fi

  echo "ok"
}

latest_readiness="$("${compose[@]}" run -T --rm \
  -e PAPER_READINESS_SESSION_DATE="${PAPER_READINESS_SESSION_DATE:-}" \
  --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
from alpaca_bot.storage.db import connect_postgres

settings = Settings.from_env()
proof_start = settings.profit_probe_start_date.isoformat()
market_timezone = ZoneInfo(settings.market_timezone.key)
today = datetime.now(market_timezone).date()
session_date = today
override = os.environ.get("PAPER_READINESS_SESSION_DATE", "")
adapter = AlpacaExecutionAdapter.from_settings(settings)
calendar = adapter.get_market_calendar(
    start=today,
    end=today + timedelta(days=10),
)

if override:
    session_date = date.fromisoformat(override)
else:
    for session in calendar:
        if session.session_date >= today:
            session_date = session.session_date
            break

session_open_at = None
session_close_at = None
for session in calendar:
    if session.session_date == session_date:
        session_open_at = session.open_at
        session_close_at = session.close_at
        break
if session_open_at is None or session_close_at is None:
    try:
        target_calendar = adapter.get_market_calendar(
            start=session_date,
            end=session_date,
        )
        for session in target_calendar:
            if session.session_date == session_date:
                session_open_at = session.open_at
                session_close_at = session.close_at
                break
    except Exception:
        pass

session_state = "unknown"
now_market = datetime.now(market_timezone)
if session_open_at is not None and session_close_at is not None:
    open_market = session_open_at.astimezone(market_timezone)
    close_market = session_close_at.astimezone(market_timezone)
    if now_market < open_market:
        session_state = "pre_open"
    elif now_market <= close_market:
        session_state = "open"
    else:
        session_state = "post_close"

conn = connect_postgres(settings.database_url)
dry_run_row = None
expected_dry_run_session = ""
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              payload->>'status',
              created_at,
              to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'check_name' = 'paper_readiness'
              AND payload->>'session_date' = %s
              AND payload->>'proof_start' = %s
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                session_date.isoformat(),
                proof_start,
                settings.trading_mode.value,
                settings.strategy_version,
            ),
        )
        row = cur.fetchone()
        cur.execute(
            """
            SELECT to_char(MAX(created_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            FROM audit_events
            WHERE event_type = 'supervisor_started'
              AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = %s)
              AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = %s)
            """,
            (settings.trading_mode.value, settings.strategy_version),
        )
        supervisor_row = cur.fetchone()
        cur.execute(
            """
            SELECT
              COALESCE(payload->>'decision_dry_run_strategy', ''),
              COALESCE(payload->>'decision_dry_run_as_of', ''),
              COALESCE(payload->>'decision_dry_run_active', ''),
              COALESCE(payload->>'decision_dry_run_ignored', ''),
              COALESCE(payload->>'decision_dry_run_fractionable', ''),
              COALESCE(payload->>'decision_dry_run_intraday', ''),
              COALESCE(payload->>'decision_dry_run_completed_intraday', ''),
              COALESCE(payload->>'decision_dry_run_daily', ''),
              COALESCE(payload->>'decision_dry_run_thin_completed_lt20', ''),
              COALESCE(payload->>'decision_dry_run_records', ''),
              COALESCE(payload->>'decision_dry_run_accepted', ''),
              COALESCE(payload->>'decision_dry_run_rejected', ''),
              COALESCE(payload->>'decision_dry_run_skipped_no_signal', ''),
              COALESCE(payload->>'decision_dry_run_entry_intents', ''),
              COALESCE(payload->>'decision_dry_run_equity', ''),
              COALESCE(payload->>'decision_dry_run_sample', ''),
              COALESCE(payload->>'decision_dry_run_sample_times', ''),
              COALESCE(payload->>'decision_dry_run_evaluations', ''),
              COALESCE(payload->>'decision_dry_run_min_decision_records', ''),
              COALESCE(payload->>'decision_dry_run_max_accepted', ''),
              COALESCE(payload->>'decision_dry_run_max_entry_intents', ''),
              COALESCE(payload->>'decision_dry_run_reject_stages', ''),
              COALESCE(payload->>'decision_dry_run_reject_reasons', ''),
              COALESCE(payload->>'decision_dry_run_strategies', ''),
              COALESCE(payload->>'decision_dry_run_strategy_count', '')
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'check_name' = 'paper_readiness'
              AND payload->>'status' = 'passed'
              AND payload->>'session_date' = %s
              AND payload->>'proof_start' = %s
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
              AND payload ? 'decision_dry_run_strategy'
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                session_date.isoformat(),
                proof_start,
                settings.trading_mode.value,
                settings.strategy_version,
            ),
        )
        dry_run_row = cur.fetchone()
        try:
            calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
                start=session_date - timedelta(days=14),
                end=session_date - timedelta(days=1),
            )
            previous_sessions = [
                session.session_date
                for session in calendar
                if session.session_date < session_date
            ]
            if previous_sessions:
                expected_dry_run_session = max(previous_sessions).isoformat()
        except Exception:
            expected_dry_run_session = ""
finally:
    conn.close()

status = row[0] if row else ""
readiness_created_raw = row[1] if row else None
readiness_created_at = row[2] if row else ""
readiness_age_minutes = ""
if readiness_created_raw is not None:
    readiness_created_utc = readiness_created_raw
    if readiness_created_utc.tzinfo is None:
        readiness_created_utc = readiness_created_utc.replace(tzinfo=timezone.utc)
    else:
        readiness_created_utc = readiness_created_utc.astimezone(timezone.utc)
    age_seconds = (
        datetime.now(timezone.utc) - readiness_created_utc
    ).total_seconds()
    readiness_age_minutes = str(max(0, int(age_seconds // 60)))
supervisor_started_at = supervisor_row[0] if supervisor_row and supervisor_row[0] else ""
print(
    "paper_readiness_latest_status="
    f"{session_date.isoformat()}|{status}|{readiness_created_at}|"
    f"{supervisor_started_at}|{readiness_age_minutes}"
)
print(f"paper_readiness_session_state={session_state}")
if expected_dry_run_session:
    print(f"paper_readiness_expected_decision_dry_run_session={expected_dry_run_session}")
if dry_run_row and dry_run_row[0]:
    keys = (
        "strategy",
        "as_of",
        "active",
        "ignored",
        "fractionable",
        "intraday",
        "completed_intraday",
        "daily",
        "thin_completed_lt20",
        "decision_records",
        "accepted",
        "rejected",
        "skipped_no_signal",
        "entry_intents",
        "equity",
        "sample",
        "sample_times",
        "evaluations",
        "min_decision_records",
        "max_accepted",
        "max_entry_intents",
        "reject_stages",
        "reject_reasons",
    )
    fields = [
        f"{key}={value}"
        for key, value in zip(keys, dry_run_row)
        if value
    ]
    print(
        "paper_readiness_latest_decision_dry_run="
        "paper decision dry run ok: "
        + " ".join(fields)
    )
if dry_run_row and len(dry_run_row) >= 25 and dry_run_row[23]:
    print(
        "paper_readiness_latest_decision_dry_run_strategies="
        "paper readiness decision dry run strategies ok: "
        f"strategies={dry_run_row[23]} count={dry_run_row[24]}"
    )
PY
)"
latest_readiness_output="$latest_readiness"
latest_readiness="$(
  printf '%s\n' "$latest_readiness_output" \
    | sed -n 's/^paper_readiness_latest_status=//p' \
    | tail -n 1
)"
expected_decision_dry_run_session="$(
  printf '%s\n' "$latest_readiness_output" \
    | sed -n 's/^paper_readiness_expected_decision_dry_run_session=//p' \
    | tail -n 1
)"
readiness_session_state="$(
  printf '%s\n' "$latest_readiness_output" \
    | sed -n 's/^paper_readiness_session_state=//p' \
    | tail -n 1
)"
latest_decision_dry_run_line="$(
  printf '%s\n' "$latest_readiness_output" \
    | sed -n 's/^paper_readiness_latest_decision_dry_run=//p' \
    | tail -n 1
)"
latest_decision_dry_run_strategies_line="$(
  printf '%s\n' "$latest_readiness_output" \
    | sed -n 's/^paper_readiness_latest_decision_dry_run_strategies=//p' \
    | tail -n 1
)"

session_date=""
latest_status=""
readiness_created_at=""
supervisor_started_at=""
readiness_age_minutes=""
IFS='|' read -r session_date latest_status readiness_created_at supervisor_started_at readiness_age_minutes <<< "$latest_readiness"

readiness_is_current=true
if [[ -n "$readiness_created_at" && -n "$supervisor_started_at" && "$readiness_created_at" < "$supervisor_started_at" ]]; then
  readiness_is_current=false
fi

readiness_is_recent=true
if [[ -n "$readiness_age_minutes" ]]; then
  readiness_is_recent=false
  if [[ "$readiness_age_minutes" =~ ^[0-9]+$ ]] \
    && (( 10#$readiness_age_minutes <= 10#$PAPER_READINESS_MAX_PASS_AGE_MINUTES )); then
    readiness_is_recent=true
  fi
fi

if [[ "$session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$latest_status" == "passed" && "$readiness_is_current" == "true" && "$readiness_is_recent" == "true" ]]; then
  proof_start="${PROFIT_PROBE_START_DATE:-2026-07-07}"
  if [[ "${PAPER_READINESS_FORCE_REFRESH,,}" == "true" ]]; then
    echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=force_refresh"
    echo "paper readiness force refresh requested; rerunning final check"
    exec "$PAPER_READINESS_CHECK_SCRIPT" "$ENV_FILE"
  fi
  latest_decision_dry_run_status="missing"
  if ! latest_decision_dry_run_status="$(validate_readiness_decision_dry_run_line "$latest_decision_dry_run_line" "$expected_decision_dry_run_session")"; then
    echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=decision_dry_run_$latest_decision_dry_run_status"
    if [[ -n "$latest_decision_dry_run_line" ]]; then
      echo "$latest_decision_dry_run_line"
    fi
    echo "paper readiness prior pass lacks accepted entry-intent decision dry-run proof ($latest_decision_dry_run_status); rerunning final check"
    exec "$PAPER_READINESS_CHECK_SCRIPT" "$ENV_FILE"
  fi
  latest_decision_dry_run_strategies_status="missing"
  if ! latest_decision_dry_run_strategies_status="$(validate_readiness_decision_dry_run_strategies_line "$latest_decision_dry_run_strategies_line" "$PAPER_READINESS_DECISION_DRY_RUN_STRATEGY,$PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES")"; then
    echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=decision_dry_run_strategies_$latest_decision_dry_run_strategies_status"
    if [[ -n "$latest_decision_dry_run_strategies_line" ]]; then
      echo "$latest_decision_dry_run_strategies_line"
    fi
    echo "paper readiness prior pass lacks approved-strategy decision dry-run proof ($latest_decision_dry_run_strategies_status); rerunning final check"
    exec "$PAPER_READINESS_CHECK_SCRIPT" "$ENV_FILE"
  fi
  echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=already_passed"
  echo "$latest_decision_dry_run_line"
  echo "$latest_decision_dry_run_strategies_line"
  echo "paper readiness already passed for session $session_date; final retry not rerun"
  exit 0
fi

if [[ "$session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$latest_status" == "passed" && "$readiness_is_current" == "false" ]]; then
  proof_start="${PROFIT_PROBE_START_DATE:-2026-07-07}"
  echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=stale_after_supervisor_start"
  echo "paper readiness prior pass is older than latest supervisor start; rerunning final check"
  if [[ "$readiness_session_state" == "open" && "${PAPER_READINESS_REQUIRE_FLAT:-true}" != "false" ]]; then
    export PAPER_READINESS_REQUIRE_FLAT=false
    echo "paper readiness stale supervisor repair is during market session; disabling flat exposure gate"
  fi
fi

if [[ "$session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$latest_status" == "passed" && "$readiness_is_current" == "true" && "$readiness_is_recent" == "false" ]]; then
  proof_start="${PROFIT_PROBE_START_DATE:-2026-07-07}"
  echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=stale_by_age"
  echo "paper readiness prior pass is older than max age ${PAPER_READINESS_MAX_PASS_AGE_MINUTES}m; rerunning final check"
fi

exec "$PAPER_READINESS_CHECK_SCRIPT" "$ENV_FILE"
