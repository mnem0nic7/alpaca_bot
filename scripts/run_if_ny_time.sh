#!/usr/bin/env bash
set -euo pipefail

EXPECTED_HHMM="${1:?expected HHMM is required}"
shift

if [[ ! "$EXPECTED_HHMM" =~ ^[0-9]{4}$ ]]; then
  echo "expected HHMM must be four digits" >&2
  exit 1
fi

if [[ "$#" -eq 0 ]]; then
  echo "missing command to run" >&2
  exit 1
fi

ACTUAL_HHMM="$(TZ=America/New_York date +%H%M)"
if [[ "$ACTUAL_HHMM" != "$EXPECTED_HHMM" ]]; then
  exit 0
fi

exec "$@"
