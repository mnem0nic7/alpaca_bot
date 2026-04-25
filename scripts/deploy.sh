#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose.yaml"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

require_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "missing required env var: $name" >&2
    exit 1
  fi
}

credentials_ready() {
  case "${TRADING_MODE:-}" in
    paper)
      [[ -n "${ALPACA_PAPER_API_KEY:-}" ]] \
        && [[ -n "${ALPACA_PAPER_SECRET_KEY:-}" ]] \
        && [[ "${ALPACA_PAPER_API_KEY}" != "replace_me" ]] \
        && [[ "${ALPACA_PAPER_SECRET_KEY}" != "replace_me" ]]
      ;;
    live)
      [[ "${ENABLE_LIVE_TRADING:-false}" == "true" ]] \
        && [[ -n "${ALPACA_LIVE_API_KEY:-}" ]] \
        && [[ -n "${ALPACA_LIVE_SECRET_KEY:-}" ]] \
        && [[ "${ALPACA_LIVE_API_KEY}" != "replace_me" ]] \
        && [[ "${ALPACA_LIVE_SECRET_KEY}" != "replace_me" ]]
      ;;
    *)
      return 1
      ;;
  esac
}

require_var POSTGRES_DB
require_var POSTGRES_USER
require_var POSTGRES_PASSWORD
require_var DATABASE_URL
require_var TRADING_MODE
require_var STRATEGY_VERSION

docker compose -f "$COMPOSE_FILE" build supervisor web migrate admin
docker compose -f "$COMPOSE_FILE" up -d postgres
docker compose -f "$COMPOSE_FILE" run --rm migrate
docker compose -f "$COMPOSE_FILE" up -d --force-recreate web

if credentials_ready; then
  docker compose -f "$COMPOSE_FILE" up -d --force-recreate supervisor
  docker compose -f "$COMPOSE_FILE" run --rm --entrypoint alpaca-bot-ops-check admin \
    --url http://web:8080/healthz \
    --expect-worker \
    --wait-seconds 60
else
  docker compose -f "$COMPOSE_FILE" rm -sf supervisor >/dev/null 2>&1 || true
  docker compose -f "$COMPOSE_FILE" run --rm --entrypoint alpaca-bot-ops-check admin \
    --url http://web:8080/healthz \
    --no-expect-worker \
    --wait-seconds 30
  echo "Postgres is up and migrations are applied, but supervisor was not started because Alpaca credentials are missing or placeholders." >&2
fi

docker compose -f "$COMPOSE_FILE" ps
