#!/usr/bin/env bash
set -uo pipefail

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
  SESSION_GUARD_STRATEGY \
  SESSION_GUARD_MIN_TRADES \
  SESSION_GUARD_FAIL_BELOW_PNL \
  SESSION_GUARD_FAIL_ON_DIAGNOSTICS \
  SESSION_GUARD_REUSE_PASS_MAX_AGE_MINUTES \
  SESSION_GUARD_START_DATE \
  SESSION_GUARD_DATE

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

SESSION_GUARD_STRATEGY="${SESSION_GUARD_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
SESSION_GUARD_MIN_TRADES="${SESSION_GUARD_MIN_TRADES:-10}"
SESSION_GUARD_FAIL_BELOW_PNL="${SESSION_GUARD_FAIL_BELOW_PNL:-0}"
SESSION_GUARD_FAIL_ON_DIAGNOSTICS="${SESSION_GUARD_FAIL_ON_DIAGNOSTICS:-true}"
SESSION_GUARD_REUSE_PASS_MAX_AGE_MINUTES="${SESSION_GUARD_REUSE_PASS_MAX_AGE_MINUTES:-180}"
SESSION_GUARD_START_DATE="${SESSION_GUARD_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-30}}"
SESSION_GUARD_DATE="${SESSION_GUARD_DATE:-}"

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

case "${SESSION_GUARD_FAIL_ON_DIAGNOSTICS,,}" in
  true|false) ;;
  *)
    echo "SESSION_GUARD_FAIL_ON_DIAGNOSTICS must be true or false" >&2
    exit 1
    ;;
esac

if [[ ! "$SESSION_GUARD_STRATEGY" =~ ^[A-Za-z0-9_:-]+$ ]]; then
  echo "SESSION_GUARD_STRATEGY contains unsupported characters" >&2
  exit 1
fi
if [[ ! "$SESSION_GUARD_START_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "SESSION_GUARD_START_DATE must use YYYY-MM-DD" >&2
  exit 1
fi
if [[ ! "$SESSION_GUARD_MIN_TRADES" =~ ^[0-9]+$ ]]; then
  echo "SESSION_GUARD_MIN_TRADES must be a non-negative integer" >&2
  exit 1
fi
if [[ ! "$SESSION_GUARD_FAIL_BELOW_PNL" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
  echo "SESSION_GUARD_FAIL_BELOW_PNL must be a number" >&2
  exit 1
fi
if [[ ! "$SESSION_GUARD_REUSE_PASS_MAX_AGE_MINUTES" =~ ^[0-9]+$ ]] \
  || [[ "$SESSION_GUARD_REUSE_PASS_MAX_AGE_MINUTES" -lt 1 ]]; then
  echo "SESSION_GUARD_REUSE_PASS_MAX_AGE_MINUTES must be a positive integer" >&2
  exit 1
fi

fallback_session_date() {
  local dow
  local hhmm
  dow="$(TZ=America/New_York date +%u)"
  hhmm="$(TZ=America/New_York date +%H%M)"

  if [[ "$dow" -ge 1 && "$dow" -le 5 && "$hhmm" -ge 1630 ]]; then
    TZ=America/New_York date +%F
    return
  fi

  case "$dow" in
    1) TZ=America/New_York date -d "3 days ago" +%F ;;
    6) TZ=America/New_York date -d "1 day ago" +%F ;;
    7) TZ=America/New_York date -d "2 days ago" +%F ;;
    *) TZ=America/New_York date -d "1 day ago" +%F ;;
  esac
}

load_latest_completed_session_date() {
  "${compose[@]}" run -T --rm \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter

settings = Settings.from_env()
market_timezone = ZoneInfo(settings.market_timezone.key)
now = datetime.now(market_timezone)
calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
    start=now.date() - timedelta(days=14),
    end=now.date(),
)

completed = []
for session in calendar:
    close_at = session.close_at
    if close_at.tzinfo is None:
        close_at = close_at.replace(tzinfo=market_timezone)
    else:
        close_at = close_at.astimezone(market_timezone)
    if now >= close_at + timedelta(minutes=30):
        completed.append(session.session_date)

if not completed:
    raise SystemExit("no completed market sessions found")

print(max(completed).isoformat())
PY
}

