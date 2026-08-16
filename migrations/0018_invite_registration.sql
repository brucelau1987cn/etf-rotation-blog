-- Self-registration invite codes with atomic bounded claims.
CREATE TABLE IF NOT EXISTS invite_codes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code_hash TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  owner_admin_id INTEGER NOT NULL DEFAULT 1,
  expires_at TEXT NOT NULL,
  max_uses INTEGER NOT NULL CHECK(max_uses > 0),
  used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
  account_expires_at TEXT NOT NULL DEFAULT '2099-12-31T00:00:00.000Z',
  revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
  last_claim_nonce TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_invite_codes_active
  ON invite_codes(revoked, expires_at, used_count, max_uses);

CREATE TABLE IF NOT EXISTS invite_registrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invite_code_id INTEGER NOT NULL,
  subscription_id INTEGER NOT NULL UNIQUE,
  username TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(invite_code_id) REFERENCES invite_codes(id),
  FOREIGN KEY(subscription_id) REFERENCES subscriptions(id)
);

CREATE INDEX IF NOT EXISTS idx_invite_registrations_code
  ON invite_registrations(invite_code_id, created_at);
