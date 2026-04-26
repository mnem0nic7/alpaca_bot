from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import StrEnum
import os
from zoneinfo import ZoneInfo


class TradingMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class MarketDataFeed(StrEnum):
    IEX = "iex"
    SIP = "sip"


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean-like value, got {value!r}")


def _parse_time(name: str, value: str) -> time:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"{name} must use HH:MM format, got {value!r}")

    hour, minute = parts
    parsed = time(hour=int(hour), minute=int(minute))
    return parsed


def _parse_symbols(value: str) -> tuple[str, ...]:
    symbols = tuple(symbol.strip().upper() for symbol in value.split(",") if symbol.strip())
    if not symbols:
        raise ValueError("SYMBOLS must contain at least one symbol")
    return symbols


def _get_required(environ: dict[str, str], name: str) -> str:
    try:
        return environ[name]
    except KeyError as exc:
        raise ValueError(f"Missing required environment variable: {name}") from exc


@dataclass(frozen=True)
class Settings:
    trading_mode: TradingMode
    enable_live_trading: bool
    strategy_version: str
    database_url: str
    market_data_feed: MarketDataFeed
    symbols: tuple[str, ...]
    daily_sma_period: int
    breakout_lookback_bars: int
    relative_volume_lookback_bars: int
    relative_volume_threshold: float
    entry_timeframe_minutes: int
    risk_per_trade_pct: float
    max_position_pct: float
    max_open_positions: int
    daily_loss_limit_pct: float
    stop_limit_buffer_pct: float
    breakout_stop_buffer_pct: float
    entry_stop_price_buffer: float
    entry_window_start: time
    entry_window_end: time
    flatten_time: time
    max_portfolio_exposure_pct: float = 0.15
    notify_slippage_threshold_pct: float = 0.005
    prior_day_high_lookback_bars: int = 1
    orb_opening_bars: int = 2
    high_watermark_lookback_days: int = 252
    ema_period: int = 9
    atr_period: int = 14
    atr_stop_multiplier: float = 1.5
    market_timezone: ZoneInfo = ZoneInfo("America/New_York")
    dashboard_auth_enabled: bool = False
    dashboard_auth_username: str | None = None
    dashboard_auth_password_hash: str | None = None
    alpaca_paper_api_key: str | None = None
    alpaca_paper_secret_key: str | None = None
    alpaca_live_api_key: str | None = None
    alpaca_live_secret_key: str | None = None
    slack_webhook_url: str | None = None
    notify_email_from: str | None = None
    notify_email_to: str | None = None
    notify_smtp_host: str | None = None
    notify_smtp_port: int = 587
    notify_smtp_user: str | None = None
    notify_smtp_password: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        values = dict(os.environ if environ is None else environ)
        settings = cls(
            trading_mode=TradingMode(_get_required(values, "TRADING_MODE").strip().lower()),
            enable_live_trading=_parse_bool(
                "ENABLE_LIVE_TRADING", values.get("ENABLE_LIVE_TRADING", "false")
            ),
            strategy_version=_get_required(values, "STRATEGY_VERSION").strip(),
            database_url=_get_required(values, "DATABASE_URL").strip(),
            market_data_feed=MarketDataFeed(
                values.get("MARKET_DATA_FEED", MarketDataFeed.SIP).strip().lower()
            ),
            symbols=_parse_symbols(_get_required(values, "SYMBOLS")),
            daily_sma_period=int(values.get("DAILY_SMA_PERIOD", "20")),
            breakout_lookback_bars=int(values.get("BREAKOUT_LOOKBACK_BARS", "20")),
            relative_volume_lookback_bars=int(
                values.get("RELATIVE_VOLUME_LOOKBACK_BARS", "20")
            ),
            relative_volume_threshold=float(values.get("RELATIVE_VOLUME_THRESHOLD", "1.5")),
            entry_timeframe_minutes=int(values.get("ENTRY_TIMEFRAME_MINUTES", "15")),
            risk_per_trade_pct=float(values.get("RISK_PER_TRADE_PCT", "0.0025")),
            max_position_pct=float(values.get("MAX_POSITION_PCT", "0.05")),
            max_open_positions=int(values.get("MAX_OPEN_POSITIONS", "3")),
            daily_loss_limit_pct=float(values.get("DAILY_LOSS_LIMIT_PCT", "0.01")),
            max_portfolio_exposure_pct=float(
                values.get("MAX_PORTFOLIO_EXPOSURE_PCT", "0.15")
            ),
            notify_slippage_threshold_pct=float(
                values.get("NOTIFY_SLIPPAGE_THRESHOLD_PCT", "0.005")
            ),
            prior_day_high_lookback_bars=int(values.get("PRIOR_DAY_HIGH_LOOKBACK_BARS", "1")),
            orb_opening_bars=int(values.get("ORB_OPENING_BARS", "2")),
            high_watermark_lookback_days=int(values.get("HIGH_WATERMARK_LOOKBACK_DAYS", "252")),
            ema_period=int(values.get("EMA_PERIOD", "9")),
            atr_period=int(values.get("ATR_PERIOD", "14")),
            atr_stop_multiplier=float(values.get("ATR_STOP_MULTIPLIER", "1.5")),
            stop_limit_buffer_pct=float(values.get("STOP_LIMIT_BUFFER_PCT", "0.001")),
            breakout_stop_buffer_pct=float(
                values.get("BREAKOUT_STOP_BUFFER_PCT", "0.001")
            ),
            entry_stop_price_buffer=float(values.get("ENTRY_STOP_PRICE_BUFFER", "0.01")),
            entry_window_start=_parse_time(
                "ENTRY_WINDOW_START", values.get("ENTRY_WINDOW_START", "10:00")
            ),
            entry_window_end=_parse_time(
                "ENTRY_WINDOW_END", values.get("ENTRY_WINDOW_END", "15:30")
            ),
            flatten_time=_parse_time("FLATTEN_TIME", values.get("FLATTEN_TIME", "15:45")),
            dashboard_auth_enabled=_parse_bool(
                "DASHBOARD_AUTH_ENABLED", values.get("DASHBOARD_AUTH_ENABLED", "false")
            ),
            dashboard_auth_username=values.get("DASHBOARD_AUTH_USERNAME"),
            dashboard_auth_password_hash=values.get("DASHBOARD_AUTH_PASSWORD_HASH"),
            alpaca_paper_api_key=values.get("ALPACA_PAPER_API_KEY"),
            alpaca_paper_secret_key=values.get("ALPACA_PAPER_SECRET_KEY"),
            alpaca_live_api_key=values.get("ALPACA_LIVE_API_KEY"),
            alpaca_live_secret_key=values.get("ALPACA_LIVE_SECRET_KEY"),
            slack_webhook_url=values.get("SLACK_WEBHOOK_URL"),
            notify_email_from=values.get("NOTIFY_EMAIL_FROM"),
            notify_email_to=values.get("NOTIFY_EMAIL_TO"),
            notify_smtp_host=values.get("NOTIFY_SMTP_HOST"),
            notify_smtp_port=int(values.get("NOTIFY_SMTP_PORT", "587")),
            notify_smtp_user=values.get("NOTIFY_SMTP_USER"),
            notify_smtp_password=values.get("NOTIFY_SMTP_PASSWORD"),
        )
        return settings

    def validate(self) -> None:
        if self.trading_mode is TradingMode.LIVE and not self.enable_live_trading:
            raise ValueError("ENABLE_LIVE_TRADING=true is required when TRADING_MODE=live")

        if self.entry_window_start >= self.entry_window_end:
            raise ValueError("ENTRY_WINDOW_START must be before ENTRY_WINDOW_END")
        if self.entry_window_end >= self.flatten_time:
            raise ValueError("ENTRY_WINDOW_END must be before FLATTEN_TIME")

        if not 0 < self.max_portfolio_exposure_pct <= 1.0:
            raise ValueError(
                "MAX_PORTFOLIO_EXPOSURE_PCT must be between 0 (exclusive) and 1.0 (inclusive)"
            )
        if self.notify_slippage_threshold_pct < 0:
            raise ValueError("NOTIFY_SLIPPAGE_THRESHOLD_PCT must be >= 0")
        _validate_positive_fraction("RISK_PER_TRADE_PCT", self.risk_per_trade_pct)
        _validate_positive_fraction("MAX_POSITION_PCT", self.max_position_pct)
        _validate_positive_fraction("DAILY_LOSS_LIMIT_PCT", self.daily_loss_limit_pct)
        _validate_positive_fraction("STOP_LIMIT_BUFFER_PCT", self.stop_limit_buffer_pct)
        _validate_positive_fraction(
            "BREAKOUT_STOP_BUFFER_PCT", self.breakout_stop_buffer_pct
        )
        if self.entry_stop_price_buffer <= 0:
            raise ValueError("ENTRY_STOP_PRICE_BUFFER must be positive")
        if self.daily_sma_period < 2:
            raise ValueError("DAILY_SMA_PERIOD must be at least 2")
        if self.breakout_lookback_bars < 2:
            raise ValueError("BREAKOUT_LOOKBACK_BARS must be at least 2")
        if self.relative_volume_lookback_bars < 2:
            raise ValueError("RELATIVE_VOLUME_LOOKBACK_BARS must be at least 2")
        if self.relative_volume_threshold <= 1.0:
            raise ValueError("RELATIVE_VOLUME_THRESHOLD must be greater than 1.0")
        if self.prior_day_high_lookback_bars < 1:
            raise ValueError("PRIOR_DAY_HIGH_LOOKBACK_BARS must be at least 1")
        if self.orb_opening_bars < 1:
            raise ValueError("ORB_OPENING_BARS must be at least 1")
        if self.high_watermark_lookback_days < 5:
            raise ValueError("HIGH_WATERMARK_LOOKBACK_DAYS must be at least 5")
        if self.ema_period < 2:
            raise ValueError("EMA_PERIOD must be at least 2")
        if self.atr_period < 2:
            raise ValueError("ATR_PERIOD must be at least 2")
        if self.atr_stop_multiplier <= 0:
            raise ValueError("ATR_STOP_MULTIPLIER must be positive")
        if self.atr_stop_multiplier > 10.0:
            raise ValueError("ATR_STOP_MULTIPLIER must be <= 10.0 (got a suspiciously large value)")
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be at least 1")
        if self.dashboard_auth_enabled:
            if not self.dashboard_auth_username:
                raise ValueError(
                    "DASHBOARD_AUTH_USERNAME is required when DASHBOARD_AUTH_ENABLED=true"
                )
            if not self.dashboard_auth_password_hash:
                raise ValueError(
                    "DASHBOARD_AUTH_PASSWORD_HASH is required when DASHBOARD_AUTH_ENABLED=true"
                )
        if self.notify_email_from or self.notify_email_to:
            for value, name in [
                (self.notify_email_from, "NOTIFY_EMAIL_FROM"),
                (self.notify_email_to, "NOTIFY_EMAIL_TO"),
                (self.notify_smtp_host, "NOTIFY_SMTP_HOST"),
                (self.notify_smtp_user, "NOTIFY_SMTP_USER"),
                (self.notify_smtp_password, "NOTIFY_SMTP_PASSWORD"),
            ]:
                if not value:
                    raise ValueError(
                        f"{name} is required when any NOTIFY_EMAIL_* var is set"
                    )


def _validate_positive_fraction(name: str, value: float) -> None:
    if not 0 < value < 1:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")
