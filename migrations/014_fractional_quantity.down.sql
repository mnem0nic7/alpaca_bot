-- 014_fractional_quantity.down.sql
ALTER TABLE orders
    ALTER COLUMN quantity TYPE INTEGER USING quantity::INTEGER,
    ALTER COLUMN filled_quantity TYPE INTEGER USING filled_quantity::INTEGER;

ALTER TABLE positions
    ALTER COLUMN quantity TYPE INTEGER USING quantity::INTEGER;

ALTER TABLE option_orders
    ALTER COLUMN quantity TYPE INTEGER USING quantity::INTEGER,
    ALTER COLUMN filled_quantity TYPE INTEGER USING filled_quantity::INTEGER;
