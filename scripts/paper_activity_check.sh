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
  PAPER_ACTIVITY_WINDOW_MINUTES \
  PAPER_ACTIVITY_MIN_DECISION_RECORDS \
  PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES \
  PAPER_ACTIVITY_REQUIRE_DECISION_LOG \
  PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT \
  PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE \
  PAPER_ACTIVITY_READINESS_RUNNER \
  PAPER_ACTIVITY_READINESS_SCRIPT \
  PAPER_ACTIVITY_STRATEGY

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

PAPER_ACTIVITY_WINDOW_MINUTES="${PAPER_ACTIVITY_WINDOW_MINUTES:-90}"
PAPER_ACTIVITY_MIN_DECISION_RECORDS="${PAPER_ACTIVITY_MIN_DECISION_RECORDS:-900}"
PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES="${PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES:-5}"
PAPER_ACTIVITY_REQUIRE_DECISION_LOG="${PAPER_ACTIVITY_REQUIRE_DECISION_LOG:-true}"
PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT="${PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT:-true}"
PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE="${PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE:-true}"
PAPER_ACTIVITY_READINESS_RUNNER="${PAPER_ACTIVITY_READINESS_RUNNER:-./scripts/run_locked_check_with_audit.sh}"
PAPER_ACTIVITY_READINESS_SCRIPT="${PAPER_ACTIVITY_READINESS_SCRIPT:-./scripts/paper_readiness_if_needed.sh}"
PAPER_ACTIVITY_STRATEGY="${PAPER_ACTIVITY_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "paper activity check skipped for TRADING_MODE=${TRADING_MODE:-unset}"
  exit 0
fi

case "${PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE,,}" in
  true|false) ;;
  *)
    echo "PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE must be true or false" >&2
    exit 1
    ;;
esac

if [[ ! "$PAPER_ACTIVITY_STRATEGY" =~ ^[A-Za-z0-9_:-]+$ ]]; then
  echo "PAPER_ACTIVITY_STRATEGY contains unsupported characters" >&2
  exit 1
fi

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

emit_scheduled_context() {
  echo "scheduled check context: session_date=$(TZ=America/New_York date +%F) proof_start=${PROFIT_PROBE_START_DATE:-2026-06-29} strategy=$PAPER_ACTIVITY_STRATEGY"
}

only_readiness_missing_reasons() {
  local reasons="${1:-}"
  if [[ -z "$reasons" ]]; then
    return 1
  fi
  local reason
  IFS=',' read -ra _paper_activity_reasons <<< "$reasons"
  for reason in "${_paper_activity_reasons[@]}"; do
    if [[ "$reason" != "paper_readiness_check_missing" ]]; then
      return 1
    fi
  done
  return 0
}

only_runtime_reconciliation_reasons() {
  local reasons="${1:-}"
  if [[ -z "$reasons" ]]; then
    return 1
  fi
  local reason
  IFS=',' read -ra _paper_activity_reasons <<< "$reasons"
  for reason in "${_paper_activity_reasons[@]}"; do
    if [[ "$reason" != "runtime_reconciliation_mismatch" ]]; then
      return 1
    fi
  done
  return 0
}

only_profit_lock_pause_reasons() {
  local reasons="${1:-}"
  if [[ -z "$reasons" ]]; then
    return 1
  fi

  local reason has_close_only=false
  IFS=',' read -ra _paper_activity_reasons <<< "$reasons"
  for reason in "${_paper_activity_reasons[@]}"; do
    case "$reason" in
      trading_status:close_only)
        has_close_only=true
        ;;
      runtime_reconciliation_mismatch)
        ;;
      *)
        return 1
        ;;
    esac
  done
  [[ "$has_close_only" == "true" ]]
}

only_strategy_session_state_reasons() {
  local reasons="${1:-}"
  if [[ -z "$reasons" ]]; then
    return 1
  fi
  local reason
  IFS=',' read -ra _paper_activity_reasons <<< "$reasons"
  for reason in "${_paper_activity_reasons[@]}"; do
    if [[ "$reason" != "strategy_session_state_entries_disabled" ]]; then
      return 1
    fi
  done
  return 0
}

