#!/usr/bin/env bash
set -euo pipefail

EXPECTED_HHMM="${1:?expected HHMM is required}"
RUN_IF_NY_TIME_GRACE_MINUTES="${RUN_IF_NY_TIME_GRACE_MINUTES:-2}"
shift

if [[ ! "$EXPECTED_HHMM" =~ ^[0-9]{4}$ ]]; then
  echo "expected HHMM must be four digits" >&2
  exit 1
fi

expected_hour=$((10#${EXPECTED_HHMM:0:2}))
expected_minute=$((10#${EXPECTED_HHMM:2:2}))
if [[ "$expected_hour" -gt 23 || "$expected_minute" -gt 59 ]]; then
  echo "expected HHMM must be a valid 24-hour time" >&2
  exit 1
fi

if [[ ! "$RUN_IF_NY_TIME_GRACE_MINUTES" =~ ^[0-9]+$ ]]; then
  echo "RUN_IF_NY_TIME_GRACE_MINUTES must be a non-negative integer" >&2
  exit 1
fi

if [[ "$RUN_IF_NY_TIME_GRACE_MINUTES" -gt 10 ]]; then
  echo "RUN_IF_NY_TIME_GRACE_MINUTES must be at most 10" >&2
  exit 1
fi

if [[ "$#" -eq 0 ]]; then
  echo "missing command to run" >&2
  exit 1
fi

ACTUAL_HHMM="$(TZ=America/New_York date +%H%M)"
if [[ ! "$ACTUAL_HHMM" =~ ^[0-9]{4}$ ]]; then
  echo "date returned invalid HHMM: $ACTUAL_HHMM" >&2
  exit 1
fi

actual_hour=$((10#${ACTUAL_HHMM:0:2}))
actual_minute=$((10#${ACTUAL_HHMM:2:2}))
if [[ "$actual_hour" -gt 23 || "$actual_minute" -gt 59 ]]; then
  echo "date returned invalid HHMM: $ACTUAL_HHMM" >&2
  exit 1
fi

expected_minutes=$((expected_hour * 60 + expected_minute))
actual_minutes=$((actual_hour * 60 + actual_minute))
delay_minutes=$((actual_minutes - expected_minutes))

if [[ "$delay_minutes" -lt 0 || "$delay_minutes" -gt "$RUN_IF_NY_TIME_GRACE_MINUTES" ]]; then
  exit 0
fi

exec "$@"
