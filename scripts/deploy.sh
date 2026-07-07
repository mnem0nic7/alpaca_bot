#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose.yaml"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
REQUIRE_CRON_HEALTH="${REQUIRE_CRON_HEALTH:-true}"
DEPLOY_PROOF_SETTLE_SECONDS="${DEPLOY_PROOF_SETTLE_SECONDS:-15}"
DEPLOY_REQUIRE_DECISION_DRY_RUN="${DEPLOY_REQUIRE_DECISION_DRY_RUN:-true}"
DEPLOY_READINESS_REFRESH_RETRIES="${DEPLOY_READINESS_REFRESH_RETRIES:-10}"
DEPLOY_READINESS_REFRESH_RETRY_SECONDS="${DEPLOY_READINESS_REFRESH_RETRY_SECONDS:-20}"
DEPLOY_PREFLIGHT_EXPOSURE_RETRIES="${DEPLOY_PREFLIGHT_EXPOSURE_RETRIES:-1}"
DEPLOY_PREFLIGHT_EXPOSURE_RETRY_SECONDS="${DEPLOY_PREFLIGHT_EXPOSURE_RETRY_SECONDS:-30}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

DEPLOY_DECISION_DRY_RUN_STRATEGY="${DEPLOY_DECISION_DRY_RUN_STRATEGY:-${PAPER_READINESS_DECISION_DRY_RUN_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}}"
DEPLOY_DECISION_DRY_RUN_STRATEGIES="${DEPLOY_DECISION_DRY_RUN_STRATEGIES:-${PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES:-${PAPER_APPROVED_STRATEGIES:-$DEPLOY_DECISION_DRY_RUN_STRATEGY}}}"
DEPLOY_DECISION_DRY_RUN_MIN_RECORDS="${DEPLOY_DECISION_DRY_RUN_MIN_RECORDS:-${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-900}}"
DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED="${DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-${PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-true}}"
DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES="${DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES:-${PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES:-10:30,11:30,12:30,13:30,14:30,15:30}}"
DEPLOY_EXPECT_ENABLED_STRATEGIES="${DEPLOY_EXPECT_ENABLED_STRATEGIES:-${PAPER_APPROVED_STRATEGIES:-bull_flag,vwap_cross}}"

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
expected_enabled_strategy_args=()
deploy_decision_dry_run_strategies=()

require_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "missing required env var: $name" >&2
    exit 1
  fi
}

build_expected_enabled_strategy_args() {
  local csv="$1"
  local raw
  local name
  local -a raw_names
  expected_enabled_strategy_args=()
  IFS=',' read -r -a raw_names <<< "$csv"
  for raw in "${raw_names[@]}"; do
    name="$(printf '%s' "$raw" | tr -d '[:space:]')"
    if [[ -z "$name" ]]; then
      continue
    fi
    if [[ ! "$name" =~ ^[A-Za-z0-9_:-]+$ ]]; then
      echo "DEPLOY_EXPECT_ENABLED_STRATEGIES contains unsupported strategy: $name" >&2
      exit 1
    fi
    expected_enabled_strategy_args+=(--expect-only-enabled-strategy "$name")
  done
  if [[ "${#expected_enabled_strategy_args[@]}" -eq 0 ]]; then
    echo "DEPLOY_EXPECT_ENABLED_STRATEGIES must contain at least one strategy" >&2
    exit 1
  fi
}

add_deploy_decision_dry_run_strategy() {
  local raw="$1"
  local name
  local existing

  name="$(printf '%s' "$raw" | tr -d '[:space:]')"
  if [[ -z "$name" ]]; then
    return
  fi
  if [[ ! "$name" =~ ^[A-Za-z0-9_:-]+$ ]]; then
    echo "DEPLOY_DECISION_DRY_RUN_STRATEGIES contains unsupported strategy: $name" >&2
    exit 1
  fi
  for existing in "${deploy_decision_dry_run_strategies[@]}"; do
    if [[ "$existing" == "$name" ]]; then
      return
    fi
  done
  deploy_decision_dry_run_strategies+=("$name")
}

