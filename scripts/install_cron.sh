#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install -m 644 "$ROOT_DIR/deploy/cron.d/alpaca-bot" /etc/cron.d/alpaca-bot
"$ROOT_DIR/scripts/cron_health_check.sh"
echo "Cron installed. Runs weekdays on New York wall time: paper readiness 09:20/09:55/09:58/10:02, paper activity 10:15/12:00, session guard 17:10, paper profit probe 17:20, nightly 17:30."
