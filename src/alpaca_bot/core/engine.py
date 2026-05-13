from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar, DecisionRecord, NewsItem, OpenPosition, Quote
from alpaca_bot.domain.models import MarketContext
from alpaca_bot.risk import calculate_position_size
from alpaca_bot.risk.option_sizing import calculate_option_position_size
from alpaca_bot.risk.atr import calculate_atr
from alpaca_bot.strategy import StrategySignalEvaluator
from alpaca_bot.strategy.breakout import (
    daily_trend_filter_exit_passes,
    daily_trend_filter_short_exit_passes,
    evaluate_breakout_signal,
    is_past_flatten_time,
    session_day,
)
from alpaca_bot.strategy.indicators import calculate_vwap
from alpaca_bot.strategy.session import (
    SessionType,
    is_flatten_time as _session_flatten_time,
    is_entry_window as _is_entry_window,
)

if TYPE_CHECKING:
    from alpaca_bot.storage import DailySessionState
    from alpaca_bot.domain.models import OptionContract


class CycleIntentType(StrEnum):
    ENTRY = "entry"
    UPDATE_STOP = "update_stop"
    EXIT = "exit"


@dataclass(frozen=True)
class CycleIntent:
    intent_type: CycleIntentType
    symbol: str
    timestamp: datetime
    quantity: float | None = None
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    client_order_id: str | None = None
    reason: str | None = None
    signal_timestamp: datetime | None = None
    strategy_name: str = "breakout"
    underlying_symbol: str | None = None
    is_option: bool = False
    option_strike: float | None = None
    option_expiry: date | None = None
    option_type_str: str | None = None


@dataclass(frozen=True)
class CycleResult:
    as_of: datetime
    intents: list[CycleIntent] = field(default_factory=list)
    regime_blocked: bool = False
    vix_blocked: bool = False
    sector_blocked: bool = False
    news_blocked_symbols: tuple[str, ...] = ()
    spread_blocked_symbols: tuple[str, ...] = ()
    decision_records: tuple[DecisionRecord, ...] = ()


