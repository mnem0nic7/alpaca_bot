from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import math
import os
from pathlib import Path
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


def _parse_date(name: str, value: str) -> date:
    if (
        len(value) != 10
        or value[4] != "-"
        or value[7] != "-"
        or not value[:4].isdigit()
        or not value[5:7].isdigit()
        or not value[8:].isdigit()
    ):
        raise ValueError(f"{name} must use YYYY-MM-DD format, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD format, got {value!r}") from exc


def _parse_optional_float(values: dict[str, str], name: str) -> float | None:
    raw_value = values.get(name)
    if raw_value is None or raw_value.strip() == "":
        return None
    return float(raw_value)


def _parse_symbols(value: str) -> tuple[str, ...]:
    symbols = tuple(symbol.strip().upper() for symbol in value.split(",") if symbol.strip())
    if not symbols:
        raise ValueError("SYMBOLS must contain at least one symbol")
    return symbols


def _parse_csv_names(name: str, value: str) -> tuple[str, ...]:
    names = tuple(item.strip() for item in value.split(",") if item.strip())
    if not names:
        raise ValueError(f"{name} must contain at least one name")
    return names


def _append_unique_name(names: tuple[str, ...], name: str) -> tuple[str, ...]:
    if not name or name in names:
        return names
    return (*names, name)


def _paper_strategy_promotion_denylist(values: dict[str, str]) -> frozenset[str]:
    raw_value = values.get(
        "PAPER_STRATEGY_PROMOTION_DENYLIST",
        "ema_pullback,vwap_cross",
    ).strip()
    if not raw_value or raw_value.lower() == "none":
        return frozenset()
    return frozenset(
        _parse_csv_names("PAPER_STRATEGY_PROMOTION_DENYLIST", raw_value)
    )


