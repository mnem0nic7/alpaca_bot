-- Index for daily_realized_pnl() correlated subquery: filters on trading_mode,
-- strategy_version, strategy_name, and intent_type with fill_price IS NOT NULL,
-- ordered by updated_at DESC. Without this index the query does a full table scan
-- per exit row, which degrades quadratically as order history grows.
CREATE INDEX IF NOT EXISTS idx_orders_mode_version_strategy_intent_fill
    ON orders (trading_mode, strategy_version, strategy_name, intent_type, updated_at DESC)
    WHERE fill_price IS NOT NULL;
