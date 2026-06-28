#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"

_preserved_env_names=()
_preserved_env_values=()

capture_env_overrides() {
  local name
  for name in "$@"; do
    if [[ -n "${!name+x}" ]]; then
      _preserved_env_names+=("$name")
      _preserved_env_values+=("${!name}")
    fi
  done
}

restore_env_overrides() {
  local index
  for index in "${!_preserved_env_names[@]}"; do
    printf -v "${_preserved_env_names[$index]}" '%s' "${_preserved_env_values[$index]}"
    export "${_preserved_env_names[$index]}"
  done
}

capture_env_overrides \
  PROOF_STATUS_STRATEGY \
  PROOF_STATUS_MIN_TRADES \
  PROOF_STATUS_MIN_PNL \
  PROOF_STATUS_START_DATE \
  PROOF_STATUS_END_DATE \
  PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT \
  PROOF_STATUS_FAIL_ON_ISSUES \
  PROOF_STATUS_MIN_WATCHLIST_SYMBOLS \
  PROOF_STATUS_MIN_CONFIDENCE_FLOOR \
  PROOF_STATUS_REQUIRE_SCENARIOS \
  PROOF_STATUS_SCENARIO_DIR \
  PROOF_STATUS_STREAM_START_GRACE_SECONDS \
  PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES \
  PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS \
  PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
restore_env_overrides

PROOF_STATUS_STRATEGY="${PROOF_STATUS_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
PROOF_STATUS_MIN_TRADES="${PROOF_STATUS_MIN_TRADES:-${PROFIT_PROBE_MIN_TRADES:-10}}"
PROOF_STATUS_MIN_PNL="${PROOF_STATUS_MIN_PNL:-${PROFIT_PROBE_MIN_PNL:-0.01}}"
PROOF_STATUS_START_DATE="${PROOF_STATUS_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}}"
PROOF_STATUS_END_DATE="${PROOF_STATUS_END_DATE:-}"
PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT="${PROOF_STATUS_RUNTIME_IMAGE_HEALTH_SCRIPT:-./scripts/runtime_image_health_check.sh}"
PROOF_STATUS_FAIL_ON_ISSUES="${PROOF_STATUS_FAIL_ON_ISSUES:-false}"
PROOF_STATUS_MIN_WATCHLIST_SYMBOLS="${PROOF_STATUS_MIN_WATCHLIST_SYMBOLS:-${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}}"
PROOF_STATUS_MIN_CONFIDENCE_FLOOR="${PROOF_STATUS_MIN_CONFIDENCE_FLOOR:-${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.25}}"
PROOF_STATUS_REQUIRE_SCENARIOS="${PROOF_STATUS_REQUIRE_SCENARIOS:-${PAPER_READINESS_REQUIRE_SCENARIOS:-true}}"
PROOF_STATUS_SCENARIO_DIR="${PROOF_STATUS_SCENARIO_DIR:-${PAPER_READINESS_SCENARIO_DIR:-/var/lib/alpaca-bot/nightly/scenarios}}"
PROOF_STATUS_STREAM_START_GRACE_SECONDS="${PROOF_STATUS_STREAM_START_GRACE_SECONDS:-120}"
PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES="${PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES:-${PAPER_READINESS_MAX_PASS_AGE_MINUTES:-180}}"
PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS="${PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS:-${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-900}}"
PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS="${PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS:-${PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS:-6}}"

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
if [[ ! "$PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS" =~ ^[0-9]+$ ]]; then
  echo "PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" >&2
  exit 1
fi
if [[ ! "$PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS" =~ ^[1-9][0-9]*$ ]]; then
  echo "PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS must be a positive integer" >&2
  exit 1
fi
case "${PROOF_STATUS_FAIL_ON_ISSUES,,}" in
  true|false) ;;
  *)
    echo "PROOF_STATUS_FAIL_ON_ISSUES must be true or false" >&2
    exit 1
    ;;
esac
case "${PROOF_STATUS_REQUIRE_SCENARIOS,,}" in
  true|false) ;;
  *)
    echo "PROOF_STATUS_REQUIRE_SCENARIOS must be true or false" >&2
    exit 1
    ;;
esac

export COMPOSE_ANSI="${COMPOSE_ANSI:-never}"
export COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-quiet}"

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)
trading_mode="${TRADING_MODE:-paper}"
scenario_volume_args=()
if [[ "${PROOF_STATUS_REQUIRE_SCENARIOS,,}" == "true" && -d "$PROOF_STATUS_SCENARIO_DIR" ]]; then
  scenario_volume_args=(-v "$PROOF_STATUS_SCENARIO_DIR:$PROOF_STATUS_SCENARIO_DIR:ro")
