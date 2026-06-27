#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PAPER_READINESS_AUTO_RESUME="${PAPER_READINESS_AUTO_RESUME:-true}"
PAPER_READINESS_AUTO_RESET_WEIGHTS="${PAPER_READINESS_AUTO_RESET_WEIGHTS:-true}"
PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}"
PAPER_READINESS_MIN_CONFIDENCE_FLOOR="${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.01}"

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

if [[ ! "$PAPER_READINESS_MIN_WATCHLIST_SYMBOLS" =~ ^[0-9]+$ ]] \
  || [[ "$PAPER_READINESS_MIN_WATCHLIST_SYMBOLS" -lt 1 ]]; then
  echo "PAPER_READINESS_MIN_WATCHLIST_SYMBOLS must be a positive integer" >&2
  exit 1
fi

if [[ ! "$PAPER_READINESS_MIN_CONFIDENCE_FLOOR" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PAPER_READINESS_MIN_CONFIDENCE_FLOOR must be a non-negative number" >&2
  exit 1
fi

require_env_value() {
  local name="$1"
  local expected="$2"
  local actual="${!name:-}"
  if [[ "$actual" != "$expected" ]]; then
    echo "paper readiness failed: $name=$actual expected $expected" >&2
    exit 1
  fi
}

require_env_true() {
  local name="$1"
  local actual="${!name:-}"
  if [[ "${actual,,}" != "true" ]]; then
    echo "paper readiness failed: $name=$actual expected true" >&2
    exit 1
  fi
}

require_env_false_or_unset() {
  local name="$1"
  local actual="${!name:-}"
  if [[ -n "$actual" && "${actual,,}" != "false" ]]; then
    echo "paper readiness failed: $name=$actual expected false or unset" >&2
    exit 1
  fi
}

require_env_value STRATEGY_VERSION v1-breakout
require_env_value MAX_OPEN_POSITIONS 2
require_env_value REPLAY_SLIPPAGE_BPS 2.0
require_env_value RISK_PER_TRADE_PCT 0.01
require_env_value MAX_POSITION_PCT 0.05
require_env_value MAX_PORTFOLIO_EXPOSURE_PCT 0.30
require_env_value DAILY_LOSS_LIMIT_PCT 0.01
require_env_true ENABLE_VWAP_ENTRY_FILTER
require_env_false_or_unset ENABLE_VIX_FILTER
require_env_false_or_unset ENABLE_SECTOR_FILTER
require_env_false_or_unset ENABLE_REGIME_FILTER
require_env_false_or_unset ENABLE_OPTIONS_TRADING

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

watchlist_counts="$("${compose[@]}" exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -tA -F '|' <<'SQL'
SELECT
  COUNT(*) FILTER (WHERE enabled = TRUE AND COALESCE(ignored, FALSE) = FALSE)::int,
  COUNT(*) FILTER (WHERE enabled = TRUE)::int,
  COUNT(*) FILTER (WHERE enabled = TRUE AND COALESCE(ignored, FALSE) = TRUE)::int
FROM symbol_watchlist
WHERE trading_mode = 'paper';
SQL
)"

IFS='|' read -r entry_watchlist_symbols enabled_watchlist_symbols ignored_watchlist_symbols \
  <<< "$watchlist_counts"

if [[ "${entry_watchlist_symbols:-0}" -lt "$PAPER_READINESS_MIN_WATCHLIST_SYMBOLS" ]]; then
  echo \
    "paper readiness failed: entry watchlist has ${entry_watchlist_symbols:-0} active symbols; expected at least $PAPER_READINESS_MIN_WATCHLIST_SYMBOLS" \
    >&2
  exit 1
fi

echo \
  "paper readiness watchlist ok: active=$entry_watchlist_symbols enabled=$enabled_watchlist_symbols ignored=$ignored_watchlist_symbols"

load_weight_alignment() {
  "${compose[@]}" exec -T postgres psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -tA -F '|' \
    -v strategy_version="$STRATEGY_VERSION" <<'SQL'
WITH active AS (
  SELECT strategy_name
  FROM strategy_flags
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
    AND enabled = TRUE
),
weights AS (
  SELECT strategy_name, weight
  FROM strategy_weights
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
),
summary AS (
  SELECT
    (SELECT COALESCE(array_agg(strategy_name ORDER BY strategy_name), ARRAY[]::text[]) FROM active) AS active_names,
    (SELECT COALESCE(array_agg(strategy_name ORDER BY strategy_name), ARRAY[]::text[]) FROM weights) AS weight_names,
    (SELECT COALESCE(SUM(weight), 0) FROM weights) AS weight_sum,
    (SELECT COUNT(*) FROM weights WHERE weight <= 0) AS nonpositive_weights
)
SELECT
  CASE
    WHEN cardinality(active_names) > 0
     AND active_names = weight_names
     AND nonpositive_weights = 0
     AND ABS(weight_sum - 1.0) < 0.0001
    THEN 'ok'
    ELSE 'mismatch'
  END,
  array_to_string(active_names, ','),
  array_to_string(weight_names, ','),
  ROUND(weight_sum::numeric, 6)
FROM summary;
SQL
}

weight_alignment="$(load_weight_alignment)"
IFS='|' read -r weight_status active_weight_names stored_weight_names stored_weight_sum \
  <<< "$weight_alignment"

if [[ "$weight_status" != "ok" ]]; then
  if [[ "$PAPER_READINESS_AUTO_RESET_WEIGHTS" != "true" ]]; then
    echo \
      "paper readiness failed: strategy weights mismatch active=[$active_weight_names] stored=[$stored_weight_names] sum=${stored_weight_sum:-0}" \
      >&2
    exit 1
  fi

  echo \
    "paper readiness resetting stale strategy weights: active=[$active_weight_names] stored=[$stored_weight_names] sum=${stored_weight_sum:-0}"
  "${compose[@]}" run -T --rm admin reset-weights \
    --mode paper \
    --strategy-version "$STRATEGY_VERSION"

  weight_alignment="$(load_weight_alignment)"
  IFS='|' read -r weight_status active_weight_names stored_weight_names stored_weight_sum \
    <<< "$weight_alignment"
  if [[ "$weight_status" != "ok" ]]; then
    echo \
      "paper readiness failed after weight reset: active=[$active_weight_names] stored=[$stored_weight_names] sum=${stored_weight_sum:-0}" \
      >&2
    exit 1
  fi
fi

echo \
  "paper readiness weights ok: active=[$active_weight_names] stored=[$stored_weight_names] sum=$stored_weight_sum"

confidence_floor_check="$("${compose[@]}" exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -tA -F '|' \
  -v strategy_version="$STRATEGY_VERSION" \
  -v default_floor="${CONFIDENCE_FLOOR:-0.25}" \
  -v min_floor="$PAPER_READINESS_MIN_CONFIDENCE_FLOOR" <<'SQL'
WITH current_floor AS (
  SELECT COALESCE(
    (
      SELECT floor_value
      FROM confidence_floor_store
      WHERE trading_mode = 'paper'
        AND strategy_version = :'strategy_version'
    ),
    (:'default_floor')::double precision
  ) AS floor_value
)
SELECT
  CASE
    WHEN floor_value >= (:'min_floor')::double precision
     AND floor_value <= 1.0
    THEN 'ok'
    ELSE 'mismatch'
  END,
  ROUND(floor_value::numeric, 6)
FROM current_floor;
SQL
)"

