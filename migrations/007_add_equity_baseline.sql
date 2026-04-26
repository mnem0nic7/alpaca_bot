-- Persist start-of-day equity baseline so mid-day supervisor restarts don't
-- reset the daily-loss-limit reference to post-loss equity.
ALTER TABLE daily_session_state
    ADD COLUMN IF NOT EXISTS equity_baseline NUMERIC(20, 8);
