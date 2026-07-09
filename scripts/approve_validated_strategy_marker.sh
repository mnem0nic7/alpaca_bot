#!/usr/bin/env bash
# Safely record only the replay-validation approval marker for a stock strategy.
# This wrapper never performs env allowlist changes, strategy enablement, or deploys.
#
# Usage:
#   approve_validated_strategy_marker.sh STRATEGY_NAME
#   approve_validated_strategy_marker.sh ENV_FILE STRATEGY_NAME [EVIDENCE_ROOT] [DEPLOY_SCRIPT]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_ENV_FILE="${APPROVE_VALIDATED_STRATEGY_MARKER_ENV_FILE:-/etc/alpaca_bot/alpaca-bot.env}"
DEFAULT_EVIDENCE_ROOT="${SECOND_STRATEGY_OUTPUT_ROOT:-/var/lib/alpaca-bot/nightly/second_strategy}"
DEFAULT_DEPLOY_SCRIPT="${APPROVE_VALIDATED_STRATEGY_MARKER_DEPLOY_SCRIPT:-$ROOT_DIR/scripts/deploy.sh}"
CONFIRMATION="${APPROVE_VALIDATED_STRATEGY_MARKER_CONFIRM:-${PROMOTE_VALIDATED_STRATEGY_CONFIRM:-}}"
DRY_RUN="${APPROVE_VALIDATED_STRATEGY_MARKER_DRY_RUN:-true}"

usage() {
  echo "usage: approve_validated_strategy_marker.sh STRATEGY_NAME" >&2
  echo "   or: approve_validated_strategy_marker.sh ENV_FILE STRATEGY_NAME [EVIDENCE_ROOT] [DEPLOY_SCRIPT]" >&2
}

case "$#" in
  0)
    usage
    exit 2
    ;;
  1)
    ENV_FILE="$DEFAULT_ENV_FILE"
    STRATEGY_NAME="$1"
    EVIDENCE_ROOT="$DEFAULT_EVIDENCE_ROOT"
    DEPLOY_SCRIPT="$DEFAULT_DEPLOY_SCRIPT"
    ;;
  *)
    ENV_FILE="$1"
    STRATEGY_NAME="$2"
    EVIDENCE_ROOT="${3:-$DEFAULT_EVIDENCE_ROOT}"
    DEPLOY_SCRIPT="${4:-$DEFAULT_DEPLOY_SCRIPT}"
    ;;
esac

if [[ -z "$STRATEGY_NAME" ]]; then
  usage
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
