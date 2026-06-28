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
  PAPER_DECISION_DRY_RUN_STRATEGY \
  PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED \
  PAPER_DECISION_DRY_RUN_MIN_RECORDS \
  PAPER_DECISION_DRY_RUN_LOOKBACK_DAYS \
  PAPER_DECISION_DRY_RUN_SAMPLE_TIME \
  PAPER_DECISION_DRY_RUN_SAMPLE_TIMES \
  PAPER_DECISION_DRY_RUN_AS_OF \
  PAPER_DECISION_DRY_RUN_SESSION_DATE \
  PAPER_DECISION_DRY_RUN_EQUITY

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

PAPER_DECISION_DRY_RUN_STRATEGY="${PAPER_DECISION_DRY_RUN_STRATEGY:-bull_flag}"
PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED="${PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-false}"
PAPER_DECISION_DRY_RUN_MIN_RECORDS="${PAPER_DECISION_DRY_RUN_MIN_RECORDS:-1}"
PAPER_DECISION_DRY_RUN_LOOKBACK_DAYS="${PAPER_DECISION_DRY_RUN_LOOKBACK_DAYS:-5}"
PAPER_DECISION_DRY_RUN_SAMPLE_TIME="${PAPER_DECISION_DRY_RUN_SAMPLE_TIME:-15:30}"
PAPER_DECISION_DRY_RUN_SAMPLE_TIMES="${PAPER_DECISION_DRY_RUN_SAMPLE_TIMES:-}"
PAPER_DECISION_DRY_RUN_AS_OF="${PAPER_DECISION_DRY_RUN_AS_OF:-}"
PAPER_DECISION_DRY_RUN_SESSION_DATE="${PAPER_DECISION_DRY_RUN_SESSION_DATE:-}"
PAPER_DECISION_DRY_RUN_EQUITY="${PAPER_DECISION_DRY_RUN_EQUITY:-}"

if [[ "${TRADING_MODE:-paper}" != "paper" ]]; then
  echo "paper decision dry run skipped for TRADING_MODE=${TRADING_MODE:-unset}"
  exit 0
fi

case "${PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED,,}" in
  true|false) ;;
  *)
    echo "PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED must be true or false" >&2
    exit 1
    ;;
esac

if ! [[ "$PAPER_DECISION_DRY_RUN_MIN_RECORDS" =~ ^[0-9]+$ ]]; then
  echo "PAPER_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" >&2
  exit 1
fi

if ! [[ "$PAPER_DECISION_DRY_RUN_LOOKBACK_DAYS" =~ ^[1-9][0-9]*$ ]]; then
  echo "PAPER_DECISION_DRY_RUN_LOOKBACK_DAYS must be a positive integer" >&2
  exit 1
fi

compose=(docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml)

"${compose[@]}" run -T --rm \
  -e PAPER_DECISION_DRY_RUN_STRATEGY="$PAPER_DECISION_DRY_RUN_STRATEGY" \
  -e PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED="$PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED" \
  -e PAPER_DECISION_DRY_RUN_MIN_RECORDS="$PAPER_DECISION_DRY_RUN_MIN_RECORDS" \
  -e PAPER_DECISION_DRY_RUN_LOOKBACK_DAYS="$PAPER_DECISION_DRY_RUN_LOOKBACK_DAYS" \
  -e PAPER_DECISION_DRY_RUN_SAMPLE_TIME="$PAPER_DECISION_DRY_RUN_SAMPLE_TIME" \
  -e PAPER_DECISION_DRY_RUN_SAMPLE_TIMES="$PAPER_DECISION_DRY_RUN_SAMPLE_TIMES" \
  -e PAPER_DECISION_DRY_RUN_AS_OF="$PAPER_DECISION_DRY_RUN_AS_OF" \
  -e PAPER_DECISION_DRY_RUN_SESSION_DATE="$PAPER_DECISION_DRY_RUN_SESSION_DATE" \
  -e PAPER_DECISION_DRY_RUN_EQUITY="$PAPER_DECISION_DRY_RUN_EQUITY" \
  --entrypoint python admin <<'PY'
from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter, AlpacaMarketDataAdapter
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import StrategyFlagStore, WatchlistStore
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.strategy.session import SessionType


