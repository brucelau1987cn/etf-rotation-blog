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

test('low-chip metrics decodes risk JSON and advisory booleans on GET', async () => {
  const db = new DB();
  db.prepare = (sql) => {
    const statement = new Statement(sql, db);
    statement.all = async () => ({ results: [{ trade_date: '20260813', stock_code: '600021', risk_reasons: '["drawdown"]', risk_coverage: '{"complete":4}', risk_freshness: '{"price_as_of":"2026-08-13"}', risk_advisory: 1 }] });
    return statement;
  };
  const response = await onRequest({ request: new Request('https://etf.peekabo.cc/api/public/v1/low-chip-metrics?date=2026-08-13&code=600021', { headers: { authorization: 'Bearer test' } }), env: { DB: db, LOW_CHIP_SYNC_TOKEN: 'test' } });
  const body = await response.json();
  assert.equal(body.results[0].risk_advisory, true);
  assert.deepEqual(body.results[0].risk_reasons, ['drawdown']);
  assert.equal(body.results[0].risk_coverage.complete, 4);
  assert.equal(body.results[0].risk_freshness.price_as_of, '2026-08-13');
});

test('replace respects D1 parameter and batch limits for all 198 rows', async () => {
  const db = new DB();
  const metrics = Array.from({ length: 198 }, (_, i) => ({ trade_date: '20260813', stock_code: String(i).padStart(6, '0') }));
  const response = await onRequest({
    request: new Request('https://etf.peekabo.cc/api/public/v1/low-chip-metrics', { method: 'POST', headers: { authorization: 'Bearer test', 'content-type': 'application/json' }, body: JSON.stringify({ metrics, replace_trade_date: '20260813' }) }),
    env: { DB: db, LOW_CHIP_SYNC_TOKEN: 'test' },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(db.batches.map(batch => batch.length), [100, 99]);
  const statements = db.batches.flat();
  assert.match(statements[0].sql, /^DELETE FROM stock_metrics/);
  assert.deepEqual(statements[0].values, ['20260813']);
  assert.equal(statements.filter(stmt => stmt.sql.startsWith('DELETE')).length, 1);
  const inserts = statements.slice(1);
  assert.equal(inserts.length, metrics.length);
  assert.deepEqual(inserts.map(stmt => stmt.values[1]), metrics.map(row => row.stock_code));
  for (const statement of inserts) {
    assert.match(statement.sql, /^INSERT OR REPLACE INTO stock_metrics/);
    assert.ok(statement.values.length <= 100);
    assert.equal((statement.sql.match(/\?/g) || []).length, statement.values.length);
  }
  const body = await response.json();
  assert.equal(body.inserted, 198);
  assert.equal(body.total, 198);
});
