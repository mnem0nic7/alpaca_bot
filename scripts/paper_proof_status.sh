#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PROOF_STATUS_STRATEGY="${PROOF_STATUS_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
PROOF_STATUS_MIN_TRADES="${PROOF_STATUS_MIN_TRADES:-${PROFIT_PROBE_MIN_TRADES:-10}}"
PROOF_STATUS_MIN_PNL="${PROOF_STATUS_MIN_PNL:-${PROFIT_PROBE_MIN_PNL:-0.01}}"
PROOF_STATUS_START_DATE="${PROOF_STATUS_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}}"
PROOF_STATUS_END_DATE="${PROOF_STATUS_END_DATE:-}"
PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT="${PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT:-./scripts/runtime_image_health_check.sh}"
PROOF_STATUS_FAIL_ON_ISSUES="${PROOF_STATUS_FAIL_ON_ISSUES:-false}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

PROOF_STATUS_MIN_WATCHLIST_SYMBOLS="${PROOF_STATUS_MIN_WATCHLIST_SYMBOLS:-${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}}"
PROOF_STATUS_MIN_CONFIDENCE_FLOOR="${PROOF_STATUS_MIN_CONFIDENCE_FLOOR:-${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.25}}"
PROOF_STATUS_STREAM_START_GRACE_SECONDS="${PROOF_STATUS_STREAM_START_GRACE_SECONDS:-120}"
PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES="${PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES:-${PAPER_READINESS_MAX_PASS_AGE_MINUTES:-180}}"

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
if [[ ! "$PROOF_STATUS_MIN_WATCHLIST_SYMBOLS" =~ ^[0-9]+$ ]] \
  || [[ "$PROOF_STATUS_MIN_WATCHLIST_SYMBOLS" -lt 1 ]]; then
  echo "PROOF_STATUS_MIN_WATCHLIST_SYMBOLS must be a positive integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_MIN_CONFIDENCE_FLOOR" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PROOF_STATUS_MIN_CONFIDENCE_FLOOR must be a non-negative number" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_STREAM_START_GRACE_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "PROOF_STATUS_STREAM_START_GRACE_SECONDS must be a non-negative integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES" =~ ^[0-9]+$ || "$PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES" -le 0 ]]; then
  echo "PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES must be a positive integer" >&2
  exit 1
fi
case "${PROOF_STATUS_FAIL_ON_ISSUES,,}" in
  true|false) ;;
  *)
    echo "PROOF_STATUS_FAIL_ON_ISSUES must be true or false" >&2
    exit 1
    ;;
esac

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
if ! ops_health_detail="$(./scripts/ops_check.sh "$ENV_FILE" \
  --expect-trading-mode "$trading_mode" \
  --expect-strategy-version "$STRATEGY_VERSION" \
  --expect-trading-status enabled \
  --expect-kill-switch false \
  --expect-only-enabled-strategy "$PROOF_STATUS_STRATEGY" \
  2>&1)"; then
  ops_health_status="failed"
fi
ops_health_detail="$(compact_check_detail "$ops_health_detail")"

runtime_image_health_status="ok"
if ! runtime_image_health_detail="$("$PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT" "$ENV_FILE" 2>&1)"; then
  runtime_image_health_status="failed"
fi
runtime_image_health_detail="$(compact_check_detail "$runtime_image_health_detail")"

echo "scheduled check context: session_date=$(TZ=America/New_York date +%F) proof_start=$PROOF_STATUS_START_DATE strategy=$PROOF_STATUS_STRATEGY min_trades=$PROOF_STATUS_MIN_TRADES min_pnl=$PROOF_STATUS_MIN_PNL"
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
  -e PROOF_STATUS_MIN_WATCHLIST_SYMBOLS="$PROOF_STATUS_MIN_WATCHLIST_SYMBOLS" \
  -e PROOF_STATUS_MIN_CONFIDENCE_FLOOR="$PROOF_STATUS_MIN_CONFIDENCE_FLOOR" \
  -e PROOF_STATUS_STREAM_START_GRACE_SECONDS="$PROOF_STATUS_STREAM_START_GRACE_SECONDS" \
  -e PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES="$PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES" \
  -e PROOF_STATUS_START_DATE="$PROOF_STATUS_START_DATE" \
  -e PROOF_STATUS_END_DATE="$PROOF_STATUS_END_DATE" \
  -e PROOF_STATUS_CRON_HEALTH_STATUS="$cron_health_status" \
  -e PROOF_STATUS_CRON_HEALTH_DETAIL="$cron_health_detail" \
  -e PROOF_STATUS_OPS_HEALTH_STATUS="$ops_health_status" \
  -e PROOF_STATUS_OPS_HEALTH_DETAIL="$ops_health_detail" \
  -e PROOF_STATUS_RUNTIME_IMAGE_HEALTH_STATUS="$runtime_image_health_status" \
  -e PROOF_STATUS_RUNTIME_IMAGE_HEALTH_DETAIL="$runtime_image_health_detail" \
  -e PROOF_STATUS_FAIL_ON_ISSUES="$PROOF_STATUS_FAIL_ON_ISSUES" \
  --entrypoint python admin <<'PY'
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone

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