fi

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
  "${scenario_volume_args[@]}" \
  -e PROOF_STATUS_STRATEGY="$PROOF_STATUS_STRATEGY" \
  -e PROOF_STATUS_MIN_TRADES="$PROOF_STATUS_MIN_TRADES" \
  -e PROOF_STATUS_MIN_PNL="$PROOF_STATUS_MIN_PNL" \
  -e PROOF_STATUS_MIN_WATCHLIST_SYMBOLS="$PROOF_STATUS_MIN_WATCHLIST_SYMBOLS" \
  -e PROOF_STATUS_MIN_CONFIDENCE_FLOOR="$PROOF_STATUS_MIN_CONFIDENCE_FLOOR" \
  -e PROOF_STATUS_REQUIRE_SCENARIOS="$PROOF_STATUS_REQUIRE_SCENARIOS" \
  -e PROOF_STATUS_SCENARIO_DIR="$PROOF_STATUS_SCENARIO_DIR" \
  -e PROOF_STATUS_STREAM_START_GRACE_SECONDS="$PROOF_STATUS_STREAM_START_GRACE_SECONDS" \
  -e PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES="$PROOF_STATUS_READINESS_MAX_PASS_AGE_MINUTES" \
  -e PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS="$PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS" \
  -e PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS="$PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS" \
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

import json
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

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


def parse_bar_date(raw: str) -> date:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw).date()


def parse_int_or_none(raw: str) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def as_hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def format_problem_summary(problems: dict[str, list[str]]) -> str:
    parts = []
    for name, values in problems.items():
        if values:
            examples = ",".join(
                re.sub(r"[^A-Za-z0-9_.:+/-]", "_", value) for value in values[:10]
            )
            parts.append(f"{name}:{len(values)}:{examples}")
    return ";".join(parts) if parts else "none"


def format_trade_pnl_atom(trade: dict, pnl: float) -> str:
    symbol = str(trade.get("symbol") or "unknown")
    exit_time = trade.get("exit_time")
    if isinstance(exit_time, datetime):
        exit_session = exit_time.astimezone(settings.market_timezone).date().isoformat()
    else:
        exit_session = "unknown"
    return f"{symbol}:{pnl:.2f}@{exit_session}"


def load_scenario_coverage(
    *,
    symbols: list[str],
    scenario_dir: Path,
    expected_date: date,
    require_scenarios: bool,
) -> tuple[str, str]:
    if not require_scenarios:
        return "skipped", "disabled"
    if not scenario_dir.is_dir():
        return "missing", f"dir={scenario_dir}"

    problems: dict[str, list[str]] = {
        "missing": [],
        "unreadable": [],
        "empty_daily": [],
        "empty_intraday": [],
        "stale_daily": [],
        "stale_intraday": [],
    }
    for symbol in symbols:
        path = scenario_dir / f"{symbol}_252d.json"
        if not path.exists():
            problems["missing"].append(symbol)
            continue
        try:
            payload = json.loads(path.read_text())
            daily = payload.get("daily_bars") or []
            intraday = payload.get("intraday_bars") or []
            if not daily:
                problems["empty_daily"].append(symbol)
            else:
                daily_max = max(parse_bar_date(str(bar["timestamp"])) for bar in daily)
                if daily_max < expected_date:
                    problems["stale_daily"].append(f"{symbol}:{daily_max.isoformat()}")
            if not intraday:
                problems["empty_intraday"].append(symbol)
            else:
                intraday_max = max(
                    parse_bar_date(str(bar["timestamp"])) for bar in intraday
                )
                if intraday_max < expected_date:
                    problems["stale_intraday"].append(
                        f"{symbol}:{intraday_max.isoformat()}"
                    )
        except Exception as exc:
            problems["unreadable"].append(f"{symbol}:{exc}")

    if any(problems.values()):
        return "failed", format_problem_summary(problems)
    return "ok", "none"


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


def load_previous_market_session_date(
    settings: Settings, *, before_date: date
) -> tuple[date | None, str | None]:
    try:
        calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
            start=before_date - timedelta(days=14),
            end=before_date - timedelta(days=1),
        )
    except Exception as exc:
        return None, str(exc)

    previous = [
        session.session_date
        for session in calendar
        if session.session_date < before_date
    ]
    if not previous:
        return None, f"no market session found before {before_date.isoformat()}"
    return max(previous), None


