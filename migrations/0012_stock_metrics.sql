-- Stock metrics for low-chip screen: 股东人数, 股东变化, 主力控盘, 90%筹码集中度, 十大流通股东
-- One row per (trade_date, stock_code). INSERT OR REPLACE for daily rollover.
CREATE TABLE IF NOT EXISTS stock_metrics (
  trade_date TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  shareholder_count REAL,          -- 股东人数（万）
  shareholder_change_pct REAL,     -- 股东变化率（%）
  main_force REAL,                 -- 主力控股/机构参与度（%）
  main_force_label TEXT,           -- 控盘等级（中度控盘等）
  concentration90 REAL,            -- 90%筹码集中度（%）
  top10_float_ratio REAL,          -- 十大流通股东占比（%）
  price REAL,                      -- 当日收盘价
  announcement_date TEXT,          -- 股东数据公告日期（YYYYMMDD）
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (trade_date, stock_code)
);

CREATE INDEX IF NOT EXISTS idx_stock_metrics_date
  ON stock_metrics (trade_date, main_force);