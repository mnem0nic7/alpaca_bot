-- 022: Clean up option_orders records orphaned by Bug 2a.
-- These records were written to status='submitting' before the broker call,
-- but the broker raised and no status rollback occurred. The dispatch loop
-- queries status='pending_submit' only, so orphaned 'submitting' records
-- are never retried or cleaned up without this migration.
UPDATE option_orders
SET    status     = 'failed',
       updated_at = NOW()
WHERE  status = 'submitting';
