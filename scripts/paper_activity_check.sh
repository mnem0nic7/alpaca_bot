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
  PAPER_ACTIVITY_STRATEGY \
  PAPER_ACTIVITY_STRATEGIES

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
PAPER_ACTIVITY_STRATEGIES="${PAPER_ACTIVITY_STRATEGIES:-${PAPER_APPROVED_STRATEGIES:-$PAPER_ACTIVITY_STRATEGY}}"

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

paper_activity_strategy_names=()
paper_activity_strategy_csv=""

add_paper_activity_strategy() {
  local raw="$1"
  local name
  local existing

  name="$(printf '%s' "$raw" | tr -d '[:space:]')"
  if [[ -z "$name" ]]; then
    return
  fi
  if [[ ! "$name" =~ ^[A-Za-z0-9_:-]+$ ]]; then
    echo "PAPER_ACTIVITY_STRATEGIES contains unsupported strategy: $name" >&2
    exit 1
  fi
  for existing in "${paper_activity_strategy_names[@]}"; do
    if [[ "$existing" == "$name" ]]; then
      return
    fi
  done
  paper_activity_strategy_names+=("$name")
}

build_paper_activity_strategies() {
  local csv="$1"
  local raw
  local -a raw_names

  paper_activity_strategy_names=()
  add_paper_activity_strategy "$PAPER_ACTIVITY_STRATEGY"
  IFS=',' read -r -a raw_names <<< "$csv"
  for raw in "${raw_names[@]}"; do
    add_paper_activity_strategy "$raw"
  done
  if [[ "${#paper_activity_strategy_names[@]}" -eq 0 ]]; then
    echo "PAPER_ACTIVITY_STRATEGIES must contain at least one strategy" >&2
    exit 1
  fi
  paper_activity_strategy_csv="$(
    IFS=,
    printf '%s' "${paper_activity_strategy_names[*]}"
  )"
}

build_paper_activity_strategies "$PAPER_ACTIVITY_STRATEGIES"

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

emit_scheduled_context() {
  echo "scheduled check context: session_date=$(TZ=America/New_York date +%F) proof_start=${PROFIT_PROBE_START_DATE:-2026-07-07} strategy=$PAPER_ACTIVITY_STRATEGY strategies=$paper_activity_strategy_csv"
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
  local reason has_runtime=false
  IFS=',' read -ra _paper_activity_reasons <<< "$reasons"
  for reason in "${_paper_activity_reasons[@]}"; do
    case "$reason" in
      runtime_reconciliation_mismatch)
        has_runtime=true
        ;;
      entry_cadence_waiting_for_new_bar)
        ;;
      *)
        return 1
        ;;
    esac
  done
  [[ "$has_runtime" == "true" ]]
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
      entry_cadence_waiting_for_new_bar|paper_readiness_check_missing|runtime_reconciliation_mismatch)
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

