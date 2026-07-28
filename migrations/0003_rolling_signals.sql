-- Rolling signal daily board: first write wins for the same day/node.
CREATE TABLE IF NOT EXISTS rolling_signals (
  trade_date TEXT NOT NULL,               -- Asia/Shanghai YYYY-MM-DD
  symbol TEXT NOT NULL,
  cycle_code TEXT NOT NULL,
  signal TEXT NOT NULL CHECK (signal IN ('BUY', 'SELL')),
  trigger_time_utc TEXT NOT NULL,
  received_at TEXT NOT NULL,
  event_id TEXT NOT NULL,
  label TEXT,
  instrument_name TEXT,
  exchange TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (trade_date, symbol, cycle_code, signal)
);

CREATE INDEX IF NOT EXISTS idx_rolling_signals_symbol_date
  ON rolling_signals (symbol, trade_date, received_at);

CREATE INDEX IF NOT EXISTS idx_rolling_signals_date
  ON rolling_signals (trade_date, received_at);
