from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import ReplayEvent, ReplayResult


@dataclass(frozen=True)
class ReplayTradeRecord:
    symbol: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_time: datetime
    exit_time: datetime
    exit_reason: str  # "stop" or "eod"
    pnl: float
    return_pct: float


@dataclass(frozen=True)
class BacktestReport:
    trades: tuple[ReplayTradeRecord, ...]
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float | None          # None when total_trades == 0
    mean_return_pct: float | None   # None when total_trades == 0
    max_drawdown_pct: float | None  # None when peak equity never exceeds 0
    sharpe_ratio: float | None = None


def build_backtest_report(result: ReplayResult) -> BacktestReport:
    trades = _extract_trades(result.events)
    total = len(trades)

    if total == 0:
        return BacktestReport(
            trades=(),
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=None,
            mean_return_pct=None,
            max_drawdown_pct=None,
        )

    winners = sum(1 for t in trades if t.pnl > 0)
    losers = sum(1 for t in trades if t.pnl < 0)
    win_rate = winners / total
    mean_return_pct = sum(t.return_pct for t in trades) / total
    max_drawdown_pct = _compute_max_drawdown(trades, result.scenario.starting_equity)

    return BacktestReport(
        trades=tuple(trades),
        total_trades=total,
        winning_trades=winners,
        losing_trades=losers,
        win_rate=win_rate,
        mean_return_pct=mean_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=_compute_sharpe(trades),
    )


def _extract_trades(events: list[ReplayEvent]) -> list[ReplayTradeRecord]:
    # Track open fills per symbol; exits pair with the most recent fill
    open_fills: dict[str, ReplayEvent] = {}
    trades: list[ReplayTradeRecord] = []

    for event in events:
        if event.event_type == IntentType.ENTRY_FILLED:
            open_fills[event.symbol] = event
        elif event.event_type in (IntentType.STOP_HIT, IntentType.EOD_EXIT):
            fill = open_fills.pop(event.symbol, None)
            if fill is None:
                continue  # exit without matching fill — skip
            entry_price = float(fill.details["entry_price"])
            exit_price = float(event.details["exit_price"])
            quantity = int(fill.details["quantity"])
            pnl = (exit_price - entry_price) * quantity
            return_pct = (exit_price - entry_price) / entry_price
            exit_reason = "stop" if event.event_type == IntentType.STOP_HIT else "eod"
            trades.append(
                ReplayTradeRecord(
                    symbol=event.symbol,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=quantity,
                    entry_time=fill.timestamp,
                    exit_time=event.timestamp,
                    exit_reason=exit_reason,
                    pnl=pnl,
                    return_pct=return_pct,
                )
            )

    return trades


def _compute_sharpe(trades: list[ReplayTradeRecord]) -> float | None:
    n = len(trades)
    if n < 2:
        return None
    returns = [t.return_pct for t in trades]
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = variance ** 0.5
    if std_r == 0.0:
        return None
    return mean_r / std_r


def _compute_max_drawdown(
    trades: list[ReplayTradeRecord], starting_equity: float
) -> float | None:
    # Drawdown is computed on absolute equity (starting_equity + cumulative PnL)
    # so that a $700 loss after a $500 gain on a $100k base reports ~0.7%, not 140%.
    equity = starting_equity
    peak = starting_equity
    max_dd = 0.0

    for trade in trades:
        equity += trade.pnl
        if equity > peak:
            peak = equity
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        if drawdown > max_dd:
            max_dd = drawdown

    return max_dd if max_dd > 0 else None