proof_risk_lock_pause_active() {
  local reasons="${1:-}"
  only_profit_lock_pause_reasons "$reasons" || return 1

  local status_line
  status_line="$(load_trading_status_line 2>/dev/null)" || return 1
  [[ "$status_line" == *"status=close_only"* ]] || return 1
  [[ "$status_line" == *"kill_switch=false"* ]] || return 1
  [[ "$status_line" == *"reason=paper proof risk lock"* ]] || return 1

  if [[ "${has_stock_exposure:-false}" == "true" ]]; then
    [[ "${stock_open_positions:-0}" -gt 0 ]] || return 1
    [[ "${active_stock_orders:-0}" -eq "${stock_open_positions:-0}" ]] || return 1
    return 0
  fi

  BROKER_FLAT_CONTEXT="paper activity proof risk lock" \
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

  local status_line
  if status_line="$(load_trading_status_line 2>/dev/null)" \
    && [[ "$status_line" == *"status=close_only"* ]] \
    && [[ "$status_line" == *"kill_switch=false"* ]] \
    && [[ "$status_line" == *"reason=paper profit lock"* ]]; then
    echo "paper activity preserving active paper profit lock after activity failure: $status_line"
    exit "$rc"
  fi

  local session_date
  session_date="$(TZ=America/New_York date +%F)"
  local reason="paper activity failed for session ${session_date}: post-open checks failed for strategies ${paper_activity_strategy_csv:-${PAPER_ACTIVITY_STRATEGY:-unknown}}"
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
  -v paper_activity_strategy="$PAPER_ACTIVITY_STRATEGY" \
  -v paper_activity_strategies="$paper_activity_strategy_csv" <<SQL
WITH recent AS (
  SELECT event_type, payload, created_at
  FROM audit_events
  WHERE created_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
    AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = :'trading_mode')
    AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = :'strategy_version')
),
requested_activity_strategies AS (
  SELECT trim(value) AS strategy_name
  FROM unnest(string_to_array(:'paper_activity_strategies', ',')) AS raw(value)
  WHERE trim(value) <> ''
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
  COALESCE((
    SELECT COUNT(*)::int
    FROM recent dispatch_failure
    WHERE dispatch_failure.event_type IN (
        'order_dispatch_failed',
        'order_dispatch_stop_price_rejected'
      )
      AND dispatch_failure.created_at >= COALESCE(
        (SELECT created_at FROM latest_supervisor_started),
        NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
      )
      AND (
        NOT (dispatch_failure.payload ? 'strategy_name')
        OR dispatch_failure.payload->>'strategy_name' IN (
          SELECT strategy_name FROM requested_activity_strategies
        )
      )
      AND (
        dispatch_failure.event_type <> 'order_dispatch_stop_price_rejected'
        OR NOT EXISTS (
          SELECT 1
          FROM recent stop_recovery
          WHERE stop_recovery.event_type = 'recovery_exit_queued_stop_above_market'
            AND stop_recovery.created_at >= dispatch_failure.created_at
            AND COALESCE(stop_recovery.payload->>'symbol', '') =
                COALESCE(dispatch_failure.payload->>'symbol', '')
            AND (
              NOT (stop_recovery.payload ? 'strategy_name')
              OR stop_recovery.payload->>'strategy_name' IN (
                SELECT strategy_name FROM requested_activity_strategies
              )
            )
        )
      )
  ), 0)::int,
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
        OR stream_issue.payload->>'strategy_name' IN (
          SELECT strategy_name FROM requested_activity_strategies
        )
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
paper_proof_risk_lock_pause=false

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
  elif proof_risk_lock_pause_active "${latest_cycle_disabled_reasons:-}"; then
    paper_proof_risk_lock_pause=true
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
  elif proof_risk_lock_pause_active "${latest_cycle_strategy_disabled_reasons:-}"; then
    paper_proof_risk_lock_pause=true
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

if [[ "$paper_profit_lock_pause" != "true" && "$paper_proof_risk_lock_pause" != "true" ]]; then
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
fi

strategy_activity_summary="skipped"
if [[ "${#paper_activity_strategy_names[@]}" -gt 1 \
  && "$paper_profit_lock_pause" != "true" \
  && "$paper_proof_risk_lock_pause" != "true" ]]; then
  strategy_activity_summary="$("${compose[@]}" exec -T postgres psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -tA -F '|' \
    -v trading_mode="${TRADING_MODE:-paper}" \
    -v strategy_version="$STRATEGY_VERSION" \
    -v paper_activity_strategies="$paper_activity_strategy_csv" <<SQL
WITH requested AS (
  SELECT trim(value) AS strategy_name, ord
  FROM unnest(string_to_array(:'paper_activity_strategies', ',')) WITH ORDINALITY AS raw(value, ord)
  WHERE trim(value) <> ''
),
recent AS (
  SELECT event_type, payload, created_at
  FROM audit_events
  WHERE created_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
    AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = :'trading_mode')
    AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = :'strategy_version')
),
recent_decisions AS (
  SELECT cycle_at, symbol, strategy_name, decision
  FROM decision_log
  WHERE cycle_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
    AND trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
),
latest_supervisor AS (
  SELECT payload, created_at
  FROM recent
  WHERE event_type = 'supervisor_cycle'
  ORDER BY created_at DESC
  LIMIT 1
),
per_strategy AS (
  SELECT
    r.strategy_name,
    r.ord,
    COALESCE((
      SELECT CASE
        WHEN COALESCE(payload->'blocked_strategy_names', '[]'::jsonb) ? r.strategy_name
        THEN TRUE ELSE FALSE
      END
      FROM latest_supervisor
    ), FALSE) AS latest_blocked,
    COALESCE((
      SELECT array_to_string(ARRAY(
        SELECT jsonb_array_elements_text(
          COALESCE(payload->'strategy_entries_disabled_reasons'->r.strategy_name, '[]'::jsonb)
        )
      ), ',')
      FROM latest_supervisor
    ), '') AS latest_disabled_reasons,
    COALESCE((
      SELECT COUNT(*)::int
      FROM recent events
      WHERE events.event_type = 'decision_cycle_completed'
        AND events.payload->>'strategy_name' = r.strategy_name
    ), 0) AS audit_cycles,
    COALESCE((
      SELECT SUM(COALESCE((events.payload->>'decision_record_count')::int, 0))::int
      FROM recent events
      WHERE events.event_type = 'decision_cycle_completed'
        AND events.payload->>'strategy_name' = r.strategy_name
    ), 0) AS audit_records,
    COALESCE((
      SELECT COUNT(DISTINCT d.cycle_at)::int
      FROM recent_decisions d
      WHERE d.strategy_name = r.strategy_name
    ), 0) AS log_cycles,
    COALESCE((
      SELECT COUNT(*)::int
      FROM recent_decisions d
      WHERE d.strategy_name = r.strategy_name
    ), 0) AS log_records,
    COALESCE((
      SELECT COUNT(*)::int
      FROM recent_decisions d
      WHERE d.strategy_name = r.strategy_name
        AND d.decision = 'accepted'
    ), 0) AS accepted_decisions,
    COALESCE((
      WITH accepted_symbols AS (
        SELECT DISTINCT symbol
        FROM recent_decisions d
        WHERE d.strategy_name = r.strategy_name
          AND d.decision = 'accepted'
      ),
      materialized_symbols AS (
        SELECT symbol
        FROM orders
        WHERE trading_mode = 'paper'
          AND strategy_version = :'strategy_version'
          AND strategy_name IS NOT DISTINCT FROM r.strategy_name
          AND intent_type = 'entry'
          AND (
            created_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
            OR updated_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
            OR status IN (
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
          )
        UNION
        SELECT symbol
        FROM positions
        WHERE trading_mode = 'paper'
          AND strategy_version = :'strategy_version'
          AND strategy_name IS NOT DISTINCT FROM r.strategy_name
      )
      SELECT COUNT(*)::int
      FROM (
        SELECT symbol FROM accepted_symbols
        EXCEPT
        SELECT symbol FROM materialized_symbols
      ) unmaterialized
    ), 0) AS unmaterialized_accepted,
    COALESCE((
      SELECT COUNT(*)::int
      FROM orders
      WHERE trading_mode = 'paper'
        AND strategy_version = :'strategy_version'
        AND strategy_name IS NOT DISTINCT FROM r.strategy_name
        AND intent_type = 'entry'
        AND status = 'pending_submit'
        AND broker_order_id IS NULL
        AND created_at <= NOW() - (${PAPER_ACTIVITY_STALE_PENDING_ENTRY_MINUTES} * interval '1 minute')
    ), 0) AS stale_pending_entries,
    COALESCE((
      SELECT COUNT(*)::int
      FROM positions
      WHERE trading_mode = 'paper'
        AND strategy_version = :'strategy_version'
        AND strategy_name IS NOT DISTINCT FROM r.strategy_name
    ), 0) AS open_positions,
    COALESCE((
      SELECT COUNT(*)::int
      FROM orders
      WHERE trading_mode = 'paper'
        AND strategy_version = :'strategy_version'
        AND strategy_name IS NOT DISTINCT FROM r.strategy_name
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
    ), 0) AS active_orders
  FROM requested r
),
scored AS (
  SELECT
    strategy_name,
    ord,
    latest_blocked,
    latest_disabled_reasons,
    audit_cycles,
    audit_records,
    log_cycles,
    log_records,
    GREATEST(audit_cycles, log_cycles) AS evidence_cycles,
    GREATEST(audit_records, log_records) AS evidence_records,
    accepted_decisions,
    unmaterialized_accepted,
    stale_pending_entries,
    (open_positions > 0 OR active_orders > 0) AS has_exposure
  FROM per_strategy
)
SELECT
  COUNT(*)::int,
  COALESCE(string_agg(strategy_name, ',' ORDER BY ord), ''),
  COALESCE(string_agg(strategy_name, ',' ORDER BY ord) FILTER (WHERE evidence_cycles = 0), ''),
  COALESCE(string_agg(strategy_name, ',' ORDER BY ord) FILTER (WHERE log_cycles = 0), ''),
  COALESCE(string_agg(strategy_name, ',' ORDER BY ord) FILTER (WHERE log_records < ${PAPER_ACTIVITY_MIN_DECISION_RECORDS} AND NOT has_exposure), ''),
  COALESCE(string_agg(strategy_name, ',' ORDER BY ord) FILTER (WHERE evidence_records < ${PAPER_ACTIVITY_MIN_DECISION_RECORDS} AND NOT has_exposure), ''),
  COALESCE(string_agg(strategy_name || ':' || COALESCE(NULLIF(latest_disabled_reasons, ''), 'blocked'), ',' ORDER BY ord) FILTER (WHERE latest_blocked), ''),
  COALESCE(string_agg(strategy_name, ',' ORDER BY ord) FILTER (WHERE unmaterialized_accepted > 0), ''),
  COALESCE(string_agg(strategy_name, ',' ORDER BY ord) FILTER (WHERE stale_pending_entries > 0), ''),
  COALESCE(string_agg(
    strategy_name || ':cycles=' || evidence_cycles::text ||
    ',records=' || evidence_records::text ||
    ',log_cycles=' || log_cycles::text ||
    ',log_records=' || log_records::text ||
    ',accepted=' || accepted_decisions::text ||
    ',exposure=' || has_exposure::text,
    ';' ORDER BY ord
  ), '')
FROM scored;
SQL
  )"

  IFS='|' read -r activity_strategy_count activity_strategy_names \
    missing_activity_strategy_cycles missing_activity_strategy_log_cycles \
    low_activity_strategy_log_records low_activity_strategy_evidence_records \
    blocked_activity_strategies unmaterialized_activity_strategies \
    stale_pending_activity_strategies activity_strategy_detail \
    <<< "$strategy_activity_summary"

  if [[ -z "${activity_strategy_count:-}" ]]; then
    echo "paper activity failed: approved strategy activity summary missing" >&2
    exit 1
  fi
  if [[ "$activity_strategy_names" != "$paper_activity_strategy_csv" ]]; then
    echo "paper activity failed: approved strategy activity summary mismatch expected=[$paper_activity_strategy_csv] actual=[$activity_strategy_names]" >&2
    exit 1
  fi
  if [[ -n "${blocked_activity_strategies:-}" ]]; then
    if ! is_after_configured_flatten_time; then
      echo "paper activity failed: approved strategy entries blocked [$blocked_activity_strategies]" >&2
      exit 1
    fi
  fi
  if [[ -n "${missing_activity_strategy_cycles:-}" ]]; then
    echo "paper activity failed: approved strategies missing decision evidence cycles [$missing_activity_strategy_cycles] detail=[$activity_strategy_detail]" >&2
    exit 1
  fi
  if [[ "${PAPER_ACTIVITY_REQUIRE_DECISION_LOG,,}" == "true" ]]; then
    if [[ -n "${missing_activity_strategy_log_cycles:-}" ]]; then
      echo "paper activity failed: approved strategies missing decision_log cycles [$missing_activity_strategy_log_cycles] detail=[$activity_strategy_detail]" >&2
      exit 1
    fi
    if [[ -n "${low_activity_strategy_log_records:-}" ]]; then
      echo "paper activity failed: approved strategies decision_log records below $PAPER_ACTIVITY_MIN_DECISION_RECORDS [$low_activity_strategy_log_records] detail=[$activity_strategy_detail]" >&2
      exit 1
    fi
  fi
  if [[ -n "${low_activity_strategy_evidence_records:-}" ]]; then
    echo "paper activity failed: approved strategies decision evidence records below $PAPER_ACTIVITY_MIN_DECISION_RECORDS [$low_activity_strategy_evidence_records] detail=[$activity_strategy_detail]" >&2
    exit 1
  fi
  if [[ -n "${unmaterialized_activity_strategies:-}" ]]; then
    echo "paper activity failed: approved strategies have unmaterialized accepted decisions [$unmaterialized_activity_strategies] detail=[$activity_strategy_detail]" >&2
    exit 1
  fi
  if [[ -n "${stale_pending_activity_strategies:-}" ]]; then
    echo "paper activity failed: approved strategies have stale pending entries [$stale_pending_activity_strategies] detail=[$activity_strategy_detail]" >&2
    exit 1
  fi

  echo "paper activity strategies ok: strategies=$activity_strategy_names count=$activity_strategy_count detail=[$activity_strategy_detail]"
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