def _parse_bool(name: str) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    print(f"{name} must be true or false", file=sys.stderr)
    raise SystemExit(1)


def _parse_sample_time(value: str) -> time:
    try:
        hour_s, minute_s = value.split(":", 1)
        return time(hour=int(hour_s), minute=int(minute_s))
    except Exception:
        print("PAPER_DECISION_DRY_RUN_SAMPLE_TIME must use HH:MM", file=sys.stderr)
        raise SystemExit(1)


def _parse_sample_times(value: str) -> tuple[time, ...]:
    sample_times = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            sample_times.append(_parse_sample_time(part))
        except SystemExit:
            print(
                "PAPER_DECISION_DRY_RUN_SAMPLE_TIMES must be comma-separated HH:MM values",
                file=sys.stderr,
            )
            raise
    if not sample_times:
        print(
            "PAPER_DECISION_DRY_RUN_SAMPLE_TIMES must include at least one HH:MM value",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return tuple(sample_times)


def _parse_session_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        print("PAPER_DECISION_DRY_RUN_SESSION_DATE must use YYYY-MM-DD", file=sys.stderr)
        raise SystemExit(1)


def _parse_as_of(settings: Settings, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        print("PAPER_DECISION_DRY_RUN_AS_OF must be an ISO datetime", file=sys.stderr)
        raise SystemExit(1)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=settings.market_timezone)
    return parsed.astimezone(settings.market_timezone)


def _parse_equity(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        print("PAPER_DECISION_DRY_RUN_EQUITY must be a number", file=sys.stderr)
        raise SystemExit(1)


def _latest_completed_session_date(
    *,
    broker: AlpacaExecutionAdapter,
    settings: Settings,
) -> date:
    today = datetime.now(settings.market_timezone).date()
    calendar = broker.get_market_calendar(start=today - timedelta(days=14), end=today)
    previous_sessions = [day.session_date for day in calendar if day.session_date < today]
    if previous_sessions:
        return max(previous_sessions)
    sessions = [day.session_date for day in calendar if day.session_date <= today]
    if sessions:
        return max(sessions)
    print("paper decision dry run failed: no completed market session found", file=sys.stderr)
    raise SystemExit(1)


def _resolve_as_ofs(
    *,
    broker: AlpacaExecutionAdapter,
    settings: Settings,
) -> tuple[datetime, ...]:
    as_of_env = (os.environ.get("PAPER_DECISION_DRY_RUN_AS_OF") or "").strip()
    if as_of_env:
        return (_parse_as_of(settings, as_of_env),)

    session_env = (os.environ.get("PAPER_DECISION_DRY_RUN_SESSION_DATE") or "").strip()
    session_date = (
        _parse_session_date(session_env)
        if session_env
        else _latest_completed_session_date(broker=broker, settings=settings)
    )
    sample_times_env = (os.environ.get("PAPER_DECISION_DRY_RUN_SAMPLE_TIMES") or "").strip()
    sample_times = (
        _parse_sample_times(sample_times_env)
        if sample_times_env
        else (_parse_sample_time(os.environ.get("PAPER_DECISION_DRY_RUN_SAMPLE_TIME", "15:30")),)
    )
    return tuple(
        datetime.combine(session_date, sample_time, tzinfo=settings.market_timezone)
        for sample_time in sample_times
    )


def _completed_intraday_bars_by_symbol(
    bars_by_symbol,
    *,
    timestamp: datetime,
    timeframe_minutes: int,
):
    cutoff = timestamp.astimezone(timezone.utc)
    timeframe = timedelta(minutes=timeframe_minutes)
    completed = {}
    for symbol, bars in bars_by_symbol.items():
        symbol_completed = []
        for bar in bars:
            bar_ts = (
                bar.timestamp.replace(tzinfo=timezone.utc)
                if bar.timestamp.tzinfo is None
                else bar.timestamp.astimezone(timezone.utc)
            )
            if bar_ts + timeframe <= cutoff:
                symbol_completed.append(bar)
        completed[symbol] = symbol_completed
    return completed


def _summary_counts(records, field_name: str) -> str:
    counts = Counter(
        (getattr(record, field_name, None) or "none")
        for record in records
    )
    if not counts:
        return "none"
    return ",".join(
        f"{key}:{count}"
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )


settings = Settings.from_env()
strategy_name = os.environ.get("PAPER_DECISION_DRY_RUN_STRATEGY", "bull_flag")
if strategy_name not in STRATEGY_REGISTRY:
    print(f"paper decision dry run failed: unknown strategy={strategy_name}", file=sys.stderr)
    raise SystemExit(1)

min_records = int(os.environ.get("PAPER_DECISION_DRY_RUN_MIN_RECORDS", "1"))
lookback_days = int(os.environ.get("PAPER_DECISION_DRY_RUN_LOOKBACK_DAYS", "5"))
require_accepted = _parse_bool("PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED")
equity_env = (os.environ.get("PAPER_DECISION_DRY_RUN_EQUITY") or "").strip()
equity_override = _parse_equity(equity_env) if equity_env else None

conn = connect_postgres(settings.database_url)
try:
    watchlist_store = WatchlistStore(conn)
    strategy_flag_store = StrategyFlagStore(conn)
    enabled_symbols = tuple(watchlist_store.list_enabled(settings.trading_mode.value))
    ignored_symbols = set(watchlist_store.list_ignored(settings.trading_mode.value))
    active_symbols = tuple(symbol for symbol in enabled_symbols if symbol not in ignored_symbols)
    strategy_flag = strategy_flag_store.load(
        strategy_name=strategy_name,
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
finally:
    conn.close()

if strategy_flag is not None and not strategy_flag.enabled:
    print(f"paper decision dry run failed: strategy disabled: {strategy_name}", file=sys.stderr)
    raise SystemExit(1)

if not active_symbols:
    print("paper decision dry run failed: active watchlist is empty", file=sys.stderr)
    raise SystemExit(1)

broker = AlpacaExecutionAdapter.from_settings(settings)
market_data = AlpacaMarketDataAdapter.from_settings(settings)
as_ofs = _resolve_as_ofs(broker=broker, settings=settings)
earliest_as_of = min(as_ofs)
latest_as_of = max(as_ofs)
equity = equity_override if equity_override is not None else broker.get_account().equity
fractionable_symbols = broker.get_fractionable_symbols(active_symbols)
settings = replace(settings, fractionable_symbols=fractionable_symbols)

daily_end = datetime.combine(
    latest_as_of.astimezone(settings.market_timezone).date(),
    datetime.min.time(),
    tzinfo=settings.market_timezone,
)
daily_start = latest_as_of - timedelta(
    days=max(
        settings.daily_sma_period * 3,
        60,
        settings.high_watermark_lookback_days + 10,
    )
)
intraday_bars = market_data.get_stock_bars(
    symbols=list(active_symbols),
    start=earliest_as_of - timedelta(days=lookback_days),
    end=latest_as_of,
    timeframe_minutes=settings.entry_timeframe_minutes,
)
daily_bars = market_data.get_daily_bars(
    symbols=list(active_symbols),
    start=daily_start,
    end=daily_end,
)
regime_bars = None
if settings.enable_regime_filter:
    if settings.regime_symbol in daily_bars:
        regime_bars = daily_bars.get(settings.regime_symbol)
    else:
        regime_daily = market_data.get_daily_bars(
            symbols=[settings.regime_symbol],
            start=latest_as_of - timedelta(days=max(settings.regime_sma_period * 3, 60)),
            end=daily_end,
        )
        regime_bars = regime_daily.get(settings.regime_symbol)

evaluations = []
for as_of in sorted(as_ofs):
    completed_intraday_bars = _completed_intraday_bars_by_symbol(
        intraday_bars,
        timestamp=as_of,
        timeframe_minutes=settings.entry_timeframe_minutes,
    )
    result = evaluate_cycle(
        settings=settings,
        now=as_of,
        equity=equity,
        intraday_bars_by_symbol=completed_intraday_bars,
        daily_bars_by_symbol=daily_bars,
        open_positions=(),
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=STRATEGY_REGISTRY[strategy_name],
        strategy_name=strategy_name,
        global_open_count=0,
        symbols=active_symbols,
        session_type=SessionType.REGULAR,
        regime_bars=regime_bars,
    )
    records = tuple(result.decision_records)
    accepted = [record for record in records if record.decision == "accepted"]
    rejected = [record for record in records if record.decision == "rejected"]
    skipped_no_signal = [record for record in records if record.decision == "skipped_no_signal"]
    entry_intents = [
        intent for intent in result.intents if intent.intent_type == CycleIntentType.ENTRY
    ]
    completed_covered = sum(1 for symbol in active_symbols if completed_intraday_bars.get(symbol))
    thin_completed = sum(
        1
        for symbol in active_symbols
        if len(completed_intraday_bars.get(symbol, ())) < settings.relative_volume_lookback_bars
    )
    evaluations.append({
        "as_of": as_of,
        "completed_intraday_bars": completed_intraday_bars,
        "records": records,
        "accepted": accepted,
        "rejected": rejected,
        "skipped_no_signal": skipped_no_signal,
        "entry_intents": entry_intents,
        "completed_covered": completed_covered,
        "thin_completed": thin_completed,
    })

low_record_evaluations = [
    item for item in evaluations if len(item["records"]) < min_records
]
if low_record_evaluations:
    detail = ",".join(
        f"{item['as_of'].isoformat()}:{len(item['records'])}"
        for item in low_record_evaluations[:10]
    )
    print(
        "paper decision dry run failed: "
        f"decision_records below min_records={min_records}: {detail}",
        file=sys.stderr,
    )
    raise SystemExit(1)

max_accepted = max(len(item["accepted"]) for item in evaluations)
if require_accepted and max_accepted == 0:
    print(
        "paper decision dry run failed: "
        f"accepted=0 require_accepted=true evaluations={len(evaluations)}",
        file=sys.stderr,
    )
    raise SystemExit(1)

best = max(
    evaluations,
    key=lambda item: (
        len(item["entry_intents"]),
        len(item["accepted"]),
        len(item["records"]),
    ),
)
as_of = best["as_of"]
completed_intraday_bars = best["completed_intraday_bars"]
records = best["records"]
accepted = best["accepted"]
rejected = best["rejected"]
skipped_no_signal = best["skipped_no_signal"]
entry_intents = best["entry_intents"]
reject_stages = _summary_counts(rejected, "reject_stage")
reject_reasons = _summary_counts(rejected, "reject_reason")

sample = "none"
if accepted:
    first = accepted[0]
    sample = f"{first.symbol}:{first.quantity}@{first.limit_price}"

intraday_covered = sum(1 for symbol in active_symbols if intraday_bars.get(symbol))
completed_covered = best["completed_covered"]
daily_covered = sum(1 for symbol in active_symbols if daily_bars.get(symbol))
thin_completed = best["thin_completed"]
sample_times_text = ",".join(
    item["as_of"].astimezone(settings.market_timezone).strftime("%H:%M")
    for item in evaluations
)
multi_sample_fields = ""
if len(evaluations) > 1:
    multi_sample_fields = (
        f" sample_times={sample_times_text}"
        f" evaluations={len(evaluations)}"
        f" min_decision_records={min(len(item['records']) for item in evaluations)}"
        f" max_accepted={max(len(item['accepted']) for item in evaluations)}"
        f" max_entry_intents={max(len(item['entry_intents']) for item in evaluations)}"
    )

print(
    "paper decision dry run ok: "
    f"strategy={strategy_name} "
    f"as_of={as_of.isoformat()} "
    f"active={len(active_symbols)} "
    f"ignored={len(ignored_symbols)} "
    f"fractionable={len(fractionable_symbols)} "
    f"intraday={intraday_covered}/{len(active_symbols)} "
    f"completed_intraday={completed_covered}/{len(active_symbols)} "
    f"daily={daily_covered}/{len(active_symbols)} "
    f"thin_completed_lt{settings.relative_volume_lookback_bars}={thin_completed} "
    f"decision_records={len(records)} "
    f"accepted={len(accepted)} "
    f"rejected={len(rejected)} "
    f"skipped_no_signal={len(skipped_no_signal)} "
    f"entry_intents={len(entry_intents)} "
    f"reject_stages={reject_stages} "
    f"reject_reasons={reject_reasons} "
    f"equity={equity:.2f} "
    f"sample={sample}"
    f"{multi_sample_fields}"
)
PY