def load_broker_exposure(
    settings: Settings,
) -> tuple[
    int | None,
    int | None,
    str | None,
    str | None,
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
        return None, None, None, None, None, None, None, None, None, str(exc)
    open_order_symbols = ",".join(
        sorted({getattr(order, "symbol", "") for order in open_orders if getattr(order, "symbol", "")})
    ) or "none"
    open_position_symbols = ",".join(
        sorted({getattr(position, "symbol", "") for position in open_positions if getattr(position, "symbol", "")})
    ) or "none"
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
        open_order_symbols,
        open_position_symbols,
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
min_decision_dry_run_records = int(os.environ["PROOF_STATUS_DECISION_DRY_RUN_MIN_RECORDS"])
min_decision_dry_run_evaluations = int(
    os.environ["PROOF_STATUS_DECISION_DRY_RUN_MIN_EVALUATIONS"]
)
min_confidence_floor = float(os.environ["PROOF_STATUS_MIN_CONFIDENCE_FLOOR"])
require_scenarios = os.environ.get("PROOF_STATUS_REQUIRE_SCENARIOS", "true").lower() == "true"
scenario_dir = Path(os.environ["PROOF_STATUS_SCENARIO_DIR"])
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
    broker_open_order_symbols,
    broker_open_position_symbols,
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
scenario_expected_session = proof_end
if (
    not end_value
    and latest_completed_session is not None
    and latest_completed_session >= current_market_date
):
    previous_session, previous_session_warning = load_previous_market_session_date(
        settings, before_date=current_market_date
    )
    if previous_session is not None:
        scenario_expected_session = previous_session
    elif previous_session_warning:
        calendar_warning = (
            f"{calendar_warning}; {previous_session_warning}"
            if calendar_warning
            else previous_session_warning
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
              )::int AS ignored_symbols,
              COALESCE(
                array_agg(symbol ORDER BY symbol) FILTER (
                  WHERE enabled = TRUE AND COALESCE(ignored, FALSE) = FALSE
                ),
                ARRAY[]::text[]
              ) AS active_symbol_names
            FROM symbol_watchlist
            WHERE trading_mode = %s
            """,
            (trading_mode.value,),
        )
        watchlist_row = cur.fetchone()
        active_watchlist_symbols = int(watchlist_row[0] or 0) if watchlist_row else 0
        enabled_watchlist_symbols = int(watchlist_row[1] or 0) if watchlist_row else 0
        ignored_watchlist_symbols = int(watchlist_row[2] or 0) if watchlist_row else 0
        active_watchlist_symbol_names = list(watchlist_row[3] or []) if watchlist_row else []

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
                'stream_restart_failed',
                'protective_stop_quantity_replace_failed'
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
              created_at,
              COALESCE(payload->>'reason', '') AS reason,
              COALESCE(payload->>'decision_dry_run_strategy', '') AS decision_dry_run_strategy,
              COALESCE(payload->>'decision_dry_run_as_of', '') AS decision_dry_run_as_of,
              COALESCE(payload->>'decision_dry_run_active', '') AS decision_dry_run_active,
              COALESCE(payload->>'decision_dry_run_records', '') AS decision_dry_run_records,
              COALESCE(payload->>'decision_dry_run_accepted', '') AS decision_dry_run_accepted,
              COALESCE(payload->>'decision_dry_run_entry_intents', '') AS decision_dry_run_entry_intents,
              COALESCE(payload->>'decision_dry_run_sample', '') AS decision_dry_run_sample,
              COALESCE(payload->>'decision_dry_run_sample_times', '') AS decision_dry_run_sample_times,
              COALESCE(payload->>'decision_dry_run_evaluations', '') AS decision_dry_run_evaluations,
              COALESCE(payload->>'decision_dry_run_min_decision_records', '') AS decision_dry_run_min_records,
              COALESCE(payload->>'decision_dry_run_max_accepted', '') AS decision_dry_run_max_accepted,
              COALESCE(payload->>'decision_dry_run_max_entry_intents', '') AS decision_dry_run_max_entry_intents,
              COALESCE(payload->>'decision_dry_run_reject_stages', '') AS decision_dry_run_reject_stages,
              COALESCE(payload->>'decision_dry_run_reject_reasons', '') AS decision_dry_run_reject_reasons
            FROM audit_events
            WHERE event_type = 'scheduled_check_completed'
              AND payload->>'trading_mode' = %s
              AND payload->>'strategy_version' = %s
              AND payload->>'check_name' = 'paper_readiness'
              AND payload->>'session_date' = %s
              AND payload->>'proof_start' = %s
            ORDER BY created_at DESC, event_id DESC
            LIMIT 32
            """,
            (
                trading_mode.value,
                strategy_version,
                readiness_target_session.isoformat(),
                proof_start.isoformat(),
            ),
        )
        readiness_audit_rows = cur.fetchall()

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
              ,
              (
                WITH filled AS (
                  SELECT
                    strategy_name,
                    occ_symbol,
                    COALESCE(filled_quantity, quantity) AS fill_qty,
                    side
                  FROM option_orders
                  WHERE trading_mode = %s
                    AND strategy_version = %s
                    AND status = 'filled'
                ),
                net AS (
                  SELECT
                    strategy_name,
                    occ_symbol,
                    SUM(CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END) AS net_qty
                  FROM filled
                  GROUP BY strategy_name, occ_symbol
                  HAVING SUM(CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END) <> 0
                )
                SELECT COUNT(*)::int FROM net
              ) AS open_option_positions,
              (
                SELECT COUNT(*)::int
                FROM option_orders
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
              ) AS active_option_orders
              ,
              (
                SELECT COALESCE(string_agg(DISTINCT symbol, ',' ORDER BY symbol), 'none')
                FROM positions
                WHERE trading_mode = %s
                  AND strategy_version = %s
              ) AS open_position_symbols,
              (
                SELECT COALESCE(string_agg(DISTINCT symbol, ',' ORDER BY symbol), 'none')
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
              ) AS active_order_symbols,
              (
                WITH filled AS (
                  SELECT
                    occ_symbol,
                    COALESCE(filled_quantity, quantity) AS fill_qty,
                    side
                  FROM option_orders
                  WHERE trading_mode = %s
                    AND strategy_version = %s
                    AND status = 'filled'
                ),
                net AS (
                  SELECT
                    occ_symbol,
                    SUM(CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END) AS net_qty
                  FROM filled
                  GROUP BY occ_symbol
                  HAVING SUM(CASE WHEN side = 'buy' THEN fill_qty ELSE -fill_qty END) <> 0
                )
                SELECT COALESCE(string_agg(DISTINCT occ_symbol, ',' ORDER BY occ_symbol), 'none')
                FROM net
              ) AS open_option_symbols,
              (
                SELECT COALESCE(string_agg(DISTINCT occ_symbol, ',' ORDER BY occ_symbol), 'none')
                FROM option_orders
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
              ) AS active_option_order_symbols
            """,
            (
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
                trading_mode.value,
                strategy_version,
            ),
        )
        exposure_row = cur.fetchone()
        local_open_positions = int(exposure_row[0] or 0) if exposure_row else 0
        local_active_orders = int(exposure_row[1] or 0) if exposure_row else 0
        local_open_option_positions = int(exposure_row[2] or 0) if exposure_row else 0
        local_active_option_orders = int(exposure_row[3] or 0) if exposure_row else 0
        local_open_position_symbols = exposure_row[4] if exposure_row else "none"
        local_active_order_symbols = exposure_row[5] if exposure_row else "none"
        local_open_option_symbols = exposure_row[6] if exposure_row else "none"
        local_active_option_order_symbols = exposure_row[7] if exposure_row else "none"

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

scenario_status, scenario_problem_summary = load_scenario_coverage(
    symbols=active_watchlist_symbol_names,
    scenario_dir=scenario_dir,
    expected_date=scenario_expected_session,
    require_scenarios=require_scenarios,
)
trade_pnl_rows = [
    (trade, (trade["exit_fill"] - trade["entry_fill"]) * trade["qty"])
    for trade in trades
]
pnl = sum(trade_pnl for _, trade_pnl in trade_pnl_rows)
trade_count = len(trades)
wins = sum(1 for _, trade_pnl in trade_pnl_rows if trade_pnl > 0)
losses = sum(1 for _, trade_pnl in trade_pnl_rows if trade_pnl < 0)
flats = trade_count - wins - losses
avg_trade_pnl = pnl / trade_count if trade_count else None
win_rate = wins / trade_count * 100 if trade_count else None
best_trade = max(trade_pnl_rows, key=lambda row: row[1]) if trade_pnl_rows else None
worst_trade = min(trade_pnl_rows, key=lambda row: row[1]) if trade_pnl_rows else None
win_rate_text = f"{win_rate:.1f}%" if win_rate is not None else "none"
avg_trade_pnl_text = f"{avg_trade_pnl:.2f}" if avg_trade_pnl is not None else "none"
best_trade_text = (
    format_trade_pnl_atom(best_trade[0], best_trade[1]) if best_trade else "none"
)
worst_trade_text = (
    format_trade_pnl_atom(worst_trade[0], worst_trade[1]) if worst_trade else "none"
)
recent_trade_rows = sorted(
    trade_pnl_rows,
    key=lambda row: row[0].get("exit_time") or datetime.min.replace(tzinfo=timezone.utc),
)[-5:]
recent_trade_summary = (
    ",".join(format_trade_pnl_atom(trade, trade_pnl) for trade, trade_pnl in recent_trade_rows)
    if recent_trade_rows
    else "none"
)
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
    "protective_stop_quantity_replace_failed": "protective_stop_quantity_replace_failed",
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
readiness_audit_row = None
if readiness_audit_rows:
    readiness_audit_row = readiness_audit_rows[0]
    latest_readiness_reason = readiness_audit_row[2] or ""
    if (
        readiness_audit_row[0] != "passed"
        and latest_readiness_reason.startswith("lock_busy")
    ):
        readiness_audit_row = next(
            (row for row in readiness_audit_rows if row[0] == "passed"),
            readiness_audit_row,
        )


def readiness_row_age_minutes(row) -> int | None:
    created_at = row[1]
    if created_at is None:
        return None
    created_utc = created_at
    if created_utc.tzinfo is None:
        created_utc = created_utc.replace(tzinfo=timezone.utc)
    else:
        created_utc = created_utc.astimezone(timezone.utc)
    return max(
        0,
        int((datetime.now(timezone.utc) - created_utc).total_seconds() // 60),
    )


def readiness_row_is_current(row) -> bool:
    created_at = row[1]
    if created_at is None:
        return False
    if (
        latest_supervisor_started_at is not None
        and created_at < latest_supervisor_started_at
    ):
        return False
    age_minutes = readiness_row_age_minutes(row)
    return age_minutes is not None and age_minutes <= readiness_max_pass_age_minutes


def readiness_row_has_decision_dry_run(row) -> bool:
    return (
        len(row) >= 15
        and bool(row[3])
        and bool(row[4])
        and bool(row[5])
        and bool(row[6])
    )


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
readiness_decision_dry_run_strategy = ""
readiness_decision_dry_run_as_of = ""
readiness_decision_dry_run_active = ""
readiness_decision_dry_run_records = ""
readiness_decision_dry_run_accepted = ""
readiness_decision_dry_run_entry_intents = ""
readiness_decision_dry_run_sample = ""
readiness_decision_dry_run_sample_times = ""
readiness_decision_dry_run_evaluations = ""
readiness_decision_dry_run_min_records = ""
readiness_decision_dry_run_max_accepted = ""
readiness_decision_dry_run_max_entry_intents = ""
readiness_decision_dry_run_reject_stages = ""
readiness_decision_dry_run_reject_reasons = ""
readiness_decision_dry_run_row = readiness_audit_row
if not (
    readiness_decision_dry_run_row
    and readiness_row_has_decision_dry_run(readiness_decision_dry_run_row)
):
    readiness_decision_dry_run_row = next(
        (
            row
            for row in readiness_audit_rows
            if row[0] == "passed"
            and readiness_row_has_decision_dry_run(row)
            and readiness_row_is_current(row)
        ),
        readiness_decision_dry_run_row,
    )
if readiness_decision_dry_run_row and len(readiness_decision_dry_run_row) >= 10:
    readiness_decision_dry_run_strategy = readiness_decision_dry_run_row[3] or ""
    readiness_decision_dry_run_as_of = readiness_decision_dry_run_row[4] or ""
    readiness_decision_dry_run_active = readiness_decision_dry_run_row[5] or ""
    readiness_decision_dry_run_records = readiness_decision_dry_run_row[6] or ""
    readiness_decision_dry_run_accepted = readiness_decision_dry_run_row[7] or ""
    readiness_decision_dry_run_entry_intents = readiness_decision_dry_run_row[8] or ""
    readiness_decision_dry_run_sample = readiness_decision_dry_run_row[9] or ""
    if len(readiness_decision_dry_run_row) >= 15:
        readiness_decision_dry_run_sample_times = (
            readiness_decision_dry_run_row[10] or ""
        )
        readiness_decision_dry_run_evaluations = (
            readiness_decision_dry_run_row[11] or ""
        )
        readiness_decision_dry_run_min_records = (
            readiness_decision_dry_run_row[12] or ""
        )
        readiness_decision_dry_run_max_accepted = (
            readiness_decision_dry_run_row[13] or ""
        )
        readiness_decision_dry_run_max_entry_intents = (
            readiness_decision_dry_run_row[14] or ""
        )
    if len(readiness_decision_dry_run_row) >= 17:
        readiness_decision_dry_run_reject_stages = (
            readiness_decision_dry_run_row[15] or ""
        )
        readiness_decision_dry_run_reject_reasons = (
            readiness_decision_dry_run_row[16] or ""
        )
readiness_decision_dry_run_active_value = parse_int_or_none(
    readiness_decision_dry_run_active
)
readiness_decision_dry_run_records_value = parse_int_or_none(
    readiness_decision_dry_run_records
)
readiness_decision_dry_run_accepted_value = parse_int_or_none(
    readiness_decision_dry_run_accepted
)
readiness_decision_dry_run_entry_intents_value = parse_int_or_none(
    readiness_decision_dry_run_entry_intents
)
readiness_decision_dry_run_min_records_value = parse_int_or_none(
    readiness_decision_dry_run_min_records
)
readiness_decision_dry_run_evaluations_value = parse_int_or_none(
    readiness_decision_dry_run_evaluations
)
readiness_decision_dry_run_max_accepted_value = parse_int_or_none(
    readiness_decision_dry_run_max_accepted
)
readiness_decision_dry_run_max_entry_intents_value = parse_int_or_none(
    readiness_decision_dry_run_max_entry_intents
)
readiness_decision_dry_run_status = "ok"
if not (
    readiness_decision_dry_run_strategy
    and readiness_decision_dry_run_as_of
    and readiness_decision_dry_run_active
    and readiness_decision_dry_run_records
    and readiness_decision_dry_run_accepted
    and readiness_decision_dry_run_entry_intents
):
    readiness_decision_dry_run_status = "missing"
elif readiness_decision_dry_run_strategy != strategy_name:
    readiness_decision_dry_run_status = "strategy_mismatch"
elif (
    readiness_decision_dry_run_active_value is None
    or readiness_decision_dry_run_records_value is None
    or readiness_decision_dry_run_accepted_value is None
    or readiness_decision_dry_run_entry_intents_value is None
):
    readiness_decision_dry_run_status = "invalid"
elif readiness_decision_dry_run_active_value < min_watchlist_symbols:
    readiness_decision_dry_run_status = "active_under_minimum"
elif readiness_decision_dry_run_records_value < min_decision_dry_run_records:
    readiness_decision_dry_run_status = "records_under_minimum"
elif (
    readiness_decision_dry_run_evaluations_value is None
    or readiness_decision_dry_run_evaluations_value < min_decision_dry_run_evaluations
):
    readiness_decision_dry_run_status = "evaluations_under_minimum"
elif (
    readiness_decision_dry_run_min_records_value is not None
    and readiness_decision_dry_run_min_records_value < min_decision_dry_run_records
):
    readiness_decision_dry_run_status = "sample_records_under_minimum"
elif (
    max(
        readiness_decision_dry_run_accepted_value,
        readiness_decision_dry_run_max_accepted_value or 0,
    )
    <= 0
):
    readiness_decision_dry_run_status = "accepted_under_minimum"
elif (
    max(
        readiness_decision_dry_run_entry_intents_value,
        readiness_decision_dry_run_max_entry_intents_value or 0,
    )
    <= 0
):
    readiness_decision_dry_run_status = "entry_intents_under_minimum"
activity_due = False
activity_due_after = "none"
activity_required_since = None
activity_required_since_text = "none"
activity_check_status = "missing"
activity_check_exit_code = "unknown"
activity_check_created_text = "none"
activity_audit_status = "not_started"
if activity_target_session is not None:
    activity_first_due_time = time(10, 35)
    activity_first_check_time = time(10, 25)
    activity_late_check_time = time(14, 35)
    activity_late_due_time = time(14, 45)
    if current_market_datetime.date() > activity_target_session or (
        current_market_datetime.date() == activity_target_session
        and current_market_datetime.time() >= activity_late_due_time
    ):
        activity_due_time = activity_late_due_time
        activity_required_since_time = activity_late_check_time
    else:
        activity_due_time = activity_first_due_time
        activity_required_since_time = activity_first_check_time
    activity_required_since = datetime.combine(
        activity_target_session,
        activity_required_since_time,
        settings.market_timezone,
    ).astimezone(timezone.utc)
    activity_required_since_text = activity_required_since.isoformat()
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
            if (
                activity_due
                and activity_required_since is not None
                and activity_created_at is not None
                and activity_created_at < activity_required_since
            ):
                activity_audit_status = "stale"
            else:
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
    proof_status = "pending"
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
        settings.market_data_feed.value == "iex"
        and int(settings.daily_sma_period) == 20
        and int(settings.breakout_lookback_bars) == 20
        and int(settings.relative_volume_lookback_bars) == 20
        and abs(float(settings.relative_volume_threshold) - 2.0) < 1e-9
        and int(settings.entry_timeframe_minutes) == 15
        and abs(float(settings.risk_per_trade_pct) - 0.01) < 1e-9
        and abs(float(settings.max_position_pct) - 0.05) < 1e-9
        and int(settings.max_open_positions) == 3
        and abs(float(settings.max_portfolio_exposure_pct) - 0.30) < 1e-9
        and abs(float(settings.daily_loss_limit_pct) - 0.01) < 1e-9
        and int(settings.atr_period) == 14
        and abs(float(settings.atr_stop_multiplier) - 1.0) < 1e-9
        and abs(float(settings.trailing_stop_atr_multiplier) - 1.5) < 1e-9
        and abs(float(settings.trailing_stop_profit_trigger_r) - 1.0) < 1e-9
        and abs(float(settings.bull_flag_min_run_pct) - 0.02) < 1e-9
        and abs(float(settings.bull_flag_consolidation_volume_ratio) - 0.6) < 1e-9
        and abs(float(settings.bull_flag_consolidation_range_pct) - 0.5) < 1e-9
        and as_hhmm(settings.entry_window_start) == "10:00"
        and as_hhmm(settings.entry_window_end) == "15:30"
        and as_hhmm(settings.flatten_time) == "15:45"
        and bool(settings.enable_vwap_entry_filter)
        and bool(settings.enable_profit_trail)
        and abs(float(settings.profit_trail_pct) - 0.95) < 1e-9
        and bool(settings.enable_breakeven_stop)
        and abs(float(settings.breakeven_trigger_pct) - 0.0025) < 1e-9
        and abs(float(settings.breakeven_trail_pct) - 0.002) < 1e-9
        and not bool(settings.enable_vix_filter)
        and not bool(settings.enable_sector_filter)
        and not bool(settings.enable_regime_filter)
        and not bool(settings.enable_news_filter)
        and not bool(settings.enable_spread_filter)
        and not bool(settings.enable_options_trading)
        and not bool(settings.option_chain_symbols)
        and not bool(settings.extended_hours_enabled)
        and not bool(settings.enable_profit_target)
        and not bool(settings.enable_trend_filter_exit)
        and not bool(settings.enable_vwap_breakdown_exit)
        and abs(float(settings.per_symbol_loss_limit_pct) - 0.0) < 1e-9
        and abs(float(settings.min_position_notional) - 0.0) < 1e-9
        and abs(float(settings.max_stop_pct) - 0.05) < 1e-9
        and int(settings.viability_daily_bar_max_age_days) == 5
        and int(settings.viability_min_hold_minutes) == 0
        and settings.max_loss_per_trade_dollars is None
        and bool(settings.paper_proof_freeze)
        and int(settings.intraday_consecutive_loss_gate) == 0
        and abs(float(settings.replay_slippage_bps) - 2.0) < 1e-9
    )
    else "drifted"
)
blockers = []
if strategy_status != "ok":
    blockers.append("strategy_disabled")
if watchlist_status != "ok":
    blockers.append("watchlist_under_minimum")
if scenario_status not in {"ok", "skipped"}:
    blockers.append(f"scenario_evidence_{scenario_status}")
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
elif readiness_decision_dry_run_status != "ok":
    blockers.append(f"readiness_decision_dry_run_{readiness_decision_dry_run_status}")
if activity_audit_status in {"missing", "failed", "skipped", "stale"} or (
    activity_due and activity_audit_status == "pending"
):
    blockers.append(f"activity_audit_{activity_audit_status}")
if post_close_audit_status in {"missing", "failed"}:
    blockers.append(f"post_close_audit_{post_close_audit_status}")
if local_open_positions > 0:
    blockers.append("local_open_positions")
if local_active_orders > 0:
    blockers.append("local_active_orders")
if local_open_option_positions > 0:
    blockers.append("local_open_option_positions")
if local_active_option_orders > 0:
    blockers.append("local_active_option_orders")
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
if not proof_not_started and 0 < trade_count:
    if trade_count < min_trades:
        if pnl < 0:
            warnings.append("partial_pnl_negative")
        elif pnl < min_pnl:
            warnings.append("partial_pnl_below_minimum")
    elif pnl < 0:
        warnings.append("cumulative_pnl_negative")
    elif pnl < min_pnl:
        warnings.append("cumulative_pnl_below_minimum")

readiness_status = "blocked" if blockers else "ready"
if proof_status == "passed":
    proof_reason = "profit_proven"
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
    "paper proof readiness decision dry run: "
    f"status={readiness_decision_dry_run_status} "
    f"strategy={readiness_decision_dry_run_strategy or 'none'} "
    f"as_of={readiness_decision_dry_run_as_of or 'none'} "
    f"active={readiness_decision_dry_run_active or 'none'} "
    f"required_active={min_watchlist_symbols} "
    f"decision_records={readiness_decision_dry_run_records or 'none'} "
    f"required_records={min_decision_dry_run_records} "
    f"accepted={readiness_decision_dry_run_accepted or 'none'} "
    f"entry_intents={readiness_decision_dry_run_entry_intents or 'none'} "
    f"sample={readiness_decision_dry_run_sample or 'none'} "
    f"sample_times={readiness_decision_dry_run_sample_times or 'none'} "
    f"evaluations={readiness_decision_dry_run_evaluations or 'none'} "
    f"required_evaluations={min_decision_dry_run_evaluations} "
    f"min_decision_records={readiness_decision_dry_run_min_records or 'none'} "
    f"max_accepted={readiness_decision_dry_run_max_accepted or 'none'} "
    f"max_entry_intents={readiness_decision_dry_run_max_entry_intents or 'none'} "
    f"reject_stages={readiness_decision_dry_run_reject_stages or 'none'} "
    f"reject_reasons={readiness_decision_dry_run_reject_reasons or 'none'}"
)
print(
    "paper proof activity audit: "
    f"status={activity_audit_status} "
    f"target_session={activity_target_session.isoformat() if activity_target_session else 'none'} "
    f"due={str(activity_due).lower()} "
    f"due_after={activity_due_after} "
    f"required_since={activity_required_since_text} "
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
    "paper proof scenarios: "
    f"status={scenario_status} "
    f"active={len(active_watchlist_symbol_names)} "
    f"expected_session={scenario_expected_session.isoformat()} "
    f"dir={scenario_dir} "
    f"problems={scenario_problem_summary}"
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
    f"market_data_feed={settings.market_data_feed.value} "
    f"daily_sma_period={settings.daily_sma_period} "
    f"breakout_lookback_bars={settings.breakout_lookback_bars} "
    f"relative_volume_lookback_bars={settings.relative_volume_lookback_bars} "
    f"relative_volume_threshold={settings.relative_volume_threshold:g} "
    f"entry_timeframe_minutes={settings.entry_timeframe_minutes} "
    f"risk_per_trade_pct={settings.risk_per_trade_pct:g} "
    f"max_position_pct={settings.max_position_pct:g} "
    f"max_open_positions={settings.max_open_positions} "
    f"max_portfolio_exposure_pct={settings.max_portfolio_exposure_pct:g} "
    f"daily_loss_limit_pct={settings.daily_loss_limit_pct:g} "
    f"atr_period={settings.atr_period} "
    f"atr_stop_multiplier={settings.atr_stop_multiplier:g} "
    f"trailing_stop_atr_multiplier={settings.trailing_stop_atr_multiplier:g} "
    f"trailing_stop_profit_trigger_r={settings.trailing_stop_profit_trigger_r:g} "
    f"bull_flag_min_run_pct={settings.bull_flag_min_run_pct:g} "
    f"bull_flag_consolidation_volume_ratio={settings.bull_flag_consolidation_volume_ratio:g} "
    f"bull_flag_consolidation_range_pct={settings.bull_flag_consolidation_range_pct:g} "
    f"entry_window_start={as_hhmm(settings.entry_window_start)} "
    f"entry_window_end={as_hhmm(settings.entry_window_end)} "
    f"flatten_time={as_hhmm(settings.flatten_time)} "
    f"vwap_filter={str(settings.enable_vwap_entry_filter).lower()} "
    f"profit_trail={str(settings.enable_profit_trail).lower()} "
    f"profit_trail_pct={settings.profit_trail_pct:g} "
    f"breakeven_stop={str(settings.enable_breakeven_stop).lower()} "
    f"breakeven_trigger_pct={settings.breakeven_trigger_pct:g} "
    f"breakeven_trail_pct={settings.breakeven_trail_pct:g} "
    f"vix_filter={str(settings.enable_vix_filter).lower()} "
    f"sector_filter={str(settings.enable_sector_filter).lower()} "
    f"regime_filter={str(settings.enable_regime_filter).lower()} "
    f"news_filter={str(settings.enable_news_filter).lower()} "
    f"spread_filter={str(settings.enable_spread_filter).lower()} "
    f"options_trading={str(settings.enable_options_trading).lower()} "
    f"option_chain_symbols={','.join(settings.option_chain_symbols) if settings.option_chain_symbols else 'none'} "
    f"extended_hours={str(settings.extended_hours_enabled).lower()} "
    f"profit_target={str(settings.enable_profit_target).lower()} "
    f"trend_filter_exit={str(settings.enable_trend_filter_exit).lower()} "
    f"vwap_breakdown_exit={str(settings.enable_vwap_breakdown_exit).lower()} "
    f"per_symbol_loss_limit_pct={settings.per_symbol_loss_limit_pct:g} "
    f"min_position_notional={settings.min_position_notional:g} "
    f"max_stop_pct={settings.max_stop_pct:g} "
    f"viability_daily_bar_max_age_days={settings.viability_daily_bar_max_age_days} "
    f"viability_min_hold_minutes={settings.viability_min_hold_minutes} "
    f"max_loss_per_trade_dollars={settings.max_loss_per_trade_dollars if settings.max_loss_per_trade_dollars is not None else 'none'} "
    f"paper_proof_freeze={str(settings.paper_proof_freeze).lower()} "
    f"intraday_consecutive_loss_gate={settings.intraday_consecutive_loss_gate} "
    f"replay_slippage_bps={settings.replay_slippage_bps:g}"
)
print(
    "paper proof local exposure: "
    f"positions={local_open_positions} "
    f"active_orders={local_active_orders} "
    f"position_symbols={local_open_position_symbols or 'none'} "
    f"active_order_symbols={local_active_order_symbols or 'none'}"
)
print(
    "paper proof option exposure: "
    f"net_open={local_open_option_positions} "
    f"active_orders={local_active_option_orders} "
    f"net_open_symbols={local_open_option_symbols or 'none'} "
    f"active_order_symbols={local_active_option_order_symbols or 'none'}"
)
if broker_exposure_warning:
    print(f"paper proof broker exposure warning: {broker_exposure_warning}")
else:
    print(
        "paper proof broker exposure: "
        f"open_orders={broker_open_orders} "
        f"open_positions={broker_open_positions} "
        f"open_order_symbols={broker_open_order_symbols or 'none'} "
        f"open_position_symbols={broker_open_position_symbols or 'none'}"
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
print(
    "paper proof trade quality: "
    f"wins={wins} "
    f"losses={losses} "
    f"flats={flats} "
    f"win_rate={win_rate_text} "
    f"avg_pnl={avg_trade_pnl_text} "
    f"best={best_trade_text} "
    f"worst={worst_trade_text} "
    f"recent={recent_trade_summary}"
)
if fail_on_issues and (readiness_status != "ready" or blockers):
    raise SystemExit(1)
if fail_on_issues and proof_status == "pending":
    raise SystemExit(43)
PY