IFS='|' read -r confidence_floor_status confidence_floor_value \
  <<< "$confidence_floor_check"

if [[ "$confidence_floor_status" != "ok" ]]; then
  echo \
    "paper readiness failed: confidence_floor=${confidence_floor_value:-unset} expected >= $PAPER_READINESS_MIN_CONFIDENCE_FLOOR and <= 1.0" \
    >&2
  exit 1
fi

echo "paper readiness confidence floor ok: floor=$confidence_floor_value"

if [[ "$PAPER_READINESS_AUTO_RESUME" == "true" ]]; then
  status_line="$("${compose[@]}" run -T --rm admin status \
    --mode paper \
    --strategy-version "$STRATEGY_VERSION")"

  if [[ "$status_line" == *"status=close_only"* ]] \
    && [[ "$status_line" == *"kill_switch=false"* ]]; then
    open_positions="$("${compose[@]}" exec -T postgres psql \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" \
      -tAc "select count(*) from positions where trading_mode = 'paper' and strategy_version = '$STRATEGY_VERSION';" \
      | tr -d '[:space:]')"

    if [[ "$open_positions" == "0" ]]; then
      echo "paper readiness auto-resuming stale close_only state"
      "${compose[@]}" run -T --rm admin resume \
        --mode paper \
        --strategy-version "$STRATEGY_VERSION" \
        --reason "pre-open paper readiness auto-resume"
    else
      echo "paper readiness found close_only with $open_positions open positions; refusing auto-resume" >&2
    fi
  fi
fi

"${compose[@]}" run -T --rm \
  --entrypoint alpaca-bot-ops-check admin \
  --url http://web:8080/healthz \
  --expect-worker \
  --wait-seconds 60 \
  --expect-trading-mode paper \
  --expect-strategy-version "$STRATEGY_VERSION" \
  --expect-trading-status enabled \
  --expect-kill-switch false \
  --expect-only-enabled-strategy bull_flag
