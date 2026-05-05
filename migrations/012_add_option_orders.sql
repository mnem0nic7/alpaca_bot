CREATE TABLE IF NOT EXISTS option_orders (
    id                BIGSERIAL PRIMARY KEY,
    client_order_id   TEXT        NOT NULL UNIQUE,
    occ_symbol        TEXT        NOT NULL,
    underlying_symbol TEXT        NOT NULL,
    option_type       TEXT        NOT NULL,
    strike            NUMERIC     NOT NULL,
    expiry            DATE        NOT NULL,
    side              TEXT        NOT NULL,
    status            TEXT        NOT NULL,
    quantity          INTEGER     NOT NULL,
    trading_mode      TEXT        NOT NULL,
    strategy_version  TEXT        NOT NULL,
    strategy_name     TEXT        NOT NULL DEFAULT 'breakout_calls',
    limit_price       NUMERIC,
    broker_order_id   TEXT,
    fill_price        NUMERIC,
    filled_quantity   INTEGER,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS option_orders_status_idx
    ON option_orders (trading_mode, strategy_version, status);

CREATE INDEX IF NOT EXISTS option_orders_broker_order_id_idx
    ON option_orders (broker_order_id)
    WHERE broker_order_id IS NOT NULL;
