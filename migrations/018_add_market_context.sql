CREATE TABLE IF NOT EXISTS market_context (
    id SERIAL PRIMARY KEY,
    as_of TIMESTAMPTZ NOT NULL,
    trading_mode VARCHAR(10) NOT NULL,
    vix_close FLOAT,
    vix_sma FLOAT,
    vix_above_sma BOOLEAN,
    sector_etf_states JSONB,
    sector_passing_pct FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS market_context_as_of_trading_mode_idx ON market_context (as_of, trading_mode);
