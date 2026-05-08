CREATE TABLE IF NOT EXISTS confidence_floor_store (
    trading_mode          TEXT    NOT NULL,
    strategy_version      TEXT    NOT NULL,
    floor_value           REAL    NOT NULL,
    manual_floor_baseline REAL    NOT NULL DEFAULT 0.25,
    equity_high_watermark REAL    NOT NULL DEFAULT 0.0,
    set_by                TEXT    NOT NULL,
    reason                TEXT    NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (trading_mode, strategy_version),
    CHECK (trading_mode IN ('paper', 'live')),
    CHECK (floor_value >= 0.0 AND floor_value <= 1.0),
    CHECK (manual_floor_baseline >= 0.0 AND manual_floor_baseline <= 1.0)
);