is_after_configured_flatten_time() {
  local now_hm flatten_hm now_hour now_min flatten_hour flatten_min
  now_hm="$(TZ=America/New_York date +%H:%M)"
  flatten_hm="${FLATTEN_TIME:-15:45}"
  if [[ ! "$now_hm" =~ ^[0-9]{1,2}:[0-9]{2}$ ]] \
    || [[ ! "$flatten_hm" =~ ^[0-9]{1,2}:[0-9]{2}$ ]]; then
    return 1
  fi
  now_hour="${now_hm%:*}"
  now_min="${now_hm#*:}"
  flatten_hour="${flatten_hm%:*}"
  flatten_min="${flatten_hm#*:}"
  (( 10#$now_hour * 60 + 10#$now_min >= 10#$flatten_hour * 60 + 10#$flatten_min ))
}

load_trading_status_line() {
  "${compose[@]}" run -T --rm admin \
    status \
    --mode paper \
    --strategy-version "${STRATEGY_VERSION:-v1-breakout}"
}

profit_lock_flat_pause_active() {
  local reasons="${1:-}"
  only_profit_lock_pause_reasons "$reasons" || return 1
  [[ "${has_stock_exposure:-false}" != "true" ]] || return 1

  local status_line
  status_line="$(load_trading_status_line 2>/dev/null)" || return 1
  [[ "$status_line" == *"status=close_only"* ]] || return 1
  [[ "$status_line" == *"kill_switch=false"* ]] || return 1
  [[ "$status_line" == *"reason=paper profit lock"* ]] || return 1

  BROKER_FLAT_CONTEXT="paper activity profit lock" \
    ./scripts/broker_flat_check.sh "$ENV_FILE" >/dev/null
}

close_only_on_activity_failure() {
  local rc="$?"
  trap - EXIT

  if [[ "$rc" -eq 0 ]]; then
    exit 0
  fi
  if [[ "$rc" -eq 43 ]]; then
    exit 43
  fi

  emit_scheduled_context

  if [[ "${PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE,,}" != "true" ]]; then
    exit "$rc"
  fi

  local session_date
  session_date="$(TZ=America/New_York date +%F)"
  local reason="paper activity failed for session ${session_date}: post-open checks failed for strategy ${PAPER_ACTIVITY_STRATEGY:-unknown}"
  if ! "${compose[@]}" run -T --rm admin \
    close-only \
    --mode paper \
    --strategy-version "${STRATEGY_VERSION:-v1-breakout}" \
    --reason "$reason"; then
    echo "paper activity warning: failed to apply close-only after activity failure" >&2
  fi

  exit "$rc"
}

trap close_only_on_activity_failure EXIT

if [[ ! "$PAPER_ACTIVITY_WINDOW_MINUTES" =~ ^[0-9]+$ ]] \
  || [[ "$PAPER_ACTIVITY_WINDOW_MINUTES" -lt 1 ]]; then
  echo "PAPER_ACTIVITY_WINDOW_MINUTES must be a positive integer" >&2
  exit 1
fi

if [[ ! "$PAPER_ACTIVITY_MIN_DECISION_RECORDS" =~ ^[0-9]+$ ]]; then
  echo "PAPER_ACTIVITY_MIN_DECISION_RECORDS must be a non-negative integer" >&2
  exit 1
fi

if [[ ! "$PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES" =~ ^[0-9]+$ ]] \
  || [[ "$PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES" -lt 1 ]]; then
  echo "PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES must be a positive integer" >&2
  exit 1
fi

case "${PAPER_ACTIVITY_REQUIRE_DECISION_LOG,,}" in
  true|false) ;;
  *)
    echo "PAPER_ACTIVITY_REQUIRE_DECISION_LOG must be true or false" >&2
    exit 1
    ;;
esac

case "${PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT,,}" in
  true|false) ;;
  *)
    echo "PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT must be true or false" >&2
    exit 1
    ;;
esac

set +e
PAPER_READINESS_AUTO_RESUME=false \
  PAPER_READINESS_AUTO_RESET_WEIGHTS=false \
  PAPER_READINESS_CLOSE_ONLY_ON_FAILURE="$PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE" \
  PAPER_READINESS_REQUIRE_FLAT=false \
  "$PAPER_ACTIVITY_READINESS_RUNNER" \
  paper_readiness \
  /var/lock/alpaca-bot-paper-readiness.lock \
  "$ENV_FILE" \
  "$PAPER_ACTIVITY_READINESS_SCRIPT" \
  "$ENV_FILE"
readiness_rc="$?"
set -e
if [[ "$readiness_rc" -eq 48 ]]; then
  emit_scheduled_context
  echo "paper activity pending: readiness repair lock busy; waiting for audited readiness"
  exit 43
fi
if [[ "$readiness_rc" -ne 0 ]]; then
  exit "$readiness_rc"
fi

emit_scheduled_context

load_market_clock_status() {
  "${compose[@]}" run -T --rm \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter

try:
    settings = Settings.from_env()
    clock = AlpacaExecutionAdapter.from_settings(settings).get_market_clock()
except Exception as exc:
    print(f"unknown|{exc}")
else:
    status = "open" if clock.is_open else "closed"
    print(
        f"{status}|timestamp={clock.timestamp.isoformat()} "
        f"next_open={clock.next_open.isoformat()} "
        f"next_close={clock.next_close.isoformat()}"
    )
PY
}

