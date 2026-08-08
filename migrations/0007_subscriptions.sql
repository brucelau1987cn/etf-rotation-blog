-- Subscription-based site access (2026-08-08)
-- 每个订阅 = 一个密码 + 有效期（相当于订阅）
CREATE TABLE IF NOT EXISTS subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  passphrase_hash TEXT NOT NULL UNIQUE,   -- sha256 密码哈希
  label TEXT NOT NULL DEFAULT '',          -- 备注（订阅者名）
  expires_at TEXT NOT NULL,                -- ISO 8601 到期时间
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  revoked INTEGER NOT NULL DEFAULT 0       -- 1 = 已撤销
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_expires ON subscriptions(expires_at);
