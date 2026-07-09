"""Cost-aware lever sweep — a diagnostic over the run_audit objective.

Sweeps cost-drag / selectivity levers around a baseline Settings, one factor
at a time, ranking each grid point by after-cost bootstrap CI lower bound
(``ci_low``) — the quantity the audit verdict turns on. Optionally runs a
chronological in-sample / out-of-sample walk-forward so candidates that only
look good in-sample are flagged. Produces candidates only; promotion is a
separate, operator-gated step through the nightly OOS gate.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import time
from typing import Callable, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.audit import (
    PooledTradesFn,
    StrategyAuditRow,
    _replay_pooled_trades,
    run_audit,
)
from alpaca_bot.replay.splitter import split_scenario


@dataclass(frozen=True)
class LeverPoint:
    """One grid point: a label and the Settings field overrides to apply."""

    label: str
    overrides: dict  # Settings dataclass field name -> typed value


@dataclass(frozen=True)
class LeverSweepRow:
    """A grid point's in-sample audit row and (optionally) its OOS audit row."""

    label: str
    overrides: dict
    is_row: StrategyAuditRow
    oos_row: StrategyAuditRow | None


def _ci_low_key(row: StrategyAuditRow) -> float:
    """Sort key: None ci_low (insufficient-data) sorts last under reverse=True."""
    return row.ci_low if row.ci_low is not None else float("-inf")


def _audit_one(
    *,
    scenarios: Sequence[ReplayScenario],
    base_settings: Settings,
    point: "LeverPoint",
    strategy: str,
    slippage_bps: float,
    pooled_trades_fn: PooledTradesFn,
) -> StrategyAuditRow:
    settings = dataclasses.replace(base_settings, **point.overrides)
    rows = run_audit(
        scenarios=scenarios,
        settings=settings,
        strategies=[strategy],
        slippage_bps=slippage_bps,
        pooled_trades_fn=pooled_trades_fn,
    )
    return rows[0]


def run_lever_sweep(
    *,
    scenarios: Sequence[ReplayScenario],
    base_settings: Settings,
    strategy: str,
    grid: Sequence["LeverPoint"],
    slippage_bps: float = 5.0,
    walk_forward: bool = True,
    in_sample_ratio: float = 0.8,
    daily_warmup: int = 30,
    top_k: int = 5,
    pooled_trades_fn: PooledTradesFn = _replay_pooled_trades,
    on_progress: Callable[[str], None] | None = None,
) -> list["LeverSweepRow"]:
    if walk_forward:
        # split_scenario raises ValueError for a scenario with <10 trading dates.
        # Skip such scenarios (with a note) rather than aborting the whole sweep,
        # mirroring the per-point invalid-settings guard below. If NONE survive,
        # raise one clear error instead of producing a misleading empty report.
        pairs = []
        for s in scenarios:
            try:
                pairs.append(
                    split_scenario(
                        s, in_sample_ratio=in_sample_ratio, daily_warmup=daily_warmup
                    )
                )
            except ValueError as exc:
                if on_progress is not None:
                    on_progress(f"SKIP scenario '{s.name}': {exc}")
        if not pairs:
            raise ValueError(
                "No scenarios survived the IS/OOS split — all too short "
                "(need at least 10 trading dates each)."
            )
        is_scenarios: list = [is_s for is_s, _ in pairs]
        oos_scenarios: list | None = [oos_s for _, oos_s in pairs]
    else:
        is_scenarios = list(scenarios)
        oos_scenarios = None

    scored: list[tuple["LeverPoint", StrategyAuditRow]] = []
    for point in grid:
        # dataclasses.replace re-runs Settings.__post_init__ -> validate(), which
        # raises ValueError for any override out of bounds *relative to the live
        # baseline* (e.g. an entry_window_end the baseline's start/flatten bracket
        # differently). Skip that single point rather than aborting the whole sweep.
        # The OOS pass below needs no guard: it only revisits points already in
        # `scored`, whose identical (scenario-independent) settings passed here.
        try:
            is_row = _audit_one(
                scenarios=is_scenarios, base_settings=base_settings, point=point,
                strategy=strategy, slippage_bps=slippage_bps,
                pooled_trades_fn=pooled_trades_fn,
            )
        except ValueError as exc:
            if on_progress is not None:
                on_progress(f"SKIP {point.label}: invalid settings ({exc})")
            continue
        scored.append((point, is_row))
        if on_progress is not None:
            on_progress(
                f"IS {point.label}: ci_low={is_row.ci_low} "
                f"trades={is_row.trades} verdict={is_row.verdict}"
            )

    scored.sort(key=lambda pr: _ci_low_key(pr[1]), reverse=True)

    shortlist: set[str] = set()
    if oos_scenarios is not None:
        shortlist = {point.label for point, _ in scored[:top_k]}
        shortlist.add("baseline")  # always confirm baseline OOS for reference

    result: list["LeverSweepRow"] = []
    for point, is_row in scored:
        oos_row: StrategyAuditRow | None = None
        if oos_scenarios is not None and point.label in shortlist:
            oos_row = _audit_one(
                scenarios=oos_scenarios, base_settings=base_settings, point=point,
                strategy=strategy, slippage_bps=slippage_bps,
                pooled_trades_fn=pooled_trades_fn,
            )
            if on_progress is not None:
                on_progress(
                    f"OOS {point.label}: ci_low={oos_row.ci_low} "
                    f"trades={oos_row.trades} verdict={oos_row.verdict}"
                )
        result.append(
            LeverSweepRow(
                label=point.label, overrides=point.overrides,
                is_row=is_row, oos_row=oos_row,
            )
        )
    return result


