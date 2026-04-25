-- Reverse: daily_session_state
ALTER TABLE daily_session_state DROP CONSTRAINT daily_session_state_pkey;
ALTER TABLE daily_session_state DROP COLUMN strategy_name;
ALTER TABLE daily_session_state ADD PRIMARY KEY (session_date, trading_mode, strategy_version);

-- Reverse: positions
ALTER TABLE positions DROP CONSTRAINT positions_pkey;
ALTER TABLE positions DROP COLUMN strategy_name;
ALTER TABLE positions ADD PRIMARY KEY (symbol, trading_mode, strategy_version);

-- Reverse: orders
ALTER TABLE orders DROP COLUMN strategy_name;
