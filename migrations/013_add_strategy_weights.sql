CREATE TABLE IF NOT EXISTS strategy_weights (
    strategy_name    TEXT NOT NULL,
    trading_mode     TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    weight           FLOAT NOT NULL,
    sharpe           FLOAT NOT NULL DEFAULT 0.0,
    computed_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (strategy_name, trading_mode, strategy_version)
);
