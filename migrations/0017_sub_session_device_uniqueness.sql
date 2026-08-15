-- Prevent concurrent first-login requests from creating duplicate device rows.
-- Existing production data was checked for duplicates before this migration.
DELETE FROM sub_sessions
WHERE id NOT IN (
  SELECT MAX(id)
  FROM sub_sessions
  GROUP BY subscription_id, device_id
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_sessions_subscription_device_unique
  ON sub_sessions(subscription_id, device_id);
