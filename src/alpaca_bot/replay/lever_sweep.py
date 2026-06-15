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
