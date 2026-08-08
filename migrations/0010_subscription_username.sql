-- Subscription username login (2026-08-08)
-- 订阅账号可选绑定用户名：有用户名者可走 用户名+密码 登录
-- password_hash = sha256(username:password)（与管理员同方案）；无用户名者只能走密码登录
ALTER TABLE subscriptions ADD COLUMN username TEXT;
ALTER TABLE subscriptions ADD COLUMN password_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_username ON subscriptions(username) WHERE username IS NOT NULL;
