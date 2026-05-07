CREATE TABLE decision_log (
    id              BIGSERIAL PRIMARY KEY,
    cycle_at        TIMESTAMPTZ NOT NULL,
    symbol          TEXT        NOT NULL,
    strategy_name   TEXT        NOT NULL,
    trading_mode    TEXT        NOT NULL,
    strategy_version TEXT       NOT NULL,
    decision        TEXT        NOT NULL,
    reject_stage    TEXT,
    reject_reason   TEXT,
    entry_level     NUMERIC(12,4),
    signal_bar_close NUMERIC(12,4),
    relative_volume NUMERIC(8,4),
    atr             NUMERIC(12,4),
    stop_price      NUMERIC(12,4),
    limit_price     NUMERIC(12,4),
    initial_stop_price NUMERIC(12,4),
    quantity        NUMERIC(12,4),
    risk_per_share  NUMERIC(12,4),
    equity          NUMERIC(14,2),
    filter_results  JSONB
);

CREATE INDEX ON decision_log (cycle_at DESC);
CREATE INDEX ON decision_log (symbol, cycle_at DESC);
CREATE INDEX ON decision_log (strategy_name, decision);
