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
  PAPER_READINESS_AUTO_RESUME \
  PAPER_READINESS_AUTO_RESET_WEIGHTS \
  PAPER_READINESS_REQUIRE_FLAT \
  PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED \
  PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR \
  PAPER_READINESS_REQUIRE_MARKET_DATA \
  PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE \
  PAPER_READINESS_REQUIRE_DECISION_DRY_RUN \
  PAPER_READINESS_REQUIRE_WATCHLIST_ASSETS \
  PAPER_READINESS_REQUIRE_SCENARIOS \
  PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS \
  PAPER_READINESS_CLOSE_ONLY_ON_FAILURE \
  PAPER_READINESS_PRIOR_PROOF_START_DATE \
  PAPER_READINESS_LOSING_STREAK_N \
  PAPER_READINESS_MIN_WATCHLIST_SYMBOLS \
  PAPER_READINESS_MIN_CONFIDENCE_FLOOR \
  PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS \
  PAPER_READINESS_DATA_SMOKE_SYMBOLS \
  PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS \
  PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS \
  PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED \
  PAPER_READINESS_DECISION_DRY_RUN_STRATEGY \
  PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES \
  PAPER_READINESS_SCENARIO_DIR \
  PAPER_READINESS_PREVIOUS_SESSION_DATE \
  PAPER_READINESS_SESSION_DATE

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

PAPER_READINESS_AUTO_RESUME="${PAPER_READINESS_AUTO_RESUME:-true}"
PAPER_READINESS_AUTO_RESET_WEIGHTS="${PAPER_READINESS_AUTO_RESET_WEIGHTS:-true}"
PAPER_READINESS_REQUIRE_FLAT="${PAPER_READINESS_REQUIRE_FLAT:-true}"
PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED="${PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED:-true}"
PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR="${PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR:-true}"
PAPER_READINESS_REQUIRE_MARKET_DATA="${PAPER_READINESS_REQUIRE_MARKET_DATA:-true}"
PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE="${PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE:-true}"
PAPER_READINESS_REQUIRE_DECISION_DRY_RUN="${PAPER_READINESS_REQUIRE_DECISION_DRY_RUN:-true}"
PAPER_READINESS_REQUIRE_WATCHLIST_ASSETS="${PAPER_READINESS_REQUIRE_WATCHLIST_ASSETS:-true}"
PAPER_READINESS_REQUIRE_SCENARIOS="${PAPER_READINESS_REQUIRE_SCENARIOS:-true}"
PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS="${PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS:-true}"
PAPER_READINESS_CLOSE_ONLY_ON_FAILURE="${PAPER_READINESS_CLOSE_ONLY_ON_FAILURE:-true}"
PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="${PAPER_READINESS_MIN_WATCHLIST_SYMBOLS:-900}"
PAPER_READINESS_MIN_CONFIDENCE_FLOOR="${PAPER_READINESS_MIN_CONFIDENCE_FLOOR:-0.25}"
PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS="${PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS:-0}"
PAPER_READINESS_DATA_SMOKE_SYMBOLS="${PAPER_READINESS_DATA_SMOKE_SYMBOLS:-SPY,AAPL}"
PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS="${PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS:-10}"
PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS="${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-900}"
PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED="${PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-true}"
PAPER_READINESS_DECISION_DRY_RUN_STRATEGY="${PAPER_READINESS_DECISION_DRY_RUN_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}"
PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES="${PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES:-10:30,11:30,12:30,13:30,14:30,15:30}"
PAPER_READINESS_SCENARIO_DIR="${PAPER_READINESS_SCENARIO_DIR:-/var/lib/alpaca-bot/nightly/scenarios}"
PAPER_READINESS_PRIOR_PROOF_START_DATE="${PAPER_READINESS_PRIOR_PROOF_START_DATE:-}"
PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-}"
PAPER_READINESS_LOSING_STREAK_N="${PAPER_READINESS_LOSING_STREAK_N:-${LOSING_STREAK_N:-3}}"
PAPER_READINESS_PRIOR_PROOF_START_DATE="${PAPER_READINESS_PRIOR_PROOF_START_DATE:-${PROFIT_PROBE_START_DATE:-2026-06-29}}"

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "paper readiness check skipped for TRADING_MODE=${TRADING_MODE:-unset}"
  exit 0
fi

case "${PAPER_READINESS_CLOSE_ONLY_ON_FAILURE,,}" in
  true|false) ;;
  *)
    echo "PAPER_READINESS_CLOSE_ONLY_ON_FAILURE must be true or false" >&2
    exit 1
    ;;
esac

case "${PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE,,}" in
  true|false) ;;
  *)
    echo "PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE must be true or false" >&2
    exit 1
    ;;
esac

case "${PAPER_READINESS_REQUIRE_DECISION_DRY_RUN,,}" in
  true|false) ;;
  *)
    echo "PAPER_READINESS_REQUIRE_DECISION_DRY_RUN must be true or false" >&2
    exit 1
    ;;
esac

case "${PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED,,}" in
  true|false) ;;
  *)
    echo "PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED must be true or false" >&2
    exit 1
    ;;
esac

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

fallback_readiness_session_date() {
  local dow
  dow="$(TZ=America/New_York date +%u)"
  case "$dow" in
    6) TZ=America/New_York date -d "2 days" +%F ;;
    7) TZ=America/New_York date -d "1 day" +%F ;;
    *) TZ=America/New_York date +%F ;;
  esac
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

load_readiness_session_date() {
  local calendar_date
  if calendar_date="$("${compose[@]}" run -T --rm \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter

settings = Settings.from_env()
market_timezone = ZoneInfo(settings.market_timezone.key)
today = datetime.now(market_timezone).date()
calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
    start=today,
    end=today + timedelta(days=10),
)
for session in calendar:
    if session.session_date >= today:
        print(session.session_date.isoformat())
        break
