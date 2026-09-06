-- Persist low-chip stock -> industry ETF mappings from the formal 91-ETF pool.
ALTER TABLE stock_metrics ADD COLUMN industry_etfs TEXT;
ALTER TABLE stock_metrics ADD COLUMN industry_etf_status TEXT;
ALTER TABLE stock_metrics ADD COLUMN industry_etf_pool_count INTEGER;
