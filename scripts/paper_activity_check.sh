#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PAPER_ACTIVITY_WINDOW_MINUTES="${PAPER_ACTIVITY_WINDOW_MINUTES:-90}"
PAPER_ACTIVITY_MIN_DECISION_RECORDS="${PAPER_ACTIVITY_MIN_DECISION_RECORDS:-1}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

if [[ ! "$PAPER_ACTIVITY_WINDOW_MINUTES" =~ ^[0-9]+$ ]] \
  || [[ "$PAPER_ACTIVITY_WINDOW_MINUTES" -lt 1 ]]; then
  echo "PAPER_ACTIVITY_WINDOW_MINUTES must be a positive integer" >&2
  exit 1
fi

if [[ ! "$PAPER_ACTIVITY_MIN_DECISION_RECORDS" =~ ^[0-9]+$ ]]; then
  echo "PAPER_ACTIVITY_MIN_DECISION_RECORDS must be a non-negative integer" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

PAPER_ACTIVITY_STRATEGY="${PAPER_ACTIVITY_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"

if [[ ! "$PAPER_ACTIVITY_STRATEGY" =~ ^[A-Za-z0-9_:-]+$ ]]; then
  echo "PAPER_ACTIVITY_STRATEGY contains unsupported characters" >&2
  exit 1
fi

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "paper activity check skipped for TRADING_MODE=${TRADING_MODE:-unset}"
  exit 0
fi

PAPER_READINESS_AUTO_RESUME=false PAPER_READINESS_REQUIRE_FLAT=false \
  ./scripts/paper_readiness_check.sh "$ENV_FILE"

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

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
  -tA -F '|' <<SQL
WITH recent AS (
  SELECT event_type, payload, created_at
  FROM audit_events
  WHERE created_at >= NOW() - (${PAPER_ACTIVITY_WINDOW_MINUTES} * interval '1 minute')
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
  COALESCE(MAX(created_at) FILTER (WHERE event_type = 'decision_cycle_completed')::text, ''),
  COUNT(*) FILTER (
    WHERE event_type = 'supervisor_cycle'
      AND COALESCE(payload->'blocked_strategy_names', '[]'::jsonb) ? '${PAPER_ACTIVITY_STRATEGY}'
  )::int,
  COUNT(*) FILTER (
    WHERE event_type = 'decision_cycle_completed'
      AND payload->>'strategy_name' = '${PAPER_ACTIVITY_STRATEGY}'
  )::int,
  COALESCE(SUM(
    CASE
      WHEN event_type = 'decision_cycle_completed'
       AND payload->>'strategy_name' = '${PAPER_ACTIVITY_STRATEGY}'
      THEN COALESCE((payload->>'decision_record_count')::int, 0)
      ELSE 0
    END
  ), 0)::int,
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
        AND COALESCE(r.payload->'blocked_strategy_names', '[]'::jsonb) ? '${PAPER_ACTIVITY_STRATEGY}'
      GROUP BY reason
    ) strategy_reason_counts
  ), '')
FROM recent;
SQL
)"

IFS='|' read -r supervisor_cycles disabled_cycles decision_cycles decision_records \
  market_closed_idles latest_cycle latest_decision strategy_blocked_cycles \
  strategy_decision_cycles strategy_decision_records disabled_reasons \
  strategy_disabled_reasons <<< "$stats"

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

if [[ "${disabled_cycles:-0}" -gt 0 ]]; then
  reason_suffix=""
  if [[ -n "${disabled_reasons:-}" ]]; then
    reason_suffix=" reasons=$disabled_reasons"
  fi
  echo "paper activity failed: $disabled_cycles/$supervisor_cycles supervisor cycles had entries disabled$reason_suffix" >&2
  exit 1
fi

if [[ "${strategy_blocked_cycles:-0}" -gt 0 ]]; then
  reason_suffix=""
  if [[ -n "${strategy_disabled_reasons:-}" ]]; then
    reason_suffix=" reasons=$strategy_disabled_reasons"
  fi
  echo "paper activity failed: $PAPER_ACTIVITY_STRATEGY entries blocked in $strategy_blocked_cycles/$supervisor_cycles supervisor cycles$reason_suffix" >&2
  exit 1
fi

if [[ "${decision_cycles:-0}" -eq 0 ]]; then
  echo "paper activity failed: no decision cycles in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes" >&2
  exit 1
fi

if [[ "${strategy_decision_cycles:-0}" -eq 0 ]]; then
  echo "paper activity failed: no $PAPER_ACTIVITY_STRATEGY decision cycles in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes" >&2
  exit 1
fi

if [[ "${strategy_decision_records:-0}" -lt "$PAPER_ACTIVITY_MIN_DECISION_RECORDS" ]]; then
  echo "paper activity failed: $PAPER_ACTIVITY_STRATEGY decision_record_count=$strategy_decision_records below $PAPER_ACTIVITY_MIN_DECISION_RECORDS" >&2
  exit 1
fi

echo "paper activity ok: supervisor_cycles=$supervisor_cycles decision_cycles=$decision_cycles decision_records=$decision_records ${PAPER_ACTIVITY_STRATEGY}_decision_cycles=$strategy_decision_cycles ${PAPER_ACTIVITY_STRATEGY}_decision_records=$strategy_decision_records latest_cycle=${latest_cycle:-none} latest_decision=${latest_decision:-none}"
