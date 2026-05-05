#!/usr/bin/env bash
set -euo pipefail
install -m 644 deploy/cron.d/alpaca-bot /etc/cron.d/alpaca-bot
echo "Cron installed. Runs weekdays at 22:30 UTC (5:30 PM ET)."
