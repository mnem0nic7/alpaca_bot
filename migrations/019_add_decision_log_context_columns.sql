ALTER TABLE decision_log
    ADD COLUMN vix_close FLOAT,
    ADD COLUMN vix_above_sma BOOLEAN,
    ADD COLUMN sector_passing_pct FLOAT,
    ADD COLUMN vwap_at_signal FLOAT,
    ADD COLUMN signal_bar_above_vwap BOOLEAN;
