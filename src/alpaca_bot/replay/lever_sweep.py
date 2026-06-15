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
_SINGLE_FIELD_FAMILIES: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    ("A_initial_stop", "atr_stop_multiplier", (0.75, 1.0, 1.5, 2.0)),
    ("B_trail_atr", "trailing_stop_atr_multiplier", (0.0, 1.0, 1.5, 2.5, 3.5)),
    ("C_trail_trigger", "trailing_stop_profit_trigger_r", (0.5, 1.0, 1.5, 2.0)),
    ("E_rel_vol", "relative_volume_threshold", (1.5, 2.0, 2.5, 3.0)),
)

_PROFIT_TARGET_RS: tuple[float, ...] = (1.5, 2.0, 3.0, 4.0)
# entry_window_end values: must be > entry_window_start (10:00) and
# < flatten_time (15:45). These restrict entries to earlier windows.
_SESSION_ENDS: tuple[time, ...] = (time(12, 0), time(14, 0))


def build_ofat_grid(base_settings: Settings) -> list[LeverPoint]:
    """One-factor-at-a-time grid around the baseline. ~22 points."""
    points: list[LeverPoint] = [LeverPoint(label="baseline", overrides={})]

    for prefix, field, values in _SINGLE_FIELD_FAMILIES:
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

    # Family F (regime filter) is intentionally OMITTED: the replay harness calls
    # evaluate_cycle() without regime_bars (runner.py:124-136), so engine.py:519
    # short-circuits `enable_regime_filter` to a no-op. Sweeping it would yield a
    # guaranteed baseline-identical row that *reads* as "regime has no edge" when
    # the truth is "the replay cannot evaluate regime." Excluded to avoid that
    # measurement artifact; evaluating it needs a benchmark series in replay.

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

    return points


def build_coarse_grid(base_settings: Settings) -> list[LeverPoint]:
    """Reduced grid (one hypothesised-best value per family) for a fast pass."""
    points: list[LeverPoint] = [LeverPoint(label="baseline", overrides={})]
    coarse: tuple[tuple[str, dict], ...] = (
        ("A_initial_stop:atr_stop_multiplier=1.5", {"atr_stop_multiplier": 1.5}),
        ("B_trail_atr:trailing_stop_atr_multiplier=2.5",
         {"trailing_stop_atr_multiplier": 2.5}),
        ("C_trail_trigger:trailing_stop_profit_trigger_r=1.5",
         {"trailing_stop_profit_trigger_r": 1.5}),
        ("D_profit_target:on@3.0",
         {"enable_profit_target": True, "profit_target_r": 3.0}),
        ("E_rel_vol:relative_volume_threshold=2.0",
         {"relative_volume_threshold": 2.0}),
        # Family F (regime) omitted — inert in replay (see build_ofat_grid).
        ("G_vwap:off", {"enable_vwap_entry_filter": False}),
        ("H_session:end=14:00", {"entry_window_end": time(14, 0)}),
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
