-- 014_fractional_quantity.sql
ALTER TABLE orders
    ALTER COLUMN quantity TYPE NUMERIC(18,4) USING quantity::NUMERIC(18,4),
    ALTER COLUMN filled_quantity TYPE NUMERIC(18,4) USING filled_quantity::NUMERIC(18,4);

ALTER TABLE positions
    ALTER COLUMN quantity TYPE NUMERIC(18,4) USING quantity::NUMERIC(18,4);

ALTER TABLE option_orders
    ALTER COLUMN quantity TYPE NUMERIC(18,4) USING quantity::NUMERIC(18,4),
    ALTER COLUMN filled_quantity TYPE NUMERIC(18,4) USING filled_quantity::NUMERIC(18,4);
