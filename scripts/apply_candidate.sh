#!/usr/bin/env bash
# Apply nightly candidate params to the system env file and restart the supervisor
# if any params changed.
#
# Usage: apply_candidate.sh [ENV_FILE] [CANDIDATE_ENV] [DEPLOY_SCRIPT]
#   ENV_FILE       System env file (default: /etc/alpaca_bot/alpaca-bot.env)
#   CANDIDATE_ENV  Candidate params file from nightly run (default: /var/lib/alpaca-bot/nightly/candidate.env)
#   DEPLOY_SCRIPT  Script to restart supervisor (default: <repo>/scripts/deploy.sh)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
CANDIDATE_ENV="${2:-/var/lib/alpaca-bot/nightly/candidate.env}"
DEPLOY_SCRIPT="${3:-$ROOT_DIR/scripts/deploy.sh}"
LOG_PREFIX="[apply_candidate $(date -u '+%Y-%m-%dT%H:%M:%SZ')]"

if [[ ! -f "$CANDIDATE_ENV" ]]; then
    echo "$LOG_PREFIX No candidate.env at $CANDIDATE_ENV — nothing to apply."
    exit 0
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "$LOG_PREFIX env file not found: $ENV_FILE" >&2
    exit 1
fi

# Extract non-comment, non-empty KEY=VALUE lines from candidate.env
PARAMS=$(grep -E '^[A-Z_]+=' "$CANDIDATE_ENV") || true

if [[ -z "$PARAMS" ]]; then
    echo "$LOG_PREFIX candidate.env has no param lines — nothing to apply."
    exit 0
fi

# Detect whether any param differs from the current env file value
CHANGED=false
while IFS='=' read -r KEY VALUE; do
    [[ -z "$KEY" ]] && continue
    if [[ ! "$VALUE" =~ ^[A-Za-z0-9._+-]+$ ]]; then
        echo "$LOG_PREFIX ERROR: unsafe value for $KEY — rejecting candidate.env" >&2
        exit 1
    fi
    CURRENT=$(grep "^${KEY}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)
    if [[ "$CURRENT" != "$VALUE" ]]; then
        CHANGED=true
        echo "$LOG_PREFIX $KEY: ${CURRENT:-(not set)} → $VALUE"
    fi
done <<< "$PARAMS"

if [[ "$CHANGED" == "false" ]]; then
    echo "$LOG_PREFIX Params unchanged — no restart needed."
    exit 0
fi

# Apply all params (update existing lines; append missing ones)
while IFS='=' read -r KEY VALUE; do
    [[ -z "$KEY" ]] && continue
    if [[ ! "$VALUE" =~ ^[A-Za-z0-9._+-]+$ ]]; then
        echo "$LOG_PREFIX ERROR: unsafe value for $KEY — rejecting candidate.env" >&2
        exit 1
    fi
    if grep -q "^${KEY}=" "$ENV_FILE"; then
        sed -i "s|^${KEY}=.*|${KEY}=${VALUE}|" "$ENV_FILE"
    else
        echo "${KEY}=${VALUE}" >> "$ENV_FILE"
    fi
done <<< "$PARAMS"

echo "$LOG_PREFIX Params applied. Restarting supervisor..."
"$DEPLOY_SCRIPT" "$ENV_FILE"
echo "$LOG_PREFIX Done."
