-- When the confidence floor was last auto-raised by the system. NULL for
-- operator-set floors and for floors raised before this column existed.
-- Used by the max-age escape (FLOOR_AUTO_RAISE_MAX_AGE_DAYS) to clear a
-- system-raised floor that hysteresis would otherwise keep alive forever.
ALTER TABLE confidence_floor_store
    ADD COLUMN floor_raised_at TIMESTAMPTZ;
