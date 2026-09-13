import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../functions/api/public/v1/low-chip-metrics.js', import.meta.url), 'utf8');

assert.match(source, /DELETE FROM stock_metrics WHERE trade_date = \?/);
assert.match(source, /const replaceTradeDate = String\(body\?\.replace_trade_date/);
assert.match(source, /replaceTradeDate[\s\S]*trade_dates must match replace_trade_date/);
assert.match(source, /env\.DB\.batch\(\[deleteStmt, \.\.\.part\]\)/);
assert.match(source, /'touchstone_metrics'/);
assert.match(source, /ALTER TABLE stock_metrics ADD COLUMN touchstone_metrics TEXT/);
assert.match(source, /JSON\.parse\(metric\.touchstone_metrics\)/);

console.log('low-chip metrics replacement contract ok');