def load_next_market_session_date(settings: Settings) -> tuple[date | None, str | None]:
    now = datetime.now(settings.market_timezone)
    try:
        calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
            start=now.date(),
            end=now.date() + timedelta(days=10),
        )
    except Exception as exc:
        return None, str(exc)

    upcoming = [
        session.session_date for session in calendar if session.session_date >= now.date()
    ]
    if not upcoming:
        return None, "no upcoming market sessions found"
    return min(upcoming), None


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
min_watchlist_symbols = int(os.environ["PROOF_STATUS_MIN_WATCHLIST_SYMBOLS"])
min_confidence_floor = float(os.environ["PROOF_STATUS_MIN_CONFIDENCE_FLOOR"])
stream_start_grace_seconds = int(os.environ["PROOF_STATUS_STREAM_START_GRACE_SECONDS"])
readiness_max_pass_age_minutes = int(
    os.environ["PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES"]
)
fail_on_issues = os.environ.get("PROOF_STATUS_FAIL_ON_ISSUES", "false").lower() == "true"
cron_health_status = os.environ.get("PROOF_STATUS_CRON_HEALTH_STATUS", "unknown")
cron_health_detail = os.environ.get("PROOF_STATUS_CRON_HEALTH_DETAIL", "").strip()
ops_health_status = os.environ.get("PROOF_STATUS_OPS_HEALTH_STATUS", "unknown")
ops_health_detail = os.environ.get("PROOF_STATUS_OPS_HEALTH_DETAIL", "").strip()
runtime_image_health_status = os.environ.get(
    "PROOF_STATUS_RUNTIME_IMAGE_HEALTH_STATUS", "unknown"
)
runtime_image_health_detail = os.environ.get(
    "PROOF_STATUS_RUNTIME_IMAGE_HEALTH_DETAIL", ""
).strip()
proof_start = parse_date(os.environ["PROOF_STATUS_START_DATE"], name="PROOF_STATUS_START_DATE")
end_value = os.environ.get("PROOF_STATUS_END_DATE", "")
current_market_datetime = datetime.now(settings.market_timezone)
current_market_date = current_market_datetime.date()
latest_completed_session, calendar_warning = load_latest_completed_session_date(settings)
next_market_session, next_session_warning = load_next_market_session_date(settings)
if next_session_warning:
    calendar_warning = (
        f"{calendar_warning}; {next_session_warning}"
        if calendar_warning
        else next_session_warning
    )
