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
  PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS \
  PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED \
  PAPER_READINESS_DECISION_DRY_RUN_STRATEGY \
  PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES \
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
PAPER_READINESS_FORCE_REFRESH="${PAPER_READINESS_FORCE_REFRESH:-false}"
case "${PAPER_READINESS_FORCE_REFRESH,,}" in
  true|false) ;;
  *)
    echo "PAPER_READINESS_FORCE_REFRESH must be true or false" >&2
    exit 1
    ;;
esac

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

latest_readiness="$("${compose[@]}" run -T --rm \
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

if override:
    session_date = date.fromisoformat(override)
else:
    calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
        start=today,
        end=today + timedelta(days=10),
    )
    for session in calendar:
        if session.session_date >= today:
            session_date = session.session_date
            break

conn = connect_postgres(settings.database_url)
dry_run_row = None
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
              COALESCE(payload->>'decision_dry_run_reject_reasons', '')
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
PY
)"
latest_readiness_output="$latest_readiness"
latest_readiness="$(
  printf '%s\n' "$latest_readiness_output" \
    | sed -n 's/^paper_readiness_latest_status=//p' \
    | tail -n 1
)"
latest_decision_dry_run_line="$(
  printf '%s\n' "$latest_readiness_output" \
    | sed -n 's/^paper_readiness_latest_decision_dry_run=//p' \
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
  proof_start="${PROFIT_PROBE_START_DATE:-2026-06-29}"
  if [[ "${PAPER_READINESS_FORCE_REFRESH,,}" == "true" ]]; then
    echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=force_refresh"
    echo "paper readiness force refresh requested; rerunning final check"
    exec "$PAPER_READINESS_CHECK_SCRIPT" "$ENV_FILE"
  fi
  echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=already_passed"
  if [[ -n "$latest_decision_dry_run_line" ]]; then
    echo "$latest_decision_dry_run_line"
  fi
  echo "paper readiness already passed for session $session_date; final retry not rerun"
  exit 0
fi

if [[ "$session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$latest_status" == "passed" && "$readiness_is_current" == "false" ]]; then
  proof_start="${PROFIT_PROBE_START_DATE:-2026-06-29}"
  echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=stale_after_supervisor_start"
  echo "paper readiness prior pass is older than latest supervisor start; rerunning final check"
fi

if [[ "$session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$latest_status" == "passed" && "$readiness_is_current" == "true" && "$readiness_is_recent" == "false" ]]; then
  proof_start="${PROFIT_PROBE_START_DATE:-2026-06-29}"
  echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=stale_by_age"
  echo "paper readiness prior pass is older than max age ${PAPER_READINESS_MAX_PASS_AGE_MINUTES}m; rerunning final check"
fi

exec "$PAPER_READINESS_CHECK_SCRIPT" "$ENV_FILE"
