from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.runtime.daily_summary import trailing_consecutive_losses, build_intraday_digest
from alpaca_bot.storage import AuditEvent, DailySessionState
from alpaca_bot.execution import BrokerAccount


# ── Shared helpers ────────────────────────────────────────────────────────────

_SESSION_DATE = date(2026, 5, 6)
_NOW = datetime(2026, 5, 6, 18, 30, tzinfo=timezone.utc)  # 14:30 ET


def _make_settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _trade(
    exit_fill: float = 155.0,
    entry_fill: float = 150.0,
    qty: int = 10,
    exit_time: str = "2026-05-06T14:00:00+00:00",
) -> dict:
    return {
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "qty": qty,
        "exit_time": exit_time,
    }


# ── Settings tests ────────────────────────────────────────────────────────────


def test_settings_intraday_digest_interval_cycles_default_zero():
    s = _make_settings()
    assert s.intraday_digest_interval_cycles == 0


def test_settings_intraday_consecutive_loss_gate_default_zero():
    s = _make_settings()
    assert s.intraday_consecutive_loss_gate == 0


def test_settings_intraday_digest_interval_cycles_parsed():
    s = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
    assert s.intraday_digest_interval_cycles == 60


def test_settings_intraday_consecutive_loss_gate_parsed():
    s = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
    assert s.intraday_consecutive_loss_gate == 3


def test_settings_intraday_digest_interval_cycles_negative_raises():
    with pytest.raises(ValueError, match="INTRADAY_DIGEST_INTERVAL_CYCLES"):
        _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="-1")


def test_settings_intraday_consecutive_loss_gate_negative_raises():
    with pytest.raises(ValueError, match="INTRADAY_CONSECUTIVE_LOSS_GATE"):
        _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="-1")


# ── trailing_consecutive_losses tests ─────────────────────────────────────────


class TestTrailingConsecutiveLosses:
    def test_empty_list_returns_zero(self):
        assert trailing_consecutive_losses([]) == 0

    def test_all_wins_returns_zero(self):
        trades = [
            _trade(exit_fill=155.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=156.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 0

    def test_all_losses_returns_count(self):
        trades = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            _trade(exit_fill=146.0, entry_fill=150.0, exit_time="2026-05-06T14:03:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 3

    def test_win_after_losses_returns_zero(self):
        """Most recent trade is a win — streak resets."""
        trades = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            _trade(exit_fill=160.0, entry_fill=150.0, exit_time="2026-05-06T14:03:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 0

    def test_losses_after_win_returns_loss_count(self):
        """Most recent trades are losses — streak counts them, stops at earlier win."""
        trades = [
            _trade(exit_fill=160.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:03:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 2

    def test_trades_missing_fills_are_skipped(self):
        """Trades without entry_fill or exit_fill are ignored (not counted as win or loss)."""
        trades = [
            {"entry_fill": None, "exit_fill": None, "qty": 10, "exit_time": "2026-05-06T14:01:00+00:00"},
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        # Only one scoreable trade, which is a loss → streak = 1
        assert trailing_consecutive_losses(trades) == 1

    def test_only_missing_fills_returns_zero(self):
        trades = [
            {"entry_fill": None, "exit_fill": None, "qty": 10, "exit_time": "2026-05-06T14:01:00+00:00"},
        ]
        assert trailing_consecutive_losses(trades) == 0

    def test_sorted_by_exit_time(self):
        """Streak is computed from exit_time order, not list order."""
        trades = [
            # This loss appears first in the list but has a later exit_time
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            # This win appears second but has an earlier exit_time
            _trade(exit_fill=160.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
        ]
        # After sorting by exit_time: win (14:01), loss (14:02) → most recent is loss → streak=1
        assert trailing_consecutive_losses(trades) == 1


# ── build_intraday_digest tests ───────────────────────────────────────────────


class TestBuildIntradayDigest:
    def _call(self, **overrides):
        defaults = dict(
            settings=_make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60"),
            trades=[],
            open_positions=[],
            baseline_equity=46_100.0,
            current_equity=46_242.80,
            cycle_num=60,
            timestamp=_NOW,
            session_date=_SESSION_DATE,
        )
        defaults.update(overrides)
        return build_intraday_digest(**defaults)

    def test_subject_contains_session_date_mode_and_time(self):
        subject, _ = self._call()
        assert "2026-05-06" in subject
        assert "paper" in subject
        assert "14:30" in subject  # _NOW is 18:30 UTC = 14:30 ET

    def test_subject_is_intraday_digest(self):
        subject, _ = self._call()
        assert "Intra-day digest" in subject

    def test_body_contains_cycle_info(self):
        _, body = self._call(cycle_num=60)
        assert "Cycle: 60/60" in body

    def test_body_with_zero_trades(self):
        _, body = self._call(trades=[])
        assert "Trades: 0" in body
        assert "Win rate" not in body

    def test_body_with_trades_shows_win_rate(self):
        trades = [
            _trade(exit_fill=155.0, entry_fill=150.0),  # win
            _trade(exit_fill=155.0, entry_fill=150.0),  # win
            _trade(exit_fill=155.0, entry_fill=150.0),  # win
            _trade(exit_fill=148.0, entry_fill=150.0),  # loss
        ]
        _, body = self._call(trades=trades)
        assert "75.0%" in body
        assert "3W / 1L" in body

    def test_body_shows_pnl(self):
        trades = [_trade(exit_fill=155.0, entry_fill=150.0, qty=10)]  # $50 gain
        _, body = self._call(trades=trades)
        assert "$50.00" in body

    def test_loss_limit_headroom_calculation(self):
        # baseline=46100, daily_loss_limit_pct=0.01 → limit=461.00
        # current=45900 → session_pnl=-200 → headroom=461-200=261.00
        _, body = self._call(
            settings=_make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60", DAILY_LOSS_LIMIT_PCT="0.01"),
            baseline_equity=46_100.0,
            current_equity=45_900.0,
        )
        assert "$261.00" in body
        assert "$461.00" in body

    def test_open_positions_listed(self):
        positions = [
            SimpleNamespace(symbol="AAPL", quantity=10, entry_price=182.50),
            SimpleNamespace(symbol="MSFT", quantity=5, entry_price=415.20),
        ]
        _, body = self._call(open_positions=positions)
        assert "Open positions: 2" in body
        assert "AAPL x10 @ 182.50" in body
        assert "MSFT x5 @ 415.20" in body

    def test_no_open_positions(self):
        _, body = self._call(open_positions=[])
        assert "Open positions: 0" in body
