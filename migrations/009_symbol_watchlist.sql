CREATE TABLE IF NOT EXISTS symbol_watchlist (
    symbol TEXT NOT NULL,
    trading_mode TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    added_by TEXT NOT NULL DEFAULT 'system',
    PRIMARY KEY (symbol, trading_mode),
    CHECK (trading_mode IN ('paper', 'live'))
);

CREATE INDEX IF NOT EXISTS idx_symbol_watchlist_trading_mode
    ON symbol_watchlist (trading_mode)
    WHERE enabled = TRUE;
