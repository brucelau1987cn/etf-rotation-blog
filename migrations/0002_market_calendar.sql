CREATE TABLE IF NOT EXISTS market_calendar (
  market TEXT NOT NULL CHECK (market IN ('CN_A', 'HK', 'US')),
  trade_date TEXT NOT NULL,
  is_open INTEGER NOT NULL CHECK (is_open IN (0, 1)),
  open_at TEXT,
  break_start_at TEXT,
  break_end_at TEXT,
  close_at TEXT,
  session_type TEXT NOT NULL DEFAULT 'normal',
  note TEXT,
  source TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (market, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_market_calendar_open_date
  ON market_calendar (market, is_open, trade_date);