load_broker_activity_status() {
  "${compose[@]}" run -T --rm \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter

settings = Settings.from_env()
broker = AlpacaExecutionAdapter.from_settings(settings)
account = broker.get_account()
open_orders = broker.list_open_orders()
open_positions = broker.list_positions()
equity = float(account.equity)
buying_power = float(account.buying_power)
minimum_buying_power = equity * float(settings.max_position_pct)
trading_blocked = bool(account.trading_blocked)
account_status = (
    "blocked"
    if trading_blocked or equity <= 0 or buying_power < minimum_buying_power
    else "ok"
)
open_order_symbols = ",".join(
    sorted({getattr(order, "symbol", "") for order in open_orders if getattr(order, "symbol", "")})
) or "none"
open_position_symbols = ",".join(
    sorted({getattr(position, "symbol", "") for position in open_positions if getattr(position, "symbol", "")})
) or "none"
print(
    f"{account_status}|{equity:.2f}|{buying_power:.2f}|{minimum_buying_power:.2f}|"
    f"{str(trading_blocked).lower()}|{len(open_orders)}|{len(open_positions)}|"
    f"{open_order_symbols}|{open_position_symbols}"
)
PY
}

stats="$("${compose[@]}" exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -tA -F '|' \
  -v trading_mode="${TRADING_MODE:-paper}" \
  -v strategy_version="$STRATEGY_VERSION" \
  -v paper_activity_strategy="$PAPER_ACTIVITY_STRATEGY" <<SQL
