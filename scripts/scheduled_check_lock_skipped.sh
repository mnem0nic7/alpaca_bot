#!/usr/bin/env bash
set -euo pipefail

CHECK_NAME="${1:-}"
LOCK_FILE="${2:-}"
ENV_FILE="${3:-/etc/alpaca_bot/alpaca-bot.env}"

if [[ -z "$CHECK_NAME" || -z "$LOCK_FILE" ]]; then
  echo "usage: scheduled_check_lock_skipped.sh CHECK_NAME LOCK_FILE [ENV_FILE]" >&2
  exit 2
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

session_date="$(TZ=America/New_York date +%F)"

fallback_readiness_session_date() {
  local dow
  dow="$(TZ=America/New_York date +%u)"
  case "$dow" in
    6) TZ=America/New_York date -d "2 days" +%F ;;
    7) TZ=America/New_York date -d "1 day" +%F ;;
    *) TZ=America/New_York date +%F ;;
  esac
}

load_readiness_session_date() {
  local lookup
  local readiness_session_date

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter

settings = Settings.from_env()
override = os.environ.get("PAPER_READINESS_SESSION_DATE", "")

if override:
    session_date = date.fromisoformat(override)
else:
    market_timezone = ZoneInfo(settings.market_timezone.key)
    today = datetime.now(market_timezone).date()
    session_date = today
    calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
        start=today,
        end=today + timedelta(days=10),
    )
    for session in calendar:
        if session.session_date >= today:
            session_date = session.session_date
            break

print(f"paper_readiness_session_date={session_date.isoformat()}")
PY
)"

  readiness_session_date="$(
    printf '%s\n' "$lookup" \
      | sed -n 's/^paper_readiness_session_date=//p' \
      | tail -n 1
  )"
  if [[ "$readiness_session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "$readiness_session_date"
    return
  fi

  fallback_readiness_session_date
}

load_latest_readiness_status() {
  local readiness_session_date="$1"
  local lookup

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e READINESS_SESSION_DATE="$readiness_session_date" \
    --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import datetime, timezone
import os

from alpaca_bot.config import Settings
from alpaca_bot.storage.db import connect_postgres

settings = Settings.from_env()
session_date = os.environ["READINESS_SESSION_DATE"]
proof_start = settings.profit_probe_start_date.isoformat()

conn = connect_postgres(settings.database_url)
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
                session_date,
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
    f"{status}|{readiness_created_at}|{supervisor_started_at}|"
    f"{readiness_age_minutes}"
)
PY
)"

  printf '%s\n' "$lookup" \
    | sed -n 's/^paper_readiness_latest_status=//p' \
    | tail -n 1
}

load_latest_proof_status() {
  local proof_start="$1"
  local proof_strategy="$2"
  local proof_min_trades="$3"
  local proof_min_pnl="$4"
  local lookup

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e PROOF_STATUS_LOCK_PROOF_START="$proof_start" \
    -e PROOF_STATUS_LOCK_STRATEGY="$proof_strategy" \
    -e PROOF_STATUS_LOCK_MIN_TRADES="$proof_min_trades" \
    -e PROOF_STATUS_LOCK_MIN_PNL="$proof_min_pnl" \
    --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import datetime, timezone
import os

from alpaca_bot.config import Settings
from alpaca_bot.storage.db import connect_postgres

settings = Settings.from_env()
proof_start = os.environ["PROOF_STATUS_LOCK_PROOF_START"]
proof_strategy = os.environ["PROOF_STATUS_LOCK_STRATEGY"]
proof_min_trades = os.environ["PROOF_STATUS_LOCK_MIN_TRADES"]
proof_min_pnl = os.environ["PROOF_STATUS_LOCK_MIN_PNL"]

conn = connect_postgres(settings.database_url)
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              COALESCE(payload->>'status', '') AS status,
              COALESCE(payload->>'exit_code', '') AS exit_code,
              COALESCE(payload->>'proof_status', '') AS proof_status,
              COALESCE(payload->>'proof_readiness', '') AS proof_readiness,
              COALESCE(payload->>'proof_blockers', '') AS proof_blockers,
              COALESCE(payload->>'proof_reason', '') AS proof_reason,
              COALESCE(payload->>'proof_warnings', '') AS proof_warnings,
              COALESCE(payload->>'proof_progress_status', '') AS proof_progress_status,
              COALESCE(payload->>'proof_closed_trades', '') AS proof_closed_trades,
              COALESCE(payload->>'proof_required_trades', '') AS proof_required_trades,
              COALESCE(payload->>'proof_pnl', '') AS proof_pnl,
              COALESCE(payload->>'proof_required_pnl', '') AS proof_required_pnl,
              COALESCE(payload->>'proof_first_exit_session', '') AS proof_first_exit_session,
              COALESCE(payload->>'proof_latest_exit_session', '') AS proof_latest_exit_session,
              created_at,
              to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'check_name' = 'paper_proof_status'
              AND payload->>'proof_start' = %s
              AND payload->>'strategy' = %s
              AND payload->>'min_trades' = %s
              AND payload->>'min_pnl' = %s
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
              AND payload ? 'proof_status'
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                proof_start,
                proof_strategy,
                proof_min_trades,
                proof_min_pnl,
                settings.trading_mode.value,
                settings.strategy_version,
            ),
        )
        row = cur.fetchone()
