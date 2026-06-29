#!/usr/bin/env bash
set -euo pipefail

CHECK_NAME="${1:-}"
LOCK_FILE="${2:-}"
ENV_FILE="${3:-/etc/alpaca_bot/alpaca-bot.env}"

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
  PAPER_ACTIVITY_STRATEGY \
  PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS \
  PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS \
  PAPER_READINESS_DECISION_DRY_RUN_STRATEGY \
  PAPER_READINESS_MAX_PASS_AGE_MINUTES \
  PAPER_READINESS_MIN_WATCHLIST_SYMBOLS \
  PAPER_READINESS_SESSION_DATE \
  POST_CLOSE_LOCK_MAX_AGE_MINUTES \
  PROFIT_PROBE_MIN_PNL \
  PROFIT_PROBE_MIN_TRADES \
  PROFIT_PROBE_START_DATE \
  PROFIT_PROBE_STRATEGY \
  PROOF_STATUS_LOCK_MAX_AGE_MINUTES \
  PROOF_STATUS_MIN_PNL \
  PROOF_STATUS_MIN_TRADES \
  PROOF_STATUS_START_DATE \
  PROOF_STATUS_STRATEGY \
  SESSION_GUARD_FAIL_BELOW_PNL \
  SESSION_GUARD_MIN_TRADES \
  SESSION_GUARD_START_DATE \
  SESSION_GUARD_STRATEGY

if [[ -z "$CHECK_NAME" || -z "$LOCK_FILE" ]]; then
  echo "usage: scheduled_check_lock_skipped.sh CHECK_NAME LOCK_FILE [ENV_FILE]" >&2
  exit 2
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  restore_env_overrides
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

load_latest_readiness_decision_dry_run() {
  local readiness_session_date="$1"
  local lookup

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e READINESS_SESSION_DATE="$readiness_session_date" \
    --entrypoint python admin <<'PY' || true
from __future__ import annotations

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
                session_date,
                proof_start,
                settings.trading_mode.value,
                settings.strategy_version,
            ),
        )
        dry_run_row = cur.fetchone()
finally:
    conn.close()

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

  printf '%s\n' "$lookup" \
    | sed -n 's/^paper_readiness_latest_decision_dry_run=//p' \
    | tail -n 1
}

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

validate_readiness_decision_dry_run_line() {
  local line="$1"
  local strategy
  local as_of
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
              COALESCE(payload->>'proof_scenario_status', '') AS proof_scenario_status,
              COALESCE(payload->>'proof_scenario_active', '') AS proof_scenario_active,
              COALESCE(payload->>'proof_scenario_expected_session', '') AS proof_scenario_expected_session,
              COALESCE(payload->>'proof_scenario_problems', '') AS proof_scenario_problems,
              COALESCE(payload->>'proof_scoreable_closed_trades', '') AS proof_scoreable_closed_trades,
              COALESCE(payload->>'proof_unpaired_filled_exits', '') AS proof_unpaired_filled_exits,
              COALESCE(payload->>'proof_unpaired_symbols', '') AS proof_unpaired_symbols,
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
            ORDER BY (payload ? 'proof_scenario_status') DESC, created_at DESC, event_id DESC
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

created_raw = row[21]
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
    f"{row[12]}|{row[13]}|{row[14]}|{row[15]}|{row[16]}|{row[17]}|"
    f"{row[18]}|{row[19]}|{row[20]}|"
    f"{row[22]}|"
    f"{age_minutes}"
)
PY
)"

  printf '%s\n' "$lookup" \
    | sed -n 's/^paper_proof_status_latest=//p' \
    | tail -n 1
}