WITH recent AS (
  SELECT event_type, payload, created_at
  FROM audit_events
  WHERE created_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
    AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = :'trading_mode')
    AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = :'strategy_version')
),
recent_decisions AS (
  SELECT cycle_at, symbol, strategy_name, decision, reject_stage, reject_reason
  FROM decision_log
  WHERE cycle_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
    AND trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
),
enabled_strategies AS (
  SELECT COALESCE(array_agg(strategy_name ORDER BY strategy_name), ARRAY[]::text[]) AS names
  FROM strategy_flags
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
    AND enabled = TRUE
),
latest_supervisor AS (
  SELECT payload, created_at
  FROM recent
  WHERE event_type = 'supervisor_cycle'
  ORDER BY created_at DESC
  LIMIT 1
),
latest_supervisor_activity AS (
  SELECT event_type, payload, created_at
  FROM recent
  WHERE event_type IN ('supervisor_cycle', 'supervisor_idle')
  ORDER BY created_at DESC
  LIMIT 1
),
latest_supervisor_started AS (
  SELECT MAX(created_at) AS created_at
  FROM recent
  WHERE event_type = 'supervisor_started'
),
strategy_positions AS (
  SELECT DISTINCT symbol
  FROM positions
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
    AND strategy_name IS NOT DISTINCT FROM :'paper_activity_strategy'
),
recent_entry_order_rows AS (
  SELECT symbol, status
  FROM orders
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
    AND strategy_name IS NOT DISTINCT FROM :'paper_activity_strategy'
    AND intent_type = 'entry'
    AND (
      created_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
      OR updated_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
    )
),
active_strategy_orders AS (
  SELECT DISTINCT symbol
  FROM orders
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
    AND strategy_name IS NOT DISTINCT FROM :'paper_activity_strategy'
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
),
active_entry_orders AS (
  SELECT DISTINCT symbol
  FROM orders
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
    AND strategy_name IS NOT DISTINCT FROM :'paper_activity_strategy'
    AND intent_type = 'entry'
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
),
accepted_symbols AS (
  SELECT DISTINCT symbol
  FROM recent_decisions
  WHERE strategy_name = :'paper_activity_strategy'
    AND decision = 'accepted'
),
materialized_entry_symbols AS (
  SELECT symbol FROM recent_entry_order_rows
  UNION
  SELECT symbol FROM active_entry_orders
  UNION
  SELECT symbol FROM strategy_positions
),
unmaterialized_accepted_symbols AS (
  SELECT symbol FROM accepted_symbols
  EXCEPT
  SELECT symbol FROM materialized_entry_symbols
)
SELECT
  COUNT(*) FILTER (WHERE event_type = 'supervisor_cycle')::int,
  COUNT(*) FILTER (
    WHERE event_type = 'supervisor_cycle'
      AND (payload->>'entries_disabled')::boolean IS TRUE
  )::int,
  COUNT(*) FILTER (WHERE event_type = 'decision_cycle_completed')::int,
  COALESCE(SUM(
    CASE
      WHEN event_type = 'decision_cycle_completed'
      THEN COALESCE((payload->>'decision_record_count')::int, 0)
      ELSE 0
    END
  ), 0)::int,
  COUNT(*) FILTER (
    WHERE event_type = 'supervisor_idle'
      AND payload->>'reason' = 'market_closed'
  )::int,
  COALESCE(MAX(created_at) FILTER (WHERE event_type = 'supervisor_cycle')::text, ''),
  COALESCE((
    SELECT CASE
      WHEN (payload->>'entries_disabled')::boolean IS TRUE THEN 'true'
      ELSE 'false'
    END
    FROM latest_supervisor
  ), 'false'),
  COALESCE((
    SELECT array_to_string(ARRAY(
      SELECT jsonb_array_elements_text(
        COALESCE(payload->'entries_disabled_reasons', '[]'::jsonb)
      )
    ), ',')
    FROM latest_supervisor
  ), ''),
  COALESCE((
    SELECT CASE
      WHEN COALESCE(payload->'blocked_strategy_names', '[]'::jsonb) ? :'paper_activity_strategy'
      THEN 'true'
      ELSE 'false'
    END
    FROM latest_supervisor
  ), 'false'),
  COALESCE((
    SELECT array_to_string(ARRAY(
      SELECT jsonb_array_elements_text(
        COALESCE(
          payload->'strategy_entries_disabled_reasons'->'${PAPER_ACTIVITY_STRATEGY}',
          '[]'::jsonb
        )
      )
    ), ',')
    FROM latest_supervisor
  ), ''),
  COALESCE(MAX(created_at) FILTER (WHERE event_type = 'decision_cycle_completed')::text, ''),
  COUNT(*) FILTER (
    WHERE event_type = 'supervisor_cycle'
      AND COALESCE(payload->'blocked_strategy_names', '[]'::jsonb) ? :'paper_activity_strategy'
  )::int,
  COUNT(*) FILTER (
    WHERE event_type = 'decision_cycle_completed'
      AND payload->>'strategy_name' = :'paper_activity_strategy'
  )::int,
  COALESCE(SUM(
    CASE
      WHEN event_type = 'decision_cycle_completed'
       AND payload->>'strategy_name' = :'paper_activity_strategy'
      THEN COALESCE((payload->>'decision_record_count')::int, 0)
      ELSE 0
    END
  ), 0)::int,
  COUNT(*) FILTER (
    WHERE event_type = 'decision_cycle_completed'
      AND NOT (payload ? 'strategy_name')
  )::int,
  COALESCE((
    SELECT COUNT(DISTINCT cycle_at)::int
    FROM recent_decisions
    WHERE strategy_name = :'paper_activity_strategy'
  ), 0)::int,
  COALESCE((
    SELECT COUNT(*)::int
    FROM recent_decisions
    WHERE strategy_name = :'paper_activity_strategy'
  ), 0)::int,
  COALESCE((
    SELECT MAX(cycle_at)::text
    FROM recent_decisions
    WHERE strategy_name = :'paper_activity_strategy'
  ), ''),
  COALESCE((
    SELECT string_agg(
      decision_key || ':' || decision_count::text,
      ',' ORDER BY decision_count DESC, decision_key
    )
    FROM (
      SELECT
        decision || '/' || COALESCE(reject_stage, 'none') || '/' || COALESCE(reject_reason, 'none') AS decision_key,
        COUNT(*)::int AS decision_count
      FROM recent_decisions
      WHERE strategy_name = :'paper_activity_strategy'
      GROUP BY decision_key
    ) decision_counts
  ), ''),
  COALESCE((
    SELECT COUNT(*)::int
    FROM recent_decisions
    WHERE strategy_name = :'paper_activity_strategy'
      AND decision = 'accepted'
  ), 0)::int,
  COALESCE((
    SELECT MAX(cycle_at)::text
    FROM recent_decisions
    WHERE strategy_name = :'paper_activity_strategy'
      AND decision = 'accepted'
  ), ''),
  COALESCE((
    SELECT COUNT(*)::int
    FROM recent_entry_order_rows
  ), 0)::int,
  COALESCE((
    SELECT string_agg(status || ':' || status_count::text, ',' ORDER BY status)
    FROM (
      SELECT status, COUNT(*)::int AS status_count
      FROM recent_entry_order_rows
      GROUP BY status
    ) entry_status_counts
  ), ''),
  COALESCE((SELECT COUNT(*)::int FROM accepted_symbols), 0)::int,
  COALESCE((SELECT string_agg(symbol, ',' ORDER BY symbol) FROM accepted_symbols), ''),
  COALESCE((SELECT COUNT(*)::int FROM materialized_entry_symbols), 0)::int,
  COALESCE((SELECT string_agg(symbol, ',' ORDER BY symbol) FROM materialized_entry_symbols), ''),
  COALESCE((SELECT COUNT(*)::int FROM unmaterialized_accepted_symbols), 0)::int,
  COALESCE((SELECT string_agg(symbol, ',' ORDER BY symbol) FROM unmaterialized_accepted_symbols), ''),
  COALESCE((
    SELECT COUNT(*)::int
    FROM orders
    WHERE trading_mode = 'paper'
      AND strategy_version = :'strategy_version'
      AND strategy_name IS NOT DISTINCT FROM :'paper_activity_strategy'
      AND intent_type = 'entry'
      AND status = 'pending_submit'
      AND broker_order_id IS NULL
      AND created_at <= NOW() - (${PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES} * interval '1 minute')
  ), 0)::int,
  COALESCE((
    SELECT string_agg(
      symbol || ':' || to_char(created_at AT TIME ZONE 'UTC', 'HH24:MI:SS'),
      ',' ORDER BY created_at, symbol
    )
    FROM orders
    WHERE trading_mode = 'paper'
      AND strategy_version = :'strategy_version'
      AND strategy_name IS NOT DISTINCT FROM :'paper_activity_strategy'
      AND intent_type = 'entry'
      AND status = 'pending_submit'
      AND broker_order_id IS NULL
      AND created_at <= NOW() - (${PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES} * interval '1 minute')
  ), ''),
  array_to_string((SELECT names FROM enabled_strategies), ','),
  COALESCE((
    SELECT string_agg(reason || ':' || reason_count::text, ',' ORDER BY reason)
    FROM (
      SELECT reason, COUNT(*)::int AS reason_count
      FROM recent r
      CROSS JOIN LATERAL jsonb_array_elements_text(
        COALESCE(r.payload->'entries_disabled_reasons', '[]'::jsonb)
      ) AS reason
      WHERE r.event_type = 'supervisor_cycle'
        AND (r.payload->>'entries_disabled')::boolean IS TRUE
      GROUP BY reason
    ) reason_counts
  ), ''),
  COALESCE((
    SELECT string_agg(reason || ':' || reason_count::text, ',' ORDER BY reason)
    FROM (
      SELECT reason, COUNT(*)::int AS reason_count
      FROM recent r
      CROSS JOIN LATERAL jsonb_array_elements_text(
        COALESCE(
          r.payload->'strategy_entries_disabled_reasons'->'${PAPER_ACTIVITY_STRATEGY}',
          '[]'::jsonb
        )
      ) AS reason
      WHERE r.event_type = 'supervisor_cycle'
        AND COALESCE(r.payload->'blocked_strategy_names', '[]'::jsonb) ? :'paper_activity_strategy'
      GROUP BY reason
    ) strategy_reason_counts
  ), ''),
  COALESCE((
    SELECT COUNT(*)::int
    FROM strategy_positions
  ), 0),
  COALESCE((
    SELECT COUNT(*)::int
    FROM active_strategy_orders
  ), 0),
  COUNT(*) FILTER (
    WHERE event_type IN (
      'order_dispatch_failed',
      'order_dispatch_stop_price_rejected'
    )
      AND created_at >= COALESCE(
        (SELECT created_at FROM latest_supervisor_started),
        NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
      )
      AND (NOT (payload ? 'strategy_name') OR payload->>'strategy_name' = :'paper_activity_strategy')
  )::int,
  COALESCE((
    SELECT COUNT(*)::int
    FROM recent stream_issue
    WHERE stream_issue.created_at >= COALESCE(
        (SELECT created_at FROM latest_supervisor_started),
        NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
      )
      AND (
        stream_issue.event_type IN (
          'stream_restart_failed',
          'trade_update_stream_failed',
          'trade_update_failed',
          'protective_stop_quantity_replace_failed'
        )
      )
      AND (
        NOT (stream_issue.payload ? 'strategy_name')
        OR stream_issue.payload->>'strategy_name' = :'paper_activity_strategy'
      )
  ), 0)::int,
  COALESCE((
    SELECT CASE
      WHEN event_type = 'supervisor_idle'
       AND payload->>'reason' = 'market_closed'
      THEN 'true'
      ELSE 'false'
    END
    FROM latest_supervisor_activity
  ), 'false')
