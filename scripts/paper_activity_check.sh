#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PAPER_ACTIVITY_WINDOW_MINUTES="${PAPER_ACTIVITY_WINDOW_MINUTES:-90}"
PAPER_ACTIVITY_MIN_DECISION_RECORDS="${PAPER_ACTIVITY_MIN_DECISION_RECORDS:-900}"
PAPER_ACTIVITY_REQUIRE_DECISION_LOG="${PAPER_ACTIVITY_REQUIRE_DECISION_LOG:-true}"
PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE="${PAPER_ACTIVITY_CLOSE_ONLY_ON_FAILURE:-true}"
PAPER_ACTIVITY_READINESS_RUNNER="${PAPER_ACTIVITY_READINESS_RUNNER:-./scripts/run_locked_check_with_audit.sh}"
PAPER_ACTIVITY_READINESS_SCRIPT="${PAPER_ACTIVITY_READINESS_SCRIPT:-./scripts/paper_readiness_if_needed.sh}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

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

case "${PAPER_ACTIVITY_REQUIRE_DECISION_LOG,,}" in
  true|false) ;;
  *)
    echo "PAPER_ACTIVITY_REQUIRE_DECISION_LOG must be true or false" >&2
    exit 1
    ;;
esac

set +e
PAPER_READINESS_AUTO_RESUME=false PAPER_READINESS_REQUIRE_FLAT=false \
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
  SELECT cycle_at, strategy_name
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
    FROM positions
    WHERE trading_mode = 'paper'
      AND strategy_version = :'strategy_version'
  ), 0),
  COALESCE((
    SELECT COUNT(*)::int
    FROM orders
    WHERE trading_mode = 'paper'
      AND strategy_version = :'strategy_version'
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
  ), 0)
FROM recent;
SQL
)"

IFS='|' read -r supervisor_cycles disabled_cycles decision_cycles decision_records \
  market_closed_idles latest_cycle latest_cycle_entries_disabled \
  latest_cycle_disabled_reasons latest_cycle_strategy_blocked \
  latest_cycle_strategy_disabled_reasons latest_decision strategy_blocked_cycles \
  strategy_decision_cycles strategy_decision_records legacy_decision_cycles \
  strategy_decision_log_cycles strategy_decision_log_records latest_decision_log \
  active_strategy_names disabled_reasons strategy_disabled_reasons \
  stock_open_positions active_stock_orders <<< "$stats"

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
  reason_suffix=""
  if [[ -n "${latest_cycle_disabled_reasons:-}" ]]; then
    reason_suffix=" reasons=$latest_cycle_disabled_reasons"
  fi
  echo "paper activity failed: latest supervisor cycle had entries disabled$reason_suffix disabled_cycles=$disabled_cycles/$supervisor_cycles" >&2
  exit 1
fi

if [[ "${latest_cycle_strategy_blocked:-false}" == "true" ]]; then
  reason_suffix=""
  if [[ -n "${latest_cycle_strategy_disabled_reasons:-}" ]]; then
    reason_suffix=" reasons=$latest_cycle_strategy_disabled_reasons"
  fi
  echo "paper activity failed: latest $PAPER_ACTIVITY_STRATEGY entries blocked$reason_suffix blocked_cycles=$strategy_blocked_cycles/$supervisor_cycles" >&2
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

if [[ "${PAPER_ACTIVITY_REQUIRE_DECISION_LOG,,}" == "true" ]]; then
  if [[ "${strategy_decision_log_cycles:-0}" -eq 0 ]]; then
    echo "paper activity failed: no $PAPER_ACTIVITY_STRATEGY decision_log cycles in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes audit_cycles=${strategy_decision_cycles:-0}" >&2
    exit 1
  fi

  if [[ "${strategy_decision_log_records:-0}" -lt "$PAPER_ACTIVITY_MIN_DECISION_RECORDS" && "$has_stock_exposure" != "true" ]]; then
    echo "paper activity failed: $PAPER_ACTIVITY_STRATEGY decision_log_records=${strategy_decision_log_records:-0} below $PAPER_ACTIVITY_MIN_DECISION_RECORDS audit_records=${strategy_decision_records:-0}" >&2
    exit 1
  fi
fi

if [[ "${strategy_evidence_records:-0}" -lt "$PAPER_ACTIVITY_MIN_DECISION_RECORDS" && "$has_stock_exposure" != "true" ]]; then
  echo "paper activity failed: $PAPER_ACTIVITY_STRATEGY decision_evidence_records=$strategy_evidence_records below $PAPER_ACTIVITY_MIN_DECISION_RECORDS audit_records=${strategy_decision_records:-0} decision_log_records=${strategy_decision_log_records:-0}" >&2
  exit 1
fi

echo "paper activity ok: supervisor_cycles=$supervisor_cycles disabled_cycles=${disabled_cycles:-0} latest_cycle_entries_disabled=${latest_cycle_entries_disabled:-false} decision_cycles=$decision_cycles decision_records=$decision_records ${PAPER_ACTIVITY_STRATEGY}_audit_cycles=$strategy_decision_cycles ${PAPER_ACTIVITY_STRATEGY}_audit_records=$strategy_decision_records ${PAPER_ACTIVITY_STRATEGY}_blocked_cycles=${strategy_blocked_cycles:-0} latest_${PAPER_ACTIVITY_STRATEGY}_blocked=${latest_cycle_strategy_blocked:-false} ${PAPER_ACTIVITY_STRATEGY}_decision_log_cycles=$strategy_decision_log_cycles ${PAPER_ACTIVITY_STRATEGY}_decision_log_records=$strategy_decision_log_records ${PAPER_ACTIVITY_STRATEGY}_evidence_records=$strategy_evidence_records evidence_source=$strategy_evidence_source require_decision_log=${PAPER_ACTIVITY_REQUIRE_DECISION_LOG,,} stock_open_positions=${stock_open_positions:-0} active_stock_orders=${active_stock_orders:-0} legacy_decision_cycles=$legacy_decision_cycles active_strategies=[${active_strategy_names:-}] latest_cycle=${latest_cycle:-none} latest_decision=${latest_decision:-none} latest_decision_log=${latest_decision_log:-none}"
