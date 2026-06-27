#!/usr/bin/env bash
set -uo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PROFIT_PROBE_STRATEGY="${PROFIT_PROBE_STRATEGY:-bull_flag}"
PROFIT_PROBE_MIN_TRADES="${PROFIT_PROBE_MIN_TRADES:-10}"
PROFIT_PROBE_MIN_PNL="${PROFIT_PROBE_MIN_PNL:-0.01}"
PROFIT_PROBE_START_DATE="${PROFIT_PROBE_START_DATE:-2026-06-26}"

default_session_date() {
  local dow
  dow="$(TZ=America/New_York date +%u)"
  case "$dow" in
    1) TZ=America/New_York date -d "3 days ago" +%F ;;
    6) TZ=America/New_York date -d "1 day ago" +%F ;;
    7) TZ=America/New_York date -d "2 days ago" +%F ;;
    *) TZ=America/New_York date +%F ;;
  esac
}

PROFIT_PROBE_DATE="${PROFIT_PROBE_DATE:-$(default_session_date)}"

cd "$(dirname "$0")/.."

set -a
source "$ENV_FILE"
set +a

docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
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
