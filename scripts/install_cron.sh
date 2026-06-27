#!/usr/bin/env bash
set -euo pipefail
install -m 644 deploy/cron.d/alpaca-bot /etc/cron.d/alpaca-bot
echo "Cron installed. Runs weekdays: premarket 12:30 UTC, paper readiness 13:20 UTC, paper activity 16:00 UTC, session guard 22:10 UTC, paper profit probe 22:20 UTC, nightly 22:30 UTC."
