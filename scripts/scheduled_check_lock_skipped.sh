#!/usr/bin/env bash
set -euo pipefail

CHECK_NAME="${1:-}"
LOCK_FILE="${2:-}"
ENV_FILE="${3:-/etc/alpaca_bot/alpaca-bot.env}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
  PAPER_APPROVED_STRATEGIES \
  PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE \
  PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER \
  PAPER_ACTIVITY_STRATEGY \
  PAPER_ACTIVITY_STRATEGIES \
  PAPER_ACTIVITY_LOCK_MAX_AGE_MINUTES \
  PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS \
  PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS \
  PAPER_READINESS_DECISION_DRY_RUN_STRATEGY \
  PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES \
  PAPER_READINESS_MAX_PASS_AGE_MINUTES \
  PAPER_READINESS_MIN_WATCHLIST_SYMBOLS \
  PAPER_READINESS_SESSION_DATE \
  PAPER_SCALE_MIN_TRADES \
  POST_CLOSE_LOCK_MAX_AGE_MINUTES \
  PROFIT_PROBE_MIN_PNL \
  PROFIT_PROBE_MIN_TRADES \
  PROFIT_PROBE_START_DATE \
  PROFIT_PROBE_STRATEGY \
  PROFIT_PROBE_STRATEGIES \
  PROOF_STATUS_LOCK_MAX_AGE_MINUTES \
  PROOF_STATUS_MIN_PNL \
  PROOF_STATUS_MIN_TRADES \
  PROOF_STATUS_SESSION_GUARD_MIN_PNL \
  PROOF_STATUS_SESSION_GUARD_MIN_TRADES \
  PROOF_STATUS_START_DATE \
  PROOF_STATUS_APPROVED_STRATEGIES \
  PROOF_STATUS_STRATEGY \
  SESSION_GUARD_FAIL_BELOW_PNL \
  SESSION_GUARD_MIN_TRADES \
  SESSION_GUARD_START_DATE \
  SESSION_GUARD_STRATEGY \
  SESSION_GUARD_STRATEGIES

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

resolve_paper_approved_strategy_csv() {
  local primary="$1"
  local fallback="${PAPER_APPROVED_STRATEGIES:-$primary}"
  local resolved

  if resolved="$(bash "$ROOT_DIR/scripts/resolve_paper_approved_strategies.sh" "$ENV_FILE" "$fallback" 2>/dev/null)" \
    && [[ -n "$resolved" ]]; then
    printf '%s\n' "$resolved"
    return
  fi
  printf '%s\n' "$fallback"
}

normalize_strategy_csv() {
  local primary="$1"
  local csv="$2"
  local label="$3"
  local raw
  local name
  local existing
  local old_ifs
  local -a names=()
  local -a raw_names=()
  local -a combined=()

  combined+=("$primary")
  IFS=',' read -r -a raw_names <<< "$csv"
  combined+=("${raw_names[@]}")
  for raw in "${combined[@]}"; do
    name="$(printf '%s' "$raw" | tr -d '[:space:]')"
    if [[ -z "$name" ]]; then
      continue
    fi
    if [[ ! "$name" =~ ^[A-Za-z0-9_:-]+$ ]]; then
      echo "$label contains unsupported strategy: $name" >&2
      exit 1
    fi
    for existing in "${names[@]}"; do
      if [[ "$existing" == "$name" ]]; then
        continue 2
      fi
    done
    names+=("$name")
  done
  if [[ "${#names[@]}" -eq 0 ]]; then
    echo "$label must contain at least one strategy" >&2
    exit 1
  fi
  old_ifs="$IFS"
  IFS=,
  printf '%s' "${names[*]}"
  IFS="$old_ifs"
}

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

load_previous_readiness_session_date() {
  local readiness_session_date="$1"
  local lookup
  local previous_session_date

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e READINESS_SESSION_DATE="$readiness_session_date" \
    --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import date, timedelta
import os

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter

settings = Settings.from_env()
session_date = date.fromisoformat(os.environ["READINESS_SESSION_DATE"])
calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
    start=session_date - timedelta(days=14),
    end=session_date - timedelta(days=1),
)
previous = [
    session.session_date
    for session in calendar
    if session.session_date < session_date
]
if previous:
    print(f"paper_readiness_previous_session_date={max(previous).isoformat()}")
