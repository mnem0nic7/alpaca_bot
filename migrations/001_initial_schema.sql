CREATE TABLE IF NOT EXISTS trading_status (
    trading_mode TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    status TEXT NOT NULL,
    kill_switch_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    status_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (trading_mode, strategy_version),
    CHECK (trading_mode IN ('paper', 'live')),
    CHECK (status IN ('enabled', 'halted', 'close_only'))
);

CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    status TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    trading_mode TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    stop_price DOUBLE PRECISION,
    limit_price DOUBLE PRECISION,
    broker_order_id TEXT,
    signal_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (trading_mode IN ('paper', 'live'))
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol_updated_at
    ON orders (symbol, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_broker_order_id
    ON orders (broker_order_id)
    WHERE broker_order_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT NOT NULL,
    trading_mode TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    stop_price DOUBLE PRECISION NOT NULL,
    initial_stop_price DOUBLE PRECISION NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, trading_mode, strategy_version),
    CHECK (trading_mode IN ('paper', 'live'))
);

CREATE TABLE IF NOT EXISTS daily_session_state (
    session_date DATE NOT NULL,
    trading_mode TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    entries_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    flatten_complete BOOLEAN NOT NULL DEFAULT FALSE,
    last_reconciled_at TIMESTAMPTZ,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_date, trading_mode, strategy_version),
    CHECK (trading_mode IN ('paper', 'live'))
);

CREATE INDEX IF NOT EXISTS idx_daily_session_state_updated_at
    ON daily_session_state (updated_at DESC);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    symbol TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
    ON audit_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_events_event_type_created_at
    ON audit_events (event_type, created_at DESC);
