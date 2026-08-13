-- Lightweight site-wide presence heartbeats.
-- One row per server-issued browser identity; counts use a rolling two-minute window.
CREATE TABLE IF NOT EXISTS presence_sessions (
  visitor_id TEXT PRIMARY KEY,
  last_seen INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_presence_sessions_last_seen
  ON presence_sessions (last_seen);
