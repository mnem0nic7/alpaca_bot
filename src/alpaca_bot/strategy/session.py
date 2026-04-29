from __future__ import annotations

import enum
from datetime import datetime, time

from alpaca_bot.config import Settings

_PRE_MARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_EXTENDED_CLOSE = time(20, 0)


class SessionType(enum.Enum):
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"


def detect_session_type(timestamp: datetime, settings: Settings) -> SessionType:
    """Classify timestamp into a trading session using ET wall clock."""
    local_time = timestamp.astimezone(settings.market_timezone).time()
    if local_time < _PRE_MARKET_OPEN or local_time >= _EXTENDED_CLOSE:
        return SessionType.CLOSED
    if local_time < _REGULAR_OPEN:
        return SessionType.PRE_MARKET
    if local_time < _REGULAR_CLOSE:
        return SessionType.REGULAR
    return SessionType.AFTER_HOURS


def is_entry_window(
    timestamp: datetime, settings: Settings, session: SessionType
) -> bool:
    local_time = timestamp.astimezone(settings.market_timezone).time()
    if session is SessionType.PRE_MARKET:
        return settings.pre_market_entry_window_start <= local_time <= settings.pre_market_entry_window_end
    if session is SessionType.REGULAR:
        return settings.entry_window_start <= local_time <= settings.entry_window_end
    if session is SessionType.AFTER_HOURS:
        return settings.after_hours_entry_window_start <= local_time <= settings.after_hours_entry_window_end
    return False


def is_flatten_time(
    timestamp: datetime, settings: Settings, session: SessionType
) -> bool:
    local_time = timestamp.astimezone(settings.market_timezone).time()
    if session is SessionType.REGULAR:
        return local_time >= settings.flatten_time
    if session is SessionType.AFTER_HOURS:
        if not settings.extended_hours_enabled:
            return True
        return local_time >= settings.extended_hours_flatten_time
    return False