readiness_target_session = next_market_session or current_market_date
if readiness_target_session < proof_start:
    readiness_target_session = proof_start
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
post_close_target_session = proof_end if proof_end >= proof_start else None
activity_target_session = None
if current_market_date >= proof_start and (
    next_market_session == current_market_date
    or latest_completed_session == current_market_date
):
    activity_target_session = current_market_date
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
        active_strategy_names = [name for name in active_strategies.split(",") if name]

        cur.execute(
            """
            SELECT strategy_name, weight, sharpe
            FROM strategy_weights
            WHERE trading_mode = %s
              AND strategy_version = %s
            ORDER BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        strategy_weight_rows = cur.fetchall()
        weights_by_strategy = {
            row[0]: {"weight": row[1], "sharpe": row[2]}
            for row in strategy_weight_rows
        }

        cur.execute(
            """
            SELECT floor_value, manual_floor_baseline, set_by
            FROM confidence_floor_store
            WHERE trading_mode = %s
              AND strategy_version = %s
            """,
            (trading_mode.value, strategy_version),
        )
        confidence_floor_row = cur.fetchone()
        if confidence_floor_row:
            confidence_floor_value = float(confidence_floor_row[0])
            confidence_floor_manual_baseline = float(confidence_floor_row[1])
            confidence_floor_set_by = confidence_floor_row[2] or "unknown"
        else:
            confidence_floor_value = float(settings.confidence_floor)
            confidence_floor_manual_baseline = float(settings.confidence_floor)
            confidence_floor_set_by = "settings"

        cur.execute(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE enabled = TRUE AND COALESCE(ignored, FALSE) = FALSE
              )::int AS active_symbols,
              COUNT(*) FILTER (WHERE enabled = TRUE)::int AS enabled_symbols,
              COUNT(*) FILTER (
                WHERE enabled = TRUE AND COALESCE(ignored, FALSE) = TRUE
              )::int AS ignored_symbols
            FROM symbol_watchlist
            WHERE trading_mode = %s
            """,
            (trading_mode.value,),
        )
        watchlist_row = cur.fetchone()
        active_watchlist_symbols = int(watchlist_row[0] or 0) if watchlist_row else 0
        enabled_watchlist_symbols = int(watchlist_row[1] or 0) if watchlist_row else 0
        ignored_watchlist_symbols = int(watchlist_row[2] or 0) if watchlist_row else 0

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

        activity_audit_row = None
        if activity_target_session is not None:
            cur.execute(
                """
                SELECT
                  COALESCE(payload->>'status', '') AS status,
                  COALESCE(payload->>'exit_code', '') AS exit_code,
                  created_at
                FROM audit_events
                WHERE event_type = 'scheduled_check_completed'
                  AND payload->>'trading_mode' = %s
                  AND payload->>'strategy_version' = %s
                  AND payload->>'check_name' = 'paper_activity'
                  AND payload->>'session_date' = %s
                  AND payload->>'proof_start' = %s
                ORDER BY created_at DESC, event_id DESC
                LIMIT 1
                """,
                (
                    trading_mode.value,
                    strategy_version,
                    activity_target_session.isoformat(),
                    proof_start.isoformat(),
                ),
            )
            activity_audit_row = cur.fetchone()

        cur.execute(
            """
            SELECT created_at
            FROM audit_events
            WHERE event_type = 'supervisor_started'
              AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = %s)
              AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = %s)
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (trading_mode.value, strategy_version),
        )
        latest_supervisor_row = cur.fetchone()
        latest_supervisor_started_at = (
            latest_supervisor_row[0] if latest_supervisor_row else None
        )

        cur.execute(
            """
            SELECT event_type, created_at
            FROM audit_events
            WHERE event_type IN (
                'trade_update_stream_started',
                'trade_update_stream_stopped',
                'trade_update_stream_failed',
                'trade_update_failed',
                'stream_heartbeat_stale',
                'stream_restart_failed'
              )
              AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = %s)
              AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = %s)
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (trading_mode.value, strategy_version),
        )
        latest_stream_event_row = cur.fetchone()

        cur.execute(
            """
            SELECT created_at
            FROM audit_events
            WHERE event_type = 'trade_update_stream_started'
              AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = %s)
              AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = %s)
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (trading_mode.value, strategy_version),
        )
        latest_stream_start_row = cur.fetchone()
        latest_stream_started_at = (
            latest_stream_start_row[0] if latest_stream_start_row else None
        )

        cur.execute(
            """
            SELECT
              COALESCE(payload->>'status', '') AS status,
              created_at
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
              AND payload->>'check_name' = 'paper_readiness'
              AND payload->>'session_date' = %s
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            (
                trading_mode.value,
                strategy_version,
                readiness_target_session.isoformat(),
            ),
        )
        readiness_audit_row = cur.fetchone()

        post_close_audit_rows = []
        if post_close_target_session is not None:
            cur.execute(
                """
                SELECT check_name, status, exit_code, created_at
                FROM (
                  SELECT DISTINCT ON (payload->>'check_name')
                    payload->>'check_name' AS check_name,
                    COALESCE(payload->>'status', '') AS status,
                    COALESCE(payload->>'exit_code', '') AS exit_code,
                    created_at
                  FROM audit_events
                  WHERE event_type = 'scheduled_check_completed'
                    AND payload->>'trading_mode' = %s
                    AND payload->>'strategy_version' = %s
                    AND payload->>'check_name' IN (
                      'session_guard',
                      'paper_profit_probe'
                    )
                    AND payload->>'session_date' = %s
                    AND payload->>'proof_start' = %s
                  ORDER BY payload->>'check_name', created_at DESC, event_id DESC
                ) latest
                ORDER BY check_name
                """,
                (
                    trading_mode.value,
                    strategy_version,
                    post_close_target_session.isoformat(),
                    proof_start.isoformat(),
                ),
            )
            post_close_audit_rows = cur.fetchall()

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
                    'pending_new',
                    'new',
                    'accepted',
                    'accepted_for_bidding',
                    'submitted',
                    'partially_filled',
                    'held',
                    'pending_replace',
                    'pending_cancel',
                    'stopped',
                    'suspended',
                    'done_for_day'
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
latest_supervisor_started_text = (
    latest_supervisor_started_at.isoformat()
    if latest_supervisor_started_at is not None
    else "none"
)
latest_stream_event_type = latest_stream_event_row[0] if latest_stream_event_row else None
latest_stream_event_at = latest_stream_event_row[1] if latest_stream_event_row else None
latest_stream_started_text = (
    latest_stream_started_at.isoformat()
    if latest_stream_started_at is not None
    else "none"
)
latest_stream_event_text = (
    f"{latest_stream_event_type}:{latest_stream_event_at.isoformat()}"
    if latest_stream_event_type is not None and latest_stream_event_at is not None
    else "none"
)
stream_status = "ok"
if latest_stream_started_at is None:
    stream_status = "missing"
