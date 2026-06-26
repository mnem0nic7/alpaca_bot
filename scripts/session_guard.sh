#!/usr/bin/env bash
set -uo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
SESSION_GUARD_STRATEGY="${SESSION_GUARD_STRATEGY:-bull_flag}"
SESSION_GUARD_MIN_TRADES="${SESSION_GUARD_MIN_TRADES:-10}"
SESSION_GUARD_FAIL_BELOW_PNL="${SESSION_GUARD_FAIL_BELOW_PNL:-0}"
SESSION_GUARD_DATE="${SESSION_GUARD_DATE:-$(TZ=America/New_York date +%F)}"

cd "$(dirname "$0")/.."

set -a
source "$ENV_FILE"
set +a

docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
  --entrypoint alpaca-bot-session-eval admin \
  --date "$SESSION_GUARD_DATE" \
  --mode "${TRADING_MODE:-paper}" \
  --strategy-version "$STRATEGY_VERSION" \
  --strategy "$SESSION_GUARD_STRATEGY" \
  --fail-below-pnl "$SESSION_GUARD_FAIL_BELOW_PNL" \
  --min-trades-for-gate "$SESSION_GUARD_MIN_TRADES"
rc=$?

if [[ "$rc" -eq 42 ]]; then
  docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm admin \
    close-only \
    --mode "${TRADING_MODE:-paper}" \
    --strategy-version "$STRATEGY_VERSION" \
    --reason "${SESSION_GUARD_STRATEGY} session guard failed ${SESSION_GUARD_DATE}: pnl below ${SESSION_GUARD_FAIL_BELOW_PNL} after ${SESSION_GUARD_MIN_TRADES}+ trades"
fi

exit "$rc"