FROM recent;
SQL
)"

IFS='|' read -r supervisor_cycles disabled_cycles decision_cycles decision_records \
  market_closed_idles latest_cycle latest_cycle_entries_disabled \
  latest_cycle_disabled_reasons latest_cycle_strategy_blocked \
  latest_cycle_strategy_disabled_reasons latest_decision strategy_blocked_cycles \
  strategy_decision_cycles strategy_decision_records legacy_decision_cycles \
  strategy_decision_log_cycles strategy_decision_log_records latest_decision_log \
  strategy_decision_log_summary strategy_accepted_decisions latest_accepted_decision_log \
  recent_entry_orders recent_entry_order_status_summary \
  accepted_symbol_count accepted_symbols materialized_entry_symbol_count materialized_entry_symbols \
  unmaterialized_accepted_symbol_count unmaterialized_accepted_symbols \
  stale_pending_entry_orders stale_pending_entry_order_summary \
  active_strategy_names disabled_reasons strategy_disabled_reasons \
  stock_open_positions active_stock_orders dispatch_failures stream_issues \
  latest_activity_market_closed <<< "$stats"

strategy_evidence_cycles="${strategy_decision_cycles:-0}"
if [[ "${strategy_decision_log_cycles:-0}" -gt "$strategy_evidence_cycles" ]]; then
  strategy_evidence_cycles="$strategy_decision_log_cycles"
