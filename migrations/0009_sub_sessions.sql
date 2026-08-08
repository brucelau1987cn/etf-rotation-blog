-- Per-subscription device session limit (2026-08-08)
-- 每个订阅最多 5 台绑定设备（设备指纹唯一）；同一设备重复登录复用会话不新增
CREATE TABLE IF NOT EXISTS sub_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subscription_id INTEGER NOT NULL,
  device_id TEXT NOT NULL,             -- 前端 localStorage 持久设备指纹（UA 哈希兜底）
  device_ua TEXT NOT NULL DEFAULT '',  -- 设备 User-Agent（展示用）
  sid TEXT NOT NULL UNIQUE,            -- cookie token 内嵌的会话 id
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL,            -- 会话到期（与订阅到期对齐）
  last_seen TEXT,
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);
CREATE INDEX IF NOT EXISTS idx_sub_sessions_sub ON sub_sessions(subscription_id);
CREATE INDEX IF NOT EXISTS idx_sub_sessions_device ON sub_sessions(device_id);
