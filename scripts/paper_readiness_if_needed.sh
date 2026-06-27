#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "paper readiness check skipped for TRADING_MODE=${TRADING_MODE:-unset}"
  exit 0
fi

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

latest_readiness="$("${compose[@]}" run -T --rm \
  --entrypoint python admin <<'PY' || true
from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
from alpaca_bot.storage.db import connect_postgres

settings = Settings.from_env()
market_timezone = ZoneInfo(settings.market_timezone.key)
today = datetime.now(market_timezone).date()
session_date = today
override = os.environ.get("PAPER_READINESS_SESSION_DATE", "")

if override:
    session_date = date.fromisoformat(override)
else:
    calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
        start=today,
        end=today + timedelta(days=10),
    )
    for session in calendar:
        if session.session_date >= today:
            session_date = session.session_date
            break

conn = connect_postgres(settings.database_url)
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT payload->>'status'
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'check_name' = 'paper_readiness'
              AND payload->>'session_date' = %s
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                session_date.isoformat(),
                settings.trading_mode.value,
                settings.strategy_version,
            ),
        )
        row = cur.fetchone()
finally:
    conn.close()

print(f"{session_date.isoformat()}|{row[0] if row else ''}")
PY
)"
latest_readiness="$(printf '%s\n' "$latest_readiness" | tail -n 1)"

session_date=""
latest_status=""
IFS='|' read -r session_date latest_status <<< "$latest_readiness"

if [[ "$session_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$latest_status" == "passed" ]]; then
  proof_start="${PROFIT_PROBE_START_DATE:-2026-06-29}"
  echo "scheduled check context: session_date=$session_date proof_start=$proof_start reason=already_passed"
  echo "paper readiness already passed for session $session_date; final retry not rerun"
  exit 0
fi

exec ./scripts/paper_readiness_check.sh "$ENV_FILE"
