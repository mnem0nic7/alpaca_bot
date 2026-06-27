#!/usr/bin/env bash
set -uo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PROFIT_PROBE_STRATEGY="${PROFIT_PROBE_STRATEGY:-bull_flag}"
PROFIT_PROBE_MIN_TRADES="${PROFIT_PROBE_MIN_TRADES:-10}"
PROFIT_PROBE_MIN_PNL="${PROFIT_PROBE_MIN_PNL:-0.01}"
PROFIT_PROBE_START_DATE="${PROFIT_PROBE_START_DATE:-2026-06-29}"

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
    "paper profit probe warning: market calendar lookup failed; using weekday fallback" \
    >&2
  fallback_session_date
}

PROFIT_PROBE_DATE="${PROFIT_PROBE_DATE:-$(default_session_date)}"

echo "scheduled check context: session_date=$PROFIT_PROBE_DATE proof_start=$PROFIT_PROBE_START_DATE strategy=$PROFIT_PROBE_STRATEGY min_trades=$PROFIT_PROBE_MIN_TRADES min_pnl=$PROFIT_PROBE_MIN_PNL"

if [[ "$PROFIT_PROBE_DATE" < "$PROFIT_PROBE_START_DATE" ]]; then
  echo \
    "paper profit probe pending: latest completed session $PROFIT_PROBE_DATE is before proof start $PROFIT_PROBE_START_DATE"
  BROKER_FLAT_CONTEXT="${PROFIT_PROBE_STRATEGY} paper proof pending ${PROFIT_PROBE_START_DATE}" \
    ./scripts/broker_flat_check.sh "$ENV_FILE"
  exit 43
fi

"${compose[@]}" run -T --rm \
  --entrypoint alpaca-bot-session-eval admin \
  --start-date "$PROFIT_PROBE_START_DATE" \
  --end-date "$PROFIT_PROBE_DATE" \
  --mode "${TRADING_MODE:-paper}" \
  --strategy-version "$STRATEGY_VERSION" \
  --strategy "$PROFIT_PROBE_STRATEGY" \
  --fail-on-open-positions \
  --require-min-trades "$PROFIT_PROBE_MIN_TRADES" \
  --fail-below-pnl "$PROFIT_PROBE_MIN_PNL" \
  --min-trades-for-gate "$PROFIT_PROBE_MIN_TRADES"
rc=$?

broker_flat_failed=false
if ! BROKER_FLAT_CONTEXT="${PROFIT_PROBE_STRATEGY} paper proof ${PROFIT_PROBE_START_DATE}..${PROFIT_PROBE_DATE}" \
  ./scripts/broker_flat_check.sh "$ENV_FILE"; then
  broker_flat_failed=true
  rc=44
fi

if [[ "$rc" -eq 42 || "$rc" -eq 43 ]]; then
  if ! "${compose[@]}" run -T --rm \
    --entrypoint alpaca-bot-funnel-report admin \
    --start "$PROFIT_PROBE_START_DATE" \
    --end "$PROFIT_PROBE_DATE" \
    --strategy "$PROFIT_PROBE_STRATEGY" \
    --mode "${TRADING_MODE:-paper}"; then
    echo "paper profit probe warning: funnel diagnostic failed" >&2
  fi
fi

if [[ "$rc" -eq 42 || "$rc" -eq 44 ]]; then
  reason="${PROFIT_PROBE_STRATEGY} paper proof failed ${PROFIT_PROBE_START_DATE}..${PROFIT_PROBE_DATE}: pnl below ${PROFIT_PROBE_MIN_PNL} after ${PROFIT_PROBE_MIN_TRADES}+ trades"
  if [[ "$rc" -eq 44 ]]; then
    reason="${PROFIT_PROBE_STRATEGY} paper proof failed ${PROFIT_PROBE_START_DATE}..${PROFIT_PROBE_DATE}: open positions remain after close"
    if [[ "$broker_flat_failed" == "true" ]]; then
      reason="${PROFIT_PROBE_STRATEGY} paper proof failed ${PROFIT_PROBE_START_DATE}..${PROFIT_PROBE_DATE}: broker exposure remains after close"
    fi
  fi
  if ! "${compose[@]}" run -T --rm admin \
    close-only \
    --mode "${TRADING_MODE:-paper}" \
    --strategy-version "$STRATEGY_VERSION" \
    --reason "$reason"; then
    echo "paper profit probe failed: could not apply close-only guard" >&2
    exit 45
  fi
fi

exit "$rc"
