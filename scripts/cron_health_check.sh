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

while read -r cron_user log_file; do
  [[ -n "$cron_user" && -n "$log_file" ]] || continue
  if [[ -e "$log_file" ]]; then
    if [[ ! -f "$log_file" ]]; then
      fail "scheduled log target is not a file: $log_file"
    fi
    if [[ "$cron_user" != "root" && ! -w "$log_file" ]]; then
      fail "scheduled log target is not writable: $log_file"
    fi
    continue
  fi

  log_dir="$(dirname "$log_file")"
  if [[ ! -d "$log_dir" ]]; then
    fail "scheduled log directory is missing: $log_dir"
  fi
  if [[ "$cron_user" != "root" && ! -w "$log_dir" ]]; then
    fail "scheduled log directory is not writable: $log_dir"
  fi
done < <(
  awk '
    NF > 0 && $1 !~ /^#/ {
      user = $6
      for (i = 7; i <= NF; i++) {
        if ($i == ">>" && (i + 1) <= NF && $(i + 1) ~ /^\//) {
          print user, $(i + 1)
        } else if ($i ~ /^>>\//) {
          print user, substr($i, 3)
        }
      }
    }
  ' "$EXPECTED_CRON" \
    | sort -u
)

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
  run_locked_check_with_audit.sh \
  run_check_with_audit.sh \
  scheduled_check_lock_skipped.sh \
  paper_readiness_check.sh \
  paper_readiness_if_needed.sh \
  paper_activity_check.sh \
  session_guard.sh \
  paper_profit_probe.sh \
  paper_proof_status.sh \
  runtime_image_health_check.sh
do
  path="$ROOT_DIR/scripts/$script"
  if [[ ! -x "$path" ]]; then
    fail "required scheduled script is not executable: $path"
  fi
  if ! bash -n "$path"; then
    fail "required scheduled script has syntax errors: $path"
  fi
done

echo "cron health ok: installed schedule matches repo and cron daemon is active"