fi

strategy_evidence_records="${strategy_decision_records:-0}"
strategy_evidence_source="audit"
if [[ "${strategy_decision_log_records:-0}" -gt "$strategy_evidence_records" ]]; then
  strategy_evidence_records="$strategy_decision_log_records"
  strategy_evidence_source="decision_log"
fi

has_stock_exposure=false
if [[ "${stock_open_positions:-0}" -gt 0 || "${active_stock_orders:-0}" -gt 0 ]]; then
  has_stock_exposure=true
fi
paper_profit_lock_pause=false

if [[ "${market_closed_idles:-0}" -gt 0 && "${latest_activity_market_closed:-false}" == "true" ]]; then
  if market_clock="$(load_market_clock_status)"; then
    IFS='|' read -r market_clock_status market_clock_detail <<< "$market_clock"
  else
    market_clock_status="unknown"
    market_clock_detail="clock command failed"
  fi

  if [[ "$market_clock_status" == "closed" ]]; then
    echo "paper activity skipped: latest supervisor activity is market_closed clock=${market_clock_detail:-unknown}"
    exit 0
  fi

  echo "paper activity failed: supervisor reported market_closed but Alpaca clock is ${market_clock_status:-unknown} (${market_clock_detail:-no detail})" >&2
  exit 1
fi

if [[ "${supervisor_cycles:-0}" -eq 0 && "${market_closed_idles:-0}" -gt 0 ]]; then
  if market_clock="$(load_market_clock_status)"; then
    IFS='|' read -r market_clock_status market_clock_detail <<< "$market_clock"
  else
    market_clock_status="unknown"
    market_clock_detail="clock command failed"
  fi

  if [[ "$market_clock_status" == "closed" ]]; then
    echo "paper activity skipped: market closed in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes clock=${market_clock_detail:-unknown}"
    exit 0
  fi

  echo "paper activity failed: supervisor reported market_closed but Alpaca clock is ${market_clock_status:-unknown} (${market_clock_detail:-no detail})" >&2
  exit 1
fi

if [[ "${supervisor_cycles:-0}" -eq 0 ]]; then
  echo "paper activity failed: no supervisor cycles in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes" >&2
  exit 1
fi

if [[ "${latest_cycle_entries_disabled:-false}" == "true" ]]; then
  if only_readiness_missing_reasons "${latest_cycle_disabled_reasons:-}"; then
    echo "paper activity pending: latest supervisor cycle still had entries disabled for paper_readiness_check_missing; waiting for post-repair cycle"
    exit 43
  fi
  if only_runtime_reconciliation_reasons "${latest_cycle_disabled_reasons:-}"; then
    echo "paper activity pending: latest supervisor cycle still had entries disabled for runtime_reconciliation_mismatch; waiting for post-reconciliation cycle"
    exit 43
  fi
  if profit_lock_flat_pause_active "${latest_cycle_disabled_reasons:-}"; then
    paper_profit_lock_pause=true
  else
    reason_suffix=""
    if [[ -n "${latest_cycle_disabled_reasons:-}" ]]; then
      reason_suffix=" reasons=$latest_cycle_disabled_reasons"
    fi
    echo "paper activity failed: latest supervisor cycle had entries disabled$reason_suffix disabled_cycles=$disabled_cycles/$supervisor_cycles" >&2
    exit 1
  fi
fi

