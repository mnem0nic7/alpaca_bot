from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from statistics import median
from typing import Sequence

from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay.report import ReplayTradeRecord


@dataclass(frozen=True)
class TradeExcursion:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    exit_session: date
    exit_reason: str
    pnl: float
    return_pct: float
    entry_price: float
    exit_price: float
    quantity: float
    bars_seen: int
    mfe_pct: float | None
    mae_pct: float | None
    giveback_pct: float | None
    mfe_price: float | None
    mae_price: float | None
    mfe_time: datetime | None
    mae_time: datetime | None
    label: str


@dataclass(frozen=True)
class ExitDiagnosticsReport:
    strategy: str
    scenarios: int
    trades: int
    eod_losses: int
    no_follow_through_losses: int
    gave_back_losses: int
    other_eod_losses: int
    missing_bar_losses: int
    eod_loss_share: float
    no_follow_through_share: float
    gave_back_share: float
    median_eod_loss_mfe_pct: float | None
    median_eod_loss_giveback_pct: float | None
    rows: tuple[TradeExcursion, ...]


def build_exit_diagnostics_report(
    *,
    scenarios: Sequence[ReplayScenario],
    trades: Sequence[ReplayTradeRecord],
    strategy: str,
    market_timezone: tzinfo | None = None,
    no_follow_through_mfe_pct: float = 0.0025,
    gave_back_mfe_pct: float = 0.0025,
) -> ExitDiagnosticsReport:
    rows = tuple(
        trade_excursion(
            trade,
            _bars_for_trade(scenarios, trade),
            market_timezone=market_timezone,
            no_follow_through_mfe_pct=no_follow_through_mfe_pct,
            gave_back_mfe_pct=gave_back_mfe_pct,
        )
        for trade in trades
    )
    eod_loss_rows = [
        row for row in rows if row.exit_reason == "eod" and row.pnl <= 0.0
    ]
    no_follow = [row for row in eod_loss_rows if row.label == "no_follow_through"]
    gave_back = [row for row in eod_loss_rows if row.label == "gave_back"]
    missing = [row for row in eod_loss_rows if row.label == "missing_bars"]
    other = [
        row
        for row in eod_loss_rows
        if row.label not in {"no_follow_through", "gave_back", "missing_bars"}
    ]
    mfe_values = [row.mfe_pct for row in eod_loss_rows if row.mfe_pct is not None]
    giveback_values = [
        row.giveback_pct
        for row in eod_loss_rows
        if row.giveback_pct is not None
    ]
    loss_count = len(eod_loss_rows)
    losing_trades = sum(1 for row in rows if row.pnl < 0.0)
    return ExitDiagnosticsReport(
        strategy=strategy,
        scenarios=len(scenarios),
        trades=len(rows),
        eod_losses=loss_count,
        no_follow_through_losses=len(no_follow),
        gave_back_losses=len(gave_back),
        other_eod_losses=len(other),
        missing_bar_losses=len(missing),
        eod_loss_share=(loss_count / losing_trades if losing_trades else 0.0),
        no_follow_through_share=(len(no_follow) / loss_count if loss_count else 0.0),
        gave_back_share=(len(gave_back) / loss_count if loss_count else 0.0),
        median_eod_loss_mfe_pct=median(mfe_values) if mfe_values else None,
        median_eod_loss_giveback_pct=(
            median(giveback_values) if giveback_values else None
        ),
        rows=rows,
    )


def trade_excursion(
    trade: ReplayTradeRecord,
    bars: Sequence[Bar],
    *,
    market_timezone: tzinfo | None = None,
    no_follow_through_mfe_pct: float = 0.0025,
    gave_back_mfe_pct: float = 0.0025,
) -> TradeExcursion:
    entry = float(trade.entry_price)
    bars_in_trade = [
        bar
        for bar in bars
        if trade.entry_time <= bar.timestamp <= trade.exit_time
    ]
    if not bars_in_trade or entry <= 0.0:
        return _trade_excursion_row(
            trade,
            bars_seen=0,
            mfe_pct=None,
            mae_pct=None,
            giveback_pct=None,
            mfe_price=None,
            mae_price=None,
            mfe_time=None,
            mae_time=None,
            label="missing_bars",
            market_timezone=market_timezone,
        )

    mfe_bar = max(bars_in_trade, key=lambda bar: bar.high)
    mae_bar = min(bars_in_trade, key=lambda bar: bar.low)
    mfe_pct = (mfe_bar.high - entry) / entry
    mae_pct = (mae_bar.low - entry) / entry
    giveback_pct = mfe_pct - float(trade.return_pct)
    label = _label_trade(
        trade,
        mfe_pct=mfe_pct,
        giveback_pct=giveback_pct,
        no_follow_through_mfe_pct=no_follow_through_mfe_pct,
        gave_back_mfe_pct=gave_back_mfe_pct,
    )
    return _trade_excursion_row(
        trade,
        bars_seen=len(bars_in_trade),
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        giveback_pct=giveback_pct,
        mfe_price=mfe_bar.high,
        mae_price=mae_bar.low,
        mfe_time=mfe_bar.timestamp,
        mae_time=mae_bar.timestamp,
        label=label,
        market_timezone=market_timezone,
    )


