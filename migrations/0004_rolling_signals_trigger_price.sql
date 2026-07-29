-- Persist signal-point price at first webhook insert.
-- trigger_price = 1-minute close at trigger_time (Asia/Shanghai minute).
ALTER TABLE rolling_signals ADD COLUMN trigger_price REAL;
ALTER TABLE rolling_signals ADD COLUMN trigger_price_source TEXT;
