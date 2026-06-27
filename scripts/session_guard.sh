#!/usr/bin/env bash
set -uo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
SESSION_GUARD_STRATEGY="${SESSION_GUARD_STRATEGY:-bull_flag}"
SESSION_GUARD_MIN_TRADES="${SESSION_GUARD_MIN_TRADES:-10}"
SESSION_GUARD_FAIL_BELOW_PNL="${SESSION_GUARD_FAIL_BELOW_PNL:-0}"
SESSION_GUARD_FAIL_ON_DIAGNOSTICS="${SESSION_GUARD_FAIL_ON_DIAGNOSTICS:-true}"
SESSION_GUARD_DATE="${SESSION_GUARD_DATE:-$(TZ=America/New_York date +%F)}"

cd "$(dirname "$0")/.."

set -a
source "$ENV_FILE"
set +a

case "${SESSION_GUARD_FAIL_ON_DIAGNOSTICS,,}" in
  true|false) ;;
  *)
    echo "SESSION_GUARD_FAIL_ON_DIAGNOSTICS must be true or false" >&2
    exit 1
    ;;
esac

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

docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
  --entrypoint alpaca-bot-session-eval admin \
  "${session_eval_args[@]}"
rc=$?

broker_flat_failed=false
if ! BROKER_FLAT_CONTEXT="${SESSION_GUARD_STRATEGY} session guard ${SESSION_GUARD_DATE}" \
  ./scripts/broker_flat_check.sh "$ENV_FILE"; then
  broker_flat_failed=true
  rc=44
fi

if [[ "$rc" -eq 42 || "$rc" -eq 44 ]]; then
  reason="${SESSION_GUARD_STRATEGY} session guard failed ${SESSION_GUARD_DATE}: pnl below ${SESSION_GUARD_FAIL_BELOW_PNL} after ${SESSION_GUARD_MIN_TRADES}+ trades"
  if [[ "$rc" -eq 44 ]]; then
    reason="${SESSION_GUARD_STRATEGY} session guard failed ${SESSION_GUARD_DATE}: open positions remain after close"
    if [[ "$broker_flat_failed" == "true" ]]; then
      reason="${SESSION_GUARD_STRATEGY} session guard failed ${SESSION_GUARD_DATE}: broker exposure remains after close"
    fi
  fi
  if ! docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm admin \
    close-only \
    --mode "${TRADING_MODE:-paper}" \
    --strategy-version "$STRATEGY_VERSION" \
    --reason "$reason"; then
    echo "session guard failed: could not apply close-only guard" >&2
    exit 45
  fi
fi

exit "$rc"