# Single-field families: (label_prefix, settings_field, candidate_values).
# Values that equal the baseline are skipped (baseline is its own point).
_SINGLE_FIELD_FAMILIES: tuple[tuple[str, str, tuple[float | int, ...]], ...] = (
    ("A_initial_stop", "atr_stop_multiplier", (0.75, 1.0, 1.5, 2.0)),
    ("B_trail_atr", "trailing_stop_atr_multiplier", (0.0, 1.0, 1.5, 2.5, 3.5)),
    ("C_trail_trigger", "trailing_stop_profit_trigger_r", (0.5, 1.0, 1.5, 2.0)),
    ("E_rel_vol", "relative_volume_threshold", (1.5, 1.8, 2.0, 2.5, 3.0)),
    ("Y_rel_vol_lookback", "relative_volume_lookback_bars", (10, 15, 20, 30, 40)),
    (
        "Z_breakout_stop_buffer",
        "breakout_stop_buffer_pct",
        (0.0005, 0.001, 0.002, 0.005),
    ),
    ("N_max_stop", "max_stop_pct", (0.02, 0.03, 0.04, 0.05)),
    ("O_loss_cap", "max_loss_per_trade_dollars", (5.0, 10.0, 15.0, 20.0)),
    ("R_stop_limit_buffer", "stop_limit_buffer_pct", (0.00025, 0.0005, 0.001, 0.002)),
    ("S_entry_stop_buffer", "entry_stop_price_buffer", (0.01, 0.03, 0.05)),
    (
        "T_max_close_to_entry",
        "entry_max_close_to_entry_pct",
        (0.001, 0.0025, 0.005, 0.01),
    ),
    (
        "W_min_close_to_entry",
        "entry_min_close_to_entry_pct",
        (-1.0, -0.05, -0.02, -0.005, 0.0),
    ),
)

_FAILED_BREAKDOWN_FIELD_FAMILIES: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    ("I_failed_breakdown_volume", "failed_breakdown_volume_ratio", (1.5, 2.0, 2.5, 3.0)),
    (
        "J_failed_breakdown_recapture",
        "failed_breakdown_recapture_buffer_pct",
        (0.0005, 0.001, 0.002, 0.003),
    ),
)

_MOMENTUM_FIELD_FAMILIES: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("U_prior_high_lookback", "prior_day_high_lookback_bars", (1, 2, 3, 5)),
)

_EMA_PULLBACK_FIELD_FAMILIES: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("AH_ema_period", "ema_period", (5, 7, 9, 12, 20)),
)

