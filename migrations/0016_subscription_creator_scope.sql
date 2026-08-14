-- Attribute every subscription to the administrator who created it.
-- Historical rows predate creator tracking and belong to the original super administrator (id=1).
ALTER TABLE subscriptions ADD COLUMN created_by_admin_id INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_subscriptions_created_by_admin
  ON subscriptions(created_by_admin_id, revoked, expires_at);
