#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PROOF_STATUS_STRATEGY="${PROOF_STATUS_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
PROOF_STATUS_MIN_TRADES="${PROOF_STATUS_MIN_TRADES:-${PROFIT_PROBE_MIN_TRADES:-10}}"
PROOF_STATUS_MIN_PNL="${PROOF_STATUS_MIN_PNL:-${PROFIT_PROBE_MIN_PNL:-0.01}}"
PROOF_STATUS_START_DATE="${PROOF_STATUS_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}}"
PROOF_STATUS_END_DATE="${PROOF_STATUS_END_DATE:-}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${STRATEGY_VERSION:-}" ]]; then
  echo "missing STRATEGY_VERSION in $ENV_FILE" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_START_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "PROOF_STATUS_START_DATE must use YYYY-MM-DD" >&2
  exit 1
fi
if [[ -n "$PROOF_STATUS_END_DATE" && ! "$PROOF_STATUS_END_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "PROOF_STATUS_END_DATE must use YYYY-MM-DD" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_MIN_TRADES" =~ ^[0-9]+$ ]]; then
  echo "PROOF_STATUS_MIN_TRADES must be a non-negative integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_MIN_PNL" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
  echo "PROOF_STATUS_MIN_PNL must be a number" >&2
  exit 1
fi

export COMPOSE_ANSI="${COMPOSE_ANSI:-never}"
export COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-quiet}"

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)
trading_mode="${TRADING_MODE:-paper}"

echo "paper proof status context: proof_start=$PROOF_STATUS_START_DATE mode=$trading_mode strategy_version=$STRATEGY_VERSION strategy=$PROOF_STATUS_STRATEGY min_trades=$PROOF_STATUS_MIN_TRADES min_pnl=$PROOF_STATUS_MIN_PNL"
echo "paper proof trading status:"
"${compose[@]}" run -T --rm admin \
  status \
  --mode "$trading_mode" \
  --strategy-version "$STRATEGY_VERSION" \
  | sed 's/^/  /'

echo "paper proof database status:"
"${compose[@]}" run -T --rm \
  -e PROOF_STATUS_STRATEGY="$PROOF_STATUS_STRATEGY" \
  -e PROOF_STATUS_MIN_TRADES="$PROOF_STATUS_MIN_TRADES" \
  -e PROOF_STATUS_MIN_PNL="$PROOF_STATUS_MIN_PNL" \
  -e PROOF_STATUS_START_DATE="$PROOF_STATUS_START_DATE" \
  -e PROOF_STATUS_END_DATE="$PROOF_STATUS_END_DATE" \
  --entrypoint python admin <<'PY'
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import OrderStore


def parse_date(value: str, *, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must use YYYY-MM-DD") from exc


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


settings = Settings.from_env()
trading_mode = TradingMode(os.environ.get("TRADING_MODE", "paper"))
strategy_version = os.environ["STRATEGY_VERSION"]
strategy_name = os.environ["PROOF_STATUS_STRATEGY"]
min_trades = int(os.environ["PROOF_STATUS_MIN_TRADES"])
min_pnl = float(os.environ["PROOF_STATUS_MIN_PNL"])
proof_start = parse_date(os.environ["PROOF_STATUS_START_DATE"], name="PROOF_STATUS_START_DATE")
end_value = os.environ.get("PROOF_STATUS_END_DATE", "")
proof_end = (
    parse_date(end_value, name="PROOF_STATUS_END_DATE")
    if end_value
    else datetime.now(settings.market_timezone).date()
)
market_timezone = settings.market_timezone.key

conn = connect_postgres(settings.database_url)
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(string_agg(strategy_name, ',' ORDER BY strategy_name), '')
            FROM strategy_flags
            WHERE trading_mode = %s
              AND strategy_version = %s
              AND enabled = TRUE
            """,
            (trading_mode.value, strategy_version),
        )
        active_row = cur.fetchone()
        active_strategies = active_row[0] if active_row else ""

        cur.execute(
            """
            SELECT check_name, status, exit_code, session_date, proof_start, created_at
            FROM (
              SELECT DISTINCT ON (payload->>'check_name')
                payload->>'check_name' AS check_name,
                COALESCE(payload->>'status', '') AS status,
                COALESCE(payload->>'exit_code', '') AS exit_code,
                COALESCE(payload->>'session_date', '') AS session_date,
                COALESCE(payload->>'proof_start', '') AS proof_start,
                to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at
              FROM audit_events
              WHERE event_type = 'scheduled_check_completed'
                AND payload->>'trading_mode' = %s
                AND payload->>'strategy_version' = %s
                AND payload->>'check_name' IN (
                  'paper_readiness',
                  'paper_activity',
                  'session_guard',
                  'paper_profit_probe'
                )
              ORDER BY payload->>'check_name', created_at DESC, event_id DESC
            ) latest
            ORDER BY check_name
            """,
            (trading_mode.value, strategy_version),
        )
        scheduled_checks = cur.fetchall()

    order_store = OrderStore(conn)
    trades = []
    if proof_end >= proof_start:
        for session_date in date_range(proof_start, proof_end):
            trades.extend(
                order_store.list_closed_trades(
                    trading_mode=trading_mode,
                    strategy_version=strategy_version,
                    session_date=session_date,
                    strategy_name=strategy_name,
                    market_timezone=market_timezone,
                )
            )
finally:
    conn.close()

pnl = sum((trade["exit_fill"] - trade["entry_fill"]) * trade["qty"] for trade in trades)
trade_count = len(trades)
exit_sessions = [
    trade["exit_time"].astimezone(settings.market_timezone).date()
    for trade in trades
    if trade.get("exit_time") is not None
]
first_exit_session = min(exit_sessions).isoformat() if exit_sessions else ""
latest_exit_session = max(exit_sessions).isoformat() if exit_sessions else ""
proof_not_started = proof_end < proof_start
if proof_not_started:
    proof_status = "pending"
elif trade_count >= min_trades and pnl >= min_pnl:
    proof_status = "passed"
elif trade_count >= min_trades:
    proof_status = "failing"
else:
    proof_status = "pending"
proof_window = (
    f"{proof_start.isoformat()}..{proof_end.isoformat()}"
    if not proof_not_started
    else f"not_started(current_market_date={proof_end.isoformat()})"
)

print(f"paper proof active strategies: {active_strategies or 'none'}")
if scheduled_checks:
    for check_name, status, exit_code, session_date, check_proof_start, created_at in scheduled_checks:
        print(
            "paper proof scheduled check: "
            f"name={check_name} status={status or 'unknown'} "
            f"exit_code={exit_code or 'unknown'} "
            f"session_date={session_date or 'unknown'} "
            f"proof_start={check_proof_start or 'unknown'} "
            f"created_at={created_at or 'unknown'}"
        )
else:
    print("paper proof scheduled checks: none")
print(
    "paper proof progress: "
    f"status={proof_status} "
    f"closed_trades={trade_count} "
    f"required_trades={min_trades} "
    f"pnl={pnl:.2f} "
    f"required_pnl={min_pnl:.2f} "
    f"window={proof_window} "
    f"first_exit_session={first_exit_session or 'none'} "
    f"latest_exit_session={latest_exit_session or 'none'}"
)
PY
