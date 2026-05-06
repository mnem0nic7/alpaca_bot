-- 014_fractional_quantity.down.sql
-- WARNING: This down migration is LOSSY — fractional quantities truncate to integer.
-- Do not run on a production database that contains live fractional-share positions.
ALTER TABLE orders
    ALTER COLUMN quantity TYPE INTEGER USING quantity::INTEGER,
    ALTER COLUMN filled_quantity TYPE INTEGER USING filled_quantity::INTEGER;

ALTER TABLE positions
    ALTER COLUMN quantity TYPE INTEGER USING quantity::INTEGER;