finally:
    conn.close()

if not row:
    raise SystemExit(0)

created_raw = row[14]
created_utc = created_raw
if created_utc.tzinfo is None:
    created_utc = created_utc.replace(tzinfo=timezone.utc)
else:
    created_utc = created_utc.astimezone(timezone.utc)
age_seconds = (datetime.now(timezone.utc) - created_utc).total_seconds()
age_minutes = str(max(0, int(age_seconds // 60)))
print(
    "paper_proof_status_latest="
    f"{row[0]}|{row[1]}|{row[2]}|{row[3]}|{row[4]}|{row[5]}|"
    f"{row[6]}|{row[7]}|{row[8]}|{row[9]}|{row[10]}|{row[11]}|"
    f"{row[12]}|{row[13]}|{row[15]}|"
    f"{age_minutes}"
)
PY
)"

  printf '%s\n' "$lookup" \
    | sed -n 's/^paper_proof_status_latest=//p' \
    | tail -n 1
}

case "$CHECK_NAME" in
  paper_readiness)
    PAPER_READINESS_MAX_PASS_AGE_MINUTES="${PAPER_READINESS_MAX_PASS_AGE_MINUTES:-180}"
    if [[ ! "$PAPER_READINESS_MAX_PASS_AGE_MINUTES" =~ ^[0-9]+$ || "$PAPER_READINESS_MAX_PASS_AGE_MINUTES" -le 0 ]]; then
      echo "PAPER_READINESS_MAX_PASS_AGE_MINUTES must be a positive integer" >&2
      exit 1
    fi
    readiness_session_date="$(load_readiness_session_date)"
    latest_readiness="$(load_latest_readiness_status "$readiness_session_date")"
    latest_readiness_status=""
    readiness_created_at=""
    supervisor_started_at=""
    readiness_age_minutes=""
    IFS='|' read -r latest_readiness_status readiness_created_at supervisor_started_at readiness_age_minutes <<< "$latest_readiness"
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
    if [[ "$latest_readiness_status" == "passed" && "$readiness_is_current" == "true" && "$readiness_is_recent" == "true" ]]; then
      echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} reason=lock_busy_already_passed"
      echo "paper readiness lock busy after prior pass for session $readiness_session_date; not blocking entries"
      exit 0
    fi
    if [[ "$latest_readiness_status" == "passed" && "$readiness_is_current" == "false" ]]; then
      echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} reason=lock_busy_stale_pass"
      echo "paper readiness prior pass is older than latest supervisor start; lock busy remains blocking" >&2
      exit 48
    fi
    if [[ "$latest_readiness_status" == "passed" && "$readiness_is_current" == "true" && "$readiness_is_recent" == "false" ]]; then
      echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} reason=lock_busy_stale_pass"
      echo "paper readiness prior pass is older than max age ${PAPER_READINESS_MAX_PASS_AGE_MINUTES}m; lock busy remains blocking" >&2
      exit 48
    fi
    echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} reason=lock_busy"
    ;;
  paper_activity)
    echo "scheduled check context: session_date=$session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} strategy=${PAPER_ACTIVITY_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}} reason=lock_busy"
    ;;
  session_guard)
    echo "scheduled check context: session_date=$session_date proof_start=${SESSION_GUARD_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}} strategy=${SESSION_GUARD_STRATEGY:-bull_flag} reason=lock_busy"
    ;;
  paper_profit_probe)
    echo "scheduled check context: session_date=$session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} strategy=${PROFIT_PROBE_STRATEGY:-bull_flag} min_trades=${PROFIT_PROBE_MIN_TRADES:-10} min_pnl=${PROFIT_PROBE_MIN_PNL:-0.01} reason=lock_busy"
    ;;
  paper_proof_status)
    proof_start="${PROOF_STATUS_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}}"
    proof_strategy="${PROOF_STATUS_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
    proof_min_trades="${PROOF_STATUS_MIN_TRADES:-${PROFIT_PROBE_MIN_TRADES:-10}}"
    proof_min_pnl="${PROOF_STATUS_MIN_PNL:-${PROFIT_PROBE_MIN_PNL:-0.01}}"
    PROOF_STATUS_LOCK_MAX_AGE_MINUTES="${PROOF_STATUS_LOCK_MAX_AGE_MINUTES:-30}"
    if [[ ! "$PROOF_STATUS_LOCK_MAX_AGE_MINUTES" =~ ^[0-9]+$ || "$PROOF_STATUS_LOCK_MAX_AGE_MINUTES" -le 0 ]]; then
      echo "PROOF_STATUS_LOCK_MAX_AGE_MINUTES must be a positive integer" >&2
      exit 1
    fi
    latest_proof_status="$(load_latest_proof_status "$proof_start" "$proof_strategy" "$proof_min_trades" "$proof_min_pnl")"
    latest_status=""
    latest_exit_code=""
    latest_proof=""
    latest_readiness=""
    latest_blockers=""
    latest_proof_reason=""
    latest_warnings=""
    latest_progress_status=""
    latest_closed_trades=""
    latest_required_trades=""
    latest_pnl=""
    latest_required_pnl=""
    latest_first_exit_session=""
    latest_latest_exit_session=""
    latest_created_at=""
    latest_age_minutes=""
    IFS='|' read -r latest_status latest_exit_code latest_proof latest_readiness latest_blockers latest_proof_reason latest_warnings latest_progress_status latest_closed_trades latest_required_trades latest_pnl latest_required_pnl latest_first_exit_session latest_latest_exit_session latest_created_at latest_age_minutes <<< "$latest_proof_status"
    proof_lock_is_recent=false
    if [[ "$latest_age_minutes" =~ ^[0-9]+$ ]] \
      && (( 10#$latest_age_minutes <= 10#$PROOF_STATUS_LOCK_MAX_AGE_MINUTES )); then
      proof_lock_is_recent=true
    fi
    proof_lock_has_current_evidence=false
    if [[ "$latest_readiness" == "ready" && "$latest_blockers" == "none" ]]; then
      if [[ "$latest_status" == "pending" && "$latest_exit_code" == "43" && "$latest_proof" == "pending" ]]; then
        proof_lock_has_current_evidence=true
      elif [[ "$latest_status" == "passed" && "$latest_exit_code" == "0" && "$latest_proof" == "passed" ]]; then
        proof_lock_has_current_evidence=true
      fi
    fi
    if [[ "$proof_lock_is_recent" == "true" && "$proof_lock_has_current_evidence" == "true" ]]; then
      echo "scheduled check context: session_date=$session_date proof_start=$proof_start strategy=$proof_strategy min_trades=$proof_min_trades min_pnl=$proof_min_pnl reason=lock_busy_already_reported"
      echo "paper proof summary: readiness=$latest_readiness proof=$latest_proof reason=${latest_proof_reason:-lock_busy_already_reported} blockers=$latest_blockers warnings=${latest_warnings:-none}"
      echo "paper proof progress: status=${latest_progress_status:-$latest_proof} closed_trades=${latest_closed_trades:-unknown} required_trades=${latest_required_trades:-$proof_min_trades} pnl=${latest_pnl:-unknown} required_pnl=${latest_required_pnl:-$proof_min_pnl} window=lock_busy_already_reported first_exit_session=${latest_first_exit_session:-none} latest_exit_session=${latest_latest_exit_session:-none}"
      echo "paper proof status check skipped: lock busy after recent proof status $latest_proof created_at=${latest_created_at:-unknown} age_minutes=$latest_age_minutes"
      exit 0
    fi
    echo "scheduled check context: session_date=$session_date proof_start=$proof_start strategy=$proof_strategy min_trades=$proof_min_trades min_pnl=$proof_min_pnl reason=lock_busy"
    ;;
  *)
    echo "scheduled check context: session_date=$session_date reason=lock_busy"
    ;;
esac

echo "scheduled check lock busy: check=$CHECK_NAME lock=$LOCK_FILE" >&2
exit 48