_BULL_FLAG_FIELD_FAMILIES: tuple[
    tuple[str, str, tuple[float, ...]], ...
] = (
    ("AP_bull_flag_min_run", "bull_flag_min_run_pct", (0.015, 0.02, 0.03)),
    (
        "AQ_bull_flag_range",
        "bull_flag_consolidation_range_pct",
        (0.4, 0.5, 0.6),
    ),
    (
        "AR_bull_flag_volume",
        "bull_flag_consolidation_volume_ratio",
        (0.5, 0.6, 0.7),
    ),
)

_BREAKOUT_FIELD_FAMILIES: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("X_breakout_lookback", "breakout_lookback_bars", (10, 15, 20, 30, 40)),
)

_ORB_FIELD_FAMILIES: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("AA_orb_opening_bars", "orb_opening_bars", (1, 2, 3, 4)),
)

_HIGH_WATERMARK_FIELD_FAMILIES: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("AB_high_watermark_lookback", "high_watermark_lookback_days", (63, 126, 252, 504)),
)

_VWAP_REVERSION_FIELD_FAMILIES: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    ("AI_vwap_dip", "vwap_dip_threshold_pct", (0.005, 0.01, 0.015, 0.02, 0.03)),
)

_GAP_AND_GO_FIELD_FAMILIES: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    ("AJ_gap_threshold", "gap_threshold_pct", (0.01, 0.015, 0.02, 0.03, 0.05)),
    ("AK_gap_volume", "gap_volume_threshold", (1.5, 2.0, 2.5, 3.0)),
)

_BB_SQUEEZE_FIELD_FAMILIES: tuple[tuple[str, str, tuple[float | int, ...]], ...] = (
    ("AL_bb_period", "bb_period", (10, 20, 30)),
    ("AM_bb_std_dev", "bb_std_dev", (1.5, 2.0, 2.5)),
    (
        "AN_bb_squeeze_threshold",
        "bb_squeeze_threshold_pct",
        (0.015, 0.02, 0.03, 0.05),
    ),
    ("AO_bb_squeeze_min_bars", "bb_squeeze_min_bars", (3, 5, 8)),
)

_PROFIT_TARGET_RS: tuple[float, ...] = (1.5, 2.0, 3.0, 4.0)
# entry_window_end values: must be > entry_window_start (10:00) and
# < flatten_time (15:45). These restrict entries to earlier windows.
_SESSION_ENDS: tuple[time, ...] = (time(12, 0), time(14, 0))
_EARLY_FLATTEN_WINDOWS: tuple[tuple[time, time], ...] = (
    (time(15, 30), time(15, 15)),
    (time(15, 15), time(15, 0)),
    (time(15, 0), time(14, 45)),
    (time(14, 45), time(14, 30)),
)
_EXIT_FILTER_POINTS: tuple[LeverPoint, ...] = (
    LeverPoint(
        label="K_trend_exit:on",
        overrides={"enable_trend_filter_exit": True},
    ),
    LeverPoint(
        label="L_vwap_breakdown_exit:on",
        overrides={"enable_vwap_breakdown_exit": True},
    ),
    LeverPoint(
        label="M_vwap_breakdown_exit:on,min_bars=2",
        overrides={"enable_vwap_breakdown_exit": True, "vwap_breakdown_min_bars": 2},
    ),
)
_NO_FOLLOW_THROUGH_POINTS: tuple[LeverPoint, ...] = (
    LeverPoint(
        label="Q_no_follow_through:60m@0.0025",
        overrides={
            "enable_no_follow_through_exit": True,
            "no_follow_through_exit_minutes": 60,
            "no_follow_through_min_favorable_pct": 0.0025,
        },
    ),
    LeverPoint(
        label="Q_no_follow_through:90m@0.0025",
        overrides={
            "enable_no_follow_through_exit": True,
            "no_follow_through_exit_minutes": 90,
            "no_follow_through_min_favorable_pct": 0.0025,
        },
    ),
    LeverPoint(
        label="Q_no_follow_through:120m@0.005",
        overrides={
            "enable_no_follow_through_exit": True,
            "no_follow_through_exit_minutes": 120,
            "no_follow_through_min_favorable_pct": 0.005,
        },
    ),
)
_GIVEBACK_EXIT_POINTS: tuple[LeverPoint, ...] = (
    LeverPoint(
        label="V_giveback_exit:on@0.0025,max_return=0",
        overrides={
            "enable_giveback_exit": True,
            "giveback_exit_min_favorable_pct": 0.0025,
            "giveback_exit_max_return_pct": 0.0,
        },
    ),
    LeverPoint(
        label="V_giveback_exit:on@0.005,max_return=0.001",
        overrides={
            "enable_giveback_exit": True,
            "giveback_exit_min_favorable_pct": 0.005,
            "giveback_exit_max_return_pct": 0.001,
        },
    ),
)
_EARLY_LOSS_EXIT_POINTS: tuple[LeverPoint, ...] = (
    LeverPoint(
        label="AF_early_loss_exit:30m@0.005",
        overrides={
            "enable_early_loss_exit": True,
            "early_loss_exit_minutes": 30,
            "early_loss_exit_return_pct": 0.005,
        },
    ),
    LeverPoint(
        label="AF_early_loss_exit:45m@0.005",
        overrides={
            "enable_early_loss_exit": True,
            "early_loss_exit_minutes": 45,
            "early_loss_exit_return_pct": 0.005,
        },
    ),
    LeverPoint(
        label="AF_early_loss_exit:60m@0.01",
        overrides={
            "enable_early_loss_exit": True,
            "early_loss_exit_minutes": 60,
            "early_loss_exit_return_pct": 0.01,
        },
    ),
)
_ENTRY_ORDER_ACTIVE_BAR_POINTS: tuple[LeverPoint, ...] = (
    LeverPoint(
        label="AG_entry_order_active_bars:2",
        overrides={"entry_order_active_bars": 2},
    ),
    LeverPoint(
        label="AG_entry_order_active_bars:3",
        overrides={"entry_order_active_bars": 3},
    ),
)


