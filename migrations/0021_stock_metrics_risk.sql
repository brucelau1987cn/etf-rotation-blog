-- D1 applies each numbered migration once and records it in its migration
-- ledger. Keep these plain ALTER statements: rerunning the SQL text manually
-- must be rejected by the local migration harness rather than partially
-- mutating a schema. See tests/test_d1_migrations.py.
ALTER TABLE stock_metrics ADD COLUMN risk_version TEXT;
ALTER TABLE stock_metrics ADD COLUMN risk_as_of TEXT;
ALTER TABLE stock_metrics ADD COLUMN risk_status TEXT;
ALTER TABLE stock_metrics ADD COLUMN risk_level TEXT;
ALTER TABLE stock_metrics ADD COLUMN risk_reasons TEXT;
ALTER TABLE stock_metrics ADD COLUMN risk_advisory INTEGER;
ALTER TABLE stock_metrics ADD COLUMN risk_coverage TEXT;
ALTER TABLE stock_metrics ADD COLUMN risk_freshness TEXT;