from __future__ import annotations

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.strategy import OPTION_STRATEGY_NAMES


def is_paper_strategy_approved(settings: Settings, strategy_name: str) -> bool:
    return strategy_name in settings.paper_approved_strategies


def strategy_enable_rejection_reason(
    *,
    settings: Settings,
    strategy_name: str,
    trading_mode: TradingMode,
    enabled: bool,
    allow_unapproved: bool = False,
) -> str | None:
    if not enabled:
        return None
    if trading_mode is not TradingMode.PAPER:
        return None
    if strategy_name in OPTION_STRATEGY_NAMES:
        if settings.paper_proof_freeze:
            return (
                f"option strategy {strategy_name!r} cannot be enabled while "
                "PAPER_PROOF_FREEZE=true because option strategies are not "
                "replay-supported for the paper proof"
            )
        if not settings.enable_options_trading:
            return (
                f"option strategy {strategy_name!r} requires "
                "ENABLE_OPTIONS_TRADING=true before it can be enabled in paper mode"
            )
    approved = is_paper_strategy_approved(settings, strategy_name)
    if settings.paper_proof_freeze and allow_unapproved and not approved:
        return (
            f"strategy {strategy_name!r} cannot be enabled with --allow-unapproved "
            "while PAPER_PROOF_FREEZE=true; add it to PAPER_APPROVED_STRATEGIES "
            "only after replay approval"
        )
    if allow_unapproved or approved:
        return None
    approved = ",".join(settings.paper_approved_strategies)
    return (
        f"strategy {strategy_name!r} is not in PAPER_APPROVED_STRATEGIES "
        f"({approved}); use --allow-unapproved only for intentional paper experiments"
    )
