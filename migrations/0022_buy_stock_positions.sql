-- 持仓记录：买入价、股数、买入时间、备注。
--
-- 数据存 D1 而非仓库 JSON：仓库在 GitHub 上是公开的，真实持仓与成本不能
-- 随构建产物分发。页面前端通过 /api/public/v1/buy-stock 读取。
CREATE TABLE IF NOT EXISTS buy_stock_positions (
  code TEXT PRIMARY KEY,
  name TEXT,
  market TEXT,
  buy_price REAL NOT NULL,
  shares INTEGER NOT NULL,
  buy_time TEXT,
  buy_date TEXT,
  note TEXT,
  sold_at TEXT,
  sell_price REAL,
  sell_time TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_buy_stock_positions_open
  ON buy_stock_positions (sold_at, buy_date);