build_deploy_decision_dry_run_strategies() {
  local csv="$1"
  local raw
  local -a raw_names

  deploy_decision_dry_run_strategies=()
  add_deploy_decision_dry_run_strategy "$DEPLOY_DECISION_DRY_RUN_STRATEGY"
  IFS=',' read -r -a raw_names <<< "$csv"
  for raw in "${raw_names[@]}"; do
    add_deploy_decision_dry_run_strategy "$raw"
  done
  if [[ "${#deploy_decision_dry_run_strategies[@]}" -eq 0 ]]; then
    echo "DEPLOY_DECISION_DRY_RUN_STRATEGIES must contain at least one strategy" >&2
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

load_deploy_trading_status_line() {
  "${compose[@]}" run -T --rm admin status \
    --mode "${TRADING_MODE}" \
    --strategy-version "${STRATEGY_VERSION}"
}

load_deploy_ops_expected_trading_status() {
  local status_line

  if ! paper_proof_enabled; then
    printf 'enabled\n'
    return
  fi

  if ! status_line="$(load_deploy_trading_status_line 2>/dev/null)"; then
    printf 'enabled\n'
    return
  fi

  if [[ "$status_line" == *"status=close_only"* \
    && "$status_line" == *"kill_switch=false"* \
    && "$status_line" == *"reason=paper profit lock"* ]]; then
    if BROKER_FLAT_CONTEXT="deploy profit lock" \
      "$ROOT_DIR/scripts/broker_flat_check.sh" "$ENV_FILE" >&2; then
      echo "deploy ops check accepting flat paper profit lock: $status_line" >&2
      printf 'close_only\n'
      return
    fi
  fi

  printf 'enabled\n'
}

run_deploy_ops_check() {
  local expected_status
  local retry_expected_status
  local rc

  expected_status="$(load_deploy_ops_expected_trading_status)"

  set +e
  "${compose[@]}" run --rm --entrypoint alpaca-bot-ops-check admin \
    --url http://web:8080/healthz \
    --expect-worker \
    --wait-seconds 60 \
    --expect-trading-mode "${TRADING_MODE}" \
    --expect-strategy-version "${STRATEGY_VERSION}" \
    --expect-trading-status "$expected_status" \
    --expect-kill-switch false \
    "${expected_enabled_strategy_args[@]}"
  rc="$?"
  set -e

  if [[ "$rc" -eq 0 ]]; then
    return 0
  fi

  retry_expected_status="$(load_deploy_ops_expected_trading_status)"
  if [[ "$expected_status" != "close_only" && "$retry_expected_status" == "close_only" ]]; then
    echo "deploy ops check retrying after flat paper profit lock transition" >&2
    "${compose[@]}" run --rm --entrypoint alpaca-bot-ops-check admin \
      --url http://web:8080/healthz \
      --expect-worker \
      --wait-seconds 60 \
      --expect-trading-mode "${TRADING_MODE}" \
      --expect-strategy-version "${STRATEGY_VERSION}" \
      --expect-trading-status close_only \
      --expect-kill-switch false \
      "${expected_enabled_strategy_args[@]}"
    return
  fi

  return "$rc"
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

paper_proof_summary_field() {
  local summary="$1"
  local key="$2"
  local part

  for part in $summary; do
    if [[ "$part" == "$key="* ]]; then
      printf '%s\n' "${part#*=}"
      return 0
    fi
  done

  return 1
}

deploy_allows_proof_blockers() {
  local blockers="$1"
  local blocker
  local -a blocker_names

  if [[ -z "$blockers" || "$blockers" == "none" ]]; then
    return 1
  fi

  IFS=',' read -r -a blocker_names <<< "$blockers"
  for blocker in "${blocker_names[@]}"; do
    case "$blocker" in
      local_open_positions|local_active_orders|broker_open_orders|broker_open_positions) ;;
      *) return 1 ;;
    esac
  done
}

deploy_accepts_protected_paper_exposure() {
  local proof_status_output="$1"
  local proof_summary="$2"
  local blockers

  blockers="$(paper_proof_summary_field "$proof_summary" blockers || true)"
  if [[ "$proof_summary" != *"readiness=blocked"* ]]; then
    return 1
  fi
  if ! deploy_allows_proof_blockers "$blockers"; then
    return 1
  fi
  if [[ "$proof_status_output" != *"paper proof readiness audit: status=ok "* ]]; then
    return 1
  fi
  if [[ "$proof_status_output" != *"paper proof runtime: ops_status=ok "* \
    || "$proof_status_output" != *" image_status=ok "* ]]; then
    return 1
  fi
  if [[ "$proof_status_output" != *"paper proof stream: status=ok "* ]]; then
    return 1
  fi
  if [[ "$proof_status_output" != *"paper proof exposure protection: status=protected issues=none "* ]]; then
    return 1
  fi
}

