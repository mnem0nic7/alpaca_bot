from __future__ import annotations

from datetime import date

from alpaca_bot.config import Settings


def build_daily_summary(
    *,
    settings: Settings,
    order_store: object,
    position_store: object,
    session_date: date,
    daily_loss_limit_breached: bool,
) -> tuple[str, str]:
    """Build (subject, body) for the end-of-session summary notification.

    Pure read — no writes, no side effects.
    """
    trades = order_store.list_closed_trades(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        session_date=session_date,
        market_timezone=str(settings.market_timezone),
    )
    total_pnl: float = order_store.daily_realized_pnl(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        session_date=session_date,
        market_timezone=str(settings.market_timezone),
    )
    open_positions = position_store.list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )

    subject = (
        f"Daily session summary \u2014 {session_date} [{settings.trading_mode.value}]"
    )
    body = _build_body(
        settings=settings,
        session_date=session_date,
        trades=trades,
        total_pnl=total_pnl,
        open_positions=open_positions,
        daily_loss_limit_breached=daily_loss_limit_breached,
    )
    return subject, body


def _build_body(
    *,
    settings: Settings,
    session_date: date,
    trades: list[dict],
    total_pnl: float,
    open_positions: list,
    daily_loss_limit_breached: bool,
) -> str:
    lines: list[str] = []

    lines.append(
        f"Session: {session_date}  "
        f"Mode: {settings.trading_mode.value}  "
        f"Strategy: {settings.strategy_version}"
    )
    lines.append("")

    # --- P&L ---
    lines.append("--- P&L ---")
    lines.append(f"Realized PnL : {_fmt_pnl(total_pnl)}")
    lines.append(f"Trades       : {len(trades)}")
    if trades:
        wins = sum(1 for t in trades if _is_win(t))
        losses = len(trades) - wins
        win_rate = wins / len(trades)
        lines.append(f"Win rate     : {win_rate:.1%}  ({wins}W / {losses}L)")
    lines.append("")

    # --- Strategy Breakdown ---
    if trades:
        lines.append("--- Strategy Breakdown ---")
        by_strategy: dict[str, list[dict]] = {}
        for t in trades:
            name = t.get("strategy_name") or "breakout"
            by_strategy.setdefault(name, []).append(t)
        for name, group in by_strategy.items():
            strat_pnl = sum(_trade_pnl(t) for t in group)
            lines.append(f"{name:<12}: {len(group)} trades  {_fmt_pnl(strat_pnl)} PnL")
        lines.append("")

    # --- Positions at Close ---
    lines.append("--- Positions at Close ---")
    lines.append(f"Open positions: {len(open_positions)}")
    for pos in open_positions:
        symbol = getattr(pos, "symbol", "?")
        qty = getattr(pos, "quantity", "?")
        entry = getattr(pos, "entry_price", 0.0)
        stop = getattr(pos, "stop_price", 0.0)
        lines.append(f"  {symbol} x{qty} @ {entry:.2f} (stop {stop:.2f})")
    lines.append("")

    # --- Risk ---
    lines.append("--- Risk ---")
    lines.append(
        f"Daily loss limit breached: {'Yes' if daily_loss_limit_breached else 'No'}"
    )

    return "\n".join(lines)


def _is_win(trade: dict) -> bool:
    entry = trade.get("entry_fill")
    exit_ = trade.get("exit_fill")
    if entry is None or exit_ is None:
        return False
    return float(exit_) > float(entry)


def _trade_pnl(trade: dict) -> float:
    entry = trade.get("entry_fill")
    exit_ = trade.get("exit_fill")
    qty = trade.get("qty", 0)
    if entry is None or exit_ is None:
        return 0.0
    return (float(exit_) - float(entry)) * int(qty)


def _fmt_pnl(v: float) -> str:
    """Format as $X.XX or -$X.XX (never $-X.XX)."""
    if v < 0:
        return f"-${abs(v):.2f}"
    return f"${v:.2f}"


def trailing_consecutive_losses(*, order_store: object, settings: Settings, session_date: date) -> int:
    """Count consecutive losses trailing into today (stub for Task 2)."""
    # Stub implementation — will be implemented in Task 2
    return 0


def build_intraday_digest(*, settings: Settings, order_store: object) -> tuple[str, str]:
    """Build (subject, body) for intra-day trade review (stub for Task 3)."""
    # Stub implementation — will be implemented in Task 3
    return "Intra-day Digest", ""
