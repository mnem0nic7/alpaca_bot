#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
BROKER_FLAT_CONTEXT="${BROKER_FLAT_CONTEXT:-broker flat check}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

"${compose[@]}" run -T --rm \
  -e BROKER_FLAT_CONTEXT="$BROKER_FLAT_CONTEXT" \
  --entrypoint python admin <<'PY'
from __future__ import annotations

import os
import sys

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter

context = os.environ.get("BROKER_FLAT_CONTEXT") or "broker flat check"
settings = Settings.from_env()
broker = AlpacaExecutionAdapter.from_settings(settings)
open_orders = broker.list_open_orders()
open_positions = broker.list_positions()

if open_orders:
    symbols = ",".join(sorted({order.symbol for order in open_orders}))
    print(
        f"{context} failed: broker has {len(open_orders)} open stock orders: {symbols}",
        file=sys.stderr,
    )
    raise SystemExit(1)

if open_positions:
    symbols = ",".join(sorted({position.symbol for position in open_positions}))
    print(
        f"{context} failed: broker has {len(open_positions)} open stock positions: {symbols}",
        file=sys.stderr,
    )
    raise SystemExit(1)

print(f"{context} broker exposure ok: open_orders=0 open_positions=0")
PY
