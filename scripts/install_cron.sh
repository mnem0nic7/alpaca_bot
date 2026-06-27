#!/usr/bin/env bash
set -euo pipefail
install -m 644 deploy/cron.d/alpaca-bot /etc/cron.d/alpaca-bot
echo "Cron installed. Runs weekdays: paper readiness 13:20 UTC, premarket 13:30 UTC, session guard 22:10 UTC, paper profit probe 22:20 UTC, nightly 22:30 UTC."