stream_issue_status_by_event_type = {
    "trade_update_stream_failed": "failed",
    "trade_update_stream_stopped": "stopped",
    "trade_update_failed": "trade_update_failed",
    "stream_heartbeat_stale": "heartbeat_stale",
    "stream_restart_failed": "restart_failed",
}
if (
    stream_status == "ok"
    and latest_stream_event_type in stream_issue_status_by_event_type
    and latest_stream_event_at is not None
    and latest_stream_started_at is not None
    and latest_stream_event_at >= latest_stream_started_at
):
    stream_status = stream_issue_status_by_event_type[latest_stream_event_type]
elif (
    latest_supervisor_started_at is not None
    and latest_stream_started_at
    < latest_supervisor_started_at - timedelta(seconds=stream_start_grace_seconds)
):
    stream_status = "stale"
readiness_audit_check_status = "missing"
readiness_audit_created_at = None
readiness_audit_age_minutes = None
readiness_audit_status = "missing"
if readiness_audit_row:
    readiness_audit_check_status = readiness_audit_row[0] or "unknown"
    readiness_audit_created_at = readiness_audit_row[1]
    readiness_audit_created_utc = readiness_audit_created_at
    if readiness_audit_created_utc.tzinfo is None:
        readiness_audit_created_utc = readiness_audit_created_utc.replace(
            tzinfo=timezone.utc
        )
    else:
        readiness_audit_created_utc = readiness_audit_created_utc.astimezone(
            timezone.utc
        )
    readiness_audit_age_minutes = max(
        0,
        int(
            (
                datetime.now(timezone.utc) - readiness_audit_created_utc
            ).total_seconds()
            // 60
        ),
    )
    if (
        latest_supervisor_started_at is not None
        and readiness_audit_created_at < latest_supervisor_started_at
    ):
        readiness_audit_status = "stale"
    elif readiness_audit_age_minutes > readiness_max_pass_age_minutes:
        readiness_audit_status = "stale_by_age"
    elif readiness_audit_check_status == "passed":
        readiness_audit_status = "ok"
    else:
        readiness_audit_status = readiness_audit_check_status
