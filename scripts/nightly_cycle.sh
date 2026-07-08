#!/usr/bin/env bash
# Run the post-close nightly evolve cycle under the cron-held nightly lock.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"

fail() {
  echo "[nightly_cycle $(date -u '+%Y-%m-%dT%H:%M:%SZ')] ERROR: $*" >&2
  exit 1
}

require_positive_integer() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || [[ "$value" -le 0 ]]; then
    fail "$name must be a positive integer; got '$value'"
  fi
}

[[ -f "$ENV_FILE" ]] || fail "missing env file: $ENV_FILE"

cd "$ROOT_DIR"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

NIGHTLY_LOG="${NIGHTLY_LOG:-/var/log/alpaca-bot-nightly.log}"
SECOND_STRATEGY_LOG="${SECOND_STRATEGY_LOG:-/var/log/alpaca-bot-second-strategy.log}"
NIGHTLY_TIMEOUT_SECONDS="${NIGHTLY_TIMEOUT_SECONDS:-14400}"
SECOND_STRATEGY_SCAN_TIMEOUT_SECONDS="${SECOND_STRATEGY_SCAN_TIMEOUT_SECONDS:-7200}"

require_positive_integer NIGHTLY_TIMEOUT_SECONDS "$NIGHTLY_TIMEOUT_SECONDS"
require_positive_integer SECOND_STRATEGY_SCAN_TIMEOUT_SECONDS "$SECOND_STRATEGY_SCAN_TIMEOUT_SECONDS"

mkdir -p "$(dirname "$NIGHTLY_LOG")" "$(dirname "$SECOND_STRATEGY_LOG")"
exec >> "$NIGHTLY_LOG" 2>&1

echo "[nightly_cycle $(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting nightly env_file=$ENV_FILE nightly_timeout_seconds=$NIGHTLY_TIMEOUT_SECONDS second_strategy_timeout_seconds=$SECOND_STRATEGY_SCAN_TIMEOUT_SECONDS"
timeout "$NIGHTLY_TIMEOUT_SECONDS" docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml run --rm nightly
echo "[nightly_cycle $(date -u '+%Y-%m-%dT%H:%M:%SZ')] nightly complete; applying candidate if allowed"

./scripts/apply_candidate.sh "$ENV_FILE"
echo "[nightly_cycle $(date -u '+%Y-%m-%dT%H:%M:%SZ')] candidate apply complete; starting second-strategy scan log=$SECOND_STRATEGY_LOG"

if timeout "$SECOND_STRATEGY_SCAN_TIMEOUT_SECONDS" ./scripts/second_strategy_basket_scan.sh "$ENV_FILE" >> "$SECOND_STRATEGY_LOG" 2>&1; then
  echo "[nightly_cycle $(date -u '+%Y-%m-%dT%H:%M:%SZ')] second-strategy scan complete"
else
  rc="$?"
  echo "[nightly_cycle $(date -u '+%Y-%m-%dT%H:%M:%SZ')] second-strategy scan failed rc=$rc; see $SECOND_STRATEGY_LOG" >&2
  exit "$rc"
fi
