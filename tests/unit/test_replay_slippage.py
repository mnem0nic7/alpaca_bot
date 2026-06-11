from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"

# Same base env as tests/unit/test_replay_golden.py make_settings, so the
# golden scenario produces the same trades here.
BASE_ENV = {
    "TRADING_MODE": "paper",
    "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout",
    "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
    "MARKET_DATA_FEED": "sip",
    "SYMBOLS": "AAPL,MSFT,SPY",
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
    "ATR_PERIOD": "14",
}


def make_settings(**overrides: str) -> Settings:
    values = dict(BASE_ENV)
    values.update(overrides)
    return Settings.from_env(values)


def test_slipped_buy_is_adverse_up():
    runner = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="20"))
    assert runner._slipped(100.0, side="buy") == 100.2  # +20 bps


def test_slipped_sell_is_adverse_down():
    runner = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="20"))
    assert runner._slipped(100.0, side="sell") == 99.8  # -20 bps


def test_slipped_zero_bps_is_identity():
    runner = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="0"))
    assert runner._slipped(123.456, side="buy") == 123.456
    assert runner._slipped(123.456, side="sell") == 123.456


def test_costed_replay_never_beats_frictionless():
    """End-to-end directional check on the golden breakout scenario.

    Compares return_pct, not pnl: the slipped entry price feeds position
    sizing, so quantity can differ between runs and pnl is not directly
    comparable per trade. return_pct = (exit - entry) / entry is
    quantity-independent and must be strictly worse with costs.
    """
    scenario = ReplayRunner.load_scenario(GOLDEN_DIR / "breakout_success.json")

    free = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="0")).run(scenario)
    costed = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="20")).run(scenario)

    free_trades = free.backtest_report.trades
    costed_trades = costed.backtest_report.trades
    assert len(free_trades) == len(costed_trades)  # triggers use unslipped prices
    assert len(free_trades) >= 1, "golden scenario must produce at least one trade"
    for f, c in zip(free_trades, costed_trades):
        assert c.entry_price >= f.entry_price
        assert c.exit_price <= f.exit_price
        assert c.return_pct < f.return_pct