def _as_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_marker_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_json_object(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _replay_asset_snapshot_identity(
    payload: dict[str, object],
    *,
    summary_path: Path,
) -> tuple[str, str, int, int, int] | None:
    snapshot = payload.get("fractionability_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        return None
    snapshot_sha256 = str(snapshot.get("snapshot_sha256") or "").lower()
    universe_sha256 = str(snapshot.get("universe_sha256") or "").lower()
    if (
        len(snapshot_sha256) != 64
        or len(universe_sha256) != 64
        or any(char not in "0123456789abcdef" for char in snapshot_sha256)
        or any(char not in "0123456789abcdef" for char in universe_sha256)
    ):
        return None
    universe_count = _as_int(snapshot.get("universe_symbol_count"))
    fractionable_count = _as_int(snapshot.get("fractionable_symbol_count"))
    non_fractionable_count = _as_int(
        snapshot.get("non_fractionable_symbol_count")
    )
    if (
        universe_count is None
        or fractionable_count is None
        or non_fractionable_count is None
        or universe_count <= 0
        or fractionable_count < 0
        or non_fractionable_count < 0
        or fractionable_count + non_fractionable_count != universe_count
    ):
        return None
    snapshot_file = str(snapshot.get("snapshot_file") or "").strip()
    universe_file = str(snapshot.get("universe_symbols_file") or "").strip()
    if not snapshot_file or not universe_file:
        return None
    snapshot_path = Path(snapshot_file)
    universe_path = Path(universe_file)
    if not snapshot_path.is_absolute():
        snapshot_path = summary_path.parent / snapshot_path
    if not universe_path.is_absolute():
        universe_path = summary_path.parent / universe_path
    try:
        snapshot_bytes = snapshot_path.read_bytes()
        universe_bytes = universe_path.read_bytes()
        fractionable_symbols = set(snapshot_bytes.decode("utf-8").split())
        universe_symbols = set(universe_bytes.decode("utf-8").split())
    except (OSError, UnicodeDecodeError):
        return None
    if (
        hashlib.sha256(snapshot_bytes).hexdigest() != snapshot_sha256
        or hashlib.sha256(universe_bytes).hexdigest() != universe_sha256
        or len(fractionable_symbols) != fractionable_count
        or len(universe_symbols) != universe_count
        or not fractionable_symbols.issubset(universe_symbols)
        or len(universe_symbols - fractionable_symbols) != non_fractionable_count
    ):
        return None
    return (
        snapshot_sha256,
        universe_sha256,
        universe_count,
        fractionable_count,
        non_fractionable_count,
    )


def _marker_approved_strategy(values: dict[str, str]) -> str | None:
    marker_text = values.get("PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER", "").strip()
    if not marker_text:
        return None
    marker_path = Path(marker_text)
    marker_payload = _load_json_object(marker_path)
    if marker_payload is None or marker_payload.get("schema_version") != 3:
        return None

    strategy = str(marker_payload.get("strategy") or "").strip()
    if not strategy or any(not (char.isalnum() or char in "_:-") for char in strategy):
        return None
    if strategy in _paper_strategy_promotion_denylist(values):
        return None
    if str(marker_payload.get("strategy_version") or "").strip() != values.get(
        "STRATEGY_VERSION", ""
    ).strip():
        return None

    evidence_root_text = str(marker_payload.get("evidence_root") or "").strip()
    if not evidence_root_text:
        return None
    evidence_root = Path(evidence_root_text)

    expected_env_file = values.get(
        "PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE", ""
    ).strip()
    marker_env_file = str(marker_payload.get("env_file") or "").strip()
    if expected_env_file and marker_env_file != expected_env_file:
        return None

    validation_summary_text = str(marker_payload.get("validation_summary") or "").strip()
    if not validation_summary_text:
        return None
    validation_summary_path = Path(validation_summary_text)
    latest_validation_path = evidence_root / "latest_validation" / "summary.json"
    try:
        if validation_summary_path.resolve() != latest_validation_path.resolve():
            return None
    except OSError:
        return None
    validation_payload = _load_json_object(validation_summary_path)
    if validation_payload is None:
        return None
    try:
        validation_summary_bytes = validation_summary_path.read_bytes()
        validation_modified_at = datetime.fromtimestamp(
            validation_summary_path.stat().st_mtime,
            timezone.utc,
        )
    except OSError:
        return None
    validation_sha256 = hashlib.sha256(validation_summary_bytes).hexdigest()
    if (
        str(marker_payload.get("validation_summary_sha256") or "").strip()
        != validation_sha256
    ):
        return None
    prefilter_summary_text = str(
        validation_payload.get("prefilter_summary_json") or ""
    ).strip()
    prefilter_sha256 = str(
        validation_payload.get("prefilter_summary_sha256") or ""
    ).strip()
    latest_prefilter_path = evidence_root / "latest" / "summary.json"
    if not prefilter_summary_text or not prefilter_sha256:
        return None
    prefilter_summary_path = Path(prefilter_summary_text)
    try:
        if prefilter_summary_path.resolve() != latest_prefilter_path.resolve():
            return None
        prefilter_summary_bytes = prefilter_summary_path.read_bytes()
        prefilter_modified_at = datetime.fromtimestamp(
            prefilter_summary_path.stat().st_mtime,
            timezone.utc,
        )
    except OSError:
        return None
    if hashlib.sha256(prefilter_summary_bytes).hexdigest() != prefilter_sha256:
        return None
    prefilter_payload = _load_json_object(prefilter_summary_path)
    if prefilter_payload is None or validation_modified_at < prefilter_modified_at:
        return None
    proof_summary_text = str(
        marker_payload.get("proof_horizon_summary") or ""
    ).strip()
    if not proof_summary_text:
        return None
    proof_summary_path = Path(proof_summary_text)
    latest_proof_path = evidence_root / "latest_proof_horizon" / "summary.json"
    try:
        if proof_summary_path.resolve() != latest_proof_path.resolve():
            return None
    except OSError:
        return None
    proof_payload = _load_json_object(proof_summary_path)
    if proof_payload is None:
        return None
    try:
        proof_summary_bytes = proof_summary_path.read_bytes()
        proof_modified_at = datetime.fromtimestamp(
            proof_summary_path.stat().st_mtime,
            timezone.utc,
        )
    except OSError:
        return None
    if proof_modified_at < validation_modified_at:
        return None
    prefilter_assets = _replay_asset_snapshot_identity(
        prefilter_payload,
        summary_path=prefilter_summary_path,
    )
    validation_assets = _replay_asset_snapshot_identity(
        validation_payload,
        summary_path=validation_summary_path,
    )
    proof_assets = _replay_asset_snapshot_identity(
        proof_payload,
        summary_path=proof_summary_path,
    )
    if (
        prefilter_assets is None
        or prefilter_assets != validation_assets
        or prefilter_assets != proof_assets
    ):
        return None
    proof_sha256 = hashlib.sha256(proof_summary_bytes).hexdigest()
    if (
        str(marker_payload.get("proof_horizon_summary_sha256") or "").strip()
        != proof_sha256
    ):
        return None
    confirmation = str(marker_payload.get("confirmation") or "").strip()
    if confirmation != (
        f"approve-{strategy}-paper-promotion-sha256-{validation_sha256}"
        f"-proof-sha256-{proof_sha256}"
    ):
        return None

    approved_at = _parse_marker_datetime(marker_payload.get("approved_at"))
    now = datetime.now(timezone.utc)
    if (
        approved_at is None
        or approved_at > now + timedelta(minutes=5)
        or approved_at
        < max(prefilter_modified_at, validation_modified_at, proof_modified_at)
    ):
        return None

    proof_selection = proof_payload.get("candidate_selection")
    if not isinstance(proof_selection, dict):
        return None
    if proof_selection.get("schema_version") != 1:
        return None
    if str(proof_selection.get("selected_candidate") or "").strip() != strategy:
        return None
    selected_scale = str(
        proof_selection.get("selected_candidate_scale") or ""
    ).strip()
    if not selected_scale:
        return None
    if str(proof_selection.get("selection_reason") or "").strip() != "first_passing":
        return None
    passing_candidate_count = _as_int(
        proof_selection.get("passing_candidate_count")
    )
    candidate_count = _as_int(proof_selection.get("candidate_count"))
    selection_min_pass_rate = _as_float(
        proof_selection.get("min_eventual_pass_rate")
    )
    required_pass_rate = _as_float(
        values.get(
            "PROMOTE_VALIDATED_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE",
            values.get(
                "PROOF_STATUS_SECOND_STRATEGY_MIN_PROOF_HORIZON_PASS_RATE",
                "0.50",
            ),
        )
    )
    required_proof_trades = _as_int(
        values.get("PROMOTE_VALIDATED_STRATEGY_MIN_CANDIDATE_TRADES", "30")
    )
    if (
        passing_candidate_count is None
        or passing_candidate_count < 1
        or candidate_count is None
        or candidate_count < 1
        or selection_min_pass_rate is None
        or required_pass_rate is None
        or required_proof_trades is None
        or required_proof_trades < 1
        or selection_min_pass_rate < required_pass_rate
    ):
        return None
    proof_rows = proof_selection.get("rows")
    if not isinstance(proof_rows, list):
        return None
    matching_proof_rows = [
        row
        for row in proof_rows
        if isinstance(row, dict)
        and str(row.get("candidate") or "").strip() == strategy
        and str(row.get("candidate_scale") or "").strip() == selected_scale
        and row.get("status") == "ok"
    ]
    if len(matching_proof_rows) != 1:
        return None
    proof_strategy_parts = {
        part.strip()
        for part in str(proof_payload.get("strategy") or "").split("+")
        if part.strip()
    }
    proof_scales = proof_payload.get("confidence_scales")
    proof_scale = (
        _as_float(proof_scales.get(strategy))
        if isinstance(proof_scales, dict)
        else None
    )
    selected_scale_value = _as_float(selected_scale)
    proof_total_pnl = _as_float(proof_payload.get("total_pnl"))
    proof_min_pnl = _as_float(proof_payload.get("min_pnl"))
    proof_eventual_pass_rate = _as_float(proof_payload.get("eventual_pass_rate"))
    proof_starts_passed = _as_int(proof_payload.get("starts_eventually_passed"))
    proof_historical_starts = _as_int(
        proof_payload.get("historical_starts_checked")
    )
    proof_trades = _as_int(proof_payload.get("trades"))
    if proof_min_pnl is None:
        proof_min_pnl = 0.01
    if (
        strategy not in proof_strategy_parts
        or proof_scale is None
        or selected_scale_value is None
        or not math.isclose(
            proof_scale,
            selected_scale_value,
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
        or proof_total_pnl is None
        or proof_total_pnl < proof_min_pnl
        or proof_eventual_pass_rate is None
        or proof_eventual_pass_rate < required_pass_rate
        or proof_starts_passed is None
        or proof_starts_passed < 1
        or proof_historical_starts is None
        or proof_historical_starts < 1
        or proof_trades is None
        or proof_trades < required_proof_trades
    ):
        return None
    proof_marker_values = {
        "proof_horizon_trades": proof_trades,
        "proof_horizon_total_pnl": proof_total_pnl,
        "proof_horizon_eventual_pass_rate": proof_eventual_pass_rate,
        "proof_horizon_starts_eventually_passed": proof_starts_passed,
        "proof_horizon_historical_starts": proof_historical_starts,
        "proof_horizon_selection_reason": "first_passing",
        "proof_horizon_candidate_count": candidate_count,
        "proof_horizon_passing_candidate_count": passing_candidate_count,
    }
    for key, expected_value in proof_marker_values.items():
        marker_value = marker_payload.get(key)
        if isinstance(expected_value, float):
            parsed_marker_value = _as_float(marker_value)
            if parsed_marker_value is None or not math.isclose(
                parsed_marker_value,
                expected_value,
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                return None
        elif marker_value != expected_value:
            return None

    rows = validation_payload.get("rows")
    if not isinstance(rows, list):
        return None
    marker_values = {
        "candidate_scale": str(marker_payload.get("candidate_scale") or ""),
        "candidate_trades": _as_int(marker_payload.get("candidate_trades")),
        "candidate_total_pnl": _as_float(marker_payload.get("candidate_total_pnl")),
        "candidate_ci_low": _as_float(marker_payload.get("candidate_ci_low")),
        "candidate_p_mean_le_zero": _as_float(
            marker_payload.get("candidate_p_mean_le_zero")
        ),
    }
    if any(value is None or value == "" for value in marker_values.values()):
        return None
    if marker_values["candidate_scale"] != selected_scale:
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("candidate") or "").strip() != strategy:
            continue
        if row.get("status") != "passed":
            continue
        if row.get("verdict") != "positive-edge":
            continue
        if row.get("candidate_verdict") != "positive-edge":
            continue
        if row.get("candidate_contribution_status") != "positive_pnl":
            continue
        row_trades = _as_int(row.get("candidate_trades"))
        row_total_pnl = _as_float(row.get("candidate_total_pnl"))
        row_ci_low = _as_float(row.get("candidate_ci_low"))
        row_p_mean_le_zero = _as_float(row.get("candidate_p_mean_le_zero"))
        if (
            row_trades is None
            or row_total_pnl is None
            or row_ci_low is None
            or row_p_mean_le_zero is None
            or row_trades < 30
            or row_total_pnl <= 0.0
            or row_ci_low <= 0.0
            or row_p_mean_le_zero > 0.05
        ):
            continue
        if str(row.get("candidate_scale") or "") != marker_values["candidate_scale"]:
            continue
        if row_trades != marker_values["candidate_trades"]:
            continue
        if abs(row_total_pnl - float(marker_values["candidate_total_pnl"])) > 1e-9:
            continue
        if abs(row_ci_low - float(marker_values["candidate_ci_low"])) > 1e-9:
            continue
        if (
            abs(
                row_p_mean_le_zero
                - float(marker_values["candidate_p_mean_le_zero"])
            )
            > 1e-9
        ):
            continue
        return strategy
    return None


def _paper_approved_strategies(values: dict[str, str]) -> tuple[str, ...]:
    approved = _parse_csv_names(
        "PAPER_APPROVED_STRATEGIES",
        values.get("PAPER_APPROVED_STRATEGIES", "bull_flag"),
    )
    denied = _paper_strategy_promotion_denylist(values)
    approved = tuple(name for name in approved if name not in denied)
    return _append_unique_name(approved, _marker_approved_strategy(values) or "")


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
    entry_min_close_to_entry_pct: float = -1.0
    entry_max_close_to_entry_pct: float = 1.0
    entry_order_active_bars: int = 1
    entry_candidate_rank_mode: str = "close_to_entry"
    max_portfolio_exposure_pct: float = 0.30
    notify_slippage_threshold_pct: float = 0.005
    confidence_floor: float = 0.25
    paper_proof_freeze: bool = False
    paper_approved_strategies: tuple[str, ...] = ("bull_flag",)
    paper_readiness_max_pass_age_minutes: int = 180
    paper_readiness_min_watchlist_symbols: int = 900
    paper_readiness_decision_dry_run_strategy: str = "bull_flag"
    paper_readiness_decision_dry_run_min_records: int = 900
    paper_readiness_decision_dry_run_min_evaluations: int = 6
    profit_probe_start_date: date = date(2026, 7, 7)
    floor_raise_step: float = 0.10
    drawdown_raise_pct: float = 0.05
    losing_streak_n: int = 3
    vol_raise_threshold: float = 0.025
    floor_auto_raise_max_age_days: int = 7
    prior_day_high_lookback_bars: int = 1
    orb_opening_bars: int = 2
    orb_relative_volume_threshold: float | None = None
    orb_atr_stop_multiplier: float | None = None
    high_watermark_lookback_days: int = 252
    ema_period: int = 9
    atr_period: int = 14
    atr_stop_multiplier: float = 1.0
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
    extended_hours_max_spread_pct: float = 0.01
    extended_hours_signal_max_age_minutes: int = 60
    vwap_dip_threshold_pct: float = 0.015
    vwap_reversion_relative_volume_threshold: float | None = None
    vwap_reversion_atr_stop_multiplier: float | None = None
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
    enable_no_follow_through_exit: bool = False
    no_follow_through_exit_minutes: int = 0
    no_follow_through_min_favorable_pct: float = 0.0025
    enable_giveback_exit: bool = False
    giveback_exit_min_favorable_pct: float = 0.0025
    giveback_exit_max_return_pct: float = 0.0
    enable_early_loss_exit: bool = False
    early_loss_exit_minutes: int = 0
    early_loss_exit_return_pct: float = 0.01
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
    enable_options_trading: bool = False
    option_chain_min_total_volume: int = 0
    option_chain_symbols: tuple[str, ...] = ()
    option_chain_snapshot_dir: str | None = None
    option_chain_request_timeout_seconds: float = 10.0
    option_stop_buffer_pct: float = 0.10
    option_max_spread_pct: float = 0.50
    option_min_open_interest: int = 0
    max_stop_pct: float = 0.05
    enable_profit_trail: bool = False
    profit_trail_pct: float = 0.95
    enable_profit_target: bool = False
    profit_target_r: float = 2.0
    # Adverse per-side slippage applied to every simulated replay fill, in
    # basis points. Sweep and nightly inherit it via the shared ReplayRunner.
    replay_slippage_bps: float = 5.0
    trend_filter_exit_lookback_days: int = 1
    enable_breakeven_stop: bool = True
    breakeven_trigger_pct: float = 0.0025
    breakeven_trail_pct: float = 0.002
    # Not from env — populated at startup after broker lookup
    fractionable_symbols: frozenset[str] = field(default_factory=frozenset)
    # From env — configurable threshold; 0.0 = disabled (default)
    min_position_notional: float = 0.0
    # Intra-day review: 0 = disabled (default)
    intraday_digest_interval_cycles: int = 0
    intraday_consecutive_loss_gate: int = 0
    max_loss_per_trade_dollars: float | None = None
    enable_vix_filter: bool = False
    vix_proxy_symbol: str = "VIXY"
    vix_lookback_bars: int = 20
    enable_sector_filter: bool = False
    sector_etf_symbols: tuple[str, ...] = (
        "XLK", "XLF", "XLE", "XLV", "XLU", "XLI", "XLB", "XLRE", "XLC", "XLY", "XLP"
    )
    sector_etf_sma_period: int = 20
    sector_filter_min_passing_pct: float = 0.5
    enable_vwap_entry_filter: bool = False
    option_strategy_max_rolling_loss_usd: float = 0.0
    option_strategy_rolling_loss_days: int = 7

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        values = dict(os.environ if environ is None else environ)
        trading_mode = TradingMode(_get_required(values, "TRADING_MODE").strip().lower())
        entry_min_close_to_entry_pct_default = (
            "-0.01" if trading_mode is TradingMode.PAPER else "-1.0"
        )
        settings = cls(
            trading_mode=trading_mode,
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
            entry_order_active_bars=int(values.get("ENTRY_ORDER_ACTIVE_BARS", "1")),
            entry_candidate_rank_mode=values.get(
                "ENTRY_CANDIDATE_RANK_MODE", "close_to_entry"
            ).strip().lower(),
            risk_per_trade_pct=float(values.get("RISK_PER_TRADE_PCT", "0.0025")),
            max_position_pct=float(values.get("MAX_POSITION_PCT", "0.015")),
            max_open_positions=int(values.get("MAX_OPEN_POSITIONS", "20")),
            daily_loss_limit_pct=float(values.get("DAILY_LOSS_LIMIT_PCT", "0.01")),
            max_portfolio_exposure_pct=float(
                values.get("MAX_PORTFOLIO_EXPOSURE_PCT", "0.30")
            ),
            notify_slippage_threshold_pct=float(
                values.get("NOTIFY_SLIPPAGE_THRESHOLD_PCT", "0.005")
            ),
            confidence_floor=float(values.get("CONFIDENCE_FLOOR", "0.25")),
            paper_proof_freeze=_parse_bool(
                "PAPER_PROOF_FREEZE", values.get("PAPER_PROOF_FREEZE", "false")
            ),
            paper_approved_strategies=_paper_approved_strategies(values),
            paper_readiness_max_pass_age_minutes=int(
                values.get("PAPER_READINESS_MAX_PASS_AGE_MINUTES", "180")
            ),
            paper_readiness_min_watchlist_symbols=int(
                values.get("PAPER_READINESS_MIN_WATCHLIST_SYMBOLS", "900")
            ),
            paper_readiness_decision_dry_run_strategy=values.get(
                "PAPER_READINESS_DECISION_DRY_RUN_STRATEGY",
                values.get("PROFIT_PROBE_STRATEGY", "bull_flag"),
            ),
            paper_readiness_decision_dry_run_min_records=int(
                values.get("PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS", "900")
            ),
            paper_readiness_decision_dry_run_min_evaluations=int(
                values.get("PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS", "6")
            ),
            profit_probe_start_date=_parse_date(
                "PROFIT_PROBE_START_DATE",
                values.get("PROFIT_PROBE_START_DATE", "2026-07-07"),
            ),
            floor_raise_step=float(values.get("FLOOR_RAISE_STEP", "0.10")),
            drawdown_raise_pct=float(values.get("DRAWDOWN_RAISE_PCT", "0.05")),
            losing_streak_n=int(values.get("LOSING_STREAK_N", "3")),
            vol_raise_threshold=float(values.get("VOL_RAISE_THRESHOLD", "0.025")),
            floor_auto_raise_max_age_days=int(
                values.get("FLOOR_AUTO_RAISE_MAX_AGE_DAYS", "7")
            ),
            prior_day_high_lookback_bars=int(values.get("PRIOR_DAY_HIGH_LOOKBACK_BARS", "1")),
            orb_opening_bars=int(values.get("ORB_OPENING_BARS", "2")),
            orb_relative_volume_threshold=_parse_optional_float(
                values, "ORB_RELATIVE_VOLUME_THRESHOLD"
            ),
            orb_atr_stop_multiplier=_parse_optional_float(
                values, "ORB_ATR_STOP_MULTIPLIER"
            ),
            high_watermark_lookback_days=int(values.get("HIGH_WATERMARK_LOOKBACK_DAYS", "252")),
            ema_period=int(values.get("EMA_PERIOD", "9")),
            atr_period=int(values.get("ATR_PERIOD", "14")),
            atr_stop_multiplier=float(values.get("ATR_STOP_MULTIPLIER", "1.0")),
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
            entry_min_close_to_entry_pct=float(
                values.get(
                    "ENTRY_MIN_CLOSE_TO_ENTRY_PCT",
                    entry_min_close_to_entry_pct_default,
                )
            ),
            entry_max_close_to_entry_pct=float(
                values.get("ENTRY_MAX_CLOSE_TO_ENTRY_PCT", "1.0")
            ),
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
            extended_hours_max_spread_pct=float(
                values.get("EXTENDED_HOURS_MAX_SPREAD_PCT", "0.01")
            ),
            extended_hours_signal_max_age_minutes=int(
                values.get("EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES", "60")
            ),
            vwap_dip_threshold_pct=float(
                values.get("VWAP_DIP_THRESHOLD_PCT", "0.015")
            ),
            vwap_reversion_relative_volume_threshold=_parse_optional_float(
                values, "VWAP_REVERSION_RELATIVE_VOLUME_THRESHOLD"
            ),
            vwap_reversion_atr_stop_multiplier=_parse_optional_float(
                values, "VWAP_REVERSION_ATR_STOP_MULTIPLIER"
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
            enable_no_follow_through_exit=_parse_bool(
                "ENABLE_NO_FOLLOW_THROUGH_EXIT",
                values.get("ENABLE_NO_FOLLOW_THROUGH_EXIT", "false"),
            ),
            no_follow_through_exit_minutes=int(
                values.get("NO_FOLLOW_THROUGH_EXIT_MINUTES", "0")
            ),
            no_follow_through_min_favorable_pct=float(
                values.get("NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT", "0.0025")
            ),
            enable_giveback_exit=_parse_bool(
                "ENABLE_GIVEBACK_EXIT",
                values.get("ENABLE_GIVEBACK_EXIT", "false"),
            ),
            giveback_exit_min_favorable_pct=float(
                values.get("GIVEBACK_EXIT_MIN_FAVORABLE_PCT", "0.0025")
            ),
            giveback_exit_max_return_pct=float(
                values.get("GIVEBACK_EXIT_MAX_RETURN_PCT", "0.0")
            ),
            enable_early_loss_exit=_parse_bool(
                "ENABLE_EARLY_LOSS_EXIT",
                values.get("ENABLE_EARLY_LOSS_EXIT", "false"),
            ),
            early_loss_exit_minutes=int(
                values.get("EARLY_LOSS_EXIT_MINUTES", "0")
            ),
            early_loss_exit_return_pct=float(
                values.get("EARLY_LOSS_EXIT_RETURN_PCT", "0.01")
            ),
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
            enable_options_trading=_parse_bool(
                "ENABLE_OPTIONS_TRADING", values.get("ENABLE_OPTIONS_TRADING", "false")
            ),
            option_chain_min_total_volume=int(
                values.get("OPTION_CHAIN_MIN_TOTAL_VOLUME", "0")
            ),
            option_chain_symbols=tuple(
                s.strip()
                for s in values.get("OPTION_CHAIN_SYMBOLS", "").split(",")
                if s.strip()
            ),
            option_chain_snapshot_dir=(
                values.get("OPTION_CHAIN_SNAPSHOT_DIR", "").strip() or None
            ),
            option_chain_request_timeout_seconds=float(
                values.get("OPTION_CHAIN_REQUEST_TIMEOUT_SECONDS", "10.0")
            ),
            option_stop_buffer_pct=float(values.get("OPTION_STOP_BUFFER_PCT", "0.10")),
            option_max_spread_pct=float(values.get("OPTION_MAX_SPREAD_PCT", "0.50")),
            option_min_open_interest=int(values.get("OPTION_MIN_OPEN_INTEREST", "0")),
            max_stop_pct=float(values.get("MAX_STOP_PCT", "0.05")),
            enable_profit_trail=_parse_bool(
                "ENABLE_PROFIT_TRAIL", values.get("ENABLE_PROFIT_TRAIL", "false")
            ),
            profit_trail_pct=float(values.get("PROFIT_TRAIL_PCT", "0.95")),
            enable_profit_target=_parse_bool(
                "ENABLE_PROFIT_TARGET", values.get("ENABLE_PROFIT_TARGET", "false")
            ),
            profit_target_r=float(values.get("PROFIT_TARGET_R", "2.0")),
            replay_slippage_bps=float(values.get("REPLAY_SLIPPAGE_BPS", "5.0")),
            trend_filter_exit_lookback_days=int(
                values.get("TREND_FILTER_EXIT_LOOKBACK_DAYS", "1")
            ),
            enable_breakeven_stop=_parse_bool(
                "ENABLE_BREAKEVEN_STOP", values.get("ENABLE_BREAKEVEN_STOP", "true")
            ),
            breakeven_trigger_pct=float(values.get("BREAKEVEN_TRIGGER_PCT", "0.0025")),
            breakeven_trail_pct=float(values.get("BREAKEVEN_TRAIL_PCT", "0.002")),
            min_position_notional=float(values.get("MIN_POSITION_NOTIONAL", "0.0")),
            intraday_digest_interval_cycles=int(
                values.get("INTRADAY_DIGEST_INTERVAL_CYCLES", "0")
            ),
            intraday_consecutive_loss_gate=int(
                values.get("INTRADAY_CONSECUTIVE_LOSS_GATE", "0")
            ),
            max_loss_per_trade_dollars=_parse_optional_float(
                values, "MAX_LOSS_PER_TRADE_DOLLARS"
            ),
            enable_vix_filter=_parse_bool(
                "ENABLE_VIX_FILTER", values.get("ENABLE_VIX_FILTER", "false")
            ),
            vix_proxy_symbol=values.get("VIX_PROXY_SYMBOL", "VIXY"),
            vix_lookback_bars=int(values.get("VIX_LOOKBACK_BARS", "20")),
            enable_sector_filter=_parse_bool(
                "ENABLE_SECTOR_FILTER", values.get("ENABLE_SECTOR_FILTER", "false")
            ),
            sector_etf_symbols=tuple(
                s.strip()
                for s in values.get(
                    "SECTOR_ETF_SYMBOLS",
                    "XLK,XLF,XLE,XLV,XLU,XLI,XLB,XLRE,XLC,XLY,XLP",
                ).split(",")
                if s.strip()
            ),
            sector_etf_sma_period=int(values.get("SECTOR_ETF_SMA_PERIOD", "20")),
            sector_filter_min_passing_pct=float(
                values.get("SECTOR_FILTER_MIN_PASSING_PCT", "0.5")
            ),
            enable_vwap_entry_filter=_parse_bool(
                "ENABLE_VWAP_ENTRY_FILTER", values.get("ENABLE_VWAP_ENTRY_FILTER", "false")
            ),
            option_strategy_max_rolling_loss_usd=float(
                values.get("OPTION_STRATEGY_MAX_ROLLING_LOSS_USD", "0.0")
            ),
            option_strategy_rolling_loss_days=int(
                values.get("OPTION_STRATEGY_ROLLING_LOSS_DAYS", "7")
            ),
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
        if not self.paper_approved_strategies:
            raise ValueError("PAPER_APPROVED_STRATEGIES must contain at least one name")
        for name in self.paper_approved_strategies:
            if any(not (char.isalnum() or char in "_:-") for char in name):
                raise ValueError("PAPER_APPROVED_STRATEGIES contains unsupported characters")
        _validate_positive_fraction("RISK_PER_TRADE_PCT", self.risk_per_trade_pct)
        _validate_positive_fraction("MAX_POSITION_PCT", self.max_position_pct)
        _validate_positive_fraction("DAILY_LOSS_LIMIT_PCT", self.daily_loss_limit_pct)
        _validate_positive_fraction("STOP_LIMIT_BUFFER_PCT", self.stop_limit_buffer_pct)
        _validate_positive_fraction(
            "BREAKOUT_STOP_BUFFER_PCT", self.breakout_stop_buffer_pct
        )
        if self.paper_readiness_max_pass_age_minutes < 1:
            raise ValueError("PAPER_READINESS_MAX_PASS_AGE_MINUTES must be positive")
        if self.paper_readiness_min_watchlist_symbols < 1:
            raise ValueError("PAPER_READINESS_MIN_WATCHLIST_SYMBOLS must be positive")
        if not self.paper_readiness_decision_dry_run_strategy:
            raise ValueError(
                "PAPER_READINESS_DECISION_DRY_RUN_STRATEGY must not be empty"
            )
        if any(
            not (char.isalnum() or char in "_:-")
            for char in self.paper_readiness_decision_dry_run_strategy
        ):
            raise ValueError(
                "PAPER_READINESS_DECISION_DRY_RUN_STRATEGY contains unsupported characters"
            )
        if self.paper_readiness_decision_dry_run_min_records < 0:
            raise ValueError(
                "PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS must be non-negative"
            )
        if self.paper_readiness_decision_dry_run_min_evaluations < 1:
            raise ValueError(
                "PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS must be positive"
            )
        if self.entry_stop_price_buffer <= 0:
            raise ValueError("ENTRY_STOP_PRICE_BUFFER must be positive")
        if not -1.0 <= self.entry_min_close_to_entry_pct <= 1.0:
            raise ValueError("ENTRY_MIN_CLOSE_TO_ENTRY_PCT must be between -1.0 and 1.0")
        if not -1.0 <= self.entry_max_close_to_entry_pct <= 1.0:
            raise ValueError("ENTRY_MAX_CLOSE_TO_ENTRY_PCT must be between -1.0 and 1.0")
        if self.entry_max_close_to_entry_pct < self.entry_min_close_to_entry_pct:
            raise ValueError(
                "ENTRY_MAX_CLOSE_TO_ENTRY_PCT must be >= ENTRY_MIN_CLOSE_TO_ENTRY_PCT"
            )
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
        if (
            self.orb_relative_volume_threshold is not None
            and self.orb_relative_volume_threshold <= 1.0
        ):
            raise ValueError(
                "ORB_RELATIVE_VOLUME_THRESHOLD must be greater than 1.0"
            )
        if (
            self.orb_atr_stop_multiplier is not None
            and self.orb_atr_stop_multiplier <= 0.0
        ):
            raise ValueError("ORB_ATR_STOP_MULTIPLIER must be positive")
        if (
            self.orb_atr_stop_multiplier is not None
            and self.orb_atr_stop_multiplier > 10.0
        ):
            raise ValueError(
                "ORB_ATR_STOP_MULTIPLIER must be <= 10.0 (got a suspiciously large value)"
            )
        if self.high_watermark_lookback_days < 5:
            raise ValueError("HIGH_WATERMARK_LOOKBACK_DAYS must be at least 5")
        if self.ema_period < 2:
            raise ValueError("EMA_PERIOD must be at least 2")
        if self.atr_period < 2:
            raise ValueError("ATR_PERIOD must be at least 2")
        if self.entry_timeframe_minutes < 1:
            raise ValueError("ENTRY_TIMEFRAME_MINUTES must be at least 1")
        if self.entry_order_active_bars < 1:
            raise ValueError("ENTRY_ORDER_ACTIVE_BARS must be at least 1")
        if self.entry_order_active_bars > 4:
            raise ValueError("ENTRY_ORDER_ACTIVE_BARS must be at most 4")
        if self.entry_candidate_rank_mode not in {
            "close_to_entry",
            "relative_volume",
            "balanced",
        }:
            raise ValueError(
                "ENTRY_CANDIDATE_RANK_MODE must be close_to_entry, "
                "relative_volume, or balanced"
            )
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
        if self.max_open_positions > 50:
            raise ValueError("MAX_OPEN_POSITIONS must be at most 50")
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
        if self.extended_hours_max_spread_pct < self.max_spread_pct:
            raise ValueError(
                f"EXTENDED_HOURS_MAX_SPREAD_PCT ({self.extended_hours_max_spread_pct}) "
                f"must be >= MAX_SPREAD_PCT ({self.max_spread_pct})"
            )
        if self.vwap_dip_threshold_pct <= 0:
            raise ValueError("VWAP_DIP_THRESHOLD_PCT must be positive")
        if self.vwap_dip_threshold_pct >= 1.0:
            raise ValueError("VWAP_DIP_THRESHOLD_PCT must be less than 1.0")
        if (
            self.vwap_reversion_relative_volume_threshold is not None
            and self.vwap_reversion_relative_volume_threshold <= 1.0
        ):
            raise ValueError(
                "VWAP_REVERSION_RELATIVE_VOLUME_THRESHOLD must be greater than 1.0"
            )
        if (
            self.vwap_reversion_atr_stop_multiplier is not None
            and self.vwap_reversion_atr_stop_multiplier <= 0.0
        ):
            raise ValueError("VWAP_REVERSION_ATR_STOP_MULTIPLIER must be positive")
        if (
            self.vwap_reversion_atr_stop_multiplier is not None
            and self.vwap_reversion_atr_stop_multiplier > 10.0
        ):
            raise ValueError(
                "VWAP_REVERSION_ATR_STOP_MULTIPLIER must be <= 10.0 "
                "(got a suspiciously large value)"
            )
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
        if self.vwap_breakdown_min_bars < 1:
            raise ValueError("VWAP_BREAKDOWN_MIN_BARS must be >= 1")
        if self.no_follow_through_exit_minutes < 0:
            raise ValueError("NO_FOLLOW_THROUGH_EXIT_MINUTES must be >= 0")
        if self.enable_no_follow_through_exit and self.no_follow_through_exit_minutes < 1:
            raise ValueError(
                "NO_FOLLOW_THROUGH_EXIT_MINUTES must be >= 1 when "
                "ENABLE_NO_FOLLOW_THROUGH_EXIT=true"
            )
        if not 0.0 <= self.no_follow_through_min_favorable_pct < 1.0:
            raise ValueError(
                "NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT must be between 0.0 "
                "and 1.0 (exclusive)"
            )
        if not 0.0 <= self.giveback_exit_min_favorable_pct < 1.0:
            raise ValueError(
                "GIVEBACK_EXIT_MIN_FAVORABLE_PCT must be between 0.0 "
                "and 1.0 (exclusive)"
            )
        if not 0.0 <= self.giveback_exit_max_return_pct < 1.0:
            raise ValueError(
                "GIVEBACK_EXIT_MAX_RETURN_PCT must be between 0.0 "
                "and 1.0 (exclusive)"
            )
        if self.early_loss_exit_minutes < 0:
            raise ValueError("EARLY_LOSS_EXIT_MINUTES must be >= 0")
        if self.enable_early_loss_exit and self.early_loss_exit_minutes < 1:
            raise ValueError(
                "EARLY_LOSS_EXIT_MINUTES must be >= 1 when "
                "ENABLE_EARLY_LOSS_EXIT=true"
            )
        if not 0.0 < self.early_loss_exit_return_pct < 1.0:
            raise ValueError(
                "EARLY_LOSS_EXIT_RETURN_PCT must be > 0.0 and < 1.0"
            )
        if self.viability_daily_bar_max_age_days < 0:
            raise ValueError("VIABILITY_DAILY_BAR_MAX_AGE_DAYS must be >= 0")
        if self.viability_min_hold_minutes < 0:
            raise ValueError("VIABILITY_MIN_HOLD_MINUTES must be >= 0")
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
        if self.option_chain_request_timeout_seconds <= 0:
            raise ValueError("OPTION_CHAIN_REQUEST_TIMEOUT_SECONDS must be positive")
        if not 0.0 < self.option_max_spread_pct <= 1.0:
            raise ValueError(
                "OPTION_MAX_SPREAD_PCT must be between 0 (exclusive) and 1.0 (inclusive)"
            )
        if self.option_min_open_interest < 0:
            raise ValueError("OPTION_MIN_OPEN_INTEREST must be >= 0")
        if not 0 < self.max_stop_pct <= 0.50:
            raise ValueError(
                "MAX_STOP_PCT must be between 0 (exclusive) and 0.50 (inclusive)"
            )
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
        if not 0 < self.profit_trail_pct < 1.0:
            raise ValueError(
                "PROFIT_TRAIL_PCT must be between 0 (exclusive) and 1.0 (exclusive); "
                f"got {self.profit_trail_pct}"
            )
        if self.breakeven_trigger_pct < 0:
            raise ValueError("BREAKEVEN_TRIGGER_PCT must be >= 0")
        if self.breakeven_trail_pct < 0:
            raise ValueError("BREAKEVEN_TRAIL_PCT must be >= 0")
        if self.min_position_notional < 0:
            raise ValueError(
                f"MIN_POSITION_NOTIONAL must be >= 0, got {self.min_position_notional}"
            )
        if self.intraday_digest_interval_cycles < 0:
            raise ValueError("INTRADAY_DIGEST_INTERVAL_CYCLES must be >= 0")
        if self.intraday_consecutive_loss_gate < 0:
            raise ValueError("INTRADAY_CONSECUTIVE_LOSS_GATE must be >= 0")
        if self.max_loss_per_trade_dollars is not None and self.max_loss_per_trade_dollars <= 0:
            raise ValueError("MAX_LOSS_PER_TRADE_DOLLARS must be > 0")
        if self.profit_target_r <= 0:
            raise ValueError("PROFIT_TARGET_R must be > 0")
        if not 0.0 <= self.replay_slippage_bps <= 100.0:
            raise ValueError("REPLAY_SLIPPAGE_BPS must be between 0 and 100")
        if self.trend_filter_exit_lookback_days < 1:
            raise ValueError("TREND_FILTER_EXIT_LOOKBACK_DAYS must be >= 1")
        if not 0.0 <= self.confidence_floor <= 1.0:
            raise ValueError("CONFIDENCE_FLOOR must be between 0.0 and 1.0")
        if not 0.0 < self.floor_raise_step <= 0.5:
            raise ValueError("FLOOR_RAISE_STEP must be between 0 (exclusive) and 0.5")
        if not 0.0 < self.drawdown_raise_pct <= 0.5:
            raise ValueError("DRAWDOWN_RAISE_PCT must be between 0 (exclusive) and 0.5")
        if self.losing_streak_n < 1:
            raise ValueError("LOSING_STREAK_N must be at least 1")
        if not 0.0 < self.vol_raise_threshold <= 1.0:
            raise ValueError("VOL_RAISE_THRESHOLD must be between 0 (exclusive) and 1.0")
        if self.floor_auto_raise_max_age_days < 1:
            raise ValueError("FLOOR_AUTO_RAISE_MAX_AGE_DAYS must be at least 1")
        if self.option_strategy_max_rolling_loss_usd < 0:
            raise ValueError("OPTION_STRATEGY_MAX_ROLLING_LOSS_USD must be >= 0")
        if self.option_strategy_rolling_loss_days < 1:
            raise ValueError("OPTION_STRATEGY_ROLLING_LOSS_DAYS must be >= 1")


def _validate_positive_fraction(name: str, value: float) -> None:
    if not 0 < value < 1:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")
