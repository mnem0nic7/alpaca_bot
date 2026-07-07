# tests/unit/test_portfolio_pooled_trades.py
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, ReplayScenario
from alpaca_bot.replay.audit import run_audit
from alpaca_bot.replay.break_even import run_break_even_sweep
from alpaca_bot.replay.portfolio import (
    portfolio_basket_pooled_trades,
    portfolio_pooled_trades,
)

ENV = {
    "TRADING_MODE": "paper", "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout", "DATABASE_URL": "postgresql://u:p@h:5432/d",
    "MARKET_DATA_FEED": "sip", "SYMBOLS": "AAA,BBB", "ENTRY_TIMEFRAME_MINUTES": "15",
}


def _bar(symbol, ts, o=100.0, h=101.0, l=99.0, c=100.5, v=1000):
    return Bar(symbol=symbol, timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _utc(h, mi):
    return datetime(2026, 1, 2, h, mi, tzinfo=timezone.utc)


def _scn(symbol):
    intraday = [
        _bar(symbol, _utc(14, 30), o=100, h=106, l=99, c=105, v=5000),
        _bar(symbol, _utc(14, 45), o=100.5, h=107, l=100, c=106, v=5000),
        _bar(symbol, _utc(15, 0), o=106, h=108, l=80, c=107, v=5000),
    ]
    daily = [_bar(symbol, datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc))]
    return ReplayScenario(name=symbol, symbol=symbol, starting_equity=100000.0,
                          daily_bars=daily, intraday_bars=intraday)


