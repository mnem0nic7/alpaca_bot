ALTER TABLE decision_log
    ADD COLUMN IF NOT EXISTS vix_close FLOAT,
    ADD COLUMN IF NOT EXISTS vix_above_sma BOOLEAN,
    ADD COLUMN IF NOT EXISTS sector_passing_pct FLOAT,
    ADD COLUMN IF NOT EXISTS vwap_at_signal FLOAT,
    ADD COLUMN IF NOT EXISTS signal_bar_above_vwap BOOLEAN;
