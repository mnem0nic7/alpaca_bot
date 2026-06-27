#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PAPER_READINESS_AUTO_RESUME="${PAPER_READINESS_AUTO_RESUME:-true}"

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

require_env_value() {
  local name="$1"
  local expected="$2"
  local actual="${!name:-}"
  if [[ "$actual" != "$expected" ]]; then
    echo "paper readiness failed: $name=$actual expected $expected" >&2
    exit 1
  fi
}

require_env_true() {
  local name="$1"
  local actual="${!name:-}"
  if [[ "${actual,,}" != "true" ]]; then
    echo "paper readiness failed: $name=$actual expected true" >&2
    exit 1
  fi
}

require_env_false_or_unset() {
  local name="$1"
  local actual="${!name:-}"
  if [[ -n "$actual" && "${actual,,}" != "false" ]]; then
    echo "paper readiness failed: $name=$actual expected false or unset" >&2
    exit 1
  fi
}

require_env_value STRATEGY_VERSION v1-breakout
require_env_value MAX_OPEN_POSITIONS 2
require_env_value REPLAY_SLIPPAGE_BPS 2.0
require_env_value RISK_PER_TRADE_PCT 0.01
require_env_value MAX_POSITION_PCT 0.05
require_env_value MAX_PORTFOLIO_EXPOSURE_PCT 0.30
require_env_value DAILY_LOSS_LIMIT_PCT 0.01
require_env_true ENABLE_VIX_FILTER
require_env_true ENABLE_SECTOR_FILTER
require_env_true ENABLE_VWAP_ENTRY_FILTER
require_env_false_or_unset ENABLE_REGIME_FILTER
require_env_false_or_unset ENABLE_OPTIONS_TRADING

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

if [[ "$PAPER_READINESS_AUTO_RESUME" == "true" ]]; then
  status_line="$("${compose[@]}" run -T --rm admin status \
    --mode paper \
    --strategy-version "$STRATEGY_VERSION")"

  if [[ "$status_line" == *"status=close_only"* ]] \
    && [[ "$status_line" == *"kill_switch=false"* ]]; then
    open_positions="$("${compose[@]}" exec -T postgres psql \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" \
      -tAc "select count(*) from positions where trading_mode = 'paper' and strategy_version = '$STRATEGY_VERSION';" \
      | tr -d '[:space:]')"

    if [[ "$open_positions" == "0" ]]; then
      echo "paper readiness auto-resuming stale close_only state"
      "${compose[@]}" run -T --rm admin resume \
        --mode paper \
        --strategy-version "$STRATEGY_VERSION" \
        --reason "pre-open paper readiness auto-resume"
    else
      echo "paper readiness found close_only with $open_positions open positions; refusing auto-resume" >&2
    fi
  fi
fi

"${compose[@]}" run -T --rm \
  --entrypoint alpaca-bot-ops-check admin \
  --url http://web:8080/healthz \
  --expect-worker \
  --wait-seconds 60 \
  --expect-trading-mode paper \
  --expect-strategy-version "$STRATEGY_VERSION" \
  --expect-trading-status enabled \
  --expect-kill-switch false \
  --expect-only-enabled-strategy bull_flag
