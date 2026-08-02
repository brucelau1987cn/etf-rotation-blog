CREATE TABLE IF NOT EXISTS jin10_etf_holdings (
  reported_on TEXT NOT NULL,
  attr_id INTEGER NOT NULL,
  trust REAL,
  change_value REAL,
  total_value REAL,
  raw_json TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  PRIMARY KEY (reported_on, attr_id)
);

CREATE INDEX IF NOT EXISTS idx_jin10_etf_holdings_attr
  ON jin10_etf_holdings (attr_id, reported_on);