default_session_date() {
  local calendar_date
  if calendar_date="$(load_latest_completed_session_date)" \
    && [[ "$calendar_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "$calendar_date"
    return
  fi

  echo \
    "session guard warning: market calendar lookup failed; using weekday fallback" \
    >&2
  fallback_session_date
}

load_latest_session_guard_pass() {
  "${compose[@]}" run -T --rm \
    -e SESSION_GUARD_PASS_SESSION_DATE="$SESSION_GUARD_DATE" \
    -e SESSION_GUARD_PASS_PROOF_START="$SESSION_GUARD_START_DATE" \
    -e SESSION_GUARD_PASS_STRATEGY="$SESSION_GUARD_STRATEGY" \
    -e SESSION_GUARD_PASS_MIN_TRADES="$SESSION_GUARD_MIN_TRADES" \
    -e SESSION_GUARD_PASS_MIN_PNL="$SESSION_GUARD_FAIL_BELOW_PNL" \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
import os

from alpaca_bot.config import Settings
from alpaca_bot.storage.db import connect_postgres

settings = Settings.from_env()
session_date = os.environ["SESSION_GUARD_PASS_SESSION_DATE"]
proof_start = os.environ["SESSION_GUARD_PASS_PROOF_START"]
strategy = os.environ["SESSION_GUARD_PASS_STRATEGY"]
min_trades = os.environ["SESSION_GUARD_PASS_MIN_TRADES"]
min_pnl = os.environ["SESSION_GUARD_PASS_MIN_PNL"]

conn = connect_postgres(settings.database_url)
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              created_at,
              to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'check_name' = 'session_guard'
              AND payload->>'status' = 'passed'
              AND payload->>'exit_code' = '0'
              AND payload->>'session_date' = %s
              AND payload->>'proof_start' = %s
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
              AND payload->>'strategy' = %s
              AND payload->>'min_trades' = %s
              AND payload->>'min_pnl' = %s
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                session_date,
                proof_start,
                settings.trading_mode.value,
                settings.strategy_version,
                strategy,
                min_trades,
                min_pnl,
            ),
        )
        row = cur.fetchone()
finally:
    conn.close()

if row is None:
    print("session_guard_latest_pass=||")
    raise SystemExit(0)

created_raw = row[0]
created_utc = created_raw
if created_utc.tzinfo is None:
    created_utc = created_utc.replace(tzinfo=timezone.utc)
else:
    created_utc = created_utc.astimezone(timezone.utc)
age_seconds = (datetime.now(timezone.utc) - created_utc).total_seconds()
print(
    "session_guard_latest_pass="
    f"{row[1]}|{max(0, int(age_seconds // 60))}"
)
PY
}