def scenarios_support_regime_filter(
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
) -> bool:
    """Return True when replay can supply point-in-time regime bars."""
    regime_symbol = settings.regime_symbol.upper()
    return any(
        (
            scenario.symbol.upper() == regime_symbol and bool(scenario.daily_bars)
        )
        or bool(scenario.regime_daily_bars)
        for scenario in scenarios
    )


def scenarios_support_vix_filter(
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
) -> bool:
    """Return True when replay can supply point-in-time VIX proxy bars."""
    vix_symbol = settings.vix_proxy_symbol.upper()
    return any(
        (
            scenario.symbol.upper() == vix_symbol and bool(scenario.daily_bars)
        )
        or bool(scenario.vix_daily_bars)
        for scenario in scenarios
    )


def scenarios_support_sector_filter(
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
) -> bool:
    """Return True when replay can supply point-in-time sector ETF bars."""
    sector_symbols = {symbol.upper() for symbol in settings.sector_etf_symbols}
    return any(
        (
            scenario.symbol.upper() in sector_symbols
            and bool(scenario.daily_bars)
        )
        or bool(scenario.sector_daily_bars_by_etf)
        for scenario in scenarios
    )


def _single_field_families(
    strategy: str | None,
) -> tuple[tuple[str, str, tuple[float | int, ...]], ...]:
    if strategy == "failed_breakdown":
        # `relative_volume_threshold` is ignored by failed_breakdown; it has its
        # own strategy-specific volume/recapture filters.
        return tuple(
            family
            for family in _SINGLE_FIELD_FAMILIES
            if family[1] != "relative_volume_threshold"
        ) + _FAILED_BREAKDOWN_FIELD_FAMILIES
    if strategy == "momentum":
        return _SINGLE_FIELD_FAMILIES + _MOMENTUM_FIELD_FAMILIES
    if strategy == "ema_pullback":
        return _SINGLE_FIELD_FAMILIES + _EMA_PULLBACK_FIELD_FAMILIES
    if strategy == "bull_flag":
        return _SINGLE_FIELD_FAMILIES + _BULL_FLAG_FIELD_FAMILIES
    if strategy == "breakout":
        return _SINGLE_FIELD_FAMILIES + _BREAKOUT_FIELD_FAMILIES
    if strategy == "orb":
        return _SINGLE_FIELD_FAMILIES + _ORB_FIELD_FAMILIES
    if strategy == "high_watermark":
        return _SINGLE_FIELD_FAMILIES + _HIGH_WATERMARK_FIELD_FAMILIES
    if strategy == "vwap_reversion":
        return _SINGLE_FIELD_FAMILIES + _VWAP_REVERSION_FIELD_FAMILIES
    if strategy == "gap_and_go":
        return _SINGLE_FIELD_FAMILIES + _GAP_AND_GO_FIELD_FAMILIES
    if strategy == "bb_squeeze":
        return _SINGLE_FIELD_FAMILIES + _BB_SQUEEZE_FIELD_FAMILIES
    return _SINGLE_FIELD_FAMILIES


