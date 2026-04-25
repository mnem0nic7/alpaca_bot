-- Add strategy_name to orders (no PK change; client_order_id remains PK)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS strategy_name TEXT NOT NULL DEFAULT 'breakout';

-- Add strategy_name to positions and change PK
ALTER TABLE positions ADD COLUMN IF NOT EXISTS strategy_name TEXT NOT NULL DEFAULT 'breakout';
ALTER TABLE positions DROP CONSTRAINT positions_pkey;
ALTER TABLE positions ADD PRIMARY KEY (symbol, trading_mode, strategy_version, strategy_name);

-- Add strategy_name to daily_session_state and change PK
ALTER TABLE daily_session_state ADD COLUMN IF NOT EXISTS strategy_name TEXT NOT NULL DEFAULT 'breakout';
ALTER TABLE daily_session_state DROP CONSTRAINT daily_session_state_pkey;
ALTER TABLE daily_session_state ADD PRIMARY KEY (session_date, trading_mode, strategy_version, strategy_name);