SESSION_GUARD_DATE="${SESSION_GUARD_DATE:-$(default_session_date)}"
if [[ ! "$SESSION_GUARD_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "SESSION_GUARD_DATE must use YYYY-MM-DD" >&2
  exit 1
fi

echo "scheduled check context: session_date=$SESSION_GUARD_DATE proof_start=$SESSION_GUARD_START_DATE strategy=$SESSION_GUARD_STRATEGY min_trades=$SESSION_GUARD_MIN_TRADES min_pnl=$SESSION_GUARD_FAIL_BELOW_PNL"

if [[ "$SESSION_GUARD_DATE" < "$SESSION_GUARD_START_DATE" ]]; then
  echo \
    "session guard pending: latest completed session $SESSION_GUARD_DATE is before proof start $SESSION_GUARD_START_DATE"
  if ! BROKER_FLAT_CONTEXT="${SESSION_GUARD_STRATEGY} session guard pending ${SESSION_GUARD_START_DATE}" \
    ./scripts/broker_flat_check.sh "$ENV_FILE"; then
    reason="${SESSION_GUARD_STRATEGY} session guard pending ${SESSION_GUARD_START_DATE}: broker exposure remains before proof start"
    if ! "${compose[@]}" run -T --rm admin \
      close-only \
      --mode "${TRADING_MODE:-paper}" \
      --strategy-version "$STRATEGY_VERSION" \
      --reason "$reason"; then
      echo "session guard failed: could not apply close-only guard" >&2
      exit 45
    fi
    exit 44
  fi
  exit 43
fi

latest_session_guard_pass="$(load_latest_session_guard_pass || true)"
latest_session_guard_pass="$(
  printf '%s\n' "$latest_session_guard_pass" \
    | sed -n 's/^session_guard_latest_pass=//p' \
    | tail -n 1
)"
latest_pass_created_at=""
latest_pass_age_minutes=""
IFS='|' read -r latest_pass_created_at latest_pass_age_minutes <<< "$latest_session_guard_pass"
if [[ "$latest_pass_age_minutes" =~ ^[0-9]+$ ]] \
  && (( 10#$latest_pass_age_minutes <= 10#$SESSION_GUARD_REUSE_PASS_MAX_AGE_MINUTES )); then
  if ! BROKER_FLAT_CONTEXT="${SESSION_GUARD_STRATEGY} session guard prior pass ${SESSION_GUARD_DATE}" \
    ./scripts/broker_flat_check.sh "$ENV_FILE"; then
    reason="${SESSION_GUARD_STRATEGY} session guard failed ${SESSION_GUARD_DATE}: broker exposure remains after prior pass"
    if ! "${compose[@]}" run -T --rm admin \
      close-only \
      --mode "${TRADING_MODE:-paper}" \
      --strategy-version "$STRATEGY_VERSION" \
      --reason "$reason"; then
      echo "session guard failed: could not apply close-only guard" >&2
      exit 45
    fi
    exit 44
  fi
  echo "scheduled check context: session_date=$SESSION_GUARD_DATE proof_start=$SESSION_GUARD_START_DATE strategy=$SESSION_GUARD_STRATEGY min_trades=$SESSION_GUARD_MIN_TRADES min_pnl=$SESSION_GUARD_FAIL_BELOW_PNL reason=already_passed"
  echo "session guard already passed for session $SESSION_GUARD_DATE created_at=${latest_pass_created_at:-unknown} age_minutes=$latest_pass_age_minutes"
  exit 0
fi

session_eval_args=(
  --date "$SESSION_GUARD_DATE"
  --mode "${TRADING_MODE:-paper}"
  --strategy-version "$STRATEGY_VERSION"
  --strategy "$SESSION_GUARD_STRATEGY"
  --fail-on-open-positions
  --fail-below-pnl "$SESSION_GUARD_FAIL_BELOW_PNL"
  --min-trades-for-gate "$SESSION_GUARD_MIN_TRADES"
)

if [[ "${SESSION_GUARD_FAIL_ON_DIAGNOSTICS,,}" == "true" ]]; then
  session_eval_args+=(--fail-on-diagnostics)
fi

"${compose[@]}" run -T --rm \
  --entrypoint alpaca-bot-session-eval admin \
  "${session_eval_args[@]}"
rc=$?

broker_flat_failed=false
if ! BROKER_FLAT_CONTEXT="${SESSION_GUARD_STRATEGY} session guard ${SESSION_GUARD_DATE}" \
  ./scripts/broker_flat_check.sh "$ENV_FILE"; then
  broker_flat_failed=true
  rc=44
fi

if [[ "$rc" -eq 42 || "$rc" -eq 46 ]]; then
  if ! "${compose[@]}" run -T --rm \
    --entrypoint alpaca-bot-funnel-report admin \
    --start "$SESSION_GUARD_DATE" \
    --end "$SESSION_GUARD_DATE" \
    --strategy "$SESSION_GUARD_STRATEGY" \
    --mode "${TRADING_MODE:-paper}"; then
    echo "session guard warning: funnel diagnostic failed" >&2
  fi
fi

if [[ "$rc" -eq 42 ]]; then
  echo \
    "session guard pending: same-day pnl below ${SESSION_GUARD_FAIL_BELOW_PNL} after ${SESSION_GUARD_MIN_TRADES}+ trades; continuing cumulative proof window"
  rc=43
fi

if [[ "$rc" -eq 44 || "$rc" -eq 46 ]]; then
  case "$rc" in
    44)
      reason="${SESSION_GUARD_STRATEGY} session guard failed ${SESSION_GUARD_DATE}: open positions remain after close"
      if [[ "$broker_flat_failed" == "true" ]]; then
        reason="${SESSION_GUARD_STRATEGY} session guard failed ${SESSION_GUARD_DATE}: broker exposure remains after close"
      fi
      ;;
    46)
      reason="${SESSION_GUARD_STRATEGY} session guard failed ${SESSION_GUARD_DATE}: operational diagnostics contain proof-blocking issues"
      ;;
  esac

  if ! "${compose[@]}" run -T --rm admin \
    close-only \
    --mode "${TRADING_MODE:-paper}" \
    --strategy-version "$STRATEGY_VERSION" \
    --reason "$reason"; then
    echo "session guard failed: could not apply close-only guard" >&2
    exit 45
  fi
fi

exit "$rc"
