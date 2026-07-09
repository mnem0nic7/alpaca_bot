#!/usr/bin/env bash
# Safely record only the replay-validation approval marker for a stock strategy.
# This wrapper never performs env allowlist changes, strategy enablement, or deploys.
#
# Usage: approve_validated_strategy_marker.sh [ENV_FILE] STRATEGY_NAME [EVIDENCE_ROOT] [DEPLOY_SCRIPT]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
STRATEGY_NAME="${2:-}"
EVIDENCE_ROOT="${3:-${SECOND_STRATEGY_OUTPUT_ROOT:-/var/lib/alpaca-bot/nightly/second_strategy}}"
DEPLOY_SCRIPT="${4:-$ROOT_DIR/scripts/deploy.sh}"
CONFIRMATION="${APPROVE_VALIDATED_STRATEGY_MARKER_CONFIRM:-${PROMOTE_VALIDATED_STRATEGY_CONFIRM:-}}"
DRY_RUN="${APPROVE_VALIDATED_STRATEGY_MARKER_DRY_RUN:-true}"

if [[ -z "$STRATEGY_NAME" ]]; then
  echo "usage: approve_validated_strategy_marker.sh [ENV_FILE] STRATEGY_NAME [EVIDENCE_ROOT] [DEPLOY_SCRIPT]" >&2
  exit 2
fi

case "${DRY_RUN,,}" in
  true|1|yes|y)
    DRY_RUN=true
    ;;
  false|0|no|n|"")
    DRY_RUN=false
    ;;
  *)
    echo "APPROVE_VALIDATED_STRATEGY_MARKER_DRY_RUN must be true or false" >&2
    exit 2
    ;;
esac

exec env \
  PROMOTE_VALIDATED_STRATEGY_CONFIRM="$CONFIRMATION" \
  PROMOTE_VALIDATED_STRATEGY_DRY_RUN="$DRY_RUN" \
  PROMOTE_VALIDATED_STRATEGY_APPROVAL_ONLY=true \
  "$ROOT_DIR/scripts/promote_validated_strategy.sh" \
  "$ENV_FILE" \
  "$STRATEGY_NAME" \
  "$EVIDENCE_ROOT" \
  "$DEPLOY_SCRIPT"