load_latest_post_close_check_status() {
  local check_name="$1"
  local target_session="$2"
  local proof_start="$3"
  local strategy="$4"
  local min_trades="${5:-}"
  local min_pnl="${6:-}"
  local lookup

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e POST_CLOSE_LOCK_CHECK_NAME="$check_name" \
    -e POST_CLOSE_LOCK_SESSION_DATE="$target_session" \
    -e POST_CLOSE_LOCK_PROOF_START="$proof_start" \
    -e POST_CLOSE_LOCK_STRATEGY="$strategy" \
    -e POST_CLOSE_LOCK_MIN_TRADES="$min_trades" \
    -e POST_CLOSE_LOCK_MIN_PNL="$min_pnl" \
    --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import datetime, timezone
import os

from alpaca_bot.config import Settings
from alpaca_bot.storage.db import connect_postgres

check_name = os.environ["POST_CLOSE_LOCK_CHECK_NAME"]
target_session = os.environ["POST_CLOSE_LOCK_SESSION_DATE"]
proof_start = os.environ["POST_CLOSE_LOCK_PROOF_START"]
strategy = os.environ["POST_CLOSE_LOCK_STRATEGY"]
min_trades = os.environ.get("POST_CLOSE_LOCK_MIN_TRADES") or ""
min_pnl = os.environ.get("POST_CLOSE_LOCK_MIN_PNL") or ""
settings = Settings.from_env()

conn = connect_postgres(settings.database_url)
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              COALESCE(payload->>'status', ''),
              COALESCE(payload->>'exit_code', ''),
              created_at,
              to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'check_name' = %s
              AND payload->>'session_date' = %s
              AND payload->>'proof_start' = %s
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
              AND (NOT (payload ? 'strategy') OR payload->>'strategy' = %s)
              AND (%s = '' OR payload->>'min_trades' = %s)
              AND (%s = '' OR payload->>'min_pnl' = %s)
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                check_name,
                target_session,
                proof_start,
                settings.trading_mode.value,
                settings.strategy_version,
                strategy,
                min_trades,
                min_trades,
                min_pnl,
                min_pnl,
            ),
        )
        row = cur.fetchone()
finally:
    conn.close()

