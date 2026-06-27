#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_CRON="$ROOT_DIR/deploy/cron.d/alpaca-bot"
INSTALLED_CRON="${ALPACA_BOT_CRON_FILE:-/etc/cron.d/alpaca-bot}"

fail() {
  echo "cron health failed: $*" >&2
  exit 1
}

if [[ ! -f "$EXPECTED_CRON" ]]; then
  fail "missing repo cron file: $EXPECTED_CRON"
fi

if [[ ! -f "$INSTALLED_CRON" ]]; then
  fail "missing installed cron file: $INSTALLED_CRON"
fi

if ! cmp -s "$EXPECTED_CRON" "$INSTALLED_CRON"; then
  echo "cron health failed: installed cron differs from repo schedule" >&2
  diff -u "$EXPECTED_CRON" "$INSTALLED_CRON" >&2 || true
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet cron 2>/dev/null \
    || systemctl is-active --quiet crond 2>/dev/null; then
    cron_active=true
  else
    cron_active=false
  fi
else
  cron_active=false
fi

if [[ "$cron_active" != "true" ]] \
  && ps -eo comm= | grep -Eq '^(cron|crond)$'; then
  cron_active=true
fi

if [[ "$cron_active" != "true" ]]; then
  fail "cron daemon is not active"
fi

for script in \
  run_if_ny_time.sh \
  run_check_with_audit.sh \
  paper_readiness_check.sh \
  paper_activity_check.sh \
  session_guard.sh \
  paper_profit_probe.sh
do
  path="$ROOT_DIR/scripts/$script"
  if [[ ! -x "$path" ]]; then
    fail "required scheduled script is not executable: $path"
  fi
done

echo "cron health ok: installed schedule matches repo and cron daemon is active"