else:
    raise SystemExit("no upcoming market session found")
PY
  )" && [[ "$calendar_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "$calendar_date"
    return
  fi

  echo \
    "paper readiness warning: market calendar lookup failed; using weekday fallback" \
    >&2
  fallback_readiness_session_date
}

PAPER_READINESS_SESSION_DATE="${PAPER_READINESS_SESSION_DATE:-$(load_readiness_session_date)}"
if [[ ! "$PAPER_READINESS_SESSION_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "PAPER_READINESS_SESSION_DATE must use YYYY-MM-DD" >&2
  exit 1
fi

fallback_previous_session_date() {
  local target_date="$1"
  local dow
  dow="$(TZ=America/New_York date -d "$target_date" +%u)"
  case "$dow" in
    1) TZ=America/New_York date -d "$target_date - 3 days" +%F ;;
    *) TZ=America/New_York date -d "$target_date - 1 day" +%F ;;
  esac
}

load_previous_session_date() {
  local previous_date
  if previous_date="$(PAPER_READINESS_SESSION_DATE="$PAPER_READINESS_SESSION_DATE" \
    "${compose[@]}" run -T --rm \
    -e PAPER_READINESS_SESSION_DATE="$PAPER_READINESS_SESSION_DATE" \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from datetime import date, timedelta
import os

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter

target_date = date.fromisoformat(os.environ["PAPER_READINESS_SESSION_DATE"])
settings = Settings.from_env()
calendar = AlpacaExecutionAdapter.from_settings(settings).get_market_calendar(
    start=target_date - timedelta(days=14),
    end=target_date,
)
previous = [
    session.session_date
    for session in calendar
    if session.session_date < target_date
]
if not previous:
    raise SystemExit("no previous market session found")
print(max(previous).isoformat())
PY
  )" && [[ "$previous_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "$previous_date"
    return
  fi

  echo \
    "paper readiness warning: previous market session lookup failed; using weekday fallback" \
    >&2
  fallback_previous_session_date "$PAPER_READINESS_SESSION_DATE"
}