def build_ofat_grid(
    base_settings: Settings,
    *,
    strategy: str | None = None,
    include_regime: bool = False,
    include_vix: bool = False,
    include_sector: bool = False,
) -> list[LeverPoint]:
    """One-factor-at-a-time grid around the baseline. ~22 points."""
    points: list[LeverPoint] = [LeverPoint(label="baseline", overrides={})]

    for prefix, field, values in _single_field_families(strategy):
        base_val = getattr(base_settings, field)
        for v in values:
            if v == base_val:
                continue  # already the baseline point
            points.append(
                LeverPoint(label=f"{prefix}:{field}={v}", overrides={field: v})
            )

    # Family D — fixed profit target (two coupled fields).
    for r in _PROFIT_TARGET_RS:
        points.append(
            LeverPoint(
                label=f"D_profit_target:on@{r}",
                overrides={"enable_profit_target": True, "profit_target_r": r},
            )
        )

    # Family F — broad-market regime filter. Only include it when the caller
    # confirmed the scenario set contains a benchmark series; otherwise the
    # engine fail-opens and the row would be baseline-identical.
    if include_regime:
        points.append(
            LeverPoint(
                label="F_regime:on",
                overrides={"enable_regime_filter": True},
            )
        )

    # Families AC/AD — market-context gates. Only include them after the caller
    # confirms replay can supply the matching daily context bars; otherwise the
    # engine fail-opens and the row would be baseline-identical.
    if include_vix:
        vix_target = not base_settings.enable_vix_filter
        points.append(
            LeverPoint(
                label=f"AC_vix:{'on' if vix_target else 'off'}",
                overrides={"enable_vix_filter": vix_target},
            )
        )
    if include_sector:
        sector_target = not base_settings.enable_sector_filter
        points.append(
            LeverPoint(
                label=f"AD_sector:{'on' if sector_target else 'off'}",
                overrides={"enable_sector_filter": sector_target},
            )
        )
    if include_vix and include_sector:
        vix_target = not base_settings.enable_vix_filter
        sector_target = not base_settings.enable_sector_filter
        points.append(
            LeverPoint(
                label=(
                    "AE_vix_sector:"
                    f"vix={'on' if vix_target else 'off'},"
                    f"sector={'on' if sector_target else 'off'}"
                ),
                overrides={
                    "enable_vix_filter": vix_target,
                    "enable_sector_filter": sector_target,
                },
            )
        )

    # Family G — VWAP entry filter (toggle opposite of baseline).
    vwap_target = not base_settings.enable_vwap_entry_filter
    points.append(
        LeverPoint(
            label=f"G_vwap:{'on' if vwap_target else 'off'}",
            overrides={"enable_vwap_entry_filter": vwap_target},
        )
    )

    # Family H — session restriction (earlier entry_window_end).
    for end in _SESSION_ENDS:
        if end == base_settings.entry_window_end:
            continue
        points.append(
            LeverPoint(
                label=f"H_session:end={end.strftime('%H:%M')}",
                overrides={"entry_window_end": end},
            )
        )

    # Family P — earlier flatten windows, coupled with an earlier entry cutoff
    # so Settings validation keeps entry_window_end < flatten_time.
    for flatten, entry_end in _EARLY_FLATTEN_WINDOWS:
        if flatten >= base_settings.flatten_time:
            continue
        if entry_end <= base_settings.entry_window_start or entry_end >= flatten:
            continue
        points.append(
            LeverPoint(
                label=(
                    f"P_flatten:flatten={flatten.strftime('%H:%M')},"
                    f"entry_end={entry_end.strftime('%H:%M')}"
                ),
                overrides={
                    "flatten_time": flatten,
                    "entry_window_end": entry_end,
                },
            )
        )

    points.extend(_EXIT_FILTER_POINTS)
    points.extend(_NO_FOLLOW_THROUGH_POINTS)
    points.extend(_GIVEBACK_EXIT_POINTS)
    points.extend(_EARLY_LOSS_EXIT_POINTS)
    points.extend(_ENTRY_ORDER_ACTIVE_BAR_POINTS)
    return points


