#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
PAPER_READINESS_AUTO_RESUME="${PAPER_READINESS_AUTO_RESUME:-true}"
PAPER_READINESS_AUTO_RESET_WEIGHTS="${PAPER_READINESS_AUTO_RESET_WEIGHTS:-true}"
PAPER_READINESS_REQUIRE_FLAT="${PAPER_READINESS_REQUIRE_FLAT:-true}"
PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED="${PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED:-true}"
PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR="${PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR:-true}"
PAPER_READINESS_REQUIRE_MARKET_DATA="${PAPER_READINESS_REQUIRE_MARKET_DATA:-true}"
PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-}"
PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}"
PAPER_READINESS_MIN_CONFIDENCE_FLOOR="${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.25}"
PAPER_READINESS_DATA_SMOKE_SYMBOLS="${PAPER_READINESS_DATA_SMOKE_SYMBOLS:-SPY,AAPL}"
PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS="${PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS:-10}"

cd "$(dirname "$0")/.."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-${LOSING_STREAK_N:-3}}"

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

if [[ ! "$PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS" =~ ^[0-9]+$ ]] \
  || [[ "$PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS" -lt 1 ]]; then
  echo "PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS must be a positive integer" >&2
  exit 1
fi

if [[ ! "$PAPER_READINESS_LOSING_STREAK_N" =~ ^[0-9]+$ ]] \
  || [[ "$PAPER_READINESS_LOSING_STREAK_N" -lt 1 ]]; then
  echo "LOSING_STREAK_N must be a positive integer" >&2
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

require_env_true_or_unset() {
  local name="$1"
  local actual="${!name:-}"
  if [[ -n "$actual" && "${actual,,}" != "true" ]]; then
    echo "paper readiness failed: $name=$actual expected true or unset" >&2
    exit 1
  fi
}

