import assert from 'node:assert/strict';
import test from 'node:test';

import { onRequest } from '../functions/api/public/v1/low-chip-metrics.js';

class Statement {
  constructor(sql, db) { this.sql = sql; this.db = db; this.values = []; }
  bind(...values) { this.values = values; return this; }
  async run() { this.db.runs.push({ sql: this.sql, values: this.values }); return { meta: { changes: 0 } }; }
  async all() { return { results: [] }; }
}

class DB {
  constructor() { this.runs = []; this.batches = []; }
  prepare(sql) { return new Statement(sql, this); }
  async batch(statements) {
    this.batches.push(statements);
    return statements.map(() => ({ meta: { changes: 1 } }));
  }
}

test('low-chip metrics persists valuation shadow columns without changing formal actions', async () => {
  const db = new DB();
  const request = new Request('https://etf.peekabo.cc/api/public/v1/low-chip-metrics', {
    method: 'POST', headers: { authorization: 'Bearer test', 'content-type': 'application/json' },
    body: JSON.stringify({ metrics: [{
      trade_date: '20260813', stock_code: '600021', pe_ttm: 12.5, pb: 1.2,
      ps_ttm: 0.8, pcf_ttm: 9.1, total_share: 1_000_000_000,
      total_mv: 14_530_000_000, fundamental_shadow_status: 'ACCUMULATING',
      fundamental_shadow_sessions: 10,
    }], preserve_existing: true }),
  });
  const response = await onRequest({ request, env: { DB: db, LOW_CHIP_SYNC_TOKEN: 'test' } });
  assert.equal(response.status, 200);
  const insert = db.batches.flat().find((statement) => statement.sql.includes('INSERT INTO'));
  for (const column of ['pe_ttm', 'pb', 'ps_ttm', 'pcf_ttm', 'total_share', 'total_mv',
    'fundamental_shadow_status', 'fundamental_shadow_sessions']) {
    assert.match(insert.sql, new RegExp(`\\b${column}\\b`));
  }
  assert.match(insert.sql, /ON CONFLICT\s*\(trade_date, stock_code\)\s*DO UPDATE/i);
  assert.doesNotMatch(insert.sql, /INSERT OR REPLACE/i);
});

test('low-chip metrics rejects a batch containing any invalid row', async () => {
  const db = new DB();
  const request = new Request('https://etf.peekabo.cc/api/public/v1/low-chip-metrics', {
    method: 'POST', headers: { authorization: 'Bearer test', 'content-type': 'application/json' },
    body: JSON.stringify({ metrics: [
      { trade_date: '20260813', stock_code: '600021', pe_ttm: 12 },
      { trade_date: '20260813' },
    ], preserve_existing: true }),
  });
  const response = await onRequest({ request, env: { DB: db, LOW_CHIP_SYNC_TOKEN: 'test' } });
  assert.equal(response.status, 400);
  assert.equal(db.batches.length, 0);
});
