#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_CRON="${ALPACA_BOT_CRON_FILE:-/etc/cron.d/alpaca-bot}"

if ! install -m 644 "$ROOT_DIR/deploy/cron.d/alpaca-bot" "$TARGET_CRON"; then
  echo "Cron install failed. Re-run as root: $ROOT_DIR/scripts/install_cron.sh" >&2
  echo "Target cron file: $TARGET_CRON" >&2
  exit 1
fi
ALPACA_BOT_CRON_FILE="$TARGET_CRON" "$ROOT_DIR/scripts/cron_health_check.sh"
echo "Cron installed. Runs weekdays on New York wall time: paper readiness 09:15/09:55/09:58/10:02/10:05/10:10 plus stale-repair checks from 10:15-15:15, force refresh 12:15/14:25/16:55/17:24, paper activity 10:25/10:35/12:00/14:35, session guard 17:10, paper profit probe 17:20, proof status 17:28, nightly cycle 17:30 with read-only second-strategy scan after nightly."