require_env_value_or_unset() {
  local name="$1"
  local expected="$2"
  local actual="${!name:-}"
  if [[ -n "$actual" && "$actual" != "$expected" ]]; then
    echo "paper readiness failed: $name=$actual expected $expected or unset" >&2
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
require_env_value MARKET_DATA_FEED iex
require_env_value DAILY_SMA_PERIOD 20
require_env_value BREAKOUT_LOOKBACK_BARS 20
require_env_value RELATIVE_VOLUME_LOOKBACK_BARS 20
require_env_value RELATIVE_VOLUME_THRESHOLD 2.0
require_env_value ENTRY_TIMEFRAME_MINUTES 15
require_env_value MAX_OPEN_POSITIONS 3
require_env_value REPLAY_SLIPPAGE_BPS 2.0
require_env_value RISK_PER_TRADE_PCT 0.01
require_env_value MAX_POSITION_PCT 0.05
require_env_value MAX_PORTFOLIO_EXPOSURE_PCT 0.30
require_env_value DAILY_LOSS_LIMIT_PCT 0.01
require_env_value INTRADAY_CONSECUTIVE_LOSS_GATE 0
require_env_value ENTRY_WINDOW_START 10:00
require_env_value ENTRY_WINDOW_END 15:30
require_env_value FLATTEN_TIME 15:45
require_env_true PAPER_PROOF_FREEZE
require_env_true ENABLE_VWAP_ENTRY_FILTER
require_env_true ENABLE_PROFIT_TRAIL
require_env_value PROFIT_TRAIL_PCT 0.95
require_env_true_or_unset ENABLE_BREAKEVEN_STOP
require_env_value_or_unset BREAKEVEN_TRIGGER_PCT 0.0025
require_env_value_or_unset BREAKEVEN_TRAIL_PCT 0.002
require_env_false_or_unset EXTENDED_HOURS_ENABLED
require_env_false_or_unset ENABLE_VIX_FILTER
require_env_false_or_unset ENABLE_SECTOR_FILTER
require_env_false_or_unset ENABLE_REGIME_FILTER
require_env_false_or_unset ENABLE_NEWS_FILTER
require_env_false_or_unset ENABLE_SPREAD_FILTER
require_env_false_or_unset ENABLE_OPTIONS_TRADING

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

run_market_data_smoke_check() {
  "${compose[@]}" run -T --rm \
    -e PAPER_READINESS_DATA_SMOKE_SYMBOLS="$PAPER_READINESS_DATA_SMOKE_SYMBOLS" \
    -e PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS="$PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS" \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import sys

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaMarketDataAdapter

symbols: list[str] = []
for raw_symbol in os.environ.get("PAPER_READINESS_DATA_SMOKE_SYMBOLS", "").split(","):
    symbol = raw_symbol.strip().upper()
    if symbol and symbol not in symbols:
        symbols.append(symbol)

if not symbols:
    print(
        "paper readiness failed: PAPER_READINESS_DATA_SMOKE_SYMBOLS produced no symbols",
        file=sys.stderr,
    )
    raise SystemExit(1)

lookback_days = int(os.environ["PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS"])
settings = Settings.from_env()
adapter = AlpacaMarketDataAdapter.from_settings(settings)
end = datetime.now(timezone.utc)
start = end - timedelta(days=lookback_days)

try:
    bars_by_symbol = adapter.get_daily_bars(symbols=symbols, start=start, end=end)
except Exception as exc:
    print(
        "paper readiness failed: market data daily-bars smoke failed "
        f"for {','.join(symbols)}: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

bar_counts = {
    symbol: len(bars_by_symbol.get(symbol, []))
    for symbol in symbols
}

if not any(bar_counts.values()):
    print(
        "paper readiness failed: market data daily-bars smoke returned no bars "
        f"for {','.join(symbols)} over {lookback_days} days",
        file=sys.stderr,
    )
    raise SystemExit(1)

summary = ",".join(f"{symbol}:{bar_counts[symbol]}" for symbol in symbols)
print(
    "paper readiness market data ok: "
    f"daily_bars={summary} feed={settings.market_data_feed.value} "
    f"lookback_days={lookback_days}"
)
PY
}

if [[ "${PAPER_READINESS_REQUIRE_MARKET_DATA,,}" == "true" ]]; then
  run_market_data_smoke_check
else
  echo "paper readiness market data check skipped"
fi

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

load_stock_exposure_counts() {
  "${compose[@]}" exec -T postgres psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -tA -F '|' \
    -v strategy_version="$STRATEGY_VERSION" <<'SQL'
SELECT
  (
    SELECT COUNT(*)::int
    FROM positions
    WHERE trading_mode = 'paper'
      AND strategy_version = :'strategy_version'
  ),
  (
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
  );
SQL
}

open_option_positions="$("${compose[@]}" exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -tA \
  -v strategy_version="$STRATEGY_VERSION" <<'SQL'
WITH filled AS (
  SELECT
    strategy_name,
    occ_symbol,
    COALESCE(filled_quantity, quantity) AS fill_qty,
    side
  FROM option_orders
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
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
SELECT COUNT(*)::int FROM net;
SQL
)"
open_option_positions="$(echo "$open_option_positions" | tr -d '[:space:]')"

if [[ "${open_option_positions:-0}" != "0" ]]; then
  echo \
    "paper readiness failed: stock-only proof has $open_option_positions net-open option positions" \
    >&2
  exit 1
fi

echo "paper readiness option positions ok: net_open=0"

if [[ "$PAPER_READINESS_AUTO_RESUME" == "true" ]]; then
  status_line="$("${compose[@]}" run -T --rm admin status \
    --mode paper \
    --strategy-version "$STRATEGY_VERSION")"

  if [[ "$status_line" == *"status=close_only"* ]] \
    && [[ "$status_line" == *"kill_switch=false"* ]]; then
    if [[ "$status_line" == *"paper proof failed"* ]] \
      || [[ "$status_line" == *"session guard failed"* ]]; then
      echo "paper readiness refusing auto-resume after failed proof guard: $status_line" >&2
      exit 1
    fi

    stock_exposure_counts="$(load_stock_exposure_counts)"
    IFS='|' read -r open_positions active_orders <<< "$stock_exposure_counts"

    if [[ "$open_positions" == "0" && "$active_orders" == "0" ]]; then
      BROKER_FLAT_CONTEXT="paper readiness" ./scripts/broker_flat_check.sh "$ENV_FILE"
      echo "paper readiness auto-resuming stale close_only state"
      "${compose[@]}" run -T --rm admin resume \
        --mode paper \
        --strategy-version "$STRATEGY_VERSION" \
        --reason "pre-open paper readiness auto-resume"
    elif [[ "$open_positions" != "0" ]]; then
      echo "paper readiness found close_only with $open_positions open positions; refusing auto-resume" >&2
    else
      echo "paper readiness found close_only with $active_orders active orders; refusing auto-resume" >&2
    fi
  fi
fi

if [[ "${PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED,,}" == "true" ]]; then
  session_entry_blocks="$("${compose[@]}" exec -T postgres psql \
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
blocked AS (
  SELECT COALESCE(strategy_name, '_global') AS strategy_name
  FROM daily_session_state
  WHERE session_date = (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')::date
    AND trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
    AND entries_disabled = TRUE
    AND (
      COALESCE(strategy_name, '_global') IN ('_global', '_equity')
      OR strategy_name IN (SELECT strategy_name FROM active)
    )
)
SELECT COUNT(*)::int, COALESCE(string_agg(strategy_name, ',' ORDER BY strategy_name), '')
FROM blocked;
SQL
)"
  IFS='|' read -r blocked_session_state_count blocked_session_state_names \
    <<< "$session_entry_blocks"

  if [[ "${blocked_session_state_count:-0}" != "0" ]]; then
    echo \
      "paper readiness failed: current session has entry-blocking state for [$blocked_session_state_names]" \
      >&2
    exit 1
  fi

  echo "paper readiness session entry blocks ok: blocked=0"
else
  echo "paper readiness session entry block check skipped"
fi

if [[ "${PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR,,}" == "true" ]]; then
  losing_streak_blocks="$("${compose[@]}" exec -T postgres psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -tA -F '|' \
    -v strategy_version="$STRATEGY_VERSION" \
    -v losing_streak_n="$PAPER_READINESS_LOSING_STREAK_N" <<'SQL'
WITH active AS (
  SELECT strategy_name
  FROM strategy_flags
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
    AND enabled = TRUE
),
trade_pnl AS (
  SELECT
    x.strategy_name,
    DATE(x.updated_at AT TIME ZONE 'America/New_York') AS exit_date,
    (x.fill_price - e.entry_fill)
      * COALESCE(x.filled_quantity, x.quantity) AS pnl
  FROM orders x
  JOIN LATERAL (
    SELECT e.fill_price AS entry_fill
    FROM orders e
    WHERE e.symbol = x.symbol
      AND e.trading_mode = x.trading_mode
      AND e.strategy_version = x.strategy_version
      AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
      AND e.intent_type = 'entry'
      AND e.fill_price IS NOT NULL
      AND e.status = 'filled'
      AND e.updated_at <= x.updated_at
      AND DATE(e.updated_at AT TIME ZONE 'America/New_York')
        = DATE(x.updated_at AT TIME ZONE 'America/New_York')
    ORDER BY e.updated_at DESC
    LIMIT 1
  ) e ON TRUE
  WHERE x.trading_mode = 'paper'
    AND x.strategy_version = :'strategy_version'
    AND x.strategy_name IN (SELECT strategy_name FROM active)
    AND x.intent_type IN ('stop', 'exit')
    AND x.fill_price IS NOT NULL
    AND x.status = 'filled'
    AND DATE(x.updated_at AT TIME ZONE 'America/New_York')
      <= ((CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')::date - 1)
),
daily_pnl AS (
  SELECT strategy_name, exit_date, SUM(pnl) AS day_pnl
  FROM trade_pnl
  GROUP BY strategy_name, exit_date
),
ranked AS (
  SELECT
    strategy_name,
    exit_date,
    day_pnl,
    COUNT(*) FILTER (WHERE day_pnl >= 0) OVER (
      PARTITION BY strategy_name
      ORDER BY exit_date DESC
      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS non_loss_days_newer
  FROM daily_pnl
),
streaks AS (
  SELECT
    a.strategy_name,
    COUNT(r.*) FILTER (
      WHERE r.day_pnl < 0
        AND COALESCE(r.non_loss_days_newer, 0) = 0
    )::int AS losing_streak
  FROM active a
  LEFT JOIN ranked r ON r.strategy_name = a.strategy_name
  GROUP BY a.strategy_name
),
blocked AS (
  SELECT strategy_name, losing_streak
  FROM streaks
  WHERE losing_streak >= (:'losing_streak_n')::int
)
SELECT
  COUNT(*)::int,
  COALESCE(string_agg(strategy_name || ':' || losing_streak::text, ',' ORDER BY strategy_name), '')
FROM blocked;
SQL
)"
  IFS='|' read -r losing_streak_block_count losing_streak_block_names \
    <<< "$losing_streak_blocks"

  if [[ "${losing_streak_block_count:-0}" != "0" ]]; then
    echo \
      "paper readiness failed: active strategies at losing-streak gate [$losing_streak_block_names] threshold=$PAPER_READINESS_LOSING_STREAK_N" \
      >&2
    exit 1
  fi

  echo "paper readiness losing streak gate ok: blocked=0 threshold=$PAPER_READINESS_LOSING_STREAK_N"
else
  echo "paper readiness losing streak gate check skipped"
fi

if [[ "${PAPER_READINESS_REQUIRE_FLAT,,}" == "true" ]]; then
  stock_exposure_counts="$(load_stock_exposure_counts)"
  IFS='|' read -r open_positions active_orders <<< "$stock_exposure_counts"

  if [[ "${open_positions:-0}" != "0" ]]; then
    echo "paper readiness failed: stock-only proof has $open_positions open stock positions" >&2
    exit 1
  fi

  if [[ "${active_orders:-0}" != "0" ]]; then
    echo "paper readiness failed: stock-only proof has $active_orders active stock orders" >&2
    exit 1
  fi

  echo "paper readiness stock exposure ok: positions=0 active_orders=0"

  BROKER_FLAT_CONTEXT="paper readiness" ./scripts/broker_flat_check.sh "$ENV_FILE"
else
  echo "paper readiness flat exposure check skipped"
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
