-- Track immutable identity creation time for the rolling new-identity rate limit.
ALTER TABLE presence_sessions ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0;
UPDATE presence_sessions SET created_at = last_seen WHERE created_at = 0;
DROP INDEX IF EXISTS idx_presence_sessions_ip_seen;
CREATE INDEX IF NOT EXISTS idx_presence_sessions_ip_created
  ON presence_sessions (ip_key, created_at);
