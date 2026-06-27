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

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "paper activity check skipped for TRADING_MODE=${TRADING_MODE:-unset}"
  exit 0
fi

PAPER_READINESS_AUTO_RESUME=false ./scripts/paper_readiness_check.sh "$ENV_FILE"

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

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
  COALESCE(MAX(created_at) FILTER (WHERE event_type = 'decision_cycle_completed')::text, '')
FROM recent;
SQL
)"

IFS='|' read -r supervisor_cycles disabled_cycles decision_cycles decision_records \
  market_closed_idles latest_cycle latest_decision <<< "$stats"

if [[ "${supervisor_cycles:-0}" -eq 0 && "${market_closed_idles:-0}" -gt 0 ]]; then
  echo "paper activity skipped: market closed in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes"
  exit 0
fi

if [[ "${supervisor_cycles:-0}" -eq 0 ]]; then
  echo "paper activity failed: no supervisor cycles in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes" >&2
  exit 1
fi

if [[ "${disabled_cycles:-0}" -gt 0 ]]; then
  echo "paper activity failed: $disabled_cycles/$supervisor_cycles supervisor cycles had entries disabled" >&2
  exit 1
fi

if [[ "${decision_cycles:-0}" -eq 0 ]]; then
  echo "paper activity failed: no decision cycles in last ${PAPER_ACTIVITY_WINDOW_MINUTES} minutes" >&2
  exit 1
fi

if [[ "${decision_records:-0}" -lt "$PAPER_ACTIVITY_MIN_DECISION_RECORDS" ]]; then
  echo "paper activity failed: decision_record_count=$decision_records below $PAPER_ACTIVITY_MIN_DECISION_RECORDS" >&2
  exit 1
fi

echo "paper activity ok: supervisor_cycles=$supervisor_cycles decision_cycles=$decision_cycles decision_records=$decision_records latest_cycle=${latest_cycle:-none} latest_decision=${latest_decision:-none}"
