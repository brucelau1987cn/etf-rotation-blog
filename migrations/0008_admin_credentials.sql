-- Admin credentials (mutable at runtime — stored in D1, not env secrets)
-- 2026-08-08: admin login requires username + password; credentials editable in admin console
CREATE TABLE IF NOT EXISTS admin_credentials (
  id INTEGER PRIMARY KEY CHECK (id = 1),      -- 单行表
  username TEXT NOT NULL,
  password_hash TEXT NOT NULL,                 -- sha256(username:password)
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO admin_credentials (id, username, password_hash)
VALUES (1, '', '');
