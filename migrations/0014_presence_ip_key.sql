-- Upgrade installations that applied the initial presence table before server-issued identity hardening.
ALTER TABLE presence_sessions ADD COLUMN ip_key TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_presence_sessions_ip_seen
  ON presence_sessions (ip_key, last_seen);
