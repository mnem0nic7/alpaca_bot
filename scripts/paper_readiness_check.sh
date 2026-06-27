#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "paper readiness check skipped for TRADING_MODE=${TRADING_MODE:-unset}"
  exit 0
fi

docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run -T --rm \
  --entrypoint alpaca-bot-ops-check admin \
  --url http://web:8080/healthz \
  --expect-worker \
  --wait-seconds 60 \
  --expect-trading-mode paper \
  --expect-strategy-version "$STRATEGY_VERSION" \
  --expect-trading-status enabled \
  --expect-kill-switch false \
  --expect-only-enabled-strategy bull_flag