def build_coarse_grid(
    base_settings: Settings,
    *,
    strategy: str | None = None,
    include_regime: bool = False,
    include_vix: bool = False,
    include_sector: bool = False,
) -> list[LeverPoint]:
    """Reduced grid (one hypothesised-best value per family) for a fast pass."""
    points: list[LeverPoint] = [LeverPoint(label="baseline", overrides={})]
    coarse: list[tuple[str, dict]] = [
        ("A_initial_stop:atr_stop_multiplier=1.5", {"atr_stop_multiplier": 1.5}),
        ("B_trail_atr:trailing_stop_atr_multiplier=2.5",
         {"trailing_stop_atr_multiplier": 2.5}),
        ("C_trail_trigger:trailing_stop_profit_trigger_r=1.5",
         {"trailing_stop_profit_trigger_r": 1.5}),
        ("D_profit_target:on@3.0",
         {"enable_profit_target": True, "profit_target_r": 3.0}),
        (
            f"G_vwap:{'off' if base_settings.enable_vwap_entry_filter else 'on'}",
            {"enable_vwap_entry_filter": not base_settings.enable_vwap_entry_filter},
        ),
        ("H_session:end=14:00", {"entry_window_end": time(14, 0)}),
        ("K_trend_exit:on", {"enable_trend_filter_exit": True}),
        ("L_vwap_breakdown_exit:on", {"enable_vwap_breakdown_exit": True}),
        (
            "Q_no_follow_through:90m@0.0025",
            {
                "enable_no_follow_through_exit": True,
                "no_follow_through_exit_minutes": 90,
                "no_follow_through_min_favorable_pct": 0.0025,
            },
        ),
        (
            "V_giveback_exit:on@0.0025,max_return=0",
            {
                "enable_giveback_exit": True,
                "giveback_exit_min_favorable_pct": 0.0025,
                "giveback_exit_max_return_pct": 0.0,
            },
        ),
        (
            "AF_early_loss_exit:45m@0.005",
            {
                "enable_early_loss_exit": True,
                "early_loss_exit_minutes": 45,
                "early_loss_exit_return_pct": 0.005,
            },
        ),
        ("AG_entry_order_active_bars:2", {"entry_order_active_bars": 2}),
        ("N_max_stop:max_stop_pct=0.04", {"max_stop_pct": 0.04}),
        ("O_loss_cap:max_loss_per_trade_dollars=10.0",
         {"max_loss_per_trade_dollars": 10.0}),
        ("R_stop_limit_buffer:stop_limit_buffer_pct=0.00025",
         {"stop_limit_buffer_pct": 0.00025}),
        ("S_entry_stop_buffer:entry_stop_price_buffer=0.03",
         {"entry_stop_price_buffer": 0.03}),
        ("T_max_close_to_entry:entry_max_close_to_entry_pct=0.005",
         {"entry_max_close_to_entry_pct": 0.005}),
        ("W_min_close_to_entry:entry_min_close_to_entry_pct=-0.02",
         {"entry_min_close_to_entry_pct": -0.02}),
        ("Y_rel_vol_lookback:relative_volume_lookback_bars=10",
         {"relative_volume_lookback_bars": 10}),
        ("Z_breakout_stop_buffer:breakout_stop_buffer_pct=0.0005",
         {"breakout_stop_buffer_pct": 0.0005}),
        ("P_flatten:flatten=15:15,entry_end=15:00",
         {"flatten_time": time(15, 15), "entry_window_end": time(15, 0)}),
    ]
    if include_regime:
        coarse.insert(
            5,
            ("F_regime:on", {"enable_regime_filter": True}),
        )
    if include_vix:
        vix_target = not base_settings.enable_vix_filter
        coarse.insert(
            6,
            (
                f"AC_vix:{'on' if vix_target else 'off'}",
                {"enable_vix_filter": vix_target},
            ),
        )
    if include_sector:
        sector_target = not base_settings.enable_sector_filter
        coarse.insert(
            7,
            (
                f"AD_sector:{'on' if sector_target else 'off'}",
                {"enable_sector_filter": sector_target},
            ),
        )
    if include_vix and include_sector:
        vix_target = not base_settings.enable_vix_filter
        sector_target = not base_settings.enable_sector_filter
        coarse.insert(
            8,
            (
                (
                    "AE_vix_sector:"
                    f"vix={'on' if vix_target else 'off'},"
                    f"sector={'on' if sector_target else 'off'}"
                ),
                {
                    "enable_vix_filter": vix_target,
                    "enable_sector_filter": sector_target,
                },
            ),
        )
    if strategy == "failed_breakdown":
        coarse.extend(
            [
                (
                    "I_failed_breakdown_volume:failed_breakdown_volume_ratio=2.5",
                    {"failed_breakdown_volume_ratio": 2.5},
                ),
                (
                    "J_failed_breakdown_recapture:failed_breakdown_recapture_buffer_pct=0.002",
                    {"failed_breakdown_recapture_buffer_pct": 0.002},
                ),
            ]
        )
    else:
        coarse.append(
            (
                "E_rel_vol:relative_volume_threshold=2.0",
                {"relative_volume_threshold": 2.0},
            )
        )
    if strategy == "momentum":
        coarse.append(
            (
                "U_prior_high_lookback:prior_day_high_lookback_bars=2",
                {"prior_day_high_lookback_bars": 2},
            )
        )
    if strategy == "ema_pullback":
        coarse.append(
            (
                "AH_ema_period:ema_period=7",
                {"ema_period": 7},
            )
        )
    if strategy == "bull_flag":
        coarse.extend(
            [
                (
                    "AP_bull_flag_min_run:bull_flag_min_run_pct=0.015",
                    {"bull_flag_min_run_pct": 0.015},
                ),
                (
                    "AQ_bull_flag_range:bull_flag_consolidation_range_pct=0.6",
                    {"bull_flag_consolidation_range_pct": 0.6},
                ),
                (
                    "AR_bull_flag_volume:bull_flag_consolidation_volume_ratio=0.7",
                    {"bull_flag_consolidation_volume_ratio": 0.7},
                ),
            ]
        )
    if strategy == "breakout":
        coarse.append(
            (
                "X_breakout_lookback:breakout_lookback_bars=10",
                {"breakout_lookback_bars": 10},
            )
        )
    if strategy == "orb":
        coarse.append(
            (
                "AA_orb_opening_bars:orb_opening_bars=3",
                {"orb_opening_bars": 3},
            )
        )
    if strategy == "high_watermark":
        coarse.append(
            (
                "AB_high_watermark_lookback:high_watermark_lookback_days=126",
                {"high_watermark_lookback_days": 126},
            )
        )
    if strategy == "vwap_reversion":
        coarse.append(
            (
                "AI_vwap_dip:vwap_dip_threshold_pct=0.02",
                {"vwap_dip_threshold_pct": 0.02},
            )
        )
    if strategy == "gap_and_go":
        coarse.extend(
            [
                (
                    "AJ_gap_threshold:gap_threshold_pct=0.01",
                    {"gap_threshold_pct": 0.01},
                ),
                (
                    "AK_gap_volume:gap_volume_threshold=1.5",
                    {"gap_volume_threshold": 1.5},
                ),
            ]
        )
    if strategy == "bb_squeeze":
        coarse.extend(
            [
                (
                    "AL_bb_period:bb_period=10",
                    {"bb_period": 10},
                ),
                (
                    "AM_bb_std_dev:bb_std_dev=1.5",
                    {"bb_std_dev": 1.5},
                ),
                (
                    "AN_bb_squeeze_threshold:bb_squeeze_threshold_pct=0.05",
                    {"bb_squeeze_threshold_pct": 0.05},
                ),
                (
                    "AO_bb_squeeze_min_bars:bb_squeeze_min_bars=3",
                    {"bb_squeeze_min_bars": 3},
                ),
            ]
        )
    for label, overrides in coarse:
        points.append(LeverPoint(label=label, overrides=overrides))
    return points


