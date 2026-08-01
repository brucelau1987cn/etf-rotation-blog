CREATE TABLE IF NOT EXISTS jin10_calendar_items (
  item_key TEXT PRIMARY KEY,
  item_type TEXT NOT NULL CHECK (item_type IN ('data', 'event', 'holiday', 'other')),
  source_id INTEGER,
  indicator_id INTEGER,
  event_time TEXT,
  country TEXT,
  star INTEGER,
  title TEXT NOT NULL,
  previous TEXT,
  consensus TEXT,
  actual TEXT,
  revised TEXT,
  unit TEXT,
  affect INTEGER,
  show_affect INTEGER,
  time_status TEXT,
  source TEXT,
  raw_json TEXT NOT NULL,
  synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jin10_calendar_time
  ON jin10_calendar_items (event_time, item_type, star);

CREATE INDEX IF NOT EXISTS idx_jin10_calendar_indicator
  ON jin10_calendar_items (indicator_id, event_time);