status = row[0] if row else ""
exit_code = row[1] if row else ""
created_raw = row[2] if row else None
created_at = row[3] if row else ""
age_minutes = ""
if created_raw is not None:
    created_utc = created_raw
    if created_utc.tzinfo is None:
        created_utc = created_utc.replace(tzinfo=timezone.utc)
    else:
        created_utc = created_utc.astimezone(timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - created_utc).total_seconds()
    age_minutes = str(max(0, int(age_seconds // 60)))

print(
    "post_close_check_latest="
    f"{status}|{exit_code}|{created_at}|{age_minutes}"
)
PY
)"

  printf '%s\n' "$lookup" \
    | sed -n 's/^post_close_check_latest=//p' \
    | tail -n 1
}

case "$CHECK_NAME" in
  paper_readiness)
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
      latest_decision_dry_run_line="$(load_latest_readiness_decision_dry_run "$readiness_session_date")"
      latest_decision_dry_run_status="missing"
      if ! latest_decision_dry_run_status="$(validate_readiness_decision_dry_run_line "$latest_decision_dry_run_line")"; then
        echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} reason=lock_busy_decision_dry_run_$latest_decision_dry_run_status"
        if [[ -n "$latest_decision_dry_run_line" ]]; then
          echo "$latest_decision_dry_run_line"
        fi
        echo "paper readiness prior pass lacks accepted entry-intent decision dry-run proof ($latest_decision_dry_run_status); lock busy remains blocking" >&2
        exit 48
      fi
      echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} reason=lock_busy_already_passed"
      echo "$latest_decision_dry_run_line"
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
    post_close_lock_max_age="${POST_CLOSE_LOCK_MAX_AGE_MINUTES:-30}"
    if [[ ! "$post_close_lock_max_age" =~ ^[0-9]+$ || "$post_close_lock_max_age" -le 0 ]]; then
      echo "POST_CLOSE_LOCK_MAX_AGE_MINUTES must be a positive integer" >&2
      exit 1
    fi
    guard_proof_start="${SESSION_GUARD_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}}"
    guard_strategy="${SESSION_GUARD_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
    guard_min_trades="${SESSION_GUARD_MIN_TRADES:-10}"
    guard_min_pnl="${SESSION_GUARD_FAIL_BELOW_PNL:-0}"
    latest_guard="$(
      load_latest_post_close_check_status \
        session_guard \
        "$session_date" \
        "$guard_proof_start" \
        "$guard_strategy" \
        "$guard_min_trades" \
        "$guard_min_pnl"
    )"
    latest_guard_status=""
    latest_guard_exit_code=""
    latest_guard_created_at=""
    latest_guard_age_minutes=""
    IFS='|' read -r latest_guard_status latest_guard_exit_code latest_guard_created_at latest_guard_age_minutes <<< "$latest_guard"
    if [[ "$latest_guard_age_minutes" =~ ^[0-9]+$ ]] \
      && (( 10#$latest_guard_age_minutes <= 10#$post_close_lock_max_age )); then
      if [[ "$latest_guard_status" == "passed" && "$latest_guard_exit_code" == "0" ]]; then
        echo "scheduled check context: session_date=$session_date proof_start=$guard_proof_start strategy=$guard_strategy min_trades=$guard_min_trades min_pnl=$guard_min_pnl reason=lock_busy_already_passed"
        echo "session guard passed: lock busy after recent pass for session $session_date created_at=${latest_guard_created_at:-unknown} age_minutes=$latest_guard_age_minutes"
        exit 0
      fi
      if [[ "$latest_guard_status" == "pending" && "$latest_guard_exit_code" == "43" ]]; then
        echo "scheduled check context: session_date=$session_date proof_start=$guard_proof_start strategy=$guard_strategy min_trades=$guard_min_trades min_pnl=$guard_min_pnl reason=lock_busy_already_pending"
        echo "session guard pending: lock busy after recent pending result for session $session_date created_at=${latest_guard_created_at:-unknown} age_minutes=$latest_guard_age_minutes"
        exit 43
      fi
    fi
    echo "scheduled check context: session_date=$session_date proof_start=$guard_proof_start strategy=$guard_strategy min_trades=$guard_min_trades min_pnl=$guard_min_pnl reason=lock_busy"
    ;;
  paper_profit_probe)
    post_close_lock_max_age="${POST_CLOSE_LOCK_MAX_AGE_MINUTES:-30}"
    if [[ ! "$post_close_lock_max_age" =~ ^[0-9]+$ || "$post_close_lock_max_age" -le 0 ]]; then
      echo "POST_CLOSE_LOCK_MAX_AGE_MINUTES must be a positive integer" >&2
      exit 1
    fi
    probe_proof_start="${PROFIT_PROBE_START_DATE:-2026-06-29}"
    probe_strategy="${PROFIT_PROBE_STRATEGY:-bull_flag}"
    probe_min_trades="${PROFIT_PROBE_MIN_TRADES:-10}"
    probe_min_pnl="${PROFIT_PROBE_MIN_PNL:-0.01}"
    latest_probe="$(
      load_latest_post_close_check_status \
        paper_profit_probe \
        "$session_date" \
        "$probe_proof_start" \
        "$probe_strategy" \
        "$probe_min_trades" \
        "$probe_min_pnl"
    )"
    latest_probe_status=""
    latest_probe_exit_code=""
    latest_probe_created_at=""
    latest_probe_age_minutes=""
    IFS='|' read -r latest_probe_status latest_probe_exit_code latest_probe_created_at latest_probe_age_minutes <<< "$latest_probe"
    if [[ "$latest_probe_age_minutes" =~ ^[0-9]+$ ]] \
      && (( 10#$latest_probe_age_minutes <= 10#$post_close_lock_max_age )); then
      if [[ "$latest_probe_status" == "passed" && "$latest_probe_exit_code" == "0" ]]; then
        echo "scheduled check context: session_date=$session_date proof_start=$probe_proof_start strategy=$probe_strategy min_trades=$probe_min_trades min_pnl=$probe_min_pnl reason=lock_busy_already_passed"
        echo "paper profit probe passed: lock busy after recent pass for session $session_date created_at=${latest_probe_created_at:-unknown} age_minutes=$latest_probe_age_minutes"
        exit 0
      fi
      if [[ "$latest_probe_status" == "pending" && "$latest_probe_exit_code" == "43" ]]; then
        echo "scheduled check context: session_date=$session_date proof_start=$probe_proof_start strategy=$probe_strategy min_trades=$probe_min_trades min_pnl=$probe_min_pnl reason=lock_busy_already_pending"
        echo "paper profit probe pending: lock busy after recent pending result for session $session_date created_at=${latest_probe_created_at:-unknown} age_minutes=$latest_probe_age_minutes"
        exit 43
      fi
    fi
    echo "scheduled check context: session_date=$session_date proof_start=$probe_proof_start strategy=$probe_strategy min_trades=$probe_min_trades min_pnl=$probe_min_pnl reason=lock_busy"
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
    latest_scenario_status=""
    latest_scenario_active=""
    latest_scenario_expected_session=""
    latest_scenario_problems=""
    latest_scoreable_closed_trades=""
    latest_unpaired_filled_exits=""
    latest_unpaired_symbols=""
    latest_created_at=""
    latest_age_minutes=""
    IFS='|' read -r latest_status latest_exit_code latest_proof latest_readiness latest_blockers latest_proof_reason latest_warnings latest_progress_status latest_closed_trades latest_required_trades latest_pnl latest_required_pnl latest_first_exit_session latest_latest_exit_session latest_scenario_status latest_scenario_active latest_scenario_expected_session latest_scenario_problems latest_scoreable_closed_trades latest_unpaired_filled_exits latest_unpaired_symbols latest_created_at latest_age_minutes <<< "$latest_proof_status"
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
      elif [[ "$latest_status" == "skipped" && "$latest_exit_code" == "0" && ( "$latest_proof" == "pending" || "$latest_proof" == "passed" ) ]]; then
        proof_lock_has_current_evidence=true
      fi
    fi
    if [[ "$proof_lock_is_recent" == "true" && "$proof_lock_has_current_evidence" == "true" ]]; then
      echo "scheduled check context: session_date=$session_date proof_start=$proof_start strategy=$proof_strategy min_trades=$proof_min_trades min_pnl=$proof_min_pnl reason=lock_busy_already_reported"
      echo "paper proof summary: readiness=$latest_readiness proof=$latest_proof reason=${latest_proof_reason:-lock_busy_already_reported} blockers=$latest_blockers warnings=${latest_warnings:-none}"
      echo "paper proof progress: status=${latest_progress_status:-$latest_proof} closed_trades=${latest_closed_trades:-unknown} required_trades=${latest_required_trades:-$proof_min_trades} pnl=${latest_pnl:-unknown} required_pnl=${latest_required_pnl:-$proof_min_pnl} window=lock_busy_already_reported first_exit_session=${latest_first_exit_session:-none} latest_exit_session=${latest_latest_exit_session:-none}"
      if [[ -n "$latest_scoreable_closed_trades$latest_unpaired_filled_exits$latest_unpaired_symbols" ]]; then
        echo "paper proof scoring: scoreable_closed_trades=${latest_scoreable_closed_trades:-${latest_closed_trades:-unknown}} unpaired_filled_exits=${latest_unpaired_filled_exits:-unknown} unpaired_symbols=${latest_unpaired_symbols:-none}"
      fi
      if [[ -n "$latest_scenario_status" ]]; then
        echo "paper proof scenarios: status=$latest_scenario_status active=${latest_scenario_active:-unknown} expected_session=${latest_scenario_expected_session:-unknown} problems=${latest_scenario_problems:-unknown}"
      fi
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
