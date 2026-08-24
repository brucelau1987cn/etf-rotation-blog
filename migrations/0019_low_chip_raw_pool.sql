-- 低筹码原始池：周/月/季线收盘获利 ≤3% 的每日原始筛选结果
--
-- 目的：把 iWenCai 三周期筛选的「原始数据」独立落库，与下游筛选条件解耦。
-- 每次 iWenCai 查询结果一律入库，后续任何筛选条件变更都从库内重算，
-- 不再为「换个条件试试」而重复消耗 iWenCai 额度。
--
-- 粒度：一行 = 一个 (交易日, 股票, 周期)。
-- 周期取值：week / month / quarter / year
--   week/month/quarter 参与入池交集；year 仅作页面独立开关（与现有语义一致）。

CREATE TABLE IF NOT EXISTS low_chip_raw_pool (
  trade_date     TEXT NOT NULL,          -- 数据交易日 YYYY-MM-DD（对应 data_as_of）
  stock_code     TEXT NOT NULL,          -- 带后缀代码，如 600519.SH
  period         TEXT NOT NULL,          -- week / month / quarter / year
  stock_name     TEXT,
  profit_ratio   REAL,                   -- 该周期收盘获利比例（%），入选条件为 ≤3
  price          REAL,                   -- 查询时点最新价
  change_percent REAL,                   -- 查询时点最新涨跌幅（%）
  source         TEXT NOT NULL DEFAULT 'iwencai',
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (trade_date, stock_code, period)
);

-- 按日取全池（重算筛选条件时的主查询路径）
CREATE INDEX IF NOT EXISTS idx_low_chip_raw_pool_date
  ON low_chip_raw_pool (trade_date, period);

-- 按股票回溯历史（个股在池内的出现轨迹）
CREATE INDEX IF NOT EXISTS idx_low_chip_raw_pool_code
  ON low_chip_raw_pool (stock_code, trade_date);


-- 每日快照的元信息与审计口径：记录当日原始池是如何生成的。
-- 与 low_chip_raw_pool 一对多，用于回答「这天的池子是什么条件筛出来的」。
CREATE TABLE IF NOT EXISTS low_chip_raw_pool_meta (
  trade_date            TEXT PRIMARY KEY,
  threshold             REAL NOT NULL,   -- 收盘获利阈值（当前为 3）
  universe              TEXT,            -- 股票池口径描述
  listing_cutoff        TEXT,            -- 新股排除截止上市日期
  listing_min_days      INTEGER,
  week_count            INTEGER,         -- 各周期原始命中数（去重后）
  month_count           INTEGER,
  quarter_count         INTEGER,
  year_count            INTEGER,
  intersection_count    INTEGER,         -- 三周期交集（过滤前）
  generated_at          TEXT NOT NULL,   -- 实际生成时刻（ISO8601）
  is_backfill           INTEGER NOT NULL DEFAULT 0,
  backfill_reason       TEXT,
  iwencai_calls         INTEGER,         -- 本次生成消耗的 iWenCai 调用次数
  created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