PY
)"

  previous_session_date="$(
    printf '%s\n' "$lookup" \
      | sed -n 's/^paper_readiness_previous_session_date=//p' \
      | tail -n 1
  )"
  if [[ "$previous_session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "$previous_session_date"
  fi
}

load_latest_readiness_status() {
  local readiness_session_date="$1"
  local lookup

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e READINESS_SESSION_DATE="$readiness_session_date" \
    --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import datetime, timezone
import json
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
              COALESCE(payload->>'decision_dry_run_strategy_disabled', ''),
              COALESCE(payload->>'decision_dry_run_allow_disabled', ''),
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
        "strategy_disabled",
        "allow_disabled",
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

load_latest_readiness_decision_dry_run_strategies() {
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
finally:
    conn.close()

if row and row[0]:
    print(
        "paper_readiness_latest_decision_dry_run_strategies="
        "paper readiness decision dry run strategies ok: "
        f"strategies={row[0]} count={row[1]}"
    )
PY
)"

  printf '%s\n' "$lookup" \
    | sed -n 's/^paper_readiness_latest_decision_dry_run_strategies=//p' \
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

normalize_strategy_csv() {
  local primary="$1"
  local csv="${2:-}"
  local label="${3:-strategy list}"
  local raw
  local name
  local existing
  local -a raw_names
  local -a names
  local -a combined

  if [[ "$#" -gt 1 ]]; then
    combined+=("$primary")
    IFS=',' read -r -a raw_names <<< "$csv"
    combined+=("${raw_names[@]}")
  else
    IFS=',' read -r -a combined <<< "$primary"
  fi
  for raw in "${combined[@]}"; do
    name="$(printf '%s' "$raw" | tr -d '[:space:]')"
    if [[ -z "$name" ]]; then
      continue
    fi
    if [[ ! "$name" =~ ^[A-Za-z0-9_:-]+$ ]]; then
      if [[ "$#" -gt 1 ]]; then
        echo "$label contains unsupported strategy: $name" >&2
        exit 1
      fi
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
    if [[ "$#" -gt 1 ]]; then
      echo "$label must contain at least one strategy" >&2
      exit 1
    fi
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

load_latest_proof_status() {
  local proof_start="$1"
  local proof_strategy="$2"
  local proof_strategies="$3"
  local proof_min_trades="$4"
  local proof_min_pnl="$5"
  local proof_session_guard_min_trades="$6"
  local proof_session_guard_min_pnl="$7"
  local lookup

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e PROOF_STATUS_LOCK_PROOF_START="$proof_start" \
    -e PROOF_STATUS_LOCK_STRATEGY="$proof_strategy" \
    -e PROOF_STATUS_LOCK_STRATEGIES="$proof_strategies" \
    -e PROOF_STATUS_LOCK_MIN_TRADES="$proof_min_trades" \
    -e PROOF_STATUS_LOCK_MIN_PNL="$proof_min_pnl" \
    -e PROOF_STATUS_LOCK_SESSION_GUARD_MIN_TRADES="$proof_session_guard_min_trades" \
    -e PROOF_STATUS_LOCK_SESSION_GUARD_MIN_PNL="$proof_session_guard_min_pnl" \
    --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from alpaca_bot.config import Settings
from alpaca_bot.storage.db import connect_postgres

settings = Settings.from_env()
proof_start = os.environ["PROOF_STATUS_LOCK_PROOF_START"]
proof_strategy = os.environ["PROOF_STATUS_LOCK_STRATEGY"]
proof_strategies = os.environ["PROOF_STATUS_LOCK_STRATEGIES"]
proof_min_trades = os.environ["PROOF_STATUS_LOCK_MIN_TRADES"]
proof_min_pnl = os.environ["PROOF_STATUS_LOCK_MIN_PNL"]
proof_session_guard_min_trades = os.environ[
    "PROOF_STATUS_LOCK_SESSION_GUARD_MIN_TRADES"
]
proof_session_guard_min_pnl = os.environ["PROOF_STATUS_LOCK_SESSION_GUARD_MIN_PNL"]

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
              COALESCE(payload->>'proof_evidence_blockers', '') AS proof_evidence_blockers,
              COALESCE(payload->>'proof_sealed_evidence_blockers', '') AS proof_sealed_evidence_blockers,
              COALESCE(payload->>'proof_overall_blockers', '') AS proof_overall_blockers,
              COALESCE(payload->>'proof_clean_window_blockers', '') AS proof_clean_window_blockers,
              COALESCE(payload->>'proof_sealed_clean_window_blockers', '') AS proof_sealed_clean_window_blockers,
              COALESCE(payload->>'proof_reason', '') AS proof_reason,
              COALESCE(payload->>'proof_overall_reason', '') AS proof_overall_reason,
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
              to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
              payload
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'check_name' = 'paper_proof_status'
              AND payload->>'proof_start' = %s
              AND payload->>'strategy' = %s
              AND (%s = %s OR payload->>'strategies' = %s)
              AND payload->>'min_trades' = %s
              AND payload->>'min_pnl' = %s
              AND payload->>'session_guard_min_trades' = %s
              AND payload->>'session_guard_min_pnl' = %s
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
              AND payload ? 'proof_status'
              AND (
                (
                  payload->>'status' = 'pending'
                  AND payload->>'exit_code' IN ('0', '43')
                  AND payload->>'proof_status' = 'pending'
                )
                OR (
                  payload->>'status' = 'passed'
                  AND payload->>'exit_code' = '0'
                  AND payload->>'proof_status' = 'passed'
                )
                OR (
                  payload->>'status' = 'skipped'
                  AND payload->>'exit_code' = '0'
                  AND payload->>'proof_status' IN ('pending', 'passed')
                )
              )
            ORDER BY (payload ? 'proof_scenario_status') DESC, created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                proof_start,
                proof_strategy,
                proof_strategies,
                proof_strategy,
                proof_strategies,
                proof_min_trades,
                proof_min_pnl,
                proof_session_guard_min_trades,
                proof_session_guard_min_pnl,
                settings.trading_mode.value,
                settings.strategy_version,
            ),
        )
        row = cur.fetchone()
finally:
    conn.close()

if not row:
    raise SystemExit(0)

created_raw = row[27]
created_utc = created_raw
if created_utc.tzinfo is None:
    created_utc = created_utc.replace(tzinfo=timezone.utc)
else:
    created_utc = created_utc.astimezone(timezone.utc)
age_seconds = (datetime.now(timezone.utc) - created_utc).total_seconds()
age_minutes = str(max(0, int(age_seconds // 60)))
payload = row[29] or {}
if isinstance(payload, str):
    payload = json.loads(payload)
execution_quality_fields = [
    ("status", "proof_execution_quality_status"),
    ("warnings", "proof_execution_quality_warnings"),
    ("evaluated", "proof_execution_quality_evaluated"),
    ("signals", "proof_execution_quality_signals"),
    ("accepted", "proof_execution_quality_accepted"),
    ("accepted_for_fill", "proof_execution_quality_accepted_for_fill"),
    ("capacity_rejected", "proof_execution_quality_capacity_rejected"),
    ("capacity_reject_rate", "proof_execution_quality_capacity_reject_rate"),
    ("max_capacity_reject_rate", "proof_execution_quality_max_capacity_reject_rate"),
    ("entry_quality_rejected", "proof_execution_quality_entry_quality_rejected"),
    ("vwap_rejected", "proof_execution_quality_vwap_rejected"),
    ("sizing_rejected", "proof_execution_quality_sizing_rejected"),
    ("entry_orders", "proof_execution_quality_entry_orders"),
    ("filled", "proof_execution_quality_filled"),
    ("canceled", "proof_execution_quality_canceled"),
    ("expired", "proof_execution_quality_expired"),
    ("rejected", "proof_execution_quality_rejected"),
    ("active", "proof_execution_quality_active"),
    ("maintenance_drained", "proof_execution_quality_maintenance_drained"),
    ("short_window_drained", "proof_execution_quality_short_window_drained"),
    ("entry_fill_rate_status", "proof_execution_quality_entry_fill_rate_status"),
    ("entry_fill_rate", "proof_execution_quality_entry_fill_rate"),
    ("min_entry_fill_rate", "proof_execution_quality_min_entry_fill_rate"),
    (
        "current_posture_entry_orders",
        "proof_execution_quality_current_posture_entry_orders",
    ),
    ("current_posture_filled", "proof_execution_quality_current_posture_filled"),
    (
        "current_posture_entry_fill_rate",
        "proof_execution_quality_current_posture_entry_fill_rate",
    ),
    (
        "current_posture_would_reject",
        "proof_execution_quality_current_posture_would_reject",
    ),
    (
        "effective_entry_fill_rate",
        "proof_execution_quality_effective_entry_fill_rate",
    ),
    (
        "effective_entry_fill_rate_source",
        "proof_execution_quality_effective_entry_fill_rate_source",
    ),
    ("accepted_to_fill_rate", "proof_execution_quality_accepted_to_fill_rate"),
    ("filled_symbols", "proof_execution_quality_filled_symbols"),
    ("expired_symbols", "proof_execution_quality_expired_symbols"),
    ("expired_reasons", "proof_execution_quality_expired_reasons"),
    (
        "expired_signal_price_posture",
        "proof_execution_quality_expired_signal_price_posture",
    ),
    (
        "expired_next_bar_fill_causes",
        "proof_execution_quality_expired_next_bar_fill_causes",
    ),
    ("entry_dispatch_delay", "proof_execution_quality_entry_dispatch_delay"),
    (
        "current_posture_entry_dispatch_delay",
        "proof_execution_quality_current_posture_entry_dispatch_delay",
    ),
    (
        "current_posture_filled_symbols",
        "proof_execution_quality_current_posture_filled_symbols",
    ),
]
execution_quality_parts = [
    f"{name}={payload[key]}"
    for name, key in execution_quality_fields
    if payload.get(key) not in {None, ""}
]
execution_quality_line = (
    "paper proof execution quality: " + " ".join(execution_quality_parts)
    if execution_quality_parts
    else ""
)
post_supervisor_fields = [
    ("session", "proof_post_supervisor_execution_session"),
    ("since", "proof_post_supervisor_execution_since"),
    ("status", "proof_post_supervisor_execution_status"),
    ("warnings", "proof_post_supervisor_execution_warnings"),
    ("evaluated", "proof_post_supervisor_execution_evaluated"),
    ("signals", "proof_post_supervisor_execution_signals"),
    ("accepted", "proof_post_supervisor_execution_accepted"),
    ("accepted_for_fill", "proof_post_supervisor_execution_accepted_for_fill"),
    (
        "settled_accepted_for_fill",
        "proof_post_supervisor_execution_settled_accepted_for_fill",
    ),
    ("capacity_rejected", "proof_post_supervisor_execution_capacity_rejected"),
    ("capacity_reject_rate", "proof_post_supervisor_execution_capacity_reject_rate"),
    (
        "max_capacity_reject_rate",
        "proof_post_supervisor_execution_max_capacity_reject_rate",
    ),
    ("entry_orders", "proof_post_supervisor_execution_entry_orders"),
    ("settled", "proof_post_supervisor_execution_settled_entries"),
    ("settled_filled", "proof_post_supervisor_execution_settled_filled"),
    ("filled", "proof_post_supervisor_execution_filled"),
    ("expired", "proof_post_supervisor_execution_expired"),
    ("active", "proof_post_supervisor_execution_active"),
    ("maintenance_drained", "proof_post_supervisor_execution_maintenance_drained"),
    ("short_window_drained", "proof_post_supervisor_execution_short_window_drained"),
    (
        "settled_entry_fill_rate",
        "proof_post_supervisor_execution_settled_entry_fill_rate",
    ),
    ("entry_fill_rate", "proof_post_supervisor_execution_entry_fill_rate"),
    ("min_entry_fill_rate", "proof_post_supervisor_execution_min_entry_fill_rate"),
    (
        "accepted_to_fill_rate",
        "proof_post_supervisor_execution_accepted_to_fill_rate",
    ),
    ("filled_symbols", "proof_post_supervisor_execution_filled_symbols"),
    ("expired_symbols", "proof_post_supervisor_execution_expired_symbols"),
    ("expired_reasons", "proof_post_supervisor_execution_expired_reasons"),
    (
        "expired_signal_price_posture",
        "proof_post_supervisor_execution_expired_signal_price_posture",
    ),
    (
        "expired_next_bar_fill_causes",
        "proof_post_supervisor_execution_expired_next_bar_fill_causes",
    ),
    (
        "entry_dispatch_delay",
        "proof_post_supervisor_execution_entry_dispatch_delay",
    ),
    ("active_symbols", "proof_post_supervisor_execution_active_symbols"),
    ("short_window", "proof_post_supervisor_execution_short_window"),
    (
        "min_remaining_active_minutes",
        "proof_post_supervisor_execution_min_remaining_active_minutes",
    ),
    ("short_window_symbols", "proof_post_supervisor_execution_short_window_symbols"),
]
post_supervisor_parts = [
    f"{name}={payload[key]}"
    for name, key in post_supervisor_fields
    if payload.get(key) not in {None, ""}
]
post_supervisor_line = (
    "paper proof post-supervisor execution: " + " ".join(post_supervisor_parts)
    if post_supervisor_parts
    else ""
)
blocker_gaps_fields = [
    ("sample_trades_remaining", "proof_gap_sample_trades_remaining"),
    ("active_days_remaining", "proof_gap_active_days_remaining"),
    ("approved_replay_strategy_gap", "proof_gap_approved_replay_strategy_gap"),
    ("concentration_net_pnl_needed", "proof_gap_concentration_net_pnl_needed"),
    ("concentration_non_best_avg_pnl", "proof_gap_concentration_non_best_avg_pnl"),
    (
        "concentration_non_best_avg_trade_gap",
        "proof_gap_concentration_non_best_avg_trade_gap",
    ),
    ("concentration_runway_status", "proof_gap_concentration_runway_status"),
    (
        "concentration_remaining_trade_required_avg_pnl",
        "proof_gap_concentration_remaining_trade_required_avg_pnl",
    ),
    (
        "concentration_remaining_active_day_required_pnl",
        "proof_gap_concentration_remaining_active_day_required_pnl",
    ),
    ("single_win_pnl_share", "proof_gap_single_win_pnl_share"),
    ("max_single_win_pnl_share", "proof_gap_max_single_win_pnl_share"),
]
blocker_gaps_parts = [
    f"{name}={payload[key]}"
    for name, key in blocker_gaps_fields
    if payload.get(key) not in {None, ""}
]
blocker_gaps_line = (
    "paper proof blocker gaps: " + " ".join(blocker_gaps_parts)
    if blocker_gaps_parts
    else ""
)
active_day_fields = [
    ("status", "proof_active_day_status"),
    ("active_days", "proof_active_days"),
    ("required_active_days", "proof_required_active_days"),
    ("active_days_remaining", "proof_active_days_remaining"),
    ("sample_trades_remaining", "proof_sample_trades_remaining"),
    (
        "remaining_trades_per_required_active_day",
        "proof_remaining_trades_per_required_active_day",
    ),
    ("sessions", "proof_active_day_sessions"),
    ("trades_by_session", "proof_trades_by_session"),
    ("latest_exit_session", "proof_active_day_latest_exit_session"),
    ("next_possible_session", "proof_active_day_next_possible_session"),
    ("future_sessions", "proof_active_day_future_sessions"),
    (
        "earliest_active_days_met_session",
        "proof_earliest_active_days_met_session",
    ),
    ("projection_status", "proof_active_day_projection_status"),
    ("projection_warning", "proof_active_day_projection_warning"),
]
active_day_parts = [
    f"{name}={payload[key]}"
    for name, key in active_day_fields
    if payload.get(key) not in {None, ""}
]
active_day_line = (
    "paper proof active day detail: " + " ".join(active_day_parts)
    if active_day_parts
    else ""
)
concentration_fields = [
    ("status", "proof_concentration_status"),
    ("best_winning_trade", "proof_concentration_best_winning_trade"),
    ("best_winning_trade_pnl", "proof_concentration_best_winning_trade_pnl"),
    ("total_pnl", "proof_concentration_total_pnl"),
    ("non_best_trades", "proof_concentration_non_best_trades"),
    ("non_best_pnl", "proof_concentration_non_best_pnl"),
    ("non_best_avg_pnl", "proof_concentration_non_best_avg_pnl"),
    ("net_pnl_needed", "proof_concentration_net_pnl_needed"),
    ("non_best_avg_trade_gap", "proof_concentration_non_best_avg_trade_gap"),
    ("runway_status", "proof_concentration_runway_status"),
    (
        "remaining_trade_required_avg_pnl",
        "proof_concentration_remaining_trade_required_avg_pnl",
    ),
    (
        "remaining_active_day_required_pnl",
        "proof_concentration_remaining_active_day_required_pnl",
    ),
    ("single_win_pnl_share", "proof_concentration_single_win_pnl_share"),
    ("max_single_win_pnl_share", "proof_concentration_max_single_win_pnl_share"),
]
concentration_parts = [
    f"{name}={payload[key]}"
    for name, key in concentration_fields
    if payload.get(key) not in {None, ""}
]
concentration_line = (
    "paper proof concentration: " + " ".join(concentration_parts)
    if concentration_parts
    else ""
)
strategy_diversification_fields = [
    ("status", "proof_strategy_diversification_status"),
    ("active", "proof_strategy_diversification_active"),
    ("required", "proof_strategy_diversification_required"),
    ("approved_active", "proof_strategy_diversification_approved_active"),
    (
        "approved_replay_active",
        "proof_strategy_diversification_approved_replay_active",
    ),
    ("approved_required", "proof_strategy_diversification_approved_required"),
    ("gap", "proof_strategy_diversification_gap"),
    ("candidate_status", "proof_strategy_diversification_candidate_status"),
    (
        "promotion_action_status",
        "proof_strategy_diversification_promotion_action_status",
    ),
    (
        "approval_marker_action_status",
        "proof_strategy_diversification_approval_marker_action_status",
    ),
    (
        "promotion_write_access_status",
        "proof_strategy_diversification_promotion_write_access_status",
    ),
    ("active_names", "proof_strategy_diversification_active_names"),
    ("approved_names", "proof_strategy_diversification_approved_names"),
    (
        "approved_replay_names",
        "proof_strategy_diversification_approved_replay_names",
    ),
    (
        "validated_unapproved_stock_candidates",
        "proof_strategy_diversification_validated_unapproved_stock_candidates",
    ),
    (
        "validated_unapproved_option_candidates",
        "proof_strategy_diversification_validated_unapproved_option_candidates",
    ),
]
strategy_diversification_parts = [
    f"{name}={payload[key]}"
    for name, key in strategy_diversification_fields
    if payload.get(key) not in {None, ""}
]
strategy_diversification_line = (
    "paper proof strategy diversification: "
    + " ".join(strategy_diversification_parts)
    if strategy_diversification_parts
    else ""
)
second_strategy_promotion_action_fields = [
    ("status", "proof_second_strategy_promotion_action_status"),
    ("strategy", "proof_second_strategy_promotion_action_strategy"),
    ("confirmation", "proof_second_strategy_promotion_action_confirmation"),
    (
        "proof_horizon_status",
        "proof_second_strategy_promotion_action_proof_horizon_status",
    ),
    (
        "proof_horizon_detail",
        "proof_second_strategy_promotion_action_proof_horizon_detail",
    ),
    (
        "proof_horizon_summary",
        "proof_second_strategy_promotion_action_proof_horizon_summary",
    ),
    (
        "proof_horizon_summary_sha256",
        "proof_second_strategy_promotion_action_proof_horizon_summary_sha256",
    ),
    (
        "proof_horizon_total_pnl",
        "proof_second_strategy_promotion_action_proof_horizon_total_pnl",
    ),
    (
        "proof_horizon_eventual_pass_rate",
        "proof_second_strategy_promotion_action_proof_horizon_eventual_pass_rate",
    ),
    (
        "proof_horizon_terminal_blockers",
        "proof_second_strategy_promotion_action_proof_horizon_terminal_blockers",
    ),
    (
        "approval_marker_action_status",
        "proof_second_strategy_promotion_action_approval_marker_action_status",
    ),
    (
        "approval_marker_command_status",
        "proof_second_strategy_promotion_action_approval_marker_command_status",
    ),
    (
        "approval_marker_command_script",
        "proof_second_strategy_promotion_action_approval_marker_command_script",
    ),
    (
        "approval_marker_command_confirm_env",
        "proof_second_strategy_promotion_action_approval_marker_command_confirm_env",
    ),
    (
        "approval_marker_command_dry_run_env",
        "proof_second_strategy_promotion_action_approval_marker_command_dry_run_env",
    ),
    (
        "approval_marker_command_dry_run_value",
        "proof_second_strategy_promotion_action_approval_marker_command_dry_run_value",
    ),
    (
        "approval_marker_command_approval_only_env",
        "proof_second_strategy_promotion_action_approval_marker_command_approval_only_env",
    ),
    (
        "approval_marker_command_approval_only_value",
        "proof_second_strategy_promotion_action_approval_marker_command_approval_only_value",
    ),
    (
        "approval_marker_command_evidence_root",
        "proof_second_strategy_promotion_action_approval_marker_command_evidence_root",
    ),
    (
        "approval_marker_command_deploy_script",
        "proof_second_strategy_promotion_action_approval_marker_command_deploy_script",
    ),
    (
        "candidate_decision_dry_run_required",
        "proof_second_strategy_promotion_action_candidate_decision_dry_run_required",
    ),
    (
        "candidate_decision_dry_run_allow_disabled",
        "proof_second_strategy_promotion_action_candidate_decision_dry_run_allow_disabled",
    ),
    (
        "candidate_decision_dry_run_script",
        "proof_second_strategy_promotion_action_candidate_decision_dry_run_script",
    ),
    (
        "approval_marker_overlay_status",
        "proof_second_strategy_promotion_action_approval_marker_overlay_status",
    ),
    (
        "approval_marker_overlay_marker",
        "proof_second_strategy_promotion_action_approval_marker_overlay_marker",
    ),
    (
        "approval_marker_overlay_env_file",
        "proof_second_strategy_promotion_action_approval_marker_overlay_env_file",
    ),
    ("broker_flat_status", "proof_second_strategy_promotion_action_broker_flat_status"),
    ("env_file", "proof_second_strategy_promotion_action_env_file"),
    ("write_access_status", "proof_second_strategy_promotion_action_write_access_status"),
    (
        "promotion_handoff_status",
        "proof_second_strategy_promotion_action_handoff_status",
    ),
    (
        "promotion_handoff_step",
        "proof_second_strategy_promotion_action_handoff_step",
    ),
    ("promotion_env_keys", "proof_second_strategy_promotion_action_env_keys"),
    (
        "env_file_writable",
        "proof_second_strategy_promotion_action_env_file_writable",
    ),
    ("env_dir_writable", "proof_second_strategy_promotion_action_env_dir_writable"),
    ("approval_marker", "proof_second_strategy_promotion_action_approval_marker"),
    (
        "approval_marker_writable",
        "proof_second_strategy_promotion_action_approval_marker_writable",
    ),
    (
        "approval_marker_dir_writable",
        "proof_second_strategy_promotion_action_approval_marker_dir_writable",
    ),
    (
        "approval_marker_status",
        "proof_second_strategy_promotion_action_approval_marker_status",
    ),
    (
        "validation_summary",
        "proof_second_strategy_promotion_action_validation_summary",
    ),
    (
        "validation_summary_sha256",
        "proof_second_strategy_promotion_action_validation_summary_sha256",
    ),
    ("candidate_scale", "proof_second_strategy_promotion_action_candidate_scale"),
    ("candidate_trades", "proof_second_strategy_promotion_action_candidate_trades"),
    (
        "candidate_total_pnl",
        "proof_second_strategy_promotion_action_candidate_total_pnl",
    ),
    ("candidate_ci_low", "proof_second_strategy_promotion_action_candidate_ci_low"),
    (
        "candidate_p_mean_le_zero",
        "proof_second_strategy_promotion_action_candidate_p_mean_le_zero",
    ),
]
second_strategy_promotion_action_parts = [
    f"{name}={payload[key]}"
    for name, key in second_strategy_promotion_action_fields
    if payload.get(key) not in {None, ""}
]
second_strategy_promotion_action_line = (
    "paper proof second strategy promotion action: "
    + " ".join(second_strategy_promotion_action_parts)
    if second_strategy_promotion_action_parts
    else ""
)
approval_quick_command_status = payload.get(
    "proof_second_strategy_promotion_action_approval_marker_quick_command_status"
)
approval_quick_command = payload.get(
    "proof_second_strategy_promotion_action_approval_marker_quick_command"
)
second_strategy_approval_quick_command_line = (
    "paper proof second strategy approval quick command: "
    f"status={approval_quick_command_status or 'unknown'} "
    f"command={approval_quick_command or 'unavailable'}"
    if approval_quick_command_status or approval_quick_command
    else ""
)
nightly_automation_fields = [
    ("status", "proof_nightly_status"),
    ("lock_status", "proof_nightly_lock_status"),
    ("pid", "proof_nightly_pid"),
    ("source", "proof_nightly_source"),
    ("age_minutes", "proof_nightly_age_minutes"),
    ("log_age_minutes", "proof_nightly_log_age_minutes"),
    ("active_log", "proof_nightly_active_log"),
    ("max_age_minutes", "proof_nightly_max_age_minutes"),
    ("stall_minutes", "proof_nightly_stall_minutes"),
    ("run_age_limit_status", "proof_nightly_run_age_limit_status"),
    ("log_stall_status", "proof_nightly_log_stall_status"),
    ("stage", "proof_nightly_stage"),
    ("second_strategy_scan_status", "proof_second_strategy_scan_status"),
    ("second_strategy_scan_detail", "proof_second_strategy_scan_detail"),
    ("detail", "proof_nightly_detail"),
]
nightly_automation_parts = [
    f"{name}={payload[key]}"
    for name, key in nightly_automation_fields
    if payload.get(key) not in {None, ""}
]
nightly_automation_line = (
    "paper proof nightly automation: " + " ".join(nightly_automation_parts)
    if nightly_automation_parts
    else ""
)
print(
    "paper_proof_status_latest="
    f"{row[0]}|{row[1]}|{row[2]}|{row[3]}|{row[4]}|{row[5]}|"
    f"{row[6]}|{row[7]}|{row[8]}|{row[9]}|{row[10]}|{row[11]}|"
    f"{row[12]}|{row[13]}|{row[14]}|{row[15]}|{row[16]}|{row[17]}|"
    f"{row[18]}|{row[19]}|{row[20]}|{row[21]}|{row[22]}|"
    f"{row[23]}|{row[24]}|{row[25]}|{row[26]}|{row[28]}|"
    f"{age_minutes}|{post_supervisor_line}|{concentration_line}|{active_day_line}|"
    f"{strategy_diversification_line}|{second_strategy_promotion_action_line}|"
    f"{second_strategy_approval_quick_command_line}|{blocker_gaps_line}|"
    f"{execution_quality_line}|{nightly_automation_line}"
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
  local strategies="$5"
  local min_trades="${6:-}"
  local min_pnl="${7:-}"
  local lookup

  lookup="$(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
    -e POST_CLOSE_LOCK_CHECK_NAME="$check_name" \
    -e POST_CLOSE_LOCK_SESSION_DATE="$target_session" \
    -e POST_CLOSE_LOCK_PROOF_START="$proof_start" \
    -e POST_CLOSE_LOCK_STRATEGY="$strategy" \
    -e POST_CLOSE_LOCK_STRATEGIES="$strategies" \
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
strategies = os.environ["POST_CLOSE_LOCK_STRATEGIES"]
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
              AND (%s = %s OR payload->>'strategies' = %s)
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
                strategies,
                strategy,
                strategies,
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
    PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES="${PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES:-$(resolve_paper_approved_strategy_csv "$PAPER_READINESS_DECISION_DRY_RUN_STRATEGY")}"
    readiness_session_date="$(load_readiness_session_date)"
    expected_decision_dry_run_session="$(load_previous_readiness_session_date "$readiness_session_date")"
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
      if ! latest_decision_dry_run_status="$(validate_readiness_decision_dry_run_line "$latest_decision_dry_run_line" "$expected_decision_dry_run_session")"; then
        echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-07-07} reason=lock_busy_decision_dry_run_$latest_decision_dry_run_status"
        if [[ -n "$latest_decision_dry_run_line" ]]; then
          echo "$latest_decision_dry_run_line"
        fi
        echo "paper readiness prior pass lacks accepted entry-intent decision dry-run proof ($latest_decision_dry_run_status); lock busy remains blocking" >&2
        exit 48
      fi
      latest_decision_dry_run_strategies_line="$(load_latest_readiness_decision_dry_run_strategies "$readiness_session_date")"
      latest_decision_dry_run_strategies_status="missing"
      if ! latest_decision_dry_run_strategies_status="$(validate_readiness_decision_dry_run_strategies_line "$latest_decision_dry_run_strategies_line" "$PAPER_READINESS_DECISION_DRY_RUN_STRATEGY,$PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES")"; then
        echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-07-07} reason=lock_busy_decision_dry_run_strategies_$latest_decision_dry_run_strategies_status"
        if [[ -n "$latest_decision_dry_run_strategies_line" ]]; then
          echo "$latest_decision_dry_run_strategies_line"
        fi
        echo "paper readiness prior pass lacks approved-strategy decision dry-run proof ($latest_decision_dry_run_strategies_status); lock busy remains blocking" >&2
        exit 48
      fi
      echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-07-07} reason=lock_busy_already_passed"
      echo "$latest_decision_dry_run_line"
      echo "$latest_decision_dry_run_strategies_line"
      echo "paper readiness lock busy after prior pass for session $readiness_session_date; not blocking entries"
      exit 0
    fi
    if [[ "$latest_readiness_status" == "passed" && "$readiness_is_current" == "false" ]]; then
      echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-07-07} reason=lock_busy_stale_pass"
      echo "paper readiness prior pass is older than latest supervisor start; lock busy remains blocking" >&2
      exit 48
    fi
    if [[ "$latest_readiness_status" == "passed" && "$readiness_is_current" == "true" && "$readiness_is_recent" == "false" ]]; then
      echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-07-07} reason=lock_busy_stale_pass"
      echo "paper readiness prior pass is older than max age ${PAPER_READINESS_MAX_PASS_AGE_MINUTES}m; lock busy remains blocking" >&2
      exit 48
    fi
    echo "scheduled check context: session_date=$readiness_session_date proof_start=${PROFIT_PROBE_START_DATE:-2026-07-07} reason=lock_busy"
    ;;
  paper_activity)
    activity_strategy="${PAPER_ACTIVITY_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
    activity_strategies="${PAPER_ACTIVITY_STRATEGIES:-$(resolve_paper_approved_strategy_csv "$activity_strategy")}"
    activity_strategy_csv="$(normalize_strategy_csv "$activity_strategy" "$activity_strategies" "PAPER_ACTIVITY_STRATEGIES")"
    activity_lock_max_age="${PAPER_ACTIVITY_LOCK_MAX_AGE_MINUTES:-30}"
    if [[ ! "$activity_lock_max_age" =~ ^[0-9]+$ || "$activity_lock_max_age" -le 0 ]]; then
      echo "PAPER_ACTIVITY_LOCK_MAX_AGE_MINUTES must be a positive integer" >&2
      exit 1
    fi
    activity_proof_start="${PROFIT_PROBE_START_DATE:-2026-07-07}"
    latest_activity="$(
      load_latest_post_close_check_status \
        paper_activity \
        "$session_date" \
        "$activity_proof_start" \
        "$activity_strategy" \
        "$activity_strategy_csv"
    )"
    latest_activity_status=""
    latest_activity_exit_code=""
    latest_activity_created_at=""
    latest_activity_age_minutes=""
    IFS='|' read -r latest_activity_status latest_activity_exit_code latest_activity_created_at latest_activity_age_minutes <<< "$latest_activity"
    if [[ "$latest_activity_age_minutes" =~ ^[0-9]+$ ]] \
      && (( 10#$latest_activity_age_minutes <= 10#$activity_lock_max_age )); then
      if [[ "$latest_activity_status" == "passed" && "$latest_activity_exit_code" == "0" ]]; then
        echo "scheduled check context: session_date=$session_date proof_start=$activity_proof_start strategy=$activity_strategy strategies=$activity_strategy_csv reason=lock_busy_already_passed"
        echo "paper activity passed: lock busy after recent pass for session $session_date created_at=${latest_activity_created_at:-unknown} age_minutes=$latest_activity_age_minutes"
        exit 0
      fi
      if [[ "$latest_activity_status" == "pending" && "$latest_activity_exit_code" == "43" ]]; then
        echo "scheduled check context: session_date=$session_date proof_start=$activity_proof_start strategy=$activity_strategy strategies=$activity_strategy_csv reason=lock_busy_already_pending"
        echo "paper activity pending: lock busy after recent pending result for session $session_date created_at=${latest_activity_created_at:-unknown} age_minutes=$latest_activity_age_minutes"
        exit 43
      fi
      if [[ "$latest_activity_status" == "skipped" && "$latest_activity_exit_code" == "0" ]]; then
        echo "scheduled check context: session_date=$session_date proof_start=$activity_proof_start strategy=$activity_strategy strategies=$activity_strategy_csv reason=lock_busy_already_skipped"
        echo "paper activity skipped: lock busy after recent skipped result for session $session_date created_at=${latest_activity_created_at:-unknown} age_minutes=$latest_activity_age_minutes"
        exit 0
      fi
    fi
    echo "scheduled check context: session_date=$session_date proof_start=$activity_proof_start strategy=$activity_strategy strategies=$activity_strategy_csv reason=lock_busy"
    ;;
  session_guard)
    post_close_lock_max_age="${POST_CLOSE_LOCK_MAX_AGE_MINUTES:-30}"
    if [[ ! "$post_close_lock_max_age" =~ ^[0-9]+$ || "$post_close_lock_max_age" -le 0 ]]; then
      echo "POST_CLOSE_LOCK_MAX_AGE_MINUTES must be a positive integer" >&2
      exit 1
    fi
    guard_proof_start="${SESSION_GUARD_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-07-07}}"
    guard_strategy="${SESSION_GUARD_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
    guard_strategies="${SESSION_GUARD_STRATEGIES:-$(resolve_paper_approved_strategy_csv "$guard_strategy")}"
    guard_strategy_csv="$(normalize_strategy_csv "$guard_strategy" "$guard_strategies" "SESSION_GUARD_STRATEGIES")"
    guard_min_trades="${SESSION_GUARD_MIN_TRADES:-10}"
    guard_min_pnl="${SESSION_GUARD_FAIL_BELOW_PNL:-0}"
    latest_guard="$(
      load_latest_post_close_check_status \
        session_guard \
        "$session_date" \
        "$guard_proof_start" \
        "$guard_strategy" \
        "$guard_strategy_csv" \
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
        echo "scheduled check context: session_date=$session_date proof_start=$guard_proof_start strategy=$guard_strategy strategies=$guard_strategy_csv min_trades=$guard_min_trades min_pnl=$guard_min_pnl reason=lock_busy_already_passed"
        echo "session guard passed: lock busy after recent pass for session $session_date created_at=${latest_guard_created_at:-unknown} age_minutes=$latest_guard_age_minutes"
        exit 0
      fi
      if [[ "$latest_guard_status" == "pending" && "$latest_guard_exit_code" == "43" ]]; then
        echo "scheduled check context: session_date=$session_date proof_start=$guard_proof_start strategy=$guard_strategy strategies=$guard_strategy_csv min_trades=$guard_min_trades min_pnl=$guard_min_pnl reason=lock_busy_already_pending"
        echo "session guard pending: lock busy after recent pending result for session $session_date created_at=${latest_guard_created_at:-unknown} age_minutes=$latest_guard_age_minutes"
        exit 43
      fi
    fi
    echo "scheduled check context: session_date=$session_date proof_start=$guard_proof_start strategy=$guard_strategy strategies=$guard_strategy_csv min_trades=$guard_min_trades min_pnl=$guard_min_pnl reason=lock_busy"
    ;;
  paper_profit_probe)
    post_close_lock_max_age="${POST_CLOSE_LOCK_MAX_AGE_MINUTES:-30}"
    if [[ ! "$post_close_lock_max_age" =~ ^[0-9]+$ || "$post_close_lock_max_age" -le 0 ]]; then
      echo "POST_CLOSE_LOCK_MAX_AGE_MINUTES must be a positive integer" >&2
      exit 1
    fi
    probe_proof_start="${PROFIT_PROBE_START_DATE:-2026-07-07}"
    probe_strategy="${PROFIT_PROBE_STRATEGY:-bull_flag}"
    probe_strategies="${PROFIT_PROBE_STRATEGIES:-$(resolve_paper_approved_strategy_csv "$probe_strategy")}"
    probe_strategy_csv="$(normalize_strategy_csv "$probe_strategy" "$probe_strategies" "PROFIT_PROBE_STRATEGIES")"
    probe_min_trades="${PROFIT_PROBE_MIN_TRADES:-${PAPER_SCALE_MIN_TRADES:-30}}"
    if [[ "$probe_min_trades" =~ ^[0-9]+$ \
      && "${PAPER_SCALE_MIN_TRADES:-30}" =~ ^[0-9]+$ \
      && "$probe_min_trades" -lt "${PAPER_SCALE_MIN_TRADES:-30}" ]]; then
      probe_min_trades="${PAPER_SCALE_MIN_TRADES:-30}"
    fi
    probe_min_pnl="${PROFIT_PROBE_MIN_PNL:-0.01}"
    latest_probe="$(
      load_latest_post_close_check_status \
        paper_profit_probe \
        "$session_date" \
        "$probe_proof_start" \
        "$probe_strategy" \
        "$probe_strategy_csv" \
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
        echo "scheduled check context: session_date=$session_date proof_start=$probe_proof_start strategy=$probe_strategy strategies=$probe_strategy_csv min_trades=$probe_min_trades min_pnl=$probe_min_pnl reason=lock_busy_already_passed"
        echo "paper profit probe passed: lock busy after recent pass for session $session_date created_at=${latest_probe_created_at:-unknown} age_minutes=$latest_probe_age_minutes"
        exit 0
      fi
      if [[ "$latest_probe_status" == "pending" && "$latest_probe_exit_code" == "43" ]]; then
        echo "scheduled check context: session_date=$session_date proof_start=$probe_proof_start strategy=$probe_strategy strategies=$probe_strategy_csv min_trades=$probe_min_trades min_pnl=$probe_min_pnl reason=lock_busy_already_pending"
        echo "paper profit probe pending: lock busy after recent pending result for session $session_date created_at=${latest_probe_created_at:-unknown} age_minutes=$latest_probe_age_minutes"
        exit 43
      fi
    fi
    echo "scheduled check context: session_date=$session_date proof_start=$probe_proof_start strategy=$probe_strategy strategies=$probe_strategy_csv min_trades=$probe_min_trades min_pnl=$probe_min_pnl reason=lock_busy"
    ;;
  paper_proof_status)
    proof_start="${PROOF_STATUS_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-07-07}}"
    proof_strategy="${PROOF_STATUS_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
    proof_strategies="${PROOF_STATUS_APPROVED_STRATEGIES:-$(resolve_paper_approved_strategy_csv "$proof_strategy")}"
    proof_strategy_csv="$(normalize_strategy_csv "$proof_strategy" "$proof_strategies" "PROOF_STATUS_APPROVED_STRATEGIES")"
    proof_min_trades="${PROOF_STATUS_MIN_TRADES:-${PROFIT_PROBE_MIN_TRADES:-${PAPER_SCALE_MIN_TRADES:-30}}}"
    if [[ "$proof_min_trades" =~ ^[0-9]+$ \
      && "${PAPER_SCALE_MIN_TRADES:-30}" =~ ^[0-9]+$ \
      && "$proof_min_trades" -lt "${PAPER_SCALE_MIN_TRADES:-30}" ]]; then
      proof_min_trades="${PAPER_SCALE_MIN_TRADES:-30}"
    fi
    proof_min_pnl="${PROOF_STATUS_MIN_PNL:-${PROFIT_PROBE_MIN_PNL:-0.01}}"
    proof_session_guard_min_trades="${PROOF_STATUS_SESSION_GUARD_MIN_TRADES:-${SESSION_GUARD_MIN_TRADES:-10}}"
    proof_session_guard_min_pnl="${PROOF_STATUS_SESSION_GUARD_MIN_PNL:-${SESSION_GUARD_FAIL_BELOW_PNL:-0}}"
    if [[ ! "$proof_session_guard_min_trades" =~ ^[0-9]+$ ]]; then
      echo "PROOF_STATUS_SESSION_GUARD_MIN_TRADES must be a non-negative integer" >&2
      exit 1
    fi
    if [[ ! "$proof_session_guard_min_pnl" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
      echo "PROOF_STATUS_SESSION_GUARD_MIN_PNL must be a number" >&2
      exit 1
    fi
    PROOF_STATUS_LOCK_MAX_AGE_MINUTES="${PROOF_STATUS_LOCK_MAX_AGE_MINUTES:-30}"
    if [[ ! "$PROOF_STATUS_LOCK_MAX_AGE_MINUTES" =~ ^[0-9]+$ || "$PROOF_STATUS_LOCK_MAX_AGE_MINUTES" -le 0 ]]; then
      echo "PROOF_STATUS_LOCK_MAX_AGE_MINUTES must be a positive integer" >&2
      exit 1
    fi
    latest_proof_status="$(load_latest_proof_status "$proof_start" "$proof_strategy" "$proof_strategy_csv" "$proof_min_trades" "$proof_min_pnl" "$proof_session_guard_min_trades" "$proof_session_guard_min_pnl")"
    latest_status=""
    latest_exit_code=""
    latest_proof=""
    latest_readiness=""
    latest_blockers=""
    latest_evidence_blockers=""
    latest_sealed_evidence_blockers=""
    latest_overall_blockers=""
    latest_clean_window_blockers=""
    latest_sealed_clean_window_blockers=""
    latest_proof_reason=""
    latest_overall_reason=""
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
    latest_post_supervisor_execution_line=""
    latest_concentration_line=""
    latest_active_day_line=""
    latest_strategy_diversification_line=""
    latest_second_strategy_promotion_action_line=""
    latest_second_strategy_approval_quick_command_line=""
    latest_blocker_gaps_line=""
    latest_execution_quality_line=""
    latest_nightly_automation_line=""
    IFS='|' read -r latest_status latest_exit_code latest_proof latest_readiness latest_blockers latest_evidence_blockers latest_sealed_evidence_blockers latest_overall_blockers latest_clean_window_blockers latest_sealed_clean_window_blockers latest_proof_reason latest_overall_reason latest_warnings latest_progress_status latest_closed_trades latest_required_trades latest_pnl latest_required_pnl latest_first_exit_session latest_latest_exit_session latest_scenario_status latest_scenario_active latest_scenario_expected_session latest_scenario_problems latest_scoreable_closed_trades latest_unpaired_filled_exits latest_unpaired_symbols latest_created_at latest_age_minutes latest_post_supervisor_execution_line latest_concentration_line latest_active_day_line latest_strategy_diversification_line latest_second_strategy_promotion_action_line latest_second_strategy_approval_quick_command_line latest_blocker_gaps_line latest_execution_quality_line latest_nightly_automation_line <<< "$latest_proof_status"
    proof_lock_is_recent=false
    if [[ "$latest_age_minutes" =~ ^[0-9]+$ ]] \
      && (( 10#$latest_age_minutes <= 10#$PROOF_STATUS_LOCK_MAX_AGE_MINUTES )); then
      proof_lock_is_recent=true
    fi
    proof_lock_has_current_evidence=false
    if [[ "$latest_readiness" == "ready" && "$latest_blockers" == "none" ]]; then
      if [[ "$latest_status" == "pending" \
        && ( "$latest_exit_code" == "0" || "$latest_exit_code" == "43" ) \
        && "$latest_proof" == "pending" ]]; then
        proof_lock_has_current_evidence=true
      elif [[ "$latest_status" == "passed" && "$latest_exit_code" == "0" && "$latest_proof" == "passed" ]]; then
        proof_lock_has_current_evidence=true
      elif [[ "$latest_status" == "skipped" && "$latest_exit_code" == "0" && ( "$latest_proof" == "pending" || "$latest_proof" == "passed" ) ]]; then
        proof_lock_has_current_evidence=true
      fi
    fi
    if [[ "$proof_lock_is_recent" == "true" && "$proof_lock_has_current_evidence" == "true" ]]; then
      echo "scheduled check context: session_date=$session_date proof_start=$proof_start strategy=$proof_strategy strategies=$proof_strategy_csv min_trades=$proof_min_trades min_pnl=$proof_min_pnl session_guard_min_trades=$proof_session_guard_min_trades session_guard_min_pnl=$proof_session_guard_min_pnl reason=lock_busy_already_reported"
      echo "paper proof summary: readiness=$latest_readiness proof=$latest_proof reason=${latest_proof_reason:-lock_busy_already_reported} overall_reason=${latest_overall_reason:-${latest_proof_reason:-lock_busy_already_reported}} blockers=$latest_blockers evidence_blockers=${latest_evidence_blockers:-none} sealed_evidence_blockers=${latest_sealed_evidence_blockers:-none} overall_blockers=${latest_overall_blockers:-unknown} clean_window_blockers=${latest_clean_window_blockers:-unknown} sealed_clean_window_blockers=${latest_sealed_clean_window_blockers:-unknown} warnings=${latest_warnings:-none}"
      if [[ -n "$latest_nightly_automation_line" ]]; then
        echo "$latest_nightly_automation_line"
      fi
      echo "paper proof progress: status=${latest_progress_status:-$latest_proof} strategies=$proof_strategy_csv closed_trades=${latest_closed_trades:-unknown} required_trades=${latest_required_trades:-$proof_min_trades} pnl=${latest_pnl:-unknown} required_pnl=${latest_required_pnl:-$proof_min_pnl} window=lock_busy_already_reported first_exit_session=${latest_first_exit_session:-none} latest_exit_session=${latest_latest_exit_session:-none}"
      if [[ -n "$latest_blocker_gaps_line" ]]; then
        echo "$latest_blocker_gaps_line"
      fi
      if [[ -n "$latest_strategy_diversification_line" ]]; then
        echo "$latest_strategy_diversification_line"
      fi
      if [[ -n "$latest_second_strategy_promotion_action_line" ]]; then
        echo "$latest_second_strategy_promotion_action_line"
      fi
      if [[ -n "$latest_second_strategy_approval_quick_command_line" ]]; then
        echo "$latest_second_strategy_approval_quick_command_line"
      fi
      if [[ -n "$latest_active_day_line" ]]; then
        echo "$latest_active_day_line"
      fi
      if [[ -n "$latest_concentration_line" ]]; then
        echo "$latest_concentration_line"
      fi
      if [[ -n "$latest_scoreable_closed_trades$latest_unpaired_filled_exits$latest_unpaired_symbols" ]]; then
        echo "paper proof scoring: strategies=$proof_strategy_csv scoreable_closed_trades=${latest_scoreable_closed_trades:-${latest_closed_trades:-unknown}} unpaired_filled_exits=${latest_unpaired_filled_exits:-unknown} unpaired_symbols=${latest_unpaired_symbols:-none}"
      fi
      if [[ -n "$latest_scenario_status" ]]; then
        echo "paper proof scenarios: status=$latest_scenario_status active=${latest_scenario_active:-unknown} expected_session=${latest_scenario_expected_session:-unknown} problems=${latest_scenario_problems:-unknown}"
      fi
      if [[ -n "$latest_execution_quality_line" ]]; then
        echo "$latest_execution_quality_line"
      fi
      if [[ -n "$latest_post_supervisor_execution_line" ]]; then
        echo "$latest_post_supervisor_execution_line"
      fi
      echo "paper proof status check skipped: lock busy after recent proof status $latest_proof created_at=${latest_created_at:-unknown} age_minutes=$latest_age_minutes"
      exit 0
    fi
    echo "scheduled check context: session_date=$session_date proof_start=$proof_start strategy=$proof_strategy strategies=$proof_strategy_csv min_trades=$proof_min_trades min_pnl=$proof_min_pnl session_guard_min_trades=$proof_session_guard_min_trades session_guard_min_pnl=$proof_session_guard_min_pnl reason=lock_busy"
    ;;
  *)
    echo "scheduled check context: session_date=$session_date reason=lock_busy"
    ;;
esac

echo "scheduled check lock busy: check=$CHECK_NAME lock=$LOCK_FILE" >&2
exit 48
