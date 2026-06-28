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
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                session_date,
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
  *)
    echo "scheduled check context: session_date=$session_date reason=lock_busy"
    ;;
esac

echo "scheduled check lock busy: check=$CHECK_NAME lock=$LOCK_FILE" >&2
exit 48