echo "paper activity ok: supervisor_cycles=$supervisor_cycles disabled_cycles=${disabled_cycles:-0} latest_cycle_entries_disabled=${latest_cycle_entries_disabled:-false} decision_cycles=$decision_cycles decision_records=$decision_records ${PAPER_ACTIVITY_STRATEGY}_audit_cycles=$strategy_decision_cycles ${PAPER_ACTIVITY_STRATEGY}_audit_records=$strategy_decision_records ${PAPER_ACTIVITY_STRATEGY}_blocked_cycles=${strategy_blocked_cycles:-0} latest_${PAPER_ACTIVITY_STRATEGY}_blocked=${latest_cycle_strategy_blocked:-false} post_flatten_strategy_blocked=${post_flatten_strategy_blocked:-false} paper_profit_lock_pause=$paper_profit_lock_pause paper_proof_risk_lock_pause=$paper_proof_risk_lock_pause dispatch_failures=${dispatch_failures:-0} stream_issues=${stream_issues:-0} ${PAPER_ACTIVITY_STRATEGY}_decision_log_cycles=$strategy_decision_log_cycles ${PAPER_ACTIVITY_STRATEGY}_decision_log_records=$strategy_decision_log_records ${PAPER_ACTIVITY_STRATEGY}_decision_log_summary=[${strategy_decision_log_summary:-}] ${PAPER_ACTIVITY_STRATEGY}_accepted_decisions=${strategy_accepted_decisions:-0} ${PAPER_ACTIVITY_STRATEGY}_accepted_symbols=[${accepted_symbols:-}] latest_${PAPER_ACTIVITY_STRATEGY}_accepted_decision_log=${latest_accepted_decision_log:-none} ${PAPER_ACTIVITY_STRATEGY}_recent_entry_orders=${recent_entry_orders:-0} ${PAPER_ACTIVITY_STRATEGY}_entry_order_status_summary=[${recent_entry_order_status_summary:-}] ${PAPER_ACTIVITY_STRATEGY}_materialized_entry_symbols=[${materialized_entry_symbols:-}] ${PAPER_ACTIVITY_STRATEGY}_unmaterialized_accepted_symbols=[${unmaterialized_accepted_symbols:-}] ${PAPER_ACTIVITY_STRATEGY}_stale_pending_entry_orders=${stale_pending_entry_orders:-0} ${PAPER_ACTIVITY_STRATEGY}_stale_pending_entry_order_summary=[${stale_pending_entry_order_summary:-}] ${PAPER_ACTIVITY_STRATEGY}_evidence_records=$strategy_evidence_records evidence_source=$strategy_evidence_source require_decision_log=${PAPER_ACTIVITY_REQUIRE_DECISION_LOG,,} require_broker_account=${PAPER_ACTIVITY_REQUIRE_BROKER_ACCOUNT,,} broker_account_status=${broker_account_status:-unset} broker_equity=${broker_equity:-unset} broker_buying_power=${broker_buying_power:-unset} broker_minimum_required=${broker_minimum_buying_power:-unset} broker_trading_blocked=${broker_trading_blocked:-unset} broker_open_orders=${broker_open_orders:-unset} broker_open_positions=${broker_open_positions:-unset} broker_open_order_symbols=${broker_open_order_symbols:-none} broker_open_position_symbols=${broker_open_position_symbols:-none} stock_open_positions=${stock_open_positions:-0} active_stock_orders=${active_stock_orders:-0} legacy_decision_cycles=$legacy_decision_cycles active_strategies=[${active_strategy_names:-}] latest_cycle=${latest_cycle:-none} latest_decision=${latest_decision:-none} latest_decision_log=${latest_decision_log:-none}"
