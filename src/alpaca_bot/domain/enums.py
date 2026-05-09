from enum import StrEnum


class IntentType(StrEnum):
    ENTRY_ORDER_PLACED = "entry_order_placed"
    ENTRY_FILLED = "entry_filled"
    ENTRY_EXPIRED = "entry_expired"
    STOP_UPDATED = "stop_updated"
    STOP_HIT = "stop_hit"
    EOD_EXIT = "eod_exit"
    PROFIT_TARGET_HIT = "profit_target_hit"
