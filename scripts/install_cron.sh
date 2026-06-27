#!/usr/bin/env bash
set -euo pipefail
install -m 644 deploy/cron.d/alpaca-bot /etc/cron.d/alpaca-bot
echo "Cron installed. Runs weekdays: session guard 22:10 UTC, paper profit probe 22:20 UTC, nightly 22:30 UTC."
