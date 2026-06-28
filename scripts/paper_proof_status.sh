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

compact_check_detail() {
  local detail
  detail="$(printf '%s\n' "$1" | sed '/^[[:space:]]*$/d' | tail -n 1)"
  detail="${detail//$'\n'/; }"
  echo "$detail"
}

cron_health_status="ok"
if ! cron_health_detail="$(./scripts/cron_health_check.sh 2>&1)"; then
  cron_health_status="failed"
fi
cron_health_detail="$(compact_check_detail "$cron_health_detail")"

ops_health_status="ok"
if ! ops_health_detail="$(./scripts/ops_check.sh "$ENV_FILE" 2>&1)"; then
  ops_health_status="failed"
fi
ops_health_detail="$(compact_check_detail "$ops_health_detail")"

echo "paper proof status context: proof_start=$PROOF_STATUS_START_DATE mode=$trading_mode strategy_version=$STRATEGY_VERSION strategy=$PROOF_STATUS_STRATEGY min_trades=$PROOF_STATUS_MIN_TRADES min_pnl=$PROOF_STATUS_MIN_PNL"
echo "paper proof trading status:"
"${compose[@]}" run -T --rm admin \
  status \
  --mode "$trading_mode" \
  --strategy-version "$STRATEGY_VERSION" \
  | sed 's/^/  /'

echo "paper proof evidence status:"
"${compose[@]}" run -T --rm \
  -e PROOF_STATUS_STRATEGY="$PROOF_STATUS_STRATEGY" \
  -e PROOF_STATUS_MIN_TRADES="$PROOF_STATUS_MIN_TRADES" \
  -e PROOF_STATUS_MIN_PNL="$PROOF_STATUS_MIN_PNL" \
  -e PROOF_STATUS_START_DATE="$PROOF_STATUS_START_DATE" \
  -e PROOF_STATUS_END_DATE="$PROOF_STATUS_END_DATE" \
  -e PROOF_STATUS_CRON_HEALTH_STATUS="$cron_health_status" \
  -e PROOF_STATUS_CRON_HEALTH_DETAIL="$cron_health_detail" \
  -e PROOF_STATUS_OPS_HEALTH_STATUS="$ops_health_status" \
  -e PROOF_STATUS_OPS_HEALTH_DETAIL="$ops_health_detail" \
  --entrypoint python admin <<'PY'
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
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


def load_latest_completed_session_date(settings: Settings) -> tuple[date | None, str | None]:
    now = datetime.now(settings.market_timezone)
    try:
        calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
            start=now.date() - timedelta(days=14),
            end=now.date(),
        )
    except Exception as exc:
        return None, str(exc)

    completed = []
    for session in calendar:
        close_at = session.close_at
        if close_at.tzinfo is None:
            close_at = close_at.replace(tzinfo=settings.market_timezone)
        else:
            close_at = close_at.astimezone(settings.market_timezone)
        if now >= close_at + timedelta(minutes=30):
            completed.append(session.session_date)
    if not completed:
        return None, "no completed market sessions found"
    return max(completed), None


def load_broker_exposure(
    settings: Settings,
) -> tuple[
    int | None,
    int | None,
    float | None,
    float | None,
    float | None,
    bool | None,
    str | None,
    str | None,
]:
    try:
        broker = AlpacaExecutionAdapter.from_settings(settings)
        open_orders = broker.list_open_orders()
        open_positions = broker.list_positions()
        account = broker.get_account()
    except Exception as exc:
        return None, None, None, None, None, None, None, str(exc)
    equity = float(account.equity)
    buying_power = float(account.buying_power)
    minimum_buying_power = equity * float(settings.max_position_pct)
    trading_blocked = bool(account.trading_blocked)
    account_status = (
        "blocked"
        if trading_blocked or equity <= 0 or buying_power < minimum_buying_power
        else "ok"
    )
    return (
        len(open_orders),
        len(open_positions),
        equity,
        buying_power,
        minimum_buying_power,
        trading_blocked,
        account_status,
        None,
    )