readiness_audit_created_text = (
    readiness_audit_created_at.isoformat()
    if readiness_audit_created_at is not None
    else "none"
)
readiness_audit_age_text = (
    str(readiness_audit_age_minutes)
    if readiness_audit_age_minutes is not None
    else "none"
)
activity_due = False
activity_due_after = "none"
activity_check_status = "missing"
activity_check_exit_code = "unknown"
activity_check_created_text = "none"
activity_audit_status = "not_started"
if activity_target_session is not None:
    activity_due_time = time(10, 35)
    activity_due_after = (
        f"{activity_target_session.isoformat()} "
        f"{activity_due_time.strftime('%H:%M')} {settings.market_timezone.key}"
    )
    activity_due = current_market_datetime.date() > activity_target_session or (
        current_market_datetime.date() == activity_target_session
        and current_market_datetime.time() >= activity_due_time
    )
    activity_audit_status = "not_due"
    if activity_audit_row:
        activity_check_status = activity_audit_row[0] or "unknown"
        activity_check_exit_code = activity_audit_row[1] or "unknown"
        activity_created_at = activity_audit_row[2]
        activity_check_created_text = (
            activity_created_at.isoformat() if activity_created_at is not None else "none"
        )
        if activity_check_status == "passed":
            activity_audit_status = "ok"
        elif activity_check_status == "skipped":
            activity_audit_status = "skipped" if activity_due else "ok"
        elif activity_check_status == "pending":
            activity_audit_status = "pending"
        else:
            activity_audit_status = "failed"
    elif activity_due:
        activity_audit_status = "missing"
post_close_due = False
post_close_due_after = "none"
post_close_audit_status = "not_started"
post_close_pass_evidence_ready = False
post_close_check_statuses = {
    "session_guard": "missing",
    "paper_profit_probe": "missing",
}
if post_close_target_session is not None:
    due_time = time(17, 25)
    post_close_due_after = (
        f"{post_close_target_session.isoformat()} "
        f"{due_time.strftime('%H:%M')} {settings.market_timezone.key}"
    )
    post_close_due = current_market_datetime.date() > post_close_target_session or (
        current_market_datetime.date() == post_close_target_session
        and current_market_datetime.time() >= due_time
    )
    post_close_audit_status = "not_due"
    for check_name, status, exit_code, created_at in post_close_audit_rows:
        created_text = created_at.isoformat() if created_at is not None else "none"
        post_close_check_statuses[check_name] = (
            f"{status or 'unknown'}:{exit_code or 'unknown'}:{created_text}"
        )
    if post_close_due:
        missing_checks = [
            name
            for name, status in post_close_check_statuses.items()
            if status == "missing"
        ]
        failed_checks = []
        session_guard_status = post_close_check_statuses["session_guard"].split(":", 1)[0]
        profit_probe_parts = post_close_check_statuses["paper_profit_probe"].split(":")
        profit_probe_status = profit_probe_parts[0]
        profit_probe_exit_code = profit_probe_parts[1] if len(profit_probe_parts) > 1 else ""
        if session_guard_status != "missing" and session_guard_status != "passed":
            failed_checks.append("session_guard")
        if profit_probe_status != "missing" and not (
            profit_probe_status == "passed"
            or (profit_probe_status == "pending" and profit_probe_exit_code == "43")
        ):
            failed_checks.append("paper_profit_probe")
        if missing_checks:
            post_close_audit_status = "missing"
        elif failed_checks:
            post_close_audit_status = "failed"
        else:
            post_close_audit_status = "ok"
            post_close_pass_evidence_ready = (
                session_guard_status == "passed" and profit_probe_status == "passed"
            )
proof_not_started = proof_end < proof_start
profitable_enough = trade_count >= min_trades and pnl >= min_pnl
if proof_not_started:
    proof_status = "pending"
elif profitable_enough and post_close_pass_evidence_ready:
    proof_status = "passed"
elif profitable_enough:
    proof_status = "pending"
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
strategy_status = "ok" if strategy_name in active_strategy_names else "disabled"
watchlist_status = (
    "ok"
    if active_watchlist_symbols >= min_watchlist_symbols
    else "under_minimum"
)
stored_weight_names = sorted(weights_by_strategy)
stored_weight_sum = sum(float(row["weight"]) for row in weights_by_strategy.values())
nonpositive_weight_count = sum(
    1 for row in weights_by_strategy.values() if float(row["weight"]) <= 0.0
)
null_sharpe_count = sum(1 for row in weights_by_strategy.values() if row["sharpe"] is None)
weight_status = (
    "ok"
    if (
        active_strategy_names
        and active_strategy_names == stored_weight_names
        and nonpositive_weight_count == 0
        and null_sharpe_count == 0
        and abs(stored_weight_sum - 1.0) < 0.0001
    )
    else "mismatch"
)
confidence_floor_status = (
    "ok"
    if min_confidence_floor <= confidence_floor_value <= 1.0
    else "mismatch"
)
sizing_status = (
    "ok" if weight_status == "ok" and confidence_floor_status == "ok" else "drifted"
)
target_weight_info = weights_by_strategy.get(strategy_name)
target_weight = (
    float(target_weight_info["weight"]) if target_weight_info is not None else None
)
target_sharpe = (
    float(target_weight_info["sharpe"])
    if target_weight_info is not None and target_weight_info["sharpe"] is not None
    else None
)
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
if watchlist_status != "ok":
    blockers.append("watchlist_under_minimum")