def _fake_registry(monkeypatch):
    def fake_eval(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[signal_index]
        if bar.timestamp != _utc(14, 30):
            return None
        return EntrySignal(symbol=symbol, signal_bar=bar, entry_level=100.0,
                           relative_volume=2.0, stop_price=99.0, limit_price=100.5,
                           initial_stop_price=99.0, option_contract=None)
    monkeypatch.setattr(
        "alpaca_bot.strategy.STRATEGY_REGISTRY", {"breakout": fake_eval}, raising=False
    )
    monkeypatch.setattr(
        "alpaca_bot.replay.portfolio.STRATEGY_REGISTRY", {"breakout": fake_eval}, raising=False
    )


def test_portfolio_pooled_trades_matches_pooledtradesfn_shape(monkeypatch):
    _fake_registry(monkeypatch)
    settings = Settings.from_env(ENV)
    trades = portfolio_pooled_trades([_scn("AAA"), _scn("BBB")], settings, "breakout")
    assert {t.symbol for t in trades} == {"AAA", "BBB"}
    assert all(hasattr(t, "pnl") for t in trades)


def test_portfolio_pooled_trades_scores_fractional_quantity_pnl(monkeypatch):
    _fake_registry(monkeypatch)
    settings = replace(
        Settings.from_env({**ENV, "REPLAY_SLIPPAGE_BPS": "0"}),
        fractionable_symbols=frozenset({"AAA"}),
    )

    trades = portfolio_pooled_trades([_scn("AAA")], settings, "breakout")

    assert len(trades) == 1
    trade = trades[0]
    assert trade.quantity != int(trade.quantity)
    assert trade.pnl == pytest.approx(
        (trade.exit_price - trade.entry_price) * trade.quantity
    )


def test_portfolio_replay_blocks_same_day_retry_after_unfilled_entry_attempt(
    monkeypatch,
):
    def fake_eval(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        del symbol, daily_bars, settings
        bar = intraday_bars[signal_index]
        if bar.timestamp == _utc(14, 30):
            return EntrySignal(
                symbol="AAA",
                signal_bar=bar,
                entry_level=110.0,
                relative_volume=2.0,
                stop_price=110.0,
                limit_price=111.0,
                initial_stop_price=100.0,
                option_contract=None,
            )
        if bar.timestamp == _utc(15, 0):
            return EntrySignal(
                symbol="AAA",
                signal_bar=bar,
                entry_level=100.0,
                relative_volume=2.0,
                stop_price=100.0,
                limit_price=101.0,
                initial_stop_price=99.0,
                option_contract=None,
            )
        return None

    monkeypatch.setattr(
        "alpaca_bot.strategy.STRATEGY_REGISTRY", {"breakout": fake_eval}, raising=False
    )
    monkeypatch.setattr(
        "alpaca_bot.replay.portfolio.STRATEGY_REGISTRY", {"breakout": fake_eval}, raising=False
    )
    settings = Settings.from_env(
        {**ENV, "REPLAY_SLIPPAGE_BPS": "0", "ENTRY_MIN_CLOSE_TO_ENTRY_PCT": "-1.0"}
    )
    intraday = [
        _bar("AAA", _utc(14, 30), o=100, h=101, l=99, c=100, v=5000),
        _bar("AAA", _utc(14, 45), o=100, h=105, l=99, c=100, v=5000),
        _bar("AAA", _utc(15, 0), o=100, h=101, l=99, c=100, v=5000),
        _bar("AAA", _utc(15, 15), o=100, h=101, l=98, c=99, v=5000),
    ]
    scenario = ReplayScenario(
        name="AAA",
        symbol="AAA",
        starting_equity=100000.0,
        daily_bars=[_bar("AAA", datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc))],
        intraday_bars=intraday,
    )

    trades = portfolio_pooled_trades([scenario], settings, "breakout")

    assert trades == []


def test_portfolio_pooled_trades_reports_progress(monkeypatch):
    _fake_registry(monkeypatch)
    settings = Settings.from_env(ENV)
    messages: list[str] = []

    portfolio_pooled_trades(
        [_scn("AAA"), _scn("BBB")],
        settings,
        "breakout",
        on_progress=messages.append,
    )

    assert messages
    assert messages[-1].startswith(f"breakout {settings.replay_slippage_bps:g}bps")
    assert "replay 100%" in messages[-1]
    assert "timestamps" in messages[-1]


def test_portfolio_basket_pooled_trades_scores_multiple_strategies(monkeypatch):
    def alpha(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        del daily_bars, settings
        bar = intraday_bars[signal_index]
        if symbol == "AAA" and bar.timestamp == _utc(14, 30):
            return EntrySignal(
                symbol=symbol,
                signal_bar=bar,
                entry_level=100.0,
                relative_volume=2.0,
                stop_price=99.0,
                limit_price=100.5,
                initial_stop_price=99.0,
                option_contract=None,
            )
        return None

    def beta(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        del daily_bars, settings
        bar = intraday_bars[signal_index]
        if symbol == "BBB" and bar.timestamp == _utc(14, 30):
            return EntrySignal(
                symbol=symbol,
                signal_bar=bar,
                entry_level=100.0,
                relative_volume=2.0,
                stop_price=99.0,
                limit_price=100.5,
                initial_stop_price=99.0,
                option_contract=None,
            )
        return None

    monkeypatch.setattr(
        "alpaca_bot.replay.portfolio.STRATEGY_REGISTRY",
        {"alpha": alpha, "beta": beta},
        raising=False,
    )
    settings = Settings.from_env(ENV)
    trades = portfolio_basket_pooled_trades(
        [_scn("AAA"), _scn("BBB")],
        settings,
        ["alpha", "beta"],
    )

    assert {trade.symbol for trade in trades} == {"AAA", "BBB"}


def test_portfolio_basket_pooled_trades_reports_progress(monkeypatch):
    _fake_registry(monkeypatch)
    settings = Settings.from_env(ENV)
    messages: list[str] = []

    portfolio_basket_pooled_trades(
        [_scn("AAA"), _scn("BBB")],
        settings,
        ["breakout"],
        on_progress=messages.append,
    )

    assert messages
    assert messages[-1].startswith(f"breakout {settings.replay_slippage_bps:g}bps")
    assert "replay 100%" in messages[-1]


def test_injectable_into_run_audit(monkeypatch):
    _fake_registry(monkeypatch)
    settings = Settings.from_env(ENV)
    rows = run_audit(
        scenarios=[_scn("AAA"), _scn("BBB")], settings=settings,
        strategies=["breakout"], slippage_bps=5.0,
        pooled_trades_fn=portfolio_pooled_trades,
    )
    assert len(rows) == 1
    assert rows[0].strategy == "breakout"


def test_injectable_into_break_even_sweep(monkeypatch):
    _fake_registry(monkeypatch)
    settings = Settings.from_env(ENV)
    res = run_break_even_sweep(
        scenarios=[_scn("AAA"), _scn("BBB")], settings=settings,
        strategy="breakout", slippage_ladder=[0.0, 5.0],
        pooled_trades_fn=portfolio_pooled_trades,
    )
    assert res.strategy == "breakout"
    assert len(res.points) == 2