settings = Settings.from_env()
trading_mode = TradingMode(os.environ.get("TRADING_MODE", "paper"))
strategy_version = os.environ["STRATEGY_VERSION"]
strategy_name = os.environ["PROOF_STATUS_STRATEGY"]
min_trades = int(os.environ["PROOF_STATUS_MIN_TRADES"])
min_pnl = float(os.environ["PROOF_STATUS_MIN_PNL"])
cron_health_status = os.environ.get("PROOF_STATUS_CRON_HEALTH_STATUS", "unknown")
cron_health_detail = os.environ.get("PROOF_STATUS_CRON_HEALTH_DETAIL", "").strip()
ops_health_status = os.environ.get("PROOF_STATUS_OPS_HEALTH_STATUS", "unknown")
ops_health_detail = os.environ.get("PROOF_STATUS_OPS_HEALTH_DETAIL", "").strip()
proof_start = parse_date(os.environ["PROOF_STATUS_START_DATE"], name="PROOF_STATUS_START_DATE")
end_value = os.environ.get("PROOF_STATUS_END_DATE", "")
current_market_date = datetime.now(settings.market_timezone).date()
latest_completed_session, calendar_warning = load_latest_completed_session_date(settings)
(
    broker_open_orders,
    broker_open_positions,
    broker_equity,
    broker_buying_power,
    broker_minimum_buying_power,
    broker_trading_blocked,
    broker_account_status,
    broker_exposure_warning,
) = load_broker_exposure(settings)
proof_end = (
    parse_date(end_value, name="PROOF_STATUS_END_DATE")
    if end_value
    else latest_completed_session or current_market_date
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

        cur.execute(
            """
            SELECT
              (
                SELECT COUNT(*)::int
                FROM positions
                WHERE trading_mode = %s
                  AND strategy_version = %s
              ) AS open_positions,
              (
                SELECT COUNT(*)::int
                FROM orders
                WHERE trading_mode = %s
                  AND strategy_version = %s
                  AND status IN (
                    'pending_submit',
                    'submitting',
                    'new',
                    'accepted',
                    'submitted',
                    'partially_filled',
                    'held',
                    'pending_new'
                  )
              ) AS active_orders
            """,
            (
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
            ),
        )
        exposure_row = cur.fetchone()
        local_open_positions = int(exposure_row[0] or 0) if exposure_row else 0
        local_active_orders = int(exposure_row[1] or 0) if exposure_row else 0

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
    else (
        "not_started("
        f"latest_completed_session={latest_completed_session.isoformat() if latest_completed_session else 'unknown'} "
        f"current_market_date={current_market_date.isoformat()}"
        ")"
    )
)
active_strategy_names = [name for name in active_strategies.split(",") if name]
strategy_status = "ok" if strategy_name in active_strategy_names else "disabled"
posture_status = (
    "ok"
    if (
        abs(float(settings.relative_volume_threshold) - 2.0) < 1e-9
        and int(settings.max_open_positions) == 3
        and bool(settings.enable_vwap_entry_filter)
        and not bool(settings.enable_vix_filter)
        and not bool(settings.enable_sector_filter)
        and not bool(settings.extended_hours_enabled)
        and bool(settings.paper_proof_freeze)
        and int(settings.intraday_consecutive_loss_gate) == 0
    )
    else "drifted"
)
blockers = []
if strategy_status != "ok":
    blockers.append("strategy_disabled")
if posture_status != "ok":
    blockers.append("posture_drifted")
if cron_health_status != "ok":
    blockers.append("cron_health_failed")
if ops_health_status != "ok":
    blockers.append("ops_health_failed")
if local_open_positions > 0:
    blockers.append("local_open_positions")
if local_active_orders > 0:
    blockers.append("local_active_orders")
if broker_exposure_warning:
    blockers.append("broker_exposure_unknown")
else:
    if broker_open_orders and broker_open_orders > 0:
        blockers.append("broker_open_orders")
    if broker_open_positions and broker_open_positions > 0:
        blockers.append("broker_open_positions")
    if broker_account_status != "ok":
        blockers.append("broker_account_blocked")

warnings = []
if calendar_warning:
    warnings.append("calendar_warning")

readiness_status = "blocked" if blockers else "ready"
if proof_status == "passed":
    proof_reason = "profit_proven"
elif proof_status == "failing":
    proof_reason = "pnl_below_minimum"
elif proof_not_started:
    proof_reason = "awaiting_completed_proof_session"
elif trade_count < min_trades:
    proof_reason = "awaiting_min_trades"
else:
    proof_reason = "awaiting_positive_pnl"

print(
    "paper proof summary: "
    f"readiness={readiness_status} "
    f"proof={proof_status} "
    f"reason={proof_reason} "
    f"blockers={','.join(blockers) if blockers else 'none'} "
    f"warnings={','.join(warnings) if warnings else 'none'}"
)

print(
    "paper proof automation: "
    f"cron_status={cron_health_status} "
    f"cron_detail={cron_health_detail or 'none'}"
)
print(
    "paper proof runtime: "
    f"ops_status={ops_health_status} "
    f"ops_detail={ops_health_detail or 'none'}"
)
print(f"paper proof active strategies: {active_strategies or 'none'}")
print(
    "paper proof strategy status: "
    f"status={strategy_status} target={strategy_name} active=[{active_strategies or ''}]"
)
print(
    "paper proof posture: "
    f"status={posture_status} "
    f"relative_volume_threshold={settings.relative_volume_threshold:g} "
    f"max_open_positions={settings.max_open_positions} "
    f"vwap_filter={str(settings.enable_vwap_entry_filter).lower()} "
    f"vix_filter={str(settings.enable_vix_filter).lower()} "
    f"sector_filter={str(settings.enable_sector_filter).lower()} "
    f"extended_hours={str(settings.extended_hours_enabled).lower()} "
    f"paper_proof_freeze={str(settings.paper_proof_freeze).lower()} "
    f"intraday_consecutive_loss_gate={settings.intraday_consecutive_loss_gate}"
)
print(
    "paper proof local exposure: "
    f"positions={local_open_positions} active_orders={local_active_orders}"
)
if broker_exposure_warning:
    print(f"paper proof broker exposure warning: {broker_exposure_warning}")
else:
    print(
        "paper proof broker exposure: "
        f"open_orders={broker_open_orders} open_positions={broker_open_positions}"
    )
    print(
        "paper proof broker account: "
        f"status={broker_account_status} "
        f"equity={broker_equity:.2f} "
        f"buying_power={broker_buying_power:.2f} "
        f"minimum_required={broker_minimum_buying_power:.2f} "
        f"trading_blocked={str(broker_trading_blocked).lower()}"
    )
if calendar_warning:
    print(f"paper proof calendar warning: {calendar_warning}")
print(
    "paper proof calendar: "
    f"current_market_date={current_market_date.isoformat()} "
    f"latest_completed_session={latest_completed_session.isoformat() if latest_completed_session else 'unknown'} "
    f"scoring_end_date={proof_end.isoformat()}"
)
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