if sizing_status != "ok":
    blockers.append("sizing_drifted")
if posture_status != "ok":
    blockers.append("posture_drifted")
if cron_health_status != "ok":
    blockers.append("cron_health_failed")
if ops_health_status != "ok":
    blockers.append("ops_health_failed")
if runtime_image_health_status != "ok":
    blockers.append("runtime_image_health_failed")
if stream_status != "ok":
    blockers.append(f"stream_{stream_status}")
if readiness_audit_status != "ok":
    blockers.append(f"readiness_audit_{readiness_audit_status}")
if activity_audit_status in {"missing", "failed", "skipped"} or (
    activity_due and activity_audit_status == "pending"
):
    blockers.append(f"activity_audit_{activity_audit_status}")
if post_close_audit_status in {"missing", "failed"}:
    blockers.append(f"post_close_audit_{post_close_audit_status}")
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
elif profitable_enough and not post_close_pass_evidence_ready:
    proof_reason = "awaiting_post_close_audit"
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
    f"ops_detail={ops_health_detail or 'none'} "
    f"image_status={runtime_image_health_status} "
    f"image_detail={runtime_image_health_detail or 'none'}"
)
print(
    "paper proof stream: "
    f"status={stream_status} "
    f"latest_start={latest_stream_started_text} "
    f"latest_event={latest_stream_event_text} "
    f"latest_supervisor_started_at={latest_supervisor_started_text} "
    f"grace_seconds={stream_start_grace_seconds}"
)
print(
    "paper proof readiness audit: "
    f"status={readiness_audit_status} "
    f"target_session={readiness_target_session.isoformat()} "
    f"check_status={readiness_audit_check_status} "
    f"created_at={readiness_audit_created_text} "
    f"age_minutes={readiness_audit_age_text} "
    f"max_age_minutes={readiness_max_pass_age_minutes} "
    f"latest_supervisor_started_at={latest_supervisor_started_text}"
)
print(
    "paper proof activity audit: "
    f"status={activity_audit_status} "
    f"target_session={activity_target_session.isoformat() if activity_target_session else 'none'} "
    f"due={str(activity_due).lower()} "
    f"due_after={activity_due_after} "
    f"check={activity_check_status}:{activity_check_exit_code}:{activity_check_created_text}"
)
print(
    "paper proof post-close audit: "
    f"status={post_close_audit_status} "
    f"target_session={post_close_target_session.isoformat() if post_close_target_session else 'none'} "
    f"due={str(post_close_due).lower()} "
    f"due_after={post_close_due_after} "
    f"session_guard={post_close_check_statuses['session_guard']} "
    f"paper_profit_probe={post_close_check_statuses['paper_profit_probe']}"
)
print(f"paper proof active strategies: {active_strategies or 'none'}")
print(
    "paper proof strategy status: "
    f"status={strategy_status} target={strategy_name} active=[{active_strategies or ''}]"
)
print(
    "paper proof watchlist: "
    f"status={watchlist_status} "
    f"active={active_watchlist_symbols} "
    f"enabled={enabled_watchlist_symbols} "
    f"ignored={ignored_watchlist_symbols} "
    f"required_active={min_watchlist_symbols}"
)
print(
    "paper proof sizing: "
    f"status={sizing_status} "
    f"confidence_floor={confidence_floor_value:g} "
    f"manual_baseline={confidence_floor_manual_baseline:g} "
    f"set_by={confidence_floor_set_by} "
    f"required_floor={min_confidence_floor:g} "
    f"weight_status={weight_status} "
    f"active_weights=[{','.join(active_strategy_names)}] "
    f"stored_weights=[{','.join(stored_weight_names)}] "
    f"weight_sum={stored_weight_sum:g} "
    f"target_weight={target_weight if target_weight is not None else 'missing'} "
    f"target_sharpe={target_sharpe if target_sharpe is not None else 'missing'}"
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
if fail_on_issues and (
    readiness_status != "ready" or blockers or proof_status == "failing"
):
    raise SystemExit(1)
PY
