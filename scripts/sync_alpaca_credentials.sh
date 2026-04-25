#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

PYTHONPATH="$ROOT_DIR/src" python3 -m alpaca_bot.admin.credential_sync --env-file "$ENV_FILE"
