from __future__ import annotations

from dataclasses import dataclass, field
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
    database_url: str = field(repr=False)
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
    trailing_stop_atr_multiplier: float = 0.0
    trailing_stop_profit_trigger_r: float = 1.0
    market_timezone: ZoneInfo = ZoneInfo("America/New_York")
    dashboard_auth_enabled: bool = False
    dashboard_auth_username: str | None = None
    dashboard_auth_password_hash: str | None = field(default=None, repr=False)
    alpaca_paper_api_key: str | None = field(default=None, repr=False)
    alpaca_paper_secret_key: str | None = field(default=None, repr=False)
    alpaca_live_api_key: str | None = field(default=None, repr=False)
    alpaca_live_secret_key: str | None = field(default=None, repr=False)
    slack_webhook_url: str | None = field(default=None, repr=False)
    notify_email_from: str | None = None
    notify_email_to: str | None = None
    notify_smtp_host: str | None = None
    notify_smtp_port: int = 587
    notify_smtp_user: str | None = None
    notify_smtp_password: str | None = field(default=None, repr=False)
    # Extended hours trading
    extended_hours_enabled: bool = False
    pre_market_entry_window_start: time = time(4, 0)
    pre_market_entry_window_end: time = time(9, 20)
    after_hours_entry_window_start: time = time(16, 5)
    after_hours_entry_window_end: time = time(19, 30)
    extended_hours_flatten_time: time = time(19, 45)
    extended_hours_limit_offset_pct: float = 0.001
    vwap_dip_threshold_pct: float = 0.015
    gap_threshold_pct: float = 0.02
    gap_volume_threshold: float = 2.0
    bull_flag_min_run_pct: float = 0.02
    bull_flag_consolidation_volume_ratio: float = 0.6
    bull_flag_consolidation_range_pct: float = 0.5
    bb_period: int = 20
    bb_std_dev: float = 2.0
    bb_squeeze_threshold_pct: float = 0.03
    bb_squeeze_min_bars: int = 5
    failed_breakdown_volume_ratio: float = 2.0
    failed_breakdown_recapture_buffer_pct: float = 0.001
    enable_trend_filter_exit: bool = False
    enable_vwap_breakdown_exit: bool = False
    vwap_breakdown_min_bars: int = 1
    viability_daily_bar_max_age_days: int = 5
    viability_min_hold_minutes: int = 0
    per_symbol_loss_limit_pct: float = 0.0
    # Data source filters
    enable_regime_filter: bool = True
    regime_symbol: str = "SPY"
    regime_sma_period: int = 20
    enable_news_filter: bool = True
    news_filter_lookback_hours: int = 24
    news_filter_keywords: tuple[str, ...] = (
        "earnings", "revenue", "fda", "clinical", "trial", "guidance"
    )
    enable_spread_filter: bool = True
    max_spread_pct: float = 0.002
    option_dte_min: int = 21
    option_dte_max: int = 60
    option_delta_target: float = 0.50

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
                values.get("MARKET_DATA_FEED", MarketDataFeed.IEX).strip().lower()
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
            trailing_stop_atr_multiplier=float(
                values.get("TRAILING_STOP_ATR_MULTIPLIER", "0.0")
            ),
            trailing_stop_profit_trigger_r=float(
                values.get("TRAILING_STOP_PROFIT_TRIGGER_R", "1.0")
            ),
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
            extended_hours_enabled=_parse_bool(
                "EXTENDED_HOURS_ENABLED", values.get("EXTENDED_HOURS_ENABLED", "false")
            ),
            pre_market_entry_window_start=_parse_time(
                "PRE_MARKET_ENTRY_WINDOW_START",
                values.get("PRE_MARKET_ENTRY_WINDOW_START", "04:00"),
            ),
            pre_market_entry_window_end=_parse_time(
                "PRE_MARKET_ENTRY_WINDOW_END",
                values.get("PRE_MARKET_ENTRY_WINDOW_END", "09:20"),
            ),
            after_hours_entry_window_start=_parse_time(
                "AFTER_HOURS_ENTRY_WINDOW_START",
                values.get("AFTER_HOURS_ENTRY_WINDOW_START", "16:05"),
            ),
            after_hours_entry_window_end=_parse_time(
                "AFTER_HOURS_ENTRY_WINDOW_END",
                values.get("AFTER_HOURS_ENTRY_WINDOW_END", "19:30"),
            ),
            extended_hours_flatten_time=_parse_time(
                "EXTENDED_HOURS_FLATTEN_TIME",
                values.get("EXTENDED_HOURS_FLATTEN_TIME", "19:45"),
            ),
            extended_hours_limit_offset_pct=float(
                values.get("EXTENDED_HOURS_LIMIT_OFFSET_PCT", "0.001")
            ),
            vwap_dip_threshold_pct=float(
                values.get("VWAP_DIP_THRESHOLD_PCT", "0.015")
            ),
            gap_threshold_pct=float(values.get("GAP_THRESHOLD_PCT", "0.02")),
            gap_volume_threshold=float(values.get("GAP_VOLUME_THRESHOLD", "2.0")),
            bull_flag_min_run_pct=float(values.get("BULL_FLAG_MIN_RUN_PCT", "0.02")),
            bull_flag_consolidation_volume_ratio=float(
                values.get("BULL_FLAG_CONSOLIDATION_VOLUME_RATIO", "0.6")
            ),
            bull_flag_consolidation_range_pct=float(
                values.get("BULL_FLAG_CONSOLIDATION_RANGE_PCT", "0.5")
            ),
            bb_period=int(values.get("BB_PERIOD", "20")),
            bb_std_dev=float(values.get("BB_STD_DEV", "2.0")),
            bb_squeeze_threshold_pct=float(values.get("BB_SQUEEZE_THRESHOLD_PCT", "0.03")),
            bb_squeeze_min_bars=int(values.get("BB_SQUEEZE_MIN_BARS", "5")),
            failed_breakdown_volume_ratio=float(
                values.get("FAILED_BREAKDOWN_VOLUME_RATIO", "2.0")
            ),
            failed_breakdown_recapture_buffer_pct=float(
                values.get("FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT", "0.001")
            ),
            enable_trend_filter_exit=_parse_bool(
                "ENABLE_TREND_FILTER_EXIT", values.get("ENABLE_TREND_FILTER_EXIT", "false")
            ),
            enable_vwap_breakdown_exit=_parse_bool(
                "ENABLE_VWAP_BREAKDOWN_EXIT", values.get("ENABLE_VWAP_BREAKDOWN_EXIT", "false")
            ),
            vwap_breakdown_min_bars=int(values.get("VWAP_BREAKDOWN_MIN_BARS", "1")),
            viability_daily_bar_max_age_days=int(
                values.get("VIABILITY_DAILY_BAR_MAX_AGE_DAYS", "5")
            ),
            viability_min_hold_minutes=int(values.get("VIABILITY_MIN_HOLD_MINUTES", "0")),
            per_symbol_loss_limit_pct=float(values.get("PER_SYMBOL_LOSS_LIMIT_PCT", "0.0")),
            enable_regime_filter=_parse_bool(
                "ENABLE_REGIME_FILTER", values.get("ENABLE_REGIME_FILTER", "false")
            ),
            regime_symbol=values.get("REGIME_SYMBOL", "SPY"),
            regime_sma_period=int(values.get("REGIME_SMA_PERIOD", "20")),
            enable_news_filter=_parse_bool(
                "ENABLE_NEWS_FILTER", values.get("ENABLE_NEWS_FILTER", "false")
            ),
            news_filter_lookback_hours=int(values.get("NEWS_FILTER_LOOKBACK_HOURS", "24")),
            news_filter_keywords=tuple(
                kw.strip().lower()
                for kw in values.get(
                    "NEWS_FILTER_KEYWORDS",
                    "earnings,revenue,fda,clinical,trial,guidance",
                ).split(",")
                if kw.strip()
            ),
            enable_spread_filter=_parse_bool(
                "ENABLE_SPREAD_FILTER", values.get("ENABLE_SPREAD_FILTER", "false")
            ),
            max_spread_pct=float(values.get("MAX_SPREAD_PCT", "0.002")),
            option_dte_min=int(values.get("OPTION_DTE_MIN", "21")),
            option_dte_max=int(values.get("OPTION_DTE_MAX", "60")),
            option_delta_target=float(values.get("OPTION_DELTA_TARGET", "0.50")),
        )
        return settings

    def validate(self) -> None:
        if self.trading_mode is TradingMode.LIVE and not self.enable_live_trading:
            raise ValueError("ENABLE_LIVE_TRADING=true is required when TRADING_MODE=live")
        if self.enable_live_trading and self.trading_mode is not TradingMode.LIVE:
            raise ValueError("TRADING_MODE=live is required when ENABLE_LIVE_TRADING=true")

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
        if self.entry_timeframe_minutes < 1:
            raise ValueError("ENTRY_TIMEFRAME_MINUTES must be at least 1")
        if self.atr_stop_multiplier <= 0:
            raise ValueError("ATR_STOP_MULTIPLIER must be positive")
        if self.atr_stop_multiplier > 10.0:
            raise ValueError("ATR_STOP_MULTIPLIER must be <= 10.0 (got a suspiciously large value)")
        if self.trailing_stop_atr_multiplier < 0:
            raise ValueError("TRAILING_STOP_ATR_MULTIPLIER must be >= 0")
        if self.trailing_stop_atr_multiplier > 10.0:
            raise ValueError(
                "TRAILING_STOP_ATR_MULTIPLIER must be <= 10.0 (got a suspiciously large value)"
            )
        if self.trailing_stop_profit_trigger_r <= 0:
            raise ValueError("TRAILING_STOP_PROFIT_TRIGGER_R must be > 0")
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be at least 1")
        if not 1 <= self.notify_smtp_port <= 65535:
            raise ValueError("NOTIFY_SMTP_PORT must be between 1 and 65535")
        if self.max_position_pct > self.max_portfolio_exposure_pct:
            raise ValueError(
                "MAX_POSITION_PCT must be <= MAX_PORTFOLIO_EXPOSURE_PCT"
            )
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
        if self.extended_hours_limit_offset_pct <= 0:
            raise ValueError("EXTENDED_HOURS_LIMIT_OFFSET_PCT must be positive")
        if self.vwap_dip_threshold_pct <= 0:
            raise ValueError("VWAP_DIP_THRESHOLD_PCT must be positive")
        if self.vwap_dip_threshold_pct >= 1.0:
            raise ValueError("VWAP_DIP_THRESHOLD_PCT must be less than 1.0")
        if self.gap_threshold_pct <= 0:
            raise ValueError("GAP_THRESHOLD_PCT must be positive")
        if self.gap_threshold_pct >= 1.0:
            raise ValueError("GAP_THRESHOLD_PCT must be less than 1.0")
        if self.gap_volume_threshold <= 0:
            raise ValueError("GAP_VOLUME_THRESHOLD must be positive")
        if self.bull_flag_min_run_pct <= 0 or self.bull_flag_min_run_pct >= 1.0:
            raise ValueError("BULL_FLAG_MIN_RUN_PCT must be > 0 and < 1.0")
        if (
            self.bull_flag_consolidation_volume_ratio <= 0
            or self.bull_flag_consolidation_volume_ratio >= 1.0
        ):
            raise ValueError("BULL_FLAG_CONSOLIDATION_VOLUME_RATIO must be > 0 and < 1.0")
        if (
            self.bull_flag_consolidation_range_pct <= 0
            or self.bull_flag_consolidation_range_pct >= 1.0
        ):
            raise ValueError("BULL_FLAG_CONSOLIDATION_RANGE_PCT must be > 0 and < 1.0")
        if self.bb_period < 2:
            raise ValueError("BB_PERIOD must be >= 2")
        if self.bb_std_dev <= 0 or self.bb_std_dev > 5.0:
            raise ValueError("BB_STD_DEV must be > 0 and <= 5.0")
        if self.bb_squeeze_threshold_pct <= 0 or self.bb_squeeze_threshold_pct >= 1.0:
            raise ValueError("BB_SQUEEZE_THRESHOLD_PCT must be > 0 and < 1.0")
        if self.bb_squeeze_min_bars < 1:
            raise ValueError("BB_SQUEEZE_MIN_BARS must be >= 1")
        if self.failed_breakdown_volume_ratio <= 0:
            raise ValueError("FAILED_BREAKDOWN_VOLUME_RATIO must be > 0")
        if (
            self.failed_breakdown_recapture_buffer_pct <= 0
            or self.failed_breakdown_recapture_buffer_pct >= 1.0
        ):
            raise ValueError("FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT must be > 0 and < 1.0")
        if self.per_symbol_loss_limit_pct < 0:
            raise ValueError("PER_SYMBOL_LOSS_LIMIT_PCT must be >= 0")
        if self.per_symbol_loss_limit_pct >= 1.0:
            raise ValueError("PER_SYMBOL_LOSS_LIMIT_PCT must be < 1.0")
        if self.regime_sma_period < 2:
            raise ValueError("REGIME_SMA_PERIOD must be >= 2")
        if self.option_dte_min < 1:
            raise ValueError("OPTION_DTE_MIN must be at least 1")
        if self.option_dte_max <= self.option_dte_min:
            raise ValueError("OPTION_DTE_MAX must be greater than OPTION_DTE_MIN")
        if not 0.0 < self.option_delta_target <= 1.0:
            raise ValueError("OPTION_DELTA_TARGET must be between 0 (exclusive) and 1.0 (inclusive)")
        if self.extended_hours_enabled:
            if self.pre_market_entry_window_start >= self.pre_market_entry_window_end:
                raise ValueError(
                    "PRE_MARKET_ENTRY_WINDOW_START must be before PRE_MARKET_ENTRY_WINDOW_END"
                )
            if self.pre_market_entry_window_end >= time(9, 30):
                raise ValueError(
                    "PRE_MARKET_ENTRY_WINDOW_END must be before 09:30 (regular open)"
                )
            if self.after_hours_entry_window_start <= time(16, 0):
                raise ValueError(
                    "AFTER_HOURS_ENTRY_WINDOW_START must be after 16:00 (regular close)"
                )
            if self.after_hours_entry_window_start >= self.after_hours_entry_window_end:
                raise ValueError(
                    "AFTER_HOURS_ENTRY_WINDOW_START must be before AFTER_HOURS_ENTRY_WINDOW_END"
                )
            if self.after_hours_entry_window_end >= self.extended_hours_flatten_time:
                raise ValueError(
                    "EXTENDED_HOURS_FLATTEN_TIME must be after AFTER_HOURS_ENTRY_WINDOW_END"
                )


def _validate_positive_fraction(name: str, value: float) -> None:
    if not 0 < value < 1:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")
