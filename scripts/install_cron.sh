#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install -m 644 "$ROOT_DIR/deploy/cron.d/alpaca-bot" /etc/cron.d/alpaca-bot
echo "Cron installed. Runs weekdays: paper readiness 13:20/13:55 UTC, paper activity 14:15/16:00 UTC, session guard 22:10 UTC, paper profit probe 22:20 UTC, nightly 22:30 UTC."
