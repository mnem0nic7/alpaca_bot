#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose.yaml"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
REQUIRE_CRON_HEALTH="${REQUIRE_CRON_HEALTH:-true}"
DEPLOY_PROOF_SETTLE_SECONDS="${DEPLOY_PROOF_SETTLE_SECONDS:-15}"
DEPLOY_REQUIRE_DECISION_DRY_RUN="${DEPLOY_REQUIRE_DECISION_DRY_RUN:-true}"
DEPLOY_READINESS_REFRESH_RETRIES="${DEPLOY_READINESS_REFRESH_RETRIES:-3}"
DEPLOY_READINESS_REFRESH_RETRY_SECONDS="${DEPLOY_READINESS_REFRESH_RETRY_SECONDS:-20}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

DEPLOY_DECISION_DRY_RUN_STRATEGY="${DEPLOY_DECISION_DRY_RUN_STRATEGY:-${PAPER_READINESS_DECISION_DRY_RUN_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}}"
DEPLOY_DECISION_DRY_RUN_MIN_RECORDS="${DEPLOY_DECISION_DRY_RUN_MIN_RECORDS:-${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-900}}"
DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED="${DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-${PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-false}}"
DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES="${DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES:-${PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES:-10:30,11:30,12:30,13:30,14:30,15:30}}"

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

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
  local attempt
  local rc

  attempt=1
  while true; do
    set +e
    PAPER_READINESS_FORCE_REFRESH=true "$ROOT_DIR/scripts/run_locked_check_with_audit.sh" \
      paper_readiness \
      /var/lock/alpaca-bot-paper-readiness.lock \
      "$ENV_FILE" \
      "$ROOT_DIR/scripts/paper_readiness_if_needed.sh" \
      "$ENV_FILE"
    rc="$?"
    set -e

    if [[ "$rc" -eq 0 ]]; then
      return 0
    fi
    if [[ "$rc" -ne 48 || "$attempt" -ge "$DEPLOY_READINESS_REFRESH_RETRIES" ]]; then
      return "$rc"
    fi

    echo \
      "paper readiness refresh lock busy after deploy; retrying in ${DEPLOY_READINESS_REFRESH_RETRY_SECONDS}s (${attempt}/${DEPLOY_READINESS_REFRESH_RETRIES})" \
      >&2
    sleep "$DEPLOY_READINESS_REFRESH_RETRY_SECONDS"
    attempt=$((attempt + 1))
  done
}

remove_supervisor_container() {
  local project_name
  local fallback_project_name
  project_name="${COMPOSE_PROJECT_NAME:-$(basename "$(dirname "$COMPOSE_FILE")")}"
  fallback_project_name="$(basename "$(dirname "$COMPOSE_FILE")")"

  "${compose[@]}" stop supervisor >/dev/null 2>&1 || true
  "${compose[@]}" rm -sf supervisor >/dev/null 2>&1 || true
  docker ps -aq \
    --filter "label=com.docker.compose.project=${project_name}" \
    --filter "label=com.docker.compose.service=supervisor" \
    | xargs -r docker rm -f >/dev/null 2>&1 || true
  docker rm -f \
    "${project_name}-supervisor-1" \
    "${fallback_project_name}-supervisor-1" \
    >/dev/null 2>&1 || true
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

verify_paper_decision_dry_run() {
  if [[ "${DEPLOY_REQUIRE_DECISION_DRY_RUN,,}" != "true" ]]; then
    echo "Paper decision dry run skipped because DEPLOY_REQUIRE_DECISION_DRY_RUN=false" >&2
    return
  fi

  PAPER_DECISION_DRY_RUN_STRATEGY="$DEPLOY_DECISION_DRY_RUN_STRATEGY" \
  PAPER_DECISION_DRY_RUN_MIN_RECORDS="$DEPLOY_DECISION_DRY_RUN_MIN_RECORDS" \
  PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED="$DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED" \
  PAPER_DECISION_DRY_RUN_SAMPLE_TIMES="$DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES" \
    "$ROOT_DIR/scripts/paper_decision_dry_run.sh" "$ENV_FILE"
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

case "${DEPLOY_REQUIRE_DECISION_DRY_RUN,,}" in
  true|false) ;;
  *)
    echo "DEPLOY_REQUIRE_DECISION_DRY_RUN must be true or false" >&2
    exit 1
    ;;
esac
if [[ ! "$DEPLOY_DECISION_DRY_RUN_STRATEGY" =~ ^[A-Za-z0-9_:-]+$ ]]; then
  echo "DEPLOY_DECISION_DRY_RUN_STRATEGY contains unsupported characters" >&2
  exit 1
fi
if [[ ! "$DEPLOY_DECISION_DRY_RUN_MIN_RECORDS" =~ ^[0-9]+$ ]]; then
  echo "DEPLOY_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" >&2
  exit 1
fi
case "${DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED,,}" in
  true|false) ;;
  *)
    echo "DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED must be true or false" >&2
    exit 1
    ;;
esac
if [[ -n "$DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES" \
  && ! "$DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES" =~ ^[0-9]{2}:[0-9]{2}(,[0-9]{2}:[0-9]{2})*$ ]]; then
  echo "DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES must be comma-separated HH:MM values" >&2
  exit 1
fi

if [[ ! "$DEPLOY_PROOF_SETTLE_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "DEPLOY_PROOF_SETTLE_SECONDS must be a non-negative integer" >&2
  exit 1
fi
if [[ ! "$DEPLOY_READINESS_REFRESH_RETRIES" =~ ^[1-9][0-9]*$ ]]; then
  echo "DEPLOY_READINESS_REFRESH_RETRIES must be a positive integer" >&2
  exit 1
fi
if [[ ! "$DEPLOY_READINESS_REFRESH_RETRY_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "DEPLOY_READINESS_REFRESH_RETRY_SECONDS must be a non-negative integer" >&2
  exit 1
fi

"${compose[@]}" build supervisor web migrate admin
"${compose[@]}" up -d postgres
"${compose[@]}" run --rm migrate
"${compose[@]}" up -d --force-recreate web

if credentials_ready; then
  remove_supervisor_container
  "${compose[@]}" up -d --force-recreate supervisor
  "${compose[@]}" run --rm --entrypoint alpaca-bot-ops-check admin \
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
  remove_supervisor_container
  "${compose[@]}" run --rm --entrypoint alpaca-bot-ops-check admin \
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
  verify_paper_decision_dry_run
  verify_paper_proof_ready
  if [[ "$DEPLOY_PROOF_SETTLE_SECONDS" -gt 0 ]]; then
    sleep "$DEPLOY_PROOF_SETTLE_SECONDS"
    verify_paper_proof_ready
  fi
fi

"${compose[@]}" ps
