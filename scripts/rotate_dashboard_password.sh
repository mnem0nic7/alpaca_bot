#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PASSWORD_FILE="${ALPACA_BOT_DASHBOARD_PASSWORD_FILE:-/etc/alpaca_bot/dashboard_password.txt}"
USERNAME="${2:-}"
COMPOSE_FILE="$ROOT_DIR/deploy/compose.yaml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
python_args=(
  -m
  alpaca_bot.web.password_rotate_cli
  --env-file
  "$ENV_FILE"
  --password-file
  "$PASSWORD_FILE"
)

if [[ -n "$USERNAME" ]]; then
  python_args+=(--username "$USERNAME")
fi

PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
python3 "${python_args[@]}"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --force-recreate web
