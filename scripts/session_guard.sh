#!/usr/bin/env bash
set -uo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
SESSION_GUARD_STRATEGY="${SESSION_GUARD_STRATEGY:-bull_flag}"
SESSION_GUARD_MIN_TRADES="${SESSION_GUARD_MIN_TRADES:-10}"
SESSION_GUARD_FAIL_BELOW_PNL="${SESSION_GUARD_FAIL_BELOW_PNL:-0}"
SESSION_GUARD_FAIL_ON_DIAGNOSTICS="${SESSION_GUARD_FAIL_ON_DIAGNOSTICS:-true}"
SESSION_GUARD_DATE="${SESSION_GUARD_DATE:-}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

case "${SESSION_GUARD_FAIL_ON_DIAGNOSTICS,,}" in
  true|false) ;;
  *)
    echo "SESSION_GUARD_FAIL_ON_DIAGNOSTICS must be true or false" >&2
    exit 1
    ;;
esac

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

SESSION_GUARD_DATE="${SESSION_GUARD_DATE:-$(default_session_date)}"
if [[ ! "$SESSION_GUARD_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "SESSION_GUARD_DATE must use YYYY-MM-DD" >&2
  exit 1
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

echo "scheduled check context: session_date=$SESSION_GUARD_DATE strategy=$SESSION_GUARD_STRATEGY"

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

if [[ "$rc" -eq 42 || "$rc" -eq 44 || "$rc" -eq 46 ]]; then
  case "$rc" in
    42)
      reason="${SESSION_GUARD_STRATEGY} session guard failed ${SESSION_GUARD_DATE}: pnl below ${SESSION_GUARD_FAIL_BELOW_PNL} after ${SESSION_GUARD_MIN_TRADES}+ trades"
      ;;
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
