CREATE TABLE IF NOT EXISTS tuning_results (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    scenario_name TEXT NOT NULL,
    trading_mode TEXT NOT NULL,
    params JSONB NOT NULL,
    score DOUBLE PRECISION,
    total_trades INTEGER NOT NULL DEFAULT 0,
    win_rate DOUBLE PRECISION,
    mean_return_pct DOUBLE PRECISION,
    max_drawdown_pct DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    is_best BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_tuning_results_trading_mode_created
    ON tuning_results (trading_mode, created_at DESC);
