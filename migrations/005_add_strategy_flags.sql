CREATE TABLE IF NOT EXISTS strategy_flags (
    strategy_name    TEXT NOT NULL,
    trading_mode     TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (strategy_name, trading_mode, strategy_version),
    CHECK (trading_mode IN ('paper', 'live'))
);