if [[ "${latest_cycle_strategy_blocked:-false}" == "true" ]]; then
  if only_readiness_missing_reasons "${latest_cycle_strategy_disabled_reasons:-}"; then
    echo "paper activity pending: latest $PAPER_ACTIVITY_STRATEGY cycle still had entries disabled for paper_readiness_check_missing; waiting for post-repair cycle"
    exit 43
  fi
  if only_runtime_reconciliation_reasons "${latest_cycle_strategy_disabled_reasons:-}"; then
    echo "paper activity pending: latest $PAPER_ACTIVITY_STRATEGY cycle still had entries disabled for runtime_reconciliation_mismatch; waiting for post-reconciliation cycle"
    exit 43
  fi
  if profit_lock_flat_pause_active "${latest_cycle_strategy_disabled_reasons:-}"; then
    paper_profit_lock_pause=true
  elif only_strategy_session_state_reasons "${latest_cycle_strategy_disabled_reasons:-}" \
    && [[ "$has_stock_exposure" != "true" ]] \
    && is_after_configured_flatten_time; then
    post_flatten_strategy_blocked=true
  else
    reason_suffix=""
    if [[ -n "${latest_cycle_strategy_disabled_reasons:-}" ]]; then
      reason_suffix=" reasons=$latest_cycle_strategy_disabled_reasons"
    fi
    echo "paper activity failed: latest $PAPER_ACTIVITY_STRATEGY entries blocked$reason_suffix blocked_cycles=$strategy_blocked_cycles/$supervisor_cycles" >&2
    exit 1
  fi
fi

if [[ "${dispatch_failures:-0}" -gt 0 ]]; then
  echo "paper activity failed: order dispatch failure events in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes count=$dispatch_failures" >&2
  exit 1
fi

if [[ "${stream_issues:-0}" -gt 0 ]]; then
  echo "paper activity failed: trade update stream issues in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes count=$stream_issues" >&2
  exit 1
fi

if [[ "${decision_cycles:-0}" -eq 0 && "${strategy_evidence_cycles:-0}" -eq 0 ]]; then
  echo "paper activity failed: no decision cycles in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes" >&2
  exit 1
fi

if [[ "${strategy_evidence_cycles:-0}" -eq 0 ]]; then
  echo "paper activity failed: no $PAPER_ACTIVITY_STRATEGY decision cycles in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes" >&2
  exit 1
fi

if [[ "${unmaterialized_accepted_symbol_count:-0}" -gt 0 ]]; then
  echo "paper activity failed: $PAPER_ACTIVITY_STRATEGY accepted_decisions=${strategy_accepted_decisions:-0} unmaterialized_accepted_symbols=[${unmaterialized_accepted_symbols:-}] accepted_symbols=[${accepted_symbols:-}] materialized_entry_symbols=[${materialized_entry_symbols:-}] recent_entry_orders=${recent_entry_orders:-0} stock_open_positions=${stock_open_positions:-0} active_stock_orders=${active_stock_orders:-0} latest_accepted_decision_log=${latest_accepted_decision_log:-none} decision_log_summary=[${strategy_decision_log_summary:-}]" >&2
  exit 1
fi

if [[ "${stale_pending_entry_orders:-0}" -gt 0 ]]; then
  echo "paper activity failed: stale pending entry orders count=${stale_pending_entry_orders:-0} max_age_minutes=$PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES symbols=[${stale_pending_entry_order_summary:-}]" >&2
  exit 1
fi

if [[ "${PAPER_ACTIVITY_REQUIRE_DECISION_LOG,,}" == "true" ]]; then
  if [[ "${strategy_decision_log_cycles:-0}" -eq 0 ]]; then
    echo "paper activity failed: no $PAPER_ACTIVITY_STRATEGY decision_log cycles in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes audit_cycles=${strategy_decision_cycles:-0} decision_log_summary=[${strategy_decision_log_summary:-}]" >&2
    exit 1
  fi

  if [[ "${strategy_decision_log_records:-0}" -lt "$PAPER_ACTIVITY_MIN_DECISION_RECORDS" && "$has_stock_exposure" != "true" ]]; then
    echo "paper activity failed: $PAPER_ACTIVITY_STRATEGY decision_log_records=${strategy_decision_log_records:-0} below $PAPER_ACTIVITY_MIN_DECISION_RECORDS audit_records=${strategy_decision_records:-0} decision_log_summary=[${strategy_decision_log_summary:-}]" >&2
    exit 1
  fi
fi

if [[ "${strategy_evidence_records:-0}" -lt "$PAPER_ACTIVITY_MIN_DECISION_RECORDS" && "$has_stock_exposure" != "true" ]]; then
  echo "paper activity failed: $PAPER_ACTIVITY_STRATEGY decision_evidence_records=$strategy_evidence_records below $PAPER_ACTIVITY_MIN_DECISION_RECORDS audit_records=${strategy_decision_records:-0} decision_log_records=${strategy_decision_log_records:-0} decision_log_summary=[${strategy_decision_log_summary:-}]" >&2
  exit 1
fi