deploy_paper_proof_status_ready() {
  local proof_status_output="$1"
  local proof_summary

  proof_summary="$(
    printf '%s\n' "$proof_status_output" \
      | grep -E '^paper proof summary: ' \
      | tail -n 1 \
      || true
  )"

  if [[ "$proof_summary" == *"readiness=ready"* \
    && "$proof_summary" == *"blockers=none"* ]]; then
    return 0
  fi
  deploy_accepts_protected_paper_exposure "$proof_status_output" "$proof_summary"
}

paper_proof_exposure_line() {
  local proof_status_output="$1"

  printf '%s\n' "$proof_status_output" \
    | grep -E '^paper proof exposure protection: ' \
    | tail -n 1 \
    || true
}

deploy_paper_exposure_safe() {
  local proof_status_output="$1"
  local exposure_line

  exposure_line="$(paper_proof_exposure_line "$proof_status_output")"
  [[ "$exposure_line" == *" status=flat issues=none "* \
    || "$exposure_line" == *" status=protected issues=none "* ]]
}

verify_deploy_preflight_paper_exposure() {
  local attempt
  local exposure_line
  local proof_status_output

  attempt=1
  while true; do
    proof_status_output="$("$ROOT_DIR/scripts/paper_proof_status.sh" "$ENV_FILE")"
    if deploy_paper_exposure_safe "$proof_status_output"; then
      return 0
    fi

    exposure_line="$(paper_proof_exposure_line "$proof_status_output")"
    if [[ "$attempt" -ge "$DEPLOY_PREFLIGHT_EXPOSURE_RETRIES" ]]; then
      echo \
        "deploy preflight failed: paper exposure is not flat or protected; ${exposure_line:-missing exposure line}" \
        >&2
      printf '%s\n' "$proof_status_output" >&2
      exit 1
    fi

    echo \
      "deploy preflight waiting for paper exposure to become flat/protected: ${exposure_line:-missing exposure line}" \
      >&2
    sleep "$DEPLOY_PREFLIGHT_EXPOSURE_RETRY_SECONDS"
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

  if deploy_paper_proof_status_ready "$proof_status_output"; then
    if deploy_accepts_protected_paper_exposure "$proof_status_output" "$proof_summary"; then
      echo "deploy accepting protected paper exposure after deploy: $proof_summary" >&2
    fi
    return 0
  fi

  echo "deploy failed: paper proof status not ready after deploy: ${proof_summary:-missing summary}" >&2
  exit 1
}

verify_paper_decision_dry_run() {
  local strategy

  if [[ "${DEPLOY_REQUIRE_DECISION_DRY_RUN,,}" != "true" ]]; then
    echo "Paper decision dry run skipped because DEPLOY_REQUIRE_DECISION_DRY_RUN=false" >&2
    return
  fi

  for strategy in "${deploy_decision_dry_run_strategies[@]}"; do
    PAPER_DECISION_DRY_RUN_STRATEGY="$strategy" \
    PAPER_DECISION_DRY_RUN_MIN_RECORDS="$DEPLOY_DECISION_DRY_RUN_MIN_RECORDS" \
    PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED="$DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED" \
    PAPER_DECISION_DRY_RUN_SAMPLE_TIMES="$DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES" \
      "$ROOT_DIR/scripts/paper_decision_dry_run.sh" "$ENV_FILE"
  done
}

if [[ "${DEPLOY_SH_SOURCE_ONLY:-false}" == "true" ]]; then
  return 0 2>/dev/null || exit 0
fi

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
build_expected_enabled_strategy_args "$DEPLOY_EXPECT_ENABLED_STRATEGIES"
build_deploy_decision_dry_run_strategies "$DEPLOY_DECISION_DRY_RUN_STRATEGIES"

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
if [[ ! "$DEPLOY_PREFLIGHT_EXPOSURE_RETRIES" =~ ^[1-9][0-9]*$ ]]; then
  echo "DEPLOY_PREFLIGHT_EXPOSURE_RETRIES must be a positive integer" >&2
  exit 1
fi
if [[ ! "$DEPLOY_PREFLIGHT_EXPOSURE_RETRY_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "DEPLOY_PREFLIGHT_EXPOSURE_RETRY_SECONDS must be a non-negative integer" >&2
  exit 1
fi

"${compose[@]}" build supervisor web migrate admin
"${compose[@]}" up -d postgres
"${compose[@]}" run --rm migrate
"${compose[@]}" up -d --force-recreate web

if credentials_ready; then
  if paper_proof_enabled; then
    verify_deploy_preflight_paper_exposure
  fi
  remove_supervisor_container
  "${compose[@]}" up -d --force-recreate supervisor
  run_deploy_ops_check
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