PAPER_READINESS_PREVIOUS_SESSION_DATE="${PAPER_READINESS_PREVIOUS_SESSION_DATE:-$(load_previous_session_date)}"
if [[ ! "$PAPER_READINESS_PREVIOUS_SESSION_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "PAPER_READINESS_PREVIOUS_SESSION_DATE must use YYYY-MM-DD" >&2
  exit 1
fi

echo "scheduled check context: session_date=$PAPER_READINESS_SESSION_DATE previous_session_date=$PAPER_READINESS_PREVIOUS_SESSION_DATE proof_start=$PAPER_READINESS_PRIOR_PROOF_START_DATE"

close_only_on_readiness_failure() {
  local rc="$?"
  trap - EXIT

  if [[ "$rc" -eq 0 ]]; then
    exit 0
  fi

  if [[ "${PAPER_READINESS_CLOSE_ONLY_ON_FAILURE,,}" != "true" ]]; then
    exit "$rc"
  fi

  local reason="paper readiness failed for session ${PAPER_READINESS_SESSION_DATE:-unknown}: pre-open checks failed"
  if ! "${compose[@]}" run -T --rm admin \
    close-only \
    --mode paper \
    --strategy-version "${STRATEGY_VERSION:-v1-breakout}" \
    --reason "$reason"; then
    echo "paper readiness warning: failed to apply close-only after readiness failure" >&2
  fi

  exit "$rc"
}

trap close_only_on_readiness_failure EXIT

if [[ ! "$PAPER_READINESS_MIN_WATCHLIST_SYMBOLS" =~ ^[0-9]+$ ]] \
  || [[ "$PAPER_READINESS_MIN_WATCHLIST_SYMBOLS" -lt 1 ]]; then
  echo "PAPER_READINESS_MIN_WATCHLIST_SYMBOLS must be a positive integer" >&2
  exit 1
fi

if [[ ! "$PAPER_READINESS_MIN_CONFIDENCE_FLOOR" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "PAPER_READINESS_MIN_CONFIDENCE_FLOOR must be a non-negative number" >&2
  exit 1
fi

if [[ ! "$PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS" =~ ^[0-9]+$ ]]; then
  echo "PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS must be a non-negative integer" >&2
  exit 1
fi

if [[ ! "$PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS" =~ ^[0-9]+$ ]] \
  || [[ "$PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS" -lt 1 ]]; then
  echo "PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS must be a positive integer" >&2
  exit 1
fi

if [[ ! "$PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS" =~ ^[0-9]+$ ]]; then
  echo "PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" >&2
  exit 1
fi

if [[ ! "$PAPER_READINESS_PRIOR_PROOF_START_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "PAPER_READINESS_PRIOR_PROOF_START_DATE must be YYYY-MM-DD" >&2
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

require_env_empty_or_unset() {
  local name="$1"
  local actual="${!name:-}"
  if [[ -n "$actual" ]]; then
    echo "paper readiness failed: $name=$actual expected empty or unset" >&2
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
require_env_value MAX_OPEN_POSITIONS 4
require_env_value REPLAY_SLIPPAGE_BPS 2.0
require_env_value RISK_PER_TRADE_PCT 0.01
require_env_value MAX_POSITION_PCT 0.05
require_env_value MAX_LOSS_PER_TRADE_DOLLARS 20.0
require_env_value MAX_PORTFOLIO_EXPOSURE_PCT 0.30
require_env_value DAILY_LOSS_LIMIT_PCT 0.01
require_env_value STOP_LIMIT_BUFFER_PCT 0.0005
require_env_value ENTRY_STOP_PRICE_BUFFER 0.02
require_env_value_or_unset ATR_PERIOD 20
require_env_value_or_unset ATR_STOP_MULTIPLIER 1.0
require_env_value TRAILING_STOP_ATR_MULTIPLIER 1.0
require_env_value_or_unset TRAILING_STOP_PROFIT_TRIGGER_R 1.0
require_env_value_or_unset BULL_FLAG_MIN_RUN_PCT 0.02
require_env_value_or_unset BULL_FLAG_CONSOLIDATION_VOLUME_RATIO 0.6
require_env_value_or_unset BULL_FLAG_CONSOLIDATION_RANGE_PCT 0.5
require_env_value INTRADAY_CONSECUTIVE_LOSS_GATE 0
require_env_value ENTRY_WINDOW_START 10:00
require_env_value ENTRY_WINDOW_END 15:30
require_env_value FLATTEN_TIME 15:45
require_env_true PAPER_PROOF_FREEZE
require_env_false_or_unset ENABLE_VWAP_ENTRY_FILTER
require_env_true ENABLE_PROFIT_TRAIL
require_env_value PROFIT_TRAIL_PCT 0.90
require_env_true ENABLE_PROFIT_TARGET
require_env_value PROFIT_TARGET_R 3.0
require_env_true_or_unset ENABLE_BREAKEVEN_STOP
require_env_value BREAKEVEN_TRIGGER_PCT 0.005
require_env_value_or_unset BREAKEVEN_TRAIL_PCT 0.002
require_env_false_or_unset EXTENDED_HOURS_ENABLED
require_env_false_or_unset ENABLE_VIX_FILTER
require_env_false_or_unset ENABLE_SECTOR_FILTER
require_env_false_or_unset ENABLE_REGIME_FILTER
require_env_false_or_unset ENABLE_NEWS_FILTER
require_env_false_or_unset ENABLE_SPREAD_FILTER
require_env_false_or_unset ENABLE_OPTIONS_TRADING
require_env_empty_or_unset OPTION_CHAIN_SYMBOLS

run_container_settings_posture_check() {
  "${compose[@]}" run -T --rm \
    --entrypoint python admin <<'PY'
from __future__ import annotations

from datetime import time
from math import isclose
import sys

from alpaca_bot.config import Settings

settings = Settings.from_env()
errors: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    if isinstance(expected, float):
        try:
            ok = isclose(float(actual), expected, rel_tol=0.0, abs_tol=1e-9)
        except (TypeError, ValueError):
            ok = False
    else:
        ok = actual == expected
    if not ok:
        errors.append(f"{name}={actual!r} expected {expected!r}")


def as_hhmm(value: time) -> str:
    return value.strftime("%H:%M")


check("strategy_version", settings.strategy_version, "v1-breakout")
check("market_data_feed", settings.market_data_feed.value, "iex")
check("daily_sma_period", settings.daily_sma_period, 20)
check("breakout_lookback_bars", settings.breakout_lookback_bars, 20)
check("relative_volume_lookback_bars", settings.relative_volume_lookback_bars, 20)
check("relative_volume_threshold", settings.relative_volume_threshold, 2.0)
check("entry_timeframe_minutes", settings.entry_timeframe_minutes, 15)
check("risk_per_trade_pct", settings.risk_per_trade_pct, 0.01)
check("max_position_pct", settings.max_position_pct, 0.05)
check("max_open_positions", settings.max_open_positions, 4)
check("max_portfolio_exposure_pct", settings.max_portfolio_exposure_pct, 0.30)
check("daily_loss_limit_pct", settings.daily_loss_limit_pct, 0.01)
check("stop_limit_buffer_pct", settings.stop_limit_buffer_pct, 0.0005)
check("entry_stop_price_buffer", settings.entry_stop_price_buffer, 0.02)
check("atr_period", settings.atr_period, 20)
check("atr_stop_multiplier", settings.atr_stop_multiplier, 1.0)
check("trailing_stop_atr_multiplier", settings.trailing_stop_atr_multiplier, 1.0)
check("trailing_stop_profit_trigger_r", settings.trailing_stop_profit_trigger_r, 1.0)
check("bull_flag_min_run_pct", settings.bull_flag_min_run_pct, 0.02)
check("bull_flag_consolidation_volume_ratio", settings.bull_flag_consolidation_volume_ratio, 0.6)
check("bull_flag_consolidation_range_pct", settings.bull_flag_consolidation_range_pct, 0.5)
check("entry_window_start", as_hhmm(settings.entry_window_start), "10:00")
check("entry_window_end", as_hhmm(settings.entry_window_end), "15:30")
check("flatten_time", as_hhmm(settings.flatten_time), "15:45")
check("enable_profit_trail", settings.enable_profit_trail, True)
check("profit_trail_pct", settings.profit_trail_pct, 0.90)
check("paper_proof_freeze", settings.paper_proof_freeze, True)
check("enable_profit_target", settings.enable_profit_target, True)
check("profit_target_r", settings.profit_target_r, 3.0)
check("enable_breakeven_stop", settings.enable_breakeven_stop, True)
check("breakeven_trigger_pct", settings.breakeven_trigger_pct, 0.005)
check("breakeven_trail_pct", settings.breakeven_trail_pct, 0.002)
check("enable_vwap_entry_filter", settings.enable_vwap_entry_filter, False)
check("enable_vix_filter", settings.enable_vix_filter, False)
check("enable_sector_filter", settings.enable_sector_filter, False)
check("enable_regime_filter", settings.enable_regime_filter, False)
check("enable_news_filter", settings.enable_news_filter, False)
check("enable_spread_filter", settings.enable_spread_filter, False)
check("enable_options_trading", settings.enable_options_trading, False)
check("option_chain_symbols", settings.option_chain_symbols, ())
check("extended_hours_enabled", settings.extended_hours_enabled, False)
check("enable_trend_filter_exit", settings.enable_trend_filter_exit, False)
check("enable_vwap_breakdown_exit", settings.enable_vwap_breakdown_exit, False)
check("per_symbol_loss_limit_pct", settings.per_symbol_loss_limit_pct, 0.0)
check("min_position_notional", settings.min_position_notional, 0.0)
check("max_stop_pct", settings.max_stop_pct, 0.05)
check("viability_daily_bar_max_age_days", settings.viability_daily_bar_max_age_days, 5)
check("viability_min_hold_minutes", settings.viability_min_hold_minutes, 0)
check("max_loss_per_trade_dollars", settings.max_loss_per_trade_dollars, 20.0)
check("intraday_consecutive_loss_gate", settings.intraday_consecutive_loss_gate, 0)
check("replay_slippage_bps", settings.replay_slippage_bps, 2.0)

if errors:
    print("paper readiness failed: container Settings posture drift:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    raise SystemExit(1)

print("paper readiness container Settings ok")
PY
}

run_container_settings_posture_check

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

try:
    intraday_bars_by_symbol = adapter.get_stock_bars(
        symbols=symbols,
        start=start,
        end=end,
        timeframe_minutes=settings.entry_timeframe_minutes,
    )
except Exception as exc:
    print(
        "paper readiness failed: market data intraday-bars smoke failed "
        f"for {','.join(symbols)}: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

bar_counts = {
    symbol: len(bars_by_symbol.get(symbol, []))
    for symbol in symbols
}
intraday_bar_counts = {
    symbol: len(intraday_bars_by_symbol.get(symbol, []))
    for symbol in symbols
}

if not any(bar_counts.values()):
    print(
        "paper readiness failed: market data daily-bars smoke returned no bars "
        f"for {','.join(symbols)} over {lookback_days} days",
        file=sys.stderr,
    )
    raise SystemExit(1)

if not any(intraday_bar_counts.values()):
    print(
        "paper readiness failed: market data intraday-bars smoke returned no bars "
        f"for {','.join(symbols)} over {lookback_days} days",
        file=sys.stderr,
    )
    raise SystemExit(1)

daily_summary = ",".join(f"{symbol}:{bar_counts[symbol]}" for symbol in symbols)
intraday_summary = ",".join(
    f"{symbol}:{intraday_bar_counts[symbol]}" for symbol in symbols
)
print(
    "paper readiness market data ok: "
    f"daily_bars={daily_summary} intraday_bars={intraday_summary} "
    f"feed={settings.market_data_feed.value} "
    f"timeframe_minutes={settings.entry_timeframe_minutes} "
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

load_active_watchlist_symbols() {
  "${compose[@]}" exec -T postgres psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -tA <<'SQL'
SELECT symbol
FROM symbol_watchlist
WHERE trading_mode = 'paper'
  AND enabled = TRUE
  AND COALESCE(ignored, FALSE) = FALSE
ORDER BY symbol;
SQL
}

run_active_data_coverage_check() {
  local active_symbols
  active_symbols="$(load_active_watchlist_symbols)"

  "${compose[@]}" run -T --rm \
      -e PAPER_READINESS_ACTIVE_SYMBOLS="$active_symbols" \
      -e PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS="$PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS" \
      -e PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS="$PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS" \
      --entrypoint python admin <<'PY'
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import sys

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaMarketDataAdapter

symbols = tuple(
    dict.fromkeys(
        symbol.strip().upper()
        for symbol in os.environ.get("PAPER_READINESS_ACTIVE_SYMBOLS", "").splitlines()
        if symbol.strip()
    )
)
if not symbols:
    print(
        "paper readiness failed: active watchlist produced no symbols for data coverage check",
        file=sys.stderr,
    )
    raise SystemExit(1)

lookback_days = int(os.environ["PAPER_READINESS_DATA_SMOKE_LOOKBACK_DAYS"])
max_missing = int(os.environ["PAPER_READINESS_ACTIVE_DATA_MAX_MISSING_SYMBOLS"])
settings = Settings.from_env()
adapter = AlpacaMarketDataAdapter.from_settings(settings)
end = datetime.now(timezone.utc)
start = end - timedelta(days=lookback_days)

try:
    daily_by_symbol = adapter.get_daily_bars(symbols=symbols, start=start, end=end)
    intraday_by_symbol = adapter.get_stock_bars(
        symbols=symbols,
        start=start,
        end=end,
        timeframe_minutes=settings.entry_timeframe_minutes,
    )
except Exception as exc:
    print(
        "paper readiness failed: active watchlist market data coverage lookup failed: "
        f"{exc}",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

daily_counts = {symbol: len(daily_by_symbol.get(symbol, [])) for symbol in symbols}
intraday_counts = {symbol: len(intraday_by_symbol.get(symbol, [])) for symbol in symbols}
missing_daily = [symbol for symbol, count in daily_counts.items() if count <= 0]
missing_intraday = [symbol for symbol, count in intraday_counts.items() if count <= 0]
missing_symbols = sorted(set(missing_daily) | set(missing_intraday))
thin_intraday = [
    f"{symbol}:{count}"
    for symbol, count in intraday_counts.items()
    if 0 < count < 20
]

if len(missing_symbols) > max_missing:
    print(
        "paper readiness failed: active watchlist market data coverage below threshold "
        f"active={len(symbols)} missing={len(missing_symbols)} max_missing={max_missing}",
        file=sys.stderr,
    )
    if missing_daily:
        print("  missing_daily: " + ",".join(missing_daily[:50]), file=sys.stderr)
    if missing_intraday:
        print("  missing_intraday: " + ",".join(missing_intraday[:50]), file=sys.stderr)
    raise SystemExit(1)

print(
    "paper readiness active data coverage ok: "
    f"active={len(symbols)} "
    f"daily={len(symbols) - len(missing_daily)}/{len(symbols)} "
    f"intraday={len(symbols) - len(missing_intraday)}/{len(symbols)} "
    f"missing={len(missing_symbols)} max_missing={max_missing} "
    f"thin_intraday_lt20={len(thin_intraday)} "
    f"feed={settings.market_data_feed.value} "
    f"timeframe_minutes={settings.entry_timeframe_minutes} "
    f"lookback_days={lookback_days}"
)
if thin_intraday:
    print(
        "paper readiness active data thin intraday symbols: "
        + ",".join(thin_intraday[:50])
    )
PY
}

if [[ "${PAPER_READINESS_REQUIRE_ACTIVE_DATA_COVERAGE,,}" == "true" ]]; then
  run_active_data_coverage_check
else
  echo "paper readiness active data coverage check skipped"
fi

run_watchlist_asset_check() {
  local active_symbols
  active_symbols="$(load_active_watchlist_symbols)"

  "${compose[@]}" run -T --rm \
      -e PAPER_READINESS_ACTIVE_SYMBOLS="$active_symbols" \
      --entrypoint python admin <<'PY'
from __future__ import annotations

import os
import sys

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import (
    AlpacaExecutionAdapter,
    _asset_field,
    _retry_with_backoff,
)

try:
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest
except ModuleNotFoundError:
    asset_filter = {"status": "active", "asset_class": "us_equity"}
else:
    asset_filter = GetAssetsRequest(
        status=AssetStatus.ACTIVE,
        asset_class=AssetClass.US_EQUITY,
    )

symbols = tuple(
    dict.fromkeys(
        symbol.strip().upper()
        for symbol in os.environ.get("PAPER_READINESS_ACTIVE_SYMBOLS", "").splitlines()
        if symbol.strip()
    )
)
if not symbols:
    print(
        "paper readiness failed: active watchlist produced no symbols for Alpaca asset check",
        file=sys.stderr,
    )
    raise SystemExit(1)

settings = Settings.from_env()
adapter = AlpacaExecutionAdapter.from_settings(settings)
try:
    raw_assets = _retry_with_backoff(
        lambda: adapter._trading.get_all_assets(filter=asset_filter)
    )
except Exception as exc:
    print(
        f"paper readiness failed: watchlist Alpaca asset lookup failed: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1)

assets_by_symbol = {
    str(_asset_field(asset, "symbol") or "").upper(): asset
    for asset in raw_assets
}
missing_active_asset = [
    symbol for symbol in symbols if symbol not in assets_by_symbol
]
not_tradable = [
    symbol
    for symbol in symbols
    if symbol in assets_by_symbol
    and not bool(_asset_field(assets_by_symbol[symbol], "tradable"))
]
fractionable = [
    symbol
    for symbol in symbols
    if symbol in assets_by_symbol
    and bool(_asset_field(assets_by_symbol[symbol], "fractionable"))
]
non_fractionable = [
    symbol
    for symbol in symbols
    if symbol in assets_by_symbol
    and not bool(_asset_field(assets_by_symbol[symbol], "fractionable"))
]

if missing_active_asset or not_tradable:
    print(
        "paper readiness failed: watchlist contains Alpaca-ineligible symbols "
        f"active={len(symbols)} "
        f"missing_active_asset={len(missing_active_asset)} "
        f"not_tradable={len(not_tradable)}",
        file=sys.stderr,
    )
    if missing_active_asset:
        print(
            "  missing_active_asset: " + ",".join(missing_active_asset[:50]),
            file=sys.stderr,
        )
    if not_tradable:
        print(
            "  not_tradable: " + ",".join(not_tradable[:50]),
            file=sys.stderr,
        )
    raise SystemExit(1)

print(
    "paper readiness Alpaca assets ok: "
    f"active={len(symbols)} tradable={len(symbols)} "
    f"fractionable={len(fractionable)} non_fractionable={len(non_fractionable)}"
)
if non_fractionable:
    print(
        "paper readiness Alpaca non-fractionable symbols: "
        + ",".join(non_fractionable[:50])
    )
PY
}

if [[ "${PAPER_READINESS_REQUIRE_WATCHLIST_ASSETS,,}" == "true" ]]; then
  run_watchlist_asset_check
else
  echo "paper readiness watchlist Alpaca asset check skipped"
fi

run_decision_dry_run_check() {
  PAPER_DECISION_DRY_RUN_STRATEGY="$PAPER_READINESS_DECISION_DRY_RUN_STRATEGY" \
  PAPER_DECISION_DRY_RUN_MIN_RECORDS="$PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS" \
  PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED="$PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED" \
  PAPER_DECISION_DRY_RUN_SAMPLE_TIMES="$PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES" \
    ./scripts/paper_decision_dry_run.sh "$ENV_FILE"
}

if [[ "${PAPER_READINESS_REQUIRE_DECISION_DRY_RUN,,}" == "true" ]]; then
  run_decision_dry_run_check
else
  echo "paper readiness decision dry run check skipped"
fi

run_scenario_freshness_check() {
  if [[ ! -d "$PAPER_READINESS_SCENARIO_DIR" ]]; then
    echo "paper readiness failed: scenario directory missing: $PAPER_READINESS_SCENARIO_DIR" >&2
    exit 1
  fi

  local active_symbols
  active_symbols="$(load_active_watchlist_symbols)"

  PAPER_READINESS_ACTIVE_SYMBOLS="$active_symbols" \
  PAPER_READINESS_EXPECTED_SCENARIO_DATE="$PAPER_READINESS_PREVIOUS_SESSION_DATE" \
  PAPER_READINESS_SCENARIO_DIR="$PAPER_READINESS_SCENARIO_DIR" \
    python3 <<'PY'
from __future__ import annotations

from datetime import date, datetime
import json
import os
from pathlib import Path
import sys

symbols = [
    line.strip().upper()
    for line in os.environ.get("PAPER_READINESS_ACTIVE_SYMBOLS", "").splitlines()
    if line.strip()
]
scenario_dir = Path(os.environ["PAPER_READINESS_SCENARIO_DIR"])
expected_date = date.fromisoformat(os.environ["PAPER_READINESS_EXPECTED_SCENARIO_DATE"])


def parse_bar_date(raw: str) -> date:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw).date()


missing: list[str] = []
empty_daily: list[str] = []
empty_intraday: list[str] = []
stale_daily: list[str] = []
stale_intraday: list[str] = []

for symbol in symbols:
    path = scenario_dir / f"{symbol}_252d.json"
    if not path.exists():
        missing.append(symbol)
        continue

    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        print(
            f"paper readiness failed: could not read scenario {path}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    daily = payload.get("daily_bars") or []
    intraday = payload.get("intraday_bars") or []
    if not daily:
        empty_daily.append(symbol)
    else:
        daily_max = max(parse_bar_date(bar["timestamp"]) for bar in daily)
        if daily_max < expected_date:
            stale_daily.append(f"{symbol}:{daily_max.isoformat()}")

    if not intraday:
        empty_intraday.append(symbol)
    else:
        intraday_max = max(parse_bar_date(bar["timestamp"]) for bar in intraday)
        if intraday_max < expected_date:
            stale_intraday.append(f"{symbol}:{intraday_max.isoformat()}")

problems = {
    "missing": missing,
    "empty_daily": empty_daily,
    "empty_intraday": empty_intraday,
    "stale_daily": stale_daily,
    "stale_intraday": stale_intraday,
}
if any(problems.values()):
    print(
        "paper readiness failed: scenario freshness check found stale or missing "
        f"active-symbol evidence expected>={expected_date.isoformat()} "
        f"dir={scenario_dir}",
        file=sys.stderr,
    )
    for name, values in problems.items():
        if values:
            examples = ",".join(values[:20])
            print(f"  {name}={len(values)} examples={examples}", file=sys.stderr)
    raise SystemExit(1)

print(
    f"paper readiness scenario freshness ok: active={len(symbols)} "
    f"expected_session={expected_date.isoformat()} dir={scenario_dir}"
)
PY
}

if [[ "${PAPER_READINESS_REQUIRE_SCENARIOS,,}" == "true" ]]; then
  run_scenario_freshness_check
else
  echo "paper readiness scenario freshness check skipped"
fi

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
  SELECT strategy_name, weight, sharpe
  FROM strategy_weights
  WHERE trading_mode = 'paper'
    AND strategy_version = :'strategy_version'
),
summary AS (
  SELECT
    (SELECT COALESCE(array_agg(strategy_name ORDER BY strategy_name), ARRAY[]::text[]) FROM active) AS active_names,
    (SELECT COALESCE(array_agg(strategy_name ORDER BY strategy_name), ARRAY[]::text[]) FROM weights) AS weight_names,
    (SELECT COALESCE(SUM(weight), 0) FROM weights) AS weight_sum,
    (SELECT COUNT(*) FROM weights WHERE weight <= 0) AS nonpositive_weights,
    (SELECT COUNT(*) FROM weights WHERE sharpe IS NULL) AS null_sharpes
)
SELECT
  CASE
    WHEN cardinality(active_names) > 0
     AND active_names = weight_names
     AND nonpositive_weights = 0
     AND null_sharpes = 0
     AND ABS(weight_sum - 1.0) < 0.0001
    THEN 'ok'
    ELSE 'mismatch'
  END,
  array_to_string(active_names, ','),
  array_to_string(weight_names, ','),
  ROUND(weight_sum::numeric, 6),
  null_sharpes
FROM summary;
SQL
}

weight_alignment="$(load_weight_alignment)"
IFS='|' read -r weight_status active_weight_names stored_weight_names stored_weight_sum null_sharpes \
  <<< "$weight_alignment"

if [[ "$weight_status" != "ok" ]]; then
  if [[ "$PAPER_READINESS_AUTO_RESET_WEIGHTS" != "true" ]]; then
    echo \
      "paper readiness failed: strategy weights mismatch active=[$active_weight_names] stored=[$stored_weight_names] sum=${stored_weight_sum:-0} null_sharpes=${null_sharpes:-0}" \
      >&2
    exit 1
  fi

  echo \
    "paper readiness resetting stale strategy weights: active=[$active_weight_names] stored=[$stored_weight_names] sum=${stored_weight_sum:-0} null_sharpes=${null_sharpes:-0}"
  "${compose[@]}" run -T --rm admin reset-weights \
    --mode paper \
    --strategy-version "$STRATEGY_VERSION"

  weight_alignment="$(load_weight_alignment)"
  IFS='|' read -r weight_status active_weight_names stored_weight_names stored_weight_sum null_sharpes \
    <<< "$weight_alignment"
  if [[ "$weight_status" != "ok" ]]; then
    echo \
      "paper readiness failed after weight reset: active=[$active_weight_names] stored=[$stored_weight_names] sum=${stored_weight_sum:-0} null_sharpes=${null_sharpes:-0}" \
      >&2
    exit 1
  fi
fi

echo \
  "paper readiness weights ok: active=[$active_weight_names] stored=[$stored_weight_names] sum=$stored_weight_sum null_sharpes=$null_sharpes"

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

confidence_watermark_check="$("${compose[@]}" run -T --rm \
  --entrypoint python admin <<'PY'
from __future__ import annotations

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import ConfidenceFloorStore

settings = Settings.from_env()
account = AlpacaExecutionAdapter.from_settings(settings).get_account()
equity = float(account.equity)
buying_power = float(account.buying_power)
minimum_buying_power = equity * float(settings.max_position_pct)
trading_blocked = bool(account.trading_blocked)
conn = connect_postgres(settings.database_url)
try:
    rec = ConfidenceFloorStore(conn).load(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
finally:
    close = getattr(conn, "close", None)
    if callable(close):
        close()

watermark = float(rec.equity_high_watermark) if rec is not None else 0.0
threshold = float(settings.drawdown_raise_pct)
drawdown = ((watermark - equity) / watermark) if watermark > 0 else 0.0
watermark_status = "mismatch" if watermark > 0 and drawdown > threshold else "ok"
account_status = (
    "mismatch"
    if trading_blocked or equity <= 0 or buying_power < minimum_buying_power
    else "ok"
)
print(
    f"{watermark_status}|{equity:.2f}|{watermark:.2f}|{drawdown:.6f}|"
    f"{threshold:.6f}|{account_status}|{buying_power:.2f}|"
    f"{minimum_buying_power:.2f}|{str(trading_blocked).lower()}"
)
PY
)"

IFS='|' read -r confidence_watermark_status broker_equity confidence_watermark_value \
  confidence_watermark_drawdown confidence_watermark_threshold broker_account_status \
  broker_buying_power broker_minimum_buying_power broker_trading_blocked \
  <<< "$confidence_watermark_check"

if [[ "$broker_account_status" != "ok" ]]; then
  echo \
    "paper readiness failed: broker account not tradable equity=${broker_equity:-unset} buying_power=${broker_buying_power:-unset} minimum_required=${broker_minimum_buying_power:-unset} trading_blocked=${broker_trading_blocked:-unset}" \
    >&2
  exit 1
fi

echo \
  "paper readiness broker account ok: equity=$broker_equity buying_power=$broker_buying_power minimum_required=$broker_minimum_buying_power trading_blocked=$broker_trading_blocked"

if [[ "$confidence_watermark_status" != "ok" ]]; then
  echo \
    "paper readiness failed: confidence watermark=${confidence_watermark_value:-unset} broker_equity=${broker_equity:-unset} drawdown=${confidence_watermark_drawdown:-unset} exceeds trigger=${confidence_watermark_threshold:-unset}" \
    >&2
  exit 1
fi

echo \
  "paper readiness confidence watermark ok: equity=$broker_equity watermark=$confidence_watermark_value drawdown=$confidence_watermark_drawdown threshold=$confidence_watermark_threshold"

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

active_option_orders="$("${compose[@]}" exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -tA \
  -v strategy_version="$STRATEGY_VERSION" <<'SQL'
SELECT COUNT(*)::int
FROM option_orders
WHERE trading_mode = 'paper'
  AND strategy_version = :'strategy_version'
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
  );
SQL
)"
active_option_orders="$(echo "$active_option_orders" | tr -d '[:space:]')"

if [[ "${open_option_positions:-0}" != "0" ]]; then
  echo \
    "paper readiness failed: stock-only proof has $open_option_positions net-open option positions" \
    >&2
  exit 1
fi

if [[ "${active_option_orders:-0}" != "0" ]]; then
  echo \
    "paper readiness failed: stock-only proof has $active_option_orders active option orders" \
    >&2
  exit 1
fi

echo "paper readiness option positions ok: net_open=0 active_orders=0"

same_session_profit_lock=false
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

    if [[ "$status_line" == *"reason=paper profit lock"* ]]; then
      status_updated_at="$(sed -n 's/.* updated_at=\([^ ]*\).*/\1/p' <<< "$status_line")"
      status_session_date=""
      if [[ -n "$status_updated_at" ]]; then
        status_session_date="$(TZ=America/New_York date -d "$status_updated_at" +%F 2>/dev/null || true)"
      fi
      current_session_date="$(TZ=America/New_York date +%F)"
      if [[ -z "$status_session_date" || "$status_session_date" == "$current_session_date" ]]; then
        same_session_profit_lock=true
      fi
    fi

    if [[ "$same_session_profit_lock" == "true" ]]; then
      echo "paper readiness preserving same-session paper profit lock: $status_line"
    else
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
fi

if [[ "${PAPER_READINESS_REQUIRE_PRIOR_PROOF_CHECKS,,}" == "true" ]]; then
  if [[ "$PAPER_READINESS_PREVIOUS_SESSION_DATE" < "$PAPER_READINESS_PRIOR_PROOF_START_DATE" ]]; then
    echo \
      "paper readiness prior proof checks pending: session=$PAPER_READINESS_PREVIOUS_SESSION_DATE proof_start=$PAPER_READINESS_PRIOR_PROOF_START_DATE"
  else
    prior_proof_status="$("${compose[@]}" exec -T postgres psql \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" \
      -tA -F '|' \
      -v strategy_version="$STRATEGY_VERSION" \
      -v previous_session_date="$PAPER_READINESS_PREVIOUS_SESSION_DATE" <<'SQL'
WITH expected(check_name) AS (
  VALUES ('session_guard'), ('paper_profit_probe')
),
latest_checks AS (
  SELECT DISTINCT ON (payload->>'check_name')
    payload->>'check_name' AS check_name,
    payload->>'status' AS status,
    payload->>'exit_code' AS exit_code,
    created_at
  FROM audit_events
  WHERE event_type = 'scheduled_check_completed'
    AND (
      payload->>'session_date' = :'previous_session_date'
      OR (
        NOT (payload ? 'session_date')
        AND created_at >= ((:'previous_session_date')::date::timestamp AT TIME ZONE 'America/New_York')
        AND created_at < (((:'previous_session_date')::date + 1)::timestamp AT TIME ZONE 'America/New_York')
      )
    )
    AND payload->>'trading_mode' = 'paper'
    AND payload->>'strategy_version' = :'strategy_version'
    AND payload->>'check_name' IN ('session_guard', 'paper_profit_probe')
  ORDER BY payload->>'check_name', created_at DESC
),
missing AS (
  SELECT expected.check_name
  FROM expected
  LEFT JOIN latest_checks USING (check_name)
  WHERE latest_checks.check_name IS NULL
),
invalid AS (
  SELECT check_name, status, exit_code, created_at
  FROM latest_checks
  WHERE NOT (
    (
      check_name = 'session_guard'
      AND (
        status = 'passed'
        OR (status = 'pending' AND exit_code = '43')
      )
    )
    OR (
      check_name = 'paper_profit_probe'
      AND (
        status = 'passed'
        OR (status = 'pending' AND exit_code = '43')
      )
    )
  )
)
SELECT
  (SELECT COUNT(*)::int FROM missing),
  COALESCE((SELECT string_agg(check_name, ',' ORDER BY check_name) FROM missing), ''),
  (SELECT COUNT(*)::int FROM invalid),
  COALESCE(
    (
      SELECT string_agg(
        check_name
          || ':status=' || COALESCE(status, '')
          || ':rc=' || COALESCE(exit_code, '')
          || ':at=' || to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        ','
        ORDER BY check_name
      )
      FROM invalid
    ),
    ''
  ),
  COALESCE(
    (
      SELECT string_agg(
        check_name || '=' || COALESCE(status, '') || ':rc=' || COALESCE(exit_code, ''),
        ','
        ORDER BY check_name
      )
      FROM latest_checks
    ),
    ''
  );
SQL
)"
    IFS='|' read -r \
      prior_proof_missing_count \
      prior_proof_missing_names \
      prior_proof_invalid_count \
      prior_proof_invalid_names \
      prior_proof_status_names \
      <<< "$prior_proof_status"

    if [[ "${prior_proof_missing_count:-0}" != "0" ]]; then
      echo \
        "paper readiness failed: prior proof scheduled checks missing for session $PAPER_READINESS_PREVIOUS_SESSION_DATE [$prior_proof_missing_names]" \
        >&2
      exit 1
    fi

    if [[ "${prior_proof_invalid_count:-0}" != "0" ]]; then
      echo \
        "paper readiness failed: prior proof scheduled checks failed for session $PAPER_READINESS_PREVIOUS_SESSION_DATE [$prior_proof_invalid_names]" \
        >&2
      exit 1
    fi

    echo "paper readiness prior proof checks ok: session=$PAPER_READINESS_PREVIOUS_SESSION_DATE [$prior_proof_status_names]"
  fi
else
  echo "paper readiness prior proof check gate skipped"
fi

if [[ "${PAPER_READINESS_REQUIRE_SESSION_UNBLOCKED,,}" == "true" ]]; then
  session_entry_blocks="$("${compose[@]}" exec -T postgres psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -tA -F '|' \
    -v strategy_version="$STRATEGY_VERSION" \
    -v readiness_session_date="$PAPER_READINESS_SESSION_DATE" <<'SQL'
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
  WHERE session_date = (:'readiness_session_date')::date
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
    if [[ "${same_session_profit_lock:-false}" == "true" ]]; then
      echo "paper readiness session entry block check accepted for same-session paper profit lock: session=$PAPER_READINESS_SESSION_DATE blocked=$blocked_session_state_count names=[$blocked_session_state_names]"
    elif is_after_configured_flatten_time; then
      echo "paper readiness session entry block check skipped after flatten: session=$PAPER_READINESS_SESSION_DATE blocked=$blocked_session_state_count names=[$blocked_session_state_names]"
    else
      echo \
        "paper readiness failed: session $PAPER_READINESS_SESSION_DATE has entry-blocking state for [$blocked_session_state_names]" \
        >&2
      exit 1
    fi
  else
    echo "paper readiness session entry blocks ok: session=$PAPER_READINESS_SESSION_DATE blocked=0"
  fi
else
  echo "paper readiness session entry block check skipped"
fi

if [[ "${PAPER_READINESS_REQUIRE_LOSING_STREAK_CLEAR,,}" == "true" ]]; then
  losing_streak_blocks="$("${compose[@]}" exec -T postgres psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -tA -F '|' \
    -v strategy_version="$STRATEGY_VERSION" \
    -v readiness_session_date="$PAPER_READINESS_SESSION_DATE" \
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
      <= ((:'readiness_session_date')::date - 1)
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
      "paper readiness failed: active strategies at losing-streak gate for session $PAPER_READINESS_SESSION_DATE [$losing_streak_block_names] threshold=$PAPER_READINESS_LOSING_STREAK_N" \
      >&2
    exit 1
  fi

  echo "paper readiness losing streak gate ok: session=$PAPER_READINESS_SESSION_DATE blocked=0 threshold=$PAPER_READINESS_LOSING_STREAK_N"
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

ops_expected_trading_status="enabled"
if [[ "${same_session_profit_lock:-false}" == "true" ]]; then
  stock_exposure_counts="$(load_stock_exposure_counts)"
  IFS='|' read -r open_positions active_orders <<< "$stock_exposure_counts"

  if [[ "${open_positions:-0}" != "0" ]]; then
    echo "paper readiness failed: same-session paper profit lock has $open_positions open stock positions" >&2
    exit 1
  fi

  if [[ "${active_orders:-0}" != "0" ]]; then
    echo "paper readiness failed: same-session paper profit lock has $active_orders active stock orders" >&2
    exit 1
  fi

  BROKER_FLAT_CONTEXT="paper readiness profit lock" ./scripts/broker_flat_check.sh "$ENV_FILE"
  echo "paper readiness ops check accepting same-session paper profit lock"
  ops_expected_trading_status="close_only"
fi

"${compose[@]}" run -T --rm \
  --entrypoint alpaca-bot-ops-check admin \
  --url http://web:8080/healthz \
  --expect-worker \
  --wait-seconds 60 \
  --expect-trading-mode paper \
  --expect-strategy-version "$STRATEGY_VERSION" \
  --expect-trading-status "$ops_expected_trading_status" \
  --expect-kill-switch false \
  --expect-only-enabled-strategy bull_flag