def evaluate_cycle(
    *,
    settings: Settings,
    now: datetime,
    equity: float,
    intraday_bars_by_symbol: Mapping[str, Sequence[Bar]],
    daily_bars_by_symbol: Mapping[str, Sequence[Bar]],
    open_positions: Sequence[OpenPosition],
    working_order_symbols: set[str],
    traded_symbols_today: set[tuple[str, date]],
    entries_disabled: bool,
    flatten_all: bool = False,
    signal_evaluator: StrategySignalEvaluator | None = None,
    session_state: "DailySessionState | None" = None,
    strategy_name: str = "breakout",
    global_open_count: int | None = None,
    symbols: tuple[str, ...] | None = None,
    session_type: SessionType | None = None,
    regime_bars: Sequence[Bar] | None = None,
    news_by_symbol: Mapping[str, Sequence[NewsItem]] | None = None,
    quotes_by_symbol: Mapping[str, Quote] | None = None,
    market_context: MarketContext | None = None,
) -> CycleResult:
    if signal_evaluator is None:
        signal_evaluator = evaluate_breakout_signal

    if flatten_all:
        seen_symbols: set[str] = set()
        intents = []
        for position in open_positions:
            if position.symbol in seen_symbols:
                continue
            seen_symbols.add(position.symbol)
            intents.append(
                CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol=position.symbol,
                    timestamp=now,
                    reason="loss_limit_flatten",
                    strategy_name=strategy_name,
                )
            )
        intents.sort(key=lambda intent: (intent.timestamp, intent.symbol, intent.intent_type.value))
        return CycleResult(as_of=now, intents=intents)

    intents: list[CycleIntent] = []
    _decision_records: list[DecisionRecord] = []
    _tm = settings.trading_mode.value
    _sv = settings.strategy_version
    open_position_symbols = {position.symbol for position in open_positions}
    emitted_exit_symbols: set[str] = set()
    is_extended = session_type in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS)
    if session_type is not None:
        past_flatten = _session_flatten_time(now, settings, session_type)
    else:
        past_flatten = is_past_flatten_time(now, settings)

    for position in open_positions:
        if past_flatten:
            if position.symbol in emitted_exit_symbols:
                continue
            emitted_exit_symbols.add(position.symbol)
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            limit_price_for_exit: float | None = None
            if is_extended and bars:
                limit_price_for_exit = round(
                    bars[-1].close * (1 - settings.extended_hours_limit_offset_pct), 2
                )
            intents.append(
                CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol=position.symbol,
                    timestamp=now,
                    reason="eod_flatten",
                    limit_price=limit_price_for_exit,
                    strategy_name=strategy_name,
                )
            )
            continue

        bars = intraday_bars_by_symbol.get(position.symbol, ())
        if not bars:
            continue
        latest_bar = bars[-1]

        if not is_extended:
            bar_age_seconds = (now - latest_bar.timestamp.astimezone(timezone.utc)).total_seconds()
            if bar_age_seconds > 2 * settings.entry_timeframe_minutes * 60:
                continue

        is_short = position.quantity < 0
        is_short_option = (
            is_short
            and position.stop_price == 0.0
            and position.strategy_name == "short_option"
        )

        if is_extended:
            stop_breached = position.stop_price > 0 and (
                (not is_short and latest_bar.close <= position.stop_price)
                or (is_short and latest_bar.close >= position.stop_price)
            )
            if stop_breached:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.EXIT,
                        symbol=position.symbol,
                        timestamp=now,
                        reason="stop_breach_extended_hours",
                        limit_price=round(
                            latest_bar.close * (1 - settings.extended_hours_limit_offset_pct), 2
                        ),
                        strategy_name=strategy_name,
                    )
                )
            continue

        if settings.enable_profit_target and not is_short_option:
            target_price = round(
                position.entry_price + settings.profit_target_r * position.risk_per_share, 2
            )
            target_hit = (
                (not is_short and latest_bar.high >= target_price)
                or (is_short and latest_bar.low <= target_price)
            )
            if target_hit:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.EXIT,
                        symbol=position.symbol,
                        timestamp=latest_bar.timestamp,
                        reason="profit_target",
                        strategy_name=strategy_name,
                    )
                )
                emitted_exit_symbols.add(position.symbol)
                continue

        position_age_s = (
            now - position.entry_timestamp.astimezone(timezone.utc)
        ).total_seconds()
        is_too_young = position_age_s < settings.viability_min_hold_minutes * 60

        if settings.enable_trend_filter_exit and not is_too_young and not is_short_option:
            daily_bars_pos = daily_bars_by_symbol.get(position.symbol, ())
            if len(daily_bars_pos) >= settings.daily_sma_period + settings.trend_filter_exit_lookback_days:
                daily_bar_age_days = (
                    now - daily_bars_pos[-1].timestamp.astimezone(timezone.utc)
                ).days
                if daily_bar_age_days <= settings.viability_daily_bar_max_age_days:
                    passes = (
                        daily_trend_filter_short_exit_passes(daily_bars_pos, settings)
                        if is_short
                        else daily_trend_filter_exit_passes(daily_bars_pos, settings)
                    )
                    if not passes:
                        intents.append(
                            CycleIntent(
                                intent_type=CycleIntentType.EXIT,
                                symbol=position.symbol,
                                timestamp=now,
                                reason="viability_trend_filter_failed",
                                strategy_name=strategy_name,
                            )
                        )
                        continue

        if settings.enable_vwap_breakdown_exit and not is_too_young and not is_short_option:
            session_date = now.astimezone(settings.market_timezone).date()
            today_bars = [
                b for b in bars
                if b.timestamp.astimezone(settings.market_timezone).date() == session_date
            ]
            if len(today_bars) >= settings.vwap_breakdown_min_bars:
                vwap = calculate_vwap(today_bars)
                vwap_exit = vwap is not None and (
                    (not is_short and latest_bar.close < vwap)
                    or (is_short and latest_bar.close > vwap)
                )
                if vwap_exit:
                    intents.append(
                        CycleIntent(
                            intent_type=CycleIntentType.EXIT,
                            symbol=position.symbol,
                            timestamp=now,
                            reason="viability_vwap_breakdown",
                            strategy_name=strategy_name,
                        )
                    )
                    continue

        if not is_short_option:
            profit_trigger = (
                position.entry_price
                + settings.trailing_stop_profit_trigger_r * position.risk_per_share
            )
            trigger_hit = (
                (not is_short and latest_bar.high >= profit_trigger)
                or (is_short and latest_bar.low <= profit_trigger)
            )
            if trigger_hit:
                atr = (
                    calculate_atr(
                        daily_bars_by_symbol.get(position.symbol, ()),
                        settings.atr_period,
                    )
                    if settings.trailing_stop_atr_multiplier > 0
                    else None
                )
                if is_short:
                    if atr is not None:
                        trailing_candidate = (
                            latest_bar.low + settings.trailing_stop_atr_multiplier * atr
                        )
                        new_stop = round(
                            min(position.stop_price, position.entry_price, trailing_candidate), 2
                        )
                    else:
                        new_stop = round(
                            min(position.stop_price, position.entry_price, latest_bar.high), 2
                        )
                    accept = new_stop < position.stop_price and new_stop > latest_bar.close
                else:
                    if atr is not None:
                        trailing_candidate = (
                            latest_bar.high - settings.trailing_stop_atr_multiplier * atr
                        )
                        new_stop = round(
                            max(position.stop_price, position.entry_price, trailing_candidate), 2
                        )
                    else:
                        new_stop = round(
                            max(position.stop_price, position.entry_price, latest_bar.low), 2
                        )
                    accept = new_stop > position.stop_price and new_stop < latest_bar.close
                if accept:
                    intents.append(
                        CycleIntent(
                            intent_type=CycleIntentType.UPDATE_STOP,
                            symbol=position.symbol,
                            timestamp=latest_bar.timestamp,
                            stop_price=new_stop,
                            strategy_name=strategy_name,
                        )
                    )

    if settings.enable_profit_trail and not is_extended:
        _profit_trail_exited = {
            i.symbol for i in intents if i.intent_type == CycleIntentType.EXIT
        }
        _pt_prior_stops: dict[str, float] = {
            i.symbol: i.stop_price
            for i in intents
            if i.intent_type == CycleIntentType.UPDATE_STOP and i.stop_price is not None
        }
        for position in open_positions:
            if position.symbol in _profit_trail_exited:
                continue
            is_short_pt = position.quantity < 0
            if is_short_pt and position.stop_price == 0.0 and position.strategy_name == "short_option":
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                continue
            session_date = now.astimezone(settings.market_timezone).date()
            today_bars = [
                b for b in bars
                if b.timestamp.astimezone(settings.market_timezone).date() == session_date
            ]
            if not today_bars:
                continue
            prior_stop = _pt_prior_stops.get(position.symbol, position.stop_price)
            if is_short_pt:
                today_low = min(b.low for b in today_bars)
                trail_candidate = round(today_low / settings.profit_trail_pct, 2)
                accept = trail_candidate < prior_stop and trail_candidate > bars[-1].close
            else:
                today_high = max(b.high for b in today_bars)
                trail_candidate = round(today_high * settings.profit_trail_pct, 2)
                accept = trail_candidate > prior_stop and trail_candidate < bars[-1].close
            if accept:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.UPDATE_STOP,
                        symbol=position.symbol,
                        timestamp=now,
                        stop_price=trail_candidate,
                        strategy_name=strategy_name,
                        reason="profit_trail",
                    )
                )

    # Breakeven pass: once a position is up BREAKEVEN_TRIGGER_PCT from entry, raise
    # stop to entry price so the trade cannot become a loss.
    # Runs during extended hours too; a safety guard skips stops that would trigger
    # immediately at open (be_stop >= close means price must gap up just to survive).
    if settings.enable_breakeven_stop:
        _be_exit_syms = {i.symbol for i in intents if i.intent_type == CycleIntentType.EXIT}
        _be_emitted: dict[str, float] = {
            i.symbol: (i.stop_price or 0.0)
            for i in intents
            if i.intent_type == CycleIntentType.UPDATE_STOP
        }
        for position in open_positions:
            if position.symbol in _be_exit_syms:
                continue
            if position.entry_price <= 0:
                continue
            is_short_be = position.quantity < 0
            if is_short_be and position.stop_price == 0.0 and position.strategy_name == "short_option":
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                continue
            latest_bar = bars[-1]
            effective_stop = _be_emitted.get(position.symbol, position.stop_price)
            if is_short_be:
                trigger = position.entry_price * (1 - settings.breakeven_trigger_pct)
                if latest_bar.low <= trigger:
                    min_price = min(position.lowest_price, latest_bar.low) if position.lowest_price > 0 else latest_bar.low
                    trail_stop = round(min_price * (1 + settings.breakeven_trail_pct), 2)
                    be_stop = min(position.entry_price, trail_stop)
                    if be_stop <= latest_bar.close:
                        continue
                    if effective_stop > be_stop:
                        intents.append(
                            CycleIntent(
                                intent_type=CycleIntentType.UPDATE_STOP,
                                symbol=position.symbol,
                                timestamp=now,
                                stop_price=be_stop,
                                strategy_name=strategy_name,
                                reason="breakeven",
                            )
                        )
            else:
                trigger = position.entry_price * (1 + settings.breakeven_trigger_pct)
                if latest_bar.high >= trigger:
                    max_price = max(position.highest_price, latest_bar.high)
                    trail_stop = round(max_price * (1 - settings.breakeven_trail_pct), 2)
                    be_stop = max(position.entry_price, trail_stop)
                    if be_stop >= latest_bar.close:
                        continue
                    if effective_stop < be_stop:
                        intents.append(
                            CycleIntent(
                                intent_type=CycleIntentType.UPDATE_STOP,
                                symbol=position.symbol,
                                timestamp=now,
                                stop_price=be_stop,
                                strategy_name=strategy_name,
                                reason="breakeven",
                            )
                        )

    # Cap-up pass: raise stop to MAX_STOP_PCT cap for any existing position whose stop
    # is more than max_stop_pct below entry. Trailing logic ran first; use emitted
    # UPDATE_STOP intents so we don't emit a duplicate for the same symbol.
    # Derive exit set from intents — emitted_exit_symbols is only populated by the
    # past_flatten branch; trend-filter and VWAP exits are not tracked there.
    emitted_exit_syms = {i.symbol for i in intents if i.intent_type == CycleIntentType.EXIT}
    emitted_update_stops: dict[str, float] = {
        i.symbol: (i.stop_price or 0.0)
        for i in intents
        if i.intent_type == CycleIntentType.UPDATE_STOP
    }
    if not is_extended:
        for position in open_positions:
            if position.symbol in emitted_exit_syms:
                continue
            if position.stop_price <= 0 or position.entry_price <= 0:
                continue
            is_short_cap = position.quantity < 0
            if is_short_cap and position.stop_price == 0.0 and position.strategy_name == "short_option":
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                continue
            effective_stop = emitted_update_stops.get(position.symbol, position.stop_price)
            if is_short_cap:
                cap_stop = round(position.entry_price * (1 + settings.max_stop_pct), 2)
                if effective_stop > cap_stop and cap_stop > bars[-1].close:
                    intents.append(
                        CycleIntent(
                            intent_type=CycleIntentType.UPDATE_STOP,
                            symbol=position.symbol,
                            timestamp=now,
                            stop_price=cap_stop,
                            strategy_name=strategy_name,
                            reason="stop_cap_applied",
                        )
                    )
            else:
                cap_stop = round(position.entry_price * (1 - settings.max_stop_pct), 2)
                if effective_stop < cap_stop and cap_stop < bars[-1].close:
                    intents.append(
                        CycleIntent(
                            intent_type=CycleIntentType.UPDATE_STOP,
                            symbol=position.symbol,
                            timestamp=now,
                            stop_price=cap_stop,
                            strategy_name=strategy_name,
                            reason="stop_cap_applied",
                        )
                    )

    # Extract market context fields for stamping onto every decision record.
    _ctx_vix_close: float | None = None
    _ctx_vix_above_sma: bool | None = None
    _ctx_sector_passing_pct: float | None = None
    if market_context is not None:
        _ctx_vix_close = market_context.vix_close
        _ctx_vix_above_sma = market_context.vix_above_sma
        _ctx_sector_passing_pct = market_context.sector_passing_pct

    # Regime filter: block all entries when broad market is in a downtrend.
    # Mirrors daily_trend_filter_passes(): window[-1] is the most recent completed
    # bar (second-to-last), excluding today's potentially partial bar.
    _regime_entries_blocked = False
    if settings.enable_regime_filter and regime_bars is not None:
        if len(regime_bars) >= settings.regime_sma_period + 1:
            window = regime_bars[-settings.regime_sma_period - 1 : -1]
            sma = sum(b.close for b in window) / len(window)
            if window[-1].close <= sma:
                _regime_entries_blocked = True
                if not entries_disabled:
                    for _rsym in (symbols or settings.symbols):
                        if _rsym in open_position_symbols or _rsym in working_order_symbols:
                            continue
                        _decision_records.append(DecisionRecord(
                            cycle_at=now,
                            symbol=_rsym,
                            strategy_name=strategy_name,
                            trading_mode=_tm,
                            strategy_version=_sv,
                            decision="rejected",
                            reject_stage="pre_filter",
                            reject_reason="regime_blocked",
                            entry_level=None,
                            signal_bar_close=None,
                            relative_volume=None,
                            atr=None,
                            stop_price=None,
                            limit_price=None,
                            initial_stop_price=None,
                            quantity=None,
                            risk_per_share=None,
                            equity=None,
                            filter_results={"regime": False},
                            vix_close=_ctx_vix_close,
                            vix_above_sma=_ctx_vix_above_sma,
                            sector_passing_pct=_ctx_sector_passing_pct,
                        ))

    # VIX regime gate: block all entries when VIX proxy is above its N-day SMA.
    # Fail-open: None vix_above_sma (insufficient history) never blocks.
    _vix_entries_blocked = False
    if (
        settings.enable_vix_filter
        and market_context is not None
        and market_context.vix_above_sma is True
    ):
        _vix_entries_blocked = True
        if not entries_disabled:
            for _vsym in (symbols or settings.symbols):
                if _vsym in open_position_symbols or _vsym in working_order_symbols:
                    continue
                _decision_records.append(DecisionRecord(
                    cycle_at=now,
                    symbol=_vsym,
                    strategy_name=strategy_name,
                    trading_mode=_tm,
                    strategy_version=_sv,
                    decision="rejected",
                    reject_stage="pre_filter",
                    reject_reason="vix_blocked",
                    entry_level=None,
                    signal_bar_close=None,
                    relative_volume=None,
                    atr=None,
                    stop_price=None,
                    limit_price=None,
                    initial_stop_price=None,
                    quantity=None,
                    risk_per_share=None,
                    equity=None,
                    filter_results={"vix": False},
                    vix_close=_ctx_vix_close,
                    vix_above_sma=_ctx_vix_above_sma,
                    sector_passing_pct=_ctx_sector_passing_pct,
                ))

    # Sector breadth gate: block all entries when fewer than N% of sector ETFs
    # are above their SMA. Fail-open: None passing_pct never blocks.
    _sector_entries_blocked = False
    if (
        settings.enable_sector_filter
        and market_context is not None
        and market_context.sector_passing_pct is not None
        and market_context.sector_passing_pct < settings.sector_filter_min_passing_pct
    ):
        _sector_entries_blocked = True
        if not entries_disabled:
            for _ssym in (symbols or settings.symbols):
                if _ssym in open_position_symbols or _ssym in working_order_symbols:
                    continue
                _decision_records.append(DecisionRecord(
                    cycle_at=now,
                    symbol=_ssym,
                    strategy_name=strategy_name,
                    trading_mode=_tm,
                    strategy_version=_sv,
                    decision="rejected",
                    reject_stage="pre_filter",
                    reject_reason="sector_blocked",
                    entry_level=None,
                    signal_bar_close=None,
                    relative_volume=None,
                    atr=None,
                    stop_price=None,
                    limit_price=None,
                    initial_stop_price=None,
                    quantity=None,
                    risk_per_share=None,
                    equity=None,
                    filter_results={"sector": False},
                    vix_close=_ctx_vix_close,
                    vix_above_sma=_ctx_vix_above_sma,
                    sector_passing_pct=_ctx_sector_passing_pct,
                ))

    _news_blocked: list[str] = []
    _spread_blocked: list[str] = []

    if not entries_disabled and not _regime_entries_blocked and not _vix_entries_blocked and not _sector_entries_blocked:
        if global_open_count is not None:
            # Caller has pre-computed the total occupied slots across ALL strategies.
            available_slots = max(settings.max_open_positions - global_open_count, 0)
        else:
            available_slots = max(
                settings.max_open_positions - len(open_positions) - len(working_order_symbols), 0
            )
        if available_slots == 0:
            for _csym in (symbols or settings.symbols):
                if _csym in open_position_symbols or _csym in working_order_symbols:
                    continue
                _decision_records.append(DecisionRecord(
                    cycle_at=now,
                    symbol=_csym,
                    strategy_name=strategy_name,
                    trading_mode=_tm,
                    strategy_version=_sv,
                    decision="rejected",
                    reject_stage="capacity",
                    reject_reason="capacity_full",
                    entry_level=None,
                    signal_bar_close=None,
                    relative_volume=None,
                    atr=None,
                    stop_price=None,
                    limit_price=None,
                    initial_stop_price=None,
                    quantity=None,
                    risk_per_share=None,
                    equity=None,
                    filter_results={},
                    vix_close=_ctx_vix_close,
                    vix_above_sma=_ctx_vix_above_sma,
                    sector_passing_pct=_ctx_sector_passing_pct,
                ))
        if available_slots > 0:
            current_exposure = (
                sum(p.entry_price * p.quantity for p in open_positions) / equity
                if equity > 0
                else 0.0
            )
            entry_candidates: list[tuple[float, float, CycleIntent]] = []
            _candidate_signals: dict[str, tuple] = {}
            _candidate_vwap: dict[str, tuple[float | None, bool | None]] = {}
            for symbol in (symbols or settings.symbols):
                if symbol in open_position_symbols or symbol in working_order_symbols:
                    _decision_records.append(DecisionRecord(
                        cycle_at=now,
                        symbol=symbol,
                        strategy_name=strategy_name,
                        trading_mode=_tm,
                        strategy_version=_sv,
                        decision="skipped_existing_position",
                        reject_stage=None,
                        reject_reason=None,
                        entry_level=None,
                        signal_bar_close=None,
                        relative_volume=None,
                        atr=None,
                        stop_price=None,
                        limit_price=None,
                        initial_stop_price=None,
                        quantity=None,
                        risk_per_share=None,
                        equity=None,
                        filter_results={},
                        vix_close=_ctx_vix_close,
                        vix_above_sma=_ctx_vix_above_sma,
                        sector_passing_pct=_ctx_sector_passing_pct,
                    ))
                    continue
                bars = intraday_bars_by_symbol.get(symbol, ())
                daily_bars = daily_bars_by_symbol.get(symbol, ())
                if not bars or not daily_bars:
                    continue
                latest_bar = bars[-1]
                if (symbol, session_day(latest_bar.timestamp, settings)) in traded_symbols_today:
                    _decision_records.append(DecisionRecord(
                        cycle_at=now,
                        symbol=symbol,
                        strategy_name=strategy_name,
                        trading_mode=_tm,
                        strategy_version=_sv,
                        decision="skipped_already_traded",
                        reject_stage=None,
                        reject_reason=None,
                        entry_level=None,
                        signal_bar_close=None,
                        relative_volume=None,
                        atr=None,
                        stop_price=None,
                        limit_price=None,
                        initial_stop_price=None,
                        quantity=None,
                        risk_per_share=None,
                        equity=None,
                        filter_results={},
                        vix_close=_ctx_vix_close,
                        vix_above_sma=_ctx_vix_above_sma,
                        sector_passing_pct=_ctx_sector_passing_pct,
                    ))
                    continue

                if not is_extended:
                    bar_age_seconds = (now - latest_bar.timestamp.astimezone(timezone.utc)).total_seconds()
                    if bar_age_seconds > 2 * settings.entry_timeframe_minutes * 60:
                        continue

                # News filter: skip entry if catalyst headline detected for this symbol.
                if settings.enable_news_filter and news_by_symbol is not None:
                    symbol_news = news_by_symbol.get(symbol, [])
                    if any(
                        any(kw in item.headline.lower() for kw in settings.news_filter_keywords)
                        for item in symbol_news
                    ):
                        _news_blocked.append(symbol)
                        continue

                # Spread filter: skip entry if NBBO spread exceeds threshold.
                if settings.enable_spread_filter and quotes_by_symbol is not None:
                    quote = quotes_by_symbol.get(symbol)
                    spread_threshold = (
                        settings.extended_hours_max_spread_pct
                        if is_extended
                        else settings.max_spread_pct
                    )
                    if quote is not None and quote.spread_pct > spread_threshold:
                        _spread_blocked.append(symbol)
                        continue

                if session_type is SessionType.AFTER_HOURS:
                    signal_index = next(
                        (
                            i
                            for i in range(len(bars) - 1, -1, -1)
                            if _is_entry_window(bars[i].timestamp, settings, SessionType.REGULAR)
                        ),
                        -1,
                    )
                    if signal_index < 0:
                        continue
                    signal_bar_age_s = (
                        now - bars[signal_index].timestamp.astimezone(timezone.utc)
                    ).total_seconds()
                    if signal_bar_age_s > settings.extended_hours_signal_max_age_minutes * 60:
                        continue
                else:
                    signal_index = len(bars) - 1

                signal = signal_evaluator(
                    symbol=symbol,
                    intraday_bars=bars,
                    signal_index=signal_index,
                    daily_bars=daily_bars,
                    settings=settings,
                )
                if signal is None:
                    _decision_records.append(DecisionRecord(
                        cycle_at=now,
                        symbol=symbol,
                        strategy_name=strategy_name,
                        trading_mode=_tm,
                        strategy_version=_sv,
                        decision="skipped_no_signal",
                        reject_stage=None,
                        reject_reason=None,
                        entry_level=None,
                        signal_bar_close=None,
                        relative_volume=None,
                        atr=None,
                        stop_price=None,
                        limit_price=None,
                        initial_stop_price=None,
                        quantity=None,
                        risk_per_share=None,
                        equity=None,
                        filter_results={},
                        vix_close=_ctx_vix_close,
                        vix_above_sma=_ctx_vix_above_sma,
                        sector_passing_pct=_ctx_sector_passing_pct,
                    ))
                    continue

                # VWAP entry filter: reject when signal bar close < session VWAP.
                # Fail-open: None VWAP (empty bars) never blocks.
                if settings.enable_vwap_entry_filter:
                    _vwap = calculate_vwap(bars)
                    if _vwap is not None and signal.signal_bar.close < _vwap:
                        _decision_records.append(DecisionRecord(
                            cycle_at=now,
                            symbol=symbol,
                            strategy_name=strategy_name,
                            trading_mode=_tm,
                            strategy_version=_sv,
                            decision="rejected",
                            reject_stage="vwap_filter",
                            reject_reason="below_vwap",
                            entry_level=None,
                            signal_bar_close=signal.signal_bar.close,
                            relative_volume=signal.relative_volume,
                            atr=None,
                            stop_price=None,
                            limit_price=None,
                            initial_stop_price=None,
                            quantity=None,
                            risk_per_share=None,
                            equity=equity,
                            filter_results={"vwap": False},
                            vix_close=_ctx_vix_close,
                            vix_above_sma=_ctx_vix_above_sma,
                            sector_passing_pct=_ctx_sector_passing_pct,
                            vwap_at_signal=_vwap,
                            signal_bar_above_vwap=False,
                        ))
                        continue
                    _candidate_vwap[symbol] = (_vwap, True if _vwap is not None else None)

                if signal.option_contract is not None:
                    # Option entry: defined risk = premium; no stop needed
                    quantity = calculate_option_position_size(
                        equity=equity,
                        ask=signal.option_contract.ask,
                        settings=settings,
                    )
                    if quantity < 1:
                        continue
                    contract = signal.option_contract
                    _candidate_signals[contract.occ_symbol] = (
                        signal.entry_level,
                        signal.signal_bar.close,
                        signal.relative_volume,
                    )
                    entry_candidates.append(
                        (
                            round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                            round(signal.relative_volume, 6),
                            CycleIntent(
                                intent_type=CycleIntentType.ENTRY,
                                symbol=contract.occ_symbol,
                                timestamp=signal.signal_bar.timestamp,
                                quantity=quantity,
                                stop_price=None,
                                limit_price=contract.ask,
                                initial_stop_price=None,
                                client_order_id=_client_order_id(
                                    settings=settings,
                                    symbol=contract.occ_symbol,
                                    signal_timestamp=signal.signal_bar.timestamp,
                                    strategy_name=strategy_name,
                                    is_option=True,
                                ),
                                signal_timestamp=signal.signal_bar.timestamp,
                                strategy_name=strategy_name,
                                underlying_symbol=symbol,
                                is_option=True,
                                option_strike=contract.strike,
                                option_expiry=contract.expiry,
                                option_type_str=contract.option_type,
                            ),
                        )
                    )
                else:
                    # Equity entry: stop-based sizing
                    if signal.initial_stop_price >= signal.limit_price:
                        continue
                    if signal.limit_price - signal.initial_stop_price < 0.01:
                        continue
                    cap_stop = round(signal.limit_price * (1 - settings.max_stop_pct), 2)
                    effective_initial_stop = max(signal.initial_stop_price, cap_stop)
                    fractionable = signal.symbol in settings.fractionable_symbols
                    quantity = calculate_position_size(
                        equity=equity,
                        entry_price=signal.limit_price,
                        stop_price=effective_initial_stop,
                        settings=settings,
                        fractionable=fractionable,
                    )
                    if quantity <= 0.0:
                        continue
                    if (
                        settings.min_position_notional > 0
                        and quantity * signal.limit_price < settings.min_position_notional
                    ):
                        continue
                    _candidate_signals[symbol] = (
                        signal.entry_level,
                        signal.signal_bar.close,
                        signal.relative_volume,
                    )
                    entry_candidates.append(
                        (
                            round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                            round(signal.relative_volume, 6),
                            CycleIntent(
                                intent_type=CycleIntentType.ENTRY,
                                symbol=symbol,
                                timestamp=signal.signal_bar.timestamp,
                                quantity=quantity,
                                stop_price=signal.stop_price,
                                limit_price=signal.limit_price,
                                initial_stop_price=effective_initial_stop,
                                client_order_id=_client_order_id(
                                    settings=settings,
                                    symbol=symbol,
                                    signal_timestamp=signal.signal_bar.timestamp,
                                    strategy_name=strategy_name,
                                ),
                                signal_timestamp=signal.signal_bar.timestamp,
                                strategy_name=strategy_name,
                            ),
                        )
                    )

            entry_candidates.sort(
                key=lambda item: (-item[0], -item[1], item[2].symbol),
            )
            selected: list[CycleIntent] = []
            for *_rank, candidate in entry_candidates:
                if len(selected) >= available_slots:
                    break
                candidate_exposure = (
                    (candidate.limit_price or 0.0) * (candidate.quantity or 0) / equity
                    if equity > 0
                    else 0.0
                )
                if current_exposure + candidate_exposure > settings.max_portfolio_exposure_pct:
                    continue
                selected.append(candidate)
                current_exposure += candidate_exposure
            _selected_symbols = {c.symbol for c in selected}
            for *_rank, candidate in entry_candidates:
                _sig = _candidate_signals.get(candidate.symbol, (None, None, None))
                _accepted = candidate.symbol in _selected_symbols
                _rps = (
                    round(candidate.limit_price - candidate.initial_stop_price, 4)
                    if candidate.limit_price is not None and candidate.initial_stop_price is not None
                    else None
                )
                _vwap_info = _candidate_vwap.get(candidate.symbol, (None, None))
                _decision_records.append(DecisionRecord(
                    cycle_at=now,
                    symbol=candidate.symbol,
                    strategy_name=candidate.strategy_name,
                    trading_mode=_tm,
                    strategy_version=_sv,
                    decision="accepted" if _accepted else "rejected",
                    reject_stage=None if _accepted else "capacity",
                    reject_reason=None if _accepted else "capacity_full",
                    entry_level=_sig[0],
                    signal_bar_close=_sig[1],
                    relative_volume=_sig[2],
                    atr=None,
                    stop_price=candidate.stop_price,
                    limit_price=candidate.limit_price,
                    initial_stop_price=candidate.initial_stop_price,
                    quantity=candidate.quantity,
                    risk_per_share=_rps,
                    equity=equity,
                    filter_results={},
                    vix_close=_ctx_vix_close,
                    vix_above_sma=_ctx_vix_above_sma,
                    sector_passing_pct=_ctx_sector_passing_pct,
                    vwap_at_signal=_vwap_info[0],
                    signal_bar_above_vwap=_vwap_info[1],
                ))
            intents.extend(selected)

    intents.sort(key=lambda intent: (intent.timestamp, intent.symbol, intent.intent_type.value))
    return CycleResult(
        as_of=now,
        intents=intents,
        regime_blocked=_regime_entries_blocked,
        vix_blocked=_vix_entries_blocked,
        sector_blocked=_sector_entries_blocked,
        news_blocked_symbols=tuple(sorted(_news_blocked)),
        spread_blocked_symbols=tuple(sorted(_spread_blocked)),
        decision_records=tuple(_decision_records),
    )


def _client_order_id(
    *,
    settings: Settings,
    symbol: str,
    signal_timestamp: datetime,
    strategy_name: str = "breakout",
    is_option: bool = False,
) -> str:
    prefix = "option" if is_option else strategy_name
    return (
        f"{prefix}:"
        f"{settings.strategy_version}:"
        f"{signal_timestamp.date().isoformat()}:"
        f"{symbol}:entry:{signal_timestamp.isoformat()}"
    )
