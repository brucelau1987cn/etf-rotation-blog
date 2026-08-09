-- Admin roles (2026-08-09)
-- role: super_admin（brucelau1987，全权限）/ admin（二级管理员，只能管理订阅）
-- 原表有 CHECK(id=1) 单行约束，重建为多行表
ALTER TABLE admin_credentials ADD COLUMN role TEXT NOT NULL DEFAULT 'super_admin';
UPDATE admin_credentials SET role = 'super_admin' WHERE id = 1;

CREATE TABLE admin_credentials_new (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'super_admin',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO admin_credentials_new (id, username, password_hash, role, updated_at)
  SELECT id, username, password_hash, role, updated_at FROM admin_credentials;
DROP TABLE admin_credentials;
ALTER TABLE admin_credentials_new RENAME TO admin_credentials;

-- 二级管理员（18918851888 / King8888）— 密码哈希 sha256('18918851888:King8888')
INSERT OR IGNORE INTO admin_credentials (id, username, password_hash, role)
VALUES (2, '18918851888', '5a78a89fb9492147c600272cae61be519fc15c69dfd407b7206b67c9d7ae8f5f', 'admin');