def format_exit_diagnostics_markdown(
    report: ExitDiagnosticsReport,
    *,
    slippage_bps: float,
    scoring_note: str,
    max_rows: int = 20,
) -> str:
    lines = [
        f"# Exit diagnostics - {report.strategy} ({slippage_bps:g} bps/side)",
        "",
        scoring_note,
        "",
        "| metric | value |",
        "|---|---:|",
        f"| scenarios | {report.scenarios} |",
        f"| closed trades | {report.trades} |",
        f"| EOD losses | {report.eod_losses} |",
        f"| EOD loss share of losses | {_fmt_pct(report.eod_loss_share)} |",
        f"| no-follow-through EOD losses | {report.no_follow_through_losses} ({_fmt_pct(report.no_follow_through_share)}) |",
        f"| gave-back EOD losses | {report.gave_back_losses} ({_fmt_pct(report.gave_back_share)}) |",
        f"| other EOD losses | {report.other_eod_losses} |",
        f"| EOD losses missing bars | {report.missing_bar_losses} |",
        f"| median EOD-loss MFE | {_fmt_pct(report.median_eod_loss_mfe_pct)} |",
        f"| median EOD-loss giveback | {_fmt_pct(report.median_eod_loss_giveback_pct)} |",
        "",
        "## Worst EOD Losses",
        "",
    ]
    eod_losses = sorted(
        (
            row
            for row in report.rows
            if row.exit_reason == "eod" and row.pnl <= 0.0
        ),
        key=lambda row: row.pnl,
    )
    if not eod_losses:
        lines.append("None.")
        return "\n".join(lines) + "\n"

    lines.extend([
        "| symbol | exit session | pnl | return | MFE | MAE | giveback | bars | label |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in eod_losses[:max_rows]:
        lines.append(
            f"| {row.symbol} | {row.exit_session.isoformat()} | "
            f"{row.pnl:.2f} | {_fmt_pct(row.return_pct)} | "
            f"{_fmt_pct(row.mfe_pct)} | {_fmt_pct(row.mae_pct)} | "
            f"{_fmt_pct(row.giveback_pct)} | {row.bars_seen} | {row.label} |"
        )
    return "\n".join(lines) + "\n"


def _bars_for_trade(
    scenarios: Sequence[ReplayScenario],
    trade: ReplayTradeRecord,
) -> list[Bar]:
    symbol = trade.symbol.upper()
    bars: list[Bar] = []
    for scenario in scenarios:
        if scenario.symbol.upper() == symbol:
            bars.extend(scenario.intraday_bars)
    return sorted(bars, key=lambda bar: bar.timestamp)


def _label_trade(
    trade: ReplayTradeRecord,
    *,
    mfe_pct: float,
    giveback_pct: float,
    no_follow_through_mfe_pct: float,
    gave_back_mfe_pct: float,
) -> str:
    if trade.exit_reason == "eod" and trade.pnl <= 0.0:
        if mfe_pct < no_follow_through_mfe_pct:
            return "no_follow_through"
        if mfe_pct >= gave_back_mfe_pct and giveback_pct >= gave_back_mfe_pct:
            return "gave_back"
        return "eod_loss_other"
    if trade.exit_reason == "eod":
        return "eod_win"
    return f"{trade.exit_reason}_{'win' if trade.pnl > 0.0 else 'loss'}"


def _trade_excursion_row(
    trade: ReplayTradeRecord,
    *,
    bars_seen: int,
    mfe_pct: float | None,
    mae_pct: float | None,
    giveback_pct: float | None,
    mfe_price: float | None,
    mae_price: float | None,
    mfe_time: datetime | None,
    mae_time: datetime | None,
    label: str,
    market_timezone: tzinfo | None,
) -> TradeExcursion:
    exit_session = (
        trade.exit_time.astimezone(market_timezone).date()
        if market_timezone is not None
        else trade.exit_time.date()
    )
    return TradeExcursion(
        symbol=trade.symbol,
        entry_time=trade.entry_time,
        exit_time=trade.exit_time,
        exit_session=exit_session,
        exit_reason=trade.exit_reason,
        pnl=float(trade.pnl),
        return_pct=float(trade.return_pct),
        entry_price=float(trade.entry_price),
        exit_price=float(trade.exit_price),
        quantity=float(trade.quantity),
        bars_seen=bars_seen,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        giveback_pct=giveback_pct,
        mfe_price=mfe_price,
        mae_price=mae_price,
        mfe_time=mfe_time,
        mae_time=mae_time,
        label=label,
    )


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"
