#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
RUNTIME_IMAGE_HEALTH_SERVICE="${RUNTIME_IMAGE_HEALTH_SERVICE:-web}"
RUNTIME_IMAGE_HEALTH_SERVICES="${RUNTIME_IMAGE_HEALTH_SERVICES:-$RUNTIME_IMAGE_HEALTH_SERVICE:supervisor}"
RUNTIME_IMAGE_HEALTH_FILES="${RUNTIME_IMAGE_HEALTH_FILES:-admin/cli.py:backfill/fetcher.py:config/__init__.py:core/engine.py:domain/enums.py:domain/models.py:execution/alpaca.py:nightly/cli.py:replay/audit.py:replay/cli.py:replay/lever_sweep.py:replay/portfolio.py:replay/runner.py:replay/splitter.py:risk/sizing.py:runtime/cycle.py:runtime/cycle_intent_execution.py:runtime/order_dispatch.py:runtime/startup_recovery.py:runtime/supervisor.py:runtime/trade_update_stream.py:runtime/trade_updates.py:storage/models.py:storage/repositories.py:strategy/__init__.py:strategy/breakout.py:strategy/bull_flag.py:strategy/session.py:strategy_approval.py:web/app.py:web/templates/dashboard.html}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

IFS=':' read -r -a services <<< "$RUNTIME_IMAGE_HEALTH_SERVICES"
if [[ "${#services[@]}" -eq 0 ]]; then
  echo "RUNTIME_IMAGE_HEALTH_SERVICES must name at least one compose service" >&2
  exit 1
fi
for service in "${services[@]}"; do
  if [[ -z "$service" || ! "$service" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "RUNTIME_IMAGE_HEALTH_SERVICES contains unsupported service: $service" >&2
    exit 1
  fi
done

IFS=':' read -r -a rel_files <<< "$RUNTIME_IMAGE_HEALTH_FILES"
if [[ "${#rel_files[@]}" -eq 0 ]]; then
  echo "RUNTIME_IMAGE_HEALTH_FILES must name at least one package file" >&2
  exit 1
fi

host_hashes="$(mktemp)"
image_hashes=()

cleanup() {
  rm -f "$host_hashes"
  if [[ "${#image_hashes[@]}" -gt 0 ]]; then
    rm -f "${image_hashes[@]}"
  fi
}
trap cleanup EXIT

for rel in "${rel_files[@]}"; do
  if [[ -z "$rel" || "$rel" == /* || "$rel" == *".."* ]]; then
    echo "runtime image health failed: unsupported package path: $rel" >&2
    exit 1
  fi
  local_path="src/alpaca_bot/$rel"
  if [[ ! -f "$local_path" ]]; then
    echo "runtime image health failed: missing workspace file: $local_path" >&2
    exit 1
  fi
  sha256sum "$local_path" | awk -v rel="$rel" '{print $1 "  " rel}'
done > "$host_hashes"

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

checked_services=()

for service in "${services[@]}"; do
  image_hash="$(mktemp)"
  image_hashes+=("$image_hash")

  if ! "${compose[@]}" exec -T \
    -e RUNTIME_IMAGE_HEALTH_FILES="$RUNTIME_IMAGE_HEALTH_FILES" \
    "$service" python - <<'PY' > "$image_hash"
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

import alpaca_bot

root = Path(alpaca_bot.__file__).resolve().parent
failed = False
for rel in os.environ["RUNTIME_IMAGE_HEALTH_FILES"].split(":"):
    path = root / rel
    if not path.is_file():
        print(f"runtime image health failed: missing deployed package file: {rel}", file=sys.stderr)
        failed = True
        continue
    print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {rel}")

if failed:
    raise SystemExit(1)
PY
  then
    echo "runtime image health failed: could not inspect service=$service" >&2
    exit 1
  fi

  if ! diff -u "$host_hashes" "$image_hash" >&2; then
    echo "runtime image health failed: deployed package differs from workspace for service=$service" >&2
    exit 1
  fi

  checked_services+=("$service")
done

IFS=,
echo "runtime image health ok: services=${checked_services[*]} files=${#rel_files[@]}"
