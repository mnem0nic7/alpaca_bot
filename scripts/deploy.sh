#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose.yaml"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
REQUIRE_CRON_HEALTH="${REQUIRE_CRON_HEALTH:-true}"

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

paper_proof_enabled() {
  local paper_proof_freeze="${PAPER_PROOF_FREEZE:-false}"
  [[ "${TRADING_MODE:-}" == "paper" && "${paper_proof_freeze,,}" == "true" ]]
}

refresh_paper_readiness() {
  "$ROOT_DIR/scripts/run_locked_check_with_audit.sh" \
    paper_readiness \
    /var/lock/alpaca-bot-paper-readiness.lock \
    "$ENV_FILE" \
    "$ROOT_DIR/scripts/paper_readiness_if_needed.sh" \
    "$ENV_FILE"
}

verify_paper_proof_ready() {
  local proof_status_output
  local proof_summary

  proof_status_output="$("$ROOT_DIR/scripts/paper_proof_status.sh" "$ENV_FILE")"
  printf '%s\n' "$proof_status_output"
  proof_summary="$(
    printf '%s\n' "$proof_status_output" \
      | grep -E '^paper proof summary: ' \
      | tail -n 1 \
      || true
  )"

  if [[ "$proof_summary" == *"readiness_audit_stale"* ]]; then
    echo "paper proof readiness stale after deploy; refreshing once" >&2
    refresh_paper_readiness
    proof_status_output="$("$ROOT_DIR/scripts/paper_proof_status.sh" "$ENV_FILE")"
    printf '%s\n' "$proof_status_output"
    proof_summary="$(
      printf '%s\n' "$proof_status_output" \
        | grep -E '^paper proof summary: ' \
        | tail -n 1 \
        || true
    )"
  fi

  if [[ "$proof_summary" != *"readiness=ready"* \
    || "$proof_summary" != *"blockers=none"* ]]; then
    echo "deploy failed: paper proof status not ready after deploy: ${proof_summary:-missing summary}" >&2
    exit 1
  fi
}

require_var POSTGRES_DB
require_var POSTGRES_USER
require_var POSTGRES_PASSWORD
require_var DATABASE_URL
require_var TRADING_MODE
require_var STRATEGY_VERSION

case "${REQUIRE_CRON_HEALTH,,}" in
  true|false) ;;
  *)
    echo "REQUIRE_CRON_HEALTH must be true or false" >&2
    exit 1
    ;;
esac

docker compose -f "$COMPOSE_FILE" build supervisor web migrate admin
docker compose -f "$COMPOSE_FILE" up -d postgres
docker compose -f "$COMPOSE_FILE" run --rm migrate
docker compose -f "$COMPOSE_FILE" up -d --force-recreate web

if credentials_ready; then
  docker compose -f "$COMPOSE_FILE" rm -sf supervisor >/dev/null 2>&1 || true
  docker compose -f "$COMPOSE_FILE" up -d --force-recreate supervisor
  docker compose -f "$COMPOSE_FILE" run --rm --entrypoint alpaca-bot-ops-check admin \
    --url http://web:8080/healthz \
    --expect-worker \
    --wait-seconds 60 \
    --expect-trading-mode "${TRADING_MODE}" \
    --expect-strategy-version "${STRATEGY_VERSION}" \
    --expect-trading-status enabled \
    --expect-kill-switch false \
    --expect-only-enabled-strategy bull_flag
  if paper_proof_enabled; then
    refresh_paper_readiness
  fi
else
  docker compose -f "$COMPOSE_FILE" rm -sf supervisor >/dev/null 2>&1 || true
  docker compose -f "$COMPOSE_FILE" run --rm --entrypoint alpaca-bot-ops-check admin \
    --url http://web:8080/healthz \
    --no-expect-worker \
    --wait-seconds 30
  echo "Postgres is up and migrations are applied, but supervisor was not started because Alpaca credentials are missing or placeholders." >&2
fi

if [[ "${REQUIRE_CRON_HEALTH,,}" == "true" ]]; then
  "$ROOT_DIR/scripts/cron_health_check.sh"
else
  echo "Cron health check skipped because REQUIRE_CRON_HEALTH=false" >&2
fi

if credentials_ready && paper_proof_enabled; then
  verify_paper_proof_ready
fi

docker compose -f "$COMPOSE_FILE" ps