broker_account_status="skipped"
broker_equity="none"
broker_buying_power="none"
broker_minimum_buying_power="none"
broker_trading_blocked="none"
broker_open_orders="none"
broker_open_positions="none"
broker_open_order_symbols="none"
broker_open_position_symbols="none"
if [[ "${PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT,,}" == "true" ]]; then
  set +e
  broker_activity="$(load_broker_activity_status)"
  broker_activity_rc="$?"
  set -e
  if [[ "$broker_activity_rc" -ne 0 ]]; then
    echo "paper activity failed: broker account check failed" >&2
    exit 1
  fi
  IFS='|' read -r broker_account_status broker_equity broker_buying_power \
    broker_minimum_buying_power broker_trading_blocked broker_open_orders \
    broker_open_positions broker_open_order_symbols broker_open_position_symbols \
    <<< "$broker_activity"

  if [[ "$broker_account_status" != "ok" ]]; then
    echo "paper activity failed: broker account not tradable equity=${broker_equity:-unset} buying_power=${broker_buying_power:-unset} minimum_required=${broker_minimum_buying_power:-unset} trading_blocked=${broker_trading_blocked:-unset} open_orders=${broker_open_orders:-unset} open_positions=${broker_open_positions:-unset}" >&2
    exit 1
  fi
fi

echo "paper activity ok: supervisor_cycles=$supervisor_cycles disabled_cycles=${disabled_cycles:-0} latest_cycle_entries_disabled=${latest_cycle_entries_disabled:-false} decision_cycles=$decision_cycles decision_records=$decision_records ${PAPER_ACTIVITY_STRATEGY}_audit_cycles=$strategy_decision_cycles ${PAPER_ACTIVITY_STRATEGY}_audit_records=$strategy_decision_records ${PAPER_ACTIVITY_STRATEGY}_blocked_cycles=${strategy_blocked_cycles:-0} latest_${PAPER_ACTIVITY_STRATEGY}_blocked=${latest_cycle_strategy_blocked:-false} post_flatten_strategy_blocked=${post_flatten_strategy_blocked:-false} paper_profit_lock_pause=$paper_profit_lock_pause dispatch_failures=${dispatch_failures:-0} stream_issues=${stream_issues:-0} ${PAPER_ACTIVITY_STRATEGY}_decision_log_cycles=$strategy_decision_log_cycles ${PAPER_ACTIVITY_STRATEGY}_decision_log_records=$strategy_decision_log_records ${PAPER_ACTIVITY_STRATEGY}_decision_log_summary=[${strategy_decision_log_summary:-}] ${PAPER_ACTIVITY_STRATEGY}_accepted_decisions=${strategy_accepted_decisions:-0} ${PAPER_ACTIVITY_STRATEGY}_accepted_symbols=[${accepted_symbols:-}] latest_${PAPER_ACTIVITY_STRATEGY}_accepted_decision_log=${latest_accepted_decision_log:-none} ${PAPER_ACTIVITY_STRATEGY}_recent_entry_orders=${recent_entry_orders:-0} ${PAPER_ACTIVITY_STRATEGY}_entry_order_status_summary=[${recent_entry_order_status_summary:-}] ${PAPER_ACTIVITY_STRATEGY}_materialized_entry_symbols=[${materialized_entry_symbols:-}] ${PAPER_ACTIVITY_STRATEGY}_unmaterialized_accepted_symbols=[${unmaterialized_accepted_symbols:-}] ${PAPER_ACTIVITY_STRATEGY}_stale_pending_entry_orders=${stale_pending_entry_orders:-0} ${PAPER_ACTIVITY_STRATEGY}_stale_pending_entry_order_summary=[${stale_pending_entry_order_summary:-}] ${PAPER_ACTIVITY_STRATEGY}_evidence_records=$strategy_evidence_records evidence_source=$strategy_evidence_source require_decision_log=${PAPER_ACTIVITY_REQUIRE_DECISION_LOG,,} require_broker_account=${PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT,,} broker_account_status=${broker_account_status:-unset} broker_equity=${broker_equity:-unset} broker_buying_power=${broker_buying_power:-unset} broker_minimum_required=${broker_minimum_buying_power:-unset} broker_trading_blocked=${broker_trading_blocked:-unset} broker_open_orders=${broker_open_orders:-unset} broker_open_positions=${broker_open_positions:-unset} broker_open_order_symbols=${broker_open_order_symbols:-none} broker_open_position_symbols=${broker_open_position_symbols:-none} stock_open_positions=${stock_open_positions:-0} active_stock_orders=${active_stock_orders:-0} legacy_decision_cycles=$legacy_decision_cycles active_strategies=[${active_strategy_names:-}] latest_cycle=${latest_cycle:-none} latest_decision=${latest_decision:-none} latest_decision_log=${latest_decision_log:-none}"