def _fmt(v: float | None, spec: str = ".4f") -> str:
    return "n/a" if v is None else format(v, spec)


def format_lever_sweep_markdown(
    rows: Sequence["LeverSweepRow"],
    *,
    strategy: str,
    slippage_bps: float,
    baseline_label: str = "baseline",
    scoring_note: str | None = None,
) -> str:
    base = next((r for r in rows if r.label == baseline_label), None)
    base_ci = base.is_row.ci_low if base and base.is_row.ci_low is not None else None

    lines: list[str] = [
        f"# Lever sweep — {strategy} ({slippage_bps:g} bps/side)",
        "",
        "Ranked by in-sample after-cost `ci_low` (the audit verdict turns on "
        "`ci_low > 0`). Read `trades` alongside `ci_low`: fewer trades widen the "
        "CI, so a high mean with few trades can still fail the verdict. "
        "Candidates only — promotion is via the nightly OOS gate.",
        "",
    ]
    if scoring_note:
        lines += [scoring_note, ""]

    if base is not None:
        lines += [
            f"**Baseline** (`{baseline_label}`): IS ci_low="
            f"{_fmt(base.is_row.ci_low)} trades={base.is_row.trades} "
            f"verdict={base.is_row.verdict}"
            + (
                f"; OOS ci_low={_fmt(base.oos_row.ci_low)} "
                f"verdict={base.oos_row.verdict}"
                if base.oos_row is not None
                else ""
            ),
            "",
        ]

    lines += [
        "| rank | lever | IS ci_low | Δci_low | IS mean | IS trades | IS p | "
        "IS verdict | OOS ci_low | OOS verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rank, r in enumerate(rows, 1):
        delta = (
            _fmt(r.is_row.ci_low - base_ci)
            if (base_ci is not None and r.is_row.ci_low is not None)
            else "n/a"
        )
        oos_ci = _fmt(r.oos_row.ci_low) if r.oos_row is not None else "—"
        oos_v = r.oos_row.verdict if r.oos_row is not None else "—"
        lines.append(
            f"| {rank} | {r.label} | {_fmt(r.is_row.ci_low)} | {delta} | "
            f"{_fmt(r.is_row.mean_trade_pnl)} | {r.is_row.trades} | "
            f"{_fmt(r.is_row.p_positive)} | {r.is_row.verdict} | "
            f"{oos_ci} | {oos_v} |"
        )

    # Surviving candidates: IS edge that holds up OOS (non-negative, not
    # negative-edge). These are the hand-off to the nightly OOS gate.
    survivors = [
        r for r in rows
        if r.oos_row is not None
        and r.oos_row.verdict != "negative-edge"
        and r.oos_row.ci_low is not None
        and r.oos_row.ci_low >= 0.0
        and r.label != baseline_label
    ]
    lines += ["", "## Candidates surviving OOS", ""]
    if not survivors:
        lines.append(
            "None. No lever point held a non-negative OOS `ci_low`. This is a "
            "valid null result — record it and iterate; do not promote anything."
        )
    else:
        for r in survivors:
            ov = ", ".join(f"{k}={v}" for k, v in r.overrides.items())
            lines.append(
                f"- `{r.label}` — overrides: {ov} — IS ci_low="
                f"{_fmt(r.is_row.ci_low)} ({r.is_row.verdict}), OOS ci_low="
                f"{_fmt(r.oos_row.ci_low)} ({r.oos_row.verdict}). "
                "Route through `alpaca-bot-nightly` (sub-project B); do not "
                "hand-apply."
            )

    return "\n".join(lines) + "\n"
